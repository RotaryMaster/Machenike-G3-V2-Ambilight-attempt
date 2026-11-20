#!/usr/bin/env python3
"""
Static inventory of all lighting-related API surfaces in MixDrive.

Finds:
- KeyLinkerlib.dll exports containing lamp/light/rgb/led/color;
- GamepadInterop P/Invokes matching those terms;
- managed call sites to those methods across Infrastructure/Shared/EXE;
- relevant method names in managed assemblies, to expose possible preview,
  realtime, test or volatile lighting paths.

No code is executed. No device access is performed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import dnfile
import pefile
from dnfile.enums import MetadataTables
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken

# "lamp" alone would false-positive on names such as MathHelper::Clamp.
KEYWORDS = ("lamplight", "light", "rgb", "led", "color", "colour")
TABLES = {table.value: table.name for table in MetadataTables}


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


def parent_maps(pe):
    methods = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []) or []:
            rid = getattr(idx, "row_index", None)
            if rid:
                methods[rid] = td
    return methods


def type_name(row):
    if row is None:
        return "?"
    ns = str(getattr(row, "TypeNamespace", "") or "")
    name = str(getattr(row, "TypeName", "") or "")
    return f"{ns}.{name}".strip(".") if name else str(row)


def resolve(pe, token):
    if isinstance(token, StringToken):
        try:
            s = pe.net.user_strings.get(token.rid)
        except Exception:
            return InvalidToken(token.value)
        return getattr(s, "value", None) if s is not None else InvalidToken(token.value)

    table_name = TABLES.get(token.table, "")
    table = getattr(pe.net.mdtables, table_name, None) if table_name else None
    if table is None:
        return InvalidToken(token.value)
    try:
        return table.rows[token.rid - 1]
    except Exception:
        return InvalidToken(token.value)


def render_target(pe, operand, method_parents):
    if not isinstance(operand, Token):
        return ""
    row = resolve(pe, operand)
    if isinstance(row, str):
        return row
    cls = row.__class__.__name__
    if cls == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{type_name(parent)}::{getattr(row, 'Name', '?')}"
    if cls == "MethodDefRow":
        return f"{type_name(method_parents.get(operand.rid))}::{getattr(row, 'Name', '?')}"
    if cls == "MethodSpecRow":
        base = getattr(getattr(row, "Method", None), "row", None)
        if base is None:
            return "MethodSpec"
        if base.__class__.__name__ == "MemberRefRow":
            parent = getattr(getattr(base, "Class", None), "row", None)
            return f"{type_name(parent)}::{getattr(base, 'Name', '?')}"
        return str(getattr(base, "Name", "?"))
    return str(getattr(row, "Name", ""))


def hit(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in KEYWORDS)


def dump_native_exports(dll: Path):
    pe = pefile.PE(str(dll), fast_load=False)
    exp = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)

    print("=" * 120)
    print("1. KEYLINKER NATIVE LIGHTING-RELATED EXPORTS")
    print("=" * 120)

    if not exp:
        print("(no export table)")
        return

    rows = []
    for sym in exp.symbols:
        if not sym.name:
            continue
        name = sym.name.decode("ascii", errors="replace")
        if hit(name):
            rows.append((name, int(sym.address)))

    for name, rva in sorted(rows):
        print(f"RVA 0x{rva:05X}  {name}")

    if not rows:
        print("(none)")


def dump_managed(path: Path):
    pe = dnfile.dnPE(str(path))
    parents = parent_maps(pe)

    print("\n" + "=" * 120)
    print(f"2. MANAGED LIGHTING API — {path.name}")
    print("=" * 120)

    impl = getattr(pe.net.mdtables, "ImplMap", None)
    found_pinvoke = []
    if impl is not None:
        for im in impl.rows:
            method = getattr(getattr(im, "MemberForwarded", None), "row", None)
            if method is None:
                continue
            name = str(getattr(method, "Name", "") or "")
            if not hit(name):
                continue
            scope = getattr(getattr(im, "ImportScope", None), "row", None)
            found_pinvoke.append((
                name,
                str(getattr(scope, "Name", "?") or "?"),
                str(getattr(im, "ImportName", "?") or "?"),
                blob_bytes(getattr(method, "Signature", None)).hex(" "),
            ))

    print("\nP/Invokes:")
    if found_pinvoke:
        for name, dll, native, sig in found_pinvoke:
            print(f"  {name} -> {dll}!{native} | sig={sig}")
    else:
        print("  (none)")

    print("\nManaged method names:")
    names = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        mn = str(getattr(row, "Name", "") or "")
        owner = type_name(parents.get(rid))
        full = f"{owner}::{mn}"
        if hit(full):
            names.append((rid, full))
    for rid, full in names:
        print(f"  0x{0x06000000 | rid:08X} {full}")
    if not names:
        print("  (none)")

    print("\nCall sites to lighting-related methods:")
    call_count = 0
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(Reader(pe, row))
        except Exception:
            continue

        code_start = body.offset + body.header_size
        owner = type_name(parents.get(rid))
        caller = f"{owner}::{getattr(row, 'Name', '?')}"
        for insn in body.instructions:
            op = getattr(insn, "mnemonic", str(insn.opcode))
            if op not in ("call", "callvirt", "newobj"):
                continue
            target = render_target(pe, insn.operand, parents)
            if not hit(target):
                continue
            call_count += 1
            print(
                f"  {caller} | IL+0x{insn.offset - code_start:04X}"
                f" -> {target}"
            )

    if call_count == 0:
        print("  (none)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    args = ap.parse_args()

    root = Path(args.root)
    dll = root / "Libs" / "Gamepad" / "KeyLinkerlib.dll"
    assemblies = (
        root / "Machenik.Infrastructure.dll",
        root / "Machenik.Shared.dll",
        root / "Machenik.exe",
    )

    for path in (dll, *assemblies):
        if not path.is_file():
            raise SystemExit(f"Missing: {path}")

    dump_native_exports(dll)
    for path in assemblies:
        dump_managed(path)

    print("\nNo code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static export/metadata/CIL inspection only.")


if __name__ == "__main__":
    main()
