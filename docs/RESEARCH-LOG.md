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
