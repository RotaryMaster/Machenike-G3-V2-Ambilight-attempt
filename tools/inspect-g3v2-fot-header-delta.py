#!/usr/bin/env python3
"""
Read-only forensic inspection of the tiny header delta between two complete
G3V2 / ZXD2267 FOT images.

The currently known pair has byte-identical XCOD, XRES, config and tail, with
only four header bytes differing (mirrored in the directory copy). This tool
prints the exact values, aligned LE fields, local context, occurrence counts,
and common checksum candidates to help classify those bytes.

No device I/O. No execution of updater payloads. No file modification.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import os
import struct
from pathlib import Path
from datetime import datetime, timezone

MAGIC = b"FOT\0"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def fmt_time(v: int) -> str:
    # Only render plausible modern Unix timestamps; otherwise explicitly reject.
    if 946684800 <= v <= 4102444800:
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return "(not a plausible 2000-2100 Unix timestamp)"


def crc32(b: bytes) -> int:
    return binascii.crc32(b) & 0xFFFFFFFF


def adler32(b: bytes) -> int:
    import zlib
    return zlib.adler32(b) & 0xFFFFFFFF


def load(path: Path) -> bytes:
    b = path.read_bytes()
    if len(b) < 0x5A400 or b[:4] != MAGIC:
        raise SystemExit(f"{path}: expected complete FOT at offset 0")
    logical = u32(b, 4) + 8
    if logical > len(b):
        raise SystemExit(f"{path}: logical size exceeds file length")
    return b[:logical]


def hexline(b: bytes, start: int, end: int) -> str:
    out = []
    for off in range(start, end, 16):
        chunk = b[off:min(off+16, end)]
        out.append(f"  {off:06X}: " + " ".join(f"{x:02X}" for x in chunk))
    return "\n".join(out)


def occurrences(haystack: bytes, needle: bytes):
    if not needle:
        return []
    hits = []
    p = 0
    while True:
        p = haystack.find(needle, p)
        if p < 0:
            return hits
        hits.append(p)
        p += 1


def checksum_table(label: str, b: bytes):
    regions = {
        "whole": b,
        "header": b[:0x200],
        "XCOD": b[0x200:0x58600],
        "XCOD payload": b[0x210:0x58600],
        "XRES": b[0x58600:0x59200],
        "XRES payload": b[0x58610:0x59200],
        "directory-copy": b[0x59200:0x59400],
        "config": b[0x59400:0x59800],
        "tail": b[0x59800:0x5A400],
        "DATA": b[0x200:0x59200],
    }
    print(f"\n=== {label} COMMON CHECKSUMS ===")
    for name, r in regions.items():
        print(f"{name:16s} CRC32=0x{crc32(r):08X} Adler32=0x{adler32(r):08X} SHA256={sha256(r)[:16]}...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    args = ap.parse_args()

    A = load(args.a)
    B = load(args.b)
    if len(A) != len(B):
        raise SystemExit("logical sizes differ")

    diffs = [i for i,(x,y) in enumerate(zip(A,B)) if x != y]
    print("=" * 120)
    print("G3V2 FOT HEADER DELTA FORENSICS — READ ONLY")
    print("=" * 120)
    print("Device I/O: none")
    print("A:", args.a)
    print("B:", args.b)
    print("A SHA256:", sha256(A))
    print("B SHA256:", sha256(B))
    print("A mtime :", datetime.fromtimestamp(os.stat(args.a).st_mtime, tz=timezone.utc).isoformat())
    print("B mtime :", datetime.fromtimestamp(os.stat(args.b).st_mtime, tz=timezone.utc).isoformat())
    print("diff count:", len(diffs))
    print("diff offsets:", ", ".join(f"0x{x:X}" for x in diffs))

    # Canonicalize mirrored directory-copy differences back to header offsets.
    canon = sorted(set(i if i < 0x200 else i - 0x59200 for i in diffs))
    print("canonical header diff offsets:", ", ".join(f"0x{x:X}" for x in canon))

    print("\n=== EXACT DIFFERING BYTES ===")
    for i in diffs:
        print(f"0x{i:06X}: A={A[i]:02X} B={B[i]:02X} XOR={A[i]^B[i]:02X}")

    # Context around the two known changed areas.
    starts = sorted(set((i // 16) * 16 for i in canon))
    print("\n=== HEADER CONTEXT ===")
    for s in starts:
        lo = max(0, s - 16)
        hi = min(0x200, s + 32)
        print(f"\nA 0x{lo:X}-0x{hi-1:X}:")
        print(hexline(A, lo, hi))
        print(f"B 0x{lo:X}-0x{hi-1:X}:")
        print(hexline(B, lo, hi))

    # Inspect aligned 16/32-bit fields touching every changed byte.
    words32 = sorted(set(i & ~3 for i in canon))
    words16 = sorted(set(i & ~1 for i in canon))
    print("\n=== ALIGNED LITTLE-ENDIAN FIELDS TOUCHING DELTAS ===")
    for off in words16:
        av, bv = u16(A, off), u16(B, off)
        print(f"u16 @0x{off:03X}: A=0x{av:04X} ({av}) B=0x{bv:04X} ({bv}) XOR=0x{av^bv:04X}")
    for off in words32:
        av, bv = u32(A, off), u32(B, off)
        print(f"u32 @0x{off:03X}: A=0x{av:08X} ({av}) B=0x{bv:08X} ({bv}) XOR=0x{av^bv:08X}")
        print(f"             A unix={fmt_time(av)}")
        print(f"             B unix={fmt_time(bv)}")

        an = A[off:off+4]
        bn = B[off:off+4]
        ah = occurrences(A, an)
        bh = occurrences(B, bn)
        print("             A value occurrences:", ", ".join(f"0x{x:X}" for x in ah) or "(none)")
        print("             B value occurrences:", ", ".join(f"0x{x:X}" for x in bh) or "(none)")

    # Check header vs directory copy at field granularity.
    print("\n=== MIRROR CHECK ===")
    print("A header == directory-copy:", A[:0x200] == A[0x59200:0x59400])
    print("B header == directory-copy:", B[:0x200] == B[0x59200:0x59400])
    for off in words32:
        print(f"field @0x{off:03X}: "
              f"A head=0x{u32(A,off):08X} dir=0x{u32(A,0x59200+off):08X} | "
              f"B head=0x{u32(B,off):08X} dir=0x{u32(B,0x59200+off):08X}")

    checksum_table("A", A)
    checksum_table("B", B)

    # Test whether either changed u32 equals a common checksum of any obvious region.
    print("\n=== CHANGED U32 VS COMMON CHECKSUM CANDIDATES ===")
    regions = {
        "whole": (0, len(A)),
        "header": (0, 0x200),
        "XCOD": (0x200, 0x58600),
        "XCOD payload": (0x210, 0x58600),
        "XRES": (0x58600, 0x59200),
        "XRES payload": (0x58610, 0x59200),
        "DATA": (0x200, 0x59200),
        "config": (0x59400, 0x59800),
        "tail": (0x59800, 0x5A400),
    }
    any_match = False
    for off in words32:
        for tag, blob in (("A",A),("B",B)):
            val = u32(blob, off)
            for rname,(s,e) in regions.items():
                c = crc32(blob[s:e])
                ad = adler32(blob[s:e])
                if val in (c,ad):
                    any_match = True
                    kind = "CRC32" if val == c else "Adler32"
                    print(f"{tag} field@0x{off:03X}=0x{val:08X} == {kind}({rname})")
    if not any_match:
        print("No changed u32 equals CRC32/Adler32 of the obvious FOT regions.")

    print("\n=== CONCLUSION GUARDRAILS ===")
    print("* XCOD/XRES identity means this pair is not a useful cross-ciphertext pair for keystream recovery.")
    print("* Mirrored header changes are deliberate container metadata differences, not payload differences.")
    print("* A changed field matching a common checksum would suggest integrity metadata; no match does not prove otherwise.")
    print("* File mtime is only filesystem metadata and must not be treated as original provenance.")
    print("* No file was modified and no device I/O was performed.")


if __name__ == "__main__":
    main()
