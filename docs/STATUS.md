# Current status and roadmap

Last consolidated: 2026-11-20.

## What is already confirmed

### Normal USB/HID mode

- VID:PID: `413D:2131`
- Linux interface used by the protocol: MI_01 / sysfs `:1.1`
- Device access is through the corresponding dynamic `/dev/hidrawN`.
- Feature discovery proved that the device advertises OTA support.

### Firmware handshake

MixDrive sends a special connection/identity request with this shape:

```text
80 F9 <seq> 01 01 30
```

A real response included:

```text
81 F9 05 01 13
5D 45 08
5A 58 50 43 5F 41 50 50
01 48 10 06 20 11
1B 00
...
```

`ZXPC_APP` is the 8-byte ASCII marker identified by the preceding length byte `08`.

Official `GamepadDeviceManager::OnReport` IL proves that the six bytes immediately following that marker are returned as a tuple:

```text
01 48 10 06 20 11
```

The MixDrive consumer formats:
- Item1 as firmware major;
- Item2 by converting to hex text and then parsing that text as decimal.

Therefore:
- `01` -> major 1
- `0x48` -> text `"48"` -> decimal 48
- displayed firmware = **1.48**

The remaining four bytes form two internal IDs:
- `0x1006`
- `0x2011`

### OTA feature

The normal feature response was reconstructed and decoded. Native code maps bit `0x80` of the relevant feature byte to `isHasOta`. The G3 V2 response has that bit set.

### Lighting / custom data

Confirmed so far:
- CustomData read selector: `0xBB`
- BB sub-selector: `0x00`
- logical CustomData block size: **20 bytes**
- real A/B readback was successful
- a breathing-blue configuration was successfully exercised

The goal remains to understand the complete lighting model well enough to support effects such as Ambilight without depending on MixDrive.

### Official firmware API identity

The official MixDrive endpoint has now closed the catalog identity for this device.

Confirmed request parameters:

```text
brand=MACHENIKE
product_name=G3V2
product_type=2
```

The endpoint returns firmware **1.48** and the official updater filename:

```text
ZXD2267_JRH_G3V2_MACHENIKE_v1.48_20250815.exe
```

The same query with `brand=THUNDEROBOT` returned HTTP 404.

### Official firmware updater

The official v1.48 updater has been analyzed statically.

Known updater SHA-256:

```text
f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255
```

Extracted `DeviceUsb.dll` SHA-256:

```text
fad660bc7e006c58f37db9d75b72bf30c089ef62fc4e72673cbfd424d247b5e5
```

The updater dynamically loads `DeviceUsb.dll` and resolves:
- `deviceUsb_OpenByDrv`
- `deviceUsb_Close`
- `deviceUsb_GetInfo`
- `deviceUsb_Request`

The actual firmware-transfer protocol is SCSI pass-through using opcodes `F0`, `F1`, and `F2`.

### Embedded FOT image

The updater embeds a complete 369664-byte FOT image.

Confirmed:
- FOT file offset in updater: `0x001712F0`
- FOT RVA: `0x001732F0`
- FOT VA: `0x005732F0`
- complete transfer size: `0x5A400` = 369664 bytes
- exact transfer count: 722 blocks x 512 bytes
- package hardware ID: `A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E 22 67`
- version field: `0x94` = 148 -> firmware 1.48

The FOT has a structured header plus high-entropy XCOD/XRES/tail regions; the updater sends the packaged bytes directly through F0.

## Current critical unknown

The Windows updater expects the device to already be available through the Windows storage/SCSI path. It does not contain the normal HID command that performs the transition.

MixDrive sends a gamepad-specific message immediately before launching the updater.

The managed-code pre-launch path is now confirmed:

- for `DeviceType == 2`, `<<UpdateFirmwareAsync>b__0>d::MoveNext` obtains the item's `Device`;
- it constructs `Machenik.Messages.EnterUpdateModeMessage`;
- it assigns that same device through `EnterUpdateModeMessage::set_Device`;
- it sends the message through `IMessengerExtensions::Send<EnterUpdateModeMessage>`;
- the very next phase constructs `ProcessStartInfo`, starts the downloaded updater, and waits for exit.

Relevant IL offsets in the state machine:

```text
IL+0x0282  FirmwareUpdateModel::get_Device
IL+0x028E  DeviceModel::get_DeviceType
IL+0x029E  ObservableRecipient::get_Messenger
IL+0x02A3  EnterUpdateModeMessage::.ctor
IL+0x02AF  FirmwareUpdateModel::get_Device
IL+0x02B4  EnterUpdateModeMessage::set_Device
IL+0x02B9  Send<EnterUpdateModeMessage>
IL+0x02BF  ProcessStartInfo::.ctor
IL+0x02E9  Process::Start
IL+0x02F1  Process::WaitForExit
```

`CheckFirmwareUpdateMessage` is therefore **not** the pre-updater transition message.

The receiver side is now also identified.

`Machenik.ViewModels.MainViewModel` implements:

```text
IRecipient<Machenik.Messages.EnterUpdateModeMessage>
```

The corresponding async receive state machine is `<Receive>d__21::MoveNext` (RID 2291 / token `0x060008F3`).

Its confirmed flow is:

```text
message.Device
  -> DeviceModel.get_HidDevice()
  -> HidDevice.get_DevicePath()
  -> DeviceManagerCache lookup
  -> reuse or construct GamepadDeviceManager(HidDevice)
  -> GetDeviceCustomDataAsync(10)
  -> data[11] |= 0x80
  -> WriteDeviceCustomDataAsync(data)
```

The full IL proves the exact mutation:

```text
IL+0x010D  ldc.i4.s 11
IL+0x0110  ldc.i4.s 11
IL+0x0112  ldelem.u1
IL+0x0113  ldc.i4 128
IL+0x0118  or
IL+0x0119  conv.u1
IL+0x011A  stelem.i1
```

This establishes that MixDrive enters update mode by setting bit `0x80` in byte index `11` of the managed CustomData buffer, then writing that buffer back.

Static IL of `GamepadDeviceManager` now clarifies that the argument `10` passed to `GetDeviceCustomDataAsync(10)` is a **timeout in seconds**, not a requested payload length. The read state machine calls `readDeviceCustomData(0)`, then waits through `GetResultAsync(..., timeoutSeconds)`. The write state machine calls `writeDeviceCustomData(data, data.Length)` and waits with a 10-second timeout.

Native `KeyLinkerlib.dll` analysis now shows that `readDeviceCustomData` constructs a six-byte `BB 06 ...` read request and returns a pointer+length descriptor after KeyLinker transformation. `receiveDeviceCustomData` is a thin wrapper over helper `0x180001760`, which actually supplies the received buffer. `writeDeviceCustomData` prepends one byte containing `data.Length`, processes `[length][data...]`, fragments it into chunks of at most 15 bytes, adds a four-byte header plus CRC, and applies the same KeyLinker transform.

**Resolved:** `0x180001760`, the 20-byte logical CustomData mapping, the four-byte write-header builder, and the managed-index-to-frame mapping are reconstructed.

For a 20-byte object, the writer forms `[0x14][data0..data19]` and fragments that stream as 15 + 6 bytes. In the first decoded/plaintext frame:

```text
0..3   = four-byte 0x3B header
4      = 0x14 total logical length
5..18  = data[0]..data[13]
19     = CRC
```

Therefore the OTA mutation is now mapped exactly:

```text
data[11] |= 0x80
        ↓
first plaintext write frame byte 0x10 (offset 16)
```

After that mutation, CRC is recomputed and the frame is KeyLinker-encoded.

The remaining transport question is the effective state carried by `E670/E67C/E68C` and the `rand()` position at the exact OTA write, plus the final HID envelope and the device's disconnect/re-enumeration behavior.

## Next steps

1. Close the exact state continuity from the immediately preceding `BB` read through `receiveDeviceCustomData` into the first `writeDeviceCustomData` fragment.
2. Close the final HID report/envelope that carries the two encoded `0x3B` frames.
3. Reconstruct the exact normal-HID OTA/bootloader request byte-for-byte without sending it.
4. Determine the update-mode USB identity and interface class from code or passive observation.
5. Implement a Linux read-only probe for the update personality.
6. Issue only `F1` and `F2` in the first live SCSI test.
7. Compare returned hardware ID/version with the embedded updater metadata.
8. Document recovery/rollback possibilities before any `F0` write.
9. Only after the above, implement a Linux updater with explicit write safeguards.

## Conditions before a write test

A firmware write should not be attempted until all of these are known:
- exact update-mode entry sequence;
- update-mode VID/PID or storage path;
- read-only F1 response structure verified on the G3 V2;
- F2 status semantics verified;
- recovery path or at least a defensible re-entry path;
- exact firmware payload selected for the attached hardware ID;
- write loop and failure handling reproduced deterministically.


### CustomData receive ABI resolved

`receiveDeviceCustomData(datas, length)` uses the Windows x64 hidden-structure-return convention. Its helper `0x180001760` receives the raw buffer in `RDX` and length in `R8D`, decodes frames through `0x1800011C0`, and has an exact 20-byte single-frame threshold matching the write-side `4 + 15 + 1` framing. Static caller behavior strongly indicates that the helper strips transport framing and the logical length prefix, returning only reconstructed logical payload bytes.

Therefore `MainViewModel`'s `data[11] |= 0x80` targets logical CustomData byte 11 (zero-based).

**Resolved:** the remainder of `0x180001760` and complete `0x1800011C0` confirm the receive-side stripping and concatenation.

### CustomData write header resolved

Static disassembly now closes `0x180001C90` itself:

```python
header[0] = command
header[1] = ((E670 & 7) << 5) | (frame_len & 0x1F)
header[2] = ((E67C & 1) << 7) | ((fragment_index & 0x0F) << 3) | (E68C & 7)
header[3] = rand() & 0xFF
```

Evidence:
- `0x18000B214` contains ASCII `"1"`;
- `0x18000B218` contains ASCII `"0"`;
- the conditional move selects `"1"` when `E67C == 1`;
- IAT `0x18000B150` is `api-ms-win-crt-utility-l1-1-0.dll!rand`;
- helper `0x180001400` concatenates its three string arguments in order.

After every write fragment the state evolves as `E670=(E670-1) mod 8`, `E68C=(E68C+1) mod 8`, and `E67C` toggles.

**Current highest-priority target:** statically close `readDeviceCustomData` and the receive path immediately preceding the OTA write, so the exact `E670/E67C/E68C` state relationship between the read and `writeDeviceCustomData` is known. The writer itself is now confirmed to consume the current state first and advance it only after each fragment. A package-wide scan found no `srand`; the default MSVC `rand()` sequence begins with low bytes `29 23 BE...`, and the known plaintext `BB 06 00 29 00 27` matches the first byte exactly. This is strong corroboration, but OTA-time PRNG position remains session-dependent.


### OTA CustomData framing now almost closed

The verified consecutive reads

```text
BB 06 00 29 00 27
BB 06 81 23 00 C0
```

combined with `0x1800014C0` prove the first observed read consumed `E67C=0,E68C=0`, advanced them to `1,1`, and consumed the first default CRT `rand()` low byte `0x29`. The next read then consumed sequence `0x81` and random byte `0x23`.

For the OTA path, the native writer prepends the logical CustomData length byte. A 20-byte CustomData object therefore becomes a 21-byte writer stream and is split as 15 + 6 bytes. The builder receives frame lengths 20 and 11 respectively.

If the writer starts that operation with `E670=7` (consistent with the verified `0x37` captures), and no intervening `rand()` occurs between the preceding read and write, the two OTA write headers are:

```text
fragment 0: 3B F4 81 23
fragment 1: 3B CB 0A BE
```

This is still conditional only on the remaining two state questions: proving the OTA-time starting value of `E670`, and proving there is no hidden `rand()` call in the receive/managed gap.


### Managed transport handoff narrowed

`WriteDeviceCustomDataAsync` is confirmed to allocate operation/result type `29`, call native `writeDeviceCustomData(data, data.Length)`, and hand the returned encoded buffer to `GetResultAsync` with a 10-second timeout. `GetDeviceCustomDataAsync` analogously uses operation/result type `28`. The exact managed transport methods present immediately downstream are `SendDeviceData`, `SendNextPacketOrFail`, and `SendDeviceDataAsync`; `OnReport` is the corresponding receive/ACK entry point. These four method bodies are the next target required to close the final HID envelope.

Raw two-byte searches for `80 F9` / `81 F9` are too collision-prone and are no longer treated as protocol evidence.


### Managed HID envelope statically closed

Direct IL now proves the host output report as `80 F9 <seq> 00 <len> <payload>`. `SendDeviceData` computes `n = min(native_length, 20)`, allocates `n+5`, fills those exact five header bytes, copies the first `n` native bytes at offset 5, and calls `HidLibrary.HidDevice.Write(byte[])`.

`OnReport` accepts report ID `0x81` and data marker `0xF9`. The generic response layout is `81 F9 <seq> <flags> <len> <payload>`; bit 0 of `flags` selects the ACK path and resolves the matching `AckMap` entry as success, while a clear bit routes `Data[4:]` of length `Data[3]` into the normal receive path.

Sequence generation is also resolved: an 8-bit counter is incremented under a lock, zero is skipped, and the range cycles 1..255.

Remaining work is no longer the HID envelope. It is limited to expanding the async state-machine bodies for `GetResultAsync` and `SendDeviceDataAsync` to document segmentation/retry/timing behavior completely and then mapping the already-known CustomData OTA mutation to the final native payload bytes.


The 20-byte cap is statically tied to `Math.Min(source_length, 20)`. The two `GetPacketCount` overloads compute ceiling division by 20, confirming that longer native results are handled as 20-byte transport units rather than one oversized HID payload.


Correction: values 28/29 are not task IDs. `CreateTaskCompletionSource` stores the supplied value in `SeqMap[seq]`; they are internal operation/result type values associated with a sequence. The actual object-return completion source is stored in `ResultMap`, while write acknowledgements are tracked separately in `AckMap`.

### Managed async HID transport fully reconstructed

The full async state machines in `Machenik.Infrastructure.dll` now close segmentation, sequence migration, retry, ACK, and timeout behavior.

`SendDeviceDataAsync`:
- records the initial `SeqTaskSource.Seq` as `lastSeq`;
- computes packet count as `ceil(native_length / 20)`;
- slices the native buffer in chunks of at most 20 bytes;
- uses the original sequence for fragment 0;
- allocates a fresh sequence for every later fragment;
- migrates `ResultMap` and `SeqMap` to the new sequence and resets the fragment index bookkeeping;
- builds each outer HID report as `80 F9 <seq> 00 <len> <native-bytes>`.

For every non-final 20-byte transport fragment:
- an entry is installed in `AckMap[currentSeq]`;
- the report is written through `HidDevice.Write`;
- the host waits for either ACK completion or a 5-second `Task.Delay`;
- timeout triggers another send;
- the retry loop permits three writes total (`retry = 0, 1, 2`);
- failure after the retry limit returns `false`.

The final transport fragment is written once and does not wait in this intermediate ACK loop. Completion of the overall operation is driven by the normal result path.

`CheckTaskTimeoutAsync` races the result task against the caller-supplied timeout. On timeout it removes the sequence from `ResultMap`, `SeqMap`, `IndexMap`, `LengthMap`, `DataMap`, and `AckMap`, then returns `true`. `GetResultAsync` turns that `true` into `TimeoutException`.

`GetNextSeq` increments an 8-bit counter under `Monitor` and skips zero, giving sequence values 1..255.

This means the outer managed HID transport is now closed. The next OTA-specific target is the exact `GetDeviceCustomDataAsync` and `WriteDeviceCustomDataAsync` state-machine handoff into native `readDeviceCustomData` / `writeDeviceCustomData`, so the already-known `data[11] |= 0x80` mutation can be followed byte-for-byte through the final two native 0x3B fragments.

### Managed CustomData dispatch closed

The dedicated CustomData async state machines and dispatcher cases are now statically identified.

Read path:
```text
CreateTaskCompletionSource(28)
  -> readDeviceCustomData(0)
  -> GetResultAsync(..., caller timeout)
  -> result cast to byte[]
```

Write path:
```text
CreateTaskCompletionSource(29)
  -> writeDeviceCustomData(data, data.Length)
  -> GetResultAsync(..., 10 seconds)
  -> result unboxed as bool
```

All three native imports (`readDeviceCustomData`, `receiveDeviceCustomData`, `writeDeviceCustomData`) are cdecl imports from `Libs\Gamepad\KeyLinkerlib.dll`.

Dispatcher operation type 28 first reassembles all managed HID transport chunks through `CheckPacketIndex` / `DataMap`. Only when the last managed chunk has arrived does it call:

```text
receiveDeviceCustomData(reassembled_bytes, reassembled_count)
```

The returned `DeviceDataStruct` is rejected if its pointer is null, length <= 0, or length > 100; that error path completes the pending result with null.

Operation type 29 does not call `receiveDeviceCustomData`. Once its normal response reaches the dispatcher, it removes the pending maps and completes the result with boolean `true`.

The formerly missing valid branch of operation type 28 has now been recovered. The next section records the closed managed copy/reassembly behavior and supersedes this earlier extraction limitation.

### CustomData managed layer closed; OTA read consumes two native request states

The previously missing valid branch of operation type 28 is now statically proven. After `receiveDeviceCustomData` returns a valid `DeviceDataStruct` (`data != NULL`, `0 < length <= 100`), managed code allocates `new byte[length]`, executes `Marshal.Copy(native.data, result, 0, native.length)`, removes the pending ResultMap/SeqMap entries, and completes the task with that byte array. Therefore the array consumed by the OTA MainViewModel is a direct byte-for-byte copy of the native decoder output.

A second, more important consequence is now proven by the full case-28 branch. If the native encoded response spans more than one 20-byte managed HID transport payload, the dispatcher increments its packet index, migrates the managed sequence, calls `readDeviceCustomData(index + 1)`, and sends that request before completing the read. A 20-byte logical CustomData object produces 31 encoded native bytes (20-byte first frame + 11-byte second frame), so the official managed read necessarily performs two native read requests:

```text
selector 0 -> first native response chunk
selector 1 -> second native response chunk
```

Each `readDeviceCustomData` call executes native helper `0x1800014C0` and one `rand()`. For the observed fresh/default sequence this advances:

```text
request 0: SEQ=00 RAND=29 selector=00
request 1: SEQ=81 RAND=23 selector=01
writer start state: E67C=0, E68C=2
next rand low byte: BE
```

The exact second read plaintext request is `BB 06 81 23 01 B9`; its KeyLinker-encoded form is `C2 37 F2 B7 A4 28`.

This supersedes the earlier one-read writer candidates. An intermediate calculation with `E670=7` would produce `3B F4 02 BE / 3B CB 8B 84`, but that is **not** the cold default. The subsequent PE/xref audit below proves cold `E670=0`, giving `3B 14 02 BE / 3B EB 8B 84`. Warm-session values remain dependent on prior process-global write traffic and any unrelated `rand()` consumption.



### Cold E670 state corrected

Static PE/xref analysis shows the protocol globals at `0x18000E670+` are not file-initialized and start at zero in a cold DLL state. No initializer writes `7` to `E670`; confirmed writes only perform the normal post-frame decrement with zero wrapping to seven.

After the two native CustomData reads used by the OTA path, a cold writer therefore enters with `E670=0, E67C=0, E68C=2`. With the default observed CRT `rand()` progression, the cold 0x3B frame headers are:

```text
3B 14 02 BE
3B EB 8B 84
```

The older pair `3B F4 02 BE / 3B CB 8B 84` is only valid if prior write-style traffic already left `E670=7`. `E670` is runtime process-global phase, not an OTA constant.


### E670 write-frame phase

Static xrefs show `E670` is decremented once per native write frame, with zero wrapping to seven. Therefore `E670 = (-W) mod 8` from a cold DLL state, where `W` is the number of emitted write frames. The cold OTA headers remain valid for a fresh process, but a normal MixDrive session may reach OTA with a different `E670` phase if earlier device writes occurred. Exact writer export names for six non-CustomData paths are the next mapping target.


### Exact E670 export mapping

PE exports map all seven E670-changing writers: `writeRockerData`, `writeTriggerData`, `writeDeviceMotorData`, `writeDeviceKeyChangeData`, `writeDeviceLamplightData`, `writeDeviceTurboData`, and `writeDeviceCustomData`. No E670 write was found in the focused audit of `writeDeviceMacroData` or `writeDeviceResetFactory`. This closes the identity of every known E670 writer.


### Cold-start KeyLinker state proven

The PE loader zero-fills the protocol-state tail of `.data` (`VirtualSize=0x6A8`, `RawSize=0x200`), proving cold-start values `E670=E67C=E68C=0` and likewise zero for the neighboring protocol globals through `E68C`. Native write command IDs are now mapped through CustomData: `0x31` Rocker, `0x32` Trigger, `0x33` Motor, `0x34` Turbo, `0x36` KeyChange, `0x37` Lamplight, `0x3B` CustomData.


### Official updater SCSI command flow closed

Static call-graph analysis of the official v1.48 updater now closes the DeviceUsb transfer roles:

- `F1`: `DataIn=1`, 20-byte read; response bytes 0..15 are compared with the package hardware ID and bytes 16..17 are parsed as a little-endian firmware version.
- `F0`: `DataIn=0`, firmware data write in blocks of up to 512 bytes.
- `F2`: `DataIn=1`, 20-byte status read; the updater requires `status[0] == 0`.

The v1.48 image is `0x5A400` bytes, so the official loop performs exactly 722 F0 transfers of 512 bytes.

F2 is read after the first F0 block and again after the complete transfer.

All three calls pass `cdbLength=10`. However, the updater visibly initializes only the first six bytes of each local CDB (`F0/F1/F2 00 00 00 00 00`). The remaining four bytes are not yet proven to be zero and appear to overlap stack padding/adjacent locals. Therefore only the opcode and the explicitly initialized leading bytes are treated as statically exact.

Full reconstruction: [UPDATER-SCSI.md](UPDATER-SCSI.md).


### Update-mode removable-drive identity closed

The updater's discovery worker at `0x00402A70` scans `GetLogicalDrives()`, filters `GetDriveTypeW(...) == 2` (`DRIVE_REMOVABLE`), opens each candidate with `deviceUsb_OpenByDrv`, reads `deviceUsb_GetInfo`, closes the handle, and accepts only the exact ASCII signature:

```text
Gamepad Updater         1.00
```

The matching drive index (A=0..Z=25) is stored globally; no match is represented by `0xFF`. The scan repeats every 200 ms.

The signature is exactly 28 characters and decomposes as 8 + 16 + 4 bytes, strongly matching standard SCSI INQUIRY Vendor/Product/Revision widths. Static confirmation inside `deviceUsb_GetInfo` is the next target.


### Bootloader identity source confirmed in DeviceUsb

`DeviceUsb.dll!deviceUsb_GetInfo` uses `DeviceIoControl(..., 0x002D1400, ...)` = `IOCTL_STORAGE_QUERY_PROPERTY`, rejects descriptors whose `STORAGE_DEVICE_DESCRIPTOR.BusType` is not `BusTypeUsb (7)`, and reads `VendorIdOffset`, `ProductIdOffset`, and `ProductRevisionOffset`.

This confirms the update-device identity fields:

```text
Vendor   = "Gamepad "
Product  = "Updater         "
Revision = "1.00"
```

The firmware request path remains separate: `deviceUsb_Request` uses `0x0004D014` = `IOCTL_SCSI_PASS_THROUGH_DIRECT` for F0/F1/F2.


### DeviceUsb request ABI and SPTD layout closed

`deviceUsb_Request` is now reconstructed as a six-argument wrapper over `IOCTL_SCSI_PASS_THROUGH_DIRECT (0x0004D014)`:

```text
(handle, data_in, cdb, cdb_len, data_buffer, data_len)
```

The updater uses data_in=0 for F0 writes and data_in=1 for F1/F2 reads. The x86 SPTD is length 0x2C, uses TargetId=1, Lun=0, SenseInfoLength=0x20, TimeOutValue=5, SenseInfoOffset=0x30, and a 0x50-byte in/out control buffer. The wrapper retries failed IOCTLs up to 10 attempts with 20 ms delays, except Win32 error 0x37 which aborts immediately.

`deviceUsb_GetInfo` string construction is also closed: IAT 0x10002090 is `strcpy_s` and 0x1000208C is `strcat_s`, proving Vendor + Product + Revision concatenation.


### Official updater bug confirmed: trailing CDB bytes contain stack residue

The F0/F1/F2 command buffers are passed with `cdb_len=10` but only bytes 0..5 are explicitly initialized. Bytes 6..9 are stack residue. For F2 specifically, bytes 8..9 overlap the function security cookie at `[ebp-0x10]`. The official updater therefore sends non-semantic stack data in the declared CDB.

Project policy: do not reproduce this bug. The official application is evidence of what the device tolerates, not a normative protocol specification. Use deterministic zero-filled 10-byte CDBs and validate the normalization with read-only F1/F2 before any F0 write.


### Controlled update-mode transition probe prepared

A Linux probe is now available at `tools/g3v2-enter-updater-linux.py`. It is fail-closed and dynamically selects only the G3 V2 normal USB personality `413D:2131` MI_01 (`:1.1`). It validates the two known BB read vectors, reads and reconstructs the complete 20-byte CustomData object, validates both inner CRCs, changes only `CustomData[11] |= 0x80`, and requires an explicit `--execute` flag before sending the two 0x3B write frames.

The probe does not issue any SCSI command. Its active boundary is only the normal-HID -> update-personality transition and post-transition USB/block enumeration.

Recovery evidence is not yet G3-V2-specific. A closely related Manba One V2 device using the same `Gamepad / Updater / 1.00` storage identity and F0/F1/F2 family is documented to leave update mode after unplug/replug. This is treated only as family corroboration until observed directly on the G3 V2.

### First live transition attempt aborted safely; probe corrected

The first Linux `--execute` transition attempt did **not** reach the OTA write. The controller answered the initial selector-0 request with a six-byte native response that decodes to `19 06 00 24 00 B2`, i.e. command `0x19` with zero logical payload. The probe aborted on its old hard-coded expectation before changing CustomData.

The earlier successful live CustomData path used two `ZXPC_APP` identity pings first, then selector 0 at outer sequence 3, yielding a 16-byte native response with 10 logical CustomData bytes. The probe is now corrected to reproduce those pings, parse command `0x19` by its declared length, read selectors 0 and 1 at outer sequences 3 and 4, and combine two 10-byte logical segments into the 20-byte CustomData object.

Next step: run the corrected probe **without `--execute` first**. No SCSI F0/F1/F2 is involved.

### Live CustomData length contradiction: write remains blocked

A second corrected dry-run reproduced the known-good outer sequence (drain -> two `ZXPC_APP` pings -> selector 0 at outer seq 3) but the controller returned:

```text
wire : B4 7B 0E 42 F1 DB
plain: 19 06 00 58 00 4A
```

The frame is CRC-valid and declares zero logical payload. The probe aborted before any OTA write.

This conflicts with an earlier successful live A/B read, where selector 0 returned a 16-byte native frame decoding to:

```text
19 10 00 6B 0A 00 01 00 00 00 00 40 04 00 02 28
```

and selector 1 returned another 10-byte logical payload. Therefore the repository must not treat a 20-byte logical CustomData object as universally proven from live behavior. The static `MainViewModel` access to `data[11]`, the native receive deframing, and the managed continuation logic still need to be reconciled with the live 10-byte and zero-byte responses before `--execute` is used again.

Immediate policy: no OTA write while the read path does not reproduce a non-empty known-good response.

### Power-cycle does not restore non-empty CustomData response

A full controller power-cycle followed by the same known-good outer sequence still returned a CRC-valid empty selector-0 response:

```text
wire : 8D D2 63 51 F6 5F
plain: 19 06 00 0A 00 EE
```

The device remained in the normal `413D:2131` personality and both `ZXPC_APP` pings succeeded. This rules out a simple stale-HID or warm-session explanation for the empty `0x19` response.

Live OTA writes remain blocked. The next step is static only: recover the exact managed continuation predicate in `CheckPacketIndex` / `GetPacketCount` / `MigrateSeq` / `HandleGenericDeviceData`. A read-only helper is available as `tools/dump-gamepad-manager-il.py`.

### Managed continuation criterion recovered: selector 1 is conditional on receiveDataLength

Static CIL of `GamepadDeviceManager.CheckPacketIndex` closes the packet-count logic.

For the first packet of an operation (`IndexMap[seq] == 0`), managed code executes:

```text
total = receiveDataLength(payload, payload_length, ...)
packet_count = (total + 20 - 1) / 20
LengthMap[seq] = packet_count
```

The received outer payload bytes are appended to `DataMap[seq]`, and `CheckPacketIndex` returns the current packet index.

Each multi-packet handler then compares:

```text
current_index == LengthMap[seq] - 1
```

If true, it finalizes/reassembles/decodes the accumulated data. If false, it increments the index, calls `MigrateSeq`, generates the next native read request with `index + 1`, and sends it through `SendNextPacketOrFail`.

Therefore selector 1 is **not unconditional**. It is requested only when native `receiveDataLength` says the current logical/native response requires more than one 20-byte outer HID packet.

`MigrateSeq` itself does not reinterpret data; it obtains a fresh sequence and moves the existing `ResultMap`, `SeqMap`, `IndexMap`, `LengthMap`, and `DataMap` entries to that new sequence.

Critical remaining discriminator: reconstruct native `receiveDataLength`. Its return value determines whether the observed 6-byte and 16-byte `0x19` responses are considered complete or require a subsequent selector.

### CustomData case 28 fully recovered

The exact operation-28 branch in `HandleGenericDeviceData` is now recovered.

It:
1. calls `CheckPacketIndex`;
2. compares `current_index` with `LengthMap[seq] - 1`;
3. if final, removes `LengthMap` / `IndexMap` / `DataMap`, calls native `receiveDeviceCustomData` on the accumulated raw bytes, validates `data != NULL` and `0 < length <= 100`, copies the native result with `Marshal.Copy`, and resolves the pending task;
4. if not final, increments `IndexMap`, migrates all pending state to a fresh outer HID sequence, calls `readDeviceCustomData(current_index + 1)`, and sends it via `SendNextPacketOrFail`.

The remaining native length ambiguity is now closed by `receiveDataLength` and `OnReport`. Normal generic HID responses call `HandleGenericDeviceData(..., type=0)`, so the ordinary native length formula applies.

For the current CRC-valid frame `19 06 ... 00 ...`, `receiveDataLength(type=0)` returns 6 native bytes. Managed code therefore stores `LengthMap=1`, treats selector 0 as the final packet, calls `receiveDeviceCustomData`, receives a logical length of 0, and resolves `GetDeviceCustomDataAsync` with `null`. `MainViewModel` then skips the OTA mutation entirely.

For the earlier captured 16-byte response declaring 10 logical bytes, `receiveDataLength(type=0)` returns 16, so it is also a one-packet result. `receiveDeviceCustomData` returns a 10-byte array. The subsequent official `data[11]` access is therefore out of range for that captured device state, constituting a second vendor-application bug path.

### Native receiveDataLength formula recovered

`Machenik.Infrastructure.Standard.GamepadInterop.receiveDataLength` is a P/Invoke to `KeyLinkerlib.dll!receiveDataLength` at RVA `0x4A20`.

The native function first KeyLinker-decodes the received native frame and reads decoded byte offset 4. For the ordinary branch where the third native argument is not 1, the returned total native response size is:

```text
logical_len = decoded[4]
total_native_len = logical_len + 6 + 5 * floor(logical_len / 15)
```

This matches all known framing cases exactly:
- logical length 0 -> 6 native bytes;
- logical length 10 -> 16 native bytes;
- logical length 20 -> 31 native bytes.

This formula is the inverse size model of the native CustomData framing: one 4-byte header, one logical-length byte in the first frame, one CRC per frame, and an additional 5 bytes for every extra 15-byte logical chunk.

The function has a distinct branch when its third native argument equals 1. Before applying the ordinary formula to the CustomData path, the managed caller argument feeding that third parameter must be identified exactly. The focused IL dumper now includes `OnReport` and method parameter metadata for that purpose.

### OnReport proves CustomData uses receiveDataLength type 0

The normal HID report path parses `seq` from report data byte 1, flags from byte 2, payload length from byte 3, extracts the payload from byte 4 onward, and calls:

```text
HandleGenericDeviceData(seq, segment, dataLength, 0)
```

This exactly matches the observed Linux raw reports after HidLibrary's report-ID separation. Therefore CustomData responses use the ordinary `receiveDataLength(..., type=0)` branch.

Consequences:
- decoded logical length 0 -> native total 6 -> one outer packet -> native CustomData length 0 -> managed result `null` -> no OTA write;
- decoded logical length 10 -> native total 16 -> one outer packet -> managed `byte[10]` -> official `data[11]` access is invalid;
- decoded logical length 20 -> native total 31 -> two outer packets/selectors -> managed `byte[20]`, which is the first size compatible with the official OTA mutation at index 11 and with the already reconstructed two-fragment writer.

### Firmware-update sender gate: DeviceType == 2

Static xrefs in `Machenik.exe` show that the firmware-update worker sends `EnterUpdateModeMessage` only if `FirmwareUpdateModel.Device.DeviceType == 2`. It then launches the external updater executable and waits for it to exit.

The current live setup uses the controller over USB only; the 2.4 GHz dongle is disconnected. The semantic meaning of enum value `2` must therefore be resolved before concluding whether the official OTA path expects wired USB, receiver/2.4G, Bluetooth, or another device classification. `tools/dump-device-type.py` scans extracted managed assemblies for the enum definition and nearby `get_DeviceType` comparisons.

### DeviceType == 2 resolved: Gamepad, not transport mode

`Machenik.Shared.Models.Devices.DeviceType` is:

```text
Unknown  = 0
Keyboard = 1
Gamepad  = 2
Mouse    = 3
```

Therefore the firmware-update gate `DeviceType == 2` means only “this device is a gamepad.” It does **not** distinguish wired USB from 2.4 GHz receiver or Bluetooth.

The current live setup remains USB-only with the 2.4 GHz dongle disconnected, but transport-specific behavior must be encoded elsewhere (another property, model subtype, HID identity, connection state, or device metadata). The next static target is to inventory connection/transport-related members and their use in the gamepad firmware-update path.

