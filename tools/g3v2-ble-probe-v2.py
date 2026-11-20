#!/usr/bin/env python3
"""
Machenike G3 V2 — BLE (KeyLinker GATT) READ-ONLY probe.

SCRIPT VERSION: v2   (file: g3v2-ble-probe-v2.py)

Purpose:
  Check whether the controller's BLE configuration peripheral ("G3V2 APP",
  KeyLinker GATT service) answers the SAME read requests already proven over
  USB HID, and measure BLE round-trip latency. This is the first step toward
  a wireless (BLE) lighting path.

Hypothesis being tested (NOT yet proven):
  The KeyLinker inner frame (`opcode len state rand index crc`, then the
  XOR/MIX encode pass) is written directly to the GATT write characteristic,
  and the response frame arrives as a GATT notification, i.e. the USB-only
  outer envelope `80 F9 seq flags len` is simply absent over BLE.

Modes:
  --enumerate-only   scan + connect + list GATT services/characteristics.
                     Sends NOTHING and subscribes to nothing.
                     Connection is retried with a freshly scanned BLEDevice
                     because KeyLinker-family peripherals may advertise
                     intermittently on Linux/BlueZ.
  (default)          replay the official 11-read warm-up (read requests only),
                     then N extra B7 LampLight reads for latency statistics.

Safety / scope:
  - READ requests only: opcodes B0/B1/B2/B3/B4/B7/BB. A guard refuses any
    other opcode before a write to the characteristic.
  - No 0x37 / 0x3B writes, no OTA bit, no SCSI, no pairing/bonding requests.
  - Does not need USB: the controller can be on battery with the dongle in.

Requirements:
  python3 -m venv ~/.venvs/g3v2 && ~/.venvs/g3v2/bin/pip install bleak
  Must live in the same tools/ directory as g3v2-warmup-readonly.py
  (codec + KeyLinker read-session reconstruction are imported from it).

Usage:
  ~/.venvs/g3v2/bin/python g3v2-ble-probe-v2.py --enumerate-only
  ~/.venvs/g3v2/bin/python g3v2-ble-probe-v2.py --reads 20
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import statistics
import shutil
import sys
import time
from pathlib import Path

VERSION = "v2"

HERE = Path(__file__).resolve().parent
BASE_TOOL = HERE / "g3v2-warmup-readonly.py"

SERVICE_UUID = "d7f010e0-660d-46e9-96c3-19c4148bdab5"
WRITE_PREFIX = "d7f010e1"
NOTIFY_PREFIX = "d7f010e2"

ALLOWED_OPCODES = {0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB7, 0xBB}


def load_base():
    spec = importlib.util.spec_from_file_location("g3v2_base", BASE_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_TOOL}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = load_base()
hx = g.hx


def codec_selftest() -> None:
    known0 = bytes.fromhex("BB 06 00 29 00 27")
    wire0 = bytes.fromhex("C0 B3 40 70 99 96")
    known1 = bytes.fromhex("BB 06 81 23 01 B9")
    wire1 = bytes.fromhex("C2 37 F2 B7 A4 28")
    if g.encode(known0) != wire0 or g.encode(known1) != wire1:
        raise RuntimeError("KeyLinker codec self-test failed")


class Link:
    """One GATT write -> one notification exchange."""

    def __init__(self, client, wchar, queue, use_response: bool, timeout: float):
        self.client = client
        self.wchar = wchar
        self.queue = queue
        self.use_response = use_response
        self.timeout = timeout
        self.first = True

    def _drain(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()

    async def exchange(self, wire: bytes) -> tuple[float, bytes]:
        attempts = 2 if self.first else 1
        last_err: Exception | None = None
        for attempt in range(attempts):
            self._drain()
            t0 = time.perf_counter()
            await self.client.write_gatt_char(
                self.wchar, wire, response=self.use_response
            )
            try:
                t1, data = await asyncio.wait_for(
                    self.queue.get(), self.timeout
                )
            except asyncio.TimeoutError as exc:
                last_err = exc
                if attempt + 1 < attempts:
                    print("    (sem resposta; reenviando o MESMO frame uma vez)")
                continue
            self.first = False
            return (t1 - t0) * 1000.0, data
        raise TimeoutError(
            f"sem notificação em {self.timeout:.1f}s para {hx(wire)}"
        ) from last_err


async def native_read(
    link: Link,
    session,
    label: str,
    command: int,
    subcommand: int | None,
    verbose: bool = True,
) -> tuple[bytes, list[float]]:
    selector = 0
    plains: list[bytes] = []
    rtts: list[float] = []
    logical_len = total = count = None
    actual = 0

    while True:
        plain_req, wire_req, number, state, rnd = g.build_read_request(
            session, command, selector, subcommand
        )
        if plain_req[0] not in ALLOWED_OPCODES:
            raise RuntimeError(
                f"read-only guard: opcode {plain_req[0]:02X} not allowed"
            )

        rtt, data = await link.exchange(wire_req)
        rtts.append(rtt)

        if verbose:
            print(
                f"  req#{number:02d} sel={selector} state={state:02X} "
                f"rand={rnd:02X}  TX {hx(wire_req)}"
            )
            print(f"    RX raw ({len(data)}B, {rtt:6.2f} ms): {hx(data)}")

        try:
            plain_rx = g.decode(data)
            g.validate_plain_response(plain_rx, label)
        except Exception as exc:  # noqa: BLE001 - report raw bytes for analysis
            print(f"    DECODE/CRC FALHOU ({exc}); bytes brutos acima.")
            raise

        if verbose:
            print(f"    Plain RX: {hx(plain_rx)}")

        plains.append(plain_rx)
        actual += len(data)

        if selector == 0:
            logical_len, total, count = g.first_frame_total_native(plain_rx)
            if count < 1 or count > 16:
                raise RuntimeError(f"{label}: packet_count inválido {count}")

        if len(plains) >= count:
            break
        selector += 1

    if actual != total:
        raise RuntimeError(
            f"{label}: total nativo {actual} != esperado {total}"
        )
    logical = g.reconstruct_logical(plains, logical_len)
    if verbose:
        print(f"  RESULT {label}: logical[{len(logical)}] = {hx(logical)}")
    return logical, rtts


def adv_name(device, adv) -> str:
    return (getattr(adv, "local_name", None) or device.name or "").strip()


async def find_device(args):
    from bleak import BleakScanner

    if args.address:
        dev = await BleakScanner.find_device_by_address(
            args.address, timeout=args.scan_timeout
        )
        if dev is None:
            raise RuntimeError(f"dispositivo {args.address} não encontrado")
        return dev

    print(f"Escaneando BLE por {args.scan_timeout:.0f}s ...")
    found = await BleakScanner.discover(
        timeout=args.scan_timeout, return_adv=True
    )
    matches = []
    for dev, adv in found.values():
        if adv_name(dev, adv).lower().startswith("g3v2"):
            matches.append((dev, adv))

    print(f"Dispositivos BLE vistos: {len(found)}; candidatos G3V2: {len(matches)}")
    for dev, adv in matches:
        print(
            f"  - {adv_name(dev, adv)!r} {dev.address} "
            f"rssi={getattr(adv, 'rssi', '?')} "
            f"uuids={list(getattr(adv, 'service_uuids', []) or [])}"
        )
    if not matches:
        raise RuntimeError(
            "nenhum 'G3V2 APP' visto. Controle ligado? Tente outro modo de "
            "inicialização (ex.: X+HOME) e rode de novo."
        )
    if len(matches) > 1:
        raise RuntimeError("mais de um candidato; use --address")
    return matches[0][0]



async def bluetoothctl_info(address: str) -> str:
    """Read-only BlueZ state snapshot for diagnostics; changes nothing."""
    if not sys.platform.startswith("linux") or shutil.which("bluetoothctl") is None:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "info", address,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 5.0)
        return out.decode("utf-8", "replace").strip()
    except (OSError, asyncio.TimeoutError):
        return ""


async def connect_with_retry(device, args):
    """Connect without sending any application-level GATT request."""
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError

    target = device
    last_error: Exception | None = None

    for attempt in range(1, args.connect_attempts + 1):
        print(
            f"Conexão GATT tentativa {attempt}/{args.connect_attempts} "
            f"(timeout {args.connect_timeout:.0f}s) ..."
        )
        client = BleakClient(target, timeout=args.connect_timeout)
        try:
            await client.connect()
            print("Link BLE + resolução GATT: OK")
            return client
        except (BleakError, asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            detail = str(exc).strip() or "(sem mensagem)"
            print(f"  falhou: {type(exc).__name__}: {detail}")
            try:
                await client.disconnect()
            except Exception:
                pass

        if attempt < args.connect_attempts:
            await asyncio.sleep(1.0)
            print("  renovando anúncio BLE antes da próxima tentativa ...")
            try:
                fresh = await BleakScanner.find_device_by_address(
                    device.address, timeout=min(6.0, args.scan_timeout)
                )
                if fresh is not None:
                    target = fresh
                    print("  anúncio renovado: OK")
                else:
                    print("  anúncio não reapareceu nesta janela; tentando novamente.")
            except (BleakError, OSError) as exc:
                print(f"  refresh do anúncio falhou: {type(exc).__name__}: {exc}")

    info = await bluetoothctl_info(device.address)
    if info:
        print("\n=== ESTADO BLUEZ APÓS FALHA ===")
        print(info)
        if any(
            line.strip().lower() == "connected: yes"
            for line in info.splitlines()
        ):
            print(
                "\nBlueZ ainda mostra Connected: yes. Isso é compatível com "
                "uma conexão BLE já ocupada/presa; o probe NÃO vai "
                "desconectá-la automaticamente."
            )

    if last_error is not None:
        raise last_error
    raise TimeoutError("conexão BLE falhou sem exceção detalhada")


def pick_characteristics(client):
    svc = None
    for s in client.services:
        if s.uuid.lower() == SERVICE_UUID:
            svc = s
            break
    chars = (
        list(svc.characteristics)
        if svc is not None
        else [c for s in client.services for c in s.characteristics]
    )
    wchar = next((c for c in chars if c.uuid.lower().startswith(WRITE_PREFIX)), None)
    nchar = next((c for c in chars if c.uuid.lower().startswith(NOTIFY_PREFIX)), None)
    return svc, wchar, nchar


def print_gatt(client) -> None:
    print("\n=== GATT ===")
    mtu = getattr(client, "mtu_size", None)
    print("MTU negociado:", mtu)
    for s in client.services:
        mark = "  <== KeyLinker" if s.uuid.lower() == SERVICE_UUID else ""
        print(f"[serviço] {s.uuid}{mark}")
        for c in s.characteristics:
            print(f"    [char] {c.uuid}  props={list(c.properties)}  handle={c.handle}")


def stats_line(name: str, values: list[float]) -> str:
    if not values:
        return f"{name:<30} (sem amostras)"
    return (
        f"{name:<30} n={len(values):<3} min={min(values):7.2f}  "
        f"mediana={statistics.median(values):7.2f}  "
        f"média={statistics.mean(values):7.2f}  máx={max(values):7.2f}  ms"
    )


async def run(args) -> int:
    try:
        import bleak  # noqa: F401 - dependency check only
    except ImportError:
        print(
            "bleak não instalado. Rode:\n"
            "  python3 -m venv ~/.venvs/g3v2 && ~/.venvs/g3v2/bin/pip install bleak"
        )
        return 1

    codec_selftest()
    print("=" * 78)
    print(f"G3V2 — PROBE BLE SOMENTE LEITURA   [{VERSION}]")
    print("=" * 78)
    print("Codec self-test: OK")
    print("Writes 0x37 / 0x3B / OTA / SCSI: NENHUM")

    device = await find_device(args)
    print(f"\nConectando em {device.address} ...")

    client = await connect_with_retry(device, args)
    try:
        print("Conectado:", client.is_connected)
        print_gatt(client)

        svc, wchar, nchar = pick_characteristics(client)
        print("\nServiço KeyLinker encontrado:", svc is not None)
        print("Característica de escrita  :", wchar.uuid if wchar else None)
        print("Característica de notify   :", nchar.uuid if nchar else None)

        if args.enumerate_only:
            print("\n--enumerate-only: nenhum frame KeyLinker foi enviado. Fim.")
            return 0
        if wchar is None or nchar is None:
            print("\nNão achei as características e/ou o serviço; abortando.")
            return 2

        props = list(wchar.properties)
        use_response = "write" in props
        if not use_response and "write-without-response" not in props:
            print("Característica de escrita sem propriedade de escrita?", props)
            return 2
        print("Escrita com resposta (ATT):", use_response)

        queue: asyncio.Queue = asyncio.Queue()

        def on_notify(_sender, data: bytearray) -> None:
            queue.put_nowait((time.perf_counter(), bytes(data)))

        await client.start_notify(nchar, on_notify)
        link = Link(client, wchar, queue, use_response, args.timeout)
        session = g.KeyLinkerReadSession()

        lamp = None
        warm_rtts: list[float] = []
        print("\n=== WARM-UP OFICIAL (11 LEITURAS) VIA BLE ===")
        for i, (label, command, sub) in enumerate(g.OFFICIAL_WARMUP, start=1):
            print("\n" + "-" * 78)
            print(f"{i:02d}/{len(g.OFFICIAL_WARMUP):02d} {label} (cmd={command:02X})")
            logical, rtts = await native_read(link, session, label, command, sub)
            warm_rtts.extend(rtts)
            if label == "Lamplight":
                lamp = logical

        print("\n=== LATÊNCIA: LEITURAS B7 EXTRAS ===")
        lat: list[float] = []
        for _ in range(args.reads):
            _, rtts = await native_read(
                link, session, "Lamplight", 0xB7, None, verbose=False
            )
            lat.extend(rtts)

        await client.stop_notify(nchar)
    finally:
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass

    print("\n" + "=" * 78)
    print(f"RESULTADO   [{VERSION}]")
    print("=" * 78)
    print("B7 LampLight via BLE :", hx(lamp) if lamp is not None else None)
    print("(via USB foi: 00 FF FF 00 00 FF)")
    print(stats_line("warm-up (todas as leituras)", warm_rtts))
    print(stats_line("B7 leituras extras", lat))
    print(
        "\nLeitura: resposta com CRC válido = a hipótese do envelope sem '80 F9' "
        "se confirma\npara leituras. Latência BLE depende do intervalo de conexão "
        "negociado."
    )
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=f"G3V2 BLE read-only probe {VERSION}")
    ap.add_argument("--address", help="endereço BLE do 'G3V2 APP' (opcional)")
    ap.add_argument("--scan-timeout", type=float, default=12.0)
    ap.add_argument("--connect-timeout", type=float, default=20.0)
    ap.add_argument("--connect-attempts", type=int, default=3,
                    help="tentativas GATT com refresh do anúncio (padrão 3)")
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="espera por notificação, por requisição (s)")
    ap.add_argument("--reads", type=int, default=20,
                    help="leituras B7 extras para latência (padrão 20)")
    ap.add_argument("--enumerate-only", action="store_true",
                    help="só lista o GATT; não envia nem assina nada")
    args = ap.parse_args()
    if args.reads < 3:
        raise SystemExit("--reads deve ser >= 3")
    if args.connect_attempts < 1 or args.connect_attempts > 5:
        raise SystemExit("--connect-attempts deve estar entre 1 e 5")

    try:
        rc = asyncio.run(run(args))
    except KeyboardInterrupt:
        rc = 130
    except Exception as exc:  # noqa: BLE001
        print(f"\nERRO: {type(exc).__name__}: {exc}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()