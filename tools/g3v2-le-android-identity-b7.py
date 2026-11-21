#!/usr/bin/env python3
"""G3V2 Android KeyLinker BLE protocol check using actual APK-derived codec.

Physical operations, one connection and NO retries:
  - LE Public L2CAP ATT CID 4 connection
  - GATT primary service / characteristic / CCCD identification
  - one temporary standard CCCD notification subscription (01 00)
  - exactly one APK e.L() identity 0x91 READ query via e1 Write Request
  - if and only if identity notification validates, optionally one APK e.q(0)
    lighting B7 READ query in the SAME session

No USB/MixDrive codec, 0x37/0x3B, lighting writes, OTA, SCSI, pairing,
reset, persistent writes, system modification, or automatic reconnection.
The ATT Write Request acknowledges transport only, not KeyLinker execution.
MAC only on command line and never committed to GitHub.
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
MAC_RE = re.compile(r"(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}\Z")
NOTIFY_ON = b"\x01\x00"
MAX_PDUS = 16


def load(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Arquivo de suporte ausente: {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


android = load("g3v2-android-keylinker-codec.py", "g3v2_android_keylinker")
base = load("g3v2-le-keylinker-b7-v2.py", "g3v2_gatt_helpers")


def fail_if_error(packet: bytes, opcode: int) -> None:
    if packet[0] == 0x01:
        raise RuntimeError(base.error_reason(packet, opcode))


def write_request(sock: socket.socket, handle: int, value: bytes,
                  early_notifications: list[bytes]) -> None:
    """Only caller-validated handles/payloads are passed here."""
    packet = b"\x12" + struct.pack("<H", handle) + value
    if sock.send(packet) != len(packet):
        raise RuntimeError("ATT Write Request enviado parcialmente")
    for _ in range(MAX_PDUS):
        reply = sock.recv(512)
        if not reply:
            raise RuntimeError("ATT desconectou ao aguardar Write Response")
        fail_if_error(reply, 0x12)
        if reply == b"\x13":
            return
        if reply[0] == 0x1B and len(reply) >= 4:
            early_notifications.append(reply)
            continue
        raise RuntimeError(f"Resposta ATT inesperada durante write: {reply.hex(' ')}")
    raise RuntimeError("Sem ATT Write Response após 16 PDUs")


def await_matching_notify(sock: socket.socket, handle: int, serial: int,
                          early: list[bytes], timeout: float) -> tuple[bytes, bytes]:
    """Require notification on e2 + decoded APK CRC + echoed serial.

    The APK callback removes in-flight requests by decoded byte [2].
    It does not require the response opcode to echo the request opcode,
    so do not assume B7/91: response 0x19 may be normal.
    """
    deadline = time.monotonic() + timeout
    for _ in range(MAX_PDUS):
        if early:
            data = early.pop(0)
        else:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError("Sem notificação KeyLinker compatível no prazo")
            sock.settimeout(remain)
            try:
                data = sock.recv(512)
            except socket.timeout as exc:
                raise TimeoutError("Sem notificação KeyLinker compatível no prazo") from exc
        if not data:
            raise RuntimeError("G3V2 encerrou socket ATT")
        fail_if_error(data, 0x12)
        if data[0] != 0x1B or len(data) < 4:
            print(f"  ATT diferente de notificação: {data.hex(' ').upper()}", flush=True)
            continue
        got_handle = struct.unpack_from("<H", data, 1)[0]
        if got_handle != handle:
            print(f"  Notificação ignorada em 0x{got_handle:04X}", flush=True)
            continue
        wire = data[3:]
        print(f"  RX e2 wire [{len(wire)}]: {wire.hex(' ').upper()}", flush=True)
        try:
            plain = android.decode(wire)
        except ValueError as err:
            print(f"  RX codec não reconheceu a notificação: {err}", flush=True)
            continue
        print(f"  RX Android decoded: {plain.hex(' ').upper()}", flush=True)
        try:
            if android.crc8(plain[:-1]) != plain[-1]:
                raise ValueError("CRC inválido")
            if plain[2] != serial:
                raise ValueError(f"serial 0x{plain[2]:02X} != 0x{serial:02X}")
        except ValueError as err:
            print(f"  Notificação sem CRC/serial correspondente: {err}", flush=True)
            continue
        print(f"  RX KeyLinker válido: opcode=0x{plain[0]:02X}, serial=0x{serial:02X}",
              flush=True)
        return wire, plain
    raise RuntimeError("Limite de 16 PDUs atingido sem notificação válida")


def query(sock: socket.socket, tx_handle: int, notify_handle: int,
          opcode: int, serial: int, payload: bytes, timeout: float) -> bytes:
    nonce = android.nonce()
    plain, wire = android.build_read(opcode, serial, nonce, payload)
    if opcode not in android.READ_ONLY_OPS:
        raise RuntimeError("Guarda: opcode de configuração proibido")
    print(f"\n=== QUERY ANDROID 0x{opcode:02X} serial=0x{serial:02X} ===", flush=True)
    print("TX plaintext :", plain.hex(" ").upper(), flush=True)
    print("TX APK wire  :", wire.hex(" ").upper(), flush=True)
    early: list[bytes] = []
    t0 = time.monotonic()
    sock.settimeout(timeout)
    write_request(sock, tx_handle, wire, early)
    print("ATT Write Response: OK; aguardando e2", flush=True)
    _, decoded = await_matching_notify(sock, notify_handle, serial, early, timeout)
    print(f"Roundtrip da QUERY: {(time.monotonic() - t0)*1000:.1f} ms", flush=True)
    return decoded


def parse_one_frame_reply(plain: bytes) -> bytes:
    """Recover one complete logical record from response opcode 0x19."""
    if len(plain) < 6 or plain[0] != 0x19 or plain[1] != len(plain):
        raise RuntimeError("Resposta KeyLinker inválida para leitura lógica")
    if android.crc8(plain[:-1]) != plain[-1]:
        raise RuntimeError("Resposta KeyLinker com CRC inválido")
    declared = plain[4]
    payload = plain[5:-1]
    if len(payload) != declared:
        raise RuntimeError(
            f"Resposta contém {len(payload)} bytes lógicos, declara {declared}; "
            "uma continuação poderá ser necessária, não haverá solicitação automática"
        )
    return payload


def show_identity(plain: bytes) -> None:
    record = parse_one_frame_reply(plain)
    if len(record) != 7:
        print("Identidade com tamanho inesperado:", record.hex(" ").upper(), flush=True)
        return
    vid = record[:2].hex().upper()
    pid = record[2:4].hex().upper()
    version = f"{record[4]:X}.{record[5]:02X}"
    print(f"IDENTIDADE REAL: ID lógico {vid}:{pid}, firmware {version}, "
          f"flags 0x{record[6]:02X}", flush=True)


def show_lamplight(plain: bytes) -> None:
    body = parse_one_frame_reply(plain)
    print("B7 lógico:", body.hex(" ").upper(), flush=True)
    if len(body) != 6:
        print("B7 tem comprimento diferente de 6; sem decodificação de cor.", flush=True)
        return
    r, g, b, packed_lo, packed_hi, brightness = body
    packed = packed_lo | (packed_hi << 8)
    speed = packed & 0x7FFF
    breath_switch = (packed >> 15) & 1
    print(f"ILUMINAÇÃO B7: RGB=({r}, {g}, {b}), "
          f"breathSpeed={speed}, breathSwitch={breath_switch}, "
          f"brightness={brightness}", flush=True)
    print("NOTA: modo visual por FN+Right não é completamente representado no B7.",
          flush=True)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--address", required=True)
    cli.add_argument("--mode", choices=("identity-only", "identity-then-b7"),
                     default="identity-then-b7")
    cli.add_argument("--connect-timeout", type=float, default=12.0)
    cli.add_argument("--att-timeout", type=float, default=5.0)
    args = cli.parse_args()
    if not (1 <= args.connect_timeout <= 30 and 1 <= args.att_timeout <= 10):
        cli.error("connect-timeout 1..30s e att-timeout 1..10s")
    if not MAC_RE.fullmatch(args.address.strip()):
        cli.error("Endereço MAC BLE inválido")

    android.selftest()
    print("=" * 76)
    print("G3V2 — VERIFICAÇÃO REAL DO CODEC ANDROID KEYLINKER (APK)")
    print("=" * 76)
    print("Autotestes offline de encode/decode, CRC e serial: OK")
    print("Proibidos 0x37/0x3B, OTA, SCSI, pairing e retries")
    print("Modo:", args.mode)
    print("A única escrita de configuração será CCCD 01 00 (notificação temporária).")

    with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                       socket.BTPROTO_L2CAP) as sock:
        sock.settimeout(args.connect_timeout)
        sock.bind((socket.BDADDR_ANY, 0, 4, socket.BDADDR_LE_PUBLIC))
        started = time.monotonic()
        sock.connect((args.address.upper(), 0, 4, socket.BDADDR_LE_PUBLIC))
        print(f"Conexão LE Public OK ({(time.monotonic() - started)*1000:.1f} ms)",
              flush=True)
        sock.settimeout(args.att_timeout)

        svc = base.gatt.discover_services(sock)
        matches = [(lo, hi) for lo, hi, uuid in svc if uuid == base.KEYLINKER_SVC]
        if len(matches) != 1:
            raise RuntimeError("Serviço KeyLinker e0 não encontrado univocamente")
        chars = base.gatt.discover_chars(sock, *matches[0])
        tx = [entry for entry in chars
              if entry[3] == base.KEYLINKER_WRITE and entry[2] & 0x08]
        rx = [entry for entry in chars
              if entry[3] == base.KEYLINKER_NOTIFY and entry[2] & 0x10]
        if len(tx) != 1 or len(rx) != 1:
            raise RuntimeError("GATT e1/e2 ou propriedades divergem")
        tx_handle = tx[0][1]
        notify_handle = rx[0][1]
        next_decls = [entry[0] for entry in chars if entry[0] > rx[0][0]]
        desc_end = min(next_decls) - 1 if next_decls else matches[0][1]
        cccd_handle = base.find_cccd(sock, notify_handle + 1, desc_end)
        print(f"GATT e1=0x{tx_handle:04X}, e2=0x{notify_handle:04X}, "
              f"CCCD=0x{cccd_handle:04X}", flush=True)
        before = base.read_cccd(sock, cccd_handle)
        if before not in (b"\x01", b"\x01\x00"):
            print("CCCD não corresponde à observação anterior; nada será enviado.")
            return 3

        print("\nEscrevendo CCCD padrão 01 00 (somente assinatura)...", flush=True)
        early: list[bytes] = []
        write_request(sock, cccd_handle, NOTIFY_ON, early)
        print("CCCD ATT Write Response: OK (não prova resposta KeyLinker)", flush=True)
        # No additional CCCD read needed: its unusual 1-byte behavior was
        # measured both before and after subscription in v3.
        if early:
            print(f"{len(early)} notificação(ões) recebida(s) durante a assinatura",
                  flush=True)

        serial = android.AndroidSerial()
        identity_serial = serial.next()  # e.K() starts at 1
        try:
            reply = query(sock, tx_handle, notify_handle, 0x91, identity_serial,
                          b"", args.att_timeout)
        except TimeoutError as exc:
            print("SEM IDENTIFICAÇÃO 0x91: " + str(exc), flush=True)
            print("B7 NÃO será enviado; nenhuma repetição automática.")
            return 4
        print(f"SUCESSO: resposta Android KeyLinker após 0x91: "
              f"{reply.hex(' ').upper()}", flush=True)
        show_identity(reply)

        if args.mode == "identity-only":
            print("Concluído em modo identidade; nenhuma leitura B7 enviada.")
            return 0

        b7_serial = serial.next()  # e.K(): second serial is 130 (0x82)
        try:
            lamp = query(sock, tx_handle, notify_handle, 0xB7, b7_serial,
                         b"\x00", args.att_timeout)
        except TimeoutError as exc:
            print("B7 SEM NOTIFICAÇÃO: " + str(exc), flush=True)
            print("0x91 funcionou, mas B7 requer investigação do corpo/estado.")
            return 5
        print(f"SUCESSO: resposta B7: {lamp.hex(' ').upper()}", flush=True)
        show_lamplight(lamp)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, socket.timeout) as exc:
        print(f"FALHA: {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nInterrompido; conexão LE encerrada.", flush=True)
        sys.exit(130)
