#!/usr/bin/env python3
"""Android KeyLinker APK BLE packet codec (offline pure-Python implementation).

Reconstructed from reference base.apk SHA256:
09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee
com/pulsenet/inputset/util/e.java (obfuscated names):
- e.h(byte[]) encodes outgoing BLE frames
- e.g(byte[]) decodes received notifications
- e.k(int[]) calculates CRC
- e.K() uses 1, 0x82, 3, 0x84, ... serial sequence
- e.L() builds 0x91 identity request without payload
- e.q(int) builds 0xB7 read request with 1-byte selector payload

No APK/source bundle included and no Bluetooth I/O in this file.
The verified Windows MixDrive/USB KeyLinkerlib.dll uses a DIFFERENT codec.
"""

from __future__ import annotations

import secrets

# APK e.java f2746g; R.styleable references normalized to their actual
# integer values, cross-checked with the independently recovered x20ctl
# KeyLinker Android protocol source.
SEND_DATA_KEY = (
    51, 99, 157, 121, 242, 219, 162, 26,
    170, 33, 139, 232, 116, 211, 88,
)
# APK e.java f2747h.
SERIAL_KEY = (
    161, 82, 213, 163, 245, 137, 246, 143, 240, 157,
    72, 147, 234, 52, 49, 186, 195, 77, 198, 235,
    73, 96, 216, 163, 218, 42, 83, 141, 244, 97,
    24, 191, 174, 215, 111, 81, 228, 160, 217, 146,
)
CRC8_POLY_REFLECTED = 0xEB
MAX_FRAME = 20
READ_ONLY_OPS = frozenset((0x91, 0xB7))


def crc8(data: bytes) -> int:
    """Matches APK's f2745f table, including CRC(B7 06 00 29 00) = A4."""
    state = 0
    for byte in data:
        state ^= byte
        for _ in range(8):
            state = ((state >> 1) ^ CRC8_POLY_REFLECTED) & 0xFF if state & 1 else state >> 1
    return state


def encode(plain: bytes) -> bytes:
    """APK e.h(byte[]), not MixDrive USB KeyLinkerlib.dll encode()."""
    if not 5 <= len(plain) <= MAX_FRAME:
        raise ValueError("Android codec length must be 5..20")
    b2, b3 = plain[2], plain[3]
    out = bytearray(len(plain))
    out[0] = plain[0] ^ ((b2 + b3 - 154) & 0xFF)
    out[1] = plain[1] ^ ((b2 + b3 + 155) & 0xFF)
    out[2] = ((b2 - 173) ^ b3) & 0xFF
    out[3] = ((b3 + 191) ^ 219) & 0xFF
    for i in range(4, len(plain)-1):
        out[i] = plain[i] ^ ((b2 + b3 - SEND_DATA_KEY[i-4]) & 0xFF)
    out[-1] = plain[-1]
    offset = (out[3] & 0x02) * 10
    return bytes(value ^ SERIAL_KEY[offset+i] for i, value in enumerate(out))


def decode(wire: bytes) -> bytes:
    """APK e.g(byte[]), exact inverse of e.h(byte[])."""
    if not 5 <= len(wire) <= MAX_FRAME:
        raise ValueError(f"Android codec frame length invalid: {len(wire)}")
    offset = 20 if ((wire[3] ^ SERIAL_KEY[3]) & 0x02) else 0
    out = bytearray(wire[i] ^ SERIAL_KEY[offset+i] for i in range(len(wire)))
    b3 = ((out[3] ^ 219) - 191) & 0xFF
    out[3] = b3
    b2 = ((out[2] ^ b3) + 173) & 0xFF
    out[2] = b2
    out[1] ^= (b2 + b3 + 155) & 0xFF
    out[0] ^= (b2 + b3 - 154) & 0xFF
    for i in range(4, len(wire)-1):
        out[i] ^= (b2 + b3 - SEND_DATA_KEY[i-4]) & 0xFF
    return bytes(out)


def build_read(opcode: int, serial: int, nonce: int, payload: bytes = b"") -> tuple[bytes, bytes]:
    if opcode not in READ_ONLY_OPS:
        raise ValueError("Only Android APK identity 0x91 or B7 lighting read allowed")
    if opcode == 0x91 and payload:
        raise ValueError("APK identity L() requires empty payload")
    if opcode == 0xB7 and payload != b"\x00":
        raise ValueError("APK B7 q(0) requires selector 0 only")
    if not 0 <= serial <= 255 or not 0 <= nonce <= 255:
        raise ValueError("Invalid serial/nonce")
    header = bytes([opcode, len(payload)+5, serial, nonce]) + payload
    plain = header + bytes([crc8(header)])
    return plain, encode(plain)


class AndroidSerial:
    """APK e.K() serial sequence: 1, 130 (0x82), 3, 132, ..."""

    def __init__(self):
        self.calls = 0

    def next(self) -> int:
        self.calls += 1
        n = self.calls % 128
        return n + 128 if self.calls % 2 == 0 else n


def nonce() -> int:
    """APK uses Java Random.nextInt(256); any byte is syntactically valid."""
    return secrets.randbelow(256)


def validate_reply(plain: bytes, expected_opcode: int, expected_serial: int) -> None:
    if len(plain) < 5:
        raise ValueError("KeyLinker response too short")
    if crc8(plain[:-1]) != plain[-1]:
        raise ValueError("KeyLinker response CRC mismatch")
    if plain[0] != expected_opcode:
        raise ValueError(
            f"KeyLinker response opcode 0x{plain[0]:02X}, expected 0x{expected_opcode:02X}"
        )
    if plain[2] != expected_serial:
        raise ValueError(
            f"KeyLinker response serial 0x{plain[2]:02X}, expected 0x{expected_serial:02X}"
        )


def selftest() -> None:
    vectors = (
        ("B7 06 00 29 00 A4", "71 A2 A2 90 2C 8E"),
        ("91 05 01 29 FB", "48 A0 A5 90 21"),
        ("B7 06 01 29 00 56", "6E A3 A5 90 2D 7C"),
        ("B7 06 82 29 00 96", "EF 20 24 90 A2 BC"),
    )
    for plain_hex, wire_hex in vectors:
        p, w = bytes.fromhex(plain_hex), bytes.fromhex(wire_hex)
        if crc8(p[:-1]) != p[-1] or encode(p) != w or decode(w) != p:
            raise RuntimeError("Android codec self-test failed: " + plain_hex)
    seq = AndroidSerial()
    if [seq.next() for _ in range(4)] != [1, 0x82, 3, 0x84]:
        raise RuntimeError("Android serial generator self-test failed")


if __name__ == "__main__":
    selftest()
    print("Android KeyLinker e.h/e.g codec and APK serial vectors: OK")
    print("G3V2 USB frame B7 06 00 29 00 A4 would encode to:")
    print(encode(bytes.fromhex("B7 06 00 29 00 A4")).hex(" ").upper())
