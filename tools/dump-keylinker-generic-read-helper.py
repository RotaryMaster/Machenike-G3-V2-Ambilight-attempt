#!/usr/bin/env python3
"""
Static audit of KeyLinkerlib generic read helper 0x180001BE0 and its immediate
helper neighborhood.

Purpose:
- resolve whether readDeviceBeforeKeyChangeList(arg=3) and
  readDeviceSupportTurboList(arg=4) advance the same KeyLinker state/rand
  stream as the explicit six-byte read builders;
- print only the bounded 0x180001BE0..0x180001F40 region;
- annotate calls/references to known KeyLinker helpers.

No DLL code is executed. No device access is performed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

KNOWN = {
    0x180001080: "KeyLinkerEncode",
    0x1800011C0: "KeyLinkerDecode",
    0x180001400: "Concat",
    0x1800014C0: "KeyLinkerState",
    0x180001BE0: "GenericReadHelper",
    0x180001C90: "HeaderBuilder",
    0x18000B150: "rand_IAT",
}

ADDR_RE = re.compile(r"^\s*([0-9a-fA-F]+):")
CALL_RE = re.compile(r"\bcall\b", re.I)


def annotate(line: str) -> str:
    low = line.lower()
    hits = []
    for addr, name in KNOWN.items():
        hx = f"{addr:x}"
        if hx in low or f"0x{hx}" in low:
            hits.append(name)
    return line + ("    <== " + ", ".join(hits) if hits else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("--objdump", default=shutil.which("objdump") or "/usr/bin/objdump")
    args = ap.parse_args()

    dll = Path(args.dll)
    if not dll.is_file():
        raise SystemExit(f"DLL not found: {dll}")

    cmd = [
        args.objdump,
        "-d",
        "-Mintel",
        "--start-address=0x180001BE0",
        "--stop-address=0x180001F40",
        str(dll),
    ]
    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    insns = [ln.rstrip() for ln in cp.stdout.splitlines() if ADDR_RE.match(ln)]

    print("=" * 112)
    print("KEYLINKER GENERIC READ HELPER 0x180001BE0 — STATIC AUDIT")
    print("=" * 112)
    print("DLL:", dll)
    print()

    # Keep all instructions in this small bounded region; it contains the
    # helper plus the nearby header-builder implementation/callees.
    for line in insns:
        print(annotate(line))

    print()
    print("=" * 112)
    print("CALL SUMMARY")
    print("=" * 112)
    for line in insns:
        if CALL_RE.search(line):
            print(annotate(line))

    print()
    print("No DLL code was executed.")
    print("No HID/USB/SCSI access was performed.")


if __name__ == "__main__":
    main()
