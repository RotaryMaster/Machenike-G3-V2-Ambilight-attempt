#!/usr/bin/env python3
"""
Static scanner for how MixDrive selects/assigns the HID interface of a gamepad.

Scans all managed PE files under the extracted MixDrive directory and reports
methods referencing DeviceModel.HidDevice, HidLibrary enumeration/path/attributes,
VID/PID/capability metadata, DeviceManagerCache, and gamepad connection methods.

No assembly is executed. No HID/USB/SCSI access is performed.
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

KEYWORDS = (
    "DeviceModel::get_HidDevice",
    "DeviceModel::set_HidDevice",
    "HidDevice::get_DevicePath",
    "HidDevice::get_Attributes",
    "HidDevice::get_Capabilities",
    "HidDevices::Enumerate",
    "HidDeviceLoader",
    "VendorId",
    "ProductId",
    "DevicePath",
    "DeviceManagerCache",
    "ConnectGamepad",
    "GamepadDeviceManager::.ctor",
)

STRING_KEYS = (
    "G3V2", "413D", "2131", "VID_", "PID_", "MI_",
    "gamepad", "wireless", "receiver", "dongle", "bluetooth", "ble",
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
            return getattr(s, "value", None)
        except Exception:
            return InvalidToken(token.value)

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


def tname(row):
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


def render_row(row, methods, fields, rid=None):
    if row is None:
        return "?"
    cls = row.__class__.__name__
    if cls == "MemberRefRow":
        parent = getattr(getattr(row, "Class", None), "row", None)
        return f"{tname(parent)}::{getattr(row, 'Name', '?')}"
    if cls == "MethodDefRow":
        return f"{tname(methods.get(rid))}::{getattr(row, 'Name', '?')}"
    if cls == "FieldRow":
        return f"{tname(fields.get(rid))}::{getattr(row, 'Name', '?')}"
    if cls in ("TypeDefRow", "TypeRefRow"):
        return tname(row)
    if cls == "MethodSpecRow":
        base = getattr(getattr(row, "Method", None), "row", None)
        return "MethodSpec::" + render_row(base, methods, fields)
    return f"{cls}:{getattr(row, 'Name', '')}"


def operand_text(pe, operand, methods, fields):
    if isinstance(operand, StringToken):
        v = resolve(pe, operand)
        return repr(v) if isinstance(v, str) else str(v)
    if isinstance(operand, Token):
        row = resolve(pe, operand)
        if isinstance(row, str):
            return repr(row)
        return render_row(row, methods, fields, operand.rid)
    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"
    return "" if operand is None else str(operand)


def insn_text(pe, insn, code_start, methods, fields):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = operand_text(pe, insn.operand, methods, fields)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def interesting_method_name(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in (
        "connectgamepad", "gamepad", "devicehotplug", "hotplug",
        "enumerate", "device", "hid",
    ))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Extracted MixDrive directory")
    ap.add_argument("--context", type=int, default=45)
    args = ap.parse_args()

    root = Path(args.root)
    files = sorted({
        *root.rglob("*.dll"),
        *root.rglob("*.exe"),
    })

    print("=" * 120)
    print("GAMEPAD HID SELECTION — STATIC CIL SCAN")
    print("=" * 120)
    print("Root:", root)
    print("Managed PE candidates:", len(files))

    total_methods = 0

    for path in files:
        try:
            pe = dnfile.dnPE(str(path))
        except Exception:
            continue
        if not getattr(pe, "net", None) or not getattr(pe.net, "mdtables", None):
            continue

        methods, fields = parent_maps(pe)
        matches = []

        for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
            if not getattr(row, "Rva", 0):
                continue
            try:
                body = CilMethodBody(Reader(pe, row))
            except Exception:
                continue

            insns = list(body.instructions)
            code_start = body.offset + body.header_size
            hit_idxs = []
            hit_reasons = []

            method_name = str(getattr(row, "Name", "") or "")
            parent_name = tname(methods.get(rid))

            for i, insn in enumerate(insns):
                text = operand_text(pe, insn.operand, methods, fields)
                lower = text.lower()

                for key in KEYWORDS:
                    if key.lower() in lower:
                        hit_idxs.append(i)
                        hit_reasons.append(key)
                        break
                else:
                    if isinstance(insn.operand, StringToken):
                        val = resolve(pe, insn.operand)
                        if isinstance(val, str):
                            for key in STRING_KEYS:
                                if key.lower() in val.lower():
                                    hit_idxs.append(i)
                                    hit_reasons.append(f"string:{key}")
                                    break

            # Promote clearly relevant named methods even when references are indirect.
            if not hit_idxs and (
                "gamepad" in parent_name.lower() and
                interesting_method_name(method_name)
            ):
                continue

            if hit_idxs:
                matches.append((rid, row, parent_name, body, insns, code_start, hit_idxs, hit_reasons))

        if not matches:
            continue

        print("\n" + "#" * 120)
        print(path)
        print("#" * 120)

        for rid, row, parent_name, body, insns, code_start, hit_idxs, hit_reasons in matches:
            total_methods += 1
            print("\n" + "-" * 120)
            print(f"{parent_name}::{row.Name} token=0x{0x06000000 | rid:08X}")
            print("Reasons:", ", ".join(sorted(set(hit_reasons))))

            show = set()
            for i in hit_idxs:
                show.update(range(max(0, i - args.context), min(len(insns), i + args.context + 1)))

            last = None
            for i in sorted(show):
                if last is not None and i != last + 1:
                    print("  ...")
                line = insn_text(pe, insns[i], code_start, methods, fields)
                if i in hit_idxs:
                    line += "    <=="
                print(line)
                last = i

    print("\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)
    print("Matched methods:", total_methods)
    print("No assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
