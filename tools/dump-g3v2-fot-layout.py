#!/usr/bin/env python3
"""
G3V2 v1.48 FOT static layout / command-dispatch scout.

This is intentionally read-only. It does not execute firmware, open HID, or
issue USB/SCSI commands.

It can accept either:
  * the official updater EXE, from which the known FOT range is carved; or
  * an already-carved FOT image.

Goals:
  1. fingerprint the exact FOT bytes;
  2. map entropy / printable regions and XCOD/XRES markers;
  3. look for plausible ARM Cortex-M vector tables;
  4. if plaintext Thumb code is present, locate clusters of immediate
     comparisons against the already-known KeyLinker command family:
       B1 B2 B3 B4 B6 B7 BB / 37 3B
     This is only a scout: a raw byte or CMP hit is not protocol proof.

Known official updater FOT:
  file offset 0x1712F0
  length      0x5A400
"""

from __future__ import annotations

import argparse
import hashlib
import math
import struct
from pathlib import Path
from collections import Counter

FOT_OFF = 0x1712F0
FOT_LEN = 0x5A400
TARGETS = (0xB1, 0xB2, 0xB3, 0xB4, 0xB6, 0xB7, 0xBB, 0x37, 0x3B)


def entropy(buf: bytes) -> float:
    if not buf:
        return 0.0
    c = Counter(buf)
    n = len(buf)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def hx(buf: bytes) -> str:
    return " ".join(f"{b:02X}" for b in buf)


def carve(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    if len(raw) == FOT_LEN:
        return raw, "input already matches known FOT length"
    if len(raw) >= FOT_OFF + FOT_LEN:
        return raw[FOT_OFF:FOT_OFF + FOT_LEN], (
            f"carved updater range off=0x{FOT_OFF:X} len=0x{FOT_LEN:X}"
        )
    raise SystemExit(
        f"{path}: size 0x{len(raw):X} is neither a 0x{FOT_LEN:X}-byte FOT "
        f"nor large enough to contain the known updater range"
    )


def ascii_runs(data: bytes, min_len: int = 5):
    out = []
    start = None
    for i, b in enumerate(data):
        ok = 0x20 <= b <= 0x7E
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= min_len:
                out.append((start, data[start:i].decode("ascii", "replace")))
            start = None
    if start is not None and len(data) - start >= min_len:
        out.append((start, data[start:].decode("ascii", "replace")))
    return out


def vector_candidates(data: bytes):
    """
    Conservative Cortex-M vector-table heuristic:
      word0 looks like SRAM stack pointer
      word1 is Thumb (bit0=1)
      several early handler entries also have bit0=1 or are zero
    We do not assume a fixed flash base.
    """
    out = []
    limit = min(len(data) - 0x40, 0x20000)
    for off in range(0, max(0, limit), 4):
        words = struct.unpack_from("<16I", data, off)
        sp, reset = words[0], words[1]
        if not (0x20000000 <= sp <= 0x3FFFFFFF):
            continue
        if reset == 0 or not (reset & 1):
            continue
        plausible = sum(1 for x in words[1:] if x == 0 or (x & 1))
        if plausible < 10:
            continue
        out.append((off, sp, reset, plausible, words))
    return out


def thumb_cmp_hits(data: bytes):
    """
    Thumb CMP Rn,#imm8 is 0x28..0x2F in the high byte of the little-endian
    halfword, i.e. bytes [imm, 0x28+Rn].
    """
    hits = []
    target_set = set(TARGETS)
    for off in range(0, len(data) - 1, 2):
        imm, op = data[off], data[off + 1]
        if imm in target_set and 0x28 <= op <= 0x2F:
            hits.append((off, imm, op - 0x28))
    return hits


def cluster_hits(hits, window=0x180, min_distinct=3):
    groups = []
    for i, h in enumerate(hits):
        off = h[0]
        g = [x for x in hits[i:] if x[0] - off <= window]
        distinct = sorted({x[1] for x in g})
        if len(distinct) >= min_distinct:
            groups.append((off, g[-1][0], distinct, g))
    # collapse overlapping groups
    collapsed = []
    for g in groups:
        if collapsed and g[0] <= collapsed[-1][1]:
            a = collapsed[-1]
            merged_hits = a[3] + [x for x in g[3] if x not in a[3]]
            collapsed[-1] = (
                a[0],
                max(a[1], g[1]),
                sorted(set(a[2]) | set(g[2])),
                merged_hits,
            )
        else:
            collapsed.append(g)
    return collapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="official updater EXE or carved FOT")
    ap.add_argument("--block", type=lambda x: int(x, 0), default=0x1000)
    args = ap.parse_args()

    path = Path(args.image)
    if not path.is_file():
        raise SystemExit(f"missing: {path}")

    data, source = carve(path)
    print("=" * 104)
    print("G3V2 FOT STATIC LAYOUT / COMMAND-DISPATCH SCOUT")
    print("=" * 104)
    print("Input :", path)
    print("Source:", source)
    print(f"Size  : 0x{len(data):X} ({len(data)})")
    print("SHA256:", hashlib.sha256(data).hexdigest())
    print(f"Entropy total: {entropy(data):.4f} bits/byte")

    print("\n=== KNOWN / PRINTABLE MARKERS ===")
    for marker in (b"XCOD", b"XRES", b"ZXPC", b"G3V2", b"MACHENIKE"):
        pos = [i for i in range(len(data)) if data.startswith(marker, i)]
        print(f"{marker.decode(errors='replace'):<10}: " +
              (", ".join(f"0x{x:X}" for x in pos[:20]) if pos else "(none)"))

    runs = ascii_runs(data)
    print(f"\nPrintable ASCII runs >=5: {len(runs)}")
    for off, s in runs[:80]:
        print(f"0x{off:06X}: {s[:120]}")
    if len(runs) > 80:
        print(f"... {len(runs)-80} more omitted")

    print("\n=== ENTROPY MAP ===")
    block = args.block
    rows = []
    for off in range(0, len(data), block):
        b = data[off:off+block]
        rows.append((off, len(b), entropy(b)))
    for off, n, e in rows:
        flag = ""
        if e >= 7.7:
            flag = " high"
        elif e <= 5.5:
            flag = " low"
        print(f"0x{off:06X}-0x{off+n-1:06X}  H={e:0.4f}{flag}")

    print("\n=== CORTEX-M VECTOR TABLE HEURISTIC ===")
    vc = vector_candidates(data)
    if not vc:
        print("No conservative Cortex-M vector-table candidate found in first 0x20000 bytes.")
    else:
        for off, sp, reset, plausible, words in vc[:20]:
            print(
                f"off=0x{off:06X} SP=0x{sp:08X} reset=0x{reset:08X} "
                f"plausible_entries={plausible}/15"
            )
            print("  ", " ".join(f"{x:08X}" for x in words[:8]))

    print("\n=== THUMB CMP-IMMEDIATE SCOUT ===")
    hits = thumb_cmp_hits(data)
    print(f"Candidate CMP hits for target immediates: {len(hits)}")
    per = Counter(x[1] for x in hits)
    for t in TARGETS:
        print(f"  0x{t:02X}: {per[t]}")

    clusters = cluster_hits(hits)
    print(f"Clusters with >=3 distinct target immediates within 0x180 bytes: {len(clusters)}")
    for a, b, distinct, gh in clusters[:40]:
        lo = max(0, a - 0x40)
        hi = min(len(data), b + 0x42)
        print(
            f"\ncluster 0x{a:06X}-0x{b:06X} "
            f"targets={' '.join(f'{x:02X}' for x in distinct)}"
        )
        print("hits:", ", ".join(
            f"0x{o:X}:cmp r{r},#0x{imm:02X}" for o, imm, r in gh[:40]
        ))
        print(f"bytes 0x{lo:06X}-0x{hi-1:06X}:")
        chunk = data[lo:hi]
        for j in range(0, len(chunk), 16):
            print(f"  {lo+j:06X}: {hx(chunk[j:j+16])}")

    print("\n=== INTERPRETATION GUARDRAILS ===")
    print("* A raw immediate/CMP hit is not proof of a protocol handler.")
    print("* Dense target clusters are useful only if the region is executable plaintext.")
    print("* If entropy is uniformly high and no vector/code structure appears, the FOT may be")
    print("  encrypted/compressed/packaged and host-side static inspection cannot answer NVM")
    print("  persistence without first decoding that firmware container.")
    print("* No code was executed and no device I/O was performed.")


if __name__ == "__main__":
    main()
