# G3 V2 BLE KeyLinker — verified Linux path

**Status: real bidirectional BLE application protocol validated on 2026-11-20.**

This page is the canonical compact summary. Older dated entries in
[STATUS.md](STATUS.md) and [RESEARCH-LOG.md](RESEARCH-LOG.md) describe
experiments and may state that the BLE link or KeyLinker reads were not
yet established; those intermediate limitations have been superseded.

## Proven transport

- Linux/Bazzite KDE, Python 3.15.2. No rpm-ostree install, no pairing.
- Direct Linux \`AF_BLUETOOTH/SOCK_SEQPACKET/BTPROTO_L2CAP\` socket;
  \`(BDADDR_LE_PUBLIC, ATT fixed CID=4)\`, explicitly public LE peer address.
- This bypasses the incorrect BR/EDR/LE bearer selection seen in the
  older BlueZ \`Device1.Connect\`/Bleak path. BlueZ's exact reason for
  not exposing \`Bearer.LE1\` remains unresolved; this working socket
  does not require fixing it.
- GATT primary service \`d7f010e0-660d-46e9-96c3-19c4148bdab5\`
  at handles \`0x0005–0x000A\`:
  - \`d7f010e1\` Write, value handle \`0x0007\`;
  - \`d7f010e2\` Notify, value handle \`0x0009\`;
  - e2 CCCD descriptor \`0x2902\`, handle \`0x000A\`.
- Additional service \`0xFF12\` at \`0x000B–0x0012\`, purpose unverified.
- CCCD read-back on the real device returns an **unusual one byte**
  \`0B 01\` (ATT Read Response + value \`01\`), even after an
  explicit standard \`01 00\` CCCD notification subscribe request.
  Nevertheless **actual e2 notifications were subsequently received**
  with the proper Android KeyLinker codec. Do not treat that one-byte
  CCCD anomaly as a blocker to the proven path.

## Critical codec distinction

The **Windows MixDrive USB** native \`KeyLinkerlib.dll\` frame encoder
is **not** the codec used by the **Android KeyLinker GATT** application.

The APK was extracted locally, SHA256:
\`09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee\`.
Its \`com/pulsenet/inputset/util/e.java\` defines:
- outgoing \`h(byte[])\` (lines 384–409);
- incoming \`g(byte[])\` (lines 356–378);
- CRC \`k(int[])\` (lines 444–449);
- serial \`K()\` (lines 92–99): \`01, 82, 03, 84...\`;
- nonce \`E()\` (lines 65–67);
- \`L()\` identity \`0x91\` (line 179), \`q(0)\` lighting read \`0xB7\` (line 551).

The GATT dispatch in \`util/b.java\` applies \`e.A().h()\` before
e1 writes and \`e.A().g()\` to e2 notifications.

For the **same logical frame** \`B7 06 00 29 00 A4\`:
- Windows USB wire bytes: \`CC B3 40 70 99 15\`;
- Android GATT wire bytes: \`71 A2 A2 90 2C 8E\`.

Older BLE test runs using the USB-encoded packet got ATT Write Response
but no e2 notification, **even after a standard CCCD subscription**.
They were not valid Android application-layer frames and cannot be used
as evidence that G3V2 BLE reads are unsupported.

## Successful real device exchange — 2026-11-20

Tool: \`tools/g3v2-le-android-identity-b7.py\`, based on
\`tools/g3v2-android-keylinker-codec.py\`.

Single LE Public link connected in **505.1 ms**; handles rediscovered,
CCCD \`01 00\` subscription received ATT Write Response.

### Identity query 0x91

\`\`\`text
TX plain: 91 05 01 CB 33
TX BLE  : 02 30 4A F2 C6
RX BLE  : C8 A0 BD 88 22 F5 C0 14 A5 37 C0 E1 16
RX plain: 19 0D 01 31 07 10 06 20 11 01 48 46 B8
Elapsed : 23.0 ms
\`\`\`

The response is a native KeyLinker response \`0x19\` with a valid CRC and
matching serial \`01\`. It declares a 7-byte identity record:
\`10 06 20 11 01 48 46\`. Following the Android-derived identity
layout, VID=\`1006\`, PID=\`2011\`, version=\`1.48\`,
extra flags byte \`46\` (not further interpreted here).
The logical ID and reported version independently corroborate the
G3V2 hardware/software identity.

### LampLight read 0xB7

\`\`\`text
TX plain: B7 06 82 FA 00 E7
TX BLE  : 1C 71 F7 C1 93 CD
RX BLE  : 32 FB 77 41 15 B3 F3 F1 FE 40 BD 46
RX plain: 19 0C 82 7A 06 00 FF FF 00 00 FF F9
Elapsed : 22.0 ms
\`\`\`

The response is \`0x19\`, CRC valid, serial \`82\` matches request,
\`0C\` is the native response length 12, \`06\` announces **six
logical LampLight bytes**:
\`00 FF FF 00 00 FF\`.

This **exactly matches the physical G3V2 USB B7 baseline**
previously recorded in a separate test; it is not an unrelated
notification. The six-byte LampLight record as recovered from the
MixDrive native format is:

| Offset | Value | Meaning |
|---|---:|---|
| 0 | 00 | R = 0 |
| 1 | FF | G = 255 |
| 2 | FF | B = 255 |
| 3–4 | 00 00 | Packed breath speed/switch field = 0 |
| 5 | FF | Brightness = 255 |

The displayed LED mode (solid vs breathing) **is not completely
represented by B7**; a prior physical \`FN+Right\` transition changed
the visible effect without changing these six bytes. Do not assume
the color/brightness readback is a full configuration snapshot.

**What has now been established:** LE transport, GATT discovery,
CCCD subscription, Android BLE frame encode/decode, 0x91 identity,
and B7 LampLight read and genuine controller notifications.

## Not yet proven / guarded

- High-rate RGB/Ambilight writes over BLE **have not** been tested.
- The KeyLinker \`0x37\` writer was previously proven to change the
  physical LED color on USB and that change survived one quick
  physical power cycle. This does **not** prove that each write
  commits flash or is safe for continuous updates.
- The user subsequently observed the last lighting setting
  apparently being forgotten after manually switching off the
  controller and leaving it on the charging dock for an unmeasured
  interval. This raises alternate possibilities (temporary state,
  mode override, reboot behavior) and means the exact persistence
  mechanism is unresolved. Do not send \`0x37\` at frame rate or
  presume flash endurance.
- Real-time / volatile lighting operation remains a separate
  reverse-engineering question. Use static APK/firmware inspection
  for a RAM-only/preview control path before attempting any
  continuous lighting writes.
- Read-query round trips of 22–23 ms **do not establish** write
  throughput, real-time BLE FPS, or 2.4 GHz coexistence quality.

## Files

- Android BLE codec: \`tools/g3v2-android-keylinker-codec.py\`
- Guarded working proof: \`tools/g3v2-le-android-identity-b7.py\`
- Raw LE GATT enumeration: \`tools/g3v2-le-gatt-discover-readonly.py\`
- Older BLE-only failed probes (historical):
  \`tools/g3v2-le-keylinker-b7-v2.py\`,
  \`tools/g3v2-le-b7-subscribe-v3.py\`
- Windows USB codec (separate): \`tools/g3v2-warmup-readonly.py\`

**Privacy:** no per-device Bluetooth MAC is embedded in this public
document or in application source. Supply address only at runtime.


## 2026-11-21: Lighting-write paths in the Android APK

After the BLE read proof, additional static JADX code confirms
two serializers feeding the same native `0x37` lighting writer:

- `util/b1.java::A1(List)` serializes `LightingDataBean`
  fields as `[R,G,B,data4,data5,brightness]`.
- `util/b1.java::z1(...)` converts UI parameters into the
  `[R,G,B,speed_lo,packed_hi,brightness]` six-byte layout,
  where `packed_hi` includes the breath flag at bit 7.
- Both use `e.A().l0(...)` and 15-byte fragments for opcode
  `0x37`; neither establishes a volatile LED-preview path.

The separately seen opcode `0x25`, emitted by
`e.A().h0`/`e.A().X` with three-byte grouped records,
is `CODE_TOOBLE_MODE` (Turbo) according to the
independent KeyLinker protocol reference
`https://github.com/AmjadAAYD/x20ctl/blob/main/docs/01-protocol.md`.
This correlates with `b1.f1(...)`'s bulk settings transfer.
It is not a candidate for realtime RGB updates.

The next distinct open research question is **where and how the
G3V2 firmware stores or overrides lighting state**, especially
after the user's charging-dock shutdown observation. Because
the visible lighting mode can change while the B7 RGB bytes
remain unchanged, any controlled dock experiment must record
**both B7 logical data and visual mode** before and after,
and should not issue unnecessary 0x37 lighting writes.
