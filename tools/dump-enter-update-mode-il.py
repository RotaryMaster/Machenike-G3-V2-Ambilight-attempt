#!/usr/bin/env python3
"""
Read-only CIL dump focused on the official MixDrive EnterUpdateMode receiver.

Target:
  Machenik.exe
  MethodDef RID 2291 / token 0x060008F3
  <Receive>d__21::MoveNext

No managed code is executed. No HID/USB/SCSI access is performed.
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


def format_row(row: Any) -> str:
    if row is None:
        return "?"
    cls = row.__class__.__name__

    if cls == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{type_name(parent)}::{row.Name}"

    if cls == "MethodDefRow":
        return f"MethodDef::{row.Name}"

    if cls == "FieldRow":
        return f"Field::{row.Name}"

    if cls in ("TypeRefRow", "TypeDefRow"):
        return type_name(row)

    if cls == "MethodSpecRow":
        base = getattr(getattr(row, "Method", None), "row", None)
        inst = getattr(row, "Instantiation", b"") or b""
        return f"MethodSpec::{format_row(base)} <blob={bytes(inst).hex(' ') if inst else ''}>"

    if cls == "TypeSpecRow":
        sig = getattr(row, "Signature", b"") or b""
        return f"TypeSpec(blob={bytes(sig).hex(' ') if sig else ''})"

    return f"{cls}:{row}"


def format_operand(pe: dnfile.dnPE, operand: Any) -> str:
    if isinstance(operand, Token):
        token_value = operand.value
        resolved = resolve_token(pe, operand)
        if isinstance(resolved, str):
            return f"token=0x{token_value:08X} {resolved!r}"
        return f"token=0x{token_value:08X} {format_row(resolved)}"
    if isinstance(operand, list):
        return "[" + ", ".join(f"IL+0x{x:04X}" for x in operand) + "]"
    if isinstance(operand, int):
        return f"0x{operand:X} ({operand})"
    if operand is None:
        return ""
    return str(operand)


def find_parent_typedef(pe, method_rid: int):
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []):
            if getattr(idx, "row_index", None) == method_rid:
                return td
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly")
    ap.add_argument("--rid", type=int, default=2291)
    args = ap.parse_args()

    pe = dnfile.dnPE(args.assembly)
    rid = args.rid
    row = pe.net.mdtables.MethodDef.rows[rid - 1]
    parent = find_parent_typedef(pe, rid)

    print("=" * 100)
    print("MIXDRIVE — FULL IL OF EnterUpdateMode RECEIVER")
    print("=" * 100)
    print(f"Assembly : {args.assembly}")
    print(f"RID      : {rid}")
    print(f"Token    : 0x{0x06000000 | rid:08X}")
    print(f"Type     : {type_name(parent)}")
    print(f"Method   : {row.Name}")
    print(f"RVA      : 0x{row.Rva:08X}")

    body = CilMethodBody(DnfileReader(pe, row))
    print(f"Code size: {body.code_size} bytes")
    print(f"MaxStack : {body.max_stack}")
    if body.local_var_sig_tok:
        print(f"Locals   : 0x{body.local_var_sig_tok.value:08X}")
    else:
        print("Locals   : none")

    code_start = body.offset + body.header_size

    print("\n" + "=" * 100)
    print("ALL INSTRUCTIONS")
    print("=" * 100)

    for insn in body.instructions:
        iloff = insn.offset - code_start
        raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
        op = getattr(insn, "mnemonic", str(insn.opcode))
        operand = format_operand(pe, insn.operand)
        marker = "  <==" if 0x00F0 <= iloff <= 0x0135 else ""
        print(f"IL+0x{iloff:04X}  {raw:<24} {op:<14} {operand}{marker}")

    print("\n" + "=" * 100)
    print("FOCUS WINDOW: IL+0x00E0..0x0135")
    print("=" * 100)

    for insn in body.instructions:
        iloff = insn.offset - code_start
        if 0x00E0 <= iloff <= 0x0135:
            raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
            op = getattr(insn, "mnemonic", str(insn.opcode))
            print(f"IL+0x{iloff:04X}  {raw:<24} {op:<14} {format_operand(pe, insn.operand)}")

    print("\n" + "=" * 100)
    print("TOKENS TO INVESTIGATE IN THE FOCUS WINDOW")
    print("=" * 100)
    seen = set()
    for insn in body.instructions:
        iloff = insn.offset - code_start
        if not (0x00E0 <= iloff <= 0x0135):
            continue
        operand = insn.operand
        if isinstance(operand, Token) and operand.value not in seen:
            seen.add(operand.value)
            print(f"0x{operand.value:08X} -> {format_operand(pe, operand)}")

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
