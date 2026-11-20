#!/usr/bin/env python3
"""
Read-only inspection of Bluetooth/gamepad identity-like fields in G3V2 FOTs.

Scans complete FOT images for:
- 98:B6:E* six-byte address-like sequences;
- a supplied live Bluetooth address and its suffix/reversed forms;
- the known header slot around 0x112;
- all occurrences in plaintext header/config and ciphertext too, clearly labeled.

No device I/O. No writes. No updater execution.
"""

from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path

MAGIC = b"FOT\0"


def load(path: Path) -> bytes:
    b = path.read_bytes()
    if len(b) < 0x200 or b[:4] != MAGIC:
        raise SystemExit(f"{path}: expected complete FOT at offset 0")
    logical = struct.unpack_from("<I", b, 4)[0] + 8
    if logical > len(b):
        raise SystemExit(f"{path}: invalid logical size")
    return b[:logical]


def macfmt(x: bytes) -> str:
    return ":".join(f"{b:02X}" for b in x)


def region(off: int) -> str:
    if off < 0x200:
        return "header"
    if off < 0x58600:
        return "XCOD"
    if off < 0x59200:
        return "XRES"
    if off < 0x59400:
        return "directory-copy"
    if off < 0x59800:
        return "config"
    return "tail"


def hits(h: bytes, n: bytes):
    out = []
    p = 0
    while True:
        p = h.find(n, p)
        if p < 0:
            return out
        out.append(p)
        p += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--live", help="live Bluetooth address, e.g. 98:B6:ED:68:58:43")
    args = ap.parse_args()

    live = None
    if args.live:
        parts = re.split(r"[:-]", args.live.strip())
        if len(parts) != 6:
            raise SystemExit("--live must contain six hex bytes")
        live = bytes(int(x, 16) for x in parts)

    print("=" * 116)
    print("G3V2 FOT BLUETOOTH / IDENTITY SURFACE — READ ONLY")
    print("=" * 116)
    print("Device I/O: none")
    if live:
        print("Live address:", macfmt(live))
        print("Live suffix :", live[3:].hex(" "))
        print("Reverse     :", live[::-1].hex(" "))
    print()

    for path in args.files:
        b = load(path)
        print("-" * 116)
        print(path)
        print(f"size=0x{len(b):X}")

        if len(b) >= 0x128:
            print("header 0x0FE..0x127:", b[0xFE:0x128].hex(" "))
            print("slot 0x112..0x117  :", macfmt(b[0x112:0x118]))
            print("slot 0x122..0x127  :", macfmt(b[0x122:0x128]))

        print("\n98:B6:E* address-like sequences:")
        found = []
        for i in range(len(b) - 5):
            if b[i] == 0x98 and b[i+1] == 0xB6 and (b[i+2] & 0xF0) == 0xE0:
                found.append((i, b[i:i+6]))
        if found:
            for off, val in found:
                print(f"  0x{off:06X} {region(off):14} {macfmt(val)}")
        else:
            print("  (none)")

        if live:
            probes = [
                ("exact live MAC", live),
                ("reversed live MAC", live[::-1]),
                ("live suffix", live[3:]),
                ("reversed suffix", live[3:][::-1]),
            ]
            print("\nLive-address byte probes:")
            for label, needle in probes:
                pos = hits(b, needle)
                if pos:
                    print(f"  {label:20}: " + ", ".join(f"0x{x:X}({region(x)})" for x in pos[:32]))
                else:
                    print(f"  {label:20}: (none)")

        # Check same suffix under E9/EC/ED family prefixes.
        if live:
            print("\nSame live suffix under family prefixes:")
            for pfx in (bytes.fromhex("98 b6 e9"), bytes.fromhex("98 b6 ec"), bytes.fromhex("98 b6 ed")):
                candidate = pfx + live[3:]
                pos = hits(b, candidate)
                print(f"  {macfmt(candidate)}: " +
                      (", ".join(f"0x{x:X}({region(x)})" for x in pos) if pos else "(none)"))

        print()

    print("=== INTERPRETATION GUARDRAILS ===")
    print("* 98:B6:E* is address-like family evidence, not proof that every occurrence is an active BLE address.")
    print("* A live ED address not matching an E9 FOT field can reflect distinct Bluetooth personalities/interfaces.")
    print("* Matches inside XCOD/XRES/tail can occur by chance because those regions are ciphertext.")
    print("* Header/config matches are the meaningful ones.")
    print("* No file or device was modified.")


if __name__ == "__main__":
    main()
