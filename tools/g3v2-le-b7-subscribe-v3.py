#!/usr/bin/env python3
"""G3V2: one-shot LE KeyLinker B7 query after explicit standard CCCD subscribe.

Uses verified G3V2 LE Public L2CAP CID 4 + GATT service/characteristic
discovery. Writes ONLY two ATT attributes:
  * CCCD 0x2902 found on the e2 Notify characteristic: 01 00 (subscribe).
  * e1 Write characteristic: exactly one encoded native B7 READ query.

No 0x37, 0x3B, OTA, SCSI, pairing, retries, bulk warm-up, or saved MAC.
The CCCD applies to this connection according to Bluetooth GATT; socket close
ends it. We read it before/after subscription and refuse ambiguous values.
ATT Write Request success is NOT an application-layer KeyLinker ACK.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAC = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z")
CCCD_NOTIFY_ON = b"\x01\x00"
KNOWN_BEFORE = (b"\x00", b"\x01", b"\x00\x00", b"\x01\x00")
KNOWN_AFTER = (b"\x01", b"\x01\x00")


def import_file(name: str):
    spec = importlib.util.spec_from_file_location("g3v2_b7_support_v2", ROOT / name)
    if spec is None or spec.loader is None:
        raise RuntimeError("Arquivo de suporte ausente: " + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = import_file("g3v2-le-keylinker-b7-v2.py")


def write_cccd_once(sock, handle: int, value: bytes) -> None:
    if value != CCCD_NOTIFY_ON:
        raise RuntimeError("Guarda CCCD: valor permitido somente 01 00")
    request = b"\x12" + struct.pack("<H", handle) + value
    if sock.send(request) != len(request):
        raise RuntimeError("Envio ATT incompleto")
    response = base.read_pdu(sock)
    if response != b"\x13":
        raise RuntimeError(base.error_reason(response, 0x12))


def run() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--address", required=True, help="MAC BLE do G3V2 APP")
    ap.add_argument("--connect-timeout", type=float, default=12.0)
    ap.add_argument("--att-timeout", type=float, default=5.0)
    args = ap.parse_args()
    if not 1 <= args.connect_timeout <= 30 or not 1 <= args.att_timeout <= 10:
        ap.error("timeout de conexão 1..30s; ATT 1..10s")
    address = args.address.strip().upper()
    if not MAC.fullmatch(address):
        ap.error("MAC inválido")

    base.codec_test()
    print("=" * 75)
    print("G3V2 — TESTE BLE CCCD STANDARD SUBSCRIBE + UMA LEITURA B7")
    print("=" * 75)
    print("ATT 0x12 ao CCCD verificado: valor 01 00 (assinatura temporária).")
    print("ATT 0x12 à e1 verificada: somente KeyLinker B7 de leitura, uma vez.")
    print("Sem 0x37 / 0x3B / OTA / SCSI / pairing / retries.")

    with socket.socket(
        socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP
    ) as sock:
        sock.settimeout(args.connect_timeout)
        sock.bind((socket.BDADDR_ANY, 0, 4, socket.BDADDR_LE_PUBLIC))
        started = time.monotonic()
        sock.connect((address, 0, 4, socket.BDADDR_LE_PUBLIC))
        print(f"LE Public conectado em {1000*(time.monotonic()-started):.1f} ms")
        sock.settimeout(args.att_timeout)

        svc = base.gatt.discover_services(sock)
        matches = [(a, b) for a, b, uid in svc if uid == base.KEYLINKER_SVC]
        if len(matches) != 1:
            raise RuntimeError("Serviço KeyLinker não encontrado univocamente")
        service_begin, service_end = matches[0]
        chars = base.gatt.discover_chars(sock, service_begin, service_end)

        e1 = [c for c in chars
              if c[3] == base.KEYLINKER_WRITE and (c[2] & 0x08)]
        e2 = [c for c in chars
              if c[3] == base.KEYLINKER_NOTIFY and (c[2] & 0x10)]
        if len(e1) != 1 or len(e2) != 1:
            raise RuntimeError("e1/e2 não correspondem a Write/Notify")
        tx_handle = e1[0][1]
        notify_handle = e2[0][1]
        following = [c[0] for c in chars if c[0] > e2[0][0]]
        last = min(following)-1 if following else service_end
        cccd_handle = base.find_cccd(sock, notify_handle + 1, last)

        print("\n=== CCCD antes da assinatura ===")
        before = base.read_cccd(sock, cccd_handle)
        if before not in KNOWN_BEFORE:
            print("CCCD tem formato/valor desconhecido. Abortando sem nenhuma escrita.")
            return 3

        print("\nHabilitando e2 com CCCD 01 00, uma única vez...")
        write_cccd_once(sock, cccd_handle, CCCD_NOTIFY_ON)
        print("CCCD ATT Write Response: OK (não prova notify funcional)")

        print("\n=== CCCD depois da assinatura ===")
        after = base.read_cccd(sock, cccd_handle)
        if after not in KNOWN_AFTER:
            print("CCCD após assinatura não é reconhecido; B7 NÃO enviado.")
            return 3

        print("\nEnviando uma única consulta KeyLinker B7 por e1...")
        session = base.keylinker.KeyLinkerReadSession()
        plain, wire, _, _, _ = base.keylinker.build_read_request(
            session, 0xB7, 0, None
        )
        print("  TX plain:", base.keylinker.hx(plain))
        print("  TX wire :", base.keylinker.hx(wire))
        queued = []
        t0 = time.monotonic()
        base.send_one_b7(sock, tx_handle, wire, notify_handle, queued)
        print("  e1 ATT Write Response: OK; esperando notificação e2...")
        try:
            encoded = base.get_notify(sock, notify_handle, queued)
        except socket.timeout:
            print("SEM NOTIFICAÇÃO e2 dentro do timeout; nenhum reenvio.")
            return 4

        elapsed = (time.monotonic()-t0)*1000.0
        decoded = base.keylinker.decode(encoded)
        print(f"  RX e2 wire: {base.keylinker.hx(encoded)} ({elapsed:.1f} ms)")
        print("  RX decoded:", base.keylinker.hx(decoded))
        base.keylinker.validate_plain_response(decoded, "Lamplight")
        if decoded[0] != 0xB7:
            raise RuntimeError(f"Opcode RX inesperado 0x{decoded[0]:02X}")
        logical_len, total, parts = base.keylinker.first_frame_total_native(decoded)
        if parts != 1 or total != len(encoded):
            print("Resposta B7 fragmentada; nenhuma continuação automática.")
            return 5
        value = base.keylinker.reconstruct_logical([decoded], logical_len)
        if len(value) != logical_len:
            raise RuntimeError("B7 comprimento lógico inconsistente")
        print("SUCESSO B7 LampLight:", base.keylinker.hx(value))
        return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (OSError, RuntimeError, ValueError, socket.timeout) as exc:
        print(f"FALHA: {type(exc).__name__}: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrompido. Socket será fechado.")
        sys.exit(130)
