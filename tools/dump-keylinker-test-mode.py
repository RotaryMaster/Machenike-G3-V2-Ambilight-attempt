#!/usr/bin/env python3
"""
Focused static audit of KeyLinker native test-mode / adjacent write paths.

Targets the still-unresolved G3V2 area around:
- FUN_180004820
- exitDeviceTestMode (around 0x180004920)
- the unassigned write/call region around 0x180004EA9
- protocol constants 0x1B (CODE_TEST_SET_MODE) and
  0x1C (CODE_TEST_NORMAL_MODE)

The tool:
- parses only the PE export table and .text metadata;
- disassembles bounded native regions with objdump;
- inventories exports containing "test"/"mode";
- finds all instruction-level references to 0x1B/0x1C in .text;
- finds direct xrefs/calls/jumps to the candidate routines/addresses;
- annotates known KeyLinker helper calls;
- prints nearby immediate stores so framing bytes can be reconstructed.

No DLL code is executed.
No HID/USB/SCSI access is performed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import pefile

KNOWN = {
    0x180001080: "KeyLinkerEncode",
    0x1800011C0: "KeyLinkerDecode",
    0x180001400: "Concat",
    0x1800014C0: "KeyLinkerState",
    0x180001760: "ReceiveReassembly",
    0x180001BE0: "GenericReadHelper",
    0x180001C90: "HeaderBuilder",
    0x18000B150: "rand_IAT",
}

CANDIDATE_VAS = {
    0x180004820: "FUN_180004820_candidate_enter_test_region",
    0x180004920: "exitDeviceTestMode_region",
    0x180004EA9: "unassigned_write_site_0x180004EA9",
}

ADDR_RE = re.compile(r"^\s*([0-9a-fA-F]+):")
IMM_RE = re.compile(r"(?<![0-9a-fA-F])0x([0-9a-fA-F]+)(?![0-9a-fA-F])")


def run(cmd: list[str]) -> str:
    cp = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return cp.stdout


def export_map(pe: pefile.PE):
    out = {}
    exp = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if not exp:
        return out
    for sym in exp.symbols:
        if not sym.name:
            continue
        name = sym.name.decode("ascii", errors="replace")
        out[name] = int(sym.address)
    return out


def text_bounds(pe: pefile.PE):
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    for sec in pe.sections:
        name = sec.Name.rstrip(b"\x00").decode("ascii", "replace")
        if name == ".text":
            start = image_base + int(sec.VirtualAddress)
            size = max(int(sec.Misc_VirtualSize), int(sec.SizeOfRawData))
            return start, start + size
    raise SystemExit(".text section not found")


def instruction_lines(disasm: str) -> list[str]:
    return [ln.rstrip() for ln in disasm.splitlines() if ADDR_RE.match(ln)]


def annotate(line: str) -> str:
    low = line.lower()
    hits = []
    for addr, name in KNOWN.items():
        hx = f"{addr:x}"
        if hx in low or f"0x{hx}" in low:
            hits.append(name)
    for addr, name in CANDIDATE_VAS.items():
        hx = f"{addr:x}"
        if hx in low or f"0x{hx}" in low:
            hits.append(name)
    return line + (("    <== " + ", ".join(hits)) if hits else "")


def disasm_range(objdump: str, dll: Path, start: int, stop: int) -> list[str]:
    out = run([
        objdump,
        "-d",
        "-Mintel",
        f"--start-address=0x{start:X}",
        f"--stop-address=0x{stop:X}",
        str(dll),
    ])
    return instruction_lines(out)


def print_context(insns: list[str], indexes: set[int], radius: int = 8):
    show = set()
    for i in indexes:
        show.update(range(max(0, i - radius), min(len(insns), i + radius + 1)))
    last = None
    for i in sorted(show):
        if last is not None and i != last + 1:
            print("  ...")
        print(annotate(insns[i]))
        last = i


def is_1b_1c_immediate(line: str) -> bool:
    # Require instruction operand immediates, not an address that merely contains
    # the digits "1b" or "1c".
    try:
        _, rhs = line.split(":", 1)
    except ValueError:
        return False
    # Strip opcode bytes: objdump emits tabs before mnemonic. Keep textual asm.
    parts = rhs.split("\t")
    asm = parts[-1] if parts else rhs
    vals = {int(x, 16) for x in IMM_RE.findall(asm)}
    return 0x1B in vals or 0x1C in vals


def target_xref(line: str) -> bool:
    low = line.lower()
    return any(f"{addr:x}" in low or f"0x{addr:x}" in low for addr in CANDIDATE_VAS)


def nearest_exports(exports: dict[str, int], image_base: int, va: int, limit: int = 6):
    rows = []
    for name, rva in exports.items():
        eva = image_base + rva
        rows.append((abs(eva - va), eva, name))
    return sorted(rows)[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll", help="Path to KeyLinkerlib.dll")
    ap.add_argument(
        "--objdump",
        default=shutil.which("objdump") or "/usr/bin/objdump",
        help="objdump binary",
    )
    ap.add_argument(
        "--radius",
        type=int,
        default=10,
        help="context instructions around hits (default: 10)",
    )
    args = ap.parse_args()

    dll = Path(args.dll)
    if not dll.is_file():
        raise SystemExit(f"DLL not found: {dll}")
    if not Path(args.objdump).is_file():
        raise SystemExit(f"objdump not found: {args.objdump}")

    pe = pefile.PE(str(dll), fast_load=False)
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    exports = export_map(pe)
    text_start, text_stop = text_bounds(pe)

    print("=" * 120)
    print("KEYLINKER TEST-MODE / 0x1B-0x1C — STATIC NATIVE AUDIT")
    print("=" * 120)
    print("DLL       :", dll)
    print(f"ImageBase : 0x{image_base:X}")
    print(f".text     : 0x{text_start:X} .. 0x{text_stop:X}")
    print("objdump   :", args.objdump)
    print("Policy    : STATIC ONLY — no DLL execution, no HID/USB/SCSI I/O")

    print("\n" + "=" * 120)
    print("1. TEST/MODE EXPORT INVENTORY")
    print("=" * 120)
    rows = []
    for name, rva in exports.items():
        low = name.lower()
        if "test" in low or "mode" in low:
            rows.append((image_base + rva, rva, name))
    if rows:
        for va, rva, name in sorted(rows):
            print(f"VA=0x{va:X} RVA=0x{rva:X}  {name}")
    else:
        print("(no export names containing test/mode)")

    for va, label in CANDIDATE_VAS.items():
        print(f"\nNearest exports to {label} @ 0x{va:X}:")
        for dist, eva, name in nearest_exports(exports, image_base, va):
            sign = "+" if eva >= va else "-"
            print(f"  {name:<40} VA=0x{eva:X}  delta={sign}0x{abs(eva-va):X}")

    print("\n" + "=" * 120)
    print("2. FOCUSED REGION 0x180004780 .. 0x180004F80")
    print("=" * 120)
    lo = max(text_start, 0x180004780)
    hi = min(text_stop, 0x180004F80)
    focused = disasm_range(args.objdump, dll, lo, hi)
    for line in focused:
        print(annotate(line))

    print("\n" + "=" * 120)
    print("3. WHOLE-.text IMMEDIATE REFERENCES TO 0x1B / 0x1C")
    print("=" * 120)
    whole = disasm_range(args.objdump, dll, text_start, text_stop)
    imm_hits = {i for i, line in enumerate(whole) if is_1b_1c_immediate(line)}
    if imm_hits:
        print(f"Instruction hits: {len(imm_hits)}")
        print_context(whole, imm_hits, args.radius)
    else:
        print("(no instruction-level immediate 0x1B/0x1C references found)")

    print("\n" + "=" * 120)
    print("4. XREFS TO CANDIDATE TEST-MODE / UNASSIGNED ADDRESSES")
    print("=" * 120)
    xrefs = {i for i, line in enumerate(whole) if target_xref(line)}
    if xrefs:
        print(f"Xref-like instruction hits: {len(xrefs)}")
        print_context(whole, xrefs, args.radius)
    else:
        print("(no direct textual xrefs found)")

    print("\n" + "=" * 120)
    print("5. BYTE-BUILDING / FRAMING HINTS IN CANDIDATE REGION")
    print("=" * 120)
    build_hits = set()
    for i, line in enumerate(focused):
        low = line.lower()
        if (
            "byte ptr" in low
            or "word ptr" in low
            or "dword ptr" in low
            or "lea" in low
            or "call" in low
        ):
            if (
                re.search(r",0x[0-9a-f]{1,8}\b", low)
                or "180001080" in low
                or "180001400" in low
                or "1800014c0" in low
                or "180001c90" in low
                or "18000b150" in low
            ):
                build_hits.add(i)
    if build_hits:
        print_context(focused, build_hits, max(5, args.radius // 2))
    else:
        print("(no candidate framing instructions matched heuristic)")

    print("\n" + "=" * 120)
    print("INTERPRETATION TARGET")
    print("=" * 120)
    print("Resolve, from evidence only:")
    print("1. whether FUN_180004820 is actually enterDeviceTestMode or another builder;")
    print("2. exact first command byte(s), argument bytes, length and checksum/transform path;")
    print("3. whether exitDeviceTestMode emits 0x1C and what payload accompanies it;")
    print("4. whether any builder emits 0x1B and how its mode argument is encoded;")
    print("5. whether 0x180004EA9 belongs to test/key-change infrastructure rather than lighting.")
    print()
    print("Do NOT run 0x1B/0x1C live from this output alone.")
    print("Static analysis must first close framing and side effects.")


if __name__ == "__main__":
    main()
