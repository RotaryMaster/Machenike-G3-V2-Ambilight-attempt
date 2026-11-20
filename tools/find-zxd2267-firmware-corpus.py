#!/usr/bin/env python3
"""
Find local ZhiXu / G3V2 firmware-family artifacts that may contain a second
same-hardware-ID image.

READ-ONLY. Recursively scans user-selected directories; no device I/O.

The high-value target is any artifact containing the exact G3V2 hardware ID:
    a1 ca 51 da 72 4c b6 10 36 58 c0 5d 7d 4e 22 67

A second updater/image with the same 16-byte ID but a different firmware
version is especially valuable because same-ID ZhiXu FOT images have been
observed externally to reuse a section-relative XOR keystream.

This script does NOT claim a file is firmware merely because a short string
matches. Exact HWID hits are ranked highest; textual ZXD2267/G3V2/FOT hits are
reported as weaker leads.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
from pathlib import Path

HWID = bytes.fromhex("a1ca51da724cb6103658c05d7d4e2267")
TEXT_NEEDLES = (
    b"ZXD2267",
    b"G3V2",
    b"G3 V2",
    b"FOT\x00",
    b"UsbUpdateAppX",
    b"DeviceUsb.dll",
)
DEFAULT_MAX = 128 * 1024 * 1024
SKIP_DIRS = {
    ".git", "node_modules", ".cache/mozilla", ".cache/google-chrome",
    ".local/share/Trash", "target", "build",
}


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_all(data: bytes, needle: bytes):
    out = []
    start = 0
    while True:
        pos = data.find(needle, start)
        if pos < 0:
            return out
        out.append(pos)
        start = pos + 1


def candidate_versions(data: bytes, hw_off: int):
    rows = []
    # Common family layouts:
    # 1) FOT header: HWID at +0x70, version at +0x80 -> +0x10 from HWID
    # 2) F1 info response: HWID[0:16], u16 version at +0x10
    for delta in (0x10, 0x12, -0x10, -0x20, 0x20):
        off = hw_off + delta
        if 0 <= off + 4 <= len(data):
            u16 = struct.unpack_from("<H", data, off)[0]
            u32 = struct.unpack_from("<I", data, off)[0]
            rows.append((delta, u16, u32))
    return rows


def should_skip(path: Path):
    text = str(path)
    return any(part in text for part in SKIP_DIRS)


def scan_file(path: Path, max_size: int):
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file() or st.st_size < 16 or st.st_size > max_size:
        return None
    try:
        data = path.read_bytes()
    except (OSError, PermissionError):
        return None

    hw = find_all(data, HWID)
    text_hits = {}
    if not hw:
        for needle in TEXT_NEEDLES:
            hits = find_all(data, needle)
            if hits:
                text_hits[needle] = hits
    if not hw and not text_hits:
        return None

    return data, hw, text_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--max-size", type=lambda x: int(x, 0), default=DEFAULT_MAX)
    args = ap.parse_args()

    print("=" * 112)
    print("G3V2 / ZXD2267 LOCAL FIRMWARE CORPUS SCAN")
    print("=" * 112)
    print("Exact HWID:", HWID.hex())
    print("Max file size:", args.max_size)
    print("Roots:")
    for root in args.roots:
        print(" ", root)

    exact = []
    weak = []
    scanned = 0

    for root in args.roots:
        root = root.expanduser()
        if not root.exists():
            print(f"[missing] {root}")
            continue

        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if should_skip(path):
                continue
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            scanned += 1
            result = scan_file(path, args.max_size)
            if result is None:
                continue
            data, hw, text_hits = result
            if hw:
                exact.append((path, data, hw))
            else:
                weak.append((path, data, text_hits))

    print(f"\nFiles considered: {scanned}")
    print(f"Exact HWID files: {len(exact)}")
    print(f"Weak textual leads: {len(weak)}")

    print("\n=== EXACT ...2267 HARDWARE-ID HITS ===")
    if not exact:
        print("(none)")
    for path, data, hw_hits in exact:
        print("\nFILE:", path)
        print(f"  size   : 0x{len(data):X} ({len(data)})")
        print("  sha256 :", hashlib.sha256(data).hexdigest())
        print("  HWID @ :", ", ".join(f"0x{x:X}" for x in hw_hits))
        print("  FOT magic count:", data.count(b"FOT\x00"))
        for off in hw_hits:
            print(f"  candidate fields around HWID @0x{off:X}:")
            for delta, u16, u32 in candidate_versions(data, off):
                print(
                    f"    rel {delta:+#x}: u16={u16} "
                    f"(v{u16//100}.{u16%100:02d} if decimal-hundredths) "
                    f"u32={u32}"
                )
            lo = max(0, off - 0x20)
            hi = min(len(data), off + 0x40)
            print("    bytes:", data[lo:hi].hex(" "))

    print("\n=== WEAKER ZXD2267 / G3V2 / FOT LEADS ===")
    for path, data, hits in weak:
        rendered = []
        for needle, offsets in hits.items():
            label = needle.decode("ascii", "replace").replace("\x00", "\\0")
            rendered.append(label + "@" + ",".join(f"0x{x:X}" for x in offsets[:8]))
        print(f"{path} | size=0x{len(data):X} | " + " ; ".join(rendered))

    print("\n=== CORPUS DECISION ===")
    hashes = {}
    for path, data, hw_hits in exact:
        digest = hashlib.sha256(data).hexdigest()
        hashes.setdefault(digest, []).append(str(path))
    print("Unique exact-HWID artifacts:", len(hashes))
    if len(hashes) >= 2:
        print("PROMISING: multiple distinct exact-HWID files exist locally.")
        print("Next step: classify their firmware versions and extract comparable")
        print("payload/container ranges before any XOR/keystream analysis.")
    elif len(hashes) == 1:
        print("Only one unique exact-HWID artifact was found.")
    else:
        print("No exact-HWID artifact found in the selected roots.")
    print("\nNo device I/O or file modification was performed.")


if __name__ == "__main__":
    main()
