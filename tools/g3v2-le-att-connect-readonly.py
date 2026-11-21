#!/usr/bin/env python3
"""One-shot direct Bluetooth LE ATT (L2CAP CID 4) connectivity probe for G3 V2.

No GATT/ATT PDU sent, no KeyLinker command, no pairing request, no retry.
The controller address is prompted at run time and never written to a file.

Requires Python 3.14+ on Linux, with AF_BLUETOOTH/BTPROTO_L2CAP and
BDADDR_LE_PUBLIC. This tests the direct LE socket pathway, not Bleak
or bluetoothd Device1.Connect automatic bearer selection.
"""
from __future__ import annotations

import argparse
import errno
import re
import socket
import sys
import time

ATT_CID = 4
MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=12.0,
                        help="timeout de conexão em segundos (padrão 12)")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 30:
        parser.error("--timeout deve estar entre 1 e 30 segundos")

    print("=" * 72)
    print("G3V2 — TESTE LE ATT DIRETO (SEM COMANDOS AO CONTROLE)")
    print("=" * 72)
    print("Uma tentativa; LE Public; L2CAP CID 4; nenhum pacote ATT enviado.")
    print("Sem pairing, sem KeyLinker 0x37/0x3B, OTA ou SCSI.")
    print("O MAC será usado somente em memória.")

    address = input("Endereço do G3V2 APP (MAC): ").strip().upper()
    if not MAC_RE.fullmatch(address):
        print("ERRO: endereço Bluetooth inválido.")
        return 2

    for key in ("AF_BLUETOOTH", "BTPROTO_L2CAP", "BDADDR_LE_PUBLIC"):
        if not hasattr(socket, key):
            print(f"ERRO: socket.{key} indisponível.")
            return 2

    started = time.monotonic()
    try:
        with socket.socket(socket.AF_BLUETOOTH,
                           socket.SOCK_SEQPACKET,
                           socket.BTPROTO_L2CAP) as sock:
            sock.settimeout(args.timeout)
            print("Abrindo canal ATT local (CID=4; origem LE Public)...")
            sock.bind((socket.BDADDR_ANY, 0, ATT_CID,
                       socket.BDADDR_LE_PUBLIC))
            print("Conectando diretamente por LE Public...")
            sock.connect((address, 0, ATT_CID,
                          socket.BDADDR_LE_PUBLIC))
            elapsed = (time.monotonic() - started) * 1000
            peer = sock.getpeername()
            print(f"SUCESSO: conexão L2CAP/ATT estabelecida em {elapsed:.1f} ms")
            print(f"Destino: CID={peer[2]}, tipo={peer[3]} (LE Public=1)")
            print("Nenhuma chamada send/sendall, recv ou alteração GATT realizada.")
            print("Fechando socket.")
            return 0
    except socket.timeout:
        print(f"FALHA: timeout após {args.timeout:.1f}s, sem conexão ATT.")
        return 1
    except OSError as exc:
        print(f"FALHA: {type(exc).__name__}, errno={exc.errno}")
        print(f"Detalhe: {exc.strerror or str(exc)}")
        if exc.errno == errno.EADDRINUSE:
            print("O CID ATT local pode estar ocupado; não forçar outro processo.")
        elif exc.errno in (errno.EACCES, errno.EPERM):
            print("Permissão recusada; não executar com sudo antes de avaliar.")
        elif exc.errno == errno.EINVAL:
            print("Endereço/CID não aceito; revisar o bind L2CAP e o kernel.")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nInterrompido; nenhuma configuração foi alterada.")
        sys.exit(130)
