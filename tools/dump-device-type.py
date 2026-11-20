#!/usr/bin/env python3
"""
Read-only metadata scanner for device type enums and DeviceModel.get_DeviceType
comparisons across extracted MixDrive assemblies.

No managed code is executed.
No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
from typing import Any

import dnfile
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.clr.token import Token, StringToken, InvalidToken
from dnfile.enums import MetadataTables

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


def blob_bytes(x: Any) -> bytes:
    if x is None:
        return b""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    for attr in ("value", "data"):
        v = getattr(x, attr, None)
        if isinstance(v, (bytes, bytearray, memoryview)):
            return bytes(v)
    try:
        return bytes(x)
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


def method_parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []):
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out


def field_parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "FieldList", []):
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out


def constant_map(pe):
    out = {}
    table = getattr(pe.net.mdtables, "Constant", None)
    if table is None:
        return out
    for row in table.rows:
        parent = getattr(getattr(row, "Parent", None), "row", None)
        if parent is None:
            continue
        out[id(parent)] = row
    return out


def decode_constant(row):
    raw = blob_bytes(getattr(row, "Value", None))
    t = int(getattr(row, "Type", 0) or 0)
    # ELEMENT_TYPE codes used for enum backing constants.
    fmts = {
        0x02: "<?",   # bool
        0x03: "<H",   # char
        0x04: "<b",   # i1
        0x05: "<B",   # u1
        0x06: "<h",   # i2
        0x07: "<H",   # u2
        0x08: "<i",   # i4
        0x09: "<I",   # u4
        0x0a: "<q",   # i8
        0x0b: "<Q",   # u8
        0x0c: "<f",
        0x0d: "<d",
    }
    fmt = fmts.get(t)
    if fmt and len(raw) >= struct.calcsize(fmt):
        try:
            return struct.unpack(fmt, raw[:struct.calcsize(fmt)])[0], raw
        except Exception:
            pass
    return None, raw


def enum_like(td):
    ext = getattr(getattr(td, "Extends", None), "row", None)
    return type_name(ext) == "System.Enum"


def format_operand(pe, operand: Any, parents) -> str:
    if isinstance(operand, Token):
        resolved = resolve_token(pe, operand)
        if isinstance(resolved, str):
            return repr(resolved)
        cls = resolved.__class__.__name__
        if cls == "MemberRefRow":
            parent = getattr(getattr(resolved, "Class", None), "row", None)
            return f"{type_name(parent)}::{resolved.Name}"
        if cls == "MethodDefRow":
            return f"{type_name(parents.get(operand.rid))}::{resolved.Name}"
        if cls in ("TypeRefRow", "TypeDefRow"):
            return type_name(resolved)
        return f"{cls}:{getattr(resolved, 'Name', '')}"
    return str(operand) if operand is not None else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Extracted MixDrive directory")
    args = ap.parse_args()

    root = Path(args.root)

    print("=" * 112)
    print("MIXDRIVE DEVICE TYPE — STATIC METADATA SCAN")
    print("=" * 112)
    print("Root:", root)

    files = sorted(
        [p for p in root.rglob("*") if p.suffix.lower() in (".dll", ".exe")]
    )

    enum_hits = []

    print("\n=== ENUMS / TYPES MATCHING 'DeviceType' OR DEVICE-RELATED ENUMS ===")
    for path in files:
        try:
            pe = dnfile.dnPE(str(path))
            if not getattr(pe, "net", None):
                continue
            consts = constant_map(pe)

            for td in pe.net.mdtables.TypeDef.rows:
                tn = type_name(td)
                if not enum_like(td):
                    continue
                if "devicetype" not in tn.lower() and "device" not in tn.lower():
                    continue

                rows = []
                for fidx in getattr(td, "FieldList", []):
                    frow = getattr(fidx, "row", None)
                    if frow is None:
                        continue
                    name = str(getattr(frow, "Name", ""))
                    if name == "value__":
                        continue
                    crow = consts.get(id(frow))
                    value = raw = None
                    if crow is not None:
                        value, raw = decode_constant(crow)
                    rows.append((name, value, raw))

                enum_hits.append((path, tn, rows))
                print(f"\n[{path}]")
                print("Enum:", tn)
                if not rows:
                    print("  (no literal fields decoded)")
                for name, value, raw in rows:
                    if value is not None:
                        print(f"  {name} = {value}")
                    else:
                        print(f"  {name} = ? raw={raw.hex(' ') if raw else ''}")
        except Exception:
            continue

    if not enum_hits:
        print("No matching enum types found.")

    print("\n=== get_DeviceType CALLS + NEARBY INTEGER COMPARISONS ===")

    for path in files:
        try:
            pe = dnfile.dnPE(str(path))
            if not getattr(pe, "net", None):
                continue
            parents = method_parent_map(pe)

            for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
                if not getattr(row, "Rva", 0):
                    continue
                try:
                    body = CilMethodBody(DnfileReader(pe, row))
                except Exception:
                    continue

                insns = list(body.instructions)
                code_start = body.offset + body.header_size

                hits = []
                for i, insn in enumerate(insns):
                    rendered = format_operand(pe, insn.operand, parents)
                    if "DeviceModel::get_DeviceType" in rendered:
                        hits.append(i)

                if not hits:
                    continue

                parent = parents.get(rid)
                print(f"\n[{path}]")
                print(f"{type_name(parent)}::{row.Name}  token=0x{0x06000000|rid:08X}")

                show = set()
                for hit in hits:
                    show.update(range(max(0, hit - 8), min(len(insns), hit + 12)))

                last = None
                for i in sorted(show):
                    if last is not None and i != last + 1:
                        print("  ...")
                    insn = insns[i]
                    iloff = insn.offset - code_start
                    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
                    op = getattr(insn, "mnemonic", str(insn.opcode))
                    operand = format_operand(pe, insn.operand, parents)
                    marker = " <==" if i in hits else ""
                    print(
                        f"  IL+0x{iloff:04X} {raw:<24} {op:<12} {operand}{marker}"
                    )
                    last = i
        except Exception:
            continue

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
