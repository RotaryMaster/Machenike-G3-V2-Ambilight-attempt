#!/usr/bin/env python3
"""
Machenike G3 V2 — focused lighting state reader.

READ-ONLY:
- identity ping
- official reconstructed warm-up reads
- captures B7 LampLight
- captures final BB CustomData when available
- decodes only already-proven lighting fields

No 0x37 writes.
No 0x3B writes.
No OTA.
No SCSI.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_TOOL = HERE / "g3v2-warmup-readonly.py"


def load_base():
    spec = importlib.util.spec_from_file_location("g3v2_warmup_readonly", BASE_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_TOOL}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = load_base()


def decode_lamp(lamp: bytes) -> None:
    print("\n=== LAMPLIGHT B7 ===")
    print(f"raw[{len(lamp)}]: {g.hx(lamp)}")

    if len(lamp) != 6:
        print("Formato inesperado; não decodificando.")
        return

    r, gg, b, packed_lo, packed_hi, brightness = lamp
    packed = packed_lo | (packed_hi << 8)
    breath_switch = (packed >> 15) & 1
    breath_speed = packed & 0x7FFF

    print(f"RGB             : R={r} G={gg} B={b}")
    print(f"packed          : 0x{packed:04X}")
    print(f"breathSwitch    : {breath_switch}")
    print(f"breathSpeed     : {breath_speed}")
    print(f"lamp brightness : {brightness}")


def decode_custom(custom: bytes) -> None:
    print("\n=== CUSTOMDATA BB ===")
    print(f"raw[{len(custom)}]: {g.hx(custom)}")

    if len(custom) == 0:
        print("CustomData vazio nesta sessão.")
        return

    if len(custom) != 20:
        print("Tamanho inesperado; não decodificando campos.")
        return

    print(f"data[0] LightBrightness : {custom[0]}")
    print(f"data[1] LightModeIndex  : {custom[1]}")
    print(f"data[2] RockerCursorType: {custom[2]}")
    print(f"data[4] MotorGripMode raw: {custom[4]}")
    print(f"data[7] flags           : 0x{custom[7]:02X}")
    print(f"data[8] RockerReturnRate: {custom[8]}")
    print(f"data[10] RockerResolution: {custom[10]}")
    print(f"data[12] left outer diameter : {custom[12]}")
    print(f"data[13] right outer diameter: {custom[13]}")


def main() -> None:
    dev, sysfs = g.discover()

    print("=" * 78)
    print("G3V2 — LEITURA FOCADA DO ESTADO DE ILUMINAÇÃO")
    print("=" * 78)
    print("Device:", dev)
    print("Sysfs :", sysfs)
    print("MODO  : SOMENTE LEITURA")
    print("0x37  : NÃO IMPLEMENTADO")
    print("0x3B  : NÃO IMPLEMENTADO")
    print("OTA   : NÃO IMPLEMENTADO")
    print("SCSI  : NÃO IMPLEMENTADO")

    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        print("Fila antiga descartada:", g.drain(fd))

        print("\n=== IDENTIDADE ===")
        g.ping_identity(fd, 1)

        session = g.KeyLinkerReadSession()
        outer_seq = 2
        lamp = None
        final_custom = None

        for label, command, subcommand in g.OFFICIAL_WARMUP:
            outer_seq, logical = g.perform_native_read(
                fd, outer_seq, session, label, command, subcommand
            )
            if label == "Lamplight":
                lamp = bytes(logical)
            if label == "CustomData FINAL":
                final_custom = bytes(logical)

        if lamp is None:
            raise RuntimeError("LampLight B7 não capturado")
        if final_custom is None:
            raise RuntimeError("CustomData FINAL não capturado")

        print("\n" + "=" * 78)
        print("RESUMO DO ESTADO")
        print("=" * 78)
        decode_lamp(lamp)
        decode_custom(final_custom)

        print("\nNenhuma escrita foi enviada.")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
