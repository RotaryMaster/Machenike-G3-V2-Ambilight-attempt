#!/usr/bin/env python3
"""
Static audit of the G3V2 firmware-updater readback surface.

READ-ONLY: parses binaries / objdump output only. It never opens the gamepad,
never enters update mode and never issues SCSI/HID/USB commands.

Usage:
  python tools/dump-deviceusb-readback-surface.py \
      firmware-analysis/g3v2-v1.48/ZXD2267_JRH_G3V2_MACHENIKE_v1.48_20250815.exe \
      firmware-analysis/g3v2-v1.48/DeviceUsb_extracted.dll

Goals:
  * fingerprint updater and DeviceUsb.dll;
  * inventory DeviceUsb exports/imports;
  * recover the exported deviceUsb_Request RVA when possible;
  * show DeviceIoControl / IOCTL_SCSI_PASS_THROUGH_DIRECT contexts;
  * show updater contexts involving F0/F1/F2 and DeviceUsb function names;
  * detect the vendor Downloader PDB/path family;
  * distinguish DATA_IN read sites from firmware DATA_OUT sites statically.

This tool intentionally does not guess that an F0/F1/F2 immediate is a command
unless it is supported by nearby call/control-flow context. The printed
contexts are evidence for manual review.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str]) -> str:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    return p.stdout


def strings_ascii_utf16(data: bytes, min_len: int = 5):
    out: list[tuple[int, str, str]] = []

    # ASCII
    start = None
    for i, b in enumerate(data):
        if 0x20 <= b <= 0x7E:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_len:
                out.append((start, "ascii", data[start:i].decode("ascii", "replace")))
            start = None
    if start is not None and len(data) - start >= min_len:
        out.append((start, "ascii", data[start:].decode("ascii", "replace")))

    # Simple UTF-16LE printable runs.
    i = 0
    while i + 1 < len(data):
        if 0x20 <= data[i] <= 0x7E and data[i + 1] == 0:
            start = i
            chars = []
            while i + 1 < len(data) and 0x20 <= data[i] <= 0x7E and data[i + 1] == 0:
                chars.append(chr(data[i]))
                i += 2
            if len(chars) >= min_len:
                out.append((start, "utf16le", "".join(chars)))
        else:
            i += 1
    return sorted(out)


def relevant_strings(path: Path):
    keys = (
        "deviceusb", "physicaldrive", "cdrom", "scsi", "updater",
        "downloader", ".pdb", "firmware", "upgrade", "fot", "flash",
        "request", "getinfo", "openbydrv",
    )
    rows = []
    for off, kind, s in strings_ascii_utf16(path.read_bytes()):
        if any(k in s.lower() for k in keys):
            rows.append((off, kind, s))
    return rows


def objdump_contexts(disasm: str, patterns: list[tuple[str, re.Pattern]], radius: int = 10):
    lines = disasm.splitlines()
    hits = []
    seen = set()
    for i, line in enumerate(lines):
        for label, rx in patterns:
            if rx.search(line):
                key = (i, label)
                if key in seen:
                    continue
                seen.add(key)
                lo, hi = max(0, i - radius), min(len(lines), i + radius + 1)
                hits.append((label, i, lines[lo:hi]))
    return hits


def print_file_header(label: str, path: Path):
    print("\n" + "=" * 112)
    print(label)
    print("=" * 112)
    print("Path  :", path)
    print("Size  :", path.stat().st_size, f"(0x{path.stat().st_size:X})")
    print("SHA256:", sha256(path))


def print_contexts(title: str, contexts, limit=80):
    print("\n---", title, "---")
    if not contexts:
        print("(none)")
        return
    for n, (label, idx, lines) in enumerate(contexts[:limit], 1):
        print(f"\n[{n}] match={label} disasm_line={idx+1}")
        for x in lines:
            print(x)
    if len(contexts) > limit:
        print(f"\n... {len(contexts)-limit} more contexts omitted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("updater")
    ap.add_argument("deviceusb")
    ap.add_argument("--radius", type=int, default=12)
    args = ap.parse_args()

    updater = Path(args.updater)
    dll = Path(args.deviceusb)
    for p in (updater, dll):
        if not p.is_file():
            raise SystemExit(f"missing: {p}")

    objdump = shutil.which("objdump")
    if not objdump:
        raise SystemExit("objdump not found")

    print("=" * 112)
    print("G3V2 DEVICEUSB / FIRMWARE READBACK SURFACE — STATIC AUDIT")
    print("=" * 112)
    print("Policy: binary parsing/disassembly only; NO device I/O")

    for label, p in (("UPDATER", updater), ("DEVICEUSB.DLL", dll)):
        print_file_header(label, p)

        hdr = run([objdump, "-f", str(p)])
        print("\n[objdump -f]")
        print(hdr.rstrip())

        pe = run([objdump, "-p", str(p)])
        print("\n[imports/exports: filtered]")
        keep = re.compile(
            r"(?i)(deviceUsb_|DeviceIoControl|CreateFile|CloseHandle|"
            r"SCSI|GetProcAddress|LoadLibrary|Export Address Table|"
            r"Ordinal Base|DLL Name|file format)"
        )
        shown = [ln for ln in pe.splitlines() if keep.search(ln)]
        print("\n".join(shown) if shown else "(no filtered rows)")

        print("\n[relevant strings]")
        rows = relevant_strings(p)
        if not rows:
            print("(none)")
        else:
            for off, kind, s in rows[:250]:
                print(f"0x{off:08X} {kind:7s} {s}")
            if len(rows) > 250:
                print(f"... {len(rows)-250} more omitted")

    dis_up = run([objdump, "-d", "-M", "intel", str(updater)])
    dis_dll = run([objdump, "-d", "-M", "intel", str(dll)])

    dll_patterns = [
        ("DeviceIoControl", re.compile(r"(?i)DeviceIoControl|deviceiocontrol")),
        ("IOCTL_SCSI_PASS_THROUGH_DIRECT 0x4D014", re.compile(r"(?i)\b4d014\b|\b0x4d014\b")),
        ("DATA_IN literal 1", re.compile(r"(?i)\b(?:push|mov)\b.*(?:0x1\b|\b1\b)")),
        ("CDB length 10", re.compile(r"(?i)\b(?:push|mov)\b.*(?:0xa\b|\b10\b)")),
    ]
    print_contexts(
        "DEVICEUSB: DeviceIoControl / SCSI wrapper contexts",
        objdump_contexts(dis_dll, dll_patterns, args.radius),
        limit=60,
    )

    updater_patterns = [
        ("F0 immediate", re.compile(r"(?i)\b0xf0\b")),
        ("F1 immediate", re.compile(r"(?i)\b0xf1\b")),
        ("F2 immediate", re.compile(r"(?i)\b0xf2\b")),
        ("512-byte transfer", re.compile(r"(?i)\b0x200\b")),
        ("20-byte transfer", re.compile(r"(?i)\b0x14\b")),
        ("GetProcAddress", re.compile(r"(?i)GetProcAddress|getprocaddress")),
    ]
    print_contexts(
        "UPDATER: F0/F1/F2 / transfer-size / dynamic-resolution contexts",
        objdump_contexts(dis_up, updater_patterns, args.radius),
        limit=120,
    )

    print("\n" + "=" * 112)
    print("INTERPRETATION CHECKLIST")
    print("=" * 112)
    print("1. Confirm DeviceUsb.dll is the same Downloader family (PDB/path/exports).")
    print("2. In deviceUsb_Request, confirm SCSI_PASS_THROUGH_DIRECT and whether DataIn")
    print("   comes from a caller argument instead of being hard-coded.")
    print("3. For each updater call site, record: opcode, DataIn, buffer length, CDB.")
    print("4. A 20-byte F1/F2 read is identity/status only unless code proves otherwise.")
    print("5. A firmware dump requires a read-direction command with address/offset and")
    print("   a scalable transfer length. Do not infer this from F1 existing.")
    print("6. No live SCSI probe should be attempted until the static call table is closed.")
    print("\nNo HID/USB/SCSI access was performed.")


if __name__ == "__main__":
    main()
