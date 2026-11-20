#!/usr/bin/env python3
"""
Static audit of the MixDrive firmware-check/download/launch flow.

READ-ONLY:
  - parses Machenik.exe metadata/CIL and printable strings
  - never opens the controller
  - never performs HTTP requests
  - never launches the updater

Usage:
  python tools/dump-mixdrive-firmware-update-flow.py \
      /path/to/mixdrive-extraido/Machenik.exe

Goal:
  Recover enough of the managed flow to reproduce ONLY the catalog/download
  request outside MixDrive, without executing EnterUpdateModeMessage or
  Process.Start.
"""

from __future__ import annotations

import argparse
import re
from typing import Any

import dnfile
from dnfile.enums import MetadataTables
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken

TABLES = {table.value: table.name for table in MetadataTables}

NAME_HINTS = (
    "FirmwareUpdate",
    "UpdateFirmware",
    "CheckFirmware",
    "LoadFirmwareVersion",
    "Download",
    "EnterUpdateMode",
)

CALL_HINTS = (
    "HttpClient",
    "HttpRequest",
    "ReadAsStream",
    "GetAsync",
    "PostAsync",
    "SendAsync",
    "Download",
    "FileStream",
    "WriteAllBytes",
    "WriteAllText",
    "CopyToAsync",
    "Path",
    "GetTempPath",
    "ProcessStartInfo",
    "Process::Start",
    "Process::WaitForExit",
    "EnterUpdateModeMessage",
    "FirmwareUpdateUrl",
    "FirmwareVersion",
)

STRING_HINTS = (
    "http://", "https://", "firmware", "update", ".exe", ".bin", ".fot",
    "MACHENIKE", "G3V2", "product_name", "product_type", "brand",
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
        inst = _blob_to_bytes(getattr(row, "Instantiation", None))
        return f"MethodSpec::{format_row(base)} <blob={inst.hex(' ') if inst else ''}>"
    return f"{cls}:{row}"


def format_operand(pe: dnfile.dnPE, operand: Any) -> str:
    if isinstance(operand, Token):
        resolved = resolve_token(pe, operand)
        if isinstance(resolved, str):
            return f"token=0x{operand.value:08X} {resolved!r}"
        return f"token=0x{operand.value:08X} {format_row(resolved)}"
    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"
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


def printable_strings(data: bytes, min_len=5):
    rows = []
    start = None
    for i, b in enumerate(data):
        if 0x20 <= b <= 0x7E:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_len:
                rows.append((start, "ascii", data[start:i].decode("ascii", "replace")))
            start = None
    if start is not None and len(data) - start >= min_len:
        rows.append((start, "ascii", data[start:].decode("ascii", "replace")))

    i = 0
    while i + 1 < len(data):
        if 0x20 <= data[i] <= 0x7E and data[i + 1] == 0:
            s = i
            chars = []
            while i + 1 < len(data) and 0x20 <= data[i] <= 0x7E and data[i+1] == 0:
                chars.append(chr(data[i]))
                i += 2
            if len(chars) >= min_len:
                rows.append((s, "utf16", "".join(chars)))
        else:
            i += 1
    return sorted(rows)


def wanted_method(parent: str, name: str) -> bool:
    combined = f"{parent}::{name}"
    if any(h.lower() in combined.lower() for h in NAME_HINTS):
        return True
    # Async state machines are often named MoveNext under
    # <UpdateFirmwareAsync>d__N, etc.
    return name == "MoveNext" and any(h.lower() in parent.lower() for h in NAME_HINTS)


def dump_method(pe, rid, row, parent):
    ptn = type_name(parent)
    print("\n" + "=" * 112)
    print(f"{ptn}::{row.Name}")
    print("=" * 112)
    print(f"RID   : {rid}")
    print(f"Token : 0x{0x06000000 | rid:08X}")
    print(f"RVA   : 0x{row.Rva:08X}")

    if not row.Rva:
        print("No CIL body.")
        return

    try:
        body = CilMethodBody(DnfileReader(pe, row))
    except Exception as e:
        print("Could not parse CIL:", e)
        return

    code_start = body.offset + body.header_size
    insns = list(body.instructions)

    # Print only windows around relevant strings/calls for very large methods.
    hits = []
    for n, insn in enumerate(insns):
        text = ""
        if isinstance(insn.operand, Token):
            r = resolve_token(pe, insn.operand)
            if isinstance(r, str):
                text = r
            else:
                text = format_row(r)
        if any(h.lower() in text.lower() for h in CALL_HINTS + STRING_HINTS):
            hits.append(n)

    if not hits:
        # For small wrapper methods, print all; otherwise just identify it.
        if body.code_size <= 600:
            ranges = [(0, len(insns)-1)]
        else:
            print(f"Size  : {body.code_size}; no focused call/string hit.")
            return
    else:
        ranges = []
        for n in hits:
            ranges.append((max(0, n-12), min(len(insns)-1, n+18)))
        # merge ranges
        merged = []
        for a,b in sorted(ranges):
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a,b))
        ranges = merged

    print(f"Size  : {body.code_size}")
    for a,b in ranges:
        print(f"\n--- instructions {a}..{b} ---")
        for insn in insns[a:b+1]:
            iloff = insn.offset - code_start
            raw = " ".join(f"{x:02x}" for x in insn.get_bytes())
            op = getattr(insn, "mnemonic", str(insn.opcode))
            operand = format_operand(pe, insn.operand)
            print(f"IL+0x{iloff:04X}  {raw:<30} {op:<14} {operand}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly", help="Path to Machenik.exe")
    args = ap.parse_args()

    path = args.assembly
    pe = dnfile.dnPE(path)
    parents = parent_map(pe)

    print("=" * 112)
    print("MIXDRIVE FIRMWARE UPDATE / DOWNLOAD FLOW — STATIC AUDIT")
    print("=" * 112)
    print("Assembly:", path)
    print("Policy  : static metadata/CIL only; no HTTP, no process launch, no device I/O")

    print("\n=== PRINTABLE FIRMWARE / URL / PRODUCT STRINGS ===")
    data = open(path, "rb").read()
    rows = []
    for off, kind, s in printable_strings(data):
        if any(h.lower() in s.lower() for h in STRING_HINTS):
            rows.append((off, kind, s))
    for off, kind, s in rows[:500]:
        print(f"0x{off:08X} {kind:6s} {s}")
    if len(rows) > 500:
        print(f"... {len(rows)-500} more omitted")

    matches = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        name = str(getattr(row, "Name", ""))
        parent = parents.get(rid)
        ptn = type_name(parent)
        if wanted_method(ptn, name):
            matches.append((rid, row, parent))

    print("\n=== RELEVANT METHOD INVENTORY ===")
    for rid, row, parent in matches:
        print(f"RID {rid:5d} token 0x{0x06000000|rid:08X} {type_name(parent)}::{row.Name}")

    for rid, row, parent in matches:
        dump_method(pe, rid, row, parent)

    print("\n" + "=" * 112)
    print("SAFE SIMULATION TARGET")
    print("=" * 112)
    print("Recover and replay ONLY the HTTP catalog/download request.")
    print("Do NOT invoke EnterUpdateModeMessage and do NOT Process.Start the updater.")
    print("If the API returns only the latest updater, lowering the local firmware")
    print("version cannot by itself reveal a historical package; a history endpoint,")
    print("predictable CDN URL, cache, old MixDrive build, or archived updater is needed.")
    print("\nNo assembly was executed.")
    print("No network request was performed.")
    print("No HID/USB/SCSI access was performed.")


if __name__ == "__main__":
    main()
