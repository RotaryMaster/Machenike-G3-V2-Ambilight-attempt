#!/usr/bin/env python3
"""
Focused read-only analysis of the G3V2 v1.48 ZXD/EPIC FOT container.

No device I/O. No updater execution.

Goals:
- parse the plaintext FOT metadata correctly;
- separate header / XCOD / XRES / directory copy / config / tail;
- verify the directory-copy relationship;
- fingerprint the KEY record;
- measure ciphertext-looking payload regions independently from plaintext metadata;
- check for repeated 16-byte blocks (simple ECB sanity check);
- print strings only from plaintext regions.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import struct
from collections import Counter
from pathlib import Path

EXPECTED_UPDATER_SHA256 = "f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255"
FOT_OFF = 0x1712F0
FOT_LEN = 0x5A400
KNOWN_KEY = bytes.fromhex("00000001eae32e09")
TAGS = ("INFO", "DEV", "KEY", "DATA", "XCOD", "XRES", "DOWN")


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((v / n) * math.log2(v / n) for v in counts.values())


def chi2(data: bytes) -> float:
    if not data:
        return 0.0
    expected = len(data) / 256.0
    counts = Counter(data)
    return sum((counts.get(i, 0) - expected) ** 2 / expected for i in range(256))


def xor80_tag(name: str) -> bytes:
    return bytes(ord(ch) ^ 0x80 for ch in name)


def find_all(data: bytes, needle: bytes) -> list[int]:
    out = []
    start = 0
    while True:
        p = data.find(needle, start)
        if p < 0:
            return out
        out.append(p)
        start = p + 1


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repeated_blocks(data: bytes, size: int = 16) -> tuple[int, int, int]:
    blocks = [data[i:i+size] for i in range(0, len(data) - size + 1, size)]
    c = Counter(blocks)
    repeated_values = sum(1 for n in c.values() if n > 1)
    repeated_instances = sum(n - 1 for n in c.values() if n > 1)
    return len(blocks), repeated_values, repeated_instances


def strings(data: bytes, minimum: int = 4):
    pat = re.compile(rb"[\x20-\x7e]{" + str(minimum).encode() + rb",}")
    for m in pat.finditer(data):
        yield m.start(), m.group().decode("ascii", "replace")


def stat(name: str, start: int, blob: bytes):
    blocks, repeated_values, repeated_instances = repeated_blocks(blob)
    zero = 100.0 * blob.count(0) / len(blob) if blob else 0.0
    print(
        f"{name:16} off=0x{start:06X} len=0x{len(blob):06X} ({len(blob):7d}) "
        f"H={entropy(blob):.6f} chi2={chi2(blob):9.2f} zero={zero:6.3f}% "
        f"16B_blocks={blocks} repeated_values={repeated_values} repeated_instances={repeated_instances}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="official updater EXE or carved 0x5A400-byte FOT")
    args = ap.parse_args()

    p = Path(args.image)
    raw = p.read_bytes()

    if len(raw) == FOT_LEN and raw.startswith(b"FOT\0"):
        fot = raw
        source = "already-carved FOT"
    else:
        got = sha256(raw)
        print("Updater SHA256:", got)
        print("Exact official updater:", got == EXPECTED_UPDATER_SHA256)
        if len(raw) < FOT_OFF + FOT_LEN:
            raise SystemExit("input is neither the known FOT nor a large-enough updater")
        fot = raw[FOT_OFF:FOT_OFF + FOT_LEN]
        source = f"carved file+0x{FOT_OFF:X}"

    if not fot.startswith(b"FOT\0"):
        raise SystemExit("FOT magic missing at container start")

    print("=" * 112)
    print("G3V2 v1.48 — FOT CRYPTO / STRUCTURE SURFACE")
    print("=" * 112)
    print("Input :", p)
    print("Source:", source)
    print(f"Size  : 0x{len(fot):X} ({len(fot)})")
    print("SHA256:", sha256(fot))

    logical = u32(fot, 4) + 8
    hwid = fot[0x70:0x80]
    version = u32(fot, 0x80)
    data_start = u32(fot, 0xB8)
    data_len = u32(fot, 0xBC)
    directory = u32(fot, 0xC0)
    config = u32(fot, 0xC4)

    print("\n=== HEADER ===")
    print(f"logical size   : 0x{logical:X}")
    print(f"hardware ID    : {hwid.hex()}")
    print(f"version        : {version}")
    print(f"data_start     : 0x{data_start:X}")
    print(f"data_len       : 0x{data_len:X}")
    print(f"data_end       : 0x{data_start + data_len:X} (exclusive)")
    print(f"directory_copy : 0x{directory:X}")
    print(f"config_start   : 0x{config:X}")
    print("DATA end == directory:", data_start + data_len == directory)

    print("\n=== XOR-0x80 TAGS ===")
    hits = {}
    for tag in TAGS:
        pos = find_all(fot, xor80_tag(tag))
        hits[tag] = pos
        print(f"{tag:5}: " + (", ".join(f"0x{x:X}" for x in pos) if pos else "(none)"))

    xcod = hits["XCOD"][0] if hits["XCOD"] else None
    xres = hits["XRES"][0] if hits["XRES"] else None
    if xcod is None or xres is None:
        raise SystemExit("XCOD/XRES section tags not found")

    print("\n=== DIRECTORY COPY ===")
    if directory + 0x200 <= len(fot):
        equal = fot[:0x200] == fot[directory:directory + 0x200]
        print("first 0x200 == directory-copy 0x200:", equal)
        print("first SHA256 :", sha256(fot[:0x200]))
        print("copy  SHA256 :", sha256(fot[directory:directory + 0x200]))
    else:
        print("directory copy does not fit image")

    key_tag = hits["KEY"][0] if hits["KEY"] else None
    print("\n=== KEY RECORD ===")
    if key_tag is not None and key_tag + 16 <= len(fot):
        key_len = u32(fot, key_tag + 4)
        key = fot[key_tag + 8:key_tag + 8 + key_len]
        print(f"KEY offset : 0x{key_tag:X}")
        print(f"KEY length : {key_len}")
        print("KEY bytes  :", key.hex())
        print("matches known family constant:", key == KNOWN_KEY)
    else:
        print("KEY record unavailable")

    config_end = min(len(fot), config + 0x400)
    tail_start = config_end

    regions = [
        ("header", 0, xcod),
        ("XCOD", xcod, xres),
        ("XRES", xres, directory),
        ("directory-copy", directory, config),
        ("config", config, config_end),
        ("tail", tail_start, len(fot)),
    ]

    print("\n=== REGION STATS ===")
    stat("full FOT", 0, fot)
    for name, a, b in regions:
        if 0 <= a < b <= len(fot):
            stat(name, a, fot[a:b])

    print("\n=== ENCRYPTED PAYLOAD STATS (16-byte section header skipped) ===")
    for name, a, b in (("XCOD payload", xcod + 16, xres), ("XRES payload", xres + 16, directory)):
        if a < b:
            stat(name, a, fot[a:b])

    print("\n=== XCOD / XRES 16-BYTE SECTION HEADERS ===")
    for name, off in (("XCOD", xcod), ("XRES", xres)):
        hdr = fot[off:off+16]
        print(f"{name} @ 0x{off:X}: {hdr.hex(' ')}")
        if len(hdr) == 16:
            print(
                "  encoded tag=", hdr[:4].hex(),
                "u32[1]=0x%X" % u32(hdr, 4),
                "u32[2]=0x%X" % u32(hdr, 8),
                "u32[3]=0x%X" % u32(hdr, 12),
            )

    print("\n=== PLAINTEXT STRINGS: HEADER + CONFIG ONLY ===")
    for region_name, a, b in (("header", 0, xcod), ("config", config, config_end)):
        for off, value in strings(fot[a:b]):
            print(f"0x{a+off:06X} {region_name:7} {value}")

    print("\n=== INTERPRETATION GUARDRAILS ===")
    print("* FOT metadata, directory and config are plaintext structure.")
    print("* XOR 0x80 on section names is obfuscation, not payload encryption.")
    print("* XCOD/XRES payload statistics must be judged separately from the plaintext metadata.")
    print("* Zero repeated 16-byte blocks would rule out simple ECB, but not prove a specific stream cipher.")
    print("* The KEY constant is not automatically the decryption key.")
    print("* No device I/O was performed.")


if __name__ == "__main__":
    main()
