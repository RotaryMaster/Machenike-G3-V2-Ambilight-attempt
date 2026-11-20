#!/usr/bin/env python3
"""
Read-only comparator for two complete G3V2 / ZXD2267 FOT images.

Purpose:
- determine where two same-HWID, same-version FOT images differ;
- measure whether XCOD/XRES differences are sparse/structured (compatible with
  same-keystream reuse + plaintext changes) or random-looking (different
  keystream / transformed image / unrelated payload);
- never execute any updater and never access the controller.

This tool reports measurements only. It does not attempt decryption.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import struct
from pathlib import Path
from collections import Counter

MAGIC = b"FOT\0"
TARGET_HWID = bytes.fromhex("a1ca51da724cb6103658c05d7d4e2267")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    n = len(b)
    c = Counter(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def chi_square_uniform(b: bytes) -> float:
    if not b:
        return 0.0
    n = len(b)
    exp = n / 256.0
    c = Counter(b)
    return sum(((c.get(i, 0) - exp) ** 2) / exp for i in range(256))


def longest_equal_run(a: bytes, b: bytes) -> tuple[int, int]:
    best_len = cur = 0
    best_end = 0
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            cur += 1
            if cur > best_len:
                best_len = cur
                best_end = i + 1
        else:
            cur = 0
    return best_end - best_len, best_len


def diff_bounds(a: bytes, b: bytes):
    idx = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if not idx:
        return None
    return idx[0], idx[-1]


def nonzero_ranges(x: bytes, max_ranges: int = 24):
    out = []
    i = 0
    while i < len(x):
        if x[i] == 0:
            i += 1
            continue
        s = i
        while i < len(x) and x[i] != 0:
            i += 1
        out.append((s, i))
        if len(out) >= max_ranges:
            break
    return out


def repeated_aligned_blocks(b: bytes, block: int = 16) -> tuple[int, int]:
    blocks = [b[i:i+block] for i in range(0, len(b) - block + 1, block)]
    c = Counter(blocks)
    vals = sum(1 for v in c.values() if v > 1)
    inst = sum(v for v in c.values() if v > 1)
    return vals, inst


def parse_fot(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:4] != MAGIC:
        raise SystemExit(f"{path}: expected complete FOT starting at file offset 0")
    if len(raw) < 0x200:
        raise SystemExit(f"{path}: too short")

    logical = struct.unpack_from("<I", raw, 4)[0] + 8
    if logical > len(raw):
        raise SystemExit(f"{path}: logical size 0x{logical:X} exceeds file length 0x{len(raw):X}")

    blob = raw[:logical]
    hwid = blob[0x70:0x80]
    version = struct.unpack_from("<I", blob, 0x80)[0]
    data_start = struct.unpack_from("<I", blob, 0xB8)[0]
    data_len = struct.unpack_from("<I", blob, 0xBC)[0]
    directory = struct.unpack_from("<I", blob, 0xC0)[0]
    config = struct.unpack_from("<I", blob, 0xC4)[0]

    return {
        "path": path,
        "raw": blob,
        "logical": logical,
        "sha256": sha256(blob),
        "hwid": hwid,
        "version": version,
        "data_start": data_start,
        "data_len": data_len,
        "directory": directory,
        "config": config,
    }


def infer_regions(f: dict):
    # For the known G3V2 layout, XCOD begins at DATA start and XRES occupies
    # the final 0xC00 immediately before the directory copy. We still derive
    # all outer boundaries from the FOT header.
    ds = f["data_start"]
    de = ds + f["data_len"]
    dr = f["directory"]
    cf = f["config"]
    if de != dr:
        raise SystemExit(f"{f['path']}: DATA end 0x{de:X} != directory 0x{dr:X}")
    if not (ds == 0x200 and dr >= 0xE00 and cf >= dr + 0x200):
        raise SystemExit(f"{f['path']}: unexpected G3V2 layout")
    xres_start = dr - 0xC00
    tail_start = cf + 0x400
    return [
        ("header", 0x0000, ds),
        ("XCOD", ds, xres_start),
        ("XRES", xres_start, dr),
        ("directory-copy", dr, cf),
        ("config", cf, min(cf + 0x400, f["logical"])),
        ("tail", min(tail_start, f["logical"]), f["logical"]),
    ]


def section_report(name: str, off: int, a: bytes, b: bytes):
    n = min(len(a), len(b))
    a = a[:n]
    b = b[:n]
    x = bytes(i ^ j for i, j in zip(a, b))
    eq = sum(i == j for i, j in zip(a, b))
    nz = n - eq
    pct = (eq / n * 100.0) if n else 100.0
    lo = longest_equal_run(a, b)
    bounds = diff_bounds(a, b)
    rv, ri = repeated_aligned_blocks(x, 16)

    print(f"\n[{name}] off=0x{off:06X} len=0x{n:X} ({n})")
    print(f"  A SHA256        : {sha256(a)}")
    print(f"  B SHA256        : {sha256(b)}")
    print(f"  identical bytes : {eq}/{n} ({pct:.6f}%)")
    print(f"  differing bytes : {nz}/{n}")
    if bounds is None:
        print("  diff bounds     : none (regions byte-identical)")
    else:
        print(f"  diff bounds     : +0x{bounds[0]:X} .. +0x{bounds[1]:X}")
    print(f"  longest eq run  : +0x{lo[0]:X}, len=0x{lo[1]:X} ({lo[1]})")
    print(f"  XOR entropy     : {entropy(x):.6f} bits/byte")
    print(f"  XOR chi-square  : {chi_square_uniform(x):.2f}")
    print(f"  XOR zero bytes  : {x.count(0)}/{n} ({(x.count(0)/n*100 if n else 0):.6f}%)")
    print(f"  XOR repeated 16B: values={rv} instances={ri}")
    ranges = nonzero_ranges(x)
    if ranges:
        print("  first nonzero XOR ranges:")
        for s, e in ranges:
            print(f"    +0x{s:X}-+0x{e-1:X} len=0x{e-s:X}")
        if len(ranges) >= 24:
            print("    ... truncated")
    else:
        print("  first nonzero XOR ranges: none")

    # Section-header-level detail for encrypted sections.
    if name in {"XCOD", "XRES"} and n >= 16:
        print("  A first 16B     :", a[:16].hex(" "))
        print("  B first 16B     :", b[:16].hex(" "))
        print("  XOR first 16B   :", x[:16].hex(" "))

    return {
        "name": name,
        "len": n,
        "equal": eq,
        "different": nz,
        "equal_pct": pct,
        "xor_entropy": entropy(x),
        "xor_zero_pct": (x.count(0) / n * 100.0) if n else 0.0,
    }


def classify(stats: list[dict]):
    d = {x["name"]: x for x in stats}
    xc = d.get("XCOD")
    xr = d.get("XRES")

    print("\n" + "=" * 120)
    print("INTERPRETATION AID")
    print("=" * 120)

    if xc and xc["equal_pct"] == 100.0 and xr and xr["equal_pct"] == 100.0:
        print("* XCOD and XRES are byte-identical. The distinct whole-FOT hash comes from metadata/config/tail, not firmware payload.")
        print("* This pair does NOT provide a cross-ciphertext plaintext-difference test.")
        return

    if xc:
        if xc["equal_pct"] > 90.0:
            print("* XCOD is overwhelmingly identical. Differences are localized/sparse, compatible with same ciphertext stream and a small payload change.")
            print("* This is the most promising case for same-keystream differential analysis.")
        elif xc["xor_entropy"] > 7.8 and xc["xor_zero_pct"] < 2.0:
            print("* XCOD XOR is near-random with very few equal bytes.")
            print("* That is NOT the signature expected from two mostly-similar plaintext builds encrypted with the same reused keystream.")
            print("* Candidate explanations: different keystream/nonce, substantially different plaintext, transformed/corrupted source, or different encryption context.")
        else:
            print("* XCOD differences are structured but not trivially sparse; inspect ranges and XOR statistics before drawing a crypto conclusion.")

    if xr:
        if xr["equal_pct"] > 90.0:
            print("* XRES is overwhelmingly identical.")
        elif xr["xor_entropy"] > 7.6 and xr["xor_zero_pct"] < 2.0:
            print("* XRES XOR is also random-looking; this supports a changed keystream/encryption context more than a tiny resource edit.")
        else:
            print("* XRES differences retain structure; useful for follow-up differential analysis.")

    print("* Same HWID + same version + different SHA256 is a confirmed fact; provenance still matters before calling either image a device dump.")
    print("* No decryption claim is made by this tool.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    args = ap.parse_args()

    A = parse_fot(args.a)
    B = parse_fot(args.b)

    print("=" * 120)
    print("G3V2 SAME-HWID FOT DIFFERENTIAL ANALYSIS — READ ONLY")
    print("=" * 120)
    print("Device I/O : none")
    print("Execution  : none")
    for label, f in (("A", A), ("B", B)):
        print(f"{label}: {f['path']}")
        print(f"   SHA256={f['sha256']}")
        print(f"   size=0x{f['logical']:X} HWID={f['hwid'].hex()} version={f['version']}")
        print(f"   DATA=0x{f['data_start']:X}+0x{f['data_len']:X} DIR=0x{f['directory']:X} CONFIG=0x{f['config']:X}")

    print("\n=== IDENTITY CHECK ===")
    print("same size    :", A["logical"] == B["logical"])
    print("same HWID    :", A["hwid"] == B["hwid"], A["hwid"].hex())
    print("target HWID  :", A["hwid"] == TARGET_HWID and B["hwid"] == TARGET_HWID)
    print("same version :", A["version"] == B["version"], A["version"], B["version"])
    print("same FOT SHA :", A["sha256"] == B["sha256"])

    if A["logical"] != B["logical"]:
        raise SystemExit("Cannot region-compare: logical sizes differ")
    if A["hwid"] != B["hwid"]:
        raise SystemExit("Cannot same-HWID compare: HWIDs differ")

    regA = infer_regions(A)
    regB = infer_regions(B)
    if [(n,s,e) for n,s,e in regA] != [(n,s,e) for n,s,e in regB]:
        raise SystemExit("Region layouts differ")

    stats = []
    for name, s, e in regA:
        stats.append(section_report(name, s, A["raw"][s:e], B["raw"][s:e]))

    print("\n=== WHOLE FOT ===")
    section_report("whole-FOT", 0, A["raw"], B["raw"])

    # Extra invariant checks.
    print("\n=== DIRECTORY / HEADER INVARIANTS ===")
    for label, f in (("A", A), ("B", B)):
        head = f["raw"][:0x200]
        dcopy = f["raw"][f["directory"]:f["directory"]+0x200]
        print(f"{label}: header == directory-copy:", head == dcopy)
        print(f"{label}: header SHA256            :", sha256(head))
        print(f"{label}: directory-copy SHA256    :", sha256(dcopy))

    classify(stats)


if __name__ == "__main__":
    main()
