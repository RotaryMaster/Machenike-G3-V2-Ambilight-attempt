#!/usr/bin/env python3
"""
Recover the exact MixDrive firmware API contract from Machenik.HttpApi assemblies.

STATIC / READ-ONLY:
- no HTTP requests
- no device access
- no code execution from MixDrive

Prints:
- Machenik.HttpApi*.dll files present
- IFirmwareUpdateApiService methods/signatures
- parameter names and types from MethodDef metadata
- CustomAttribute rows attached to interface/service methods and types
- string literals and URLs/routes near firmware-update classes
- implementations/references to GetLatestVersionAsync

This is intended to close the route/parameter names needed for a later safe
catalog-only client.
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

TABLES = {t.value: t.name for t in MetadataTables}


class Reader(CilMethodBodyReaderBase):
    def __init__(self, pe: dnfile.dnPE, row):
        self.pe = pe
        self.offset = pe.get_offset_from_rva(row.Rva)

    def read(self, n: int) -> bytes:
        data = self.pe.get_data(self.pe.get_rva_from_offset(self.offset), n)
        self.offset += n
        return data

    def tell(self) -> int:
        return self.offset

    def seek(self, off: int) -> int:
        self.offset = off
        return off


def tname(row: Any) -> str:
    if row is None:
        return "?"
    ns = str(getattr(row, "TypeNamespace", "") or "")
    name = str(getattr(row, "TypeName", "") or "")
    return f"{ns}.{name}".strip(".") if name else str(row)


def parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []):
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out


def resolve(pe, tok):
    if isinstance(tok, StringToken):
        try:
            s = pe.net.user_strings.get(tok.rid)
            return s.value if s and not isinstance(s, bytes) else None
        except Exception:
            return None
    if not isinstance(tok, Token):
        return None
    tn = TABLES.get(tok.table)
    if not tn:
        return None
    tbl = getattr(pe.net.mdtables, tn, None)
    if not tbl:
        return None
    try:
        return tbl.rows[tok.rid - 1]
    except Exception:
        return None


def fmt_member(row):
    if row is None:
        return "?"
    c = row.__class__.__name__
    if c == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{tname(parent)}::{row.Name}"
    if c == "MethodDefRow":
        return f"MethodDef::{row.Name}"
    if c == "FieldRow":
        return f"Field::{row.Name}"
    if c in ("TypeDefRow", "TypeRefRow"):
        return tname(row)
    return f"{c}:{row}"


def fmt_operand(pe, op):
    if isinstance(op, Token):
        x = resolve(pe, op)
        if isinstance(x, str):
            return repr(x)
        return fmt_member(x)
    if isinstance(op, list):
        return "[" + ", ".join(map(str, op)) + "]"
    return "" if op is None else str(op)


def iter_strings(pe):
    # User strings are the important managed literals.
    us = getattr(pe.net, "user_strings", None)
    if us:
        # dnfile heap doesn't provide simple iteration consistently; scan Method IL
        pass


def attrs_for(pe, table_name: str, rid: int):
    ca = getattr(pe.net.mdtables, "CustomAttribute", None)
    if not ca:
        return []
    out = []
    for i, row in enumerate(ca.rows, 1):
        parent = getattr(row, "Parent", None)
        ptab = getattr(parent, "table", None)
        prid = getattr(parent, "row_index", None)
        # Some dnfile versions expose .row/.table differently.
        if prid is None:
            prid = getattr(parent, "row_index", None)
        if ptab is None and getattr(parent, "row", None) is not None:
            ptab = getattr(getattr(parent, "row", None), "__class__", type("",(),{})).__name__
        # Robust fallback: compare token if available.
        token = getattr(parent, "value", None)
        wanted_table = {"TypeDef":0x02000000,"MethodDef":0x06000000,"Param":0x08000000}.get(table_name)
        if token is not None and wanted_table is not None:
            if token != (wanted_table | rid):
                continue
        elif prid != rid:
            continue
        ctor = getattr(getattr(row, "Type", None), "row", None)
        blob = getattr(row, "Value", b"") or b""
        try:
            blob = bytes(blob)
        except Exception:
            blob = b""
        out.append((i, fmt_member(ctor), blob))
    return out


def printable_blob(blob: bytes) -> str:
    parts = []
    cur = []
    for b in blob:
        if 0x20 <= b <= 0x7E:
            cur.append(chr(b))
        else:
            if len(cur) >= 2:
                parts.append("".join(cur))
            cur = []
    if len(cur) >= 2:
        parts.append("".join(cur))
    return " | ".join(parts)


def dump_method(pe, rid, row, parent):
    print(f"\nMETHOD token=0x{0x06000000|rid:08X} {tname(parent)}::{row.Name}")
    # Params
    params = []
    for pidx in getattr(row, "ParamList", []):
        prow = getattr(pidx, "row", None)
        prid = getattr(pidx, "row_index", None)
        if prow is not None:
            params.append((prid, getattr(prow, "Sequence", None), str(getattr(prow, "Name", ""))))
    if params:
        print("  PARAMS:")
        for prid, seq, name in params:
            print(f"    seq={seq} rid={prid} name={name!r}")
            for _, ctor, blob in attrs_for(pe, "Param", prid or 0):
                print(f"      ATTR {ctor} blob={blob.hex(' ')} text={printable_blob(blob)!r}")

    for _, ctor, blob in attrs_for(pe, "MethodDef", rid):
        print(f"  ATTR {ctor} blob={blob.hex(' ')} text={printable_blob(blob)!r}")

    if not getattr(row, "Rva", 0):
        return
    try:
        body = CilMethodBody(Reader(pe, row))
    except Exception as e:
        print("  [IL decode failed]", e)
        return
    start = body.offset + body.header_size
    for ins in body.instructions:
        operand = fmt_operand(pe, ins.operand)
        low = operand.lower()
        if (
            "firmware" in low or "latest" in low or "http" in low or
            "url" in low or "api" in low or "download" in low or
            isinstance(ins.operand, StringToken)
        ):
            off = ins.offset - start
            print(f"  IL+0x{off:04X} {getattr(ins,'mnemonic',str(ins.opcode)):<13} {operand}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    args = ap.parse_args()
    root = Path(args.root)

    files = sorted(set(root.rglob("Machenik.HttpApi*.dll")))
    print("="*112)
    print("MIXDRIVE FIRMWARE API CONTRACT — STATIC METADATA AUDIT")
    print("="*112)
    print("Root:", root)
    print("Assemblies:")
    for f in files:
        print(" ", f.relative_to(root))

    if not files:
        print("\nNo Machenik.HttpApi*.dll found.")
        return

    needles = (
        "IFirmwareUpdateApiService",
        "FirmwareUpdateApiService",
        "FirmwareUpdateDto",
        "FirmwareInfoDto",
        "GetLatestVersionAsync",
    )

    for path in files:
        print("\n" + "="*112)
        print(path.relative_to(root))
        print("="*112)
        try:
            pe = dnfile.dnPE(str(path))
        except Exception as e:
            print("PE error:", e)
            continue
        if not getattr(pe, "net", None):
            print("Not managed")
            continue

        pmap = parent_map(pe)

        # Type-level target attributes.
        for trid, td in enumerate(pe.net.mdtables.TypeDef.rows, 1):
            full = tname(td)
            if any(n.lower() in full.lower() for n in needles) or "updates" in full.lower():
                print(f"\nTYPE token=0x{0x02000000|trid:08X} {full}")
                for _, ctor, blob in attrs_for(pe, "TypeDef", trid):
                    print(f"  ATTR {ctor} blob={blob.hex(' ')} text={printable_blob(blob)!r}")

        # Methods.
        for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
            parent = pmap.get(rid)
            full = f"{tname(parent)}::{row.Name}"
            if any(n.lower() in full.lower() for n in needles) or (
                "updates" in full.lower() and any(x in str(row.Name).lower() for x in ("latest","firmware","version"))
            ):
                dump_method(pe, rid, row, parent)

        # Raw ASCII/UTF16 strings in the whole assembly, intentionally narrow.
        data = path.read_bytes()
        print("\n=== RAW ROUTE / URL STRINGS ===")
        seen = set()
        for enc, step in (("ascii",1),("utf-16le",2)):
            if enc == "ascii":
                chunks = []
                cur = bytearray()
                st = 0
                for i,b in enumerate(data):
                    if 0x20 <= b <= 0x7e:
                        if not cur: st=i
                        cur.append(b)
                    else:
                        if len(cur)>=4: chunks.append((st,bytes(cur).decode("ascii","replace")))
                        cur.clear()
                if len(cur)>=4: chunks.append((st,bytes(cur).decode("ascii","replace")))
            else:
                chunks=[]
                i=0
                while i+1<len(data):
                    if 0x20<=data[i]<=0x7e and data[i+1]==0:
                        st=i; chars=[]
                        while i+1<len(data) and 0x20<=data[i]<=0x7e and data[i+1]==0:
                            chars.append(chr(data[i])); i+=2
                        if len(chars)>=4: chunks.append((st,"".join(chars)))
                    else:
                        i+=1
            for off,s in chunks:
                lo=s.lower()
                if (
                    "firmware" in lo or "latest" in lo or
                    "/api" in lo or "http://" in lo or "https://" in lo or
                    "download" in lo
                ):
                    key=(enc,s)
                    if key not in seen:
                        seen.add(key)
                        print(f"0x{off:08X} {enc:8s} {s}")

    print("\n" + "="*112)
    print("TARGET")
    print("="*112)
    print("We need the exact route attribute + parameter names for IFirmwareUpdateApiService.GetLatestVersionAsync.")
    print("No HTTP request or device I/O was performed.")


if __name__ == "__main__":
    main()
