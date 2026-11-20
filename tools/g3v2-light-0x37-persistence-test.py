#!/usr/bin/env python3
"""
Machenike G3 V2 — isolated LampLight 0x37 persistence test.

Purpose:
  Determine whether ONE standalone LampLight write (native command 0x37)
  survives a real controller power-cycle, without any CustomData 0x3B write.

Modes:
  --apply    Read current LampLight, save a local snapshot, change RGB only
             with exactly one 0x37 frame, then B7-read it back.
  --check    READ ONLY after a real controller power-cycle. Compare current
             LampLight against the marker and original snapshot.
  --restore  Restore the original LampLight snapshot with exactly one 0x37
             frame, then B7-read it back.

Safety / scope:
  - No 0x3B CustomData writes.
  - No OTA bit changes.
  - No SCSI/F0/F1/F2.
  - No loops or realtime lighting.
  - At most one 0x37 write in --apply or --restore.
  - Aborts unless the live B7 LampLight payload is exactly one 6-byte lamp.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import time

HERE = Path(__file__).resolve().parent
BASE_TOOL = HERE / "g3v2-warmup-readonly.py"
SNAPSHOT_DEFAULT = Path("/tmp/g3v2-light-0x37-snapshot.json")


def load_base():
    spec = importlib.util.spec_from_file_location("g3v2_warmup_readonly", BASE_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_TOOL}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = load_base()


def next_seq(seq: int) -> int:
    seq += 1
    return 1 if seq > 255 else seq


def receive_any_outer(fd: int, seq: int, timeout: float = 3.0):
    """Accept either ACK or normal data result for the write sequence."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue

        try:
            raw = os.read(fd, g.REPORT_LEN)
        except BlockingIOError:
            continue

        if len(raw) < 5:
            continue

        print(
            "    HID RX raw:",
            g.hx(raw[: min(len(raw), 32)]),
            "..." if len(raw) > 32 else "",
        )

        if raw[0] != 0x81 or raw[1] != 0xF9 or raw[2] != seq:
            continue

        flags = raw[3]
        length = raw[4]
        if 5 + length > len(raw):
            raise RuntimeError("truncated HID write response")

        payload = bytes(raw[5 : 5 + length])
        print(
            f"    HID RX write seq={seq:02X} flags={flags:02X} "
            f"len={length}: {g.hx(payload)}"
        )
        return flags, payload


def warmup_and_capture(fd: int):
    print("Fila antiga descartada:", g.drain(fd))

    print("\n=== IDENTIDADE ===")
    g.ping_identity(fd, 1)

    session = g.KeyLinkerReadSession()
    outer_seq = 2
    lamp = None
    custom_final = None

    print("\n=== WARM-UP OFICIAL — SOMENTE LEITURAS ===")
    for index, (label, command, subcommand) in enumerate(g.OFFICIAL_WARMUP, start=1):
        print("\n" + "-" * 78)
        print(
            f"{index:02d}/{len(g.OFFICIAL_WARMUP):02d} {label} "
            f"(cmd={command:02X})"
        )
        outer_seq, logical = g.perform_native_read(
            fd, outer_seq, session, label, command, subcommand
        )
        if label == "Lamplight":
            lamp = bytes(logical)
        if label == "CustomData FINAL":
            custom_final = bytes(logical)

    if lamp is None:
        raise RuntimeError("B7 LampLight was not captured")
    if len(lamp) != 6:
        raise RuntimeError(
            f"refusing write: expected one 6-byte LampLight payload, got {len(lamp)}"
        )

    print("\n=== BASELINE CAPTURADO ===")
    print("LampLight B7 logical[6]:", g.hx(lamp))
    if custom_final is not None:
        print(f"CustomData FINAL[{len(custom_final)}]:", g.hx(custom_final))

    return outer_seq, session, lamp, custom_final


def choose_marker(original: bytes) -> bytes:
    # Preserve effect/breath bytes and physical brightness exactly.
    # Only RGB changes. Prefer red; if already red, use green.
    rgb = original[:3]
    marker_rgb = bytes([0xFF, 0x00, 0x00])
    if rgb == marker_rgb:
        marker_rgb = bytes([0x00, 0xFF, 0x00])
    return marker_rgb + original[3:]


def build_single_lamp_write(session, lamp6: bytes):
    if len(lamp6) != 6:
        raise ValueError("LampLight must be exactly 6 bytes")

    # Native writer's logical stream for one lamp:
    #   06 R G B packed_lo packed_hi brightness
    stream = bytes([0x06]) + lamp6

    # Exactly one fragment:
    # 4-byte header + 7-byte stream + 1 CRC = 12 bytes.
    frame_len = 4 + len(stream) + 1

    # Cold Python test has emitted no native write frames before this point,
    # therefore E670 write phase is 0. Reads do not advance E670.
    e670 = 0
    header1 = ((e670 & 7) << 5) | (frame_len & 0x1F)

    # For fragment 0, the write header's fragment-index bits are zero.
    header2 = (
        ((session.e67c & 1) << 7)
        | (0 << 3)
        | (session.e68c & 7)
    )

    rnd = session.next_rand_low()
    plain = bytearray([0x37, header1, header2, rnd])
    plain.extend(stream)
    plain.append(g.crc8(plain))
    wire = g.encode(bytes(plain))

    return bytes(plain), wire, rnd


def advance_after_one_write(session):
    # Native writer advances these after each generated write frame.
    session.e67c = 1 if session.e67c == 0 else 0
    session.e68c = (session.e68c + 1) & 7
    # E670 would become 7 after this first write, but reads do not use it.


def send_one_0x37(fd: int, outer_seq: int, session, lamp6: bytes):
    plain, wire, rnd = build_single_lamp_write(session, lamp6)

    print("\n=== ÚNICO WRITE 0x37 ===")
    print("E670 inicial : 0")
    print(f"E67C inicial : {session.e67c}")
    print(f"E68C inicial : {session.e68c}")
    print(f"rand         : {rnd:02X}")
    print("Plain TX     :", g.hx(plain))
    print("Wire TX      :", g.hx(wire))
    print("0x3B         : NÃO ENVIADO")

    g.send_outer(fd, outer_seq, wire)

    result = receive_any_outer(fd, outer_seq, timeout=3.0)
    if result is None:
        print("    Aviso: sem resposta externa imediata; B7 readback decidirá aceitação.")
    else:
        flags, payload = result
        if flags & 1:
            print("    Resposta externa: ACK")
        else:
            print("    Resposta externa: DATA")

    advance_after_one_write(session)
    return next_seq(outer_seq)


def readback_b7(fd: int, outer_seq: int, session, label: str):
    print(f"\n=== {label} — B7 READBACK ===")
    outer_seq, logical = g.perform_native_read(
        fd, outer_seq, session, label, 0xB7, None
    )
    return outer_seq, bytes(logical)


def save_snapshot(path: Path, original: bytes, marker: bytes):
    data = {
        "version": 1,
        "original_lamplight_hex": original.hex(),
        "marker_lamplight_hex": marker.hex(),
        "note": "Per-unit identifiers intentionally not stored.",
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_snapshot(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise RuntimeError("unsupported snapshot version")
    original = bytes.fromhex(data["original_lamplight_hex"])
    marker = bytes.fromhex(data["marker_lamplight_hex"])
    if len(original) != 6 or len(marker) != 6:
        raise RuntimeError("invalid snapshot LampLight length")
    return original, marker


def print_compare(current: bytes, original: bytes, marker: bytes):
    print("\n=== COMPARAÇÃO ===")
    print("Original :", g.hx(original))
    print("Marcador :", g.hx(marker))
    print("Atual    :", g.hx(current))

    if current == marker:
        print("RESULTADO: MATCH MARCADOR")
        print("O estado aplicado pelo 0x37 está presente.")
        return "marker"
    if current == original:
        print("RESULTADO: MATCH ORIGINAL")
        print("O estado original está presente.")
        return "original"

    print("RESULTADO: TERCEIRO ESTADO / DIFERENTE")
    return "other"


def open_device():
    dev, sysfs = g.discover()
    print("=" * 78)
    print("G3V2 — TESTE ISOLADO DE PERSISTÊNCIA LAMPLIGHT 0x37")
    print("=" * 78)
    print("Device:", dev)
    print("Sysfs :", sysfs)
    print("0x3B  : BLOQUEADO / NÃO IMPLEMENTADO NESTE TESTE")
    print("OTA   : BLOQUEADO / NÃO IMPLEMENTADO")
    print("SCSI  : BLOQUEADO / NÃO IMPLEMENTADO")
    return os.open(dev, os.O_RDWR | os.O_NONBLOCK)


def mode_apply(snapshot: Path):
    if snapshot.exists():
        raise RuntimeError(
            f"snapshot already exists: {snapshot}\n"
            "Use --check/--restore first, or remove it only after confirming restoration."
        )

    fd = open_device()
    try:
        outer_seq, session, original, _ = warmup_and_capture(fd)
        marker = choose_marker(original)

        print("\n=== PLANO DA ÚNICA ALTERAÇÃO ===")
        print("Original :", g.hx(original))
        print("Marcador :", g.hx(marker))
        print("Somente RGB muda; bytes de efeito/brilho são preservados.")
        print("Writes 0x37 previstos: EXATAMENTE 1")
        print("Writes 0x3B previstos: 0")

        # Save before mutation so restoration data already exists even if the
        # process is interrupted immediately after the write.
        save_snapshot(snapshot, original, marker)
        print("Snapshot salvo em:", snapshot)

        outer_seq = send_one_0x37(fd, outer_seq, session, marker)
        _, current = readback_b7(fd, outer_seq, session, "PÓS-WRITE")

        state = print_compare(current, original, marker)
        if state != "marker":
            print("\nO 0x37 não foi confirmado por readback; NÃO faça o power-cycle ainda.")
            raise SystemExit(2)

        print("\nAPPLY CONFIRMADO.")
        print("Agora desligue fisicamente o controle, aguarde alguns segundos,")
        print("ligue/reconecte por USB e execute este mesmo script com --check.")
    finally:
        os.close(fd)


def mode_check(snapshot: Path):
    original, marker = load_snapshot(snapshot)

    fd = open_device()
    try:
        _, _, current, _ = warmup_and_capture(fd)
        state = print_compare(current, original, marker)

        print("\n=== INTERPRETAÇÃO PÓS-POWER-CYCLE ===")
        if state == "marker":
            print("CONFIRMADO: o estado do 0x37 sobreviveu ao power-cycle.")
            print("Isso é evidência de persistência do 0x37 isolado.")
        elif state == "original":
            print("O marcador não sobreviveu; o estado voltou ao original.")
            print("Isso é compatível com 0x37 volátil/não persistente isoladamente.")
        else:
            print("Estado diferente de original e marcador; resultado inconclusivo.")

        print("\nNenhuma escrita foi realizada por --check.")
        print("Quando terminar a inspeção, use --restore para repor o snapshot original.")
    finally:
        os.close(fd)


def mode_restore(snapshot: Path):
    original, marker = load_snapshot(snapshot)

    fd = open_device()
    try:
        outer_seq, session, current, _ = warmup_and_capture(fd)

        print("\n=== RESTAURAÇÃO ===")
        print("Atual    :", g.hx(current))
        print("Original :", g.hx(original))
        print("Writes 0x37 previstos: EXATAMENTE 1")
        print("Writes 0x3B previstos: 0")

        if current == original:
            print("Estado já é o original; nenhuma escrita necessária.")
            snapshot.unlink(missing_ok=True)
            print("Snapshot removido:", snapshot)
            return

        outer_seq = send_one_0x37(fd, outer_seq, session, original)
        _, after = readback_b7(fd, outer_seq, session, "PÓS-RESTORE")

        state = print_compare(after, original, marker)
        if state != "original":
            print("Restauração não confirmada; snapshot foi mantido.")
            raise SystemExit(3)

        print("\nRESTAURAÇÃO CONFIRMADA.")
        snapshot.unlink(missing_ok=True)
        print("Snapshot removido:", snapshot)
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--restore", action="store_true")
    ap.add_argument(
        "--snapshot",
        type=Path,
        default=SNAPSHOT_DEFAULT,
        help=f"snapshot path (default: {SNAPSHOT_DEFAULT})",
    )
    args = ap.parse_args()

    if args.apply:
        mode_apply(args.snapshot)
    elif args.check:
        mode_check(args.snapshot)
    else:
        mode_restore(args.snapshot)


if __name__ == "__main__":
    main()
