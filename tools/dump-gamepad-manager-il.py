#!/usr/bin/env python3
"""
Read-only CIL dumper for Machenik.Infrastructure.Manager.GamepadDeviceManager.

Purpose:
  Recover the exact managed packet-continuation logic used by CustomData reads,
  especially CheckPacketIndex / GetPacketCount / MigrateSeq / HandleGenericDeviceData.

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

DEFAULT_NAMES = {
    "CheckPacketIndex",
    "GetPacketCount",
    "MigrateSeq",
    "HandleGenericDeviceData",
    "SendDeviceData",
    "SendNextPacketOrFail",
    "SendDeviceDataAsync",
    "GetDeviceCustomDataAsync",
    "WriteDeviceCustomDataAsync",
    "receiveDataLength",
}

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

def _blob_to_bytes(blob: Any) -> bytes:
    if blob is None:
        return b""
    if isinstance(blob, (bytes, bytearray, memoryview)):
        return bytes(blob)
    value = getattr(blob, "value", None)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    data = getattr(blob, "data", None)
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    try:
        return bytes(blob)
    except Exception:
        return b""

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
        inst = _blob_to_bytes(getattr(row, "Instantiation", None))
        return f"MethodSpec::{format_row(base)} <blob={inst.hex(' ') if inst else ''}>"
    if cls == "TypeSpecRow":
        sig = _blob_to_bytes(getattr(row, "Signature", None))
        return f"TypeSpec(blob={sig.hex(' ') if sig else ''})"
    return f"{cls}:{row}"

def format_operand(pe: dnfile.dnPE, operand: Any) -> str:
    if isinstance(operand, Token):
        resolved = resolve_token(pe, operand)
        if isinstance(resolved, str):
            return f"token=0x{operand.value:08X} {resolved!r}"
        return f"token=0x{operand.value:08X} {format_row(resolved)}"
    if isinstance(operand, list):
        return "[" + ", ".join(f"IL+0x{x:04X}" for x in operand) + "]"
    if isinstance(operand, int):
        return f"0x{operand:X} ({operand})"
    if operand is None:
        return ""
    return str(operand)

def parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []):
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out

def dump_implmap(pe, row):
    table = getattr(pe.net.mdtables, "ImplMap", None)
    if table is None:
        return False
    found = False
    for im in table.rows:
        forwarded = getattr(getattr(im, "MemberForwarded", None), "row", None)
        if forwarded is not row:
            continue
        found = True
        scope = getattr(getattr(im, "ImportScope", None), "row", None)
        dll = str(getattr(scope, "Name", "?"))
        name = str(getattr(im, "ImportName", "?"))
        print(f"P/Invoke: {dll}!{name}")
    return found

def dump_method(pe, rid, row, parent):
    print()
    print("=" * 110)
    print(f"{type_name(parent)}::{row.Name}")
    print("=" * 110)
    print(f"RID   : {rid}")
    print(f"Token : 0x{0x06000000 | rid:08X}")
    print(f"RVA   : 0x{row.Rva:08X}")

    if not row.Rva:
        if not dump_implmap(pe, row):
            print("No method body (RVA=0).")
        return

    try:
        body = CilMethodBody(DnfileReader(pe, row))
    except Exception as e:
        print(f"Could not parse CIL body: {e}")
        return

    print(f"Size  : {body.code_size}")
    print(f"Stack : {body.max_stack}")
    code_start = body.offset + body.header_size

    # dncil branch/switch operands are absolute offsets in the PE stream,
    # not method-relative IL offsets. Recover the operation 28/29 switch
    # targets dynamically and normalize them against code_start.
    if str(getattr(row, "Name", "")) == "HandleGenericDeviceData":
        ranges = []
        switch_insn = next(
            (i for i in body.instructions
             if getattr(i, "mnemonic", str(i.opcode)) == "switch"
             and isinstance(i.operand, list)
             and len(i.operand) >= 22),
            None,
        )
        if switch_insn is not None:
            # The method does: ldloc.0 ; ldc.i4.8 ; sub ; switch.
            # Thus switch slot 20 == operation 28, slot 21 == operation 29.
            def rel(x):
                return x - code_start if isinstance(x, int) and x >= code_start else x
            op28 = rel(switch_insn.operand[20])
            op29 = rel(switch_insn.operand[21])
            start = max(0, op28 - 0x20)
            end = min(body.code_size, op29 + 0x180)
            ranges = [(start, end)]
            print(f"Operation 28 target: IL+0x{op28:04X}")
            print(f"Operation 29 target: IL+0x{op29:04X}")
        if not ranges:
            ranges = [(0, body.code_size)]
        print("Focused ranges: " + ", ".join(
            f"IL+0x{a:04X}..0x{b:04X}" for a, b in ranges
        ))
    else:
        ranges = None

    for insn in body.instructions:
        iloff = insn.offset - code_start
        if ranges is not None and not any(a <= iloff <= b for a, b in ranges):
            continue
        raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
        op = getattr(insn, "mnemonic", str(insn.opcode))
        operand = format_operand(pe, insn.operand)
        print(f"IL+0x{iloff:04X}  {raw:<30} {op:<14} {operand}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly", help="Path to Machenik.Infrastructure.dll")
    ap.add_argument(
        "--name",
        action="append",
        default=[],
        help="Additional exact MethodDef name to dump; may be repeated",
    )
    args = ap.parse_args()

    pe = dnfile.dnPE(args.assembly)
    parents = parent_map(pe)
    names = set(DEFAULT_NAMES)
    names.update(args.name)

    matches = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        name = str(getattr(row, "Name", ""))
        parent = parents.get(rid)
        ptn = type_name(parent)

        if name in names and (
            name == "receiveDataLength"
            or "GamepadDeviceManager" in ptn
            or "Machenik.Infrastructure.Manager" in ptn
        ):
            matches.append((rid, row, parent))

    print("=" * 110)
    print("GAMEPAD DEVICE MANAGER — READ-ONLY CIL DUMP")
    print("=" * 110)
    print(f"Assembly: {args.assembly}")
    print("Targets :", ", ".join(sorted(names)))
    print(f"Matches : {len(matches)}")

    if not matches:
        print("\nNo matching methods found.")
        print("Listing method names containing Packet, Seq, DeviceData or CustomData:")
        for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
            name = str(getattr(row, "Name", ""))
            parent = parents.get(rid)
            ptn = type_name(parent)
            if "GamepadDeviceManager" not in ptn:
                continue
            if any(x.lower() in name.lower() for x in ("packet", "seq", "devicedata", "customdata")):
                print(f"RID {rid:5d} token 0x{0x06000000|rid:08X} {ptn}::{name}")
        return

    for rid, row, parent in matches:
        dump_method(pe, rid, row, parent)

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")

if __name__ == "__main__":
    main()
