#!/usr/bin/env python3
import argparse, glob, os, pathlib, select, subprocess, time

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
READ0_PLAIN = bytes.fromhex("BB 06 00 29 00 27")
READ0_WIRE  = bytes.fromhex("C0 B3 40 70 99 96")
READ1_PLAIN = bytes.fromhex("BB 06 81 23 01 B9")
READ1_WIRE  = bytes.fromhex("C2 37 F2 B7 A4 28")
REPORT_LEN = 64

def hx(b): return " ".join(f"{x:02X}" for x in b)

def crc8(data):
    c = 0
    for b in data:
        c = CRC[(b ^ c) & 0xFF]
    return c

def encode(p):
    p = bytes(p); n = len(p)
    if n < 5: raise ValueError("frame too short")
    q = bytearray(n); b2, b3 = p[2], p[3]
    q[0] = p[0] ^ ((b2 + b3 + 0x6E) & 0xFF)
    q[1] = p[1] ^ ((b2 + b3 - 0x57) & 0xFF)
    q[2] = ((b2 + 0x53) & 0xFF) ^ b3
    q[3] = ((b3 - 0x41) & 0xFF) ^ 0xDB
    for i in range(4, n - 1):
        q[i] = p[i] ^ ((b3 - MIX16[i - 4] + b2) & 0xFF)
    q[-1] = p[-1]
    start = 20 if (q[3] & 0x02) else 0
    return bytes(q[i] ^ XOR40[start + i] for i in range(n))

def decode(wire):
    wire = bytes(wire); n = len(wire)
    if n < 5: raise ValueError("frame too short")
    start = 20 if (wire[3] & 0x02) == 0 else 0
    p = bytearray(wire[i] ^ XOR40[start + i] for i in range(n))
    p3 = ((p[3] ^ 0xDB) + 0x41) & 0xFF
    p2 = ((p3 ^ p[2]) - 0x53) & 0xFF
    p[3], p[2] = p3, p2
    s = (p2 + p3) & 0xFF
    p[0] ^= (s + 0x6E) & 0xFF
    p[1] ^= (s - 0x57) & 0xFF
    for i in range(4, n - 1):
        p[i] ^= (p2 - MIX16[i - 4] + p3) & 0xFF
    return bytes(p)

def discover():
    candidates = []
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        pp = pathlib.Path(p)
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
        raise RuntimeError(f"expected exactly one G3V2 MI_01 hidraw, found {candidates}")
    return candidates[0]

def drain(fd):
    n = 0
    while True:
        try:
            b = os.read(fd, REPORT_LEN)
            if not b: break
            n += 1
        except BlockingIOError:
            break
    return n

def tx(fd, seq, payload):
    if len(payload) > 20: raise ValueError("outer payload >20")
    report = bytes([0x80, 0xF9, seq & 0xFF, 0x00, len(payload)]) + bytes(payload)
    report += bytes(REPORT_LEN - len(report))
    wrote = os.write(fd, report)
    if wrote != REPORT_LEN:
        raise RuntimeError(f"short HID write: {wrote}/{REPORT_LEN}")
    print(f"TX seq={seq:02X}: {hx(report[:5+len(payload)])}")

def ping_app(fd, seq):
    # ConnectDeviceAsync special path: 80 F9 <seq> 01 01 30
    report = bytes([0x80, 0xF9, seq & 0xFF, 0x01, 0x01, 0x30])
    report += bytes(REPORT_LEN - len(report))
    wrote = os.write(fd, report)
    if wrote != REPORT_LEN:
        raise RuntimeError(f"short HID ping write: {wrote}/{REPORT_LEN}")
    print(f"PING TX seq={seq:02X}: {hx(report[:6])}")

    deadline = time.monotonic() + 3.0
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            raise TimeoutError(f"timeout waiting ZXPC_APP ping seq={seq:02X}")
        r, _, _ = select.select([fd], [], [], rem)
        if not r:
            continue
        try:
            b = os.read(fd, REPORT_LEN)
        except BlockingIOError:
            continue
        if len(b) < 5:
            continue
        print(f"PING RX raw: {hx(b[:min(len(b),32)])}" + (" ..." if len(b)>32 else ""))
        if b[0] != 0x81 or b[1] != 0xF9 or b[2] != (seq & 0xFF):
            continue
        n = b[4]
        if 5 + n > len(b):
            raise RuntimeError("truncated identity response")
        payload = bytes(b[5:5+n])
        if b"ZXPC_APP" not in payload:
            raise RuntimeError(f"identity response missing ZXPC_APP: {hx(payload)}")
        print(f"PING seq={seq:02X}: ZXPC_APP OK")
        return payload

def rx(fd, seq, timeout=2.0, ack=None):
    deadline = time.monotonic() + timeout
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            raise TimeoutError(f"timeout waiting seq={seq:02X}, ack={ack}")
        r, _, _ = select.select([fd], [], [], rem)
        if not r: continue
        try:
            b = os.read(fd, REPORT_LEN)
        except BlockingIOError:
            continue
        if not b: continue
        print(f"RX raw: {hx(b[:min(len(b),32)])}" + (" ..." if len(b)>32 else ""))
        if len(b) < 5 or b[0] != 0x81 or b[1] != 0xF9 or b[2] != (seq & 0xFF):
            continue
        flags, n = b[3], b[4]
        is_ack = bool(flags & 1)
        if ack is not None and is_ack != ack:
            continue
        if 5 + n > len(b): raise RuntimeError("truncated HID response")
        payload = bytes(b[5:5+n])
        print(f"RX seq={seq:02X} flags={flags:02X} len={n}: {hx(payload)}")
        return flags, payload

def receive_data_length_type0(wire):
    """
    Exact KeyLinkerlib!receiveDataLength branch used by normal OnReport:
      type == 0
      logical_len = decode(wire)[4]
      total_native = logical_len + 6 + 5 * floor(logical_len / 15)
    """
    p = decode(wire)
    if len(p) < 6:
        raise RuntimeError(f"native response too short: {len(p)}")
    logical_len = p[4]
    return logical_len + 6 + 5 * (logical_len // 15)

def parse_native_frame(wire, selector, first_frame=True):
    p = decode(wire)
    print(f"Plain selector {selector}:", hx(p))
    if len(p) < 6:
        raise RuntimeError(f"selector{selector} response too short: {len(p)}")
    if p[-1] != crc8(p[:-1]):
        raise RuntimeError(f"selector{selector} CRC mismatch")
    if p[0] != 0x19:
        raise RuntimeError(
            f"selector{selector} unexpected response command 0x{p[0]:02X}"
        )
    if p[1] != len(p):
        raise RuntimeError(
            f"selector{selector} frame length mismatch: "
            f"header={p[1]} actual={len(p)}"
        )
    if first_frame:
        logical_len = p[4]
        data = bytes(p[5:-1])
        print(f"Logical length declared: {logical_len}")
        print(f"First-frame logical bytes ({len(data)}):", hx(data))
        return p, logical_len, data
    data = bytes(p[4:-1])
    print(f"Continuation logical bytes ({len(data)}):", hx(data))
    return p, None, data

def read_custom(fd):
    """
    Reproduce the managed type-28 read decision:
      CheckPacketIndex -> receiveDataLength(type=0) -> ceil(total/20).
    Selector 1 is sent only if the first native response says another
    outer 20-byte packet is required.
    """
    print("\n=== READ CustomData selector 0 ===")
    tx(fd, 3, READ0_WIRE)
    _, w0 = rx(fd, 3, timeout=3.0, ack=False)

    p0, logical_len, d0 = parse_native_frame(w0, 0, first_frame=True)
    total_native = receive_data_length_type0(w0)
    packet_count = (total_native + 19) // 20

    print(f"receiveDataLength(type=0): {total_native} native bytes")
    print(f"Managed packet_count     : {packet_count}")

    if packet_count <= 0:
        raise RuntimeError(f"invalid managed packet_count={packet_count}")

    if packet_count == 1:
        # receiveDeviceCustomData(single frame) returns decoded[5:-1].
        data = d0
        if len(data) <= 0:
            print("\nOFFICIAL MANAGED RESULT: null")
            print("Reason: receiveDeviceCustomData length <= 0.")
            return None
        print(f"\nOFFICIAL MANAGED RESULT: byte[{len(data)}]")
        print("CustomData:", hx(data))
        return data

    if packet_count != 2:
        raise RuntimeError(
            f"unsupported CustomData packet_count={packet_count}; "
            "probe intentionally supports only the proven 1- or 2-packet cases"
        )

    if len(w0) != 20:
        raise RuntimeError(
            f"receiveDataLength expects continuation but first outer payload "
            f"is {len(w0)} bytes, expected 20"
        )

    print("\n=== READ CustomData selector 1 (managed continuation) ===")
    tx(fd, 4, READ1_WIRE)
    _, w1 = rx(fd, 4, timeout=3.0, ack=False)
    _, _, d1 = parse_native_frame(w1, 1, first_frame=False)

    if len(w0) + len(w1) != total_native:
        raise RuntimeError(
            f"combined native length mismatch: got {len(w0)+len(w1)}, "
            f"expected {total_native}"
        )

    data = d0 + d1
    if len(data) != logical_len:
        raise RuntimeError(
            f"combined logical length mismatch: got {len(data)}, "
            f"declared {logical_len}"
        )

    print(f"\nOFFICIAL MANAGED RESULT: byte[{len(data)}]")
    print("CustomData:", hx(data))
    return data

def build_ota_frames(data):
    if len(data) != 20: raise ValueError("CustomData must be 20 bytes")
    new = bytearray(data); before = new[11]; new[11] |= 0x80
    if new[11] == before:
        raise RuntimeError("OTA bit is already set in CustomData[11]; refusing to rewrite")
    stream = bytes([20]) + bytes(new)
    c0, c1 = stream[:15], stream[15:]
    # Cold host state after the two BB reads: E670=0, E67C=0, E68C=2.
    # Default CRT rand low bytes consumed: 29,23, then BE,84 for these frames.
    p0 = bytes.fromhex("3B 14 02 BE") + c0
    p0 += bytes([crc8(p0)])
    p1 = bytes.fromhex("3B EB 8B 84") + c1
    p1 += bytes([crc8(p1)])
    if len(p0) != 20 or len(p1) != 11: raise AssertionError((len(p0), len(p1)))
    return bytes(new), p0, p1, encode(p0), encode(p1)

def write_ota(fd, w0, w1):
    print("\n=== WRITE OTA CustomData ===")
    for attempt in range(1, 4):
        tx(fd, 5, w0)
        try:
            rx(fd, 5, timeout=2.0, ack=True)
            print(f"ACK fragment 0 OK (attempt {attempt})")
            break
        except TimeoutError:
            if attempt == 3: raise
            print(f"ACK timeout; retrying fragment 0 ({attempt}/3)")
    tx(fd, 6, w1)
    try:
        flags, payload = rx(fd, 6, timeout=1.5, ack=False)
        print(f"Final result received: flags={flags:02X}, payload={hx(payload)}")
    except (TimeoutError, OSError) as e:
        print(f"Final response not observed ({e}); re-enumeration may already be in progress.")

def snapshot(label):
    print(f"\n=== {label}: USB/BLOCK ===")
    for cmd in (["lsusb"], ["lsblk","-o","NAME,MAJ:MIN,RM,SIZE,RO,TYPE,FSTYPE,LABEL,MODEL,TRAN,VENDOR"]):
        try:
            p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=4)
            print("$", " ".join(cmd)); print(p.stdout.rstrip())
        except Exception as e:
            print("$", " ".join(cmd), "->", e)

def main():
    ap = argparse.ArgumentParser(description="G3 V2 CustomData OTA transition probe")
    ap.add_argument(
        "--execute",
        action="store_true",
        help="set CustomData[11] |= 0x80 only if the official read yields exactly 20 bytes",
    )
    args = ap.parse_args()
    if encode(READ0_PLAIN) != READ0_WIRE or encode(READ1_PLAIN) != READ1_WIRE:
        raise RuntimeError("codec self-test failed")
    dev, sysfs = discover()
    print("=== G3V2 OTA TRANSITION PROBE ===")
    print("Device:", dev); print("Sysfs :", sysfs); print("Codec : OK")
    snapshot("BEFORE")
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        print("Fila antiga descartada:", drain(fd))
        print("\n=== PINGS ZXPC_APP ===")
        ping_app(fd, 1)
        ping_app(fd, 2)
        print("Pings ZXPC_APP: OK")
        data = read_custom(fd)

        if data is None:
            print("\nDRY-RUN CONCLUÍDO: o caminho oficial retornaria null.")
            print("MainViewModel não executaria data[11] |= 0x80.")
            print("Nenhuma escrita OTA será tentada.")
            return

        if len(data) <= 11:
            print(
                f"\nDRY-RUN CONCLUÍDO: caminho oficial retornaria byte[{len(data)}]."
            )
            print(
                "MainViewModel acessaria data[11], que está fora do array; "
                "isso caracteriza um bug do aplicativo oficial nesse estado."
            )
            print("Nenhuma escrita OTA será tentada.")
            return

        if len(data) != 20:
            print(
                f"\nCustomData tem {len(data)} bytes; a implementação de escrita "
                "do probe permanece bloqueada fora do caso de 20 bytes."
            )
            print("Nenhuma escrita OTA será tentada.")
            return

        print(
            "\nCustomData de 20 bytes obtido, mas a geração/escrita OTA deste "
            "probe está temporariamente BLOQUEADA."
        )
        print(
            "Motivo: o warm-up oficial recém-reconstruído prova que o estado "
            "KeyLinker/rand do caminho real não é o antigo estado frio "
            "hardcoded por build_ota_frames()."
        )
        print(
            "Use tools/g3v2-warmup-readonly.py primeiro. Nenhum frame 0x3B "
            "será gerado ou enviado por este probe."
        )
        if args.execute:
            print("--execute foi ignorado por segurança.")
        return
    finally:
        try: os.close(fd)
        except OSError: pass
    time.sleep(3.0)
    snapshot("AFTER")
    print("\nNão envie F0. O objetivo desta etapa termina na re-enumeração.")

if __name__ == "__main__":
    main()
