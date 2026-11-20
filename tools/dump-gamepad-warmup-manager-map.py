#!/usr/bin/env python3
"""
Focused static audit that maps the official MixDrive warm-up loaders to
GamepadDeviceManager calls, then dumps the corresponding manager async
state machines so their native/read operation can be identified.

No managed code is executed.
No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import dnfile
from dnfile.enums import MetadataTables
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken

TABLES = {table.value: table.name for table in MetadataTables}

LOADER_HINTS = (
    "<LoadKeyMapAsync>",
    "<LoadTurboAsync>",
    "<LoadRockerAsync>",
    "<LoadTriggerAsync>",
    "<LoadMotorAsync>",
    "<LoadLightAsync>",
    "<LoadCustomDataAsync>",
)

MANAGER_METHOD_HINTS = (
    "GetDeviceBeforeKeyChangeListAsync",
    "GetDeviceAfterKeyChangeListAsync",
    "GetDeviceSupportTurboAsync",
    "GetDeviceCurrentTurboAsync",
    "GetDeviceRockerAsync",
    "GetDeviceTriggerAsync",
    "GetDeviceMotorAsync",
    "GetDeviceLightAsync",
    "GetDeviceCustomDataAsync",
)


class DnfileReader(CilMethodBodyReaderBase):
    def __init__(self, pe: dnfile.dnPE, row):
        self.pe = pe
        self.offset = pe.get_offset_from_rva(row.Rva)

    def read(self, n: int) -> bytes:
        data = self.pe.get_data(self.pe.get_rva_from_offset(self.offset), n)
        self.offset += n
        return data

    def tell(self) -> int:
        return self.offset

    def seek(self, offset: int) -> int:
        self.offset = offset
        return offset


def resolve_token(pe: dnfile.dnPE, token: Token) -> Any:
    if isinstance(token, StringToken):
        try:
            s = pe.net.user_strings.get(token.rid)
        except Exception:
            return InvalidToken(token.value)
        if s is None or isinstance(s, bytes) or s.value is None:
            return InvalidToken(token.value)
        return s.value

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


def type_name(row: Any) -> str:
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


def render_row(row: Any, method_parents, field_parents, rid=None) -> str:
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


def render_operand(pe, operand: Any, method_parents, field_parents) -> str:
    if isinstance(operand, StringToken):
        v = resolve_token(pe, operand)
        return repr(v) if isinstance(v, str) else str(v)

    if isinstance(operand, Token):
        row = resolve_token(pe, operand)
        if isinstance(row, str):
            return repr(row)
        return render_row(
            row, method_parents, field_parents, rid=operand.rid
        )

    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"

    return "" if operand is None else str(operand)


def method_inventory(pe):
    method_parents, field_parents = parent_maps(pe)
    out = []

    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(DnfileReader(pe, row))
        except Exception:
            continue
        out.append(
            (
                rid,
                row,
                method_parents.get(rid),
                body,
                list(body.instructions),
                body.offset + body.header_size,
            )
        )

    return method_parents, field_parents, out


def format_insn(pe, insn, code_start, method_parents, field_parents):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = render_operand(pe, insn.operand, method_parents, field_parents)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def is_call(insn):
    return getattr(insn, "mnemonic", str(insn.opcode)) in (
        "call", "callvirt", "newobj"
    )


def scan_assembly(path: Path):
    pe = dnfile.dnPE(str(path))
    method_parents, field_parents, methods = method_inventory(pe)
    return pe, method_parents, field_parents, methods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "root",
        help=(
            "Path to MixDrive extracted directory containing "
            "Machenik.exe, Machenik.Shared.dll and Machenik.Infrastructure.dll"
        ),
    )
    args = ap.parse_args()

    root = Path(args.root)
    assembly_paths = {
        "Machenik.exe": root / "Machenik.exe",
        "Machenik.Shared.dll": root / "Machenik.Shared.dll",
        "Machenik.Infrastructure.dll": root / "Machenik.Infrastructure.dll",
    }

    missing = [str(p) for p in assembly_paths.values() if not p.is_file()]
    if missing:
        raise SystemExit("Missing required assembly: " + ", ".join(missing))

    loaded = {
        name: scan_assembly(path)
        for name, path in assembly_paths.items()
    }

    print("=" * 112)
    print("GAMEPAD WARM-UP -> MANAGER/NATIVE MAP — MULTI-ASSEMBLY STATIC CIL AUDIT")
    print("=" * 112)
    print("Root:", root)
    for name, path in assembly_paths.items():
        print(f"  {name}: {path}")

    called_manager = []

    print("\n=== 1. LOADER ORDER: GamepadDeviceManager CALLS ACROSS EXE + SHARED ===")

    for asm_name in ("Machenik.exe", "Machenik.Shared.dll"):
        pe, method_parents, field_parents, methods = loaded[asm_name]

        for rid, row, parent, body, insns, code_start in methods:
            pn = type_name(parent)
            mn = str(getattr(row, "Name", "") or "")

            if mn != "MoveNext":
                continue
            if not any(h.lower() in pn.lower() for h in LOADER_HINTS):
                continue

            hits = []
            for insn in insns:
                if not is_call(insn):
                    continue
                rendered = render_operand(
                    pe, insn.operand, method_parents, field_parents
                )
                if "GamepadDeviceManager::" in rendered:
                    hits.append((insn, rendered))
                    name = rendered.split("::")[-1]
                    if name not in called_manager:
                        called_manager.append(name)

            if not hits:
                continue

            print("\n" + "-" * 112)
            print(
                f"[{asm_name}] {pn}::{mn} "
                f"token=0x{0x06000000 | rid:08X}"
            )
            for insn, rendered in hits:
                print(
                    format_insn(
                        pe, insn, code_start,
                        method_parents, field_parents
                    )
                )

    print("\nManager methods reached by loader scan:")
    for name in called_manager:
        print("  -", name)

    print("\n=== 2. MATCHING GamepadDeviceManager STATE MACHINES IN INFRASTRUCTURE ===")

    pe, method_parents, field_parents, methods = loaded[
        "Machenik.Infrastructure.dll"
    ]

    wanted = set(MANAGER_METHOD_HINTS)
    wanted.update(called_manager)

    found = 0
    for rid, row, parent, body, insns, code_start in methods:
        pn = type_name(parent)
        mn = str(getattr(row, "Name", "") or "")

        if mn != "MoveNext":
            continue

        matched = None
        for name in sorted(wanted):
            if f"<{name}>" in pn:
                matched = name
                break
        if matched is None:
            continue

        found += 1
        print("\n" + "-" * 112)
        print(
            f"[Machenik.Infrastructure.dll] {matched} -> "
            f"{pn}::{mn} token=0x{0x06000000 | rid:08X}"
        )

        # Show direct calls and enough preceding context to expose request
        # builders, operation IDs, GetResultAsync and native interop calls.
        call_indexes = []
        for i, insn in enumerate(insns):
            if is_call(insn):
                call_indexes.append(i)

        show = set()
        for i in call_indexes:
            rendered = render_operand(
                pe, insns[i].operand, method_parents, field_parents
            )
            if (
                "GamepadDeviceManager::" in rendered
                or "GamepadInterop::" in rendered
                or "GetResultAsync" in rendered
                or "readDevice" in rendered
                or "receiveDevice" in rendered
                or "SendDeviceData" in rendered
            ):
                show.update(range(max(0, i - 10), min(len(insns), i + 5)))

        # The native request call is commonly an unqualified MethodDef on
        # GamepadInterop. If filtering found too little, include the first
        # 70 instructions so the operation constructor is still visible.
        if len(show) < 12:
            show.update(range(min(70, len(insns))))

        last = None
        for i in sorted(show):
            if last is not None and i != last + 1:
                print("  ...")
            print(
                format_insn(
                    pe, insns[i], code_start,
                    method_parents, field_parents
                )
            )
            last = i

    print(f"\nManager state machines dumped: {found}")

    print("\n=== 3. COVERAGE CHECK ===")
    unresolved = []
    for name in called_manager:
        if not any(
            f"<{name}>" in type_name(parent)
            for _, _, parent, _, _, _ in methods
        ):
            unresolved.append(name)

    if unresolved:
        print("Loader manager methods without matching Infrastructure state machine:")
        for name in unresolved:
            print("  -", name)
    else:
        print("All loader-reached manager methods have matching Infrastructure state machines.")

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
