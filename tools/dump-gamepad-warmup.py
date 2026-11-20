#!/usr/bin/env python3
"""
Focused read-only CIL audit of the MixDrive gamepad warm-up path.

Purpose:
- recover the order of first-party calls made by GamepadConfigInitializer
  before normal gamepad configuration is considered initialized;
- list only LOAD/INITIALIZE call sites of GetDeviceCustomDataAsync;
- show whether those same warm-up methods call WriteDeviceCustomDataAsync.

This intentionally ignores third-party assemblies and does not execute
managed code.

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

CUSTOM_READ = "GamepadDeviceManager::GetDeviceCustomDataAsync"
CUSTOM_WRITE = "GamepadDeviceManager::WriteDeviceCustomDataAsync"

# Generated async state-machine type names that matter for the startup path.
PRIMARY_PARENT_HINTS = (
    "<InitializeFromHardwareAsync>",
    "<InitializeConfigFromHardwareAsync>",
)

# Only report CustomData xrefs from startup/load methods by default.
WARM_METHOD_HINTS = (
    "Initialize",
    "Load",
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
            row,
            method_parents,
            field_parents,
            rid=operand.rid,
        )

    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"

    return "" if operand is None else str(operand)


def get_methods(pe):
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


def line_for(pe, insn, code_start, method_parents, field_parents):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = render_operand(pe, insn.operand, method_parents, field_parents)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def first_party_call(text: str) -> bool:
    return (
        "Machenik." in text
        or "GamepadDeviceManager::" in text
        or "IGamepadHardwareLoader::" in text
    )


def warm_method(parent_name: str, method_name: str) -> bool:
    blob = f"{parent_name}::{method_name}"
    return any(h.lower() in blob.lower() for h in WARM_METHOD_HINTS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly", help="Path to Machenik.exe")
    ap.add_argument(
        "--all-customdata-xrefs",
        action="store_true",
        help="also show non-load/non-initialize CustomData xrefs",
    )
    args = ap.parse_args()

    pe = dnfile.dnPE(args.assembly)
    method_parents, field_parents, methods = get_methods(pe)

    print("=" * 112)
    print("GAMEPAD WARM-UP / CUSTOMDATA — STATIC CIL AUDIT")
    print("=" * 112)
    print("Assembly:", args.assembly)

    print("\n=== 1. INITIALIZATION STATE MACHINES — FIRST-PARTY CALL ORDER ===")
    found_primary = 0

    for rid, row, parent, body, insns, code_start in methods:
        pn = type_name(parent)
        mn = str(getattr(row, "Name", "") or "")
        blob = f"{pn}::{mn}"

        if not (
            mn == "MoveNext"
            and any(h.lower() in pn.lower() for h in PRIMARY_PARENT_HINTS)
        ):
            continue

        found_primary += 1
        print("\n" + "-" * 112)
        print(f"{blob} token=0x{0x06000000 | rid:08X}")

        for insn in insns:
            op = getattr(insn, "mnemonic", str(insn.opcode))
            if op not in ("call", "callvirt", "newobj"):
                continue
            rendered = render_operand(
                pe, insn.operand, method_parents, field_parents
            )
            if first_party_call(rendered):
                print(
                    line_for(
                        pe, insn, code_start,
                        method_parents, field_parents
                    )
                )

    if not found_primary:
        print("No matching initialization state machine found.")

    print("\n=== 2. CUSTOMDATA READ XREFS IN LOAD/INITIALIZE METHODS ===")
    read_count = 0

    for rid, row, parent, body, insns, code_start in methods:
        pn = type_name(parent)
        mn = str(getattr(row, "Name", "") or "")
        if not args.all_customdata_xrefs and not warm_method(pn, mn):
            continue

        hits = []
        for i, insn in enumerate(insns):
            rendered = render_operand(
                pe, insn.operand, method_parents, field_parents
            )
            if CUSTOM_READ in rendered:
                hits.append(i)

        if not hits:
            continue

        read_count += len(hits)
        print("\n" + "-" * 112)
        print(f"{pn}::{mn} token=0x{0x06000000 | rid:08X}")

        for hit in hits:
            a = max(0, hit - 4)
            b = min(len(insns), hit + 7)
            for i in range(a, b):
                marker = "  <== CUSTOMDATA READ" if i == hit else ""
                print(
                    line_for(
                        pe, insns[i], code_start,
                        method_parents, field_parents
                    ) + marker
                )
            print("...")

    print(f"\nWarm/load CustomData read call sites: {read_count}")

    print("\n=== 3. CUSTOMDATA WRITES INSIDE THE SAME LOAD/INITIALIZE CLASS OF METHODS ===")
    write_count = 0

    for rid, row, parent, body, insns, code_start in methods:
        pn = type_name(parent)
        mn = str(getattr(row, "Name", "") or "")
        if not warm_method(pn, mn):
            continue

        for i, insn in enumerate(insns):
            rendered = render_operand(
                pe, insn.operand, method_parents, field_parents
            )
            if CUSTOM_WRITE not in rendered:
                continue
            write_count += 1
            print(
                f"{pn}::{mn} token=0x{0x06000000 | rid:08X}  "
                + line_for(
                    pe, insn, code_start,
                    method_parents, field_parents
                )
            )

    print(f"Warm/load CustomData write call sites: {write_count}")

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
