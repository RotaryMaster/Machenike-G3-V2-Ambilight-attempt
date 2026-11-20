#!/usr/bin/env python3
"""
Read-only CIL xref finder for EnterUpdateModeMessage in Machenik.exe.

Finds methods that construct/reference EnterUpdateModeMessage and prints a
focused IL window around each reference. Also reports nearby Send/Publish calls.

No managed code is executed.
No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
from typing import Any

import dnfile
from dnfile.enums import MetadataTables
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken

TABLES = {table.value: table.name for table in MetadataTables}
TARGET = "EnterUpdateModeMessage"


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
    ns = getattr(row, "TypeNamespace", "")
    name = getattr(row, "TypeName", "")
    if name:
        return f"{ns}.{name}".strip(".")
    return str(row)


def method_parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []):
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out


def format_row(row: Any) -> str:
    if row is None:
        return "?"
    cls = row.__class__.__name__

    if cls == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{type_name(parent)}::{row.Name}"

    if cls == "MethodDefRow":
        return f"MethodDef::{row.Name}"

    if cls in ("TypeRefRow", "TypeDefRow"):
        return type_name(row)

    if cls == "MethodSpecRow":
        base = getattr(getattr(row, "Method", None), "row", None)
        return f"MethodSpec::{format_row(base)}"

    if cls == "FieldRow":
        return f"Field::{row.Name}"

    return f"{cls}:{row}"


def format_operand(pe, operand: Any, method_parents) -> str:
    if isinstance(operand, Token):
        resolved = resolve_token(pe, operand)
        if isinstance(resolved, str):
            return f"token=0x{operand.value:08X} {resolved!r}"
        if resolved.__class__.__name__ == "MethodDefRow":
            rid = operand.rid
            parent = method_parents.get(rid)
            return (
                f"token=0x{operand.value:08X} "
                f"{type_name(parent)}::{getattr(resolved, 'Name', '?')}"
            )
        return f"token=0x{operand.value:08X} {format_row(resolved)}"
    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"
    if operand is None:
        return ""
    return str(operand)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly", help="Path to Machenik.exe")
    ap.add_argument("--context", type=int, default=35)
    args = ap.parse_args()

    pe = dnfile.dnPE(args.assembly)
    parents = method_parent_map(pe)

    print("=" * 108)
    print("ENTER UPDATE MODE — STATIC XREF")
    print("=" * 108)
    print("Assembly:", args.assembly)

    # Inventory matching metadata first.
    print("\n=== MATCHING TYPES ===")
    found_types = False
    for table_name in ("TypeDef", "TypeRef"):
        table = getattr(pe.net.mdtables, table_name, None)
        if not table:
            continue
        for rid, row in enumerate(table.rows, 1):
            if TARGET in str(getattr(row, "TypeName", "")):
                found_types = True
                token_base = 0x02000000 if table_name == "TypeDef" else 0x01000000
                print(
                    f"{table_name} token=0x{token_base|rid:08X} "
                    f"{type_name(row)}"
                )
    if not found_types:
        print("No matching type metadata found.")

    matches = []

    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(DnfileReader(pe, row))
        except Exception:
            continue

        insns = list(body.instructions)
        code_start = body.offset + body.header_size
        hit_indices = []

        for i, insn in enumerate(insns):
            rendered = format_operand(pe, insn.operand, parents)
            op = getattr(insn, "mnemonic", str(insn.opcode))
            # Strong xref: operand resolves directly to the message type/member.
            if TARGET in rendered:
                hit_indices.append(i)
                continue
            # Secondary signal: method names / strings around update-mode send.
            if TARGET.lower() in rendered.lower():
                hit_indices.append(i)
                continue
            if isinstance(insn.operand, str) and "update" in insn.operand.lower():
                # Do not promote generic update strings alone.
                pass

        if hit_indices:
            matches.append((rid, row, body, insns, code_start, hit_indices))

    print(f"\nMethods referencing {TARGET}: {len(matches)}")

    for rid, row, body, insns, code_start, hit_indices in matches:
        parent = parents.get(rid)
        print("\n" + "=" * 108)
        print(f"{type_name(parent)}::{row.Name}")
        print("=" * 108)
        print(f"RID   : {rid}")
        print(f"Token : 0x{0x06000000|rid:08X}")
        print(f"RVA   : 0x{row.Rva:08X}")
        print(f"Size  : {body.code_size}")
        print("Hits  :", ", ".join(str(i) for i in hit_indices))

        show = set()
        for hit in hit_indices:
            a = max(0, hit - args.context)
            b = min(len(insns), hit + args.context + 1)
            show.update(range(a, b))

        last = None
        for i in sorted(show):
            if last is not None and i != last + 1:
                print("...")
            insn = insns[i]
            iloff = insn.offset - code_start
            raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
            op = getattr(insn, "mnemonic", str(insn.opcode))
            operand = format_operand(pe, insn.operand, parents)
            marker = "  <==" if i in hit_indices else ""
            print(
                f"IL+0x{iloff:04X}  {raw:<28} "
                f"{op:<14} {operand}{marker}"
            )
            last = i

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
