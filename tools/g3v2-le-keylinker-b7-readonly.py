#!/usr/bin/env python3
"""G3V2 direct LE KeyLinker B7 read probe (one query, no lighting/OTA writes).

Uses proven LE Public L2CAP ATT CID 4 connection. Discovers the *real* KeyLinker
service/characteristic handles and CCCD; does not assume CCCD is at 0x000A.
--inspect-only: only ATT discovery/read requests; NO ATT writes.
--b7: enables notifications via the verified CCCD (temporary ATT write),
       sends exactly ONE KeyLinker B7 read/query on e1 via ATT Write Request,
       receives e2 notification, validates KeyLinker codec+CRC, and attempts
       to restore the original CCCD before disconnecting.

"Read-only" describes the KeyLinker application command (B7); GATT setup
necessarily writes the subscription descriptor. The e1 GATT write carries
only the guarded read/query frame, NOT lighting/firmware configuration.
No pairing requests, no 0x37/0x3B, OTA, SCSI or retries.
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

HERE = Path(__file__).resolve().parent
MAC_RE = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z")
KEYLINKER_SVC = "d7f010e0-660d-46e9-96c3-19c4148bdab5"
KEYLINKER_WRITE = "d7f010e1-660d-46e9-96c3-19c4148bdab5"
KEYLINKER_NOTIFY = "d7f010e2-660d-46e9-96c3-19c4148bdab5"
ATT_CID = 4
MAX_PDUS = 12


def load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Arquivo de suporte ausente: {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gatt = load_module("g3v2-le-gatt-discover-readonly.py", "g3v2_direct_gatt")
keylinker = load_module("g3v2-warmup-readonly.py", "g3v2_keylinker_codec")


def codec_test():
    if keylinker.encode(bytes.fromhex("BB 06 00 29 00 27")) != bytes.fromhex(
        "C0 B3 40 70 99 96"
    ):
        raise RuntimeError("Codec KeyLinker: autoteste falhou")


def read_pdu(sock: socket.socket) -> bytes:
    packet = sock.recv(512)
    if not packet:
        raise RuntimeError("Dispositivo fechou a conexão ATT.")
    return packet


def error_reason(packet: bytes, request_opcode: int) -> str:
    if len(packet) == 5 and packet[0] == 0x01:
        return ("ATT Error: para 0x%02X, handle 0x%04X, código 0x%02X" %
                (packet[1], struct.unpack_from("<H", packet, 2)[0], packet[4]))
    return f"ATT resposta inesperada a 0x{request_opcode:02X}: {packet.hex(' ')}"


def att_request(sock: socket.socket, request: bytes, expected: int) -> bytes:
    if request[0] not in (0x04, 0x0A):
        raise RuntimeError("Bloqueio: consulta ATT inesperada")
    if sock.send(request) != len(request):
        raise RuntimeError("Envio ATT incompleto")
    rx = read_pdu(sock)
    if rx[0] != expected:
        raise RuntimeError(error_reason(rx, request[0]))
    return rx


def find_cccd(sock: socket.socket, first: int, last: int) -> int:
    if not 1 <= first <= last <= 0xFFFF:
        raise RuntimeError("Faixa de descritores inválida")
    req = b"\x04" + struct.pack("<HH", first, last)  # ATT Find Information Request
    rx = att_request(sock, req, 0x05)
    if len(rx) < 2 or rx[1] not in (1, 2):
        raise RuntimeError(f"ATT Find Information inválido: {rx.hex(' ')}")
    size = 4 if rx[1] == 1 else 18
    data = rx[2:]
    if not data or len(data) % size:
        raise RuntimeError("Lista de descritores truncada")
    cccds = []
    for i in range(0, len(data), size):
        item = data[i:i + size]
        handle = struct.unpack_from("<H", item)[0]
        desc_uuid = gatt.fmt_uuid(item[2:])
        print(f"  descritor 0x{handle:04X}: {desc_uuid}", flush=True)
        if desc_uuid == "0x2902":
            cccds.append(handle)
    if len(cccds) != 1:
        raise RuntimeError("CCCD 0x2902 não identificado univocamente; nenhuma escrita será feita")
    return cccds[0]


def read_cccd(sock: socket.socket, handle: int) -> bytes:
    rx = att_request(sock, b"\x0A" + struct.pack("<H", handle), 0x0B)
    if len(rx) != 3:
        raise RuntimeError(f"CCCD inesperado: {rx.hex(' ')}")
    value = rx[1:]
    print(f"  CCCD lido: {value.hex(' ')}", flush=True)
    return value


def write_att(sock: socket.socket, handle: int, value: bytes,
              early_notifications: list[bytes]) -> None:
    if handle not in (write_att.cccd_handle, write_att.query_handle):
        raise RuntimeError("Bloqueio: handle não autorizado")
    if handle == write_att.cccd_handle and value not in (b"\x01\x00", write_att.original_cccd):
        raise RuntimeError("Bloqueio: CCCD só pode habilitar notify ou restaurar original")
    if handle == write_att.query_handle:
        if len(value) != 6 or keylinker.decode(value)[0] != 0xB7:
            raise RuntimeError("Bloqueio: permitido somente um frame KeyLinker B7 de leitura")
    req = b"\x12" + struct.pack("<H", handle) + value
    if sock.send(req) != len(req):
        raise RuntimeError("ATT Write Request incompleto")
    for _ in range(MAX_PDUS):
        rx = read_pdu(sock)
        if rx == b"\x13":
            return
        if rx[0] == 0x1B and len(rx) >= 4:
            early_notifications.append(rx)
            continue
        raise RuntimeError(error_reason(rx, 0x12))
    raise RuntimeError("Muitas PDUs sem resposta a ATT Write Request")


def get_notify(sock: socket.socket, expected_handle: int,
               queued: list[bytes]) -> bytes:
    for _ in range(MAX_PDUS):
        rx = queued.pop(0) if queued else read_pdu(sock)
        if rx[0] == 0x1B and len(rx) >= 4:
            handle = struct.unpack_from("<H", rx, 1)[0]
            if handle == expected_handle:
                return rx[3:]
            print(f"  Notificação em outro handle 0x{handle:04X}, ignorada", flush=True)
            continue
        if rx[0] == 0x01:
            raise RuntimeError(error_reason(rx, 0x12))
        print(f"  PDU inesperada enquanto aguarda notificação: {rx.hex(' ')}", flush=True)
    raise RuntimeError("Nenhuma notificação KeyLinker recebida após 12 PDUs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect-only", action="store_true",
                      help="descoberta do CCCD sem nenhuma escrita ATT")
    mode.add_argument("--b7", action="store_true",
                      help="ativa temporariamente notify, transmite uma leitura B7 e restaura")
    parser.add_argument("--connect-timeout", type=float, default=12.0)
    parser.add_argument("--att-timeout", type=float, default=5.0)
    args = parser.parse_args()
    if not 1 <= args.connect_timeout <= 30 or not 1 <= args.att_timeout <= 10:
        parser.error("timeout fora do intervalo 1 a 30/10 segundos")

    codec_test()
    print("=" * 74)
    print("G3V2 — LE DIRETO: KEYLINKER B7 READ PROBE", flush=True)
    print("=" * 74)
    print("0x37, 0x3B, OTA, SCSI e pairing: NÃO IMPLEMENTADOS", flush=True)
    print("Modo:", "SOMENTE DESCOBERTA ATT" if args.inspect_only else
          "UMA LEITURA B7 + ASSINATURA CCCD TEMPORÁRIA", flush=True)
    address = input("Endereço G3V2 APP (MAC): ").strip().upper()
    if not MAC_RE.fullmatch(address):
        print("MAC inválido")
        return 2

    with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                       socket.BTPROTO_L2CAP) as sock:
        sock.settimeout(args.connect_timeout)
        sock.bind((socket.BDADDR_ANY, 0, ATT_CID, socket.BDADDR_LE_PUBLIC))
        start = time.monotonic()
        sock.connect((address, 0, ATT_CID, socket.BDADDR_LE_PUBLIC))
        print(f"Conexão LE Public OK ({1000 * (time.monotonic()-start):.1f} ms)", flush=True)
        sock.settimeout(args.att_timeout)

        print("\n=== VERIFICAÇÃO DE SERVIÇO E CARACTERÍSTICAS ===", flush=True)
        services = gatt.discover_services(sock)
        candidates = [(a, b) for a, b, uid in services if uid == KEYLINKER_SVC]
        if len(candidates) != 1:
            raise RuntimeError("Serviço KeyLinker ausente/duplicado; abortando")
        service_start, service_end = candidates[0]
        chars = gatt.discover_chars(sock, service_start, service_end)
        tx_matches = [c for c in chars if c[3] == KEYLINKER_WRITE and c[2] & 0x08]
        rx_matches = [c for c in chars if c[3] == KEYLINKER_NOTIFY and c[2] & 0x10]
        if len(tx_matches) != 1 or len(rx_matches) != 1:
            raise RuntimeError("Características e1/e2 ou propriedades divergentes")
        tx_handle = tx_matches[0][1]
        rx_handle = rx_matches[0][1]
        following = [c[0] for c in chars if c[0] > rx_matches[0][0]]
        descriptor_end = min(following) - 1 if following else service_end
        desc_start = rx_handle + 1
        print("\n=== DESCRITOR REAL DA NOTIFICAÇÃO ===", flush=True)
        cccd_handle = find_cccd(sock, desc_start, descriptor_end)
        original_cccd = read_cccd(sock, cccd_handle)

        if args.inspect_only:
            print("\nINSPEÇÃO CONCLUÍDA: nenhum pacote de escrita ATT enviado.", flush=True)
            return 0

        # The only remotely mutable value is the connection's CCCD (notification
        # subscription). Restoration is attempted on exit if we changed it.
        write_att.cccd_handle = cccd_handle
        write_att.query_handle = tx_handle
        write_att.original_cccd = original_cccd
        changed_cccd = False
        queued: list[bytes] = []
        try:
            if original_cccd != b"\x01\x00":
                print("\nHabilitando notificação via CCCD verificado (somente assinatura)...",
                      flush=True)
                write_att(sock, cccd_handle, b"\x01\x00", queued)
                changed_cccd = True
            else:
                print("\nNotificações já habilitadas nesta conexão.", flush=True)

            session = keylinker.KeyLinkerReadSession()
            plain, wire, number, state, rnd = keylinker.build_read_request(
                session, 0xB7, 0, None
            )
            if plain[0] != 0xB7 or len(plain) != 6:
                raise RuntimeError("Guarda KeyLinker B7 falhou")
            print(f"\nLeitura B7 #{number}: TX plain {keylinker.hx(plain)}", flush=True)
            print(f"              TX wire  {keylinker.hx(wire)}", flush=True)
            moment = time.monotonic()
            write_att(sock, tx_handle, wire, queued)
            data = get_notify(sock, rx_handle, queued)
            elapsed = (time.monotonic() - moment) * 1000
            print(f"Notificação e2: {keylinker.hx(data)} ({elapsed:.1f} ms)", flush=True)
            plain_rx = keylinker.decode(data)
            print(f"KeyLinker RX:   {keylinker.hx(plain_rx)}", flush=True)
            keylinker.validate_plain_response(plain_rx, "Lamplight")
            if plain_rx[0] != 0xB7:
                raise RuntimeError(f"Resposta foi opcode 0x{plain_rx[0]:02X}, não B7")
            logical_len, total_native, chunks = keylinker.first_frame_total_native(plain_rx)
            if chunks != 1 or total_native != len(data):
                print("Resposta válida, mas fragmentada: não enviarei continuação neste teste.",
                      flush=True)
                return 3
            logical = keylinker.reconstruct_logical([plain_rx], logical_len)
            if len(logical) != logical_len:
                raise RuntimeError("Comprimento lógico B7 inconsistente")
            print(f"SUCESSO: B7 LampLight logical[{len(logical)}]: {keylinker.hx(logical)}",
                  flush=True)
            return 0
        finally:
            if changed_cccd:
                try:
                    print("\nRestaurando CCCD ao valor original...", flush=True)
                    write_att(sock, cccd_handle, original_cccd, [])
                    print("CCCD restaurado.", flush=True)
                except Exception as exc:
                    print(f"Não foi possível confirmar a restauração: {exc}", flush=True)
                    print("O socket será fechado, encerrando a conexão.", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (socket.timeout, OSError, RuntimeError, ValueError) as exc:
        print(f"FALHA: {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
    except (EOFError, KeyboardInterrupt):
        print("\nInterrompido; socket será fechado.", flush=True)
        sys.exit(130)
