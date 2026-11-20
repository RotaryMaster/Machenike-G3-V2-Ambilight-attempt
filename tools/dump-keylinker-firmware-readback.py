#!/usr/bin/env python3
"""
Static inventory of possible firmware/memory readback primitives in
KeyLinkerlib.dll and first-party managed assemblies.

READ-ONLY: no device access.

Usage:
  python tools/dump-keylinker-firmware-readback.py \
      /path/to/mixdrive-extraido

The tool deliberately reports candidates instead of declaring a dump API from
a name alone. A real firmware-dump primitive needs an address/offset plus a
read result whose length can scale beyond identity/status metadata.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

KEYS = (
    "firmware", "upgrade", "update", "ota", "flash", "rom", "memory",
    "boot", "dfu", "isp", "read", "dump", "code", "bin", "fot",
)

STRONG = (
    "readfirmware", "readflash", "readrom", "readmemory", "dumpfirmware",
    "firmwareread", "flashread", "romread", "memoryread",
)


def run(cmd):
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    return p.stdout


def ascii_utf16(data: bytes, min_len=5):
    out = []
    # ASCII
    start = None
    for i, b in enumerate(data):
        if 0x20 <= b <= 0x7e:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_len:
                out.append((start, data[start:i].decode("ascii", "replace")))
            start = None
    # UTF16LE
    i = 0
    while i + 1 < len(data):
        if 0x20 <= data[i] <= 0x7e and data[i+1] == 0:
            s = i
            chars = []
            while i + 1 < len(data) and 0x20 <= data[i] <= 0x7e and data[i+1] == 0:
                chars.append(chr(data[i]))
                i += 2
            if len(chars) >= min_len:
                out.append((s, "".join(chars)))
        else:
            i += 1
    return sorted(out)


def relevant(s: str) -> bool:
    lo = s.lower()
    return any(k in lo for k in KEYS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="extracted MixDrive root")
    args = ap.parse_args()

    root = Path(args.root)
    dll = root / "Libs" / "Gamepad" / "KeyLinkerlib.dll"
    if not dll.is_file():
        raise SystemExit(f"missing: {dll}")

    objdump = shutil.which("objdump")
    if not objdump:
        raise SystemExit("objdump not found")

    print("=" * 108)
    print("KEYLINKER FIRMWARE / MEMORY READBACK SURFACE — STATIC ONLY")
    print("=" * 108)
    print("Root:", root)
    print("DLL :", dll)

    pe = run([objdump, "-p", str(dll)])
    lines = pe.splitlines()

    print("\n=== NATIVE EXPORT/IMPORT LINES MATCHING READBACK TERMS ===")
    matches = []
    for ln in lines:
        if relevant(ln):
            matches.append(ln)
    print("\n".join(matches) if matches else "(none)")

    print("\n=== RELEVANT NATIVE STRINGS ===")
    rows = [(o, s) for o, s in ascii_utf16(dll.read_bytes()) if relevant(s)]
    for off, s in rows[:300]:
        mark = " ***" if any(x in s.lower().replace("_","") for x in STRONG) else ""
        print(f"0x{off:08X}: {s}{mark}")
    if len(rows) > 300:
        print(f"... {len(rows)-300} more omitted")

    print("\n=== MANAGED ASSEMBLY STRING SURFACE ===")
    assemblies = [
        root / "Machenik.exe",
        root / "Machenik.Infrastructure.dll",
        root / "Machenik.Shared.dll",
    ]
    for asm in assemblies:
        if not asm.is_file():
            continue
        rows = [(o, s) for o, s in ascii_utf16(asm.read_bytes()) if relevant(s)]
        print(f"\n[{asm.name}]")
        if not rows:
            print("(none)")
        else:
            for off, s in rows[:220]:
                print(f"0x{off:08X}: {s}")
            if len(rows) > 220:
                print(f"... {len(rows)-220} more omitted")

    print("\n=== DISASSEMBLY CONTEXT: SUSPICIOUS SIZE / ADDRESS TERMS ===")
    dis = run([objdump, "-d", "-M", "intel", str(dll)])
    dl = dis.splitlines()
    # Context around obvious large transfer sizes and common flash-ish constants.
    pats = [
        ("512B", re.compile(r"(?i)\b0x200\b")),
        ("1KiB", re.compile(r"(?i)\b0x400\b")),
        ("4KiB", re.compile(r"(?i)\b0x1000\b")),
        ("64KiB", re.compile(r"(?i)\b0x10000\b")),
        ("256KiB", re.compile(r"(?i)\b0x40000\b")),
        ("512KiB", re.compile(r"(?i)\b0x80000\b")),
    ]
    shown = 0
    for i, ln in enumerate(dl):
        for label, rx in pats:
            if rx.search(ln):
                shown += 1
                print(f"\n[{shown}] {label} line {i+1}")
                for x in dl[max(0,i-8):min(len(dl),i+9)]:
                    print(x)
                break
        if shown >= 80:
            break
    if shown == 0:
        print("(no selected transfer-size constants found)")

    print("\n=== DECISION RULE ===")
    print("A candidate is useful for firmware dumping only if static code proves:")
    print("  * it is a READ path;")
    print("  * caller controls an address/offset or advances a read pointer;")
    print("  * returned data length can scale to firmware-sized ranges;")
    print("  * it does not mutate boot/config state as a side effect.")
    print("Identity/version/status queries do not qualify.")
    print("\nNo HID/BLE/USB/SCSI access was performed.")


if __name__ == "__main__":
    main()
