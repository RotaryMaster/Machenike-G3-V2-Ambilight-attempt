#!/usr/bin/env python3
"""
Static audit of GamepadDeviceManager connection/bootstrap logic.

Targets:
- GamepadDeviceManager::.ctor
- ConnectDeviceAsync wrapper
- <ConnectDeviceAsync> state machine
- any directly called GamepadDeviceManager methods from that state machine

No managed code is executed. No HID/USB/SCSI access is performed.
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
            return getattr(s, "value", None)
        except Exception:
            return InvalidToken(token.value)

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


def tname(row):
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
        return f"{tname(parent)}::{getattr(row, 'Name', '?')}"

    if cls == "MethodDefRow":
        return f"{tname(method_parents.get(rid))}::{getattr(row, 'Name', '?')}"

    if cls == "FieldRow":
        return f"{tname(field_parents.get(rid))}::{getattr(row, 'Name', '?')}"

    if cls in ("TypeDefRow", "TypeRefRow"):
        return tname(row)

    if cls == "MethodSpecRow":
        base = getattr(getattr(row, "Method", None), "row", None)
        if base is None:
            return "MethodSpec"
        if base.__class__.__name__ == "MemberRefRow":
            parent = getattr(getattr(base, "Class", None), "row", None)
            return f"MethodSpec::{tname(parent)}::{getattr(base, 'Name', '?')}"
        return f"MethodSpec::{getattr(base, 'Name', '?')}"

    return f"{cls}:{getattr(row, 'Name', '')}"


def operand_text(pe, operand, method_parents, field_parents):
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


def inventory(pe):
    method_parents, field_parents = parent_maps(pe)
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
                method_parents.get(rid),
                body,
                list(body.instructions),
                body.offset + body.header_size,
            )
        )
    return method_parents, field_parents, methods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    args = ap.parse_args()

    root = Path(args.root)
    dll = root / "Machenik.Infrastructure.dll"
    if not dll.is_file():
        raise SystemExit(f"Missing: {dll}")

    pe = dnfile.dnPE(str(dll))
    method_parents, field_parents, methods = inventory(pe)

    print("=" * 120)
    print("GAMEPAD CONNECTION / PRE-LOADER BOOTSTRAP — STATIC CIL AUDIT")
    print("=" * 120)
    print("Assembly:", dll)

    selected = []
    connect_calls = set()

    # First select ctor, ConnectDeviceAsync wrapper and its generated state machine.
    for item in methods:
        rid, row, parent, body, insns, code_start = item
        pn = tname(parent)
        mn = str(getattr(row, "Name", "") or "")
        if (
            pn == "Machenik.Infrastructure.Manager.GamepadDeviceManager"
            and mn in (".ctor", "ConnectDeviceAsync")
        ) or (
            "<ConnectDeviceAsync>" in pn and mn == "MoveNext"
        ):
            selected.append(item)

    # Discover direct GamepadDeviceManager calls from the ConnectDeviceAsync state machine.
    for rid, row, parent, body, insns, code_start in selected:
        pn = tname(parent)
        if "<ConnectDeviceAsync>" not in pn:
            continue
        for insn in insns:
            op = getattr(insn, "mnemonic", str(insn.opcode))
            if op not in ("call", "callvirt", "newobj"):
                continue
            text = operand_text(pe, insn.operand, method_parents, field_parents)
            if "Machenik.Infrastructure.Manager.GamepadDeviceManager::" in text:
                name = text.split("::")[-1]
                connect_calls.add(name)

    # Add state machines/wrappers for those directly reached methods.
    for item in methods:
        rid, row, parent, body, insns, code_start = item
        pn = tname(parent)
        mn = str(getattr(row, "Name", "") or "")
        if (
            pn == "Machenik.Infrastructure.Manager.GamepadDeviceManager"
            and mn in connect_calls
        ) or (
            mn == "MoveNext"
            and any(f"<{name}>" in pn for name in connect_calls)
        ):
            if item not in selected:
                selected.append(item)

    selected.sort(key=lambda x: x[0])

    print("\nDirect manager calls discovered from ConnectDeviceAsync:")
    if connect_calls:
        for name in sorted(connect_calls):
            print("  -", name)
    else:
        print("  (none)")

    for rid, row, parent, body, insns, code_start in selected:
        pn = tname(parent)
        mn = str(getattr(row, "Name", "") or "")
        print("\n" + "-" * 120)
        print(f"{pn}::{mn}  token=0x{0x06000000 | rid:08X}  size={body.size}")
        print("-" * 120)

        for insn in insns:
            line = format_insn(
                pe, insn, code_start, method_parents, field_parents
            )
            optext = operand_text(
                pe, insn.operand, method_parents, field_parents
            )
            if any(k in optext for k in (
                "GamepadInterop::",
                "HidDevice::",
                "GamepadDeviceManager::",
                "CreateTaskCompletionSource",
                "GetResultAsync",
                "SendDeviceData",
                "Read",
                "Write",
            )):
                line += "    <=="
            print(line)

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
