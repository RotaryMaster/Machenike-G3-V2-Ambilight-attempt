#!/usr/bin/env python3
"""
Read-only scanner for MixDrive gamepad transport/connection metadata.

Focuses on:
- DeviceModel / gamepad-related fields and methods
- transport-ish names: USB, wireless, receiver, dongle, Bluetooth, connection,
  mode, interface, VID/PID, device path
- firmware-update methods and nearby references

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

KEYWORDS = (
    "usb", "wireless", "receiver", "dongle", "bluetooth", "ble",
    "connection", "connecttype", "transport", "interface", "devicepath",
    "vid", "pid", "vendor", "product", "mode", "link", "2.4", "2g4",
)

TYPE_HINTS = (
    "devicemodel", "gamepad", "deviceinfo", "deviceconfig",
)

FIRMWARE_METHOD_HINTS = (
    "UpdateGamepadFirmwareAsync",
    "CheckFirmwareUpdateAsync",
    "UpdateFirmwareAsync",
    "EnterUpdateMode",
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
    ns = str(getattr(row, "TypeNamespace", "") or "")
    name = str(getattr(row, "TypeName", "") or "")
    if name:
        return f"{ns}.{name}".strip(".")
    return str(row)


def parent_maps(pe):
    method_parent = {}
    field_parent = {}
    for td in pe.net.mdtables.TypeDef.rows:
        for idx in getattr(td, "MethodList", []) or []:
            rid = getattr(idx, "row_index", None)
            if rid:
                method_parent[rid] = td
        for idx in getattr(td, "FieldList", []) or []:
            rid = getattr(idx, "row_index", None)
            if rid:
                field_parent[rid] = td
    return method_parent, field_parent


def interesting(text: str) -> bool:
    s = text.lower()
    return any(k in s for k in KEYWORDS)


def relevant_type(text: str) -> bool:
    s = text.lower()
    return any(k in s for k in TYPE_HINTS)


def render_operand(pe, operand: Any, method_parent, field_parent) -> str:
    if isinstance(operand, StringToken):
        resolved = resolve_token(pe, operand)
        return repr(resolved) if isinstance(resolved, str) else str(resolved)

    if not isinstance(operand, Token):
        return str(operand) if operand is not None else ""

    resolved = resolve_token(pe, operand)
    if isinstance(resolved, str):
        return repr(resolved)

    cls = resolved.__class__.__name__

    if cls == "MemberRefRow":
        parent = getattr(getattr(resolved, "Class", None), "row", None)
        return f"{type_name(parent)}::{getattr(resolved, 'Name', '?')}"

    if cls == "MethodDefRow":
        return (
            f"{type_name(method_parent.get(operand.rid))}::"
            f"{getattr(resolved, 'Name', '?')}"
        )

    if cls == "FieldRow":
        return (
            f"{type_name(field_parent.get(operand.rid))}::"
            f"{getattr(resolved, 'Name', '?')}"
        )

    if cls in ("TypeDefRow", "TypeRefRow"):
        return type_name(resolved)

    if cls == "MethodSpecRow":
        base = getattr(getattr(resolved, "Method", None), "row", None)
        if base is not None:
            return f"MethodSpec::{getattr(base, 'Name', '?')}"
        return "MethodSpec"

    return f"{cls}:{getattr(resolved, 'Name', '')}"


def list_model_members(path: Path, pe):
    method_parent, field_parent = parent_maps(pe)
    output = []

    for rid, td in enumerate(pe.net.mdtables.TypeDef.rows, 1):
        tn = type_name(td)
        if not relevant_type(tn):
            continue

        members = []
        for fidx in getattr(td, "FieldList", []) or []:
            frow = getattr(fidx, "row", None)
            if frow is None:
                continue
            name = str(getattr(frow, "Name", "") or "")
            if interesting(name):
                members.append(("FIELD", name))

        for midx in getattr(td, "MethodList", []) or []:
            mrow = getattr(midx, "row", None)
            if mrow is None:
                continue
            name = str(getattr(mrow, "Name", "") or "")
            if interesting(name):
                members.append(("METHOD", name))

        if members:
            output.append((tn, members))

    if not output:
        return

    print(f"\n[{path}]")
    for tn, members in output:
        print(f"Type: {tn}")
        for kind, name in members:
            print(f"  {kind:6} {name}")


def dump_transport_xrefs(path: Path, pe):
    method_parent, field_parent = parent_maps(pe)

    hits_for_file = []

    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue

        parent = method_parent.get(rid)
        ptn = type_name(parent)
        mname = str(getattr(row, "Name", "") or "")

        try:
            body = CilMethodBody(DnfileReader(pe, row))
        except Exception:
            continue

        insns = list(body.instructions)
        code_start = body.offset + body.header_size
        hit_indices = []

        for i, insn in enumerate(insns):
            rendered = render_operand(
                pe, insn.operand, method_parent, field_parent
            )
            if interesting(rendered):
                hit_indices.append(i)

        strong_method = (
            any(h.lower() in mname.lower() for h in FIRMWARE_METHOD_HINTS)
            or "firmware" in mname.lower()
            or "update" in mname.lower() and "gamepad" in ptn.lower()
        )

        if hit_indices or strong_method:
            hits_for_file.append(
                (rid, row, ptn, mname, body, insns, code_start,
                 hit_indices, strong_method)
            )

    if not hits_for_file:
        return

    print(f"\n[{path}]")

    for rid, row, ptn, mname, body, insns, code_start, hit_indices, strong_method in hits_for_file:
        # Avoid flooding with unrelated update methods unless they actually
        # contain a transport-ish reference.
        if strong_method and not hit_indices:
            continue

        print(f"\n{ptn}::{mname} token=0x{0x06000000|rid:08X}")

        show = set()
        for hit in hit_indices:
            show.update(
                range(max(0, hit - 6), min(len(insns), hit + 9))
            )

        last = None
        for i in sorted(show):
            if last is not None and i != last + 1:
                print("  ...")
            insn = insns[i]
            iloff = insn.offset - code_start
            raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
            op = getattr(insn, "mnemonic", str(insn.opcode))
            rendered = render_operand(
                pe, insn.operand, method_parent, field_parent
            )
            marker = " <==" if i in hit_indices else ""
            print(
                f"  IL+0x{iloff:04X} {raw:<24} "
                f"{op:<12} {rendered}{marker}"
            )
            last = i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Extracted MixDrive directory")
    args = ap.parse_args()

    root = Path(args.root)
    files = sorted(
        p for p in root.rglob("*")
        if p.suffix.lower() in (".dll", ".exe")
    )

    print("=" * 112)
    print("MIXDRIVE GAMEPAD TRANSPORT — STATIC SCAN")
    print("=" * 112)
    print("Root:", root)

    print("\n=== DEVICE/GAMEPAD MODEL MEMBERS WITH TRANSPORT-LIKE NAMES ===")
    for path in files:
        try:
            pe = dnfile.dnPE(str(path))
            if getattr(pe, "net", None):
                list_model_members(path, pe)
        except Exception:
            continue

    print("\n=== TRANSPORT-LIKE REFERENCES IN MANAGED METHODS ===")
    for path in files:
        try:
            pe = dnfile.dnPE(str(path))
            if getattr(pe, "net", None):
                dump_transport_xrefs(path, pe)
        except Exception:
            continue

    print("\nNo assembly was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
