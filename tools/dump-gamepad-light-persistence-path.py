#!/usr/bin/env python3
"""
Focused static audit of the managed G3V2 lighting save/apply paths.

Goal:
  Determine whether WriteDeviceLamplightAsync (native command 0x37) is paired
  with WriteDeviceCustomDataAsync (0x3B) or any additional save/commit-like
  operation, and distinguish UI-only color changes from hardware writes.

Targets:
  - ChangeLightAsync
  - SaveLightAsync
  - ResetLightAsync
  - ApplyCurrentConfigAsync
  - ChangeGamepadConfigAsync
  - BuildCustomDataFromViewModel
  - WriteDeviceLamplightAsync
  - WriteDeviceCustomDataAsync

No managed code is executed.
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

TARGET_NAMES = {
    "ChangeLightAsync",
    "SaveLightAsync",
    "ResetLightAsync",
    "ApplyCurrentConfigAsync",
    "ChangeGamepadConfigAsync",
    "BuildCustomDataFromViewModel",
    "WriteDeviceLamplightAsync",
    "WriteDeviceCustomDataAsync",
}

INTEREST = (
    "Lamp",
    "Light",
    "CustomData",
    "WriteDevice",
    "Save",
    "Apply",
    "Config",
    "Mode",
    "Brightness",
    "Breath",
    "Color",
)


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
        except Exception:
            return InvalidToken(token.value)
        return getattr(s, "value", None) if s is not None else InvalidToken(token.value)

    table_name = TABLES.get(token.table, "")
    table = getattr(pe.net.mdtables, table_name, None) if table_name else None
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


def parent_map(pe):
    out = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []) or []:
            rid = getattr(idx, "row_index", None)
            if rid:
                out[rid] = td
    return out


def render(pe, operand, parents):
    if isinstance(operand, StringToken):
        x = resolve(pe, operand)
        return repr(x) if isinstance(x, str) else str(x)

    if not isinstance(operand, Token):
        if isinstance(operand, list):
            return "[" + ", ".join(str(x) for x in operand) + "]"
        return "" if operand is None else str(operand)

    row = resolve(pe, operand)
    if isinstance(row, str):
        return repr(row)

    cls = row.__class__.__name__
    if cls == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{type_name(parent)}::{getattr(row, 'Name', '?')}"
    if cls == "MethodDefRow":
        return f"{type_name(parents.get(operand.rid))}::{getattr(row, 'Name', '?')}"
    if cls == "FieldRow":
        return f"Field::{getattr(row, 'Name', '?')}"
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
    return str(getattr(row, "Name", cls))


def interesting(text: str) -> bool:
    return any(k.lower() in text.lower() for k in INTEREST)


def dump_method(pe, rid, row, parent, parents):
    body = CilMethodBody(Reader(pe, row))
    insns = list(body.instructions)
    code_start = body.offset + body.header_size

    print("\n" + "=" * 124)
    print(f"{type_name(parent)}::{row.Name} token=0x{0x06000000 | rid:08X}")
    print("=" * 124)

    # For small target methods dump everything. For very large state machines,
    # dump windows around relevant calls/field accesses.
    if body.code_size <= 0x500:
        selected = set(range(len(insns)))
    else:
        selected = set()
        for i, insn in enumerate(insns):
            text = render(pe, insn.operand, parents)
            if interesting(text):
                selected.update(range(max(0, i - 16), min(len(insns), i + 20)))

    last = None
    for i in sorted(selected):
        if last is not None and i != last + 1:
            print("  ...")
        insn = insns[i]
        raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
        op = getattr(insn, "mnemonic", str(insn.opcode))
        text = render(pe, insn.operand, parents)
        iloff = insn.offset - code_start

        suffix = ""
        if "WriteDeviceLamplightAsync" in text:
            suffix = "    <== LAMPLIGHT WRITE / native 0x37"
        elif "WriteDeviceCustomDataAsync" in text:
            suffix = "    <== CUSTOMDATA WRITE / native 0x3B"
        elif "writeDeviceLamplightData" in text:
            suffix = "    <== native LampLight writer"
        elif "writeDeviceCustomData" in text:
            suffix = "    <== native CustomData writer"

        print(f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {text}{suffix}")
        last = i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted root")
    args = ap.parse_args()

    root = Path(args.root)
    assemblies = (
        root / "Machenik.Infrastructure.dll",
        root / "Machenik.exe",
    )

    for asm in assemblies:
        if not asm.is_file():
            raise SystemExit(f"Missing: {asm}")

        pe = dnfile.dnPE(str(asm))
        parents = parent_map(pe)

        print("\n" + "#" * 124)
        print(f"ASSEMBLY: {asm}")
        print("#" * 124)

        found = 0
        for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
            name = str(getattr(row, "Name", "") or "")
            parent = parents.get(rid)
            owner = type_name(parent)

            # Async methods usually move the real body into nested MoveNext.
            # Include both named wrapper methods and state-machine MoveNext bodies
            # whose owner names contain one of the requested method names.
            target = (
                name in TARGET_NAMES
                or (
                    name == "MoveNext"
                    and any(f"<{t}>" in owner for t in TARGET_NAMES)
                )
            )
            if not target or not getattr(row, "Rva", 0):
                continue

            found += 1
            try:
                dump_method(pe, rid, row, parent, parents)
            except Exception as e:
                print(f"\nCould not dump {owner}::{name}: {e}")

        if not found:
            print("(no target methods found)")

    print("\nNo managed code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL inspection only.")


if __name__ == "__main__":
    main()
