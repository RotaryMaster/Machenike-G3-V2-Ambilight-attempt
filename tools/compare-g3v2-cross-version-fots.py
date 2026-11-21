#!/usr/bin/env python3
"""Offline, read-only G3V2 FOT cross-version ciphertext comparison.

Accepts .fot/.bin firmware images, updater .exe files, and zip archives
containing MKG_FW_G3V2.exe. It NEVER executes an EXE, downloads anything,
opens the controller, or writes output files.

Different XCOD sizes (e.g. 1.48 vs 1.59) are supported. The comparison
is indexed relative to each encrypted section, not to whole-file offsets.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import math
from pathlib import Path
import struct
import sys
import zipfile

MAGIC = b"FOT\0"
G3V2_HWID = bytes.fromhex("a1ca51da724cb6103658c05d7d4e2267")
KEY_ID = bytes.fromhex("00000001eae32e09")
MAX_INPUT = 70 * 1024 * 1024
MAX_FOT = 8 * 1024 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u32(d: bytes, off: int) -> int:
    return struct.unpack_from("<I", d, off)[0]


def xor_tag(tag: str) -> bytes:
    return bytes(ord(ch) ^ 0x80 for ch in tag)


def source_bytes(path: Path):
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    if path.stat().st_size > MAX_INPUT:
        raise ValueError(f"input too large: {path}")
    if path.suffix.lower() != ".zip":
        return path.read_bytes(), path.name
    with zipfile.ZipFile(path) as z:
        files = [f for f in z.infolist()
                 if not f.is_dir()
                 and Path(f.filename).name.lower() == "mkg_fw_g3v2.exe"]
        if len(files) != 1:
            # Explicitly avoid accidentally selecting the receiver updater.
            raise ValueError(
                f"ZIP must contain exactly one MKG_FW_G3V2.exe; found {len(files)}"
            )
        item = files[0]
        if item.file_size > MAX_INPUT:
            raise ValueError("embedded EXE is too large")
        return z.read(item), f"{path.name}::{item.filename}"


def parse_candidate(source: bytes, pos: int):
    remaining = len(source) - pos
    if remaining < 0x800:
        return None
    logical = u32(source, pos + 4) + 8
    padded = (logical + 511) & ~511
    if not (0x200 <= logical <= MAX_FOT and padded <= remaining):
        return None
    raw = source[pos:pos+padded]
    hwid = raw[0x70:0x80]
    v = u32(raw, 0x80)
    data_start = u32(raw, 0xB8)
    data_len = u32(raw, 0xBC)
    directory = u32(raw, 0xC0)
    config = u32(raw, 0xC4)
    if not (hwid == G3V2_HWID and 0x200 <= data_start
            and data_start + data_len == directory
            and directory + 0x200 == config
            and config + 0x400 <= len(raw)):
        return None
    if raw[:0x200] != raw[directory:directory + 0x200]:
        return None
    xcod = raw.find(xor_tag("XCOD"), data_start, directory)
    xres = raw.find(xor_tag("XRES"), data_start, directory)
    if not (xcod == data_start and xcod + 16 < xres
            and xres + 16 < directory):
        return None
    return {
        "raw": raw, "off": pos, "ver": v, "hwid": hwid,
        "xcod": raw[xcod:xres], "xres": raw[xres:directory],
        "xcod_data": raw[xcod + 16:xres],
        "xres_data": raw[xres + 16:directory],
        "header": raw[:0x200],
        "config": raw[config:config + 0x400],
        "key": raw[0xA8:0xB0], "directory": directory,
    }


def load(path: Path):
    source, desc = source_bytes(path)
    hits = []
    off = 0
    while True:
        pos = source.find(MAGIC, off)
        if pos < 0:
            break
        off = pos + 4
        candidate = parse_candidate(source, pos)
        if candidate is not None:
            hits.append(candidate)
    if not hits:
        raise ValueError(f"{desc}: no complete G3V2 FOT with valid section layout")
    # Directory mirror of the FOT header is NOT itself a second firmware!
    if len(hits) > 1:
        raise ValueError(f"{desc}: ambiguous ({len(hits)}) complete validated FOTs")
    result = hits[0]
    result["source"] = desc
    return result


def entropy(data: bytes):
    if not data:
        return 0.0
    c = Counter(data)
    n = len(data)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def equal_prefix(a: bytes, b: bytes):
    i = 0
    for x, y in zip(a, b):
        if x != y:
            break
        i += 1
    return i


def equal_suffix(a: bytes, b: bytes):
    i = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        i += 1
    return i


def longest_same_run(a: bytes, b: bytes):
    best, bestend, run = 0, 0, 0
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            run += 1
            if run > best:
                best = run
                bestend = i + 1
        else:
            run = 0
    return bestend - best, best


def compare_region(name: str, a: bytes, b: bytes):
    common = min(len(a), len(b))
    x = bytes(i ^ j for i, j in zip(a[:common], b[:common]))
    equal = x.count(0)
    print(f"\n[{name}]")
    print(f"  A bytes: {len(a)}, SHA256 {sha(a)}")
    print(f"  B bytes: {len(b)}, SHA256 {sha(b)}")
    print(f"  size change B-A: {len(b) - len(a):+d}")
    print(f"  common section-relative bytes: {common}")
    print(f"  equal at identical relative offset: {equal}/{common} "
          f"({100 * equal / common:.4f}%)")
    print(f"  aligned XOR entropy: {entropy(x):.5f} bits/byte")
    print(f"  equal prefix: {equal_prefix(a, b)} bytes")
    print(f"  equal suffix: {equal_suffix(a, b)} bytes")
    start, length = longest_same_run(a, b)
    print(f"  longest equal same-offset run: +0x{start:X}, {length} bytes")
    for block in (16, 512, 4096):
        total = common // block
        matches = sum(
            a[i:i+block] == b[i:i+block]
            for i in range(0, total * block, block)
        )
        print(f"  matching aligned {block}-byte blocks: {matches}/{total}")

    if common < 4096 or len(a) == len(b):
        return
    # Detect insertions or deletions that shift the encrypted section
    # relative to itself. A shift only yields exact matches if the
    # keystream/ciphertext blocks were actually reused.
    block = 512
    best = []
    maxshift = min(32 * 1024, max(len(a), len(b)) // 4)
    for delta in range(-maxshift, maxshift + 1, block):
        if delta == 0:
            continue
        start_a, start_b = max(0, -delta), max(0, delta)
        overlap = min(len(a) - start_a, len(b) - start_b)
        if overlap <= block:
            continue
        matches = 0
        for j in range(0, overlap - block + 1, block):
            if a[start_a+j:start_a+j+block] == b[start_b+j:start_b+j+block]:
                matches += 1
        if matches:
            best.append((matches, delta))
    if best:
        for matches, delta in sorted(best, reverse=True)[:4]:
            print(f"  SHIFT candidate B relative to A: {delta:+d} bytes, "
                  f"{matches} exact 512B blocks")
    else:
        print("  no exact 512B shifted matches within +/-32 KiB")
    print("  Shift mismatches do not prove different keys: the plaintext "
          "may change, move, or be recompressed.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("a", type=Path, help="existing v1.48 FOT or official updater EXE")
    p.add_argument("b", type=Path, help="new v1.59 FOT, updater EXE, or ZIP")
    args = p.parse_args()
    try:
        a, b = load(args.a), load(args.b)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        print("ERROR:", e, file=sys.stderr)
        return 2
    print("=" * 100)
    print("MACHENIKE G3V2 — SAME-HWID CROSS-VERSION FOT CIPHERTEXT AUDIT")
    print("=" * 100)
    print("OFFLINE / read-only, no device access, firmware installation or execution")
    for label, item in (("A", a), ("B", b)):
        print(f"\n{label} source: {item['source']}")
        print(f"  FOT offset: 0x{item['off']:X}")
        print(f"  firmware version field: {item['ver']} "
              f"(display {item['ver'] // 100}.{item['ver'] % 100:02d})")
        print(f"  HWID: {item['hwid'].hex()}")
        print(f"  FOT size: {len(item['raw'])}")
        print(f"  FOT SHA256: {sha(item['raw'])}")
        print(f"  header equals directory mirror: True")
        print(f"  KEY record: {item['key'].hex()} "
              f"(known family constant: {item['key'] == KEY_ID})")
    print(f"\nSame exact G3V2 HWID: {a['hwid'] == b['hwid']}")
    print(f"Different firmware version fields: {a['ver'] != b['ver']}")
    for name in ("xcod", "xcod_data", "xres", "xres_data", "config"):
        compare_region(name.upper(), a[name], b[name])
    print("\nINTERPRETATION:")
    print("* Different version + XCOD hashes/lengths can confirm a distinct")
    print("  firmware image; a matching HWID is not proof of keystream reuse.")
    print("* Exactly matching ciphertext chunks are strong evidence of")
    print("  unchanged encrypted data with a compatible keystream; random")
    print("  XOR results alone do not recover executable plaintext.")
    print("* If C1=P1^K and C2=P2^K, C1^C2=P1^P2, NOT P1 or P2.")
    print("* Zero writes; no output binaries generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
