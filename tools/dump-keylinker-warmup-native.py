#!/usr/bin/env python3
"""
Focused static disassembly of KeyLinker warm-up read builders.

Targets only the native exports used by the official gamepad initializer:
- readDeviceBeforeKeyChangeList
- readDeviceSupportTurboList
- readDeviceTurboData
- readRockerData
- readTriggerData
- readDeviceMotorData
- readDeviceLamplightData
- receiveDeviceLamplightData
- writeDeviceLamplightData
- readDeviceCustomData
- optional readDeviceAfterKeyChangeList

For each export the script:
- resolves RVA/VA from the PE export table;
- disassembles only a bounded function range with objdump;
- prints call sites and nearby setup instructions;
- highlights references to the already-known common KeyLinker helpers:
  encoder 0x180001080
  decoder 0x1800011C0
  state   0x1800014C0
  concat  0x180001400
  header  0x180001C90
  rand IAT 0x18000B150

No PE code is executed.
No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import pefile

TARGETS = [
    "readDeviceBeforeKeyChangeList",
    "readDeviceSupportTurboList",
    "readDeviceTurboData",
    "readRockerData",
    "readTriggerData",
    "readDeviceMotorData",
    "readDeviceLamplightData",
    "receiveDeviceLamplightData",
    "writeDeviceLamplightData",
    "readDeviceCustomData",
    "readDeviceAfterKeyChangeList",
]

KNOWN = {
    0x180001080: "KeyLinkerEncode",
    0x1800011C0: "KeyLinkerDecode",
    0x1800014C0: "KeyLinkerState",
    0x180001400: "Concat",
    0x180001C90: "HeaderBuilder",
    0x18000B150: "rand_IAT",
}

CALL_RE = re.compile(r"\bcall\b", re.I)
ADDR_RE = re.compile(r"^\s*([0-9a-fA-F]+):")
HEXREF_RE = re.compile(r"0x([0-9a-fA-F]{6,16})")


def export_map(pe: pefile.PE):
    out = {}
    exp = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if not exp:
        return out
    for sym in exp.symbols:
        if not sym.name:
            continue
        try:
            name = sym.name.decode("ascii", errors="replace")
        except Exception:
            continue
        out[name] = int(sym.address)
    return out


def run_objdump(objdump: str, dll: Path, start: int, stop: int) -> list[str]:
    cmd = [
        objdump,
        "-d",
        "-Mintel",
        f"--start-address=0x{start:X}",
        f"--stop-address=0x{stop:X}",
        str(dll),
    ]
    cp = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return cp.stdout.splitlines()


def instruction_lines(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        if ADDR_RE.match(line):
            out.append(line.rstrip())
    return out


def referenced_known(line: str):
    refs = []
    lower = line.lower()
    for addr, name in KNOWN.items():
        # objdump may render either bare hex target or # 0x... comments
        if f"{addr:x}" in lower or f"0x{addr:x}" in lower:
            refs.append(name)
    return refs


def interesting_indexes(insns: list[str]) -> set[int]:
    out = set()
    for i, line in enumerate(insns):
        refs = referenced_known(line)
        if CALL_RE.search(line) or refs:
            out.update(range(max(0, i - 8), min(len(insns), i + 5)))

        # Also preserve immediate byte stores / command-building instructions.
        if (
            "byte ptr" in line.lower()
            and re.search(r",0x[0-9a-fA-F]{1,2}\b", line)
        ):
            out.update(range(max(0, i - 2), min(len(insns), i + 3)))
    return out


def function_stop(rva: int, sorted_rvas: list[int], image_base: int, cap: int):
    next_rva = None
    for candidate in sorted_rvas:
        if candidate > rva:
            next_rva = candidate
            break

    end_rva = rva + cap
    if next_rva is not None:
        end_rva = min(end_rva, next_rva)
    if end_rva <= rva:
        end_rva = rva + cap
    return image_base + end_rva


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll", help="Path to KeyLinkerlib.dll")
    ap.add_argument(
        "--objdump",
        default=shutil.which("objdump") or "/usr/bin/objdump",
        help="objdump binary",
    )
    ap.add_argument(
        "--cap",
        type=lambda x: int(x, 0),
        default=0x800,
        help="maximum bytes to disassemble per export (default 0x800)",
    )
    args = ap.parse_args()

    dll = Path(args.dll)
    if not dll.is_file():
        raise SystemExit(f"DLL not found: {dll}")
    if not Path(args.objdump).is_file():
        raise SystemExit(f"objdump not found: {args.objdump}")

    pe = pefile.PE(str(dll), fast_load=False)
    exports = export_map(pe)
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    sorted_rvas = sorted(set(exports.values()))

    print("=" * 112)
    print("KEYLINKER WARM-UP READ BUILDERS — STATIC NATIVE AUDIT")
    print("=" * 112)
    print("DLL       :", dll)
    print(f"ImageBase : 0x{image_base:X}")
    print("objdump   :", args.objdump)
    print()

    missing = [name for name in TARGETS if name not in exports]
    if missing:
        print("Missing exports:")
        for name in missing:
            print("  -", name)
        print()

    summary = []

    for name in TARGETS:
        rva = exports.get(name)
        if rva is None:
            continue

        start = image_base + rva
        stop = function_stop(rva, sorted_rvas, image_base, args.cap)
        raw = run_objdump(args.objdump, dll, start, stop)
        insns = instruction_lines(raw)

        known_refs = []
        call_count = 0
        for line in insns:
            if CALL_RE.search(line):
                call_count += 1
            for ref in referenced_known(line):
                if ref not in known_refs:
                    known_refs.append(ref)

        print("-" * 112)
        print(name)
        print(f"RVA   : 0x{rva:X}")
        print(f"VA    : 0x{start:X}")
        print(f"Stop  : 0x{stop:X}")
        print(f"Calls : {call_count}")
        print(
            "Known : "
            + (", ".join(known_refs) if known_refs else "(none in bounded range)")
        )

        show = interesting_indexes(insns)
        if not show:
            show = set(range(min(50, len(insns))))

        last = None
        for i in sorted(show):
            if last is not None and i != last + 1:
                print("  ...")
            suffix = ""
            refs = referenced_known(insns[i])
            if refs:
                suffix = "    <== " + ", ".join(refs)
            print(insns[i] + suffix)
            last = i
        print()

        summary.append((name, rva, call_count, known_refs))

    print("=" * 112)
    print("SUMMARY")
    print("=" * 112)
    for name, rva, calls, refs in summary:
        print(
            f"{name:<34} RVA=0x{rva:05X} calls={calls:<2} "
            f"known={','.join(refs) if refs else '-'}"
        )

    print()
    print("No DLL code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static PE export parsing + objdump only.")


if __name__ == "__main__":
    main()
