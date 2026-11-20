#!/usr/bin/env python3
"""
Machenike G3 V2 — official MixDrive warm-up replay (READ REQUESTS ONLY).

Replays the success-path initialization reads recovered from:
  GamepadConfigInitializer -> GamepadHardwareLoader -> GamepadDeviceManager
using the statically reconstructed KeyLinker builders.

It sends only read/query requests:
  B0/B1/B2/B3/B4/B7/BB
It NEVER calls the 0x3B CustomData writer and NEVER sets the OTA bit.

The script dynamically follows native response fragmentation. Every extra
continuation request advances the reconstructed KeyLinker state and MSVC
rand() stream, exactly like another native read builder invocation would.

No firmware/SCSI commands are present.
"""

from __future__ import annotations

import glob
import os
import pathlib
import select
import time

XOR40 = bytes.fromhex(
    "6b7c058ef791832950dba2713b4fbdc4"
    "9d63e86eec673a434eb1fbbc5c09707d"
    "aec8e9d76f959de4"
)
MIX16 = bytes.fromhex("52ce615ab758eaa0693c9bc6bffd8b00")
CRC = bytes.fromhex(
    "0079f28b334ac1b8661f94ed552ca7de"
    "ccb53e47ff860d74aad3582199e06b12"
    "4f36bdc47c058ef72950dba21a63e891"
    "83fa7108b0c9423be59c176ed6af245d"
    "9ee76c15add45f26f8810a73cbb23940"
    "522ba0d9611893ea344dc6bf077ef58c"
    "d1a8235ae29b1069b7ce453c84fd760f"
    "1d64ef962e57dca57b0289f04831bac3"
    "eb921960d8a12a538df47f06bec74c35"
    "275ed5ac146de69f4138b3ca720b80f9"
    "a4dd562f97ee651cc2bb3049f188037a"
    "68119ae35b22a9d00e77fc853d44cfb6"
    "750c87fe463fb4cd136ae1982059d2ab"
    "b9c04b328af37801dfa62d54ec951e67"
    "3a43c8b10970fb825c25aed76f169de4"
    "f68f047dc5bc374e90e9621ba3da5128"
)

REPORT_LEN = 64

# (label, native command, optional B0 subcommand)
OFFICIAL_WARMUP = [
    ("BeforeKeyChangeList", 0xB0, 0x03),
    ("SupportTurboList",    0xB0, 0x04),
    ("CurrentTurbo",        0xB4, None),
    ("Rocker",              0xB1, None),
    ("CustomData #1",       0xBB, None),
    ("Trigger",             0xB2, None),
    ("Motor",               0xB3, None),
    ("CustomData #2",       0xBB, None),
    ("Lamplight",           0xB7, None),
    ("CustomData #3",       0xBB, None),
    ("CustomData FINAL",    0xBB, None),
]


def hx(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c = CRC[(b ^ c) & 0xFF]
    return c


def encode(plain: bytes) -> bytes:
    plain = bytes(plain)
    n = len(plain)
    if n < 5:
        raise ValueError("KeyLinker frame too short")

    out = bytearray(n)
    b2, b3 = plain[2], plain[3]
    out[0] = plain[0] ^ ((b2 + b3 + 0x6E) & 0xFF)
    out[1] = plain[1] ^ ((b2 + b3 - 0x57) & 0xFF)
    out[2] = ((b2 + 0x53) & 0xFF) ^ b3
    out[3] = ((b3 - 0x41) & 0xFF) ^ 0xDB

    for i in range(4, n - 1):
        out[i] = plain[i] ^ ((b3 - MIX16[i - 4] + b2) & 0xFF)

    out[-1] = plain[-1]
    start = 20 if (out[3] & 0x02) else 0
    return bytes(out[i] ^ XOR40[start + i] for i in range(n))


def decode(wire: bytes) -> bytes:
    wire = bytes(wire)
    n = len(wire)
    if n < 5:
        raise ValueError("KeyLinker frame too short")

    start = 20 if (wire[3] & 0x02) == 0 else 0
    out = bytearray(wire[i] ^ XOR40[start + i] for i in range(n))

    b3 = ((out[3] ^ 0xDB) + 0x41) & 0xFF
    b2 = ((b3 ^ out[2]) - 0x53) & 0xFF
    out[3], out[2] = b3, b2

    s = (b2 + b3) & 0xFF
    out[0] ^= (s + 0x6E) & 0xFF
    out[1] ^= (s - 0x57) & 0xFF

    for i in range(4, n - 1):
        out[i] ^= (b2 - MIX16[i - 4] + b3) & 0xFF

    return bytes(out)


class KeyLinkerReadSession:
    """
    Host-side state reconstructed from KeyLinkerState (0x1800014C0).

    Returned state byte is:
      bit 7    = E67C
      bits 6:3 = (E678 >> 2) & 0x0F
      bits 2:0 = E68C

    Cold G3V2/MixDrive state has E678=0, E67C=0, E68C=0.
    Each read invocation toggles E67C and increments E68C modulo 8.

    MSVC/UCRT rand() defaults to seed/state 1 when srand() was never called.
    Package-wide static analysis found no srand, and the first observed values
    0x29, 0x23 match this stream.
    """

    def __init__(self) -> None:
        self.e678 = 0
        self.e67c = 0
        self.e68c = 0
        self.rand_state = 1
        self.read_count = 0

    def current_state_byte(self) -> int:
        return (
            ((self.e67c & 1) << 7)
            | (((self.e678 >> 2) & 0x0F) << 3)
            | (self.e68c & 7)
        )

    def next_rand_low(self) -> int:
        self.rand_state = (
            (214013 * self.rand_state + 2531011) & 0xFFFFFFFF
        )
        return ((self.rand_state >> 16) & 0x7FFF) & 0xFF

    def consume_read(self) -> tuple[int, int, int]:
        number = self.read_count + 1
        state = self.current_state_byte()
        rnd = self.next_rand_low()

        self.e67c = 1 if self.e67c == 0 else 0
        self.e68c = (self.e68c + 1) & 7
        self.read_count = number
        return number, state, rnd


def build_read_request(
    session: KeyLinkerReadSession,
    command: int,
    selector: int,
    subcommand: int | None,
) -> tuple[bytes, bytes, int, int, int]:
    number, state, rnd = session.consume_read()

    if command == 0xB0:
        if subcommand is None:
            raise ValueError("B0 requires subcommand")
        plain = bytearray(
            [0xB0, 0x07, state, rnd, selector & 0xFF, subcommand & 0xFF]
        )
    else:
        plain = bytearray(
            [command & 0xFF, 0x06, state, rnd, selector & 0xFF]
        )

    plain.append(crc8(plain))
    wire = encode(plain)
    return bytes(plain), wire, number, state, rnd


def discover() -> tuple[str, str]:
    candidates = []
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        pp = pathlib.Path(path)
        try:
            uevent = (pp / "device/uevent").read_text(errors="replace")
            real = str((pp / "device").resolve())
        except OSError:
            continue

        if "0000413D:00002131" not in uevent.upper():
            continue
        if ":1.1/" not in real:
            continue

        candidates.append(("/dev/" + pp.name, real))

    if len(candidates) != 1:
        raise RuntimeError(
            "expected exactly one 413D:2131 MI_01 hidraw; "
            f"found {candidates}"
        )
    return candidates[0]


def drain(fd: int) -> int:
    count = 0
    while True:
        try:
            data = os.read(fd, REPORT_LEN)
            if not data:
                break
            count += 1
        except BlockingIOError:
            break
    return count


def send_outer(fd: int, seq: int, payload: bytes) -> None:
    if not 1 <= seq <= 255:
        raise ValueError("seq outside 1..255")
    if len(payload) > 20:
        raise ValueError("outer payload > 20")

    report = bytes([0x80, 0xF9, seq, 0x00, len(payload)]) + payload
    report += bytes(REPORT_LEN - len(report))

    wrote = os.write(fd, report)
    if wrote != REPORT_LEN:
        raise RuntimeError(f"short HID write: {wrote}/{REPORT_LEN}")

    print(f"    HID TX seq={seq:02X}: {hx(report[:5 + len(payload)])}")


def receive_outer(
    fd: int, seq: int, timeout: float = 3.0
) -> bytes:
    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timeout waiting HID seq={seq:02X}")

        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue

        try:
            raw = os.read(fd, REPORT_LEN)
        except BlockingIOError:
            continue

        if len(raw) < 5:
            continue

        print(
            "    HID RX raw:",
            hx(raw[: min(len(raw), 32)]),
            "..." if len(raw) > 32 else "",
        )

        if raw[0] != 0x81 or raw[1] != 0xF9:
            continue
        if raw[2] != seq:
            continue

        flags = raw[3]
        length = raw[4]
        if 5 + length > len(raw):
            raise RuntimeError("truncated HID response")

        payload = bytes(raw[5 : 5 + length])

        # Warm-up reads expect the generic data path, not write ACKs.
        if flags & 1:
            print(
                f"    Ignorando ACK inesperado seq={seq:02X}: {hx(payload)}"
            )
            continue

        print(
            f"    HID RX seq={seq:02X} flags={flags:02X} "
            f"len={length}: {hx(payload)}"
        )
        return payload


def ping_identity(fd: int, seq: int) -> None:
    report = bytes([0x80, 0xF9, seq, 0x01, 0x01, 0x30])
    report += bytes(REPORT_LEN - len(report))
    wrote = os.write(fd, report)
    if wrote != REPORT_LEN:
        raise RuntimeError(f"short identity write: {wrote}/{REPORT_LEN}")

    print(f"PING TX seq={seq:02X}: {hx(report[:6])}")

    deadline = time.monotonic() + 3.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timeout waiting ZXPC_APP identity response")

        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue

        try:
            raw = os.read(fd, REPORT_LEN)
        except BlockingIOError:
            continue

        if len(raw) < 5:
            continue
        if raw[0] != 0x81 or raw[1] != 0xF9 or raw[2] != seq:
            continue

        length = raw[4]
        if 5 + length > len(raw):
            raise RuntimeError("truncated identity response")

        payload = bytes(raw[5 : 5 + length])
        print(f"PING RX seq={seq:02X}: {hx(payload)}")

        if b"ZXPC_APP" not in payload:
            raise RuntimeError("identity response did not contain ZXPC_APP")

        print("ZXPC_APP: OK")
        return


def validate_plain_response(plain: bytes, label: str) -> None:
    if len(plain) < 6:
        raise RuntimeError(f"{label}: native response too short: {len(plain)}")
    if plain[1] != len(plain):
        raise RuntimeError(
            f"{label}: native frame length mismatch "
            f"header={plain[1]} actual={len(plain)}"
        )
    expected_crc = crc8(plain[:-1])
    if plain[-1] != expected_crc:
        raise RuntimeError(
            f"{label}: CRC mismatch got={plain[-1]:02X} "
            f"expected={expected_crc:02X}"
        )


def first_frame_total_native(plain: bytes) -> tuple[int, int, int]:
    logical_len = plain[4]
    total_native = logical_len + 6 + 5 * (logical_len // 15)
    packet_count = (total_native + 19) // 20
    return logical_len, total_native, packet_count


def reconstruct_logical(plains: list[bytes], logical_len: int) -> bytes:
    if not plains:
        return b""

    logical = bytearray(plains[0][5:-1])
    for plain in plains[1:]:
        logical.extend(plain[4:-1])

    return bytes(logical[:logical_len])


def perform_native_read(
    fd: int,
    outer_seq: int,
    session: KeyLinkerReadSession,
    label: str,
    command: int,
    subcommand: int | None,
) -> tuple[int, bytes]:
    selector = 0
    native_wires: list[bytes] = []
    native_plains: list[bytes] = []
    logical_len: int | None = None
    total_native: int | None = None
    packet_count: int | None = None

    while True:
        plain_req, wire_req, number, state, rnd = build_read_request(
            session, command, selector, subcommand
        )

        print(
            f"  request#{number:02d} selector={selector} "
            f"state={state:02X} rand={rnd:02X}"
        )
        print("    Plain TX:", hx(plain_req))
        print("    Wire  TX:", hx(wire_req))

        send_outer(fd, outer_seq, wire_req)
        wire_rx = receive_outer(fd, outer_seq)
        plain_rx = decode(wire_rx)

        print("    Plain RX:", hx(plain_rx))
        validate_plain_response(plain_rx, label)

        native_wires.append(wire_rx)
        native_plains.append(plain_rx)

        if selector == 0:
            logical_len, total_native, packet_count = first_frame_total_native(
                plain_rx
            )
            print(
                f"    logical_len={logical_len} "
                f"total_native={total_native} "
                f"packet_count={packet_count}"
            )
            if packet_count < 1:
                raise RuntimeError(f"{label}: invalid packet_count")
            if packet_count > 16:
                raise RuntimeError(
                    f"{label}: refusing unreasonable packet_count={packet_count}"
                )

        outer_seq += 1
        if outer_seq > 255:
            outer_seq = 1

        assert packet_count is not None
        if len(native_wires) >= packet_count:
            break

        selector += 1

    assert logical_len is not None
    assert total_native is not None

    actual_native = sum(len(x) for x in native_wires)
    if actual_native != total_native:
        raise RuntimeError(
            f"{label}: native total mismatch got={actual_native} "
            f"expected={total_native}"
        )

    logical = reconstruct_logical(native_plains, logical_len)
    if len(logical) != logical_len:
        raise RuntimeError(
            f"{label}: logical reconstruction mismatch got={len(logical)} "
            f"expected={logical_len}"
        )

    print(f"  RESULT {label}: logical[{len(logical)}] = {hx(logical)}")
    return outer_seq, logical


def main() -> None:
    # Deterministic self-tests already observed on the real controller.
    known0 = bytes.fromhex("BB 06 00 29 00 27")
    wire0 = bytes.fromhex("C0 B3 40 70 99 96")
    known1 = bytes.fromhex("BB 06 81 23 01 B9")
    wire1 = bytes.fromhex("C2 37 F2 B7 A4 28")
    if encode(known0) != wire0 or encode(known1) != wire1:
        raise RuntimeError("KeyLinker codec self-test failed")

    dev, sysfs = discover()
    print("=" * 78)
    print("G3V2 — WARM-UP OFICIAL MIXDRIVE (SOMENTE LEITURAS)")
    print("=" * 78)
    print("Device:", dev)
    print("Sysfs :", sysfs)
    print("Codec : OK")
    print("Mutating 0x3B writes: DISABLED / NOT IMPLEMENTED")
    print("SCSI/F0/F1/F2       : NOT IMPLEMENTED")

    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        print("Fila antiga descartada:", drain(fd))

        # One successful ConnectDevice-style identity request. It is outside
        # KeyLinker and therefore does not consume KeyLinker state/rand.
        print("\n=== IDENTIDADE ===")
        ping_identity(fd, 1)

        session = KeyLinkerReadSession()
        outer_seq = 2
        final_custom: bytes | None = None

        print("\n=== WARM-UP ===")
        for index, (label, command, subcommand) in enumerate(
            OFFICIAL_WARMUP, start=1
        ):
            print("\n" + "-" * 78)
            print(
                f"{index:02d}/{len(OFFICIAL_WARMUP):02d} {label} "
                f"(cmd={command:02X})"
            )
            outer_seq, logical = perform_native_read(
                fd,
                outer_seq,
                session,
                label,
                command,
                subcommand,
            )
            if label == "CustomData FINAL":
                final_custom = logical

        print("\n" + "=" * 78)
        print("RESULTADO FINAL")
        print("=" * 78)
        print("Native read requests enviados:", session.read_count)

        if final_custom is None:
            raise RuntimeError("final CustomData was not captured")

        print(f"CustomData FINAL: byte[{len(final_custom)}]")
        print("Dados:", hx(final_custom))

        if len(final_custom) == 20:
            print(
                "\nSUCESSO: o warm-up oficial produziu CustomData de 20 bytes."
            )
            print(
                "Nenhuma alteração foi escrita. O próximo passo será analisar "
                "esta sessão antes de habilitar qualquer mutação OTA."
            )
        elif len(final_custom) == 0:
            print(
                "\nO warm-up oficial ainda terminou em CustomData vazio."
            )
            print(
                "Nenhuma alteração foi escrita; será necessário comparar "
                "alguma etapa de inicialização anterior ao loader."
            )
        else:
            print(
                f"\nWarm-up terminou em CustomData de {len(final_custom)} bytes."
            )
            print(
                "Nenhuma alteração foi escrita. Esse tamanho será comparado "
                "com o comportamento gerenciado oficial."
            )

    finally:
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == "__main__":
    main()
