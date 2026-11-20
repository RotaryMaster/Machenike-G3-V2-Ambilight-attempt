#!/usr/bin/env python3
"""
Static audit of GamepadDeviceManager read paths used by the normal hardware loader.

Purpose:
  Bridge the managed loader calls to the native GamepadInterop/KeyLinkerlib
  functions so the Linux read-only warm-up can reproduce MixDrive's actual
  initialization sequence.

Targets:
  - GetDeviceBeforeKeyChangeListAsync
  - GetDeviceSupportTurboAsync
  - GetDeviceCurrentTurboAsync
  - GetDeviceRockerAsync
  - GetDeviceTriggerAsync
  - GetDeviceMotorAsync
  - GetDeviceLamplightAsync
  - GetDeviceCustomDataAsync

Also prints focused HandleGenericDeviceData receive/dispatcher windows and
relevant P/Invoke imports.

No managed/native code is executed.
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

TARGETS = (
    "GetDeviceBeforeKeyChangeListAsync",
    "GetDeviceSupportTurboAsync",
    "GetDeviceCurrentTurboAsync",
    "GetDeviceRockerAsync",
    "GetDeviceTriggerAsync",
    "GetDeviceMotorAsync",
    "GetDeviceLamplightAsync",
    "GetDeviceCustomDataAsync",
)

KEYWORDS = (
    "keychange",
    "turbo",
    "rocker",
    "trigger",
    "motor",
    "lamp",
    "customdata",
)

MARKERS = (
    "GamepadInterop::",
    "CreateTaskCompletionSource",
    "GetResultAsync",
    "readDevice",
    "receiveDevice",
    "GetPacketCount",
    "CheckPacketIndex",
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
    if not table_name:
        return InvalidToken(token.value)
    table = getattr(pe.net.mdtables, table_name, None)
    if table is None:
        return InvalidToken(token.value)
    try:
        return table.rows[token.rid - 1]
    except Exception:
        return InvalidToken(token.value)


def type_name(row):
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


def operand_text(pe, operand, method_parents, field_parents):
    if isinstance(operand, StringToken):
        value = resolve(pe, operand)
        return repr(value) if isinstance(value, str) else str(value)

    if isinstance(operand, Token):
        row = resolve(pe, operand)
        if isinstance(row, str):
            return repr(row)
        return render_row(row, method_parents, field_parents, operand.rid)

    if isinstance(operand, list):
        return "[" + ", ".join(str(x) for x in operand) + "]"

    return "" if operand is None else str(operand)


def fmt(pe, insn, code_start, mp, fp):
    iloff = insn.offset - code_start
    raw = " ".join(f"{b:02x}" for b in insn.get_bytes())
    op = getattr(insn, "mnemonic", str(insn.opcode))
    operand = operand_text(pe, insn.operand, mp, fp)
    return f"IL+0x{iloff:04X}  {raw:<28} {op:<14} {operand}".rstrip()


def inventory(pe):
    mp, fp = parent_maps(pe)
    methods = []
    for rid, row in enumerate(pe.net.mdtables.MethodDef.rows, 1):
        if not getattr(row, "Rva", 0):
            continue
        try:
            body = CilMethodBody(Reader(pe, row))
        except Exception:
            continue
        methods.append(
            (
                rid,
                row,
                mp.get(rid),
                list(body.instructions),
                body.offset + body.header_size,
            )
        )
    return mp, fp, methods


def is_call(insn):
    return getattr(insn, "mnemonic", str(insn.opcode)) in ("call", "callvirt", "newobj")


def target_for_parent(parent_name):
    for target in TARGETS:
        if f"<{target}>" in parent_name:
            return target
    return None


def implmap_entries(pe, method_parents):
    table = getattr(pe.net.mdtables, "ImplMap", None)
    if table is None:
        return []

    rows = []
    for im in table.rows:
        forwarded = getattr(getattr(im, "MemberForwarded", None), "row", None)
        if forwarded is None:
            continue
        try:
            rid = pe.net.mdtables.MethodDef.rows.index(forwarded) + 1
        except ValueError:
            continue

        parent = method_parents.get(rid)
        parent_name = type_name(parent)
        managed_name = str(getattr(forwarded, "Name", "") or "")
        scope = getattr(getattr(im, "ImportScope", None), "row", None)
        dll = str(getattr(scope, "Name", "?") or "?")
        native_name = str(getattr(im, "ImportName", "?") or "?")
        rows.append((rid, parent_name, managed_name, dll, native_name))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MixDrive extracted directory")
    args = ap.parse_args()

    dll = Path(args.root) / "Machenik.Infrastructure.dll"
    if not dll.is_file():
        raise SystemExit(f"Missing: {dll}")

    pe = dnfile.dnPE(str(dll))
    mp, fp, methods = inventory(pe)

    print("=" * 132)
    print("GAMEPAD MANAGER -> NATIVE READ PATHS — STATIC CIL AUDIT")
    print("=" * 132)
    print(f"Assembly: {dll}")

    found_targets = {}
    summaries = []

    for rid, row, parent, insns, code_start in methods:
        parent_name = type_name(parent)
        method_name = str(getattr(row, "Name", "") or "")
        if method_name != "MoveNext":
            continue

        target = target_for_parent(parent_name)
        if target is None:
            continue

        found_targets[target] = True
        print("\n" + "=" * 132)
        print(f"{target} | {parent_name}::MoveNext | token=0x{0x06000000 | rid:08X}")
        print("=" * 132)

        calls = []
        for insn in insns:
            text = operand_text(pe, insn.operand, mp, fp)
            line = fmt(pe, insn, code_start, mp, fp)
            if any(marker in text for marker in MARKERS):
                line += "    <=="
            print(line)

            if is_call(insn) and (
                "GamepadInterop::" in text
                or "readDevice" in text
                or "receiveDevice" in text
            ):
                calls.append((insn.offset - code_start, text))

        summaries.append((target, calls))

    print("\n" + "=" * 132)
    print("MANAGED READ -> NATIVE CALL SUMMARY")
    print("=" * 132)
    for target in TARGETS:
        pair = next((x for x in summaries if x[0] == target), None)
        print(f"\n{target}:")
        if pair is None:
            print("  state machine not found")
            continue
        calls = pair[1]
        if not calls:
            print("  no native call resolved by name")
            continue
        for n, (off, text) in enumerate(calls, 1):
            print(f"  {n:02d}. IL+0x{off:04X}  {text}")

    missing = [x for x in TARGETS if x not in found_targets]
    if missing:
        print("\nMissing target state machines:", ", ".join(missing))

    print("\n" + "=" * 132)
    print("HANDLE GENERIC DEVICE DATA — FOCUSED RECEIVE WINDOWS")
    print("=" * 132)

    handler_found = False
    for rid, row, parent, insns, code_start in methods:
        parent_name = type_name(parent)
        method_name = str(getattr(row, "Name", "") or "")
        if "GamepadDeviceManager" not in parent_name or method_name != "HandleGenericDeviceData":
            continue

        handler_found = True
        hits = []
        for i, insn in enumerate(insns):
            if not is_call(insn):
                continue
            text = operand_text(pe, insn.operand, mp, fp)
            low = text.lower()
            if (
                "gamepadinterop::" in low
                and any(key in low for key in KEYWORDS)
            ) or (
                "receivedevice" in low
                and any(key in low for key in KEYWORDS)
            ):
                hits.append((i, text))

        if not hits:
            print("(HandleGenericDeviceData found, but no relevant receive call resolved by name)")
            continue

        show = set()
        for i, _ in hits:
            show.update(range(max(0, i - 22), min(len(insns), i + 28)))

        last = None
        for i in sorted(show):
            if last is not None and i != last + 1:
                print("  ...")
            line = fmt(pe, insns[i], code_start, mp, fp)
            if any(i == h[0] for h in hits):
                text = next(h[1] for h in hits if h[0] == i)
                line += f"    <== RECEIVE {text}"
            print(line)
            last = i

    if not handler_found:
        print("HandleGenericDeviceData not found.")

    print("\n" + "=" * 132)
    print("RELEVANT P/INVOKE IMPORTS")
    print("=" * 132)
    pinvokes = implmap_entries(pe, mp)
    selected = []
    for rid, parent_name, managed_name, dll_name, native_name in pinvokes:
        low = f"{parent_name} {managed_name} {native_name}".lower()
        if "gamepad" not in low and "keylinker" not in dll_name.lower():
            continue
        if any(key in low for key in KEYWORDS):
            selected.append((rid, parent_name, managed_name, dll_name, native_name))

    if not selected:
        print("(no relevant P/Invoke imports resolved)")
    else:
        for rid, parent_name, managed_name, dll_name, native_name in selected:
            print(
                f"0x{0x06000000 | rid:08X}  "
                f"{parent_name}::{managed_name} -> {dll_name}!{native_name}"
            )

    print("\nNo managed/native code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static metadata/CIL read only.")


if __name__ == "__main__":
    main()
