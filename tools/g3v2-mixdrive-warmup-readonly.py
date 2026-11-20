#!/usr/bin/env python3
"""
G3 V2 — read-only MixDrive warm-up reproduction.

This script reproduces the proven GamepadConfigInitializer read baseline:
  1 BeforeKeyChangeList
  2 SupportTurbo
  3 CurrentTurbo
  4 Rocker
  5 CustomData
  6 Trigger
  7 Motor
  8 CustomData
  9 Lamplight
 10 CustomData
 11 CustomData

It sends READ REQUESTS only. It contains no CustomData writer, no OTA-bit
mutation and no SCSI F0/F1/F2 support.

The KeyLinker request state and CRT rand() stream are emulated from a cold
vendor-DLL process. Continuation requests, when required by the managed
receiveDataLength(type=0) packet count, consume additional state/rand entries
before the next high-level loader operation, just as MixDrive does.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
BASE_SCRIPT = HERE / "g3v2-enter-updater-linux.py"

spec = importlib.util.spec_from_file_location("g3v2_enter_updater", BASE_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {BASE_SCRIPT}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class MsvcRand:
    """MSVCRT rand() with implicit default seed 1."""

    def __init__(self):
        self.state = 1

    def next_low8(self) -> int:
        self.state = (self.state * 214013 + 2531011) & 0xFFFFFFFF
        value = (self.state >> 16) & 0x7FFF
        return value & 0xFF


class ReadState:
    """
    Cold read-only KeyLinker state sequence.

    With write-phase globals untouched, the proven read state byte cycles:
      00 81 02 83 04 85 06 87 ...
    """

    def __init__(self):
        self.request_index = 0
        self.rand = MsvcRand()

    def next_pair(self) -> tuple[int, int, int]:
        n = self.request_index
        state = (n & 0x07) | ((n & 0x01) << 7)
        rnd = self.rand.next_low8()
        self.request_index += 1
        return self.request_index, state, rnd


def make_frame(rs: ReadState, command: int, payload: bytes) -> tuple[int, bytes, bytes, int, int]:
    request_no, state, rnd = rs.next_pair()
    length = 2 + 2 + len(payload) + 1
    plain = bytes([command, length, state, rnd]) + bytes(payload)
    plain += bytes([base.crc8(plain)])
    wire = base.encode(plain)
    return request_no, plain, wire, state, rnd


def generic_list_frame(rs: ReadState, packet_index: int, kind: int):
    # Native 0x180001BE0:
    #   B0 07 <state> <rand> <packet_index> <kind> <crc>
    return make_frame(rs, 0xB0, bytes([packet_index & 0xFF, kind & 0xFF]))


def indexed_frame(rs: ReadState, command: int, packet_index: int):
    # Six-byte read builder:
    #   CMD 06 <state> <rand> <packet_index> <crc>
    return make_frame(rs, command, bytes([packet_index & 0xFF]))


def fixed_zero_frame(rs: ReadState, command: int):
    # Rocker/Trigger/Motor builders have no managed packet-index argument
    # on the initial request and encode a zero data byte.
    return make_frame(rs, command, b"\x00")


def validate_response(wire: bytes, label: str, first: bool = True):
    p = base.decode(wire)
    print(f"    RX plain: {base.hx(p)}")
    if len(p) < 6:
        raise RuntimeError(f"{label}: native response too short ({len(p)})")
    if p[-1] != base.crc8(p[:-1]):
        raise RuntimeError(f"{label}: native response CRC mismatch")
    if p[1] != len(p):
        raise RuntimeError(
            f"{label}: native response length field {p[1]} != {len(p)}"
        )

    if first:
        total = base.receive_data_length_type0(wire)
        count = (total + 19) // 20
        logical = p[4]
        print(
            f"    logical_len={logical} total_native={total} "
            f"managed_packets={count}"
        )
        return p, logical, total, count

    return p, None, None, None


def send_read(
    fd,
    outer_seq: int,
    rs: ReadState,
    label: str,
    builder,
    continuation_supported: bool,
):
    """
    Send one high-level MixDrive read and any managed continuation packets.

    builder(packet_index) -> (request_no, plain, wire, state, rand)
    """
    packet_index = 0
    responses = []

    request_no, plain, wire, state, rnd = builder(packet_index)

    print(f"\n=== {label} ===")
    print(
        f"    KeyLinker request #{request_no}: "
        f"state={state:02X} rand={rnd:02X} packet_index={packet_index}"
    )
    print(f"    TX plain: {base.hx(plain)}")
    print(f"    TX wire : {base.hx(wire)}")

    base.tx(fd, outer_seq, wire)
    _, response = base.rx(fd, outer_seq, timeout=3.0, ack=False)
    responses.append(response)

    _, logical_len, total_native, packet_count = validate_response(
        response, label, first=True
    )

    if packet_count <= 0:
        raise RuntimeError(f"{label}: invalid managed packet_count={packet_count}")

    if packet_count > 1 and not continuation_supported:
        raise RuntimeError(
            f"{label}: response requires {packet_count} managed packets, "
            "but this builder has no proven continuation argument; "
            "aborting read-only warm-up before guessing"
        )

    outer_seq = (outer_seq % 255) + 1

    for packet_index in range(1, packet_count):
        request_no, plain, wire, state, rnd = builder(packet_index)
        print(
            f"    CONT {packet_index}: KeyLinker request #{request_no}: "
            f"state={state:02X} rand={rnd:02X}"
        )
        print(f"    TX plain: {base.hx(plain)}")
        print(f"    TX wire : {base.hx(wire)}")

        base.tx(fd, outer_seq, wire)
        _, response = base.rx(fd, outer_seq, timeout=3.0, ack=False)
        responses.append(response)
        validate_response(response, label, first=False)
        outer_seq = (outer_seq % 255) + 1

    actual_native = sum(len(x) for x in responses)
    if actual_native != total_native:
        raise RuntimeError(
            f"{label}: combined native length {actual_native} != "
            f"declared {total_native}"
        )

    print(
        f"    DONE: logical_len={logical_len}, "
        f"native_bytes={actual_native}, packets={packet_count}"
    )
    return outer_seq, logical_len, responses


def main():
    if base.encode(base.READ0_PLAIN) != base.READ0_WIRE:
        raise RuntimeError("base codec self-test failed")

    dev, sysfs = base.discover()
    print("=== G3V2 MIXDRIVE WARM-UP — READ ONLY ===")
    print("Device:", dev)
    print("Sysfs :", sysfs)
    print("Policy: READ REQUESTS ONLY; no CustomData write; no OTA; no SCSI")

    fd = __import__("os").open(
        dev,
        __import__("os").O_RDWR | __import__("os").O_NONBLOCK,
    )

    try:
        print("Fila antiga descartada:", base.drain(fd))

        # MixDrive's LoadGamepadAsync performs ConnectDeviceAsync before
        # GamepadConfigInitializer. One identity ping is sufficient here;
        # it is outside the KeyLinker state/rand stream.
        print("\n=== ConnectDeviceAsync / ZXPC_APP ===")
        identity = base.ping_app(fd, 1)
        print("Identity:", base.hx(identity))

        rs = ReadState()
        outer_seq = 2
        custom_results = []

        plan = [
            (
                "1 BeforeKeyChangeList (op 9)",
                lambda idx: generic_list_frame(rs, idx, 3),
                True,
                False,
            ),
            (
                "2 SupportTurbo (op 16)",
                lambda idx: generic_list_frame(rs, idx, 4),
                True,
                False,
            ),
            (
                "3 CurrentTurbo (op 17)",
                lambda idx: indexed_frame(rs, 0xB4, idx),
                True,
                False,
            ),
            (
                "4 Rocker (op 12)",
                lambda idx: fixed_zero_frame(rs, 0xB1),
                False,
                False,
            ),
            (
                "5 CustomData #1 (op 28)",
                lambda idx: indexed_frame(rs, 0xBB, idx),
                True,
                True,
            ),
            (
                "6 Trigger (op 14)",
                lambda idx: fixed_zero_frame(rs, 0xB2),
                False,
                False,
            ),
            (
                "7 Motor (op 19)",
                lambda idx: fixed_zero_frame(rs, 0xB3),
                False,
                False,
            ),
            (
                "8 CustomData #2 (op 28)",
                lambda idx: indexed_frame(rs, 0xBB, idx),
                True,
                True,
            ),
            (
                "9 Lamplight (op 21)",
                lambda idx: indexed_frame(rs, 0xB7, idx),
                True,
                False,
            ),
            (
                "10 CustomData #3 (op 28)",
                lambda idx: indexed_frame(rs, 0xBB, idx),
                True,
                True,
            ),
            (
                "11 CustomData #4/final (op 28)",
                lambda idx: indexed_frame(rs, 0xBB, idx),
                True,
                True,
            ),
        ]

        for label, builder, continuation_supported, is_custom in plan:
            outer_seq, logical_len, responses = send_read(
                fd,
                outer_seq,
                rs,
                label,
                builder,
                continuation_supported,
            )
            if is_custom:
                # Reuse the proven native deframer for CustomData so the
                # final report is directly comparable with the OTA receiver.
                first_plain = base.decode(responses[0])
                data = bytearray(first_plain[5:-1])
                for response in responses[1:]:
                    p = base.decode(response)
                    data.extend(p[4:-1])

                result = bytes(data)
                if len(result) != logical_len:
                    raise RuntimeError(
                        f"{label}: reconstructed CustomData {len(result)} "
                        f"!= declared {logical_len}"
                    )
                custom_results.append(result)
                print(
                    f"    CUSTOMDATA RESULT byte[{len(result)}]: "
                    f"{base.hx(result)}"
                )

        print("\n" + "=" * 88)
        print("WARM-UP READ-ONLY COMPLETE")
        print("=" * 88)
        print("Total KeyLinker read requests:", rs.request_index)
        for i, data in enumerate(custom_results, 1):
            print(f"CustomData #{i}: byte[{len(data)}] {base.hx(data)}")

        final = custom_results[-1] if custom_results else b""
        print("\nFINAL CUSTOMDATA:", f"byte[{len(final)}]", base.hx(final))

        if len(final) == 20:
            print(
                "RESULT: final warm CustomData is 20 bytes. "
                "This is the first state compatible with the official "
                "data[11] OTA mutation path."
            )
        elif len(final) == 0:
            print(
                "RESULT: final warm CustomData is still empty. "
                "Warm-up state alone does not explain the current null result."
            )
        elif len(final) <= 11:
            print(
                "RESULT: official MainViewModel would still index data[11] "
                "outside this returned array."
            )
        else:
            print(
                "RESULT: non-empty warm CustomData recovered, but length is "
                f"{len(final)}, not the 20-byte OTA case."
            )

        print("\nNo configuration write was performed.")
        print("No OTA bit was written.")
        print("No SCSI command was sent.")

    finally:
        try:
            __import__("os").close(fd)
        except OSError:
            pass


if __name__ == "__main__":
    main()
