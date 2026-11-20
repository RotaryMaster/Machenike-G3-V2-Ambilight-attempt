#!/usr/bin/env python3
"""
Read-only local FOT corpus scanner for the Machenike G3 V2 / ZXD2267.

Scans files/directories for complete FOT containers and reports hardware ID,
version, offsets and hashes. It never executes an updater and never accesses
the controller.

Useful goal:
find a second DISTINCT firmware image with the exact G3V2 hardware ID
  a1ca51da724cb6103658c05d7d4e2267
because same-hardware-ID version pairs can test keystream reuse.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
from pathlib import Path

MAGIC = b"FOT\0"
BLOCK = 512
TARGET_HWID = bytes.fromhex("a1ca51da724cb6103658c05d7d4e2267")
DEFAULT_MAX = 256 * 1024 * 1024


def align_up(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_files(paths: list[Path]):
    seen: set[tuple[int, int]] = set()
    for root in paths:
        try:
            if root.is_file():
                st = root.stat()
                key = (st.st_dev, st.st_ino)
                if key not in seen:
                    seen.add(key)
                    yield root
                continue
            if not root.is_dir():
                continue
            for base, dirs, files in os.walk(root):
                # Skip noisy VCS/cache directories only; scan everything else.
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".cache"}]
                for name in files:
                    p = Path(base) / name
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield p
        except OSError:
            continue


def parse_fots(raw: bytes):
    start = 0
    while True:
        off = raw.find(MAGIC, start)
        if off < 0:
            return
        start = off + 1
        if off + 0xC8 > len(raw):
            continue
        logical = struct.unpack_from("<I", raw, off + 4)[0] + 8
        if logical < 0x200 or logical > 64 * 1024 * 1024:
            continue
        stored = align_up(logical, BLOCK)
        if off + stored > len(raw):
            continue
        blob = raw[off:off + stored]
        hwid = blob[0x70:0x80]
        version = struct.unpack_from("<I", blob, 0x80)[0]
        data_start = struct.unpack_from("<I", blob, 0xB8)[0]
        data_len = struct.unpack_from("<I", blob, 0xBC)[0]
        directory = struct.unpack_from("<I", blob, 0xC0)[0]
        config = struct.unpack_from("<I", blob, 0xC4)[0]
        # Require a minimally self-consistent FOT layout to suppress random FOT strings.
        if not (0x200 <= data_start < stored):
            continue
        if data_start + data_len > stored:
            continue
        yield {
            "off": off,
            "logical": logical,
            "stored": stored,
            "hwid": hwid,
            "version": version,
            "data_start": data_start,
            "data_len": data_len,
            "directory": directory,
            "config": config,
            "sha256": sha256(blob),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument(
        "--max-file-size",
        type=lambda x: int(x, 0),
        default=DEFAULT_MAX,
        help="skip files larger than this many bytes (default 256 MiB)",
    )
    args = ap.parse_args()

    print("=" * 118)
    print("G3V2 / ZXD2267 LOCAL FOT CORPUS SCAN — READ ONLY")
    print("=" * 118)
    print("Target HWID :", TARGET_HWID.hex())
    print("Device I/O  : none")
    print("Execution   : none")
    print("Paths       :", ", ".join(str(x) for x in args.paths))
    print()

    scanned = 0
    skipped_large = 0
    found = []
    errors = 0

    for p in iter_files(args.paths):
        try:
            size = p.stat().st_size
            if size < 0x200:
                continue
            if size > args.max_file_size:
                skipped_large += 1
                continue
            raw = p.read_bytes()
            scanned += 1
        except (OSError, PermissionError):
            errors += 1
            continue

        for f in parse_fots(raw):
            f["path"] = p
            found.append(f)
            mark = "<<< TARGET G3V2" if f["hwid"] == TARGET_HWID else ""
            print(
                f"{p}\n"
                f"  FOT file+0x{f['off']:X} stored=0x{f['stored']:X} "
                f"logical=0x{f['logical']:X}\n"
                f"  HWID={f['hwid'].hex()} version={f['version']} "
                f"SHA256={f['sha256']} {mark}\n"
                f"  DATA=0x{f['data_start']:X}+0x{f['data_len']:X} "
                f"DIR=0x{f['directory']:X} CONFIG=0x{f['config']:X}"
            )

    target = [x for x in found if x["hwid"] == TARGET_HWID]
    distinct = {}
    for x in target:
        distinct.setdefault(x["sha256"], x)

    print("\n=== SUMMARY ===")
    print("Files scanned          :", scanned)
    print("Files skipped > limit  :", skipped_large)
    print("Read errors            :", errors)
    print("All complete FOTs      :", len(found))
    print("G3V2 HWID FOT entries  :", len(target))
    print("Distinct G3V2 FOT SHA  :", len(distinct))

    if not target:
        print("\nNo local FOT with the exact G3V2 hardware ID was found.")
    elif len(distinct) == 1:
        one = next(iter(distinct.values()))
        duplicates = [x for x in target if x["sha256"] == one["sha256"]]
        print("\nOnly one DISTINCT G3V2 firmware image was found.")
        print("Version :", one["version"])
        print("SHA256  :", one["sha256"])
        print("Copies  :", len(duplicates))
        for x in duplicates:
            print("  -", x["path"], f"(FOT file+0x{x['off']:X})")
    else:
        print("\n*** MULTIPLE DISTINCT SAME-HWID G3V2 FIRMWARE IMAGES FOUND ***")
        for x in sorted(distinct.values(), key=lambda y: y["version"]):
            print(
                f"version={x['version']} sha256={x['sha256']} "
                f"path={x['path']} FOT+0x{x['off']:X}"
            )
        print("\nThese are candidates for same-HWID cross-version keystream analysis.")


if __name__ == "__main__":
    main()
