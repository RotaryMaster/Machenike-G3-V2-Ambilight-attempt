#!/usr/bin/env python3
"""
Resolve selected KeyLinkerlib.dll IAT slots used by LampLight serialization.

This closes the remaining ambiguity in the order of the CRT string helpers
used to combine the 15-bit breathSpeed string and the 1-bit breathSwitch
string.

No code is executed. No device access is performed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pefile

TARGETS = {
    0x18000B0B0,
    0x18000B130,
    0x18000B138,
    0x18000B140,
    0x18000B150,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll", help="Path to KeyLinkerlib.dll")
    args = ap.parse_args()

    dll = Path(args.dll)
    if not dll.is_file():
        raise SystemExit(f"Missing DLL: {dll}")

    pe = pefile.PE(str(dll), fast_load=False)

    print("=" * 96)
    print("KEYLINKER LAMPLIGHT IAT RESOLUTION")
    print("=" * 96)
    print("DLL:", dll)
    print()

    found = set()

    for desc in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        dll_name = desc.dll.decode("ascii", errors="replace")
        for imp in desc.imports:
            va = int(imp.address)
            if va not in TARGETS:
                continue
            found.add(va)
            if imp.name:
                name = imp.name.decode("ascii", errors="replace")
            else:
                name = f"ordinal:{imp.ordinal}"
            print(f"0x{va:016X} -> {dll_name}!{name}")

    missing = sorted(TARGETS - found)
    if missing:
        print("\nUnresolved target slots:")
        for va in missing:
            print(f"  0x{va:016X}")

    print("\nNo DLL code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static PE import-table parsing only.")


if __name__ == "__main__":
    main()
