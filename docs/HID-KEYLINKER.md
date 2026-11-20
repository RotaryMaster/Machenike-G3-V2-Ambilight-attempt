# HID / KeyLinker protocol

## Linux discovery

The controller normally appears as USB `413D:2131`.

The relevant protocol interface is MI_01 / sysfs path containing `:1.1/`. The hidraw number is dynamic and must not be hard-coded.

A robust discovery strategy is:
1. enumerate `/sys/class/hidraw/*/device/uevent`;
2. select an entry containing VID `413D` and PID `2131`;
3. resolve its sysfs path;
4. require interface `:1.1/`;
5. map it back to `/dev/hidrawN`.

## Envelope

Normal request/response traffic uses report IDs around an `F9` envelope.

Known read-feature request:

```text
Plain:
B0 07 00 29 00 01 E5

Encoded:
CB B2 40 70 99 EB 1E

USB report:
80 F9 04 00 07 CB B2 40 70 99 EB 1E 00 ...
```

Known response:

```text
81 F9 04 00 10
82 C5 60 50 F3 89 50 10 0F BB 6C 15 0C C5 87 3D
...
```

Decoded payload:

```text
19 10 00 09 0A 03 03 03 01 03 03 01 02 C0 00 EA
```

CRC validation succeeds.

The decoded byte at index 13 is `C0`; native feature parsing proves bit `0x80` corresponds to `isHasOta`.

## Confirmed KeyLinker codec constants

```python
XOR40 = bytes.fromhex("""
6b7c058ef791832950dba2713b4fbdc4
9d63e86eec673a434eb1fbbc5c09707d
aec8e9d76f959de4
""")

MIX16 = bytes.fromhex(
    "52ce615ab758eaa0693c9bc6bffd8b00"
)

CRC = bytes.fromhex("""
0079f28b334ac1b8661f94ed552ca7de
ccb53e47ff860d74aad3582199e06b12
4f36bdc47c058ef72950dba21a63e891
83fa7108b0c9423be59c176ed6af245d
9ee76c15add45f26f8810a73cbb23940
522ba0d9611893ea344dc6bf077ef58c
d1a8235ae29b1069b7ce453c84fd760f
1d64ef962e57dca57b0289f04831bac3
eb921960d8a12a538df47f06bec74c35
275ed5ac146de69f4138b3ca720b80f9
a4dd562f97ee651cc2bb3049f188037a
68119ae35b22a9d00e77fc853d44cfb6
750c87fe463fb4cd136ae1982059d2ab
b9c04b328af37801dfa62d54ec951e67
3a43c8b10970fb825c25aed76f169de4
f68f047dc5bc374e90e9621ba3da5128
""")
```

Known verified vector:

```text
Plain : BB 06 00 29 00 27
Wire  : C0 B3 40 70 99 96
```

The encode/decode implementation reconstructed from `KeyLinkerlib.dll` matches real traffic vectors.

## Special identity handshake

The connection handshake is a special path and is not handled exactly like ordinary KeyLinker generic payloads.

Request:

```text
80 F9 <seq> 01 01 30
```

Real response example:

```text
81 F9 05 01 13
5D 45 08
5A 58 50 43 5F 41 50 50
01 48 10 06 20 11
1B 00
```

`HidLibrary.HidReport.Data` excludes ReportId `0x81`. The official `OnReport` handler separately checks the report ID and then indexes the Data array.

For the captured response:
- `Data[6] = 0x08`
- the next 8 bytes are `ZXPC_APP`
- the next six bytes are `01 48 10 06 20 11`

Those six bytes become the connection-result tuple.

## OnReport routing facts

Static IL shows:
- expected report ID: `0x81`
- expected `Data[0]`: `0xF9`
- sequence correlation uses a sequence map
- the special identity request follows a dedicated branch
- generic packets use `Data[1]` as sequence
- ACK state includes `(Data[2] & 1) == 1`
- generic payload length is read from `Data[3]`
- payload starts at `Data[4]`
- decoded data is then routed to handlers for features, keys, turbo, rocker, trigger, motor, lighting, CustomData, macros, etc.

## CustomData

Confirmed:
- read selector `BB`
- BB selector `00`
- expected payload size: 10 bytes
- real readback works

More lighting-field semantics should be documented here as they are decoded.
