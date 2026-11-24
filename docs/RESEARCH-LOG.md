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


### Header builder formula confirmed by real captures

The obfuscated native builder at `0x180001C90` has been reduced to:

```text
header[0] = command
header[1] = ((E670 & 7) << 5) | (frame_len & 0x1F)
header[2] = ((E67C == 1) << 7) | ((fragment_index & 0xF) << 3) | (E68C & 7)
header[3] = rand() & 0xFF
```

Real decoded `0x37` frames `EC/CC/AC/8C` and `81/02/83/04` verify all three state fields and their update direction.

The direct CustomData read path was also closed. `0x1800014C0` returns the read sequence byte and advances only `E67C/E68C`; it does not change `E670`. Verified consecutive reads:

```text
BB 06 00 29 00 27
BB 06 81 23 00 C0
```

prove the state transition `(E67C,E68C) 0,0 -> 1,1`, while the low-byte CRT random stream advances `29 -> 23`.


### Exact managed OTA byte mapping closed

Full static dataflow of `receiveDeviceCustomData` helper `0x180001760` confirms the receive/write symmetry.

For a single decoded frame, the helper shifts away five leading bytes and returns `input_length - 6` bytes: four header bytes plus the one-byte logical-length prefix are removed from the front, while the final CRC is excluded by the returned length. For fragmented input, only the first frame removes the extra logical-length byte; later frames remove four header bytes and their final CRC before concatenation.

For the 20-byte G3 V2 CustomData object, `writeDeviceCustomData` creates the stream `[0x14][data0..data19]` and fragments it as 15 + 6 bytes. Thus the first plaintext frame is `header[4] + 0x14 + data[0..13] + CRC`, total 20 bytes, and the second is `header[4] + data[14..19] + CRC`, total 11 bytes.

The managed OTA mutation therefore maps exactly to:

```text
data[11]  <=>  first plaintext 0x3B frame offset 0x10
```

This closes the managed-index/native-frame ambiguity. The remaining byte-identical OTA reconstruction depends on runtime sequence state (`E670/E67C/E68C`), CRT `rand()` position, the final HID envelope, and post-write re-enumeration behavior.


### Managed dispatch path narrowed to four methods

Static IL confirms that `WriteDeviceCustomDataAsync` creates operation/result type `29`, invokes native `writeDeviceCustomData(data, data.Length)`, and forwards the returned encoded buffer to `GetResultAsync` with a 10-second timeout. The read state machine uses operation/result type `28`.

`GamepadDeviceManager` exposes the downstream transport methods `SendDeviceData`, `SendNextPacketOrFail`, and `SendDeviceDataAsync`, while `OnReport` is the receive-side entry point. Their bodies are the remaining managed slice needed to reconstruct packetization, HID output-report construction, retry/sequence handling, and the ACK that resolves operation/result type 29.

The broad raw-byte scan for `80 F9` / `81 F9` produced hits across many unrelated assemblies and is therefore classified as false-positive-prone/non-evidentiary. Only context-validated IL/dataflow or longer signatures should be used for the final envelope reconstruction.


### 2026-11-20 — HID envelope and ACK semantics proven

Static IL of `GamepadDeviceManager.SendDeviceData` proves the exact host write buffer: `80 F9 <seq> 00 <len> <payload>`, followed by a direct call to `HidLibrary.HidDevice.Write(byte[])`. `OnReport` proves the inverse generic receive framing: report ID `81`, marker `F9`, sequence at Data[1], flags at Data[2], payload length at Data[3], and payload starting at Data[4]. A set bit 0 in flags resolves the sequence's `AckMap` entry as success; otherwise the payload is sent through the receive path.

This matches the previously observed live packets and upgrades the `80 F9 / 81 F9` envelope from empirical/inferred to statically proven. The earlier broad two-byte signature scan remains classified as non-evidentiary; the proof comes from managed IL dataflow.

`GetNextSeq` is lock-protected, increments a byte counter, skips zero, and therefore cycles through 1..255.


Correction: values 28/29 are not task IDs. `CreateTaskCompletionSource` stores the supplied value in `SeqMap[seq]`; they are internal operation/result type values associated with a sequence. The actual object-return completion source is stored in `ResultMap`, while write acknowledgements are tracked separately in `AckMap`.

### 2026-11-20 — CustomData managed valid branch closes read-to-write state count

Focused IL extraction recovered the operation-28 branch omitted by the earlier range script. It explicitly allocates a byte array of `DeviceDataStruct.length`, copies `DeviceDataStruct.data` into it with `Marshal.Copy`, and resolves the pending result with that array.

The same full branch shows that non-final read packets cause a new native `readDeviceCustomData(index+1)` call after managed sequence migration. Because 20 logical CustomData bytes encode into 31 native bytes, the OTA read consumes two native read requests before the managed `data[11] |= 0x80` mutation.

This corrects the native state timeline. Observed/default sequence: selector0 uses SEQ/RAND `00/29`; selector1 uses `81/23`; writer then begins with E67C/E68C `0/2` and next rand `BE`. Conditional on E670=7, writer headers become `3B F4 02 BE` then `3B CB 8B 84`.



### 2026-11-20 — Cold E670 initialization correction

A full `.data`/xref audit corrected the previous conditional assumption that the CustomData writer might begin with `E670=7`. The file-backed `.data` bytes end long before the protocol globals at `0x18000E670+`, and no initializer writes 7 into `E670`. Cold state is therefore zero.

Because the two native CustomData reads advance only `E67C/E68C`, the cold OTA writer begins with `E670=0, E67C=0, E68C=2`. The resulting default-observed headers are `3B 14 02 BE` and, after zero wraps to seven, `3B EB 8B 84`.

This supersedes `3B F4 02 BE / 3B CB 8B 84` as the cold-sequence candidate. That older pair remains possible only after prior write-style traffic has advanced process-global `E670`.


### 2026-11-20 — E670 proven as write-frame phase

A complete write-xref audit showed that every confirmed `E670` update is the same wrap/decrement operation performed after native write-frame generation. `writeDeviceCustomData` (`0x3B`) is one such writer; two other multipart writers use commands `0x36` and `0x37`. This establishes `E670 = (-W) mod 8` from cold state and explains why a warm MixDrive session can enter OTA with a different first-header high field than a freshly loaded DLL.


## 2026-11-20 — Official updater SCSI call graph closed

The updater's indirect `deviceUsb_Request` calls were resolved from `0x005F9864`.

Confirmed:
- F1 is a 20-byte DataIn identity/version request.
- F0 is a DataOut firmware block write, max 512 bytes per call.
- F2 is a 20-byte DataIn status request.
- F2 is checked after the first F0 block and again after the full image.
- Success criterion is `F2_response[0] == 0`.
- the v1.48 image is 722 x 512-byte F0 blocks.
- each request passes CDB length 10 even though only six CDB bytes are explicitly initialized in the caller.

No device I/O was performed.


## 2026-11-20 — Update-mode drive identity recovered

Static analysis of updater function `0x00402A70` recovered its bootloader discovery loop. It scans A..Z from `GetLogicalDrives`, only examines `DRIVE_REMOVABLE` entries, opens each candidate with `deviceUsb_OpenByDrv`, obtains `deviceUsb_GetInfo`, and matches the exact 28-byte identity string `Gamepad Updater         1.00`.

The 28-byte length splits exactly as SCSI INQUIRY's 8-byte Vendor + 16-byte Product + 4-byte Revision fields, yielding the strong hypothesis Vendor=`Gamepad `, Product=`Updater         `, Revision=`1.00`. The DeviceUsb implementation still needs to confirm the source of those fields.


## 2026-11-20 — DeviceUsb storage identity implementation closed

Disassembly of `deviceUsb_GetInfo` at `0x10001200` proved that the updater does not obtain its bootloader identity by directly issuing INQUIRY from that function. It calls `IOCTL_STORAGE_QUERY_PROPERTY (0x002D1400)`, requires `BusTypeUsb (7)`, and extracts the standard `STORAGE_DEVICE_DESCRIPTOR` Vendor/Product/Revision strings. This confirms the exact identity tuple `Gamepad ` / `Updater         ` / `1.00`.

The firmware command path is distinct: `deviceUsb_Request` uses `IOCTL_SCSI_PASS_THROUGH_DIRECT (0x0004D014)` for the F0/F1/F2 CDBs.


## 2026-11-20 — deviceUsb_Request / SPTD wrapper closed

Static disassembly of `deviceUsb_Request` proved the official updater ABI `(handle, data_in, cdb, cdb_len, buffer, buffer_len)`. F0 passes data_in=0; F1 and F2 pass data_in=1. The function constructs a 0x2C-byte x86 `SCSI_PASS_THROUGH_DIRECT`, sets PathId=0, TargetId=1, Lun=0, SenseInfoLength=0x20, TimeOutValue=5, SenseInfoOffset=0x30, copies the caller CDB, and issues `IOCTL_SCSI_PASS_THROUGH_DIRECT (0x0004D014)` using a 0x50-byte local buffer for both input and output. Failed IOCTLs retry up to 10 attempts with 20 ms sleeps, except error 0x37 which terminates immediately.

The remaining GetInfo CRT ambiguity was also removed: IAT slots 0x10002090 and 0x1000208C are `strcpy_s` and `strcat_s`.


## 2026-11-20 — Official updater sends uninitialized bytes in 10-byte vendor CDBs

The updater initializes only six bytes of each local F0/F1/F2 CDB despite passing a length of 10 to `deviceUsb_Request`. F0/F1 bytes 6..9 are uninitialized stack contents. F2 is stronger evidence: its byte range overlaps the stack-cookie slot, so CDB bytes 8..9 are the low two bytes of the function cookie. This confirms an implementation bug in the original updater and argues against byte-for-byte emulation of its stack garbage.

Canonical clean-room plan: zero all 10 CDB bytes and set only opcode byte 0; validate F1/F2 first; never infer that trailing garbage is protocol-significant without device evidence.


## 2026-11-20 — Transition test narrowed to HID OTA bit only

The next live experiment is intentionally bounded before SCSI: read the real 20-byte CustomData through BB selectors 0/1, validate CRCs, set only logical byte 11 bit 0x80, write it with command 0x3B, then observe re-enumeration. `tools/g3v2-enter-updater-linux.py` implements that sequence with explicit `--execute` gating and no F0/F1/F2 support.

External family corroboration: the Manba One V2 research project reports the same mass-storage identity (`Gamepad` / `Updater` / `1.00`) and vendor SCSI command family and documents exit from its update mode by unplug/replug. This is supporting evidence for a temporary bootloader personality, not proof of G3 V2 recovery behavior.

## 2026-11-20 — Documentation consistency pass: vendor bugs are not protocol requirements

The repository was audited after the cold-state and updater-stack findings. An obsolete one-read CustomData paragraph was found in `MIXDRIVE-MESSENGER.md`; it has been replaced with the proven two-read selector 0/1 sequence and cold headers `3B 14 02 BE / 3B EB 8B 84`.

The updater documentation was also normalized around a key engineering rule: the original vendor application contains at least one confirmed implementation bug. It declares 10-byte F0/F1/F2 CDBs while leaving trailing bytes uninitialized, including stack-cookie overlap in F2. Those bytes are therefore not treated as protocol semantics. Clean-room implementations should prefer deterministic zero-filled CDBs and validate read-only behavior first.

## 2026-11-20 — Live probe parser correction before OTA write

The first controlled Linux transition attempt aborted safely before any OTA write. The probe sent selector 0 as outer sequence 1 without first reproducing the two successful `ZXPC_APP` identity pings. The controller returned a valid six-byte KeyLinker response which decodes to `19 06 00 24 00 B2`, i.e. command `0x19` with zero logical payload. The probe then rejected it because it had incorrectly hard-coded a 20-byte native selector-0 response.

Earlier successful live evidence is more authoritative for this read path: after two `ZXPC_APP` pings, selector 0 at outer sequence 3 returned a 16-byte encoded response decoding to `19 10 00 6B 0A <10 data bytes> 28`. The probe has therefore been corrected to reproduce the two identity pings first, use outer sequences 3/4 for selectors 0/1, parse response command `0x19` by its declared logical length, and combine two 10-byte selector payloads into the 20-byte logical CustomData object. No OTA bit was written during the failed attempt.

## 2026-11-20 — Corrected dry-run returns CRC-valid empty 0x19 frame

With the same live order as the earlier successful A/B test (queue drain, two `ZXPC_APP` pings, selector 0 at outer sequence 3), the controller returned `B4 7B 0E 42 F1 DB`, decoding to `19 06 00 58 00 4A`. CRC validates and logical length is zero. The script stopped before any write.

Earlier live evidence remains: selector 0 once returned `19 10 00 6B 0A 00 01 00 00 00 00 40 04 00 02 28`, i.e. 10 logical bytes, and selector 1 returned the same 10-byte payload with its own state/CRC. This means current documentation that promotes a 20-byte logical object from static interpretation is not yet fully reconciled with the device. No more live write attempts should occur until either a true power-cycle restores the known-good read or the managed packet-continuation criterion is recovered from IL.

## 2026-11-20 — Full power-cycle still yields empty selector-0 payload

After a real controller power-off/disconnect/reconnect cycle, normal USB enumeration returned as `413D:2131`, the two `ZXPC_APP` identity pings succeeded, and selector 0 at outer sequence 3 returned `8D D2 63 51 F6 5F`, decoding to CRC-valid `19 06 00 0A 00 EE` with logical length zero.

Therefore the empty response is not explained by a stale hidraw node or by merely retaining the previous protocol session across reconnect. Live writes remain frozen. A new read-only CIL helper, `tools/dump-gamepad-manager-il.py`, was added to recover the exact packet-count/continuation condition in `GamepadDeviceManager`.

## 2026-11-20 — CheckPacketIndex proves selector continuation is conditional

Focused CIL of `GamepadDeviceManager.CheckPacketIndex` shows that the first response packet is passed to native `receiveDataLength`. Managed code computes `ceil(total/20)` as `(total + 19) / 20` and stores it in `LengthMap`. It appends the received bytes to `DataMap` and returns the current packet index.

Handlers compare the returned index with `LengthMap[seq] - 1`. Only a non-final result causes index increment, `MigrateSeq`, and a new native read request for `index + 1`. `MigrateSeq` simply migrates all pending maps to a fresh outer HID sequence.

This invalidates the earlier unconditional-two-read interpretation. Whether selector 1 is issued depends entirely on native `receiveDataLength`. That function is now the next static target.

## 2026-11-20 — Operation 28 branch fully recovered

The normalized switch offsets place CustomData read at `IL+0x0B89` and CustomData write at `IL+0x0CDA`. The full read branch proves that a final packet is decoded through `receiveDeviceCustomData`, checked for `data != NULL` and `0 < length <= 100`, copied with `Marshal.Copy`, and returned. A non-final packet increments the index, migrates maps to a fresh outer sequence, calls `readDeviceCustomData(index+1)`, and sends it.

Combined with the later `receiveDataLength` and `OnReport` findings, the earlier zero-return edge-case hypothesis is superseded. The normal HID path passes `type=0`; a decoded logical length of 0 maps to a native total length of 6, hence one outer packet. The handler finalizes selector 0, `receiveDeviceCustomData` returns logical length 0, and the managed task resolves to `null`.

## 2026-11-20 — receiveDataLength native size formula recovered

The managed wrapper token `0x06000017` resolves to `Libs\\Gamepad\\KeyLinkerlib.dll!receiveDataLength`, RVA `0x4A20`.

For the native branch where the third argument is not 1, the function KeyLinker-decodes the input, reads decoded byte 4 as the logical payload length, and returns `logical_len + 6 + 5*floor(logical_len/15)`. Therefore the observed empty frame maps to total native length 6, the earlier 10-byte CustomData response maps to 16, and a hypothetical 20-byte logical object maps to 31.

A separate `third_arg == 1` branch exists and encodes a wider/special length field. The next static step is to identify the exact value passed from `OnReport` into `HandleGenericDeviceData(..., arg4)` and then into `receiveDataLength` for CustomData.

## 2026-11-20 — OnReport closes type=0; official behavior for 0/10/20-byte CustomData is now deterministic

`OnReport` parses normal generic responses as `seq=data[1]`, flags from `data[2]`, payload length from `data[3]`, payload at `data[4:]`, then calls `HandleGenericDeviceData(seq, segment, length, 0)`. Thus CustomData uses `receiveDataLength` type 0.

Applying the recovered native formula:
- the current 6-byte native frame with logical length 0 is one packet; `receiveDeviceCustomData` returns length 0 and managed returns `null`, so `MainViewModel` skips OTA;
- the earlier 16-byte native frame with logical length 10 is one packet; managed returns `byte[10]`, after which the official `data[11]` mutation would index past the end of the array;
- only a logical length of at least 12 can make index 11 valid, and the reconstructed 20-byte case maps to 31 native bytes / two outer packets.

The Linux probe was updated to mirror this official packet-count behavior and to block `--execute` unless the managed-equivalent read produces exactly 20 bytes.

## 2026-11-20 — Firmware-update sender is gated by DeviceType == 2

Static xrefs in `Machenik.exe` show that `<UpdateFirmwareAsync>b__0>d::MoveNext` sends `EnterUpdateModeMessage` only when `firmwareUpdate.Device.DeviceType == 2`. Immediately after sending the message, it launches the extracted external updater process and waits for it to exit.

The current live test setup is USB-only; the 2.4 GHz dongle is disconnected. Therefore the next static target is to resolve the exact enum meaning of `DeviceType == 2` before attributing the failed/empty CustomData read to USB versus receiver mode.

## 2026-11-20 — DeviceType value 2 is Gamepad, not USB/2.4G

Static metadata in `Machenik.Shared.dll` resolves `DeviceType` as `Unknown=0, Keyboard=1, Gamepad=2, Mouse=3`. Thus the `DeviceType == 2` check before `EnterUpdateModeMessage` merely gates the gamepad firmware path and provides no evidence that OTA requires wired USB or the 2.4 GHz receiver.

The live test context is still USB-only with the dongle disconnected. Any transport-specific restriction must be represented elsewhere in the model/manager/update code.

## 2026-11-20 — G3V2 physical HID and logical ZXPC_APP identities reconciled

Focused `LoadGamepadAsync` IL closes the six-byte tuple returned by
`ConnectDeviceAsync(3)`. `Item1/Item2` become firmware version 1.48;
`(Item3 << 8) | Item4` and `(Item5 << 8) | Item6` are passed directly to
`GamepadDeviceHelper.FindGamepadInfo`.

For the live tuple `01 48 10 06 20 11`, this is:

```text
firmware: 1.48
logical VID:PID: 1006:2011
```

The controller therefore has a physical HID identity `413D:2131` and a
logical gamepad identity `1006:2011`. The latter is explicitly associated
with the G3V2 mapping by MixDrive. The loader keeps the physical HidDevice but
attaches the GamepadInfo resolved from the logical tuple.

This also closes the transport-name detour: `DeviceType=2` is Gamepad, and
`DeviceMode` is a model string rather than USB/2.4G state. No managed
receiver requirement has been found before `EnterUpdateModeMessage`.

## 2026-11-20 — Cold CustomData probe differs from MixDrive's warm manager state

Cross-references show that normal MixDrive initialization invokes
`GetDeviceCustomDataAsync` from multiple hardware loaders (light, motor,
rocker, sensor, and generic CustomData paths) before a later firmware update.
The OTA receiver reuses the cached `GamepadDeviceManager` when present.

Therefore the current zero-length response from a standalone cold probe is
not sufficient evidence of a transport mismatch. The next task is to recover
the exact read-only initialization order and reproduce only the necessary
warm-up sequence before reconsidering any OTA write.

## 2026-11-20 — Official warm-up order and CustomData xrefs recovered

A focused CIL audit of `GamepadConfigInitializer.InitializeFromHardwareAsync`
recovered the startup sequence:

```text
LoadKeyMapAsync
LoadTurboAsync
LoadRockerAsync
LoadTriggerAsync
LoadMotorAsync
LoadLightAsync
LoadCustomDataAsync
```

The same audit found three `GetDeviceCustomDataAsync` load/initialize call
sites and no `WriteDeviceCustomDataAsync` call sites in those methods.

The warm-up is still not classified as completely passive because the loaders
perform other `GamepadDeviceManager.GetDevice*Async` operations first.
Confirmed examples are key-map, turbo-support, rocker, trigger, and motor
reads. These may advance KeyLinker sequence/random state even if they do not
change user configuration.

A new read-only helper,
`tools/dump-gamepad-warmup-manager-map.py`, was added to map those manager
methods to their native request builders and operation/result types before any
live warm-up reproduction.

## 2026-11-20 — Warm-up manager map fixed to cross assemblies

The first focused manager-map run showed only
`GetDeviceCustomDataAsync` / `GetDeviceAfterKeyChangeListAsync` and zero
manager state machines because the helper scanned only `Machenik.exe`.

That result is invalid as a protocol conclusion. The concrete
`GamepadHardwareLoader` state machines are in `Machenik.Shared.dll`, while
the corresponding `GamepadDeviceManager` async state machines are in
`Machenik.Infrastructure.dll`.

The helper was corrected to scan all three first-party assemblies and perform
the cross-assembly correlation explicitly.

## 2026-11-20 — Concrete initializer warm-up is 11 reads, four CustomData

The corrected EXE/Shared/Infrastructure correlation closes the actual
`GamepadConfigInitializer` path. The concrete loader is
`Machenik.Shared.Services.GamepadHardwareLoader`, not the similarly named
ViewModel helper methods in `Machenik.exe`.

Sequence:

```text
BeforeKeyChangeList (op 9)
SupportTurbo        (op 16)
CurrentTurbo        (op 17)
Rocker              (op 12)
CustomData          (op 28)
Trigger             (op 14)
Motor               (op 19)
CustomData          (op 28)
Lamplight           (op 21)
CustomData          (op 28)
CustomData          (op 28)
```

All eleven baseline operations are read paths. No `WriteDeviceCustomDataAsync` occurs in the initializer/load path. Retries and multi-packet continuation can increase the actual native request count beyond eleven. The next static target is to inspect the native
KeyLinkerlib implementations of these request builders and determine which
ones share the same state/rand generator as `readDeviceCustomData`.

## 2026-11-20 — Warm-up operations 3-11 share KeyLinker state/rand/encoder

Native disassembly confirms that `readDeviceTurboData`, `readRockerData`,
`readTriggerData`, `readDeviceMotorData`, `readDeviceLamplightData`, and
`readDeviceCustomData` all directly call the same state helper
(`0x1800014C0`), the same CRT `rand()` IAT slot (`0x18000B150`), and the
same KeyLinker encoder (`0x180001080`).

Thus the initializer baseline from operation 3 through operation 11 is
definitively stateful. Before its final CustomData read, the official app has
already executed at least eight prior requests that advance the same
state/rand stream.

The only unresolved baseline reads are operations 1 and 2, whose wrappers call
generic helper `0x180001BE0` with selectors 3 and 4. That helper must be
disassembled before deciding whether the exact offset is eight or ten earlier
stateful requests.



## 2026-11-20 — KeyLinker state-byte cycle implication

The observed cold single-frame state-byte sequence cycles every eight calls
(`00,81,02,83,04,85,06,87`). If operations 1-2 do not consume the same
state helper, the final initializer CustomData read can therefore return to
state byte `0x00` while the CRT `rand()` stream is already eight calls
ahead. A state-byte match alone is not sufficient evidence that the Linux
probe matches MixDrive's request context.

## 2026-11-20 — Generic read helper closes operations 1-2 as stateful

Disassembly of `0x180001BE0` proves that the generic read helper used by
BeforeKeyChangeList and SupportTurbo directly calls the same
`KeyLinkerState`, CRT `rand()`, and KeyLinker encoder as the other read
builders. Its own function returns at `0x180001C87`; the later `rand()`
at `0x180001EE3` is part of the separate function starting at
`0x180001C90`.

Thus all eleven baseline initializer reads consume one state/rand pair per
native request. Under a cold no-retry/no-continuation baseline, the final
CustomData is request #11 and uses state/rand `02/49`, yielding plaintext
`BB 06 02 49 00 D4` and encoded wire `EE 95 26 90 B7 65`.

A new bounded live helper,
`tools/g3v2-mixdrive-warmup-readonly.py`, was added. It defaults to plan-only
and requires `--run-readonly` to send the read-request sequence. It contains
no OTA write or SCSI support.

## 2026-11-20 — Full read-only warm-up still returns zero-length CustomData

A live USB-only reproduction of the concrete 11-read initializer baseline
completed without any write. All ordinary configuration reads returned valid,
non-empty data, while every CustomData read returned logical length zero.
There were no continuation packets, so the final request was exactly baseline
request #11 with state/rand `02/49`.

This rules out missing warm-up/state progression as the sole explanation for
the current null CustomData result.

Static loader behavior provides an important reinterpretation:
`LoadRockerAsync` explicitly requires a non-null 20-byte CustomData block
before applying its extended fields, while null simply skips them. Light and
motor loaders also tolerate null. CustomData therefore behaves as optional
extended configuration state rather than a mandatory base configuration
record.

A new static helper, `tools/dump-customdata-semantics.py`, was added to map
all managed CustomData read/write call sites, G3V2 branches, length checks, and
builder/mutation logic before any further live write experiment.



## 2026-11-20 — Hardware-loader sequence and G3V2 CustomData semantics

Static CIL audit of `GamepadHardwareLoader` closed the manager calls used by
the normal KeyMap/Turbo/Rocker/Trigger/Motor/Light loaders. Rocker, Motor and
Light each perform a `GetDeviceCustomDataAsync` read after their specialized
manager read.

Key findings:

- `GetDeviceCustomDataAsync(10)` still means a 10-second timeout; the native
  CustomData selector remains `readDeviceCustomData(0)`.
- `LoadRockerAsync` requires a non-null logical CustomData payload of exactly
  20 bytes.
- G3V2-specific rocker semantics consume byte 3 nibbles, byte 8 and byte 10;
  shared fields also use bytes 2, 7, 12 and 13.
- `LoadMotorAsync` consumes CustomData byte 4 for `MotorGripMode`.
- `LoadLightAsync` obtains R/G/B from `GetDeviceLamplightAsync` /
  `DeviceLampLightStruct`, then uses CustomData byte 1 for light mode and
  byte 0 for logical brightness.
- Therefore CustomData/BB is a shared configuration object, not the primary
  RGB carrier.

A methodological correction was recorded: the section order printed by
`dump-gamepad-hardware-loader-sequence.py` is fixed by its `TARGETS`
tuple. The actual global sequence remains established by the separate
`InitializeFromHardwareAsync` audit.

Added `tools/dump-gamepad-manager-read-paths.py` to bridge the normal manager
getters to their underlying `GamepadInterop`/KeyLinkerlib calls. This is the
next step toward replacing the approximate Linux warm-up with the exact
read-only MixDrive initialization sequence.


## 2026-11-20 — Managed getters mapped to KeyLinkerlib exports

The new `dump-gamepad-manager-read-paths.py` output closed the
GamepadDeviceManager-to-KeyLinkerlib bridge for the normal initialization
sequence.

Confirmed operation/result types and native builders:

```text
9  -> readDeviceBeforeKeyChangeList(0)
12 -> readRockerData()
14 -> readTriggerData()
16 -> readDeviceSupportTurboList(0)
17 -> readDeviceTurboData(0)
19 -> readDeviceMotorData()
21 -> readDeviceLamplightData(0)
28 -> readDeviceCustomData(0)
```

Receive-side dispatch is symmetric and resolves to the matching
`receive*` exports. The operation values are internal managed dispatcher
types and must not be confused with native KeyLinker commands.

`HandleGenericDeviceData` also confirms continuation semantics for segmented
reads: after a partial response, it increments the packet index, migrates the
managed sequence and invokes the same native read function with the next
index. This behavior is explicitly visible for BeforeKeyChange,
SupportTurbo, CurrentTurbo and CustomData.

All confirmed functions are direct P/Invokes into
`Libs\\Gamepad\\KeyLinkerlib.dll`.

Next target: run `tools/dump-keylinker-warmup-native.py` against the extracted
KeyLinker DLL to recover the actual native command builders. Priority is
LampLight read/write for Ambilight and the exact warm-up reads needed before
CustomData.


## 2026-11-20 — LampLight command IDs and dedicated 0x37 writer

Native audit of `KeyLinkerlib.dll` confirmed the specialized read-command
family:

```text
B1 06 = Rocker
B2 06 = Trigger
B3 06 = Motor
B4 06 = current Turbo
B6 06 = AfterKeyChange list
B7 06 = LampLight
BB 06 = CustomData
```

Each specialized read uses `KeyLinkerState`, consumes one CRT `rand()`
value, finishes a six-byte plaintext request and applies
`KeyLinkerEncode`.

The major Ambilight result is the write side:
`writeDeviceLamplightData` passes command `0x37` to the already-reversed
`HeaderBuilder`, fragments the logical object in chunks of at most 15 bytes,
appends the transport checksum/CRC and runs each frame through
`KeyLinkerEncode`.

This gives a clean native command pairing:

```text
LampLight   B7 read / 37 write
CustomData  BB read / 3B write
```

The LampLight writer is therefore the primary target for dynamic RGB control.

Added `tools/dump-lamplight-detail.py` to print the complete
`readDeviceLamplightData`, `receiveDeviceLamplightData` and
`writeDeviceLamplightData` bodies, enumerate managed
`DeviceLampLightStruct` fields/layout, print LampLight P/Invoke signatures
and locate all managed call sites. This should close the logical payload
format without device I/O.


## 2026-11-20 — LampLight 0x37 payload reconstructed

Full native LampLight audit closed the logical write format.

`LampLightStruct` contains six sequential `int32` values:
`R, G, B, breathSpeed, breathSwitch, brightness` (24 bytes total).
`DeviceLampLightStruct` contains four such records followed by
`lampLightNumber` at offset `0x60`.

`writeDeviceLamplightData` serializes:

```text
length = 6 * lampLightNumber

stream[0] = length
for each lamp:
    append R & 0xff
    append G & 0xff
    append B & 0xff
    append packed_byte_0
    append packed_byte_1
    append brightness & 0xff

The CRT IAT was subsequently resolved:
0x18000B130=strcpy_s, 0x18000B138=strncpy_s, 0x18000B140=strcat_s.
The writer copies the one-character breathSwitch string first and appends the
15-character breathSpeed string. It then parses chars 8..15 as the low byte
and chars 0..7 as the high byte. Therefore:

    packed = ((breathSwitch & 1) << 15) | (breathSpeed & 0x7fff)

and the protocol transmits packed little-endian.
```

The native receive function performs the inverse expansion from each six-byte
record into six 32-bit managed fields. This also explains the binary-string
construction visible in the compiler output: it is implementing a 15-bit
speed plus one-bit switch packing.

Thus a one-lamp write has a seven-byte logical object:

```text
06 R G B packed_lo packed_hi brightness
```

This stream is then fragmented into <=15-byte chunks and wrapped as command
`0x37` with the already-known KeyLinker header/checksum/encoding.

`readDeviceLamplightData(index)` plaintext is:

```text
B7 06 state rand index crc
```

Managed operation types are now confirmed as 21 for LampLight read and 22 for
LampLight write.

Write call sites include SaveLightAsync, ResetLightAsync,
ApplyCurrentConfigAsync and ChangeGamepadConfigAsync. That establishes 0x37
as the normal settings-write path, but does not establish RAM-vs-NVM
persistence. Do not use it as a high-frequency Ambilight transport until that
is separately proven.


### Correction note — LampLight speed/switch bit order

The first reconstruction correctly established the six-byte per-lamp record,
but prematurely assigned the exact bit position of `breathSwitch`. Native
code clearly converts `breathSpeed` to a 15-character binary string and
`breathSwitch` to a one-character binary string, then concatenates them and
splits the resulting 16 characters into two bytes. The order of that
concatenation depends on which CRT helpers occupy IAT slots
`0x18000B130`/`0x18000B140`. Until those imports are resolved, only the
record size and R/G/B/brightness positions are treated as confirmed.


## 2026-11-20 — LampLight bit packing closed; no alternate gamepad RGB API exposed

Static IAT resolution identified the LampLight string helpers as
`strcpy_s`, `strncpy_s` and `strcat_s`. This removes the final ambiguity
from the per-lamp six-byte record: `breathSwitch` is bit 15 and
`breathSpeed` occupies bits 14..0, transmitted low byte then high byte.

A full lighting API inventory found no second gamepad lighting writer in
`KeyLinkerlib.dll`; only read/receive/write DeviceLamplight are exported.
RGB/LED APIs discovered elsewhere belong to keyboard/mouse backends.

Consequently `0x37` remains the only MixDrive-exposed G3V2 RGB write path.
The next blocker is whether firmware treats 0x37 as volatile state or commits
persistent memory. Managed call sites show writes in SaveLightAsync,
ResetLightAsync, ApplyCurrentConfigAsync and ChangeGamepadConfigAsync, while
ChangeLightAsync itself only updates the view model. That pattern is
compatible with a settings write and should not be assumed safe for high-rate
streaming until persistence is proven.


## 2026-11-20 — 0x3B is not a LampLight commit

A focused audit of the managed lighting persistence paths resolves the
host-side ordering question.

`WriteDeviceLamplightAsync` creates operation/result type 22 and directly
invokes native `writeDeviceLamplightData` / command 0x37. CustomData write
remains operation type 29 / command 0x3B.

`SaveLightAsync` first performs `GetDeviceCustomDataAsync(10)`, updates
CustomData byte 0 with UI light brightness and byte 1 with LightModeIndex,
then calls `WriteDeviceCustomDataAsync`. The applicable LampLight branch is
built and written only afterwards. `ResetLightAsync` has the same broad
ordering: CustomData write first, LampLight write later.

Conversely, other apply/config-change flows contain the reverse order:
LampLight 0x37 first, then CustomData read/update and 0x3B.

This rules out the working hypothesis that 0x3B is a mandatory commit/finalize
step for a preceding 0x37. 0x3B and 0x37 carry two different lighting-related
state layers whose ordering varies by caller. No additional managed
gamepad-lighting commit call was found after 0x37.

The remaining unresolved question is firmware-side persistence of 0x37
itself. Static host code cannot distinguish an immediate NVM write from a
volatile apply followed by firmware-internal retention.


## 2026-11-20 — FOT scan classifies package as transformed data

`dump-g3v2-fot-layout.py` was run against the official v1.48 updater and
carved exactly `0x5A400` bytes from file offset `0x1712F0`.

Result:
- SHA-256 `0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085`;
- total entropy 7.9968 bits/byte;
- almost every 4 KiB block ~7.95 bits/byte;
- no XCOD, XRES, ZXPC, G3V2 or MACHENIKE marker in the carved FOT;
- no credible Cortex-M vector table;
- 201 target-immediate Thumb-CMP-shaped byte pairs and four small clusters,
  but the surrounding bytes remain high-entropy/random-looking, so these are
  statistical false positives rather than protocol-handler evidence.

Correction: the earlier documentation must not describe the carved FOT itself
as containing XCOD/XRES regions. Those observations came from wider updater
analysis. The exact transferred package is presently classified as encrypted,
compressed or otherwise transformed data.

This means following handler 0x37 inside the package first requires recovering
the package transform. In parallel, obtaining a post-install firmware dump from
the controller becomes more valuable.

## 2026-11-20 — Firmware dump track opened; ZhiXu prior art found

The exact G3V2 FCC filing `2BBT5-G3V2` now exposes five pages of internal
photos (document `8099258`). These are the preferred source for identifying
the MCU, external flash and debug pads before opening the user's controller.

External project `AmjadAAYD/x20ctl` independently reversed another
KeyLinker/ZhiXu Downloader platform. Its documented updater hardware-ID family
uses the exact prefix `A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E` seen in
our G3V2 package, and lists model code `2267`. Our hardware ID ends `22 67`
and the official filename is `ZXD2267_...`.

That project found `deviceUsb_Request` to be a generic
`IOCTL_SCSI_PASS_THROUGH_DIRECT` wrapper, but its updater has only 20-byte
DATA_IN identity/status calls and 512-byte DATA_OUT firmware chunks: no bulk
firmware readback. This is highly relevant family-level prior art, not yet
G3V2-specific proof.

Added `tools/dump-deviceusb-readback-surface.py` to independently recover
the G3V2 DeviceUsb exports, DeviceIoControl/SCSI wrapper, F0/F1/F2 call
contexts, transfer lengths and data directions without touching hardware.


## 2026-11-20 — Normal-mode readback inventory added

Added `tools/dump-keylinker-firmware-readback.py` to scan KeyLinkerlib and the
first-party managed assemblies for firmware/flash/ROM/memory read primitives
and suspicious large-transfer/address constants. It performs static parsing
only and applies a strict rule: identity/version/status reads do not count as a
firmware-dump path unless code proves scalable addressable reads.


## 2026-11-20 — Physical label confirms exact FCC filing

A photograph of the user's actual controller label confirms:
- product marking: Machenike G3 V2 Tri-mode Controller;
- FCC ID: `2BBT5-G3V2`;
- manufacturer: Qingdao Machenike Technology Co., Ltd.

This makes the `2BBT5-G3V2` FCC filing an exact-hardware reference for this
unit rather than a same-family inference. Any unique per-unit production or
serial code visible in the photograph is deliberately excluded from project
documentation.


## 2026-11-20 — G3V2 updater request directions closed

The focused static run against the exact official v1.48 updater resolves the
argument pattern of the dynamically loaded `deviceUsb_Request` function as:

`(handle, data_in, cdb_ptr, cdb_len, data_ptr, data_len)`

The firmware-update path contains:
- F1 / DATA_IN / 20 bytes at 0x402644;
- F0 / DATA_OUT / <=512 bytes at 0x402872;
- F2 / DATA_IN / 20 bytes at 0x402897;
- F2 / DATA_IN / 20 bytes at 0x4028DE.

The F0 site is in the loop that copies 0x200-byte chunks from the embedded FOT
before sending them. The F2 sites poll one-byte status inside a fixed 20-byte
response buffer. No bulk or addressable DATA_IN call appears in the official
updater firmware-transfer path.

Conclusion: the official updater itself offers no firmware backup/readback
route. This matches the behavior independently documented for another
ZhiXu/KeyLinker Downloader-family updater, but the G3V2 conclusion is based on
our own binary, not that prior art.

The concurrent KeyLinker inventory also found no exposed
firmware/flash/ROM/memory read API in normal mode. Existing exports are
configuration/status readers only.

Created `tools/dump-g3v2-updater-request-table.py` for a concise,
hash-guarded static proof of the exact request table.


## 2026-11-20 — ZhiXu family points toward internal MCU flash

External research found a useful family-level hardware clue. ZhiXu's own
controller-PCBA material describes a custom MCU with integrated Flash and no
external EEPROM. Independent ZXD2222 controller teardowns identify a
`ZC32RF1M` QFN40 ZhiXu controller next to a 26 MHz crystal, and multiple FCC
records corroborate that part in unrelated gamepads.

This is not evidence that G3V2 uses the same chip. It only changes physical
dump priority: inspect the exact G3V2 internal photos for the main QFN marking
and debug/test pads before expecting a clip-readable external flash.


## 2026-11-20 — Exact request-table verifier returned four and only four calls

The dedicated `dump-g3v2-updater-request-table.py` run matched the expected
official updater SHA-256 and located the four known indirect calls through
`0x005F9864`:

- 0x402644 F1 DATA_IN 20 B;
- 0x402872 F0 DATA_OUT <=512 B;
- 0x402897 F2 DATA_IN 20 B;
- 0x4028DE F2 DATA_IN 20 B.

The inventory count was exactly 4 and reported no extra request-pointer call
sites. This closes the host-side updater request table and confirms that no
bulk/addressable DATA_IN firmware-read command is present in the official
v1.48 updater.


## 2026-11-20 — Same-family ZhiXu FOT research opens a second-image route

Recent public reverse-engineering of Manba One V2 (`ZXD2055`) and Anbernic
RG G01 (`ZXD2271`) independently matches our updater architecture:
`UsbUpdateAppX`, `DeviceUsb.dll`, SCSI F1/F0/F2 and the common
14-byte hardware-ID prefix.

The Manba research found:
- plaintext FOT header/directory;
- encrypted high-entropy XCOD/XRES regions;
- firmware transmitted byte-for-byte to the bootloader;
- same-hardware-ID sibling versions behaving like
  `ciphertext = plaintext XOR reused_section_keystream`;
- different hardware IDs producing unrelated keystreams;
- no useful USB read-flash command.

An October 2026 coordination issue for ZXD2271 reports the same FOT family and
the same embedded-image offset 0x1712F0 used by its updater, reinforcing that
this is a vendor platform rather than a brand-specific design.

This prompts a correction in investigation order: before treating our
0x1712F0 slice as the complete encrypted object, scan the whole G3V2 updater
for plaintext FOT metadata and exact hardware-ID placement.

A separate public G3 V2 test from 2025-01-28 reports firmware v1.25. This proves
an older firmware generation existed. If an updater/image for that generation
can be recovered and its F1/header hardware ID is also
`a1ca51da724cb6103658c05d7d4e2267`, it becomes the best current
non-invasive cryptanalytic lead.

Added `dump-g3v2-fot-container-surface.py` and
`find-zxd2267-firmware-corpus.py`.


## 2026-11-20 — FCC close-up confirms ZC32RF1M on exact G3V2

The user supplied the exact internal-photo pages from FCC ID
`2BBT5-G3V2`. Photo 9 clearly shows the main IC marking:

`ZC32RF1M / QFN40-24 / EMU13322`.

This converts the earlier ZhiXu-family inference into direct hardware evidence
for the G3V2 itself.

The board silkscreen also contains `ZXD2267 G3 V2` and a visible date/revision
string beginning `20241114 ... V1.4`, tying the physical PCB to the same
ZXD2267 family seen in the official updater.

No discrete external firmware flash is visible on either photographed side of
the main PCB. The most likely dump target is therefore internal MCU flash.

A public schematic of another ZC32RF1M QFN40 design maps RESET to pin 22,
USB D- to pin 23, USB D+ to pin 24, and the 26 MHz oscillator to pins 20/21.
These are treated as same-part pinout evidence, not proof of exact G3V2 test
pad routing.


## 2026-11-20 — Safe update simulation proposed

Instead of forcing the controller through OTA, the preferred simulation is to
replay only the MixDrive firmware catalog/download request and capture the
artifact. The real managed flow must be avoided because it sends
`EnterUpdateModeMessage` and mutates `CustomData[11]` before starting the
downloaded updater.

The current server query yields v1.48. Lowering only the locally reported
firmware version may trigger the UI but will still return the server's latest
artifact unless a historical-version parameter or endpoint exists.

Created `tools/dump-mixdrive-firmware-update-flow.py` for a read-only static
audit of firmware API strings, async update state machines, HTTP/download calls,
temporary-file handling and updater launch boundaries.


## 2026-11-20 — Managed firmware catalog/download flow closed

Static CIL inspection of the exact MixDrive application resolved both update
discovery and download behavior.

For gamepads, \`UpdateGamepadFirmwareAsync\` invokes
\`IFirmwareUpdateApiService.GetLatestVersionAsync\` with exactly three logical
arguments: constant \`2\`, \`DeviceModel.DeviceName\`, and
\`DeviceModel.DeviceMode\`.

No local firmware version is supplied to the API call. Instead, the returned
\`FirmwareInfoDto.Version\` is parsed as \`System.Version\` and compared locally
to \`DeviceModel.FirmwareVersion\`. This means lowering or spoofing the
controller's reported version is unnecessary for reproducing the server
catalog request outside MixDrive.

The firmware notice download worker then:
- reads \`FirmwareUpdateModel.FirmwareUpdateUrl\`;
- prefixes the MixDrive API base
  \`https://mixdriver.thunderobot.com/api\` if the URL is relative;
- performs a plain HTTP GET;
- saves the response to \`Path.GetTempFileName() + ".exe"\`;
- copies the HTTP content stream directly into that file;
- for \`DeviceType == 2\`, sends the existing gamepad EnterUpdateMode message;
- executes the downloaded file via \`Process.Start\` and waits for it.

This proves there is no managed step that separately downloads a
\`.bin\`, \`.fot\` or firmware ZIP for the gamepad. The downloaded server
artifact is treated as an updater executable.

Practical consequence: a catalog-only reimplementation is the preferred safe
path. It can obtain and archive the official updater without modifying
CustomData, entering bootloader mode, or launching the updater. It is not,
however, a direct raw-firmware extraction path.

Added \`tools/dump-firmware-api-contract.py\` to statically recover the exact
route attributes and parameter names from \`Machenik.HttpApi*.dll\`.


## 2026-11-20 — Exact firmware API route recovered

The focused \`Machenik.HttpApi.dll\` audit resolved the full MixDrive firmware
catalog contract.

\`FirmwareUpdateApiService.GetLatestVersionAsync\` has parameters
\`productType\`, \`brand\`, \`productName\`, and its async state machine embeds:

- route: \`/firmware/by-product-info\`;
- query key: \`brand\`;
- query key: \`product_name\`;
- query key: \`product_type\`.

The client-software updater is separate and uses \`/software/latest\`.

Combined with the already-established G3V2 values, the controller firmware
catalog request is:

\`GET https://mixdriver.thunderobot.com/api/firmware/by-product-info?brand=MACHENIKE&product_name=G3V2&product_type=2\`

This is sufficient to reproduce the catalog lookup without altering the
controller's reported firmware version or entering updater mode.

Added \`tools/get-g3v2-firmware.py\`. It defaults to catalog-only operation,
prints/saves the raw JSON and resolved download URL, and only downloads the
returned executable when \`--download\` is explicitly supplied. It never
communicates with the controller or starts the updater.


## 2026-11-20 — Live firmware catalog verified; official updater hash matched

The exact recovered G3V2 catalog request was run successfully against the
official MixDrive API.

Response:
- product id 8, MACHENIKE, G3V2, type 2;
- product created/updated 2026-05-07 14:41:08 UTC;
- firmware id 11;
- version 1.48;
- changelog "性能优化";
- download path
  /download/firmware/69fd65594961c_ZXD2267_JRH_G3V2_MACHENIKE_v1.48_20250815.exe;
- file size 2,245,632 bytes;
- published_at 2026-05-08 04:23:53 UTC;
- download_cnt 0.

The updater was then downloaded without execution. Its SHA256 is
f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255,
identical to the updater already under analysis. This confirms the current
official API serves exactly the same v1.48 host updater.

Storage prefix clue:
0x69fd6559 = Unix 1778214233 = 2026-05-08 04:23:53 UTC, exactly the API
published_at timestamp. The filename prefix has 13 hex digits
(69fd65594961c), matching the shape produced by PHP uniqid() (seconds plus a
microsecond component). Treat this as a strong inference only.

Because the product row itself was created in May 2026 while the original
updater filename embeds 20250815, the current backend catalog was likely
populated after the binary build. That makes it plausible that older firmware
such as 1.25 is not returned by /firmware/by-product-info even if its updater
file still exists elsewhere on the server.

Added a conservative read-only route probe:
tools/probe-firmware-history-api.py
It tests only a small fixed set of plausible firmware/history/id GET routes
using product id 8 and firmware id 11; it does not enumerate large ID ranges,
write to the API, or touch the controller.


## 2026-11-20 — Scope correction: stop historical-version detour

Historical firmware probing was recognized as unnecessary for the current
objective. The connected G3V2 reports 1.48 and the official API serves the same
v1.48 updater already under analysis (identical SHA256).

Older versions are now considered optional future material for differential
analysis only.

The current artifact set is sufficient to proceed:
- official v1.48 updater EXE;
- known embedded FOT payload;
- static updater protocol map;
- managed/native lighting protocol map.

Do not equate the embedded update payload with a complete raw flash dump yet:
bootloader, per-device configuration/calibration, or other NVM regions may be
outside the OTA image. Resume FOT/container analysis and trace firmware-side
handling of LampLight command 0x37.


## 2026-11-20 — FOT Thumb hits match random expectation

The exact v1.48 FOT produced 201 aligned halfword patterns that superficially
match Thumb \`CMP Rn,#imm8\` for the nine known command constants. Under a
uniform-random model the expected count is 203.0625, so the observation is
statistically indistinguishable from random data.

This strengthens the classification of the 0x1712F0/0x5A400 transfer payload
as encrypted/compressed/transformed rather than plaintext MCU code. Do not
follow any of the reported CMP clusters as handlers.

The anomalous edge entropy remains useful evidence: the first 4 KiB are less
random than the body and 0x59000-0x59FFF drops to ~6.42 bits/byte. Inspect the
full updater/container surface and edge metadata next.


## 2026-11-20 — Reanalysis corrects FOT-container classification

The full updater scan found a valid FOT container starting exactly at the first
byte sent by F0 (file+0x1712F0). The earlier scout missed this because its
marker list omitted FOT\\0 and looked for XCOD/XRES in raw ASCII instead of
their XOR-0x80 representation.

Key structure:
- FOT header/directory: 0x000000-0x0001FF
- XCOD: starts 0x000200
- XRES: starts 0x058600
- directory copy: 0x059200
- config start: 0x059400
- total: 0x05A400

The DATA record itself encodes 0x200 / 0x59000 / 0x59200 / 0x59400, so these
boundaries are internally self-consistent. The second FOT magic at 0x59200 is
the directory copy, not another embedded firmware.

The eight-byte KEY payload is 00 00 00 01 EA E3 2E 09, identical to the value
reported in same-family public ZXD/EPIC FOT work. The INFO area also contains
the exact G3V2 hardware ID and version 148.

This changes the classification from "entire FOT is opaque transformed data"
to "plaintext FOT metadata/container wrapping high-entropy encrypted payload
sections." The previous statistical CMP conclusion still stands for the
encrypted payload: raw Thumb-looking hits there remain random false positives.


Added \`tools/analyze-g3v2-fot-crypto-surface.py\` to keep subsequent crypto
analysis region-aware and prevent whole-image entropy from masking the
plaintext FOT metadata.


## 2026-11-20 — G3V2 v1.48 crypto-surface run completed

Region-aware scan confirms:
- official updater hash exact
- FOT hash 0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085
- FOT metadata 0x5A400 / HWID ...2267 / version 148
- directory copy at 0x59200 is byte-identical to first 0x200
- KEY=00000001eae32e09
- XCOD @0x200 and XRES @0x58600 have no repeated aligned 16-byte blocks
- XCOD entropy 7.999503 and near-uniform chi-square
- XRES statistics are also consistent with encrypted/random-looking data despite its smaller sample
- DOWN @0x594C0 and BT-BOX / LE-Remoter are plaintext config-region evidence

Interpretation:
the updater contains the correct v1.48 firmware package, but not a plaintext installed-flash
dump. The encrypted code/resource payload is transformed on-device by the bootloader.
Simple ECB is ruled out; no exact cipher is identified.

External same-family ZXD2055/ZXD2271 research independently reports the same structure,
the same KEY constant, and bootloader-side decryption, plus same-HWID keystream reuse in
an X20 version pair. Treat that as family-level guidance until reproduced for HWID ...2267.


Added a local same-HWID corpus scanner. This is now the cheapest non-invasive
test for an overlooked older/newer ...2267 image in project archives or Downloads.


## 2026-11-20 — local corpus produced a same-HWID/same-version pair

The recursive read-only corpus scan found two distinct G3V2 v1.48 FOT hashes with the
same exact ...2267 hardware ID:
- 0b97b5e38fc706182110cdfa1682b1c252a1c0dac8314f5c836d3f037a67e836
- 0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085

The first is stored under \`firmware-original/G3V2-1006-2011-v1.48.fot\`. Available
history does not establish how it was obtained, so do not label it a device dump yet.

A section-aware comparator was added to determine whether the pair exhibits sparse,
same-keystream-compatible differences or random-looking cross-ciphertext XOR.


## 2026-11-20 — same-HWID pair differential result

The two G3V2 v1.48 FOT files with different whole-file SHA256 values do **not** contain
different encrypted firmware payloads.

Measured result:
- XCOD: 100% byte-identical
- XRES: 100% byte-identical
- config: 100% byte-identical
- tail: 100% byte-identical
- header: 508/512 bytes identical

Only header offsets 0x101 and 0x115..0x117 differ. Because the FOT header is duplicated
verbatim at directory-copy offset 0x59200, the same four byte changes occur again there.
This accounts for all eight differing bytes in the complete 0x5A400-byte FOT.

This closes the attempted same-keystream differential route for this pair: there is no
ciphertext delta in XCOD/XRES to exploit. The remaining task is merely to classify the
four container-metadata bytes and establish the provenance of the local
\`firmware-original\` copy before assigning any semantic meaning to them.


## 2026-11-20 — FOT delta: likely per-device address metadata

Follow-up forensic inspection showed the only non-mirrored canonical differences are
\`0x101\` and \`0x115..0x117\`. The latter three bytes complete the six-byte field
\`0x112..0x117\`:

- A: \`98:B6:E9:B0:25:BE\`
- B: \`98:B6:E9:CE:4B:03\`

This is not behaving like a payload checksum. Public KeyLinker/X20 reverse engineering
documents \`98:B6:E9\` as the standard-gamepad address prefix, and a public ZXD2055/EPIC
FOT independently has \`98:B6:E9:1A:78:60\` at the same offset.

Current interpretation:
- XCOD/XRES/config/tail are identical.
- the whole-image SHA difference is container personalization.
- \`0x112..0x117\` is very likely a Bluetooth/gamepad device address field.
- \`0x101\` remains unknown and must not be overinterpreted.

Next safe test: observe the live controller address and compare it with the two FOT
candidates. Querying BlueZ/known devices is read-only; no firmware write/update is needed.


## 2026-11-20 — live G3V2 BLE address does not match FOT E9 personalization

Active read-only discovery found \`98:B6:ED:<redacted> G3V2 APP\`.

This does not match either known FOT field at 0x112:
- \`98:B6:E9:B0:25:BE\`
- \`98:B6:E9:CE:4B:03\`

So the FOT E9 field must not be described as the live BLE address of the physical unit.
Same-family KeyLinker evidence supports multiple Bluetooth personalities/interfaces, so an
E9 standard-gamepad identity and an ED app/configuration identity can coexist conceptually.
That relationship still needs to be proven for G3V2.

Added a read-only identity-surface scanner to search header/config for related family
addresses and the observed live suffix.


## 2026-11-20 — G3V2_APP is likely the separate KeyLinker/config BLE interface

Live discovery found only \`98:B6:ED:<redacted> G3V2 APP\`; that address and suffix are absent
from both v1.48 FOTs.

The G3V2 manual independently states that \`G3V2_APP\` is visible to KeyLinker when the
controller is running in any mode and does not require normal Bluetooth pairing to the
phone. This strongly resolves the apparent contradiction: the ED APP address is a
configuration peripheral, while gameplay Bluetooth identities are separate.

The manual lists gameplay modes/names:
- X+HOME -> Xbox Wireless Controller
- Y+HOME -> Pro Controller
- A+HOME -> MACHENIKE G3 V2

The FOT E9 field may therefore belong to one of those gameplay/factory identities rather
than the APP peripheral. Scan those modes read-only before assigning the E9 field.


## 2026-11-20 — B+HOME mode correction; X+HOME scan saw only APP peripheral

Confirmed by user observation:
- B+HOME = dongle mode
- dongle mode appears as Xbox 360 Controller

X+HOME active discovery returned only:
\`98:B6:ED:<redacted> G3V2 APP\`

No gameplay-side E9 address was exposed in that run. Keep the APP/config personality and
gameplay identity separate; current evidence is insufficient to map the FOT E9 field to a
live radio address.


## 2026-11-20 — X+HOME auto discovery still shows only G3V2 APP

With X+HOME selected, BlueZ discovered only the persistent ED-address APP/configuration
peripheral. The expected gameplay name \`Xbox Wireless Controller\` did not appear.

Manual evidence corroborates X+HOME as Bluetooth PC/mobile mode and B+HOME as dongle mode.
Next diagnostic: force separate BR/EDR and LE scans while the controller indicator is
flashing rapidly in X+HOME pairing state.


## 2026-11-20 — correct interpretation of separate BREDR/LE scan test

The X+HOME BREDR-only and LE-only attempts both left only the known \`G3V2 APP\` device in
\`bluetoothctl devices\`. Do not infer transport from that post-scan listing: it reflects
BlueZ-known device objects rather than serving as a per-transport event transcript.

Use \`btmon\` next to capture real MGMT/HCI discovery events and determine whether the
gameplay personality emits BR/EDR Inquiry results, LE advertising reports, or nothing
while X+HOME is actively pairable.


## 2026-11-20 — btmon exposed an invalid scan wrapper

The raw btmon log showed Start Discovery followed essentially immediately by Stop
Discovery/Inquiry Cancel. The previous wrapper used external \`timeout 22s bluetoothctl
scan on\`, but bluetoothctl exited on its own, so discovery lasted only milliseconds/
microseconds instead of 22 seconds.

Correction: use bluetoothctl's native non-interactive timeout option:
\`bluetoothctl --timeout 22 scan on\`.

Re-run X+HOME identity discovery with btmon after this correction. Earlier negative scan
results are not evidence of absence.


## 2026-11-20 — X+HOME live gameplay identity captured

Corrected scan method (\`bluetoothctl --timeout 25 scan on\`) plus btmon produced:

- LE \`98:B6:ED:<redacted>\` — \`G3V2 APP\`
- BR/EDR \`98:B6:EA:<redacted>\` — \`Xbox Wireless Controller\`

Both share the exact lower 24 bits \`<redacted>\`, proving the APP and gameplay personalities
belong to the same physical unit while using different public address prefixes.

This is decisive evidence that the FOT E9 fields are not the current live addresses of
this unit. The two FOT suffixes B0:25:BE and CE:4B:03 do not occur in the live identities.

The next read-only experiment is to map Y+HOME and A+HOME in the same way and see whether
their gameplay personalities preserve the same suffix under other family prefixes.


## 2026-11-20 — Y+HOME did not connect to Bazzite

Post-capture inspection found no connection event in btmon and no connected BlueZ device.
Only \`98:B6:ED:<redacted> G3V2 APP\` remained visible.

So the user's observation that Y+HOME "connected" was not a connection to this Bazzite
adapter. Most likely it reattached to another already-paired host. The Y+HOME gameplay
identity is still unknown.

Next safe test: temporarily make the previously paired host unavailable, then repeat
Y+HOME discovery with btmon; do not erase pairing data from the controller.


## 2026-11-20 — Y+HOME final connection was via dongle, not Bluetooth

User clarified that the controller ultimately connected to the same Bazzite machine and
appeared as an Xbox 360 Controller.

Because btmon/BlueZ showed no HCI connection, this means the final connection used the
separate 2.4 GHz dongle/USB path. That path is outside hci0 and therefore invisible to
btmon.

Correction to prior interpretation: it did not necessarily reconnect to another host.
For a clean Y+HOME Bluetooth discovery, remove the dongle from the equation during the
capture.


## 2026-11-20 — decisive Y+HOME identity result

With the dongle removed, Y+HOME advertised over BR/EDR as:

\`98:B6:E9:<redacted> Pro Controller\`

At the same time the persistent LE configuration peripheral remained:

\`98:B6:ED:<redacted> G3V2 APP\`

Together with the prior X+HOME result:

\`98:B6:EA:<redacted> Xbox Wireless Controller\`

the physical unit now has a confirmed three-personality identity map sharing one lower
24-bit suffix.

This directly links the E9 prefix to the G3V2's Pro Controller personality and makes the
FOT header E9 field at 0x112 much more likely to be Bluetooth identity/personalization
metadata rather than a generic family marker.

Neither known FOT's E9 suffix matches the live unit:
- A: B0:25:BE
- B: CE:4B:03
- live unit: <redacted>

Since A/B share the exact same encrypted firmware payload and only the E9 suffix plus byte
0x101 differ (mirrored in the directory copy), treat them as the same firmware payload
with different identity metadata. Byte 0x101 may depend on that personalization, but its
algorithm/meaning remains unknown.


## 2026-11-20 — generic updater constrains interpretation of the FOT E9 field

Y+HOME proves the live Pro Controller personality uses the E9 prefix, linking the FOT
0x112 field to the correct identity namespace.

But the official updater FOT is generic and carries one fixed E9-form value. If flashing
that value directly became the runtime BD_ADDR, every updated unit would collide. Therefore
the field cannot yet be called the final live MAC.

Most plausible possibilities are separate per-device NVM, bootloader/runtime patching or
derivation, or provisioning/reference metadata. The A/B FOT delta still shows this is an
identity/personalization-related header field, but its exact lifecycle is unresolved.


## 2026-11-20 — A+HOME maps to E8 / MACHENIKE G3V2; lighting survives mode changes

A+HOME discovery produced a BR/EDR gameplay identity with:
- prefix \`98:B6:E8\`
- name \`MACHENIKE G3V2\`
- same lower-24-bit per-unit suffix as the already mapped ED/EA/E9 personalities.

The confirmed personality map is now:
- ED = G3V2 APP (BLE configuration)
- EA = Xbox Wireless Controller (X+HOME, BR/EDR)
- E9 = Pro Controller (Y+HOME, BR/EDR)
- E8 = MACHENIKE G3V2 (A+HOME, BR/EDR)
- B+HOME = 2.4 GHz dongle path, host sees Xbox 360 Controller

The user also observed that the previously configured blue breathing lighting state
survived repeated power-off/startup and switching among these modes. This is direct
evidence that the final lighting configuration is persistent across normal power cycles
and mode changes.

Do not yet attribute persistence to \`0x37\` alone: the exact write sequence that originally
committed the state must be isolated before using that command at realtime Ambilight rates.


## 2026-11-20 — live unit suffix is external to the known FOT payload

After mapping E8/E9/EA/ED personalities, all live Bluetooth identities share the same
per-unit lower-24-bit suffix. Earlier exhaustive FOT scanning found that suffix nowhere in
either v1.48 image.

This is strong evidence that the runtime device-specific Bluetooth identity is sourced
from persistent per-unit storage outside the generic FOT or is derived from another unique
hardware value. This model naturally explains why the official generic updater does not
clone Bluetooth addresses across units.


## 2026-11-20 — isolated standalone 0x37 persistence test added

Added a guarded three-phase live test for the critical Ambilight question.

The test performs one RGB-only 0x37 write while preserving the current LampLight
effect/breath/brightness bytes and deliberately never sends 0x3B. It snapshots the
original six-byte B7 lamp payload before mutation, verifies the marker by B7, then uses a
separate read-only \`--check\` run after a real controller power-cycle. A final
\`--restore\` returns the original LampLight bytes.

No streaming loop, OTA path or SCSI commands are present.


## 2026-11-20 — standalone 0x37 persistence conclusively proven

Live test result:

- original B7 LampLight: \`00 FF FF 00 00 FF\`
- one isolated 0x37 RGB marker was applied
- no 0x3B write occurred
- marker survived a physical controller power-cycle
- read-only B7 after restart still returned the marker
- one isolated 0x37 restore returned the exact original bytes
- post-restore B7 confirmed the restoration

Therefore 0x37 itself persists independently of 0x3B.

This is the critical Ambilight safety result: do not stream 0x37 at high frequency unless
the persistent-storage implementation is later proven to be wear-safe. The next reverse-
engineering target is an undocumented volatile/realtime LED update path.


## 2026-11-20 — LampLight independence from zero-length CustomData observed

All warm-up CustomData responses were empty during the successful persistence test, yet
B7/0x37 lighting read, write, power-cycle verification and restoration all worked.

This is direct evidence that basic LampLight control is operational independently of a
valid CustomData payload in that session.


## 2026-11-20 — visual observation corrects meaning of the 0x37 restore

The isolated test restored the exact B7 bytes but did not restore the original visual
effect.

Observed:
- pre-test: blue breathing;
- marker \`FF 00 00 00 00 FF\`: solid red;
- restored B7 \`00 FF FF 00 00 FF\`: solid cyan/light-blue.

So B7/0x37 persistence is proven for the LampLight payload, but B7 is not a complete
snapshot of the visual lighting mode. The missing breathing state is consistent with the
separate CustomData LightModeIndex already identified at data[1].

Since CustomData was zero-length throughout the Linux test, that mode value was never
captured and therefore could not be restored. Do not claim the original visual state was
fully restored.


## 2026-11-20 — FN+Right changes breathing without changing B7

The controller was solid cyan/light-blue after the isolated 0x37 restore. The user then
used FN+Right until the original-looking blue breathing effect returned.

A read-only capture immediately afterward still returned exactly:
\`B7 = 00 FF FF 00 00 FF\`

with packed effect bytes 0x0000, while the LEDs were visibly breathing. BB remained zero
length.

This conclusively shows that the visible solid/breathing mode is separate from the six-byte
B7 payload. FN+Right changes an internal effect/mode state while leaving B7 unchanged.

Next target: capture USB/HID traffic during FN+Right to determine whether the mode transition
is controller-local or accompanied by any host-visible command/report.


## 2026-11-20 — manual corroboration and FN+Right usbmon probe prepared

Public G3 V2 manuals independently identify FN+D-pad Right as the lighting mode switch and
FN+D-pad Left as brightness control, matching the live hardware behavior.

Added a read-only usbmon capture helper to observe one FN+Right transition and distinguish
a controller-local mode change from any host-visible output command.


## 2026-11-20 — recovered prior FN+Right usbmon result; duplicate test canceled

Project history confirms that FN+Right had already been captured with usbmon.

Earlier result:
- 15,634 USB transfers;
- 7,817 packets received;
- 0 OUT transfers during the mode change.

Therefore there was no host->controller USB command associated with FN+Right. Together
with the current unchanged-B7 result across solid and breathing states, this strongly
supports controller-local firmware handling of lighting mode selection.

The newly proposed duplicate usbmon capture is no longer needed.


## 2026-11-20 — consolidated closed lighting experiments to prevent duplicate work

Recovered earlier project history shows that the lighting fingerprint work had already
gone further than the latest STATUS summary reflected.

Already completed:
- FN+Right traversed 13 physical effects;
- passive USB capture showed the key/input event but no host->device OUT traffic causing
  the effect transition;
- B7 and BB fingerprints remained unchanged while FN+Right changed the visible hardware
  effect;
- static app analysis mapped Solid/Breathing/Rainbow-related normal configuration paths;
- no exposed volatile/realtime RGB writer was found;
- subsequent live work proved standalone 0x37 persistence across power loss.

Do not repeat those tests.

Unexecuted, intentionally deferred:
- test-mode opcodes 0x1B/0x1C (\`CODE_TEST_SET_MODE\` /
  \`CODE_TEST_NORMAL_MODE\`);
- they must remain offline-analysis targets until their arguments and side effects are
  fully understood.

The active next target is offline reverse engineering of the still-unassigned KeyLinker
dispatcher/callers, KeyLinker 3.73/APK lighting/test-mode code, and the exact
enter/exit-test-mode framing. Only a statically understood candidate for a volatile LED
path should justify another live hardware probe.


## 2026-11-20 — verification of prior offline RE work

A history/artifact review corrected another duplication risk.

Previously completed:
- full Ghidra analysis of \`FUN_180001080\` and its callers;
- extraction/static analysis of KeyLinker APK 3.73;
- Android lighting path traced through \`A1()/z1() -> e.l0() -> 0x37\`;
- no alternate exposed Android RGB-live writer found.

Still incomplete:
- exact semantics and argument/framing of the test-mode functions around
  \`0x180004820\` and \`exitDeviceTestMode@0x180004920\`;
- exact linkage of named constants \`0x1B CODE_TEST_SET_MODE\` and
  \`0x1C CODE_TEST_NORMAL_MODE\` to those functions;
- whether either is related to the firmware-local FN+Right effect cycle.

No live 0x1B/0x1C command was sent.


## 2026-11-20 — 0x1B/0x1C test-mode framing resolved; 0x180004EA9 eliminated from lighting

Ran the focused static audit `tools/dump-keylinker-test-mode.py` against the same
`KeyLinkerlib.dll`.

Confirmed:
- `enterDeviceTestlevelMode` export @ `0x180004900`
- `exitDeviceTestMode` export @ `0x180004920`
- `keyTestBeforeEffect` export @ `0x1800049E0`
- `keyTestAfterEffect` export @ `0x180004A00`

`FUN_180004820` is the common 0x1B builder. Its 9-byte pre-transform frame is:
`1B 09 <state> <rand> 03 DF AB <mode> <checksum>`
followed by `KeyLinkerEncode(..., 9)`.

Concrete caller arguments:
- `keyTestBeforeEffect` -> mode 0
- `keyTestAfterEffect` -> mode 1
- `enterDeviceTestlevelMode` -> forwards caller-supplied mode

`exitDeviceTestMode` builds the 8-byte 0x1C frame:
`1C 08 <state> <rand> 02 DF AC <checksum>`
followed by `KeyLinkerEncode(..., 8)`.

This closes the exact linkage of:
- `0x1B = CODE_TEST_SET_MODE`
- `0x1C = CODE_TEST_NORMAL_MODE`

The formerly suspicious site `0x180004EA9` is inside the exported
`writeDeviceKeyChangeData` routine and calls `HeaderBuilder` with command `0x36`.
It belongs to the fragmented key-change writer and is not a lighting path.

No live 0x1B/0x1C command was sent. The static result does not connect test-mode mode values
to FN+Right's 13 lighting effects, so these commands remain excluded from live lighting
experiments unless a new independent static clue links them to LED control.


## 2026-11-20 — 0x37 timing data recovered; BLE family comparison sharpened

A separate controlled timing run performed four isolated 0x37 LampLight writes while
alternating a marker colour and the original value. No 0x3B, OTA or SCSI command was sent.

Measured:
- B7 baseline median about 4–5 ms;
- 0x37 ACK times: 3.84, 9.94, 7.95, 9.97 ms (median 8.95 ms);
- B7 immediately after writes: 4.01, 30.00, 6.00, 4.01 ms;
- final B7 matched the original state.

This adds evidence that 0x37 does more controller-side work than a simple B7 query in many
samples, but timing alone cannot identify whether/when NVM is programmed. The single 30 ms
readback is retained as an outlier, not treated as proof of a flash commit. No endurance
lifetime calculation is considered established without MCU/NVM data and firmware behavior.

Current x20ctl main was also inspected as external family evidence. Its X20 Pro path:
- uses the same d7f010e0/e1/e2 KeyLinker BLE GATT service;
- writes encoded KeyLinker frames directly to e1 and decodes e2 notifications, with no USB
  80 F9 outer envelope;
- verifies identity with read opcode 0x91 before model-specific operations;
- reads a 24-byte lighting record via B7 in two pieces;
- writes that record through 0x37 in two chunks;
- contains a persistent BLE session for software-generated lighting effects including
  screen ambient sampling.

This does not establish a safe realtime 0x37 path on G3 V2. X20 Pro's 24-byte lighting
record differs materially from the six-byte G3 V2 LampLight object already observed over
USB, and x20ctl's current changelog itself still calls for final physical verification of
the newest packaged simultaneous dongle+BLE behavior.

The useful conclusion is narrower: direct KeyLinker-over-GATT is now independently
corroborated by another implementation, and the G3 V2 BLE read-only probe is justified as
the next safe transport experiment. A G3 V2 lighting write over BLE remains deferred.


## 2026-11-20 — G3V2 APP BLE advertisement proven; first GATT connection timed out

Executed `g3v2-ble-probe-v1.py --enumerate-only` with the controller off USB and available
wirelessly.

Observed:
- 7 BLE devices in the scan;
- exactly 1 G3V2 candidate;
- name `G3V2 APP`;
- RSSI about -68 dBm;
- advertised UUIDs included `0000ff12-0000-1000-8000-00805f9b34fb` and the exact
  KeyLinker configuration service `d7f010e0-660d-46e9-96c3-19c4148bdab5`.

The following Bleak connection attempt timed out before the script could list GATT
characteristics. v1 emitted only the top-level `TimeoutError`, so the evidence does not
yet distinguish a failed LE connection from BlueZ waiting for GATT service resolution.

No KeyLinker request was transmitted in enumerate-only mode.

Added `g3v2-ble-probe-v2.py` to diagnose this specific Linux/BlueZ stage with bounded
connection retries, fresh discovery between retries, and a read-only BlueZ state dump after
failure. No automatic disconnect/pairing or controller-setting write was added.


## 2026-11-20 — BLE v2 failed all connects; v3 isolates BlueZ discovery-lifetime hypothesis

`g3v2-ble-probe-v2.py --enumerate-only` saw one `G3V2 APP` at roughly -64 dBm.
Three connection attempts each timed out after 20 s. The probe successfully refreshed the
advertisement between attempts, proving that the configuration peripheral remained on-air.

After the third failure BlueZ no longer exposed the address as a Device object to
`bluetoothctl info`. No application-level command was sent.

This does not imply the peripheral requires pairing. The vendor manual explicitly says the
Keylinker application can find `G3V2_APP` with the controller in any mode and that normal
phone Bluetooth pairing is not required for configuration.

A new `g3v2-ble-probe-v3.py` was added to keep BlueZ discovery active while connecting,
then inspect the Device object before discovery is stopped. This isolates a known class of
Linux/BlueZ failures where temporary unpaired LE objects disappear or connection behavior
changes when discovery ends.


## 2026-11-20 — BLE v3: connection still fails with discovery continuously active

Live run `g3v2-ble-probe-v3.py --enumerate-only`:
- 4 BLE devices seen, exactly one `G3V2 APP`;
- approximately -74 dBm during scan, -78 dBm by the time BlueZ state was sampled;
- three attempts with scan kept active throughout each connection attempt all timed out;
- `bluetoothctl info` while discovery remained active showed `Paired: no`,
  `Bonded: no`, `Trusted: no`, `Blocked: no`, `Connected: no`,
  public address type and `AdvertisingFlags: 02`.
- `DBG: cs_proxies is empty` appeared, but this text alone does not identify
  the failed stage.
- No KeyLinker frame was sent; this was still GATT-enumeration-only.

This closes the discovery-lifetime-only hypothesis. It does *not* prove whether the LE
connection failed or an attempted GATT service resolution timed out: there was no
instrumentation at HCI level, and the BlueZ state was sampled after each connect had
already failed/cleaned up.

The next diagnostic is a **single**, bounded read-only `btmon` HCI trace spanning one
v3 attempt, avoiding any more repeated three-attempt runs. Keep unredacted controller and
other-device MAC addresses in local files only; publish/analyze sanitized HCI status lines.


## 2026-11-20 — HCI/MGMT capture reveals BR/EDR-address failure (transport-selection hypothesis)

One v3 enumerate-only run was recorded using `btmon -t`. BLE scan found the
`G3V2 APP`, but the sole Bleak connection timed out after 20s.

The prior summarizer output contained:

```text
@ MGMT Event: Connect Failed (0x000d)
    BR/EDR Address: [redacted]
    Status: Connect Failed (0x04)
```

This is **not** an HCI `Page Timeout` diagnosis; `0x04` here is a BlueZ MGMT
status. MGMT address type `BR/EDR` is nonetheless notable because the desired
configuration service is BLE.

No extracted HCI `Create Connection` / `LE Create Connection` command was included
in the first filtered output. Do not conclude which bearer was attempted without
reading those events from the existing raw capture. Read-only offline log analysis is
the next experiment; further live retries are not justified yet. Address values and
unredacted btmon files stay local.


## 2026-11-20 — HCI capture definitively shows a classic BR/EDR connect attempt

The user inspected the previously recorded `btmon` log and recovered the
command preceding the management error:

- 23:15:55.172894: `HCI Command: Create Connection (0x01|0x0005)` — BR/EDR.
- 23:15:55.175138: `Command Status: Success (0x00)` — host controller accepted command.
- 23:16:00.295198: `HCI Event: Connect Complete (0x03)` — `Page Timeout (0x04)`.
- 23:16:00.295226: MGMT `Connect Failed` for `BR/EDR Address`.

The 0x04 in HCI is **Page Timeout**; the 0x04 in MGMT is **Connect Failed**.
This confirms the observed connection attempted the **wrong bearer** for the
KeyLinker BLE GATT peripheral. It does not yet explain *why* BlueZ chose BR/EDR,
nor demonstrate whether an LE connection would succeed.

Next minimally invasive test: use BlueZ's LE-specific bluetoothctl submenu
(`menu le` / `connect <address>` while the device has a fresh LE advertising report).
Only if unsupported, inspect the optional experimental dual-mode `PreferredBearer`
interface; do not change immutable OS base, pair, reset hardware, or transmit
KeyLinker configuration writes. No additional HCI capture needed yet.


## 2026-11-20 — bluetoothctl LE connect blocked by missing org.bluez.Bearer.LE1

Using `bluetoothctl scan le` the user found `G3V2 APP` in the active
discovery list, then entered `menu le` and ran `connect <controller>`.
BlueZ replied `org.bluez.Bearer.LE1 is not available on <controller>`.

Checked upstream BlueZ client source: the LE menu's connect handler first
looks for the `org.bluez.Bearer.LE1` interface proxy and prints this error
without invoking `Connect` when missing. **No LE connection attempt was
made by this command**, so do not treat it as LE RF failure.

Correlated an additional independent clue:
- BlueZ previously printed `AdvertisingFlags: 02` and address type
  `public` for this controller;
- upstream `adapter.c` infers BR/EDR capability for an LE public address
  whose advertising flags lack `EIR_BREDR_UNSUP` (`0x04`);
- this provides a concrete mechanism explaining the HCI classic
  `Create Connection` and `Page Timeout` already captured;
- BlueZ commit 9965f36 (July 2026) fixed the analogous case for LE
  **random** addresses, not all LE public addresses.

Remaining uncertainty: whether this G3V2 firmware truly supports BR/EDR
and which of BlueZ's optional bearer APIs/properties are available on the
installed daemon. Query `menu le -> list/show`, main `info`, and the
read-only `bearer <device>`; avoid pairing, controller writes or OS service
reconfiguration until results are interpreted. Local address is intentionally
omitted from public docs.


## 2026-11-20 — LE1 absent; PreferredBearer not exposed by bluetoothctl query

Interactive `bluetoothctl` session:
- `scan le`: `G3V2 APP` rediscovered.
- `menu le` → `list`: empty.
- `menu le` → `show <controller>`: "LE bearer not found on ...".
- `back` → `info <controller>`: public address, unpaired and disconnected,
  `AdvertisingFlags: 02`.
- `bearer <controller>`: no output.
- Scan then cleanly stopped and session exited.

Checked BlueZ `client/main.c`: read-only `bearer` queries the optional
`PreferredBearer` property and prints nothing if unavailable. The LE menu
operations require an existing `org.bluez.Bearer.LE1` proxy; the daemon is
not publishing one for this device. No connection or configuration mutation
occurred during this round.

Avoid repeating scans, trying `menu le connect`, or changing
`PreferredBearer` without observing the property. Investigate direct,
explicit-LE L2CAP ATT clients as a bounded one-time connection test. BlueZ
`tools/btgatt-client.c` supports `--type public`, and Python >=3.14
supports `(bdaddr, psm, cid, bdaddr_type)` for L2CAP sockets. Check tool
availability before implementation. Refrain from global BR/EDR shutdown:
it requires cycling adapter power, can disrupt unrelated devices and is
unnecessary at this stage.


## 2026-11-20 — Python 3.15.2 supports explicit LE public ATT socket on user's Bazzite

Runtime capability probe showed `AF_BLUETOOTH=31`, `BTPROTO_L2CAP=0`,
`BDADDR_LE_PUBLIC=1` in Python **3.15.2**, but neither
`btgatt-client` nor `gatttool` is installed. RPM did not recognize
`bluez` and `bluez-deprecated` as installed packages, which must not
be interpreted as absence of the functioning daemon.

Verified Python docs: since 3.14, the socket L2CAP address has
`(bdaddr, psm, cid, bdaddr_type)`. Verified BlueZ's own direct ATT client:
`SOCK_SEQPACKET` + L2CAP + fixed ATT CID 4 + LE Public destination type.

Published `tools/g3v2-le-att-connect-readonly.py` for one **not yet
executed** direct kernel LE ATT socket connection, with an interactive
MAC prompt and no user-level ATT/GATT payloads, pairing or settings writes.
Next step is a single bounded execution, then evaluate the connection result
or errno (including possible CID ownership/privilege issues). No new
BlueZ/daemon/kernel modifications are warranted.


## 2026-11-20 — G3V2 direct LE Public L2CAP/ATT connection: SUCCESS (462.9 ms)

A real execution of `tools/g3v2-le-att-connect-readonly.py` returned:

```text
SUCESSO: conexão L2CAP/ATT estabelecida em 462.9 ms
Destino: CID=4, tipo=1 (LE Public=1)
```

The socket was closed immediately and no ATT PDU was sent or received.
The `BR/EDR Page Timeout` observed in earlier Bleak/BlueZ path was
therefore a transport-selection/interface problem in that tested path,
not proof that the device is unreachable by Bluetooth LE. Root reason for
BlueZ's misclassification remains a hypothesis (advertising flags 0x02
with public address), not a fully proven root cause.

For the first **read-only** real GATT interrogation, added
`tools/g3v2-le-gatt-discover-readonly.py` with one direct LE Public
connection and guarded ATT discovery `0x10` (primary services) followed
by `0x08` (characteristic declarations inside service d7f010e0, when found).
No attribute value reads/writes, notification subscriptions, or application
KeyLinker commands. The request types follow Bluetooth SIG ATT/GATT service
discovery and BlueZ's reference approach. Local syntax and UUID-decoding
tests passed, but the device responses have **not yet been measured**.


## 2026-11-20 — Direct G3V2 GATT discovery confirms e0/e1/e2 on physical controller

Received physical output for `tools/g3v2-le-gatt-discover-readonly.py`
after a direct LE Public connection in 459.9 ms:

| Attribute | Range or value handle | Value |
|---|---|---|
| KeyLinker primary service | 0x0005–0x000A | d7f010e0-660d-46e9-96c3-19c4148bdab5 |
| Additional primary service | 0x000B–0x0012 | 0xFF12 |
| KeyLinker Write characteristic | decl 0x0006, value 0x0007 | d7f010e1-660d-46e9-96c3-19c4148bdab5, properties 0x08 |
| KeyLinker Notify characteristic | decl 0x0008, value 0x0009 | d7f010e2-660d-46e9-96c3-19c4148bdab5, properties 0x10 |

The expected GATT profile is now proven, but descriptor `0x000A`
is **not** yet identified (likely, but not proven CCCD `0x2902`).
The two raw discovery requests changed no attributes. The exact meaning
of `0xFF12` remains undetermined.

Added a next-stage script, `tools/g3v2-le-keylinker-b7-readonly.py`.
Default CLI requires an explicit choice:
- `--inspect-only` discovers the CCCD with ATT Find Information 0x04,
  reads it with ATT Read 0x0A, and makes **no ATT writes**.
- `--b7` first validates all handles/UUIDs, then writes the connection
  subscription CCCD and submits one G3V2 native `B7` **read request**
  to e1, waits for the e2 notification, validates CRC, and restores the
  CCCD. The ATT writes used for subscription/query must not be
  conflated with persistent lighting opcodes. No 0x37, 0x3B, OTA, or SCSI
  code exists in the script. No actual read-response test yet performed.

Next hardware run should begin with `--inspect-only` to confirm the
descriptor without changing any characteristic. Do not repeat the earlier
Bleak/BlueZ connection experiments.


## 2026-11-20 — e2 CCCD physically identified, nonstandard 1-byte ATT read `0B 01`

The user ran the first direct BLE B7 read probe. LE Public connection:
**665.0 ms**, with unchanged service map and e1/e2 handles.
ATT Find Information confirmed `0x000A => UUID 0x2902` (actual CCCD).
ATT Read `0x0A` on handle `0x000A` returned `0B 01` (one byte of
value `01`). The script expected a standard two-byte CCCD and
aborted. No CCCD writes or B7 frames were transmitted.

Bluetooth Core specification defines CCCD as two octets; do not
assume the meaning of nonstandard `01` or auto-write standard
`01 00`.

Implemented `tools/g3v2-le-keylinker-b7-v2.py`:
- `--inspect-only` still performs only read-only discovery and CCCD
  value read;
- `--b7` checks actual handles/properties and confirms CCCD value is
  exactly `01` (observed) or `01 00` (standard notify-on) before
  sending **only one** KeyLinker application-level read request `B7`
  by a single transport ATT Write Request to e1, without writing the
  CCCD. If no notify arrives, do not retry automatically.
- new `--address` option avoids interactive MAC entry (the user's
  MAC is never hardcoded into the public repository).

The original one-byte descriptor response and no-CCCD-write test
are now the relevant next step; avoid repeating previous discovery,
probe v1, or BlueZ BR/EDR attempts.


## 2026-11-20 — B7 submitted once via BLE e1; ATT ACK but no e2 notification

Measured direct LE Public connection 398.6 ms, then:
- e1 value handle `0x0007` is Write;
- e2 value handle `0x0009` is Notify;
- actual CCCD handle `0x000A` (0x2902) reads back
  `0B 01`, i.e. a nonstandard one-byte `01` value;
- sent **one** native KeyLinker `B7` read frame:
  `B7 06 00 29 00 A4` -> `CC B3 40 70 99 15`;
- received the transport-level ATT Write Response from e1;
- received **no e2 Handle Value Notification** within timeout.
No subscribe/CCCD write was made and no other KeyLinker command
was transmitted.

This proves the handle is writable and the ATT write was processed,
**not** that the KeyLinker parser accepted or responded to B7.
The initial single-byte CCCD `01` should not be interpreted as
an established subscription.

Added `tools/g3v2-le-b7-subscribe-v3.py` to distinguish the
subscription hypothesis from a possible required session bootstrap.
The tool checks real UUIDs/handles, restricts the observed initial
CCCD to `01`/`01 00`, writes canonical `01 00` only on its
verified CCCD as a connection-scoped notification subscription, then
only upon successful ACK and positive readback sends a **single B7**
read frame to e1. It never sends 0x37/0x3B/OTA/SCSI/pairing
commands or automatic read retries. It closes the socket and does
not request a persistent setting change. The user has not run this
new targeted experiment yet.


## 2026-11-20 — Explicit CCCD subscription did not yield B7 notifications; codec mismatch discovered

The user ran `g3v2-le-b7-subscribe-v3.py` on the real controller:
- LE connection 639.3ms; GATT KeyLinker handles unchanged;
- CCCD 0x000A ATT Read Response `0B 01` before and after writing the standard
  two-byte subscribe value `01 00`; ATT Write Response was successful;
- one `B7` native read query encoded by the *Windows MixDrive USB codec*
  was sent to GATT e1 and received an ATT Write Response;
- no GATT e2 notification appeared within the timeout.

The descriptor may have a one-byte custom representation and the APP
protocol parser may differ. Neither the explicit subscribe nor the ATT
write ACK prove that the gamepad accepted or processed the KeyLinker
command. Stop replaying the same B7 wire frame.

A static source comparison with `AmjadAAYD/x20ctl`
(`x20ctl/protocol.py`, recovered from a **different controller's Android
KeyLinker app**) uncovered a previously missed codec mismatch:
same native plaintext `B7 06 00 29 00 A4` yields
`CC B3 40 70 99 15` with the known-G3V2 USB MixDrive codec, but
`71 A2 A2 90 2C 8E` with the Android-derived X20 Pro KeyLinker codec.
The same opcode, service UUID, and CRC do **not** imply identical frame
encryption. Inspect the actual G3V2-compatible Android KeyLinker APK 3.73
and its `CodeHelper.encrypt` plus GATT send/notification registration
offline **before sending any alternative BLE packet**.

Separately, the user reports that manually powering off the G3 V2 and
leaving it in the charging dock for an unmeasured duration makes its most
recent lighting configuration apparently disappear. This qualifies the
earlier rapid-power-cycle persistence observation. It could be volatile
state expiration, a charging-profile override, or another firmware
reload; it does **not** yet prove any flash behavior or lifespan.
A clean, single-setting observation comparing power-off with and without
dock (and removing from dock before evaluation) will be needed later.


## 2026-11-20 — Located Android KeyLinker APK, preparing offline codec diff

User's read-only local file search identified
`keylinker-referencia/base.apk` alongside Android configuration split
APKs. The direct GATT connection already works; further `B7` retries
using the Windows USB codec are paused until codec verification.

Committed `tools/inspect-g3v2-keylinker-apk-codec.py` to examine
the app DEX (not the unrelated TouchManager/game APKs). The tool
reports APK SHA256, detects APK DEX files, uses installed JADX or an
explicitly opted-in official portable user-cache download, then prints
the relevant CodeHelper/GATT Java sections. All decompiled app files
remain outside GitHub under user cache. Android APK `encrypt` has not
yet been extracted nor compared against USB `KeyLinkerlib.dll` or
X20 Pro; no new controller query should be sent until this is resolved.

## 2026-11-20 — APK DEX is present; Java not found; portable JRE path added

Offline APK scan:
`keylinker-referencia/base.apk`,
SHA256 `09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee`,
`classes.dex` 8,002,728 bytes; expected GATT UUID strings, KeyLinker,
encrypt and BluetoothGatt found.

JADX decompilation did **not run**: `java` was not found in PATH.
Updated `tools/inspect-g3v2-keylinker-apk-codec.py` to use an
optional verified portable Eclipse Temurin 21 JRE via Adoptium API
(`--fetch-portable-java`) alongside opt-in official portable JADX
(`--fetch-portable-jadx`). Both are user-cache-only operations;
the immutable Bazzite OS and physical gamepad remain untouched.
Next action: run the updated script once, inspect Java/GATT class output,
then compare exact app encryption. No new controller tests yet.


## 2026-11-20 — JADX output identifies precise obfuscated Android KeyLinker codec calls

Temurin JRE and JADX CLI were downloaded to the user's cache,
not installed in immutable Bazzite. JADX exit code 3 but yielded
4,314 Java sources. The initial scoring discovered only
`com/pulsenet/inputset/util/b.java`, as the app is obfuscated.

Static evidence from the user's decompilation:
- notification decode: `e.A().g(bluetoothGattCharacteristic.getValue())`,
  then dispatch decoded data to listener;
- outgoing KeyLinker e1 characteristic write:
  `characteristic.setValue(e.A().h(bArr))`;
- on service discovery, app finds KeyLinker e0/e2 and enables
  notifications via `BluetoothGatt.setCharacteristicNotification` plus
  `BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE` in the 0x2902 CCCD.

Next source is `com/pulsenet/inputset/util/e.java`, specifically
method bodies `g(byte[])`, `h(byte[])`, and their constants. This is a
direct source-grounded path to verify the BLE transformation rather
than continuing speculative frame replays. The method bodies have
**not yet been supplied or independently inspected**. No protocol
difference is yet confirmed for the G3V2.


## 2026-11-20 — Decompiled APK e.java proves BLE codec and serial differ from USB

Read user's complete 689-line `com/pulsenet/inputset/util/e.java`
from actual KeyLinker APK (SHA256 `09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee`).

Direct source observations:
- outgoing GATT write in `util/b.java` calls `e.A().h(bArr)` before
  `BluetoothGatt.writeCharacteristic` to e1;
- incoming e2 `onCharacteristicChanged` calls `e.A().g(value)`;
- e.h lines 384–409 uses `(serial + nonce - 154)`,
  `(serial + nonce + 155)` and f2746g / f2747h masks;
- e.g lines 356–378 is the corresponding inverse;
- e.k lines 444–449 is CRC using the table common to both;
- e.K() lines 92–99 starts `01`, then `82`, `03`, `84`, while USB
  host session from native KeyLinkerlib starts `00`;
- e.E() uses Java Random for the nonce;
- e.L() is 0x91 identification with no payload;
- e.q(0) is B7 lighting read with one selector byte 00.

Verified static vectors with fixed nonce 0x29:
`B7 06 00 29 00 A4` -> USB `CC B3 40 70 99 15`
versus Android `71 A2 A2 90 2C 8E`.
Android `91 05 01 29 FB` -> `48 A0 A5 90 21`.
Android B7 after identity `B7 06 82 29 00 96`
-> `EF 20 24 90 A2 BC`.

The BLE silence under v2/v3 is thus not a meaningful rejection
of valid app protocol; the transmitted host bytes did not match the
actual Android e.h transform or serial generator.

Published `tools/g3v2-android-keylinker-codec.py` and
`tools/g3v2-le-android-identity-b7.py`, with separate portable
codec and single-link, bounded identity-first read experiment.
The latter verifies all UUIDs/CCCD and sends only 0x91, then 0xB7
iff identity gets a decoded, CRC-valid, matching-serial notification.
No firmware writes, OTA, pairing, lighting 0x37, or retries.
**Not yet executed on hardware.**


## 2026-11-20 — BREAKTHROUGH: Android-coded 0x91 + B7 over BLE really answer

The user ran `g3v2-le-android-identity-b7.py --mode identity-then-b7`
on the real G3 V2. Direct LE Public connect: **505.1 ms**.
CCCD `0x000A` read one byte `01` before standard `01 00`
subscription, and subscription got an ATT Write Response.

**Identity read 0x91**
- TX plain: `91 05 01 CB 33`
- TX APK wire: `02 30 4A F2 C6`
- RX e2 wire: `C8 A0 BD 88 22 F5 C0 14 A5 37 C0 E1 16`
- RX APK decoded: `19 0D 01 31 07 10 06 20 11 01 48 46 B8`
- native response opcode `0x19`, CRC valid, matching serial `0x01`;
- 7-byte identity `10 06 20 11 01 48 46`: logical hardware
  `1006:2011`, firmware `1.48`, raw flags `0x46`;
- query round-trip **23.0 ms**.

**LampLight read 0xB7**
- TX plain: `B7 06 82 FA 00 E7`
- TX APK wire: `1C 71 F7 C1 93 CD`
- RX e2 wire: `32 FB 77 41 15 B3 F3 F1 FE 40 BD 46`
- RX APK decoded: `19 0C 82 7A 06 00 FF FF 00 00 FF F9`
- native response opcode `0x19`, CRC valid, matching serial `0x82`;
- six-byte logical record `00 FF FF 00 00 FF`, matching the earlier
  USB LampLight B7 baseline exactly. RGB=(0,255,255),
  breath/switch packed field 0, brightness=255;
- query round-trip **22.0 ms**.

Conclusions: valid native KeyLinker application responses have been
received over Linux direct LE GATT; Android APK codec and serial
are correct; CCCD one-byte Read Response did not prevent the e2
notification after standard subscribe; the BlueZ/Bleak BR/EDR
selection issue can be sidestepped with direct typed LE L2CAP.

No lighting configuration write or OTA/SCSI command was sent.
The successful reads do not resolve whether `0x37` writes commit
flash or are safe for continuous ambilight. Record this milestone
in `docs/G3V2-BLE-VERIFIED.md` and protect against confusion
with the earlier unsuccessful probes. No repeat is required.


## 2026-11-21 — Static-only Android app command call-site audit prepared

BLE identity/lighting reads are solved. The remaining critical path
is an LED update command safe for recurrent Ambilight changes, not
another transport/CRC test.

Decompiled `com/pulsenet/inputset/util/e.java` has clear opcodes:
- `l0(int[],int)` emits 0x37 (decimal 55), G3V2 lighting config writer;
- `X(byte[],int)` emits 0x25 (decimal 37), **unidentified**;
- `h0(int)` emits 0x25 (decimal 37), **unidentified**;
- `q(int)` emits B7 lighting reads.

The distinction between decimal 37 and hexadecimal 0x37 matters.
No assumption that 0x25 is a real-time RGB command is permitted.
Created `tools/audit-keylinker-android-light-callers.py` for a
read-only search of APK-derived Java call sites and nearby transport
dispatch in the user's local JADX cache. No further BLE experiments
until that targeted source output is inspected.

Charging-dock light reset observation remains an unquantified clue:
do not overinterpret earlier quick-power-cycle 0x37 persistence.


## 2026-11-21 — Actual Android 0x37 and 0x25 call sites extracted (read-only)

The user's source xref audit scanned 142 app-package Java sources
from the JADX cache. These are direct `e.A()` calls:

| Opcode | Construction call | APK caller |
|---|---|---|
| 0x37 (decimal 55) | `e.A().l0(int[],i)` | `util/b1.java:1449,1460,2445,2454` |
| 0x25 (decimal 37) | `e.A().X(byte[],i)` | `util/f.java:107; util/b1.java:2055` |
| 0x25 (decimal 37) | `e.A().h0(int)` | `util/b1.java:2012` |
| 0xB7 (read) | `e.A().q(int)` | `util/b1.java:1791` |

The two observed 0x37 construction loops split arrays in 15-byte
fragments, consistent with protocol framing. The 0x25 callers appear
in a distinct data arrangement with three-byte grouped arrays; do
not infer LED streaming or volatile updates. Several loose-name
matches elsewhere in the APK are false positives due to obfuscation.
The meaning of both 0x37 calling contexts and 0x25 is unresolved.

Next user-side read-only command: show bounded sections
`b1.java:1395-1480,2390-2470,1970-2070` and
`f.java:55-120` to identify enclosing method names, payload
construction, and whether any write path is distinct from persistent
configuration. Avoid repetitive BLE/USB hardware probes.


## 2026-11-21 — Two Android 0x37 serializers, 0x25 identified as Turbo mode

User supplied direct decompiled Java method bodies:

**`b1.A1(List)` (1427–1465):**
The app prepends `6 * lighting entries`, appends
`lighting_r/g/b/data4/data5/light` from
`HostMenuBean.LightingDataBean` and partitions into
15-byte fragments through `e.A().l0` (**0x37**).

**`b1.z1(List×4)` (2396–2459):**
Four 6-element lighting inputs are normalized by helper
`k`, then per-record indexes 3/4/5 are remapped:
`[r,g,b,flag,brightness,speed]` →
`[r,g,b, speed & 0xFF,
 (speed >> 8 & 0x0F) | (flag ? 0x80 : 0),
 brightness]`. The source explicitly formats speed to
12 bits and emits the same 15-byte `0x37` chunks.
The exact effects of helper `k` upstream still require
inspection before declaring all UI inputs semantically final,
but the downstream wire format is clear and agrees with the
known native USB LampLight record.

**`b1.f1(...)` (1976–2070):**
Bulk configuration path starts with modes `0x17`, `0x18`,
then sends `0x25` metadata with `h0` and grouped three-byte
records with `X`; later follows other command families.
In `util/f.java`, `X` is also used to build an offline
representation of 3-byte grouped records.
This is clearly **not** a dedicated single RGB/lamp writer.

Family corroboration from
`https://github.com/AmjadAAYD/x20ctl/blob/main/docs/01-protocol.md`:
opcode `0x25` = `CODE_TOOBLE_MODE` (Turbo).
Thus `0x25` is removed from the active hypothesis list for
volatile LED updates, though the G3V2 has not separately
been sent this command as a hardware verification (nor should
it be for Ambilight).

Next objective: inspect firmware for distinct volatile LED
output updates or NVM save scheduling; do not keep mining
the same Android call sites or do repetitive BLE read tests.
The Android app's `0x37` paths do not demonstrate
high-frequency/wear-safe operation. A controlled, no-new-write
baseline/dock/B7 + visible-effect observation may help separate
mode reset from RGB register persistence.


## 2026-11-21 — Green solid B7 baseline; user's dock observation specifies both color and mode

Clarification from user: a manually powered-down G3V2 placed on its
charging dock for **several hours (unmeasured)** eventually seemed to
forget **both the color and the effect/mode**. The earlier event was
not accompanied by before/after protocol logs and does not establish
flash erasure or prove the dock caused the change independently of
power-off duration.

Current read-only Android BLE capture with visible **solid green**:
- LE connection 474.9 ms;
- 0x91 identity confirms logical ID 1006:2011, firmware 1.48;
- B7 decoded response `19 0C 82 AA 06 15 F6 05 00 00 FF 7D`;
- six logical LampLight bytes `15 F6 05 00 00 FF` =
  RGB(21,246,5), packed field 0x0000, brightness 255;
- B7 roundtrip 21.9ms with valid response CRC and serial 0x82.

This six-byte observation and the user's visual note form a new
**pre-dock baseline**. If the user chooses to repeat an *extended*
off+dock event, first record visible LED color/effect immediately
after removing from dock and powering up, then do a single verified
BLE read of B7; compare exact bytes to baseline. Only afterward
consider a long off-without-dock control case. Do not use 0x37
writes, reset firmware, or assume a particular number of hours
is necessary. Visible effect can vary independently of B7.


## 2026-11-21 — Important qualification of earlier dock experiment

User corrected the retrospective observation: the controller had
reached **100% charge** and **all illumination was OFF when
restarted** following several hours on the dock. There has been
**no controlled dock test yet**. Earlier statements that *both
the remembered color and mode were erased* are not hardware-validated:
when LEDs are all off neither the underlying RGB record nor
effect selection is visible, and no B7 record was read during that
earlier event.

This may represent a charging/full-battery LED-off policy,
a restored default mode, volatile state loss, or a changed RGB
record; the cause is **undetermined**. Retain the independent
current pre-experiment baseline solid green,
B7 = `15 F6 05 00 00 FF` (RGB 21/246/5, brightness FF).

If the event occurs again, inspect the LED state immediately,
avoid button hotkeys that alter lighting, then perform **one
read-only B7 query** using the already verified Android BLE
path. Never use repeated 0x37 writes just to reproduce.


## 2026-11-21 — Video demonstration of joystick-reactive RGB animation

The user uploaded a short (~28.5 sec) captured tutorial clip of a
Machenike G3 V2. In the clip, the second lighting effect responds
with changed colors as the sticks move (roughly 2–14 seconds).
A later, third effect presents circular/chasing illumination
(roughly 16–27 seconds). The tutorial displays a host controller
tester simultaneously; therefore the video alone does **not**
prove operation while disconnected.

This adds a new strong firmware hypothesis: a stored RGB effect
selector/program plus an internal runtime loop reading the Hall
joystick input and driving the LED rings. That architecture is
fully compatible with continuous color changes with **no 0x37
host transactions per color frame**, and without any per-frame
flash writes. The independently established local Fn+D-pad-Right
mode cycling and the fact that Fn+Right changes visible effect
without changing B7 RGB fields support separation of the
effect engine from the six-byte LampLight record.

No claims that RAM-only LED commands are remotely accessible:
built-in effect execution and external programmable Ambilight
are distinct properties. Recommended reversible test is
disabling/disconnecting host link while the gamepad remains
powered and moving the sticks; stop before sleep confounds
results. If effects still react, internal execution is proven;
if not, distinguish sleep/power behavior before attributing
lighting changes to host traffic. No 0x37 BLE/USB writing.

External manual cross-check:
`https://manuals.plus/ae/1005010417633958` lists
Fn+D-pad Right as cycling local lighting modes and Fn+D-pad
Left as brightness adjustment. The upload/media itself and
per-device identity are kept outside the public repo.


## 2026-11-21 — Decryption of G3V2 FOT selected as next major project track

User explicitly chose the **firmware-package decryption** option.

Reviewed and rejected already exhausted variants:
- two same-HWID G3V2 v1.48 FOTs have identical
  XCOD/XRES content and differ only in duplicated header
  metadata: no plaintext-difference oracle;
- no host-side decryptor in the official updater: F0
  transfers the embedded ciphertext bytes as-is;
- high entropy, obfuscated XOR-0x80 section names and
  simple crypto probes are not a new result.

New public comparison (verified links):
`https://github.com/LopeKinz/Manba-One-V2-Firmware-tools`
reports X20 v1.18/1.21 same-HWID reused section stream;
`https://github.com/LopeKinz/Manba-One-V2-Firmware-tools/issues/2`
reports extensive failed cipher sweeps and missing public
same-ID test images; this supports a differential strategy
only when a **genuinely different code version with
the exact G3V2 HWID** can be obtained.
`https://gamepadla.com/machenike-g3-v2.html`
records v1.25 in January 2025 but the old FOT is
not yet found and HWID equivalence is unverified.

Prepared `docs/FOT-RECOVERY-PLAN.md` as canonical
grounded/no-repetition research plan.

The next zero-hardware experiment is the existing
`tools/probe-firmware-history-api.py` against eight
conservative GET routes for product id 8, firmware id 11;
the script has no logged executed result yet. If no old
version surfaces, do not brute-force the vendor archive.
A historical pair is not itself plaintext; if
`C_i=P_i XOR K` with reused K, then
`C1 XOR C2=P1 XOR P2`, which may reveal only
differences and requires known plaintext for decryption.
No binary, MAC or unique personal device identity
uploaded to the research repo.



## 2026-11-21 — Eight historical-firmware GET probes yielded uniform 405

User's actual console capture:
- `/firmware/11`
- `/firmware?id=11`
- `/firmware?product_id=8`
- `/firmware/list?product_id=8`
- `/firmware/history?product_id=8`
- `/firmware/by-product-id?product_id=8`
- `/firmware/by-product?product_id=8`
- `/firmware/all?product_id=8`

All returned HTTP **405 Method Not Allowed** and JSON
`{"message":"405 Method Not Allowed"}`. This
exhausts that **specific guessed GET list**; it does
not exhaust the vendor's unknown API or prove its
historical data is absent. Do not reinterpret 405 as
affirmative evidence of a valid POST endpoint or try
mutating HTTP methods without an actual contract.

Separate public research: targeted English/Chinese
searches plus GitHub code search found **no downloadable
different-version ZXD2267 FOT** verified at this time.
The G3 V2 manual points to a legacy Machenike download
path, but its present availability is not confirmed.

Correction of version certainty: Gamepadla labels the
Jan 2025 number `1.25` as **XInput Firmware**;
whether it maps 1:1 to the ZXD2267 firmware update
header's `1.48` is unknown. Search for the HWID
rather than rely on that apparent version number.

Next step: contact OEM support or a researcher who
has an older genuine G3V2 updater, then validate
HWID `a1ca51da724cb6103658c05d7d4e2267`
using existing `scan-g3v2-local-fot-corpus.py`.
No device read/write or firmware update performed.



## 2026-11-21 — Wayback Machine leads for historical G3V2 installer URLs

The user proposed looking up older G3V2 firmware links in Internet
Archive Wayback snapshots. This is a worthwhile **URL recovery**
strategy even when the EXE itself was not archived.

Original download-page paths grounded by distinct contemporaneous
G3 V2 manuals:
- Chinese: `https://machenike.com/shoubing/download`;
- Global: `https://global.machenike.com/shoubing/download`.
Source manuals:
`https://device.report/m/c90e1ba13aa83314feb8a35fe43d881669dfadb51d66f044282a428fcf3f7ebf.pdf`;
`https://manuals.plus/m/3641d84ab7ee9e4549bdab839ca39be0dd01922ed353b8dbbd989c023cd15979`.

**Verification status:** Internet Archive index and replay URLs
were inaccessible from this assistant's web-retrieval environment,
including CDX and the public availability endpoint. Therefore
**no actual capture or historical download link was confirmed**.
Targeted public search-engine queries for archived
`ZXD2267` / `JRH_G3V2` links also yielded no credible match.
Do not say Wayback has a historical v1.25 package, or that
there are zero snapshots: both remain unknown.

Added `tools/audit-g3v2-wayback.py`:
- bounded Wayback CDX GET on four known plausible Machenike
  controller-download page variants, 2023–2026;
- availability API fallback for selected years;
- fetches only archived HTML/text (no binaries), lists
  potential firmware, installer and download links;
- read-only network operation, no controller access,
  no Bazzite base changes;
- intended execution in the user's Linux Bazzite network
  environment when Wayback is reachable.
  Report CDX errors explicitly if the archive is blocked
  from there too.

If the tool extracts original URL(s), separately check their
availability or archive record, without executing a downloaded
updater. Only a genuine complete FOT with exact
`a1ca51da724cb6103658c05d7d4e2267` and
**different** XCOD content would advance our decryption goal.



## 2026-11-21 — Wayback CDX and availability fallback both failed to surface Machenike URLs

Actual test from user's Bazzite returned
`CDX: empty response` and `Selected: 0` for all
four known/plausible Machenike controller-download pages.
This does **not** certify that Wayback has never archived
the site. No old URL was recovered; do not repeat the same
requests as a "new" test.

Confirmed Common Crawl maintains separate 2024 and 2025
crawl indices (`https://index.commoncrawl.org/collinfo.json`).
Assistant's web tool could open the crawl index catalog,
but couldn't access URL-specific archive queries; this is
a tool/network limitation, not an empty-result finding.

Committed new independent archive-only script
`tools/audit-g3v2-commoncrawl.py`.
It queries a few representative CDXJ indices for
`machenike.com/shoubing/*` and variants, and
optionally fetches historical HTML only via ranged WARC
requests, searching for `ZXD2267`, G3V2 and potential
installer URLs. It has not been run on the user's network.
No device or OS modifications are needed.



## 2026-11-21 — v1.59 exact G3V2 HWID: distinct XCOD/XRES recovered offline!

The user discovered Machenike Chinese official download page
`https://www.machenike.com/driver/download/?id=190`
and uploaded `MKG_FW_G3V2.zip` (2,403,883 bytes,
SHA256 `54a98a5e568989275f3b6264c518341609240e70b85a511de4b09266a5e5d9c5`).

Contents inspected without executing:
- `MKG_FW_G3V2.exe` (controller), SHA256
  `d511557ff30542b1513ca645ebd8631a9287757d160225687e2bc0c8cf0f66d9`;
- `MKG_FW_G3V2.Receiver.exe` (separate receiver update),
  SHA256 `aa6c5e16d2297e32a9b5765040e63ed18a1bef6cb03c473589e575d7d5cff41c`;
- Chinese text instructions; no firmware execution.

Complete **controller** FOT at updater EXE `+0x1712F0`,
size `0x5B400`, SHA256
`ebb611eb8d4d730e991fa1f8027d653152515c4e47903c07537a6d7fadd4e849`.
Header's hardware ID at `0x70..0x7F`
`a1ca51da724cb6103658c05d7d4e2267`
matches v1.48 exactly. Version `0x9F`=159.
Header is repeated at FOT offset `0x5A200`,
hence apparent second FOT signature is only
the directory duplicate. XCOD at `0x200`,
XRES at `0x59600`. Size and hash comparison:
- XCOD 1.48 length 361,472,
  SHA256 `3ddaec3bb4d3723d4d1c55392473e6830872674ce3c33f6ab401b857844821fe`;
- XCOD 1.59 length **365,568 (+4,096)**,
  SHA256 `65b9ed83fdcead0628ccdf4b25ba20ad8dd753c3df5ae9a73f4cc64416b04148`;
- XRES 1.48 length 3,072,
  SHA256 `173740c39fce50700eea71a2616afbf445feabb0217ec4bc6c3ab57919377bb9`;
- XRES 1.59 length 3,072,
  SHA256 `13f6dbea6dc1347f3a7927ef25815fe74c8568acec68f1bb61f5ed400f428bcb`.
KEY field `00000001eae32e09` unchanged.
1.59 XCOD entropy ~7.99946; still opaque ciphertext.

Unlike the two earlier 1.48 FOTs, 1.59 is a genuinely
distinct encrypted firmware image with exact same HWID.
A cryptographic comparison is now justified, but
**different ciphertext hash alone is not proof of
keystream reuse**, and `C1 XOR C2` is not plaintext.

Committed new strict offline cross-version comparator
`tools/compare-g3v2-cross-version-fots.py` because
old comparator rejected different section lengths/layouts.
It accepts local v1.48 EXE and user's v1.59 ZIP,
never starts the EXE or touches the controller.
Results of actual byte-wise 1.48/1.59 comparison
pending user-side execution, because only the new ZIP
was available as an uploaded binary in this turn.



## 2026-11-21 — Real cross-version ciphertext stats (user's console output)

Using `compare-g3v2-cross-version-fots.py` to parse
the *correct* v1.48 and v1.59 controller updaters
confirmed the same G3V2 HWID and KEY record,
but the encrypted program difference is **near-random**,
not a direct XOR-based firmware recovery.

XCOD length 361472 → 365568 (+4096).
Same-position equality 2002/361472 (0.5538%),
XOR H=7.99908. There is one exact 512-byte
ciphertext match at relative XCOD +0x800.
Its 32 contiguous 16-byte blocks constitute
**all** 16-byte aligned same-position matches;
outside that 512-byte island,
1490/360960 bytes match (0.4128%),
close to random expectation 0.390625%.
No other exact 512B shifted matches within
the 32KiB range investigated by this script.

XRES length 3072 on both. 2074/3072 equal
(67.5130%), XOR entropy 3.29729 due to
unchanged ciphertext. Four 512-byte blocks
are identical in a single span XRES
relative +0x200 through +0x9FF, plus a
24-byte structural matching prefix.
Other XRES bytes changed, with random-looking
differences. Do not infer plaintext entropy
from the XOR of identical ciphertext.

CONFIG 957/1024 equal, as expected for plaintext
settings in the section; not evidence of decryption.

The XCOD observation is less favorable to
straightforward same-keystream comparison than
the EasySMX X20 reference, where consecutive
versions preserved >99% of ciphertext. This
does NOT disprove identical keystream: source
reordering, compressed plaintext, or per-build
key derivation also fit some of the evidence.
The four identical XRES blocks support exact
ciphertext reuse for unchanged resources,
but not recovery of the secret for *changed*
resources or the program.

We must now distinguish **ciphertext identity**
from **recovery of original code**. Neither
the v1.48 ↔ v1.59 XOR nor XRES overlap
is an executable dump, deciphered plaintext
or a portable per-model encryption key.

Next high-value target: a third same-HWID
firmware (especially version nearer 1.59)
to test whether code overlap follows version
distance and whether identical XRES blocks
persist; or a same-HWID plaintext reference.
No controller or immutable OS modification.


## 2026-11-21 — Consolidated user PDF of further LED/B7 and timing results

Reviewed a user-exported 88-page Claude discussion and recorded already executed tests in `docs/AMBILIGHT-TESTES-2026-11-21.md`. We distinguish raw user logs, observed visual behavior, and assistant hypotheses; none of the older studies is being rerun.

**Color versus visual mode:** after green was applied in KeyLinker, extended charging-dock time left LEDs visually off, yet three BLE B7 reads yielded identical `15 F6 05 00 00 FF`, before FN+Right activated rainbow. This establishes preserved registered color despite LED-off, NOT demonstrated NVM/flash storage, and corrects earlier mistaken speculation that the dock set green automatically.

**Apply from rainbow:** KeyLinker user applied purple; observed solid purple afterwards; BLE returned `80 00 FF 00 00 FF`. Color apply can supersede a running local effect.

**New timing experiment:** v2 compared eight equal-color and eight changing-color 0x37 writes, with one equal warm-up. All output was restored to original white. ACK medians 5.88ms vs 6.88ms, permutation p≈0.830 for mean delta; following B7 medians 5.02ms vs 8.02ms with p≈0.076. One equal-color ACK outlier took 31.89ms. No statistically persuasive flash-commit evidence, and USB polling/scheduling likely limits interpretation. Do not retry more writes.

**Safety/provenance:** PDF shows a v2 `--dry-run` command near output containing writes, and the v2 script isn't committed in the repository. Without source, cannot say if that flag was unsafe or if the user pasted another full execution. Flag v2 as *not safe to invoke unreviewed*.

Firmware v1.48/v1.59 offline decryption track unchanged; no plaintext recovered. No MACs, personal information, or proprietary PDF added to repository.


## 2026-11-21 — Offline regression against actual uploaded v2 and BLE diagnostic Python

User supplied `g3v2-light-write-timing-v2.py` (SHA256 `1785cad636599330d417300a7a977e64f63a1ce1e1f5c76708d06f0b30f3568b`) and `g3v2-le-connect-diag-v1.py` (SHA256 `7859fe28692a000041e08da9ed6021cebcb78c37840acfb1323a006b66a5dd35`).

Both sources passed `py_compile`. Isolated execution of the real `main()` functions with in-memory fake persistence/HID/socket objects showed: dry-run 0 writes and 6 synthetic B7 reads, full default flow 17 synthetic writes and a restored simulated final color, and BLE one fake L2CAP connection, no data sending. Imported helper code's main guards prevent write execution merely upon import. No device was accessed.

Revised the earlier caution about an ambiguous `--dry-run`/0x37 log juxtaposition in the exported PDF: this is **not** evidence that the flag is broken, and the supplied code did not reproduce the issue. Complete physical v2 test should not be rerun; none of the software-only findings establishes Flash safety or a volatile RGB command. Full detail in `docs/G3V2-SCRIPT-AUDIT-2026-11-21.md`.


### 2026-11-21 — Future troubleshooting reference: G5 Pro receiver/controller firmware recovery

At user request, archived external Reddit report for reference if the G3 V2 later develops symptoms after calibration, lost 2.4 GHz, or permanently pressed triggers:
https://www.reddit.com/r/Controller/comments/1v0dmci/solved_machenike_g5_pro_triggers_permanently/

Full structured G3V2-safe note: [TROUBLESHOOTING-REFERENCES.md](TROUBLESHOOTING-REFERENCES.md).

Reddit author reports G5 Pro USB/Bluetooth working but 2.4 GHz failure and triggers LT/RT held at 1.00 after calibration; recovery by installing matching official Chinese **G5 Pro** tri-mode controller and receiver firmwares separately. This is user anecdote, **not** confirmation of the same failure or safe repair for G3 V2.

Relevant cross-check: our downloaded official G3 V2 package already has two distinct executables for controller vs receiver. If G3 V2 ever fails, inspect both components' IDs/versions/communications read-only before considering any firmware action. **Never flash G5 Pro binaries or execute G5 Pro recovery hotkeys on a G3 V2.** No existing G3 V2 hardware issue is asserted.


### 2026-11-21 — New silicon vendor trail: ZC32RF1M / ZhiXu Technology

The user supplied EEVblog discussion:
https://www.eevblog.com/forum/microcontrollers/which-brand-is-this-microcontroller-logo-with-2-x/

**EEVblog source unavailable to automated inspection**, so the thread's logo identity is **UNVERIFIED**. No assumption that the "two X" logo matches the chip in our device.

An independent 2021 FCC internal-photo exhibit for another controller **explicitly names ZC32RF1M manufacturer ZHIXU TECHNOLOGY**:
https://device.report/m/f762c4fd93632c35cdd85bdeba1214b50f66fd8613c60fa1a26f4b02254368fa

ZhiXu's **official** site confirms it develops gamepad chips and boards, provides technical support and mentions SDKs:
https://www.smart-one.cn/page?p=about_company
https://www.smart-one.cn/service-support
https://www.smart-one.cn/contact
Public contact: info@smart-one.cn. No publicly available ZC32RF1M datasheet/SDK or FOT decrypt routine was found in this investigation.

Separate controller patent names **ZC32RF1M LQFP48** (NOT the G3V2's QFN40): https://patents.google.com/patent/CN112439189A/en. Do NOT transfer package pinouts or attempt physical hardware probe based on this source.

Complete evidence and targeted questions for ZhiXu support documented in [ZC32RF1M-MANUFACTURER-LEADS.md](ZC32RF1M-MANUFACTURER-LEADS.md). This is a **non-destructive, static-documentation next step** that could clarify flash endurance, CPU architecture, debug/bootloader and encryption; it does not yet enable firmware decryption or make high-rate 0x37 writing safe.


## 2026-11-24 — RGB-01/02/03: two LampLight records accepted

Static CIL confirms LoadLightAsync sets LightCount from returned lampLightNumber; MixDrive writers replicate one LampLightStruct across all selected slots. Native receive at 0x180005160 computes logical length / 6, checks divisibility, and stores count at offset 0x60. Live G3V2 1.48 USB RGB-03: baseline B7 `FF FF FF 00 00 FF` (6 bytes); one 0x37 frame `37 12 83 F1 0C FF 00 00 00 00 40 00 00 FF 00 00 40 1C`; outer ACK flags 01 payload `31 AD`; subsequent B7 returned exactly `FF 00 00 00 00 40 00 00 FF 00 00 40` (12 bytes). No 0x3B, OTA or SCSI. This proves immediate logical acceptance/readback of two distinct records, NOT physical independent ring rendering or power-cycle persistence of the expanded record. Visual confirmation pending; do not repeat write or use old restore helper which refuses a 12-byte baseline. Original snapshot retained locally. No high-frequency writes authorized.

### RGB-03 visual follow-up — 2026-11-24

User reports physical illumination is red only after the verified 12-byte red/blue LampLight write. The second blue record is present in immediate B7 readback but not visibly rendered in the tested mode. Independent physical channel control is NOT demonstrated. Next bounded experiment: manually cycle native FN+Right effects with the existing two-record object, without further host writes, and look for use of both custom colors (distinguish from stock rainbow). No expanded-record persistence claim.

### RGB-04 — native effects visual check

User observed no discernible new custom red/blue behavior while checking native effects with the expanded LampLight configuration. This is a negative visual observation, not proof that every internal effect ignores the second record. Next proposed bounded test is reversing the two records once and checking B7 and physical solid-mode output; preserve the original six-byte snapshot. No continuous writes.

### RGB-05 — reversed LampLight records, first-record rendering corroborated (2026-11-24)

G3V2 firmware 1.48, USB. Initial B7 retained RGB-03's 12-byte red/blue configuration across the subsequent session and native effect cycling. One 0x37 write reversed records: plaintext `37 12 81 23 0C 00 00 FF 00 00 40 FF 00 00 00 00 40 60`; ACK flags 01 payload `31 AD`; B7 returned exactly `00 00 FF 00 00 40 FF 00 00 00 00 40` (12 bytes). User reports physical blue only. Together with RGB-03 red-only output, this strongly supports first-record-only rendering in the tested solid mode; second record is accepted logically but independent physical rendering is not demonstrated. RGB-04 native effect cycling showed no discernible custom palette. Do not repeat order reversal or expand to 3/4 records without new evidence. Original six-byte white snapshot `FF FF FF 00 00 FF` remains saved locally. Restore with the known single-record writer after validating expanded baseline, then verify B7; old restore helper rejects expanded baseline. No power-cycle persistence of expanded record established; no OTA, SCSI or 0x3B writes. Next distinct avenue requires independent evidence of firmware effect/palette selection or model-specific LED routing; standalone game-triggered single-color themes remain feasible.

### CROSS-RGB-01 — comparative hardware evidence (2026-11-24)

JYS-NS207 manual documents individually selectable Lamplight1/2, solid and breathing (https://device.report/manual/2467075). FCC model equivalence declaration confirms NS207/NS227 same PCB/circuit (https://fccid.io/2A5DV-JYS-NS207/Letter/Declaration-Letter-of-Model-Difference-5760302). FCC internal photos p4/p6 show probable ZC32RF1M marking (not unequivocal) and board marking JYS_ZXD1859_Wakeup_KEY V1.1 (https://device.report/m/9c5b2f8813013652107fb19a57bee7bbe008404da57fec4b926727d38d110fb8.pdf). YS46 schematic identifies ZC32RF1M and RGB/PWM nets (https://fcc.report/FCC-ID/2A9NY-YS46/6276988.pdf); its manual confirms KeyLinker (https://device.report/manual/6018318). Schematic screenshot inaccessible (403), so no confirmed topology interpretation. ELISWEEN NC300 four-zone claim remains secondary-source evidence. None establishes a new G3V2 lighting opcode or autonomous custom palette. RGB-06 restoration reported done by user, raw verification log not supplied. Next distinct offline target: trace Android lamp-zone UI selection to serializer, compare external implementations; do not repeat G3V2 multirecord writes.

### CROSS-RGB-02 — Android zone capability mask (2026-11-24)

User's offline JADX audit of KeyLinker 3.73 (143 app Java classes) identifies HostActivity.java:5962-6002: iArr[7] is converted to a bit string; positions 7,6,5,4 select Lamplight1/2/3/4 respectively, create LightingDataBean with lighting_No 1..4 and populate menu/data list. Assuming standard MSB-first conversion (w0.a to verify), masks are 01/02/04/08. HostActivity:4455-4457 O9 calls b1.L().A1(sb.getData()); previously audited serializer sends sequential six-byte records via 0x37, not lighting_No. HostActivity:9082-9095 maps B7 data sequentially with min(length/6,list size). No alternative zone opcode identified; no model identifier hits. New target: trace origin of iArr[7] and capability parsing, compare existing B0/feature evidence before any hardware query. UI capability mask is not proof of firmware zone enablement. No device access or writes.

### CROSS-RGB-03 through 06 — historical Feature response resolves zone capability

Android w0.a confirmed MSB-first. HostActivity.ab uses feature[7], CKAdvanced/Simplified strip first byte then z1.i.y uses index 6. b1.e1 copies decoded range [4,length-1), j retains full payload for opcode 176, dispatch case 176 with f2694g=1 invokes p0 -> y1.d.r. ConnectionActivity registers y1.d and forwards feature arrays; exact assignment not inspected. Historical CRC-validated request/response in docs/HID-KEYLINKER.md: B0 07 00 29 00 01 E5 -> 19 10 00 09 0A 03 03 03 01 03 03 01 02 C0 00 EA. Applying Android extraction yields 0A 03 03 03 01 03 03 01 02 C0 00, feature[7]=01, only zone 1 advertised. Consistent with RGB-03/05 physical first-record-only rendering. No evidence of writable capability mask or alternate zone opcode. Close zone-mask investigation unless new evidence; no new device query or write. Native rainbow selection/custom palette remains unresolved; firmware or distinct manufacturer-specific effect commands are separate avenues.

### RGB Studio — mandatory performance requirements (2026-11-24)

User requires functional features with extremely low CPU/GPU overhead, including when games saturate CPU/GPU. Standalone app; prioritize event-driven telemetry, process lifecycle notifications and existing input events, then lightweight audio statistics; optional low-rate HUD ROI analysis only if end-to-end capture overhead passes benchmarks. No continuous full-resolution capture, GPU inference, continuous OCR, busy polling, or background GUI rendering. Suggested architecture: small native Rust service plus separate optional Qt GUI, low CPU/IO scheduling priority, adaptive backoff or suspension of optional recognition under contention, maintain last autonomous controller effect. Proposed (not measured or guaranteed) acceptance targets on 16 logical CPUs: near-zero idle, <0.1% total CPU for lightweight recognition, <0.5% total CPU for optional HUD, <30 MB service RSS, no dedicated GPU recognition, no measurable FPS/frame-time degradation in repeatable saturated-load A/B tests including KDE and Gamescope. Account for compositor/PipeWire overhead and 1% lows, not just service CPU. Persisted 0x37 writes remain separate safety concern: deduplicate, explicit opt-in reactive mode, dry-run and conservative rate limits; no claimed safe endurance until established. No implementation or benchmark performed yet. User requirement takes precedence over feature breadth.

### RGB Studio — mandatory performance design requirement

User requires fully functional game-context lighting with extremely low CPU/GPU overhead, including when games saturate CPU/GPU, without sacrificing required recognition results. Prefer event-driven native backend (Rust proposed), independent Qt UI, official telemetry first, lightweight audio features, small calibrated HUD regions only as needed, no heavy ML or dedicated GPU compute, suspend hidden UI animation, bounded queues and low-priority workers. Measure backend plus indirect compositor/PipeWire overhead; compare baseline and enabled FPS, 1% lows and frametimes under CPU/GPU saturation. Proposed unverified targets on 16 logical CPUs: idle <0.05% total CPU, telemetry <0.1%, audio <0.2%, HUD <0.5%, backend RAM <=30 MB; zero dedicated GPU compute, not a guarantee of zero capture overhead. No silent degradation of recognition: defer or explicitly report unavailable context when resource limits prevent reliable detection. All adaptive recognition initially dry-run; 0x37 persistence means no continuous writes, no endurance guarantee, real adaptive writes opt-in only after evaluation. Independent from QuikLight/LumaSync; preserve KDE/Gamescope and immutable Bazzite base. No implementation or benchmark performed yet.


## 2026-11-24 — Machenike Control v0.2 wireless observation

User log shows ZhiXu receiver enumerated as 045e:028e, one vendor-specific XInput interface bound to xpad, interrupt IN 0x81 and OUT 0x01, max packet 32 bytes. Earlier enumeration showed Receiver Update 1a34:f517 on the same physical port; no configuration HID interface in normal XInput mode. With receiver present, BLE G3V2 APP discovery and direct LE connection succeeded (467.6 ms), identity 1006:2011 firmware 1.48 (22.9 ms), B7 read FF FF FF 00 00 FF (21.9 ms). GTK4 and Adw available on Bazzite. This confirms BLE access with receiver present, not yet simultaneous active game input, direct receiver lighting writes, or BLE lighting writes. No 0x37 or firmware mutation in this test. Next: verify active game input during BLE access and isolate one manual RGB change with backup/readback; no streaming or automatic persistent writes. No device MAC retained.


## 2026-11-24 — v0.3 Alpha: first physical BLE RGB write

User explicitly confirmed that the controller LEDs turned green after the v0.3 Alpha manual BLE RGB test. This establishes physical RGB change via BLE on G3V2 firmware 1.48, following the previously validated identity/B7 transport and hybrid receiver setup. Exact TX/ACK/B7 post-write logs, restoration result, and active input behavior during the write itself have not yet been supplied. Do not infer volatile operation, safe streaming, brightness control, breathing control, or independent ring control. Next: verify restoration and capture transaction log, then test one parameter at a time. No per-device MAC recorded.


## 2026-11-24 — v0.3 RGB readback and restoration

Independent BLE B7 read after manual green application returned `00 FF 00 00 00 FF`, valid CRC and matching serial; identity 1006:2011 firmware 1.48, BLE connect 377.3 ms, identity 23.0 ms, B7 22.9 ms. User confirms visual restoration to white at maximum brightness and green also at maximum brightness. Restoration B7/ACK transaction log not provided. Earlier ENOSYS was reported but its origin has not been isolated; later operation succeeded. No evidence yet of physical brightness response, breathing speed response, volatile writes or safe streaming. Next test individual brightness and breathing parameters with explicit manual writes and backup. No MAC recorded.
