#!/usr/bin/env python3
"""
Static, full disassembly of the small KeyLinker core used by the official
MixDrive gamepad warm-up reads.

Focus:
- complete state helper 0x1800014C0
- common read wrapper helper 0x180001BE0
- exact read builders used before CustomData

No DLL code is loaded or executed. No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

RANGES = [
    # Previous stop at 0x180001540 cut this function in the middle of a branch
    # to 0x1800015C2. Keep a generous bounded tail so its state transitions and
    # return value are visible in full.
    ("KeyLinkerState_COMPLETE",     0x1800014C0, 0x180001700),
    ("CommonReadHelper_1BE0",       0x180001BE0, 0x180001C90),
    ("readRockerData",              0x180001F40, 0x180002010),
    ("readTriggerData",             0x180002010, 0x1800020E0),
    ("readDeviceMotorData",         0x180002CA0, 0x180002D70),
    ("readDeviceBeforeKeyChangeList",0x180004CB0,0x180004CE0),
    ("readDeviceAfterKeyChangeList",0x180004D00, 0x180004DD0),
    ("readDeviceLamplightData",     0x180005090, 0x180005160),
    ("readDeviceSupportTurboList",  0x180005C20, 0x180005C50),
    ("readDeviceTurboData",         0x1800061A0, 0x180006270),
    ("readDeviceCustomData",        0x180008DB0, 0x180008E80),
]


def dump(objdump: str, dll: Path, start: int, stop: int) -> str:
    cp = subprocess.run(
        [
            objdump,
            "-d",
            "-M",
            "intel",
            f"--start-address=0x{start:X}",
            f"--stop-address=0x{stop:X}",
            str(dll),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if cp.returncode:
        raise SystemExit(cp.stdout)
    return cp.stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dll", type=Path, help="Path to KeyLinkerlib.dll")
    ap.add_argument(
        "--objdump",
        default=shutil.which("objdump") or "/usr/bin/objdump",
        help="objdump executable",
    )
    args = ap.parse_args()

    dll = args.dll.resolve()
    if not dll.is_file():
        raise SystemExit(f"DLL not found: {dll}")
    if not Path(args.objdump).is_file():
        raise SystemExit(f"objdump not found: {args.objdump}")

    print("=" * 112)
    print("KEYLINKER WARM-UP CORE — FULL STATIC DISASSEMBLY")
    print("=" * 112)
    print("DLL     :", dll)
    print("objdump :", args.objdump)

    for name, start, stop in RANGES:
        print("\n" + "=" * 112)
        print(f"{name}  0x{start:X} .. 0x{stop:X}")
        print("=" * 112)
        print(dump(args.objdump, dll, start, stop).rstrip())

    print("\nKnown plaintext construction recovered from CommonReadHelper_1BE0:")
    print("  B0 07 <state> <rand> <arg> <subcommand> <crc>")
    print("  BeforeKeyChangeList: arg=0, subcommand=3")
    print("  SupportTurboList   : arg=0, subcommand=4")

    print("\nNo DLL code was executed.")
    print("No HID/USB/SCSI access was performed.")
    print("Static objdump inspection only.")


if __name__ == "__main__":
    main()
