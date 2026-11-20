#!/usr/bin/env python3
"""
Static audit of Machenik.Shared.Services.GamepadHardwareLoader read sequence.

Prints the async state machines for:
- LoadKeyMapAsync
- LoadTurboAsync
- LoadRockerAsync
- LoadTriggerAsync
- LoadMotorAsync
- LoadLightAsync

and extracts all calls into GamepadDeviceManager/GamepadInterop so the exact
native read order can be reconstructed.

No assembly is executed. No HID/USB/SCSI access is performed.
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
TARGETS = (
    "LoadKeyMapAsync",
    "LoadTurboAsync",
    "LoadRockerAsync",
    "LoadTriggerAsync",
    "LoadMotorAsync",
    "LoadLightAsync",
)


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
        if base.__class__.__name__ == "MethodDefRow":
            rid2 = None
            try:
                rid2 = pe.net.mdtables.MethodDef.rows.index(base) + 1
            except Exception:
                pass
            return f"MethodSpec::{render_row(base, method_parents, field_parents, rid2)}"
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


def fmt(pe, insn, code_start, mp, fp):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = operand_text(pe, insn.operand, mp, fp)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def inventory(pe):
    mp, fp = parent_maps(pe)
    methods = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(Reader(pe, row))
        except Exception:
            continue
        methods.append(
            (
                rid,
                row,
                mp.get(rid),
                body,
                list(body.instructions),
                body.offset + body.header_size,
            )
        )
    return mp, fp, methods


def relevant_state_machine(parent_name, method_name):
    if method_name != "MoveNext":
        return False
    return any(f"<{target}>" in parent_name for target in TARGETS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    args = ap.parse_args()

    dll = Path(args.root) / "Machenik.Shared.dll"
    if not dll.is_file():
        raise SystemExit(f"Missing: {dll}")

    pe = dnfile.dnPE(str(dll))
    mp, fp, methods = inventory(pe)

    print("=" * 132)
    print("GAMEPAD HARDWARE LOADER — EXACT READ ORDER (STATIC CIL)")
    print("=" * 132)
    print(f"Assembly: {dll}")

    matches = []
    for item in methods:
        rid, row, parent, body, insns, code_start = item
        pname = type_name(parent)
        mname = str(getattr(row, "Name", ""))
        if relevant_state_machine(pname, mname):
            matches.append(item)

    if not matches:
        print("No target async state machines found.")
        return

    # sort by requested logical method order, not metadata order
    def target_index(item):
        pname = type_name(item[2])
        for i, target in enumerate(TARGETS):
            if f"<{target}>" in pname:
                return i
        return 999

    matches.sort(key=target_index)

    summary = []

    for item in matches:
        rid, row, parent, body, insns, code_start = item
        pname = type_name(parent)
        target = next((t for t in TARGETS if f"<{t}>" in pname), pname)

        print("\n" + "=" * 132)
        print(f"{target}  |  {pname}::MoveNext  token=0x{0x06000000 | rid:08X}")
        print("=" * 132)

        calls = []
        for insn in insns:
            text = operand_text(pe, insn.operand, mp, fp)
            op = getattr(insn, "mnemonic", str(insn.opcode))
            interesting = (
                "GamepadDeviceManager::" in text
                or "GamepadInterop::" in text
                or "readDevice" in text
                or "GetDevice" in text
            )
            line = fmt(pe, insn, code_start, mp, fp)
            if interesting and op in ("call", "callvirt", "newobj"):
                line += "    <=="
                calls.append((insn.offset - code_start, text))
            print(line)

        summary.append((target, calls))

    print("\n" + "=" * 132)
    print("ORDERED DEVICE/MANAGER CALL SUMMARY")
    print("=" * 132)
    for target, calls in summary:
        print(f"\n{target}:")
        n = 0
        for iloff, text in calls:
            if (
                "GamepadDeviceManager::" not in text
                and "GamepadInterop::" not in text
            ):
                continue
            n += 1
            print(f"  {n:02d}. IL+0x{iloff:04X}  {text}")
        if n == 0:
            print("  (no direct manager/interop call resolved)")

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
