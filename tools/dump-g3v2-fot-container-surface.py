#!/usr/bin/env python3
"""
Static scanner for the G3V2 updater's *full* firmware-container surface.

READ-ONLY. No device I/O.

Why this exists:
- The exact F0-transferred range at file offset 0x1712F0 is almost perfectly
  high-entropy and contains no plaintext FOT/XCOD/XRES markers.
- Independent ZhiXu/UsbUpdateAppX work shows sibling products often embed a
  plaintext 0x200-byte FOT header/directory immediately before encrypted
  XCOD/XRES sections.
- We therefore scan the entire updater, not only the transferred range, for
  FOT magic, XOR-0x80 section tags, the exact hardware ID and low-entropy
  metadata adjacent to the transfer buffer.

This script does not assume the sibling format is identical. It prints evidence
and marks only structurally plausible candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import struct
from collections import Counter
from pathlib import Path

EXPECTED_UPDATER_SHA256 = (
    "f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255"
)
TRANSFER_OFF = 0x1712F0
TRANSFER_LEN = 0x5A400
HWID = bytes.fromhex("a1ca51da724cb6103658c05d7d4e2267")
TAGS = ("INFO", "DEV", "KEY", "DATA", "XCOD", "XRES", "DOWN")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entropy(buf: bytes) -> float:
    if not buf:
        return 0.0
    c = Counter(buf)
    n = len(buf)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def find_all(data: bytes, needle: bytes):
    start = 0
    while True:
        pos = data.find(needle, start)
        if pos < 0:
            return
        yield pos
        start = pos + 1


def xor80_tag(tag: str) -> bytes:
    return bytes(ord(ch) ^ 0x80 for ch in tag)


def hexdump(data: bytes, base: int, width: int = 16):
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexpart = " ".join(f"{b:02X}" for b in chunk)
        text = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in chunk)
        print(f"  {base+off:08X}: {hexpart:<47}  {text}")


def u32(data: bytes, off: int):
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, off)[0]


def plausible_fot(data: bytes, off: int):
    if off + 0xC8 > len(data) or data[off:off + 4] != b"FOT\x00":
        return None
    logical_minus_8 = u32(data, off + 4)
    if logical_minus_8 is None:
        return None
    logical = logical_minus_8 + 8
    stored = (logical + 0x1FF) & ~0x1FF
    hwid = data[off + 0x70:off + 0x80]
    version = u32(data, off + 0x80)
    data_start = u32(data, off + 0xB8)
    data_len = u32(data, off + 0xBC)
    directory_copy = u32(data, off + 0xC0)
    config_start = u32(data, off + 0xC4)
    in_file = off + stored <= len(data)
    return {
        "logical": logical,
        "stored": stored,
        "hwid": hwid,
        "version": version,
        "data_start": data_start,
        "data_len": data_len,
        "directory_copy": directory_copy,
        "config_start": config_start,
        "in_file": in_file,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("updater")
    args = ap.parse_args()

    path = Path(args.updater)
    if not path.is_file():
        raise SystemExit(f"missing: {path}")

    data = path.read_bytes()
    got = sha256(data)

    print("=" * 112)
    print("G3V2 v1.48 — FULL FOT / CONTAINER SURFACE SCAN")
    print("=" * 112)
    print("File  :", path)
    print(f"Size  : 0x{len(data):X} ({len(data)})")
    print("SHA256:", got)
    print("Exact official updater:", got == EXPECTED_UPDATER_SHA256)
    print(f"Known F0 range: 0x{TRANSFER_OFF:X}-0x{TRANSFER_OFF+TRANSFER_LEN-1:X}")
    if TRANSFER_OFF + TRANSFER_LEN > len(data):
        raise SystemExit("known transfer range does not fit file")

    transferred = data[TRANSFER_OFF:TRANSFER_OFF + TRANSFER_LEN]
    print("F0 range SHA256 :", sha256(transferred))
    print(f"F0 range entropy: {entropy(transferred):.6f}")

    print("\n=== RAW FOT MAGIC ACROSS THE ENTIRE EXE ===")
    fots = list(find_all(data, b"FOT\x00"))
    print("count:", len(fots))
    if not fots:
        print("(none)")
    for off in fots:
        info = plausible_fot(data, off)
        print(f"\nFOT @ file+0x{off:X}")
        if info is None:
            print("  not structurally parseable")
            continue
        print(f"  logical size : 0x{info['logical']:X}")
        print(f"  stored size  : 0x{info['stored']:X}")
        print(f"  fits file    : {info['in_file']}")
        print(f"  hardware ID  : {info['hwid'].hex()}")
        print(f"  version u32  : {info['version']}")
        print(f"  data_start   : {info['data_start']!r}")
        print(f"  data_len     : {info['data_len']!r}")
        print(f"  directory    : {info['directory_copy']!r}")
        print(f"  config_start : {info['config_start']!r}")
        print(f"  relation to F0 transfer: delta={TRANSFER_OFF-off:+#x}")

    print("\n=== XOR-0x80 SECTION TAGS ACROSS THE ENTIRE EXE ===")
    tag_hits = {}
    for tag in TAGS:
        enc = xor80_tag(tag)
        hits = list(find_all(data, enc))
        tag_hits[tag] = hits
        print(f"{tag:5s} encoded={enc.hex()} count={len(hits)}",
              "" if not hits else " @ " + ", ".join(f"0x{x:X}" for x in hits[:40]))
        if len(hits) > 40:
            print(f"      ... {len(hits)-40} more")

    print("\n=== EXACT G3V2 HARDWARE ID ACROSS THE ENTIRE EXE ===")
    hw_hits = list(find_all(data, HWID))
    print("HWID:", HWID.hex())
    print("count:", len(hw_hits))
    if not hw_hits:
        print("(none)")
    for off in hw_hits:
        print(f"\nHWID @ file+0x{off:X} (delta to F0={TRANSFER_OFF-off:+#x})")
        # Same-family FOT headers often store version immediately after 16-byte HWID.
        if off + 20 <= len(data):
            print(f"  +0x10 u16 LE: {struct.unpack_from('<H', data, off+16)[0]}")
            print(f"  +0x10 u32 LE: {struct.unpack_from('<I', data, off+16)[0]}")
        lo = max(0, off - 0x80)
        hi = min(len(data), off + 0x100)
        hexdump(data[lo:hi], lo)

    print("\n=== ENTROPY AROUND THE F0 TRANSFER START ===")
    lo = max(0, TRANSFER_OFF - 0x2000)
    hi = min(len(data), TRANSFER_OFF + 0x2000)
    for off in range(lo, hi, 0x200):
        chunk = data[off:min(off+0x200, len(data))]
        relation = "<F0" if off < TRANSFER_OFF else "F0>" if off > TRANSFER_OFF else "F0 START"
        print(f"0x{off:08X} H={entropy(chunk):.5f} {relation}")

    print("\n=== 0x1000 BYTES IMMEDIATELY BEFORE F0 START ===")
    pre = data[max(0, TRANSFER_OFF - 0x1000):TRANSFER_OFF]
    hexdump(pre, TRANSFER_OFF - len(pre))

    print("\n=== FIRST 0x400 BYTES ACTUALLY SENT BY F0 ===")
    hexdump(transferred[:0x400], TRANSFER_OFF)

    print("\n=== NEARBY STRUCTURE SUMMARY ===")
    near_lo = max(0, TRANSFER_OFF - 0x4000)
    near_hi = min(len(data), TRANSFER_OFF + 0x4000)
    near_fot = [x for x in fots if near_lo <= x < near_hi]
    near_tags = {
        tag: [x for x in hits if near_lo <= x < near_hi]
        for tag, hits in tag_hits.items()
        if any(near_lo <= x < near_hi for x in hits)
    }
    near_hwid = [x for x in hw_hits if near_lo <= x < near_hi]
    print("FOT magic near transfer:", [hex(x) for x in near_fot] or "(none)")
    print("encoded tags near transfer:", {
        k: [hex(x) for x in v] for k, v in near_tags.items()
    } or "(none)")
    print("HWID near transfer:", [hex(x) for x in near_hwid] or "(none)")

    print("\n=== INTERPRETATION ===")
    if near_fot:
        print("* A plaintext FOT header exists close to the F0 buffer; inspect its parsed")
        print("  size/offset fields before treating 0x1712F0 as the container start.")
    else:
        print("* No plaintext FOT magic was found near the known F0 transfer start.")
    if near_tags:
        print("* XOR-0x80 section tags exist nearby, supporting a FOT-family directory.")
    else:
        print("* No sibling-style XOR-0x80 section tags were found near the F0 start.")
    if near_hwid:
        print("* The exact ...2267 hardware ID is stored close to the transfer buffer;")
        print("  this may be host-side metadata even if the sent payload starts later.")
    else:
        print("* The exact hardware ID is not adjacent to the transfer buffer.")
    print("* Do not infer encryption format from entropy alone; this scan exists to")
    print("  distinguish a full FOT container from an extracted/transformed payload.")
    print("\nNo device I/O was performed.")


if __name__ == "__main__":
    main()
