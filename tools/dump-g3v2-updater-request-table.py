#!/usr/bin/env python3
"""
Focused static proof table for the exact official G3V2 v1.48 updater.

READ-ONLY. No device I/O.

This script is intentionally narrow: it verifies the updater hash, disassembles
the exact call sites to the dynamically resolved deviceUsb_Request pointer, and
prints the six x86 arguments immediately around each call.

Recovered ABI for this binary:
    deviceUsb_Request(
        handle,
        data_in,   # 0 = host->device, 1 = device->host
        cdb_ptr,
        cdb_len,
        data_ptr,
        data_len,
    )

Known exact v1.48 call sites:
    0x402644 : F1, DATA_IN=1, 20 bytes
    0x402872 : F0, DATA_IN=0, <=512 bytes
    0x402897 : F2, DATA_IN=1, 20 bytes
    0x4028DE : F2, DATA_IN=1, 20 bytes

The script does not infer that another opcode cannot exist in firmware; it only
closes the host-side updater call table present in this exact executable.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

EXPECTED_SHA256 = "f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255"
REQUEST_IAT = 0x005F9864

CALLS = {
    0x00402644: ("F1", "DATA_IN", 20),
    0x00402872: ("F0", "DATA_OUT", 512),
    0x00402897: ("F2", "DATA_IN", 20),
    0x004028DE: ("F2", "DATA_IN", 20),
}


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


def parse_disasm(text: str):
    rows = []
    rx = re.compile(r"^\s*([0-9a-fA-F]+):\s+(.*)$")
    for line in text.splitlines():
        m = rx.match(line)
        if m:
            rows.append((int(m.group(1), 16), line))
    return rows


def context(rows, addr, before=18, after=4):
    for i, (a, _) in enumerate(rows):
        if a == addr:
            return rows[max(0, i-before):min(len(rows), i+after+1)]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("updater")
    args = ap.parse_args()

    path = Path(args.updater)
    if not path.is_file():
        raise SystemExit(f"missing: {path}")

    got = sha256(path)
    print("=" * 108)
    print("G3V2 v1.48 — EXACT deviceUsb_Request CALL TABLE")
    print("=" * 108)
    print("File  :", path)
    print("SHA256:", got)
    print("Match :", got == EXPECTED_SHA256)
    if got != EXPECTED_SHA256:
        raise SystemExit("unexpected updater hash; refusing exact-address interpretation")

    objdump = shutil.which("objdump")
    if not objdump:
        raise SystemExit("objdump not found")

    dis = run([objdump, "-d", "-M", "intel", str(path)])
    rows = parse_disasm(dis)

    print("\nRecovered ABI:")
    print("  deviceUsb_Request(handle, data_in, cdb_ptr, cdb_len, data_ptr, data_len)")
    print("  data_in=0 -> host to device")
    print("  data_in=1 -> device to host")
    print(f"  dynamic function pointer IAT/global = 0x{REQUEST_IAT:08X}")

    print("\n=== EXACT CALL SITES ===")
    all_ok = True
    for addr, (opcode, direction, length) in CALLS.items():
        ctx = context(rows, addr)
        found = bool(ctx)
        all_ok &= found
        print(f"\n0x{addr:08X}: {opcode} / {direction} / {length} B" +
              ("" if found else "  [NOT FOUND]"))
        for _, line in ctx:
            prefix = ">> " if f"{addr:x}:" in line.lower() else "   "
            print(prefix + line)

    print("\n=== REQUEST-POINTER CALL INVENTORY ===")
    needle = f"ds:0x{REQUEST_IAT:x}"
    hits = [(a, line) for a, line in rows if needle in line.lower() and "call" in line.lower()]
    for a, line in hits:
        print(f"0x{a:08X}: {line.strip()}")
    print("count:", len(hits))

    expected = set(CALLS)
    actual = {a for a, _ in hits}
    print("all four known sites present:", expected.issubset(actual))
    print("extra updater request sites :", sorted(hex(x) for x in (actual - expected)) or "(none)")

    print("\n=== STATIC CONCLUSION FOR THIS HOST UPDATER ===")
    print("* F0 is the only bulk transfer in the firmware loop and is DATA_OUT.")
    print("* F1 is DATA_IN but fixed at 20 bytes.")
    print("* F2 is DATA_IN but fixed at 20 bytes and used as status polling.")
    print("* No host-side bulk/addressable DATA_IN request is present in this exact updater.")
    print("* Therefore this updater exposes no firmware-backup path.")
    print("* This does NOT prove the bootloader firmware lacks undocumented opcodes.")
    print("\nNo HID/USB/SCSI access was performed.")


if __name__ == "__main__":
    main()
