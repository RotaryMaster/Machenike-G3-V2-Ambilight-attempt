#!/usr/bin/env python3
"""
Focused static audit of the G3V2 LampLight protocol.

Combines:
1) full native disassembly of:
   - readDeviceLamplightData
   - receiveDeviceLamplightData
   - writeDeviceLamplightData
2) managed metadata for DeviceLampLightStruct;
3) P/Invoke signatures/parameter names for LampLight functions;
4) managed call sites that invoke LampLight read/write functions.

No managed/native code is executed.
No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import dnfile
import pefile
from dnfile.enums import MetadataTables
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken

TABLES = {table.value: table.name for table in MetadataTables}

NATIVE_TARGETS = (
    "readDeviceLamplightData",
    "receiveDeviceLamplightData",
    "writeDeviceLamplightData",
)

KNOWN = {
    0x180001080: "KeyLinkerEncode",
    0x1800011C0: "KeyLinkerDecode",
    0x180001400: "Concat",
    0x1800014C0: "KeyLinkerState",
    0x180001760: "ReceiveReassembly",
    0x180001BE0: "GenericReadHelper",
    0x180001C90: "HeaderBuilder",
    0x18000B150: "rand_IAT",
}

ADDR_RE = re.compile(r"^\s*([0-9a-fA-F]+):")


class Reader(CilMethodBodyReaderBase):
    def __init__(self, pe, row):
        self.pe = pe
        self.offset = pe.get_offset_from_rva(row.Rva)

    def read(self, n):
        data = self.pe.get_data(self.pe.get_rva_from_offset(self.offset), n)
        self.offset += n
        return data

    def tell(self):
        return self.offset

    def seek(self, offset):
        self.offset = offset
        return offset


def blob_bytes(blob: Any) -> bytes:
    if blob is None:
        return b""
    if isinstance(blob, (bytes, bytearray, memoryview)):
        return bytes(blob)
    for attr in ("value", "data"):
        value = getattr(blob, attr, None)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
    try:
        return bytes(blob)
    except Exception:
        return b""


def resolve(pe, token):
    if isinstance(token, StringToken):
        try:
            s = pe.net.user_strings.get(token.rid)
        except Exception:
            return InvalidToken(token.value)
        return getattr(s, "value", None) if s is not None else InvalidToken(token.value)

    table_name = TABLES.get(token.table, "")
    if not table_name:
        return InvalidToken(token.value)
    table = getattr(pe.net.mdtables, table_name, None)
    if table is None:
        return InvalidToken(token.value)
    try:
        return table.rows[token.rid - 1]
    except Exception:
        return InvalidToken(token.value)


def type_name(row):
    if row is None:
        return "?"
    ns = str(getattr(row, "TypeNamespace", "") or "")
    name = str(getattr(row, "TypeName", "") or "")
    return f"{ns}.{name}".strip(".") if name else str(row)


def parent_maps(pe):
    methods = {}
    fields = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []) or []:
            rid = getattr(idx, "row_index", None)
            if rid:
                methods[rid] = td
        for idx in getattr(td, "FieldList", []) or []:
            rid = getattr(idx, "row_index", None)
            if rid:
                fields[rid] = td
    return methods, fields


def render_row(row, method_parents, field_parents, rid=None):
    if row is None:
        return "?"
    cls = row.__class__.__name__
    if cls == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{type_name(parent)}::{getattr(row, 'Name', '?')}"
    if cls == "MethodDefRow":
        parent = method_parents.get(rid) if rid else None
        return f"{type_name(parent)}::{getattr(row, 'Name', '?')}"
    if cls == "FieldRow":
        parent = field_parents.get(rid) if rid else None
        return f"{type_name(parent)}::{getattr(row, 'Name', '?')}"
    if cls in ("TypeDefRow", "TypeRefRow"):
        return type_name(row)
    if cls == "MethodSpecRow":
        base = getattr(getattr(row, "Method", None), "row", None)
        if base is None:
            return "MethodSpec"
        if base.__class__.__name__ == "MemberRefRow":
            parent = getattr(getattr(base, "Class", None), "row", None)
            return f"MethodSpec::{type_name(parent)}::{getattr(base, 'Name', '?')}"
        return f"MethodSpec::{getattr(base, 'Name', '?')}"
    return f"{cls}:{getattr(row, 'Name', '')}"


def operand_text(pe, operand, method_parents, field_parents):
    if isinstance(operand, StringToken):
        value = resolve(pe, operand)
        return repr(value) if isinstance(value, str) else str(value)
    if isinstance(operand, Token):
        row = resolve(pe, operand)
        if isinstance(row, str):
            return repr(row)
        return render_row(row, method_parents, field_parents, operand.rid)
    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"
    return "" if operand is None else str(operand)


def annotate_native(line: str) -> str:
    low = line.lower()
    hits = []
    for addr, name in KNOWN.items():
        hx = f"{addr:x}"
        if hx in low or f"0x{hx}" in low:
            hits.append(name)
    return line + (("    <== " + ", ".join(hits)) if hits else "")


def export_map(pe: pefile.PE):
    out = {}
    exp = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if not exp:
        return out
    for sym in exp.symbols:
        if not sym.name:
            continue
        name = sym.name.decode("ascii", errors="replace")
        out[name] = int(sym.address)
    return out


def native_dump(dll: Path, objdump: str):
    pe = pefile.PE(str(dll), fast_load=False)
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    exports = export_map(pe)
    all_rvas = sorted(set(exports.values()))

    print("=" * 128)
    print("1. FULL NATIVE LAMPLIGHT FUNCTIONS")
    print("=" * 128)

    for name in NATIVE_TARGETS:
        rva = exports.get(name)
        if rva is None:
            print(f"\n{name}: EXPORT NOT FOUND")
            continue

        next_rva = next((x for x in all_rvas if x > rva), None)
        stop_rva = next_rva if next_rva is not None else rva + 0x1000
        start = image_base + rva
        stop = image_base + stop_rva

        cp = subprocess.run(
            [
                objdump,
                "-d",
                "-Mintel",
                f"--start-address=0x{start:X}",
                f"--stop-address=0x{stop:X}",
                str(dll),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        print("\n" + "-" * 128)
        print(name)
        print(f"RVA=0x{rva:X} VA=0x{start:X} STOP=0x{stop:X}")
        print("-" * 128)
        for line in cp.stdout.splitlines():
            if ADDR_RE.match(line):
                print(annotate_native(line.rstrip()))


def field_layout_map(pe):
    out = {}
    table = getattr(pe.net.mdtables, "FieldLayout", None)
    if table is None:
        return out
    for row in table.rows:
        field = getattr(getattr(row, "Field", None), "row", None)
        if field is not None:
            out[id(field)] = int(getattr(row, "Offset", -1))
    return out


def class_layout_map(pe):
    out = {}
    table = getattr(pe.net.mdtables, "ClassLayout", None)
    if table is None:
        return out
    for row in table.rows:
        parent = getattr(getattr(row, "Parent", None), "row", None)
        if parent is not None:
            out[id(parent)] = (
                int(getattr(row, "PackingSize", 0)),
                int(getattr(row, "ClassSize", 0)),
            )
    return out


def print_managed_structs(path: Path):
    pe = dnfile.dnPE(str(path))
    field_layouts = field_layout_map(pe)
    class_layouts = class_layout_map(pe)

    print("\n" + "=" * 128)
    print(f"2. MANAGED LAMPLIGHT STRUCT METADATA — {path.name}")
    print("=" * 128)

    found = 0
    for td in pe.net.mdtables.TypeDef.rows:
        tn = type_name(td)
        if "LampLight" not in tn and "Lamplight" not in tn:
            continue
        found += 1
        print(f"\nTYPE: {tn}")
        print(f"Flags: {getattr(td, 'Flags', '?')}")
        layout = class_layouts.get(id(td))
        if layout:
            print(f"ClassLayout: pack={layout[0]} size={layout[1]}")

        for findex in getattr(td, "FieldList", []) or []:
            field = getattr(findex, "row", None)
            if field is None:
                continue
            sig = blob_bytes(getattr(field, "Signature", None))
            off = field_layouts.get(id(field))
            print(
                f"  FIELD {getattr(field, 'Name', '?')}"
                f" | offset={off if off is not None else '?'}"
                f" | sig={sig.hex(' ')}"
            )

    if not found:
        print("(no LampLight/Lamplight types found)")


def print_pinvokes_and_calls(path: Path):
    pe = dnfile.dnPE(str(path))
    mp, fp = parent_maps(pe)

    print("\n" + "=" * 128)
    print(f"3. LAMPLIGHT P/INVOKE SIGNATURES — {path.name}")
    print("=" * 128)

    impl = getattr(pe.net.mdtables, "ImplMap", None)
    if impl is not None:
        for im in impl.rows:
            method = getattr(getattr(im, "MemberForwarded", None), "row", None)
            if method is None:
                continue
            name = str(getattr(method, "Name", "") or "")
            if "lamp" not in name.lower():
                continue

            rid = None
            try:
                rid = pe.net.mdtables.MethodDef.rows.index(method) + 1
            except ValueError:
                pass

            scope = getattr(getattr(im, "ImportScope", None), "row", None)
            dll = str(getattr(scope, "Name", "?") or "?")
            native = str(getattr(im, "ImportName", "?") or "?")
            sig = blob_bytes(getattr(method, "Signature", None))

            params = []
            for pidx in getattr(method, "ParamList", []) or []:
                prow = getattr(pidx, "row", None)
                if prow is None:
                    continue
                params.append(
                    f"{getattr(prow, 'Sequence', '?')}:{getattr(prow, 'Name', '')}"
                )

            print(
                f"0x{(0x06000000 | rid) if rid else 0:08X} "
                f"{type_name(mp.get(rid) if rid else None)}::{name}"
            )
            print(f"  -> {dll}!{native}")
            print(f"  sig={sig.hex(' ')}")
            print(f"  params={', '.join(params) if params else '(none named)'}")

    print("\n" + "=" * 128)
    print(f"4. MANAGED LAMPLIGHT CALL SITES — {path.name}")
    print("=" * 128)

    any_hits = False
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(Reader(pe, row))
        except Exception:
            continue

        insns = list(body.instructions)
        code_start = body.offset + body.header_size
        hits = []
        for i, insn in enumerate(insns):
            op = getattr(insn, "mnemonic", str(insn.opcode))
            if op not in ("call", "callvirt", "newobj"):
                continue
            text = operand_text(pe, insn.operand, mp, fp)
            if "lamp" in text.lower():
                hits.append((i, text))

        if not hits:
            continue

        any_hits = True
        parent = mp.get(rid)
        print(
            f"\n{type_name(parent)}::{getattr(row, 'Name', '?')} "
            f"token=0x{0x06000000 | rid:08X}"
        )

        show = set()
        for i, _ in hits:
            show.update(range(max(0, i - 18), min(len(insns), i + 20)))

        last = None
        for i in sorted(show):
            if last is not None and i != last + 1:
                print("  ...")
            insn = insns[i]
            raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
            op = getattr(insn, "mnemonic", str(insn.opcode))
            text = operand_text(pe, insn.operand, mp, fp)
            iloff = insn.offset - code_start
            suffix = ""
            if any(i == h[0] for h in hits):
                suffix = "    <== LAMPLIGHT"
            print(f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {text}{suffix}")
            last = i

    if not any_hits:
        print("(no managed LampLight call sites found)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    ap.add_argument(
        "--objdump",
        default=shutil.which("objdump") or "/usr/bin/objdump",
        help="objdump binary",
    )
    args = ap.parse_args()

    root = Path(args.root)
    dll = root / "Libs" / "Gamepad" / "KeyLinkerlib.dll"
    infra = root / "Machenik.Infrastructure.dll"
    shared = root / "Machenik.Shared.dll"
    exe = root / "Machenik.exe"

    for path in (dll, infra, shared, exe):
        if not path.is_file():
            raise SystemExit(f"Missing: {path}")

    print("=" * 128)
    print("G3V2 LAMPLIGHT — FULL STATIC PROTOCOL AUDIT")
    print("=" * 128)
    print("Root:", root)
    print("DLL :", dll)
    print()

    native_dump(dll, args.objdump)

    for path in (infra, shared, exe):
        print_managed_structs(path)

    print_pinvokes_and_calls(infra)
    print_pinvokes_and_calls(shared)
    print_pinvokes_and_calls(exe)

    print("\nNo managed/native code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata + PE disassembly only.")


if __name__ == "__main__":
    main()
