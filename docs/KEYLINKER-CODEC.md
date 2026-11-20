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

The MSVC CRT `rand()` result is non-negative, so the compiler's signed-normalization sequence does not change this low-byte interpretation. Reproducing the exact same byte sequence as MixDrive still requires identifying whether/where the DLL seeds the CRT PRNG (`srand`) and the runtime state before the call.

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

`0x180001C90` itself is now fully reconstructed. The remaining work for byte-identical offline generation is to recover the initial/synchronized values of `E670`, `E67C`, and `E68C`, and determine the CRT `rand()` seeding/state. No hardware write is required for that analysis.
