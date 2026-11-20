#!/usr/bin/env python3
"""
Read-only Linux HID interface audit for Machenike G3 V2 physical USB identity.

Enumerates hidraw nodes matching VID:PID 413D:2131 and decodes the top-level
HID Usage Page / Usage from the sysfs report descriptor. This lets us compare
the Linux interface with MixDrive's physical profile:

    VID:PID     413D:2131
    Usage Page  0xFF10
    Usage       0x01

No device node is opened for writing. No HID reports are sent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


TARGET_VID = 0x413D
TARGET_PID = 0x2131
TARGET_USAGE_PAGE = 0xFF10
TARGET_USAGE = 0x01


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except Exception:
        return ""


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def find_ancestor(start: Path, predicate):
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        try:
            if predicate(p):
                return p
        except Exception:
            pass
    return None


def hid_identity(device: Path):
    # HID uevent normally contains:
    # HID_ID=0003:0000413D:00002131
    for p in [device.resolve(), *device.resolve().parents]:
        uevent = read_text(p / "uevent")
        m = re.search(
            r"(?m)^HID_ID=([0-9A-Fa-f]+):([0-9A-Fa-f]+):([0-9A-Fa-f]+)$",
            uevent,
        )
        if m:
            bus = int(m.group(1), 16)
            vid = int(m.group(2), 16)
            pid = int(m.group(3), 16)
            return p, bus, vid, pid
    return None, None, None, None


def usb_interface_ancestor(device: Path):
    # USB interface directories look like 1-6.3.4:1.1
    pat = re.compile(r"^\d+-[\d.]+:\d+\.\d+$")
    return find_ancestor(device, lambda p: pat.match(p.name) is not None)


def decode_unsigned(data: bytes) -> int:
    if not data:
        return 0
    return int.from_bytes(data, "little", signed=False)


def parse_hid_descriptor(desc: bytes):
    usage_page = None
    local_usages = []
    top_usage_page = None
    top_usage = None
    report_ids = set()
    counts = {"input": 0, "output": 0, "feature": 0, "collection": 0}

    i = 0
    while i < len(desc):
        prefix = desc[i]
        i += 1

        if prefix == 0xFE:
            if i + 2 > len(desc):
                break
            size = desc[i]
            i += 2  # size + long-item tag
            i += size
            continue

        size_code = prefix & 0x03
        size = 4 if size_code == 3 else size_code
        item_type = (prefix >> 2) & 0x03
        tag = (prefix >> 4) & 0x0F

        payload = desc[i:i + size]
        i += size
        value = decode_unsigned(payload)

        # Global
        if item_type == 1:
            if tag == 0x0:  # Usage Page
                usage_page = value
            elif tag == 0x8:  # Report ID
                report_ids.add(value)

        # Local
        elif item_type == 2:
            if tag == 0x0:  # Usage
                local_usages.append(value)

        # Main
        elif item_type == 0:
            if tag == 0xA:  # Collection
                counts["collection"] += 1
                if top_usage_page is None:
                    top_usage_page = usage_page
                    top_usage = local_usages[0] if local_usages else None
                local_usages.clear()
            elif tag == 0x8:  # Input
                counts["input"] += 1
                local_usages.clear()
            elif tag == 0x9:  # Output
                counts["output"] += 1
                local_usages.clear()
            elif tag == 0xB:  # Feature
                counts["feature"] += 1
                local_usages.clear()
            else:
                local_usages.clear()

    return {
        "usage_page": top_usage_page,
        "usage": top_usage,
        "report_ids": sorted(report_ids),
        **counts,
    }


def fmt_hex(v, width=4):
    return "?" if v is None else f"0x{v:0{width}X}"


def main():
    print("=" * 78)
    print("G3V2 — AUDITORIA DAS INTERFACES HID LINUX (SOMENTE LEITURA)")
    print("=" * 78)
    print(f"Perfil físico esperado pelo MixDrive: {TARGET_VID:04X}:{TARGET_PID:04X}")
    print(
        f"Usage Page={fmt_hex(TARGET_USAGE_PAGE)} "
        f"Usage={fmt_hex(TARGET_USAGE, 2)}"
    )
    print()

    found = 0
    matching_profile = []

    for cls in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        dev = cls / "device"
        hid_dir, bus, vid, pid = hid_identity(dev)
        if vid != TARGET_VID or pid != TARGET_PID:
            continue

        found += 1
        node = Path("/dev") / cls.name
        resolved = dev.resolve()
        iface = usb_interface_ancestor(dev)

        descriptor_path = resolved / "report_descriptor"
        if not descriptor_path.exists() and hid_dir:
            descriptor_path = hid_dir / "report_descriptor"

        desc = read_bytes(descriptor_path)
        parsed = parse_hid_descriptor(desc) if desc else {}

        page = parsed.get("usage_page")
        usage = parsed.get("usage")
        profile_match = page == TARGET_USAGE_PAGE and usage == TARGET_USAGE

        if profile_match:
            matching_profile.append(node)

        print(f"--- {node} ---")
        print(f"Sysfs HID : {resolved}")
        print(f"HID bus   : {bus if bus is not None else '?'}")
        print(f"VID:PID   : {vid:04X}:{pid:04X}")

        if iface:
            print(f"USB iface : {iface.name}")
            for attr, label in (
                ("bInterfaceNumber", "Iface num"),
                ("bInterfaceClass", "Iface class"),
                ("bInterfaceSubClass", "Iface subclass"),
                ("bInterfaceProtocol", "Iface protocol"),
            ):
                val = read_text(iface / attr)
                if val:
                    print(f"{label:<10}: {val}")

        print(f"Descriptor: {descriptor_path}")
        print(f"Desc bytes: {len(desc)}")
        if desc:
            print(
                "Top-level : "
                f"Usage Page={fmt_hex(page)} "
                f"Usage={fmt_hex(usage, 2)}"
            )
            print(
                "Main items: "
                f"Input={parsed.get('input', 0)} "
                f"Output={parsed.get('output', 0)} "
                f"Feature={parsed.get('feature', 0)} "
                f"Collections={parsed.get('collection', 0)}"
            )
            ids = parsed.get("report_ids", [])
            print("Report IDs:", ", ".join(f"0x{x:02X}" for x in ids) if ids else "(none)")
            print("MixDrive profile (Usage Page/Usage):", "MATCH" if profile_match else "NO")
            print("Descriptor HEX:")
            print(desc.hex(" ").upper())
        else:
            print("Top-level : descriptor indisponível")

        print(f"Node existe: {node.exists()}")
        print(f"Leitura    : {os.access(node, os.R_OK) if node.exists() else False}")
        print(f"Escrita    : {os.access(node, os.W_OK) if node.exists() else False}")
        print()

    print("=" * 78)
    print("RESUMO")
    print("=" * 78)
    print("Interfaces 413D:2131 encontradas:", found)
    print(
        "Interfaces que batem Usage Page 0xFF10 / Usage 0x01:",
        len(matching_profile),
    )
    for node in matching_profile:
        print("  -", node)

    if len(matching_profile) == 1:
        print()
        print("Conclusão parcial: há uma única interface com o perfil HID esperado pelo MixDrive.")
    elif len(matching_profile) > 1:
        print()
        print("Conclusão parcial: mais de uma interface compartilha o Usage Page/Usage;")
        print("será necessário comparar os demais campos do descritor.")
    else:
        print()
        print("Conclusão parcial: nenhuma interface atual bate o Usage Page/Usage esperado.")

    print()
    print("Nenhum relatório HID foi enviado.")
    print("Nenhum arquivo de dispositivo foi aberto para escrita.")


if __name__ == "__main__":
    main()
