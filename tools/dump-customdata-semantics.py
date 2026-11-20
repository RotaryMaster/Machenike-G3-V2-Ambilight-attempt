#!/usr/bin/env python3
"""
Static CIL audit of CustomData semantics and writers.

Scans Machenik.exe + Machenik.Shared.dll for:
- all GetDeviceCustomDataAsync call sites;
- all WriteDeviceCustomDataAsync call sites;
- G3V2-specific branches near those calls;
- array length checks / indexed byte accesses near read sites;
- builder/helper calls near write sites.

No assembly is executed. No device I/O occurs.
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


class DnfileReader(CilMethodBodyReaderBase):
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


def resolve_token(pe, token):
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


def type_name(row):
    if row is None:
        return "?"
    ns = str(getattr(row, "TypeNamespace", "") or "")
    name = str(getattr(row, "TypeName", "") or "")
    return f"{ns}.{name}".strip(".") if name else str(row)


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
        return f"MethodSpec::{getattr(base, 'Name', '?')}"

    return f"{cls}:{getattr(row, 'Name', '')}"


def render_operand(pe, operand, method_parents, field_parents):
    if isinstance(operand, StringToken):
        v = resolve_token(pe, operand)
        return repr(v) if isinstance(v, str) else str(v)

    if isinstance(operand, Token):
        row = resolve_token(pe, operand)
        if isinstance(row, str):
            return repr(row)
        return render_row(row, method_parents, field_parents, rid=operand.rid)

    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"

    return "" if operand is None else str(operand)


def format_insn(pe, insn, code_start, method_parents, field_parents):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = render_operand(pe, insn.operand, method_parents, field_parents)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def method_inventory(pe):
    method_parents, field_parents = parent_maps(pe)
    out = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(DnfileReader(pe, row))
        except Exception:
            continue
        out.append((
            rid, row, method_parents.get(rid),
            list(body.instructions), body.offset + body.header_size,
        ))
    return method_parents, field_parents, out


def is_call(insn):
    return getattr(insn, "mnemonic", str(insn.opcode)) in ("call", "callvirt", "newobj")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    args = ap.parse_args()

    root = Path(args.root)
    paths = [root / "Machenik.exe", root / "Machenik.Shared.dll"]

    print("=" * 116)
    print("CUSTOMDATA SEMANTICS / WRITERS — STATIC CIL AUDIT")
    print("=" * 116)

    totals = {"read": 0, "write": 0}

    for path in paths:
        if not path.is_file():
            raise SystemExit(f"missing {path}")

        pe = dnfile.dnPE(str(path))
        method_parents, field_parents, methods = method_inventory(pe)

        print(f"\n### {path.name}")

        for rid, row, parent, insns, code_start in methods:
            hits = []
            for i, insn in enumerate(insns):
                if not is_call(insn):
                    continue
                rendered = render_operand(pe, insn.operand, method_parents, field_parents)
                kind = None
                if "GamepadDeviceManager::GetDeviceCustomDataAsync" in rendered:
                    kind = "READ"
                    totals["read"] += 1
                elif "GamepadDeviceManager::WriteDeviceCustomDataAsync" in rendered:
                    kind = "WRITE"
                    totals["write"] += 1
                if kind:
                    hits.append((i, kind, rendered))

            if not hits:
                continue

            pn = type_name(parent)
            mn = str(getattr(row, "Name", "") or "")
            print("\n" + "-" * 116)
            print(
                f"[{path.name}] {pn}::{mn} "
                f"token=0x{0x06000000 | rid:08X}"
            )

            show = set()
            for i, kind, rendered in hits:
                # Wide enough to reveal null/length checks, byte indices,
                # DeviceMode strings, builder methods, and mutation before writes.
                show.update(range(max(0, i - 70), min(len(insns), i + 105)))

            # Also include any G3V2 string and nearby branch within the same method.
            for i, insn in enumerate(insns):
                operand = render_operand(pe, insn.operand, method_parents, field_parents)
                if operand == "'G3V2'":
                    show.update(range(max(0, i - 25), min(len(insns), i + 45)))

            last = None
            for i in sorted(show):
                if last is not None and i != last + 1:
                    print("  ...")
                line = format_insn(
                    pe, insns[i], code_start, method_parents, field_parents
                )
                if any(i == h[0] for h in hits):
                    kind = next(h[1] for h in hits if h[0] == i)
                    line += f"    <== CUSTOMDATA {kind}"
                print(line)
                last = i

    print("\n" + "=" * 116)
    print("SUMMARY")
    print("=" * 116)
    print("GetDeviceCustomDataAsync call sites :", totals["read"])
    print("WriteDeviceCustomDataAsync call sites:", totals["write"])
    print()
    print("No assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
