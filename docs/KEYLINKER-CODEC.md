# KeyLinker codec and CustomData framing

Last consolidated: 2026-11-20.

This document records the native codec and framing recovered from `KeyLinkerlib.dll`.

## Evidence boundary

The findings below come from static disassembly only. No DLL was executed and no HID/USB/SCSI command was sent while deriving them.

Known native functions:

- `0x180001080`: KeyLinker encoder
- `0x1800011C0`: KeyLinker decoder
- `0x180001760`: receive deframer
- `0x180001C90`: four-byte header builder
- `0x180008EB0`: `writeDeviceCustomData`

## Exact constants

```python
XOR40 = bytes.fromhex(
    "6b7c058ef791832950dba2713b4fbdc4"
    "9d63e86eec673a434eb1fbbc5c09707d"
    "aec8e9d76f959de4"
)

MIX16 = bytes.fromhex(
    "52ce615ab758eaa0693c9bc6bffd8b00"
)

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
```

## Encoder `0x180001080`

For a valid frame `p` of length `n`, arithmetic is modulo 256.

```python
def encode(p):
    p = bytes(p)
    n = len(p)
    q = bytearray(n)

    b2 = p[2]
    b3 = p[3]

    q[0] = p[0] ^ ((b2 + b3 + 0x6E) & 0xFF)
    q[1] = p[1] ^ ((b2 + b3 - 0x57) & 0xFF)
    q[2] = ((b2 + 0x53) & 0xFF) ^ b3
    q[3] = ((b3 - 0x41) & 0xFF) ^ 0xDB

    for i in range(4, n - 1):
        q[i] = p[i] ^ ((b3 - MIX16[i - 4] + b2) & 0xFF)

    q[n - 1] = p[n - 1]

    start = 20 if (q[3] & 0x02) else 0

    return bytes(
        q[i] ^ XOR40[start + i]
        for i in range(n)
    )
```

Verified vector:

```text
Plain : BB 06 00 29 00 27
Wire  : C0 B3 40 70 99 96
```

## Decoder `0x1800011C0`

The decoder is the inverse of the encoder for valid protocol frames.

```python
def decode(wire):
    wire = bytes(wire)
    n = len(wire)

    start = 20 if (wire[3] & 0x02) == 0 else 0
    p = bytearray(
        wire[i] ^ XOR40[start + i]
        for i in range(n)
    )

    p3 = ((p[3] ^ 0xDB) + 0x41) & 0xFF
    p2 = ((p3 ^ p[2]) - 0x53) & 0xFF

    p[3] = p3
    p[2] = p2

    s = (p2 + p3) & 0xFF
    p[0] ^= (s + 0x6E) & 0xFF
    p[1] ^= (s - 0x57) & 0xFF

    for i in range(4, n - 1):
        p[i] ^= (p2 - MIX16[i - 4] + p3) & 0xFF

    return bytes(p)
```

## CRC

The table at `0x18000B320` is used as a byte-wise CRC/check table.

For write framing, the native code starts at zero and folds every byte of `header + chunk`:

```python
def crc8(data):
    crc = 0
    for b in data:
        crc = CRC[(b ^ crc) & 0xFF]
    return crc
```

The resulting byte is appended before KeyLinker encoding.

## `writeDeviceCustomData` framing

The native function receives the logical CustomData array and its length.

It first creates this stream:

```text
[data_length][data bytes...]
```

The complete stream is divided into chunks of at most 15 bytes.

For each chunk:

1. call `0x180001C90` with:
   - command = `0x3B`
   - fragment index = 0, 1, ...
   - total plaintext frame length = `len(chunk) + 5`
2. copy the returned four-byte header;
3. append the chunk;
4. calculate CRC over the four-byte header plus chunk;
5. append the CRC byte;
6. encode the complete frame through `0x180001080`;
7. concatenate encoded frames in 20-byte slots.

Thus the exact outer shape is:

```text
[4-byte header][up to 15 stream bytes][CRC]
```

and every encoded frame is at most 20 bytes.

Equivalent high-level framing:

```python
stream = bytes([len(data) & 0xFF]) + bytes(data)

for fragment_index, off in enumerate(range(0, len(stream), 15)):
    chunk = stream[off:off + 15]
    frame_len = len(chunk) + 5
    header = build_header(0x3B, fragment_index, frame_len)  # 0x180001C90
    plain = header + chunk
    plain += bytes([crc8(plain)])
    wire = encode(plain)
```

Static disassembly now closes the four-byte header builder itself. The constants at `0x18000B214/0x18000B218` are ASCII `1`/`0`, respectively, and IAT `0x18000B150` resolves to CRT `rand`.


## Header builder `0x180001C90`

Signature inferred from all callers:

```c
uint8_t *build_header(uint8_t command, uint8_t fragment_index, uint8_t frame_len);
```

The function always allocates and returns four bytes.

Final stores prove:

```text
header[0] = command
header[1] = packed counter/length byte
header[2] = packed toggle/fragment/counter byte
header[3] = low byte returned by IAT function 0x18000B150
```

### Header byte 1

The routine converts `frame_len` into a 5-character binary string and global `E670` into a 3-character binary string, concatenates them in that order, then evaluates the resulting binary text with the recurrence:

```text
value = 2 * value + bit
```

Therefore this byte is exact:

```python
header1 = ((E670 & 0x07) << 5) | (frame_len & 0x1F)
```

### Header byte 2

The routine converts `fragment_index` into four binary characters and `E68C` into three binary characters. It prepends one single-character string selected from `0x18000B214` or `0x18000B218` according to `E67C`, concatenates the three parts through helper `0x180001400`, then evaluates the result as binary.

Thus the field layout is confirmed as:

```text
bit 7    = E67C (0 or 1)
bits 6:3 = fragment_index & 0x0F
bits 2:0 = E68C & 0x07
```

The constants are exact: `0x18000B214 = "1"` and `0x18000B218 = "0"`. The code selects `"1"` only when `E67C == 1`; otherwise it selects `"0"`. Therefore:

```python
header2 = ((E67C & 1) << 7) | ((fragment_index & 0x0F) << 3) | (E68C & 0x07)
```

### Header byte 3

IAT `0x18000B150` resolves to `api-ms-win-crt-utility-l1-1-0.dll!rand`. The return value is reduced to its low byte before being stored, so for this code path:

```python
header3 = rand() & 0xFF
```

The MSVC CRT `rand()` result is non-negative, so the compiler's signed-normalization sequence does not change this low-byte interpretation. `KeyLinkerlib.dll` imports `rand` but does not import `srand`. Microsoft documents that calling `rand` before any `srand` produces the same sequence as `srand(1)`, while the CRT state is application-scoped by default. Therefore the DLL alone does not establish whether another module in the host process may have advanced or reseeded the shared generator.

### Concatenation helper `0x180001400`

Static disassembly confirms this helper simply concatenates three NUL-terminated strings in argument order. In the header builder the inputs are the one-character `E67C` bit string, the four-character fragment-index string, and the three-character `E68C` string. This confirms the exact bit ordering used by `header[2]`.

### State evolution after every encoded fragment

The write loop updates the three header state globals after each fragment:

```python
E67C = 1 if E67C == 0 else 0
E68C = (E68C + 1) % 8
E670 = (E670 - 1) % 8
```

The native implementation expresses the last line as:

```text
if E670 == 0:
    E670 = 7
else:
    E670 -= 1
```

This means a multi-frame write has fully deterministic state progression once the initial three state values are known.

### Two-frame G3 V2 CustomData write

Let initial state before fragment 0 be:

```text
A = E670 & 7
B = E67C
C = E68C & 7
```

For the 20-byte G3 V2 logical object, fragment 0 has `frame_len=20` and index 0:

```python
h0_0 = 0x3B
h0_1 = (A << 5) | 20
h0_2 = ((B & 1) << 7) | C
h0_3 = source_byte_0
```

Then:

```python
A1 = (A - 1) & 7
B1 = 1 if B == 0 else 0
C1 = (C + 1) & 7
```

Fragment 1 has `frame_len=11` and index 1:

```python
h1_0 = 0x3B
h1_1 = (A1 << 5) | 11
h1_2 = ((B1 & 1) << 7) | 0x08 | C1
h1_3 = source_byte_1
```

## Receive deframer `0x180001760`

The receive helper returns a pointer+length descriptor.

It decodes each physical frame through `0x1800011C0`.

For the first decoded frame it removes:

```text
4-byte header
1-byte logical data length
...
1-byte CRC
```

For later frames it removes:

```text
4-byte header
...
1-byte CRC
```

and concatenates the logical bytes.

The first frame therefore contributes `frame_len - 6` payload bytes; each later frame contributes `frame_len - 5`.

## CustomData length correction

The G3 V2 CustomData object used by command `0x3B` is a 20-byte logical block.

The earlier project note that treated a 10-byte decoded payload as the CustomData object was incorrect: that 10-byte length belongs to a different decoded payload and must not be reused as the CustomData size.

This correction is also required by the confirmed OTA transition:

```text
data[11] |= 0x80
```

The managed array passed to `WriteDeviceCustomDataAsync` therefore has a valid byte 11.

## Exact 20-byte CustomData split

For the G3 V2 logical CustomData size of 20 bytes, the native writer builds a 21-byte stream:

```text
14 data[0] data[1] ... data[19]
^^
logical length = 0x14
```

That stream is split exactly as follows.

First plaintext frame, length 20:

```text
[header0 header1 header2 header3]
[14]
[data0 ... data13]
[CRC]
```

Second plaintext frame, length 11:

```text
[header0 header1 header2 header3]
[data14 ... data19]
[CRC]
```

Therefore the confirmed OTA mutation `data[11] |= 0x80` maps to **plaintext byte offset 16 of the first frame**:

```text
frame[4]  = logical length 0x14
frame[5]  = data[0]
...
frame[16] = data[11]   <- OTA bit 0x80
...
frame[19] = CRC
```

The CRC must be recomputed after this mutation, and only then is the 20-byte first frame passed to the KeyLinker encoder.

## OTA relevance

The official MixDrive update-mode transition is now narrowed to:

```text
read logical CustomData
set bit 0x80 in logical byte 11
write logical CustomData through command 0x3B
```

`0x180001C90` itself is fully reconstructed. The remaining byte-identical reproduction work is to recover the effective `E670/E67C/E68C` state at the target call and, only if byte-for-byte MixDrive output is desired, the process-wide CRT `rand()` position. For protocol-equivalent KeyLinker encoding, the random byte is carried inside the frame and recovered by the decoder; the codec itself does not require the peer to share the PRNG state.


### PRNG and security-cookie clarification

Static import analysis found `rand`, `QueryPerformanceCounter`, and `GetSystemTimeAsFileTime`, but no `srand` import in `KeyLinkerlib.dll`. The time/QPC references are in the standard MSVC security-cookie initializer around `0x180009640` (including the characteristic `0x2B992DDFA232` cookie constant), not in the protocol header builder or a PRNG-seeding path.

This distinction matters: matching MixDrive's exact `header[3]` sequence is a process-state reproduction problem, not a requirement imposed by the KeyLinker codec itself. The decoder derives byte 3 from the transmitted frame and never needs a synchronized `rand()` state.


### Writer state ordering and default rand correlation

A full static dump of `writeDeviceCustomData` at `0x180008EB0` confirms that the writer does **not** reset `E670/E67C/E68C` before the first fragment. It calls `0x180001C90` using the current global state, encodes/copies the frame, and only then advances the state:

```text
E67C = 1 if E67C == 0 else 0
E68C = (E68C + 1) mod 8
E670 = (E670 == 0 ? 7 : E670 - 1)
```

For a 20-byte logical CustomData object, the writer therefore consumes the pre-existing state for fragment 0 and the once-advanced state for fragment 1.

A package-wide PE scan found no `srand` or `rand_s` import/string in the MixDrive binaries; only `KeyLinkerlib.dll` imports CRT `rand`. Microsoft documents that calling `rand` before any `srand` is equivalent to `srand(1)`. Microsoft's own sample output for the default well-known sequence begins `41, 18467, 6334, ...`, whose low bytes are `0x29, 0x23, 0xBE, ...`.

This gives a strong correlation with the already verified plaintext read request:

```text
BB 06 00 29 00 27
         ^^
         first default rand() low byte
```

The correlation is not yet treated as proof of the exact OTA-time PRNG position, because MixDrive performs other protocol traffic before an update and CRT random state is application-scoped. It does, however, strongly support the reconstructed role of header byte 3.


### Confirmed 4-byte write header formula

Static analysis of `0x180001C90` plus real plaintext captures now closes the four-byte write header exactly.

For `writeDeviceCustomData` and the other writers using this builder:

```text
H0 = command
H1 = ((E670 & 7) << 5) | (frame_len & 0x1F)
H2 = ((E67C == 1) << 7) | ((fragment_index & 0x0F) << 3) | (E68C & 7)
H3 = rand() & 0xFF
```

The helper internally converts bit fields to ASCII binary strings and parses those strings back as binary integers; this obfuscation reduces exactly to the expressions above.

The previously captured plaintext lighting-write headers validate the formula bit-for-bit:

```text
37 EC 81 ...
37 CC 02 ...
37 AC 83 ...
37 8C 04 ...
```

For those frames:

- `H1 = EC, CC, AC, 8C` means `frame_len = 12` while `E670 = 7,6,5,4`;
- `H2 = 81,02,83,04` means fragment index 0, `E67C = 1,0,1,0`, and `E68C = 1,2,3,4`.

Therefore the builder formula is no longer merely inferred from decompilation; it is corroborated by real protocol captures.

### Read helper sequence byte

`readDeviceCustomData` builds:

```text
BB 06 SEQ RAND SELECTOR CRC
```

where:

```text
SEQ = ((E67C == 1) << 7) | ((E678 >> 2 & 0x0F) << 3) | (E68C & 7)
```

and then `0x1800014C0` advances:

```text
E67C = (E67C == 0) ? 1 : 0
E68C = (E68C + 1) mod 8
```

Two consecutive verified CustomData reads were:

```text
BB 06 00 29 00 27
BB 06 81 23 00 C0
```

This proves the observed starting sequence state was `E67C=0, E68C=0` and that the next read saw `E67C=1, E68C=1`. It also independently confirms the CRT default `rand()` progression `0x29 -> 0x23`.


### Conditional OTA writer headers after one fresh read

For a 20-byte CustomData array, `writeDeviceCustomData` constructs the logical stream:

```text
14  data[0] data[1] ... data[19]
^^
20-byte length prefix
```

It fragments that 21-byte stream as:

```text
fragment 0 payload: 14 + data[0..13]   (15 bytes)
fragment 1 payload: data[14..19]       (6 bytes)
```

Since the four-byte header and one-byte CRC are included in the builder's frame length, the corresponding lengths are `20 (0x14)` and `11 (0x0B)`.

After a verified first read, `E67C/E68C` are `1/1`. If `E670=7` and the next two CRT random low bytes are `23/BE`, the plaintext frames begin:

```text
3B F4 81 23  14 data[0..13] CRC
3B CB 0A BE  data[14..19]    CRC
```

The `E670=7` premise is strongly corroborated by real `0x37` write headers but is being kept explicitly conditional until the session/DLL initialization path is closed.


## 2026-11-20 — Android BLE codec is NOT Windows MixDrive USB codec

**Confirmed directly from the user's reference Android APK decompilation**:
`base.apk` SHA256 `09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee`,
`com/pulsenet/inputset/util/e.java` (JADX line numbers refer to the local
4,314-file decompilation):

- `h(byte[])` lines 384–409 is outgoing BLE frame transform.
- `g(byte[])` lines 356–378 is incoming BLE notification decoder.
- `k(int[])` lines 444–449 computes CRC from 256-entry lookup table, matching
  the previously recovered reflected CRC-8 polynomial `0xEB`.
- `K()` lines 92–99 increments the app's host serial counter:
  `01, 82, 03, 84, ...` (hex), unlike the first USB/MixDrive read
  session state byte `00`.
- `E()` lines 65–67 produces a random byte via Android `Random.nextInt(256)`,
  not the Windows MSVC CRT `rand()` initial sequence.
- `L()` line 179 creates the read-only identity opcode `0x91` frame with
  no query payload.
- `q(int)` line 551 creates a read-only `0xB7` frame with one index byte,
  e.g. `q(0)` for index zero.

The reference APK's actual BLE dispatch is
`com/pulsenet/inputset/util/b.java`:
`e.A().h(bArr)` goes to GATT `d7f010e1` in
`BluetoothGatt.writeCharacteristic` (around line 804);
`e.A().g(characteristic.getValue())` is invoked for
`d7f010e2` notifications (around line 251).
The application registers e2 notifications through the CCCD using
`ENABLE_NOTIFICATION_VALUE` (around lines 555–566 and 591–596).

This Android BLE codec has different substitutions/tables from the
native Windows MixDrive `KeyLinkerlib.dll` codec. **Do not reuse
`g3v2-warmup-readonly.py.encode()` to transmit Android BLE packets.**

Static deterministic vectors for the *same* logical data:

| Plaintext | Verified USB/MixDrive wire | Android APK BLE wire |
|---|---|---|
| `B7 06 00 29 00 A4` | `CC B3 40 70 99 15` | `71 A2 A2 90 2C 8E` |
| `91 05 01 29 FB` | not tested over USB | `48 A0 A5 90 21` |
| `B7 06 82 29 00 96` | not tested over USB | `EF 20 24 90 A2 BC` |

The first row is a codec comparison using identical plaintext; it is
**not a recommended Android request**, because the APK's first sequence
value is `01`, not `00`. The subsequent identity-then-B7 row shows
serial `82` after the identity used serial `01`, for fixed illustrative
nonce `29`. Real APK nonces are random.

An independent X20 Pro project's Android-derived implementation
(`AmjadAAYD/x20ctl` `x20ctl/protocol.py`) contains the same Android
transform, but the decisive evidence here is the locally decompiled
KeyLinker APK itself; X20 Pro compatibility is not assumed.

New pure Python implementation in
`tools/g3v2-android-keylinker-codec.py` has deterministic CRC,
encode/decode and host serial self-tests. A guarded hardware experiment
`tools/g3v2-le-android-identity-b7.py` makes one Android `0x91`
identity query and, **only if a valid CRC- and serial-matched
notification is received**, one `0xB7` read query. It uses a single
direct LE Public ATT connection and can enable the connection-scoped
e2 CCCD. It does not send lighting writer `0x37` or any firmware
writes. This experiment is **not yet proven successful on hardware**.

Never conflate ATT Write Response with an application KeyLinker reply.
The previous USB-coded `B7` frames wrote to e1 successfully in ATT
but got no e2 response; that negative result cannot be interpreted
as proof that *valid Android-coded* requests are ignored.


### Physical Android BLE validation — 2026-11-20

The APK-derived `e.h/e.g` codec has now been proven with real
G3V2 `d7f010e1/e2` traffic (not merely test vectors):

- `0x91` identity request (serial 01) encoded
  `91 05 01 CB 33` to `02 30 4A F2 C6`.
  Actual e2 response `C8 A0 BD 88 22 F5 C0 14 A5 37 C0 E1 16`
  decoded to `19 0D 01 31 07 10 06 20 11 01 48 46 B8`
  with valid CRC/serial; identity `1006:2011`, firmware `1.48`.
- `0xB7` LampLight read (serial 82) encoded
  `B7 06 82 FA 00 E7` to `1C 71 F7 C1 93 CD`.
  Actual e2 response `32 FB 77 41 15 B3 F3 F1 FE 40 BD 46`
  decoded to `19 0C 82 7A 06 00 FF FF 00 00 FF F9`
  with valid CRC/serial.
  Logical lighting: `00 FF FF 00 00 FF` (six bytes),
  same as previously read over USB. RTTs: **23.0ms / 22.0ms**.

The direct-LE Bazzite path is now **confirmed operational for reads**.
Previous identical-read attempts with MixDrive native USB encoding
were not valid Android application packets and should not be repeated.
Do not infer realtime writable LED safety or flash endurance.
See `docs/G3V2-BLE-VERIFIED.md`.


### Android lighting write serializers — 2026-11-21

Additional direct APK caller trace resolves two independent
`0x37` **input preparations**, not independent transport
commands:

- `b1.A1(List<LightingDataBean>)` builds
  `[length = 6*N, R,G,B,data4,data5,brightness, ...]`,
  partitions at 15 bytes, sends all chunks via `e.l0()`.
- `b1.z1(...)` gathers up to four 6-field lamp records and
  repacks UI-style `[R,G,B,flag,brightness,speed]` into
  `[R,G,B,speed_low,packed_high,brightness]`, where
  `packed_high = ((speed >> 8) & 15) | (flag ? 128 : 0)`
  according to the app's explicit 12-bit string split.
  This path also uses `e.l0()` for all chunks.

These use the **same KeyLinker lighting config writer 0x37**
and the previously reconstructed 6-byte G3V2 LampLight layout;
neither is a verified volatile/preview LED command.

Distinct `e.X`/`e.h0` builder opcode **0x25**
is used in `b1.f1` for grouped three-byte elements,
not RGB lighting records.
The KeyLinker-family command reference from x20ctl
calls `0x25` **`CODE_TOOBLE_MODE` (Turbo)**.
Do not confuse decimal 37 (0x25) with hexadecimal 0x37.
No high-frequency 0x37 write has been cleared as safe.
