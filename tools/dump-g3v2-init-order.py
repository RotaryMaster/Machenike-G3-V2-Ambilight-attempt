#!/usr/bin/env python3
"""
Focused static audit of the G3V2 hardware initialization path.

Reports:
1) exact CIL for GamepadDeviceManager.GetDeviceCustomDataAsync state machine,
   including the argument passed to GamepadInterop.readDeviceCustomData;
2) exact ordered IGamepadHardwareLoader/GamepadDeviceManager calls made by
   InitializeFromHardwareAsync;
3) exact CIL around every readDeviceCustomData call in Machenik.Infrastructure.dll.

No managed/native code is executed.
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


class Reader(CilMethodBodyReaderBase):
    def __init__(self, pe, row):
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


def resolve(pe: dnfile.dnPE, token: Token) -> Any:
    if isinstance(token, StringToken):
        try:
            s = pe.net.user_strings.get(token.rid)
        except Exception:
            return InvalidToken(token.value)
        if s is None:
            return InvalidToken(token.value)
        return getattr(s, "value", None)

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


def render_row(row, method_parents, field_parents, rid=None) -> str:
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


def operand_text(pe, operand, method_parents, field_parents) -> str:
    if isinstance(operand, StringToken):
        v = resolve(pe, operand)
        return repr(v) if isinstance(v, str) else str(v)

    if isinstance(operand, Token):
        row = resolve(pe, operand)
        if isinstance(row, str):
            return repr(row)
        return render_row(row, method_parents, field_parents, operand.rid)

    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"

    return "" if operand is None else str(operand)


def format_insn(pe, insn, code_start, method_parents, field_parents):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = operand_text(pe, insn.operand, method_parents, field_parents)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def inventory(path: Path):
    pe = dnfile.dnPE(str(path))
    method_parents, field_parents = parent_maps(pe)
    out = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(Reader(pe, row))
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
    return pe, method_parents, field_parents, out


def dump_method(title, pe, mp, fp, item):
    rid, row, parent, body, insns, code_start = item
    print("\n" + "=" * 128)
    print(title)
    print(f"{type_name(parent)}::{getattr(row, 'Name', '?')} token=0x{0x06000000 | rid:08X}")
    print("=" * 128)
    for insn in insns:
        line = format_insn(pe, insn, code_start, mp, fp)
        text = operand_text(pe, insn.operand, mp, fp)
        if any(k in text for k in (
            "readDeviceCustomData",
            "GetDeviceCustomDataAsync",
            "GetResultAsync",
            "CreateTaskCompletionSource",
            "LoadKeyMapAsync",
            "LoadTurboAsync",
            "LoadRockerAsync",
            "LoadTriggerAsync",
            "LoadMotorAsync",
            "LoadLightAsync",
            "LoadCustomDataAsync",
            "LoadSensorAsync",
        )):
            line += "    <=="
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    args = ap.parse_args()

    root = Path(args.root)
    infra = root / "Machenik.Infrastructure.dll"
    app = root / "Machenik.exe"

    for p in (infra, app):
        if not p.is_file():
            raise SystemExit(f"Missing: {p}")

    ipe, imp, ifp, imethods = inventory(infra)
    ape, amp, afp, amethods = inventory(app)

    print("=" * 128)
    print("G3V2 INIT ORDER + CUSTOMDATA SELECTOR — STATIC CIL AUDIT")
    print("=" * 128)

    # 1. Exact custom data manager state machine.
    custom_items = [
        item for item in imethods
        if (
            "<GetDeviceCustomDataAsync>" in type_name(item[2])
            and str(getattr(item[1], "Name", "")) == "MoveNext"
        )
    ]
    if not custom_items:
        print("\nERROR: GetDeviceCustomDataAsync state machine not found.")
    for item in custom_items:
        dump_method("CUSTOMDATA MANAGER STATE MACHINE", ipe, imp, ifp, item)

    # 2. All call sites to native readDeviceCustomData, with nearby instructions.
    print("\n" + "=" * 128)
    print("ALL readDeviceCustomData CALL SITES")
    print("=" * 128)
    found = 0
    for item in imethods:
        rid, row, parent, body, insns, code_start = item
        for idx, insn in enumerate(insns):
            text = operand_text(ipe, insn.operand, imp, ifp)
            if "readDeviceCustomData" not in text:
                continue
            found += 1
            print(
                f"\n{type_name(parent)}::{getattr(row, 'Name', '?')} "
                f"token=0x{0x06000000 | rid:08X}"
            )
            lo = max(0, idx - 12)
            hi = min(len(insns), idx + 8)
            for j in range(lo, hi):
                line = format_insn(ipe, insns[j], code_start, imp, ifp)
                if j == idx:
                    line += "    <== CALL"
                print(line)
    if not found:
        print("(none resolved by name)")

    # 3. InitializeFromHardwareAsync full state machine and concise ordered call list.
    init_items = [
        item for item in amethods
        if (
            "<InitializeFromHardwareAsync>" in type_name(item[2])
            and str(getattr(item[1], "Name", "")) == "MoveNext"
        )
    ]

    for item in init_items:
        dump_method("INITIALIZE FROM HARDWARE — FULL CIL", ape, amp, afp, item)

        print("\nORDERED HARDWARE CALLS")
        print("-" * 128)
        _, _, _, _, insns, code_start = item
        n = 0
        for insn in insns:
            text = operand_text(ape, insn.operand, amp, afp)
            if (
                "IGamepadHardwareLoader::" in text
                or "GamepadDeviceManager::" in text
            ):
                op = getattr(insn, "mnemonic", str(insn.opcode))
                if op not in ("call", "callvirt", "newobj"):
                    continue
                n += 1
                iloff = insn.offset - code_start
                print(f"{n:02d}. IL+0x{iloff:04X}  {text}")
        if n == 0:
            print("(none)")

    if not init_items:
        print("\nERROR: InitializeFromHardwareAsync state machine not found.")

    print("\nNo managed/native code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
