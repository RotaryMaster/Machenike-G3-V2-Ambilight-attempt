# Research log and evidence policy

This file is the chronological/conceptual record of the reverse-engineering effort. It should be updated whenever a material discovery changes the model of the protocol.

## Methodology

The project follows this evidence order:

1. search existing public information, prioritizing GitHub and reproducible community findings;
2. inspect official software and firmware artifacts when public information is incomplete;
3. prefer passive/static analysis before live hardware experiments;
4. cross-check independent evidence sources;
5. use the fastest reversible test that can falsify the current hypothesis;
6. keep destructive firmware writes as a last stage.

## Confirmed milestones

### HID interface identified

Normal G3 V2 USB identity is `413D:2131`. The protocol interface is MI_01 / sysfs `:1.1`.

### KeyLinker codec reconstructed

Tables and encode/decode behavior were recovered from `KeyLinkerlib.dll`. Real vectors and CRC behavior match the reconstructed codec.

### Feature report decoded

A real feature request/response was decoded and CRC-validated. Native code mapped the OTA feature bit, confirming OTA support on the G3 V2.

### CustomData communication validated

The `BB` CustomData path was read successfully using the corrected selector. Real readback confirmed the transport, and at least one breathing-blue lighting configuration was exercised.

### Firmware identity handshake closed

Official `GamepadDeviceManager::OnReport` IL resolved the six-byte tuple returned after the `ZXPC_APP` marker.

Real tuple:

```text
01 48 10 06 20 11
```

Confirmed interpretation:
- firmware: **1.48**
- ID #1: `0x1006`
- ID #2: `0x2011`

An earlier simplistic interpretation of raw bytes as a decimal version was discarded.

### Official G3 V2 API identity closed

The MixDrive firmware endpoint was queried with both plausible brands.

Confirmed G3 V2 catalog identity:

```text
brand=MACHENIKE
product_name=G3V2
product_type=2
```

The API returned firmware **1.48** and the official updater artifact. The corresponding `THUNDEROBOT` query returned HTTP 404.

This independently corroborates the live 1.48 handshake.

### MixDrive updater architecture closed

MixDrive:
- checks firmware through its HTTP API;
- downloads a model-specific updater EXE;
- sends a gamepad-specific pre-launch message;
- launches the updater;
- waits for exit.

The actual flash transport is therefore in the native updater, not in the managed MixDrive code.

### DeviceUsb transport reconstructed

`DeviceUsb.dll` was identified as a Windows storage/SCSI wrapper using `CreateFileW` and `DeviceIoControl`.

Its request path was reconstructed as SCSI pass-through-direct with retries.

Known SHA-256:

```text
fad660bc7e006c58f37db9d75b72bf30c089ef62fc4e72673cbfd424d247b5e5
```

### F0/F1/F2 update loop reconstructed

The v1.48 updater:
- uses F1 for hardware/version information;
- sends the complete FOT through sequential F0 transfers;
- checks F2 after the first F0 and after the complete transfer.

The FOT transfer is 722 blocks of exactly 512 bytes.

### FOT container layout recovered

The embedded FOT was extracted without executing updater code.

Confirmed:
- total size: `0x5A400` / 369664 bytes
- internal size field: `0x5A3F8`
- hardware ID: `A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E 22 67`
- version raw: `0x94` -> 1.48
- structured header + high-entropy XCOD/XRES regions
- duplicate 512-byte directory/header copy near the end
- complete transfer is exactly aligned to 512-byte blocks

### Pre-updater messenger closed

Raw ECMA-335 MethodSpec decoding identified the exact transition message:

```text
Machenik.Messages.EnterUpdateModeMessage
```

Inside `<<UpdateFirmwareAsync>b__0>d::MoveNext`, MixDrive checks for gamepad `DeviceType == 2`, constructs this message, copies the firmware-update item's `Device` into it, sends it, and then immediately launches the external updater.

Key tokens:

```text
Send<EnterUpdateModeMessage>      = 0x2B00031E
Register<EnterUpdateModeMessage>  = 0x2B00001E / 0x2B000022
```

This disproves the earlier working possibility that `CheckFirmwareUpdateMessage` might be the transition message; it belongs to a separate workflow.

The earlier zero-hit scan remains classified as a tooling false negative. Raw metadata/body token scanning is now the preferred method for this managed assembly.

### EnterUpdateMode receiver identified

The receiver chain was resolved from InterfaceImpl metadata and direct state-machine token references.

`Machenik.ViewModels.MainViewModel` implements:

```text
IRecipient<EnterUpdateModeMessage>
```

The async state machine `<Receive>d__21::MoveNext` reads `message.Device`, resolves or creates a `GamepadDeviceManager`, calls `GetDeviceCustomDataAsync`, then calls `WriteDeviceCustomDataAsync`.

The full IL dump then closed the mutation itself.

MixDrive calls:

```text
GetDeviceCustomDataAsync(10)
```

and, if the returned byte array is non-null, performs:

```text
data[11] |= 0x80;
```

followed by:

```text
WriteDeviceCustomDataAsync(data)
```

Therefore the official managed-side OTA transition is definitively a **CustomData read-modify-write** that sets bit `0x80` of managed buffer index `11`.

The remaining task is not to find the mutation anymore, but to map managed index `11` through `GamepadDeviceManager` to the exact 10-byte CustomData/on-wire HID byte.


## Open questions

### 1. HID -> update mode transition

This remains the highest-priority unresolved question, but the receiver chain is now largely closed.

Confirmed:
- message type: `EnterUpdateModeMessage`;
- payload: the update item's `DeviceModel`;
- receiver class: `Machenik.ViewModels.MainViewModel`;
- interface: `IRecipient<EnterUpdateModeMessage>`;
- receive state machine: `<Receive>d__21::MoveNext` / token `0x060008F3`;
- the handler obtains/reuses a `GamepadDeviceManager` for the message's HID device;
- it calls `GetDeviceCustomDataAsync()`;
- later it calls `WriteDeviceCustomDataAsync()`.

This strongly indicates that the official HID -> update-mode transition is encoded as a mutation of the normal CustomData object rather than as an unrelated transport.

Confirmed managed mutation:
- `GetDeviceCustomDataAsync(10)`;
- non-null result is modified as `data[11] |= 0x80`;
- the same array is passed to `WriteDeviceCustomDataAsync(data)`.

Need to determine:
- how managed array index `11` maps to the 10-byte CustomData protocol representation;
- exact final HID/KeyLinker packet;
- expected disconnect/re-enumeration;
- timeout/retry behavior.

### 2. Update-mode USB identity

Unknown for the G3 V2:
- VID/PID;
- USB class/subclass/protocol;
- whether it presents as disk, CD-ROM, or another storage/SCSI personality;
- Linux `/dev/sgN` / block-device mapping.

### 3. F1 full response semantics

Known:
- first 16 bytes: hardware ID comparison
- two bytes used as version

Still to document exactly:
- byte order and exact indexes for version in a live G3 V2 update-mode response;
- meaning of remaining response bytes.

### 4. F2 status codes

Only success criterion is currently established from updater code:
- byte 0 == 0 -> success

Nonzero values need semantic mapping.

### 5. Recovery behavior

Before any write test, determine:
- whether update mode can be re-entered after interruption;
- whether F0 writes staging flash or active flash directly;
- whether verification/commit occurs internally;
- whether dual-bank/rollback exists;
- what happens on cable loss.

## External corroboration

Useful related project:

- LopeKinz/Manba-One-V2-Firmware-tools  
  https://github.com/LopeKinz/Manba-One-V2-Firmware-tools

It independently reports a closely related `UsbUpdateAppX + DeviceUsb.dll` / FOT / F0-F1-F2 design.

Policy: use it to identify patterns and save time, but never replace G3 V2-specific static or live evidence with assumptions from another controller.

## Documentation rule going forward

Whenever a new fact is established:
1. update the relevant focused document;
2. update `STATUS.md` if it changes the current state or roadmap;
3. add the milestone or correction here when historically significant;
4. explicitly mark superseded hypotheses as superseded instead of silently deleting the history.


### CustomData manager wrapper semantics resolved

Static IL of `Machenik.Infrastructure.dll` shows that the `10` in `GetDeviceCustomDataAsync(10)` is the method's `timeoutSeconds` argument.

Read path:

```text
CreateTaskCompletionSource(28)
readDeviceCustomData(0)
GetResultAsync(..., timeoutSeconds)
```

Write path:

```text
CreateTaskCompletionSource(29)
writeDeviceCustomData(data, data.Length)
GetResultAsync(..., 10)
```

Thus `data[11] |= 0x80` acts on the native callback/result buffer itself. Later native framing analysis establishes that the logical G3 V2 CustomData object is 20 bytes; the previous 10-byte-size assumption was incorrect.


### Native CustomData framing partially reconstructed

`KeyLinkerlib.dll` exports the three managed P/Invoke targets directly.

`readDeviceCustomData(index)` builds a six-byte `BB 06 ...` command, applies the known KeyLinker transform, and returns a native pointer+length descriptor with length 6.

`receiveDeviceCustomData` is only a wrapper around helper `0x180001760`, copying its 16-byte result descriptor.

`writeDeviceCustomData(data, length)` first creates a length-prefixed stream `[length][data...]`. It processes `length + 1` bytes in chunks no larger than 15 bytes. Each chunk is combined with a four-byte protocol header, one CRC byte, then KeyLinker-transformed.

The exact receive-buffer layout is now isolated to helper `0x180001760` and its backing state.


### receiveDeviceCustomData hidden-return ABI identified

The thin export at `0x180004DD0` is explained by Windows x64 structure-return ABI: `RCX` is the hidden return descriptor, while `RDX` and `R8D` carry the explicit `datas` and `length` arguments. The helper `0x180001760` copies the raw input, decodes it, uses a 20-byte maximum-frame boundary, and begins stripping five framing bytes. This aligns exactly with the write-side 4-byte header + up-to-15-byte chunk + CRC format.

This establishes that the OTA bit mutation at managed index 11 is in logical CustomData, not in raw HID framing.


### KeyLinker codec and CustomData framing closed

Static disassembly of `KeyLinkerlib.dll` closed the core transport codec:

- `0x180001080` is the exact encoder;
- `0x1800011C0` is its inverse decoder;
- table `0x18000B2E0` is the 40-byte XOR stream;
- table `0x18000B308` is the 16-byte position-mix table;
- table `0x18000B320` is the 256-byte CRC/check table;
- the known vector `BB 06 00 29 00 27 -> C0 B3 40 70 99 96` is reproduced exactly.

`writeDeviceCustomData` was also closed at the framing level. It prefixes the logical object with one length byte, fragments the stream into at most 15 bytes per frame, obtains a four-byte header from `0x180001C90`, computes CRC over header+chunk, appends CRC, then applies the KeyLinker encoder. Physical frames are therefore at most 20 bytes.

The receive helper `0x180001760` decodes physical frames, strips five bytes before the first logical fragment (four-byte header plus total-length byte), four bytes before later fragments, drops each final CRC byte, and concatenates the payload.

A prior project assumption was corrected at this point: the G3 V2 `0x3B` CustomData object is **20 bytes**, not 10. The 10-byte length previously recorded belonged to a different decoded payload. This correction resolves the apparent contradiction with the confirmed OTA mutation `data[11] |= 0x80`.

The only remaining component needed for a fully standalone byte-for-byte CustomData writer is the exact formula of `0x180001C90` for the four-byte write header.


### CustomData header bitfields reconstructed

The dedicated dump of `0x180001C90` closed the header field layout used by `writeDeviceCustomData`.

Confirmed:

```text
header[0] = command
header[1] = ((E670 & 7) << 5) | (frame_len & 0x1F)
header[2] = toggle_bit(E67C) << 7 | (fragment_index & 0x0F) << 3 | (E68C & 7)
header[3] = low byte returned by IAT 0x18000B150
```

The binary-string code in the native DLL is not cryptographic at this stage: it simply formats bitfields as ASCII `0`/`1`, concatenates them, and re-evaluates the resulting binary text through `value = 2*value + bit`.

The write loop then advances state after every fragment:

```text
E670 = (E670 - 1) mod 8
E68C = (E68C + 1) mod 8
E67C = !E67C
```

For a 20-byte G3 V2 CustomData write, this makes the second frame header predictable from the first frame state.

Those final header details were subsequently closed:
- `0x18000B214 = "1"` and `0x18000B218 = "0"`;
- the conditional move selects `"1"` exactly when `E67C == 1`, so bit 7 is the boolean `E67C` itself;
- IAT `0x18000B150` resolves to CRT `rand`, giving `header[3] = rand() & 0xFF`;
- helper `0x180001400` is the three-string concatenation helper and preserves argument order.

The raw bytes previously printed for addresses `E670/E67C/E684/E68C` must not be treated as live protocol-state values. These globals are mutable runtime state; their effective values are established/updated by the protocol code.


### Four-byte write header fully reconstructed

The last two unknowns in `0x180001C90` were resolved directly from the PE and import table.

Exact formula:

```python
header0 = command
header1 = ((E670 & 7) << 5) | (frame_len & 0x1F)
header2 = ((E67C & 1) << 7) | ((fragment_index & 0x0F) << 3) | (E68C & 7)
header3 = rand() & 0xFF
```

The constants used to materialize the high bit are ASCII `1` at VA `0x18000B214` and ASCII `0` at VA `0x18000B218`. The imported function at IAT `0x18000B150` is `api-ms-win-crt-utility-l1-1-0.dll!rand`.

Helper `0x180001400` was disassembled and confirmed to allocate a destination and concatenate three NUL-terminated strings in argument order. This validates the field order in header byte 2.

The remaining byte-identical reproduction problem has moved outside the builder: determine the protocol globals' runtime initial/synchronized state and the CRT PRNG seed/state. Until those are known, the formulas are exact but the sequence values at a specific moment cannot be predicted from a cold offline start.


### PRNG scan and security-cookie distinction

A dedicated import/xref scan found `api-ms-win-crt-utility-l1-1-0.dll!rand`, but no `srand` import in `KeyLinkerlib.dll`. Microsoft CRT documentation states that `rand()` before `srand()` follows the same sequence as `srand(1)`, while the generator state is application-scoped by default. This means another module in the same process could still theoretically alter the sequence.

The nearby `GetSystemTimeAsFileTime` and `QueryPerformanceCounter` imports are not evidence of a protocol PRNG seed. Their xrefs resolve to the MSVC security-cookie initialization routine around `0x180009640`, recognizable by the `0x2B992DDFA232` cookie constant and its standard entropy mix.

Most importantly, KeyLinker's decoder reconstructs the transmitted byte 3 from the frame itself. Therefore a synchronized PRNG is not required by the codec. Reproducing the exact MixDrive `rand()` sequence is only necessary for byte-identical output, not for constructing a mathematically valid encode/decode pair. Whether the controller applies any additional semantic validation to this byte remains a separate device-side question.


### writeDeviceCustomData state ordering closed

The complete `writeDeviceCustomData` body at `0x180008EB0` confirms the fragment loop and state ordering unambiguously.

For each fragment:

1. choose at most 15 logical stream bytes;
2. call `0x180001C90(command=0x3B, fragment_index, frame_len)` using the **current** global sequence state;
3. copy the four-byte header;
4. append payload;
5. calculate CRC;
6. KeyLinker-encode the frame;
7. append it to the output;
8. only then update `E67C/E68C/E670`.

Thus the first OTA CustomData fragment inherits whatever transport state exists after the immediately preceding CustomData read/receive path.

A scan across every PE in the extracted MixDrive package found only one relevant CRT import: `KeyLinkerlib.dll -> rand`. No `srand` or `rand_s` was found, including a raw-string/GetProcAddress check.

Microsoft's documented default `rand` sequence provides an independent consistency check: without `srand`, the sequence is equivalent to seed 1, and the published sequence starts `41, 18467, 6334...` = low bytes `29 23 BE...`. The known verified read plaintext `BB 06 00 29 00 27` therefore exactly matches the first default low byte. This is classified as strong corroboration rather than proof of the OTA-time call index.
