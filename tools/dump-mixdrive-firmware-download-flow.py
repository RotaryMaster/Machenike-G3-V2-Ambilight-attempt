#!/usr/bin/env python3
"""
Static reconstruction of the MixDrive firmware-check/download flow.

READ-ONLY: parses .NET metadata/CIL only. Does not execute MixDrive, contact
the network, touch the gamepad, or launch an updater.

Usage:
  python tools/dump-mixdrive-firmware-download-flow.py /path/to/mixdrive-extraido

Focus:
  * CheckFirmwareUpdateAsync
  * UpdateGamepadFirmwareAsync
  * UpdateFirmwareAsync
  * LoadFirmwareVersionAsync
  * FirmwareUpdateUrl / firmwareUpdateApiService
  * generated async state-machine MoveNext methods
  * literal URLs / paths / filenames / HTTP-related calls

The goal is to learn the exact server request and local staging path so the
same request can later be reproduced without falsifying the controller itself.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import dnfile
from dnfile.enums import MetadataTables
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken

TABLES = {table.value: table.name for table in MetadataTables}

TARGET_TERMS = (
    "CheckFirmwareUpdateAsync",
    "UpdateGamepadFirmwareAsync",
    "UpdateFirmwareAsync",
    "LoadFirmwareVersionAsync",
    "FirmwareUpdateUrl",
    "firmwareUpdateApiService",
    "FirmwareUpdateApi",
)

INTERESTING = (
    "http", "firmware", "update", "download", "url", "uri", "path",
    "file", "temp", "exe", "zip", "bin", "fot", "stream",
    "getasync", "readasstream", "downloadfile", "start",
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
        return f"MethodSpec::{format_row(base)}"
    return f"{cls}:{row}"


def fmt_operand(pe, operand):
    if isinstance(operand, Token):
        resolved = resolve_token(pe, operand)
        if isinstance(resolved, str):
            return f"0x{operand.value:08X} {resolved!r}"
        return f"0x{operand.value:08X} {format_row(resolved)}"
    if isinstance(operand, list):
        return "[" + ", ".join(f"IL+0x{x:04X}" for x in operand) + "]"
    return "" if operand is None else str(operand)


def parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []):
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out


def iter_ascii_utf16(data: bytes, min_len=5):
    # ASCII
    start = None
    for i, b in enumerate(data):
        if 0x20 <= b <= 0x7E:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_len:
                yield start, "ascii", data[start:i].decode("ascii", "replace")
            start = None
    # UTF16LE
    i = 0
    while i + 1 < len(data):
        if 0x20 <= data[i] <= 0x7E and data[i+1] == 0:
            st = i
            chars = []
            while i + 1 < len(data) and 0x20 <= data[i] <= 0x7E and data[i+1] == 0:
                chars.append(chr(data[i]))
                i += 2
            if len(chars) >= min_len:
                yield st, "utf16", "".join(chars)
        else:
            i += 1


def is_target(parent: str, method: str) -> bool:
    blob = f"{parent}::{method}".lower()
    return any(x.lower() in blob for x in TARGET_TERMS)


def dump_method(pe, rid, row, parent):
    try:
        body = CilMethodBody(DnfileReader(pe, row))
    except Exception as e:
        print(f"  [cannot decode body: {e}]")
        return

    code_start = body.offset + body.header_size
    print(f"RID={rid} token=0x{0x06000000|rid:08X} RVA=0x{row.Rva:08X}")
    print(f"{type_name(parent)}::{row.Name}")
    print(f"code_size={body.code_size}")

    # Print all instructions for targeted methods, but mark high-value calls/strings.
    for ins in body.instructions:
        iloff = ins.offset - code_start
        op = getattr(ins, "mnemonic", str(ins.opcode))
        operand = fmt_operand(pe, ins.operand)
        low = operand.lower()
        mark = " <==" if any(k in low for k in INTERESTING) else ""
        print(f"  IL+0x{iloff:04X} {op:<13} {operand}{mark}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    args = ap.parse_args()

    root = Path(args.root)
    assemblies = [
        root / "Machenik.exe",
        root / "Machenik.Infrastructure.dll",
        root / "Machenik.Shared.dll",
    ]

    print("=" * 112)
    print("MIXDRIVE FIRMWARE CHECK / DOWNLOAD FLOW — STATIC CIL AUDIT")
    print("=" * 112)
    print("Root:", root)
    print("No network/device access will be performed.")

    for asm in assemblies:
        if not asm.is_file():
            continue
        print("\n" + "=" * 112)
        print(asm.name)
        print("=" * 112)

        data = asm.read_bytes()
        print("\n=== URL / FILE / UPDATE STRINGS ===")
        rows = []
        for off, kind, s in iter_ascii_utf16(data):
            lo = s.lower()
            if (
                "http://" in lo or "https://" in lo or
                any(k in lo for k in ("firmware", "update", ".exe", ".zip", ".bin", ".fot", "temp"))
            ):
                rows.append((off, kind, s))
        for off, kind, s in rows[:500]:
            print(f"0x{off:08X} {kind:6s} {s}")
        if len(rows) > 500:
            print(f"... {len(rows)-500} more omitted")

        pe = dnfile.dnPE(str(asm))
        if not getattr(pe, "net", None):
            continue
        pmap = parent_map(pe)

        print("\n=== TARGET METHODS / STATE MACHINES ===")
        found = 0
        for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
            parent = pmap.get(rid)
            pname = type_name(parent)
            mname = str(row.Name)
            if is_target(pname, mname):
                found += 1
                print("\n---")
                dump_method(pe, rid, row, parent)

        if not found:
            print("(none)")

        print("\n=== MEMBERREFS RELATED TO HTTP / DOWNLOAD / PATHS ===")
        mrefs = getattr(pe.net.mdtables, "MemberRef", None)
        if mrefs:
            for i, row in enumerate(mrefs.rows, 1):
                text = format_row(row)
                if any(k in text.lower() for k in INTERESTING):
                    print(f"0x{0x0A000000|i:08X} {text}")

    print("\n" + "=" * 112)
    print("WHAT TO EXTRACT FROM THE OUTPUT")
    print("=" * 112)
    print("* exact API method and endpoint/base URL")
    print("* request parameters/model fields (brand, product_name, product_type, version, etc.)")
    print("* returned FirmwareUpdateUrl or equivalent")
    print("* local destination path / temp filename")
    print("* whether the app downloads an updater EXE directly or a separate BIN/FOT/ZIP")
    print("* Process.Start / shell launch arguments")
    print("\nStatic metadata/CIL parsing only.")


if __name__ == "__main__":
    main()
