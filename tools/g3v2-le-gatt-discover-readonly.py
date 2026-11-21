#!/usr/bin/env python3
"""G3V2: discover BLE GATT services and characteristic declarations via direct LE ATT.

One explicit LE Public connection through Linux L2CAP fixed ATT CID 4.
ATT reads ONLY: Read By Group Type (0x10), Read By Type (0x08).
No characteristic value reads, CCCD writes, subscriptions, KeyLinker frames,
controller setting writes, pairing requests, or automatic reconnects.
"""
from __future__ import annotations

import argparse
import errno
import re
import socket
import struct
import sys
import time
import uuid

CID_ATT = 4
UUID_KEYLINKER = "d7f010e0-660d-46e9-96c3-19c4148bdab5"
MAC_PATTERN = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
MAX_QUERIES = 40
ATT_ERROR = {0x01: "Invalid Handle", 0x05: "Insufficient Authentication",
             0x06: "Request Not Supported", 0x08: "Insufficient Authorization",
             0x0A: "Attribute Not Found", 0x0F: "Insufficient Encryption",
             0x10: "Unsupported Group Type"}


def u16(data: bytes) -> int:
    return struct.unpack('<H', data)[0]


def fmt_uuid(value: bytes) -> str:
    if len(value) == 2:
        return f"0x{u16(value):04X}"
    if len(value) == 16:
        # Bluetooth ATT transmits the entire 128-bit UUID in reverse octet order.
        return str(uuid.UUID(bytes=value[::-1]))
    raise ValueError(f"UUID com comprimento inválido: {len(value)}")


def exchange(sock: socket.socket, tx: bytes, expected: int) -> bytes | None:
    if tx[0] not in (0x10, 0x08):
        raise RuntimeError("Bloqueio: opcode não é consulta ATT permitida")
    n = sock.send(tx)
    if n != len(tx):
        raise RuntimeError("Envio ATT incompleto")
    rx = sock.recv(512)
    if not rx:
        raise RuntimeError("Conexão ATT encerrada pelo dispositivo")
    if rx[0] == 0x01:
        if len(rx) != 5 or rx[1] != tx[0]:
            raise RuntimeError(f"ATT Error Response inesperada: {rx.hex(' ')}")
        err = rx[4]
        if err == 0x0A:
            return None  # Attribute Not Found: fim normal da descoberta.
        raise RuntimeError(f"Erro ATT 0x{err:02X}: {ATT_ERROR.get(err, 'desconhecido')}")
    if rx[0] != expected:
        raise RuntimeError(f"Resposta ATT inesperada para 0x{tx[0]:02X}: {rx.hex(' ')}")
    return rx


def records(rx: bytes, allowed: set[int]):
    if len(rx) < 2 or rx[1] not in allowed:
        raise RuntimeError(f"Tamanho de registro ATT inesperado: {rx.hex(' ')}")
    size = rx[1]
    data = rx[2:]
    if not data or len(data) % size:
        raise RuntimeError(f"Pacote ATT truncado/inconsistente: {rx.hex(' ')}")
    return [data[i:i+size] for i in range(0, len(data), size)]


def discover_services(sock: socket.socket):
    result = []
    start = 1
    for attempt in range(MAX_QUERIES):
        req = b'\x10' + struct.pack('<HHH', start, 0xFFFF, 0x2800)
        rx = exchange(sock, req, 0x11)
        if rx is None:
            break
        entries = records(rx, {6, 20})
        final = 0
        for item in entries:
            begin, end = struct.unpack_from('<HH', item)
            if not start <= begin <= end <= 0xFFFF or (final and begin <= final):
                raise RuntimeError("Faixa de serviço ATT inconsistente")
            name = fmt_uuid(item[4:])
            result.append((begin, end, name))
            print(f"  serviço 0x{begin:04X}-0x{end:04X}: {name}")
            final = end
        if final == 0xFFFF:
            break
        if final < start:
            raise RuntimeError("Descoberta sem progresso")
        start = final + 1
    else:
        raise RuntimeError("Limite de consultas de serviços atingido")
    return result


def flags(props: int) -> str:
    options = ((0x02, 'read'), (0x04, 'write-no-response'),
               (0x08, 'write'), (0x10, 'notify'), (0x20, 'indicate'))
    return ','.join(name for bit, name in options if props & bit) or 'outras'


def discover_chars(sock: socket.socket, service_start: int, service_end: int):
    result = []
    start = service_start
    for attempt in range(MAX_QUERIES):
        if start > service_end:
            break
        req = b'\x08' + struct.pack('<HHH', start, service_end, 0x2803)
        rx = exchange(sock, req, 0x09)
        if rx is None:
            break
        entries = records(rx, {7, 21})
        previous = start-1
        for item in entries:
            declaration = u16(item[:2])
            props = item[2]
            value_handle = u16(item[3:5])
            name = fmt_uuid(item[5:])
            if not previous < declaration < value_handle <= service_end:
                raise RuntimeError("Declaração de característica inconsistente")
            result.append((declaration, value_handle, props, name))
            print(f"  declaração 0x{declaration:04X} valor=0x{value_handle:04X} "
                  f"{name} props=0x{props:02X} ({flags(props)})")
            previous = declaration
        if previous == 0xFFFF:
            break
        start = previous + 1
    else:
        raise RuntimeError("Limite de consultas de características atingido")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect-timeout', type=float, default=12.0)
    parser.add_argument('--att-timeout', type=float, default=4.0)
    parser.add_argument('--services-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.connect_timeout <= 30 or not 1 <= args.att_timeout <= 10:
        parser.error('timeouts fora do intervalo permitido')

    print('=== G3V2 GATT READ-ONLY / LE PUBLIC / CID 4 ===', flush=True)
    print('Somente consultas ATT 0x10 e 0x08; sem KeyLinker, OTA ou gravações.', flush=True)
    address = input('Endereço do G3V2 APP (MAC): ').strip().upper()
    if not MAC_PATTERN.fullmatch(address):
        print('Endereço inválido.')
        return 2

    started = time.monotonic()
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                           socket.BTPROTO_L2CAP) as sock:
            sock.settimeout(args.connect_timeout)
            sock.bind((socket.BDADDR_ANY, 0, CID_ATT, socket.BDADDR_LE_PUBLIC))
            print('Conectando por LE Public (uma tentativa)...', flush=True)
            sock.connect((address, 0, CID_ATT, socket.BDADDR_LE_PUBLIC))
            print(f'Conexão LE: OK ({(time.monotonic()-started)*1000:.1f} ms)', flush=True)
            sock.settimeout(args.att_timeout)
            print('\n=== SERVIÇOS PRIMÁRIOS ===', flush=True)
            services = discover_services(sock)
            print(f'Serviços descobertos: {len(services)}')
            matches = [(a,b) for a,b,u in services if u.lower() == UUID_KEYLINKER]
            if not matches:
                print('Serviço KeyLinker d7f010e0: NÃO ENCONTRADO.')
                print('Nenhuma consulta a características foi realizada.')
                return 2
            print('Serviço KeyLinker d7f010e0: ENCONTRADO.')
            if args.services_only:
                return 0
            print('\n=== CARACTERÍSTICAS KEYLINKER (DECLARAÇÕES, SEM LER VALORES) ===')
            chars = discover_chars(sock, *matches[0])
            print(f'Características descobertas: {len(chars)}')
            for prefix in ('d7f010e1', 'd7f010e2'):
                found = [c for c in chars if c[3].startswith(prefix)]
                print(f'  {prefix}: {"ENCONTRADA" if found else "NÃO ENCONTRADA"}')
            return 0
    except socket.timeout:
        print('FALHA: timeout na conexão ou durante consulta ATT.')
        return 1
    except OSError as exc:
        print(f'FALHA SOCKET: errno={exc.errno}, {exc.strerror or exc}')
        if exc.errno in (errno.EACCES, errno.EPERM):
            print('Não tente sudo antes de avaliarmos o resultado.')
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f'FALHA PROTOCOLO: {exc}')
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print('\nInterrompido; socket encerrado.')
        sys.exit(130)
