# Current status and roadmap

> **STATUS ATUAL (2026-11-20): BLE KeyLinker G3V2 CONFIRMADO.** Conexão LE Public direta, notificações e2, identidade 1006:2011 / firmware 1.48 e leitura B7 de seis bytes validadas fisicamente. Ver **[resumo BLE confirmado](G3V2-BLE-VERIFIED.md)**. Testes antigos de falha BLE são históricos. O comando de escrita 0x37 ainda NÃO está liberado para Ambilight contínuo; persistência/flash não resolvida.


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

The remaining four bytes are the controller's **logical gamepad identity**:
- `Item3 << 8 | Item4 = 0x1006`
- `Item5 << 8 | Item6 = 0x2011`

This is now proven by `<LoadGamepadAsync>d__27::MoveNext`: after
`ConnectDeviceAsync(3)`, MixDrive constructs the firmware version from
`Item1/Item2`, constructs the two 16-bit IDs from `Item3..Item6`, and calls
`GamepadDeviceHelper.FindGamepadInfo(0x1006, 0x2011)`.

This closes an apparent identity mismatch. The attached controller has two
identity layers:

```text
physical HID enumeration : 413D:2131
ZXPC_APP logical identity: 1006:2011
logical model            : G3V2
firmware                  : 1.48
```

`GamepadKeyTestMapping.GetMappingByVidPid` explicitly maps both
`1006:2011` and `3973:7001` to the G3V2 mapping. The static
`Gamepad.SupportedList` also contains a G3V2 record at `3973:7001`, while
the physical `413D:2131` pair appears in a different initial catalog record.
That is not a contradiction: `LoadGamepadAsync` first opens/matches the
physical HID device, then re-resolves the gamepad information from the
six-byte `ZXPC_APP` tuple and attaches that logical `GamepadInfo` while
retaining the same physical `HidDevice`.

### USB vs. 2.4 GHz transport gate

The firmware-update gate `DeviceType == 2` means only `Gamepad`
(`Unknown=0, Keyboard=1, Gamepad=2, Mouse=3`). `DeviceMode` is also not a
transport selector; it is a model string such as `G3V2`, `G60`, or
`G5Pro V2`.

No managed transport gate has been found before `EnterUpdateModeMessage`.
The normal loader and the updater transition both keep using the same
`HidDevice` that was opened from the physical HID enumeration. The current
test configuration is therefore valid as USB-only; the disconnected 2.4 GHz
dongle is not required by any proven managed condition.

This does not prove that the device firmware behaves identically on every
transport. It does remove the earlier hypothesis that MixDrive's managed
update path explicitly requires the receiver.

### Normal hardware initialization and warm-session CustomData

The current zero-length CustomData response must not be treated as evidence
that USB is the wrong transport. MixDrive normally reaches firmware update
with a previously initialized `GamepadDeviceManager`.

The managed initialization order is now statically closed. The normal
`InitializeFromHardwareAsync` path awaits the hardware loaders in this order:

```text
LoadKeyMapAsync
-> LoadTurboAsync
-> LoadRockerAsync
-> LoadTriggerAsync
-> LoadMotorAsync
-> LoadLightAsync
-> LoadCustomDataAsync
```

The per-loader manager calls are also closed:

```text
KeyMap : GetDeviceBeforeKeyChangeListAsync
Turbo  : GetDeviceSupportTurboAsync -> GetDeviceCurrentTurboAsync
Rocker : GetDeviceRockerAsync -> GetDeviceCustomDataAsync
Trigger: GetDeviceTriggerAsync
Motor  : GetDeviceMotorAsync -> GetDeviceCustomDataAsync
Light  : GetDeviceLamplightAsync -> GetDeviceCustomDataAsync
```

Important methodological note: `dump-gamepad-hardware-loader-sequence.py`
prints its sections in a fixed target order, so that script alone does not
establish the global order. The global order above comes from the independent
`InitializeFromHardwareAsync` state-machine audit.

`LoadRockerAsync` explicitly rejects CustomData unless the returned logical
payload has exactly **20 bytes**. The next static target is no longer the
loader order; it is the manager-to-native bridge for each read above so the
Linux read-only warm-up can reproduce the exact KeyLinker commands and
arguments before retrying the final CustomData read.

OTA writes remain blocked while the cold/partial warm-up resolves CustomData
to null/zero length.

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

A direct scan of the exact carved 0x5A400-byte FOT supersedes the earlier
XCOD/XRES wording. The carved payload has total Shannon entropy **7.9968
bits/byte**, almost every 4 KiB block is ~7.95 bits/byte, contains no XCOD,
XRES, ZXPC, G3V2 or MACHENIKE marker, and yields no credible Cortex-M vector
table. Apparent Thumb CMP-immediate clusters occur at the rate expected from
near-random bytes and are not treated as code.

Therefore the updater-carried FOT is currently classified as an
**encrypted/compressed/otherwise transformed firmware package**, not plaintext
ARM/Thumb firmware. The earlier XCOD/XRES observation came from a wider updater
layout/region and must not be attributed to the carved FOT itself.

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

### Official warm-up order recovered

Static CIL of `GamepadConfigInitializer.InitializeFromHardwareAsync` now gives
the official startup order:

```text
LoadKeyMapAsync
LoadTurboAsync
LoadRockerAsync
LoadTriggerAsync
LoadMotorAsync
LoadLightAsync
LoadCustomDataAsync
```

The focused xref audit found three startup/load call sites of
`GetDeviceCustomDataAsync` and **zero** `WriteDeviceCustomDataAsync` call
sites inside load/initialize methods.

This is important but does not yet prove that the entire warm-up is passive.
The loaders call other `GamepadDeviceManager.GetDevice*Async` methods before
the final CustomData read. Confirmed examples include:

```text
GetDeviceBeforeKeyChangeListAsync
GetDeviceSupportTurboAsync
GetDeviceRockerAsync
GetDeviceTriggerAsync
GetDeviceMotorAsync
```

Those operations are semantically reads, but they can still advance the
KeyLinker request state (`E67C/E68C`) and CRT `rand()` position. A cold
standalone CustomData request therefore does not yet reproduce the state of
MixDrive's cached manager.

The next static step is to map every warm-up `GetDevice*Async` call to its
native request builder/operation type before sending any live warm-up traffic.

### Warm-up audit scanner scope correction

The first version of `dump-gamepad-warmup-manager-map.py` scanned only
`Machenik.exe`. That produced an incomplete result: it saw only the loader
state machines compiled into the EXE and reported zero manager async state
machines.

The large earlier metadata scan proves the split explicitly:

```text
Machenik.Shared.dll
  GamepadHardwareLoader loader state machines
  e.g. <LoadRockerAsync>d__13, <LoadMotorAsync>d__11, <LoadTurboAsync>d__8

Machenik.Infrastructure.dll
  GamepadDeviceManager async state machines
  e.g. <GetDeviceRockerAsync>d__33, <GetDeviceTriggerAsync>d__35,
       <GetDeviceSupportTurboAsync>d__37
```

Therefore the zero-state-machine result was a tooling scope bug, not protocol
evidence. The scanner now cross-correlates `Machenik.exe`,
`Machenik.Shared.dll`, and `Machenik.Infrastructure.dll`.

### Warm-up manager map closed: 11 read operations in the initializer path

The corrected multi-assembly audit separates the actual
`GamepadConfigInitializer -> IGamepadHardwareLoader` path from unrelated
ViewModel-specific `Load*` methods in `Machenik.exe`.

The concrete initializer uses `GamepadHardwareLoader` from
`Machenik.Shared.dll`. Its read sequence is:

```text
1.  LoadKeyMapAsync
    -> GetDeviceBeforeKeyChangeListAsync
    -> native readDeviceBeforeKeyChangeList
    -> operation 9

2.  LoadTurboAsync
    -> GetDeviceSupportTurboAsync
    -> native readDeviceSupportTurboList
    -> operation 16
    -> GetDeviceCurrentTurboAsync
    -> native readDeviceTurboData
    -> operation 17

3.  LoadRockerAsync
    -> GetDeviceRockerAsync
    -> native readRockerData
    -> operation 12
    -> GetDeviceCustomDataAsync
    -> native readDeviceCustomData
    -> operation 28

4.  LoadTriggerAsync
    -> GetDeviceTriggerAsync
    -> native readTriggerData
    -> operation 14

5.  LoadMotorAsync
    -> GetDeviceMotorAsync
    -> native readDeviceMotorData
    -> operation 19
    -> GetDeviceCustomDataAsync
    -> native readDeviceCustomData
    -> operation 28

6.  LoadLightAsync
    -> GetDeviceLamplightAsync
    -> native readDeviceLamplightData
    -> operation 21
    -> GetDeviceCustomDataAsync
    -> native readDeviceCustomData
    -> operation 28

7.  GamepadConfigInitializer.LoadCustomDataAsync
    -> GetDeviceCustomDataAsync
    -> native readDeviceCustomData
    -> operation 28
```

This is a **baseline of 11 high-level read operations**, including **four CustomData reads**, before initialization completes on the normal success path. The real number of native requests can be higher if a loader retries or if a response requires managed continuation packets.

The CustomData calls inside rocker, motor, and light loaders are reached
directly after their primary reads in the compiled loader state machines; no
CustomData write is involved.

The separate EXE path that calls `GetDeviceAfterKeyChangeListAsync`
(operation 10) belongs to a ViewModel `LoadKeyMapAsync` and is not part of
the concrete `GamepadHardwareLoader` sequence used by
`GamepadConfigInitializer`.

The next blocker is now purely native: determine whether each of these read
builders advances the same KeyLinker state / CRT `rand()` stream used by
`readDeviceCustomData`. If so, the cold Linux probe's first CustomData
request is necessarily in a different state than MixDrive's fourth/final
CustomData request.

### Native warm-up state correlation: operations 3-11 share the CustomData KeyLinker path

The focused native audit proves that the following initializer reads all call the
same three primitives used by `readDeviceCustomData`:

```text
readDeviceTurboData
readRockerData
readTriggerData
readDeviceMotorData
readDeviceLamplightData
readDeviceCustomData
```

Each directly calls:

```text
0x1800014C0  KeyLinker state helper
0x18000B150  CRT rand() IAT
0x180001080  KeyLinker encoder
```

Therefore, in the 11-operation initializer baseline, operations 3 through 11
are definitely stateful KeyLinker requests. The final CustomData read is not
equivalent to a cold first CustomData request: even ignoring the first two
operations, eight prior stateful requests have already advanced the shared
state/rand stream before the final CustomData builder runs.

The first two operations remain unresolved:

```text
readDeviceBeforeKeyChangeList -> helper 0x180001BE0 with arg 3
readDeviceSupportTurboList    -> helper 0x180001BE0 with arg 4
```

They do not reference the state/rand/encoder primitives directly in their tiny
export wrappers. The next static target is helper `0x180001BE0` itself. If it
also reaches the same primitives, then all 11 baseline reads share the same
state machine and the final CustomData occurs after ten earlier stateful
requests.



### KeyLinker state-byte cycle implication

For the known cold single-frame sequence, the state byte generated by
`0x1800014C0` follows the observed 8-call pattern
`00,81,02,83,04,85,06,87` before wrapping. Therefore, if the first two
initializer helpers do not consume that state machine, operation 11 (the final
CustomData read) can again carry state byte `0x00`, even though eight earlier
stateful requests have already consumed the shared CRT `rand()` stream.

This means matching the visible state byte alone does not reproduce the
official request context. The random byte position and any other shared helper
state must also be reproduced or understood.

### All 11 baseline warm-up reads share the same KeyLinker state/rand stream

Native helper `0x180001BE0`, used by
`readDeviceBeforeKeyChangeList` and `readDeviceSupportTurboList`, is now
closed. It directly calls:

```text
0x1800014C0  KeyLinkerState
0x18000B150  CRT rand()
0x180001080  KeyLinkerEncode
```

The helper returns at `0x180001C87`; the later second `rand()` seen at
`0x180001EE3` belongs to the separate function beginning at
`0x180001C90` and is not part of either generic-list read request.

Therefore **all eleven baseline initializer reads consume exactly one
KeyLinker state value and one CRT rand value per native request**.

With a cold vendor-DLL process, no retries, and no continuation packets, the
baseline state/rand sequence is:

```text
#   operation             state  rand(low8)
1   BeforeKeyChangeList   00     29
2   SupportTurbo          81     23
3   CurrentTurbo          02     BE
4   Rocker                83     84
5   CustomData #1         04     E1
6   Trigger               85     6C
7   Motor                 06     D6
8   CustomData #2         87     AE
9   Lamplight             00     52
10  CustomData #3         81     90
11  CustomData #4/final   02     49
```

This table is only a baseline. Any retry or managed continuation inserts
another state/rand consumption and shifts every later row.

The final baseline CustomData request is therefore:

```text
plain: BB 06 02 49 00 D4
wire : EE 95 26 90 B7 65
```

It must **not** be sent in isolation, because the device has not then observed
the ten preceding stateful read requests.

`tools/g3v2-mixdrive-warmup-readonly.py` reproduces the bounded initializer
read sequence and dynamically consumes extra state/rand entries for proven
continuation-capable builders. By default it only prints the plan; live HID
read requests require explicit `--run-readonly`. The script contains no
CustomData writer, no OTA-bit mutation, and no SCSI path.

### Read-only official warm-up reproduced live: CustomData stays empty

The bounded Linux warm-up reproduced the concrete 11-read
`GamepadConfigInitializer` baseline over USB-only and received valid,
CRC-correct responses for every non-CustomData operation:

```text
BeforeKeyChangeList  logical 9
SupportTurbo         logical 10
CurrentTurbo         logical 13
Rocker               logical 14
Trigger              logical 14
Motor                logical 8
Lamplight            logical 6
```

All four CustomData reads returned a valid six-byte native response with
logical length zero:

```text
CustomData #1 -> 0 bytes
CustomData #2 -> 0 bytes
CustomData #3 -> 0 bytes
CustomData #4 -> 0 bytes
```

No continuation packet was requested, so the live run consumed exactly the
11 baseline KeyLinker requests. The final request was the predicted cold
baseline `BB 06 02 49 00 D4`.

This disproves the hypothesis that the current empty CustomData is caused only
by skipping MixDrive's warm-up/read sequence. The normal transport, codec,
state/rand progression, and unrelated read paths are all working.

Managed loader semantics also show that CustomData is optional state:
`LoadRockerAsync` only consumes it when `data != null && data.Length == 20`;
otherwise it skips the extended fields. `LoadLightAsync` and
`LoadMotorAsync` also branch away when the CustomData result is null.

The next target is therefore the set of **CustomData write call sites**:
identify which G3V2 actions create/populate a 20-byte block, and whether the
earlier successful 10-byte A/B read occurred after one of those actions.
Live writes remain frozen until this is understood.



### G3V2 CustomData field map from normal hardware loaders

The normal hardware-loader CIL gives a direct semantic map for several bytes
of the 20-byte logical CustomData object:

```text
data[0]      = LightBrightness (0..100; invalid values fall back to 2)
data[1]      = LightModeIndex (G3V2 accepts 0..7; invalid values fall back to 0)
data[2]      = RockerCursorType
data[3] & 0F = left rocker adsorption strength (G3V2 branch)
data[3] >> 4 = right rocker adsorption strength (G3V2 branch)
data[4]      = MotorGripMode encoding
data[7] bit3 = RockerExchange
data[8]      = RockerReturnRate (G3V2 branch)
data[10]     = RockerResolution (G3V2 branch)
data[12]     = left rocker outer diameter
data[13]     = right rocker outer diameter
```

The motor loader decodes `data[4]` as:

```text
raw 0 or 1 -> MotorGripMode 0
raw 2      -> MotorGripMode 1
raw 3      -> MotorGripMode 2
other      -> leave unchanged
```

Most importantly for the Ambilight goal, `LoadLightAsync` obtains the actual
R/G/B color, lamp brightness, breath speed and lamp count from
`GetDeviceLamplightAsync` / `DeviceLampLightStruct` first. Its subsequent
CustomData read only overlays the logical light mode and UI brightness (plus
the shared configuration bytes consumed by other loaders).

This strengthens the protocol split:

```text
LampLight path   = actual RGB/effect light structure
CustomData / BB  = shared configuration, including light mode/brightness
```

Accordingly, the next Ambilight-specific reverse-engineering target is the
LampLight **write/apply** path and whether it has a volatile/non-NVM variant
suitable for high-rate color updates.

A new static tool, `tools/dump-gamepad-manager-read-paths.py`, maps the
normal manager getters to their underlying GamepadInterop/KeyLinkerlib calls.
Its first use should precede another live warm-up attempt.


### Managed warm-up getters mapped to native KeyLinker exports

The managed-to-native bridge for the normal hardware warm-up is now closed.

```text
GetDeviceBeforeKeyChangeListAsync
  operation 9  -> readDeviceBeforeKeyChangeList(0)

GetDeviceRockerAsync
  operation 12 -> readRockerData()

GetDeviceTriggerAsync
  operation 14 -> readTriggerData()

GetDeviceSupportTurboAsync
  operation 16 -> readDeviceSupportTurboList(0)

GetDeviceCurrentTurboAsync
  operation 17 -> readDeviceTurboData(0)

GetDeviceMotorAsync
  operation 19 -> readDeviceMotorData()

GetDeviceLamplightAsync
  operation 21 -> readDeviceLamplightData(0)

GetDeviceCustomDataAsync
  operation 28 -> readDeviceCustomData(0)
```

These operation numbers are internal `SeqMap` / result-dispatch types, not task
IDs and not native command bytes.

The corresponding receive-side functions are also confirmed:

```text
receiveDeviceBeforeKeyChangeList
receiveRockerData
receiveTriggerData
receiveDeviceSupportTurboList
receiveDeviceTurboData
receiveDeviceMotorData
receiveDeviceLamplightData
receiveDeviceCustomData
```

For list/segmented reads, `HandleGenericDeviceData` advances the logical
packet index, migrates the managed sequence number and calls the same native
read builder again with `index + 1`. This is visible for BeforeKeyChange,
SupportTurbo, CurrentTurbo and CustomData. The outer managed transport
therefore performs request continuation rather than treating the first native
read as necessarily complete.

All of these P/Invokes resolve directly to
`Libs\\Gamepad\\KeyLinkerlib.dll`.

The next static step is native disassembly of those exports, especially
`readDeviceLamplightData`, `writeDeviceLamplightData` and the warm-up
reads preceding CustomData. The existing
`tools/dump-keylinker-warmup-native.py` already targets exactly these read
builders and should be used before modifying the live warm-up.


### LampLight native command family and write command

Static native disassembly of `KeyLinkerlib.dll` now exposes the read-command
family used by the normal gamepad initializer:

```text
readRockerData             -> plaintext begins B1 06 ...
readTriggerData            -> plaintext begins B2 06 ...
readDeviceMotorData        -> plaintext begins B3 06 ...
readDeviceTurboData        -> plaintext begins B4 06 ...
readDeviceAfterKeyChange   -> plaintext begins B6 06 ...
readDeviceLamplightData    -> plaintext begins B7 06 ...
readDeviceCustomData       -> plaintext begins BB 06 ...
```

All of those specialized readers call the same `KeyLinkerState`, consume
`rand()`, build a six-byte plaintext request and pass it through
`KeyLinkerEncode`. The exact omitted middle bytes of the LampLight read are
being audited separately, but its command byte is now statically confirmed as
`0xB7`.

Most importantly for the Ambilight path, `writeDeviceLamplightData` calls:

```text
HeaderBuilder(command=0x37, fragment_index, frame_length)
-> KeyLinkerEncode(...)
```

The writer uses a maximum logical fragment payload of 15 bytes and adds the
same four-byte KeyLinker header plus trailing checksum/CRC byte, producing the
same broad write framing family already reconstructed for CustomData.

This confirms a protocol split at native level:

```text
LampLight read  : 0xB7
LampLight write : 0x37
CustomData read : 0xBB
CustomData write: 0x3B
```

Therefore Ambilight should target the dedicated `0x37` LampLight writer,
not repeated CustomData/0x3B writes.

The remaining blocker is the logical LampLight payload layout serialized
before the 15-byte fragmentation step. A focused static tool,
`tools/dump-lamplight-detail.py`, was added to dump the entire native
read/receive/write functions together with the managed
`DeviceLampLightStruct` metadata and LampLight call sites.


### LampLight 0x37 logical payload fully reconstructed

The native `writeDeviceLamplightData` serialization is now reconstructed
exactly.

Managed layout:

```text
LampLightStruct (sequential, 24 bytes)
+0x00 int32 R
+0x04 int32 G
+0x08 int32 B
+0x0C int32 breathSpeed
+0x10 int32 breathSwitch
+0x14 int32 brightness

DeviceLampLightStruct
+0x00 LampLightStruct lampLight0
+0x18 LampLightStruct lampLight1
+0x30 LampLightStruct lampLight2
+0x48 LampLightStruct lampLight3
+0x60 int32 lampLightNumber
```

The `+0x60` count is independently confirmed by both native reader and
writer.

The writer reduces each 24-byte managed lamp record to six protocol bytes.
The complete logical stream before 15-byte fragmentation is:

```text
byte 0 = lampLightNumber * 6

for lamp n:
  byte 1+6n = R & 0xFF
  byte 2+6n = G & 0xFF
  byte 3+6n = B & 0xFF

  packed = ((breathSwitch & 1) << 15) | (breathSpeed & 0x7FFF)

  byte 4+6n = packed & 0xFF
  byte 5+6n = (packed >> 8) & 0xFF
  byte 6+6n = brightness & 0xFF
```

The apparently complex ASCII `'0'/'1'` conversion in the optimized native
code is now fully resolved. IAT mapping identifies
`0x18000B130=strcpy_s`, `0x18000B138=strncpy_s` and
`0x18000B140=strcat_s`. The writer first copies the one-character
`breathSwitch` string, appends the 15-character `breathSpeed` string,
then converts characters 8..15 to the low byte and 0..7 to the high byte.
Therefore bit 15 is `breathSwitch` and bits 14..0 are `breathSpeed`.
The receive path performs the exact inverse expansion.

For one lamp the logical stream is therefore only seven bytes:

```text
06 R G B packed_lo packed_hi brightness
```

After serialization, `writeDeviceLamplightData` fragments this logical
stream into chunks of at most 15 bytes and wraps each chunk with
`HeaderBuilder(command=0x37,...)`, checksum/CRC, then
`KeyLinkerEncode`.

The corresponding read request is also fully resolved:

```text
B7 06 <state> <rand> <index> <crc>
```

where `index` is the managed continuation index passed to
`readDeviceLamplightData(index)`.

Managed write operation/result type is `22`; read type remains `21`.

Important persistence note: the same `WriteDeviceLamplightAsync` path is
used by `SaveLightAsync`, `ResetLightAsync`, `ApplyCurrentConfigAsync`
and `ChangeGamepadConfigAsync`. This proves it is the normal configuration
write path, but does NOT yet prove whether each 0x37 transaction is volatile
RAM-only or commits NVM/flash. High-rate Ambilight writes should therefore
remain disabled until persistence behavior is resolved.


### Managed LampLight save/apply ordering

Static CIL of the actual lighting save/apply paths resolves an important
persistence hypothesis.

`WriteDeviceLamplightAsync` uses managed operation/result type **22** and
directly calls native `writeDeviceLamplightData` (command `0x37`).
`WriteDeviceCustomDataAsync` uses type **29** and calls native
`writeDeviceCustomData` (command `0x3B`).

For `GamepadSettingViewModel::SaveLightAsync`, MixDrive first reads the
20-byte CustomData block, writes logical brightness to byte 0 and mode to
byte 1, and sends `0x3B`. Only after that does the applicable lighting branch
build `DeviceLampLightStruct` and send `0x37`. G3V2 falls through the
non-F1/non-G50 mode-limit path (max mode 7); the MACHENIKE branch uses 0x37
for the RGB/breath-capable mode groups.

`ResetLightAsync` likewise performs CustomData read/update/write before its
LampLight `0x37` write.

Other configuration-application paths can use the opposite ordering:
`ApplyCurrentConfigAsync` and `ChangeGamepadConfigAsync` contain a
LampLight write followed by a CustomData read/update/write.

Therefore **0x3B is not a required commit/finalize command for 0x37**. Its
confirmed role in these paths is updating the shared CustomData object
(including light mode/brightness), while 0x37 carries the actual LampLight
RGB/breath/brightness structure. The ordering is not fixed across callers.

This narrows but does not settle persistence: 0x37 may itself update
persistent storage, or it may alter live state that firmware later retains
by some internal mechanism. There is no separate host-side gamepad-lighting
commit API exposed by MixDrive.

### Gamepad lighting API surface

A full static inventory of lighting-related exports and P/Invokes found only
the following gamepad lighting API in `KeyLinkerlib.dll`:

```text
readDeviceLamplightData
receiveDeviceLamplightData
writeDeviceLamplightData
```

Other RGB/LED functions found by the inventory belong to keyboard or mouse
DLLs and are unrelated to the G3V2 gamepad transport.

Therefore the current MixDrive exposes no separate gamepad preview/realtime
RGB writer besides `writeDeviceLamplightData` / command `0x37`.
This does not prove that the firmware has no undocumented volatile lighting
command; it only proves that MixDrive/KeyLinker does not expose or call one.

Managed UI evidence also matters: `ChangeLightAsync` only changes the
view-model color property, while actual hardware writes occur in save/apply/
reset/config-change flows. This makes persistence behavior of command 0x37
the next critical question before any high-frequency Ambilight use.


### Firmware readback / controller dump path

A parallel track now targets obtaining the controller's *installed* firmware,
which could bypass the high-entropy updater package if the device stores
plaintext executable code internally.

Priority order:

1. **Normal KeyLinker/HID/BLE read surface** — look for any memory/ROM/firmware
   read primitive without entering the bootloader.
2. **Updater mass-storage/SCSI read surface** — statically prove every
   `deviceUsb_Request` caller and its transfer direction before any live
   probe. A useful dump primitive must be DATA_IN, addressable and scalable;
   20-byte identity/status reads do not qualify.
3. **Physical debug/storage interface** — use the now-public exact G3V2 FCC
   internal photos to identify MCU, external flash and SWD/JTAG/UART/ISP pads
   before opening the user's device.

Exact G3V2 FCC record:
`2BBT5-G3V2`, internal-photo document `8099258`, five pages, released
2025-09-03.

External prior art from `AmjadAAYD/x20ctl` is highly relevant but not yet
treated as proof for this unit. That project reversed a ZhiXu/KeyLinker
Downloader bootloader with the same hardware-ID prefix
`A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E`. It identifies model code
`0x2267` in this family; our official G3V2 package ID ends in `22 67` and
the updater filename begins `ZXD2267`.

Their DeviceUsb implementation exposes a generic
`IOCTL_SCSI_PASS_THROUGH_DIRECT` request wrapper, but the specific updater
they audited has only 20-byte DATA_IN identity/status reads and a 512-byte
DATA_OUT firmware path, i.e. no bulk readback. This is strong prior art that
software backup may be unavailable, but the G3V2 binary must be independently
closed before adopting that conclusion.

Added `tools/dump-deviceusb-readback-surface.py` for that independent,
read-only static audit.


Added `tools/dump-keylinker-firmware-readback.py` to statically inventory
normal-mode KeyLinker/managed firmware, ROM and memory readback candidates
before attempting any bootloader or physical-access experiment.


### Physical unit identity confirmation

A user-supplied photograph of the actual controller label independently
confirms the attached unit is labeled **Machenike G3 V2 Tri-mode Controller**
with **FCC ID `2BBT5-G3V2`** and manufacturer **Qingdao Machenike Technology
Co., Ltd.**

This closes the remaining identity gap between the user's physical unit and
the exact FCC filing used for teardown/reference work. The filing and internal
photos for `2BBT5-G3V2` can therefore be treated as directly relevant to
this hardware revision.

The per-unit serial/production code visible on the label is intentionally not
recorded in the repository.


### Updater readback path closed

The G3V2 v1.48 updater's actual `deviceUsb_Request` call pattern is now
statically closed from the exact executable.

Recovered six-argument ABI at the host call sites:

`deviceUsb_Request(handle, data_in, cdb_ptr, cdb_len, data_ptr, data_len)`

Exact firmware-update calls:
- `0x402644`: F1, `data_in=1`, 20 bytes;
- `0x402872`: F0, `data_in=0`, variable final chunk but capped at 512 bytes;
- `0x402897`: F2, `data_in=1`, 20 bytes;
- `0x4028DE`: F2, `data_in=1`, 20 bytes.

The F0 call sits in the 512-byte firmware loop over the embedded FOT payload.
F1 is the initial 20-byte identity/hardware-information read and F2 is used as
20-byte status polling around/after writes.

Therefore the official host updater contains **no bulk or addressable DATA_IN
request that can back up controller firmware**. This closes the updater itself
as a software dump path. It does not prove the bootloader firmware has no
undocumented vendor opcode; probing unknown SCSI commands remains blocked
without stronger recovery evidence.

The normal KeyLinker surface was also inventoried. Its exposed gamepad reads
are configuration/status records (feature, battery, key maps, macro data,
rocker, trigger, motor, LampLight, CustomData, etc.). No exported or managed
firmware/flash/ROM/memory read primitive and no suspicious large-transfer
builder was found. This closes the **exposed MixDrive/KeyLinker API** as a
firmware-backup path, while leaving undocumented native commands as an
unproven possibility.

Added `tools/dump-g3v2-updater-request-table.py` to verify the exact updater
hash and print the four firmware-request call sites without device I/O.


### ZhiXu hardware-family clue

External evidence suggests a likely physical-dump architecture, but this is
**not yet confirmed for the G3V2 PCB**.

ZhiXu's own product material states that at least some of its controller PCBAs
use a custom MCU with integrated Flash and no external EEPROM. Independent
teardowns of related ZXD-series boards (for example ZXD2222) identify a
ZhiXu `ZC32RF1M` QFN40 controller with a 26 MHz crystal. Other FCC filings
also identify `ZC32RF1M` as the gamepad RF/controller chip.

This makes an external 8-pin firmware flash less likely if the G3V2 follows
the same architecture. The exact G3V2 chip must still be read from its own FCC
internal photographs or a physical board before choosing a programmer/debug
method.

Do not assume ZC32RF1M pinout, debug protocol or read protection for G3V2
until the exact chip marking is confirmed.


### Exact updater request table independently verified

The hash-guarded focused audit was run successfully against the official
v1.48 updater (SHA-256
`f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255`).

It found **exactly four** calls through the dynamically resolved
`deviceUsb_Request` pointer at `0x005F9864`, with no additional call sites:

- `0x00402644`: F1 / DATA_IN / 20 bytes;
- `0x00402872`: F0 / DATA_OUT / up to 512 bytes;
- `0x00402897`: F2 / DATA_IN / 20 bytes;
- `0x004028DE`: F2 / DATA_IN / 20 bytes.

The script reported `count: 4`, `all four known sites present: True`, and
`extra updater request sites: (none)`.

This upgrades the updater-side conclusion from a focused interpretation to a
closed host call table: **the official updater binary exposes no firmware
backup/readback request**. Any future software dump route would therefore have
to rely on an undocumented bootloader command not used by the updater, a
normal-mode undocumented command, or physical debug/storage access.


### Same-family FOT crypto prior art

External reverse-engineering published in October 2026 materially changes the
non-invasive strategy.

`LopeKinz/Manba-One-V2-Firmware-tools` documents a controller updater using
the same `UsbUpdateAppX` template, `DeviceUsb.dll`, SCSI
`F1 -> F0 -> F2` protocol and hardware-ID prefix
`A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E`.

That project identifies a plaintext FOT container header/directory followed by
high-entropy encrypted `XCOD`/`XRES` payloads. Cross-version experiments on
a sibling EasySMX X20 pair with the **same hardware ID** indicate a
section-relative XOR-stream construction with a keystream reused across
versions of that hardware ID, while different hardware-ID tails use unrelated
streams.

A second independent G01/ZhiXu research thread reports the same updater
template and FOT family on `ZXD2271`, including an embedded image at file
offset `0x1712F0`, and states that hardware dumping/decryption remains the
open gap.

Implication for G3V2/Z`XD2267`:
- do not assume the 0x1712F0 high-entropy slice is the complete logical FOT
  container until the entire EXE is scanned for plaintext FOT metadata;
- the highest-value non-invasive artifact is a **second updater/image with the
  exact `...2267` hardware ID**;
- a public G3 V2 latency test records firmware **1.25 on 2025-01-28**, proving
  an earlier G3V2 firmware generation existed before the current v1.48 package.
  It is not yet proven that v1.25 used the same 16-byte hardware ID.

Added:
- `tools/dump-g3v2-fot-container-surface.py` to scan the complete updater for
  FOT magic, XOR-0x80 section tags, exact HWID and metadata around the known F0
  range;
- `tools/find-zxd2267-firmware-corpus.py` to search selected local directories
  for another exact-HWID updater/image without modifying files.


### Exact G3V2 MCU identified from FCC photos

User-supplied copies of the exact `2BBT5-G3V2` internal FCC photographs were
inspected at full resolution.

The main controller IC marking is legible in Photo 9:

- `ZC32RF1M`
- `QFN40-24`
- lot/trace code `EMU13322`

The PCB silkscreen on the exact board also reads approximately
`20241114 ZXD2267 G3 V2 V1.4 ...`, directly linking the physical PCB family
to the `ZXD2267` identifier in the official updater filename and hardware
family already recovered from firmware metadata.

Both visible sides of the main PCB were inspected. No discrete 8-pin SPI/QSPI
flash or obvious external EEPROM dedicated to firmware storage is visible.
This materially strengthens the conclusion that the installed firmware is held
in the ZC32RF1M's internal nonvolatile memory, though hidden/buried storage
cannot be excluded solely from photographs.

Photo 9 also exposes the 26 MHz crystal/oscillator network, PCB RF antenna,
USB-C connector and numerous labeled button/test pads around the MCU. No pad is
visibly labeled SWD, JTAG, ISP, TX or RX in the supplied close-up.

A public schematic for another controller using the same ZC32RF1M QFN40 shows
the part with:
- pin 22: RESET;
- pin 23: USB D-;
- pin 24: USB D+;
- pins 20/21: crystal OSCI/OSCO;
- exposed pad 41: GND.

This is useful family-level pinout evidence, but the exact G3V2 routing still
needs to be traced before attaching a programmer.

Physical-dump priority is now:
1. map the G3V2 MCU pins/test pads from the photos and continuity when the board
   is eventually accessible;
2. research ZhiXu factory/programming tooling for ZC32RF1M;
3. avoid any "unlock" command until its read-protection behavior is known.


### Safe simulated firmware-download path

A safer alternative to forcing a real OTA is now defined: reproduce only the
MixDrive firmware catalog/download stage outside the application, capture the
returned package, and never send `EnterUpdateModeMessage` or launch the
downloaded updater.

This matters because the managed update flow is already proven to set
`CustomData[11] |= 0x80` before `Process.Start`; clicking a real update is
therefore unnecessary and can transition the controller into bootloader mode.

The current official API query for `brand=MACHENIKE`,
`product_name=G3V2`, `product_type=2` returns the latest v1.48 updater.
Spoofing a lower local firmware version can make the UI consider an update
available, but it cannot force the server to expose an older package if the
API itself returns only the current release.

The highest-value target is a historical exact-hardware updater (especially a
G3V2 1.25 generation, which is independently known to have existed). A second
ZXD2267 package could materially help differential analysis of the transformed
FOT.

Added `tools/dump-mixdrive-firmware-update-flow.py` to statically recover the
catalog/download/request/launch path and URLs without HTTP requests, process
launches or device I/O.


### Firmware catalog/download behavior closed at managed layer

The MixDrive managed flow now proves that a controller-side firmware-version
spoof is **not required** to ask the server for the current package.

For gamepads, \`UpdateGamepadFirmwareAsync\` calls:

\`IFirmwareUpdateApiService.GetLatestVersionAsync(2, device.DeviceName, device.DeviceMode)\`

The current local firmware version is **not** passed to that server call. The
returned \`FirmwareInfoDto.Version\` is parsed locally and compared against
\`DeviceModel.FirmwareVersion\`; only if the server version is newer is a
\`FirmwareUpdateModel\` added to the UI.

The package download path is also explicit:

1. take \`FirmwareInfoDto.DownloadUrl\`;
2. if it is relative, prefix \`https://mixdriver.thunderobot.com/api\`;
3. issue a plain HTTP GET;
4. create \`Path.GetTempFileName() + ".exe"\`;
5. stream the response directly into that file;
6. for a gamepad, send the existing EnterUpdateMode message;
7. launch the downloaded EXE with \`Process.Start\` and wait for exit.

Therefore MixDrive does **not** expose a managed intermediate BIN/FOT/ZIP
download for controller firmware. The server artifact is treated directly as
an executable updater. A safe simulation can still retrieve and archive that
official EXE without touching the controller, but it will not by itself yield
a separate raw firmware image.

The remaining missing piece is the exact HTTP route and parameter names behind
\`IFirmwareUpdateApiService.GetLatestVersionAsync\`. Added
\`tools/dump-firmware-api-contract.py\` to recover those from the
\`Machenik.HttpApi*.dll\` metadata without network or device access.


### Exact firmware catalog route recovered

Static metadata/IL inspection of \`Machenik.HttpApi.dll\` closed the exact
firmware lookup contract used by MixDrive.

Service method:

\`IFirmwareUpdateApiService.GetLatestVersionAsync(productType, brand, productName)\`

Implementation route and query keys:

\`GET /firmware/by-product-info\`

with:
- \`brand\`
- \`product_name\`
- \`product_type\`

For the known G3V2/MACHENIKE mapping, the safe catalog request is therefore:

\`https://mixdriver.thunderobot.com/api/firmware/by-product-info?brand=MACHENIKE&product_name=G3V2&product_type=2\`

The same assembly shows the client updater route separately as
\`/software/latest\`.

Added \`tools/get-g3v2-firmware.py\`, a network-only client that reproduces
the firmware catalog stage and optionally downloads the returned updater.
It never opens HID/USB, sends EnterUpdateMode, modifies CustomData or launches
the downloaded executable. Default mode is catalog-only; \`--download\` must be
passed explicitly.


### Live catalog verification and updater identity

The recovered catalog request was executed from Bazzite and returned HTTP 200
JSON for MACHENIKE/G3V2/product_type=2.

Confirmed response fields:
- product.id = 8
- product.created_at = 2026-05-07T14:41:08Z
- firmware.id = 11
- firmware.version = 1.48
- firmware.changelog = "性能优化"
- firmware.download_url =
  /download/firmware/69fd65594961c_ZXD2267_JRH_G3V2_MACHENIKE_v1.48_20250815.exe
- firmware.file_size = 2245632
- firmware.published_at = 2026-05-08T04:23:53Z
- firmware.download_cnt = 0

The file downloaded through the official API has SHA256:
f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255

This exactly matches the updater already used for the static v1.48 analysis, so
there is no difference between the previously obtained updater and the file
currently served by the official MixDrive API.

Interesting storage-name observation: the first 8 hex digits of the 13-hex
prefix 69fd65594961c decode to Unix timestamp 2026-05-08 04:23:53 UTC, exactly
matching published_at. The full 13-hex shape is consistent with a PHP
uniqid()-style timestamp+microsecond prefix. This is a strong implementation
clue, not yet proof of the server-side source code.

The product record is dated May 2026 while the original updater filename
contains build date 20250815. Therefore the active catalog appears to have
been populated/migrated after the updater was built; older firmware may not
be exposed by the latest-version route even if historical files still exist.

Added tools/probe-firmware-history-api.py, a deliberately small GET-only probe
for likely history/id route shapes using product id 8 and firmware id 11.
It performs no device access and is not a brute-force scanner.


### Scope correction — historical firmware is not required for the current goal

The current G3V2 reports firmware 1.48, and the updater downloaded from the
official MixDrive API is byte-identical (same SHA256) to the v1.48 updater
already under analysis.

Therefore historical firmware discovery is NOT a prerequisite for the current
reverse-engineering path. Older versions may be useful later for differential
analysis, but they are now explicitly deprioritized.

Important distinction:
- we have the official v1.48 updater EXE;
- it contains the known embedded FOT payload used by the updater;
- this is sufficient for static analysis of the current firmware/update path;
- it is not yet proven to be a byte-for-byte dump of the controller's entire
  currently installed flash, because bootloader/config/calibration/NVM regions
  may be outside the update payload and the FOT may be a container/encoded image.

Current priority returns to static analysis of the v1.48 embedded FOT and the
0x37 LampLight handler/persistence behavior.


### FOT randomness check — Thumb scout false positives quantified

The exact 0x5A400-byte FOT scan produced 201 halfword patterns matching
Thumb \`CMP Rn,#imm8\` for the nine command immediates under investigation.

For a uniformly random byte stream, scanning 184832 aligned halfwords gives:

\`184832 * (9/256) * (8/256) = 203.0625\` expected hits.

Observed 201 is essentially the random expectation. This quantitatively
confirms that the apparent CMP clusters are not evidence of a plaintext
protocol dispatcher. The vector-table heuristic hits are likewise not treated
as executable-code evidence in this near-max-entropy payload.

Current next step: scan the whole updater/container surface and the low-entropy
edge regions to determine whether plaintext FOT metadata sits outside the
0x1712F0 F0-transferred slice, and whether the updater sends transformed bytes
directly or performs a host-side decode first.


### Correction — FOT container is structured; only payload sections are high entropy

The full-container scan corrects the earlier interpretation of the 0x1712F0
slice. That slice is not an opaque all-ciphertext blob: it begins with a valid
plaintext FOT container header.

Confirmed relative layout inside the 0x5A400-byte G3V2 image:

- 0x000000: FOT header/directory, 0x200 bytes;
- 0x000054: XOR-0x80 encoded INFO tag;
- 0x00008C: XOR-0x80 encoded DEV tag;
- 0x0000A0: XOR-0x80 encoded KEY tag;
- 0x0000B0: XOR-0x80 encoded DATA tag;
- 0x000200: XOR-0x80 encoded XCOD section start;
- 0x058600: XOR-0x80 encoded XRES section start;
- 0x059200: internal directory copy;
- 0x059400: configuration region start;
- 0x0598xx candidate boundary to the final high-entropy tail, to be verified by the corrected scanner.

The DATA record declares data_start=0x200, data_len=0x59000,
directory_copy=0x59200 and config_start=0x59400. Therefore the DATA span ends
exactly where the internal directory copy begins.

The apparent second FOT magic at file offset 0x1CA4F0 is exactly
container-relative 0x59200, matching the directory_copy field; it is not a
second firmware image.

The G3V2 KEY record contains the same eight-byte value documented in another
ZXD/EPIC FOT family image: 00 00 00 01 EA E3 2E 09. This strengthens the view
that it is a shared key-id/nonce/check field rather than the secret decryption
key itself, but does not prove its exact semantics.

The original dump-g3v2-fot-layout.py had two blind spots: it did not scan for
FOT\\0 and it searched XCOD/XRES as plaintext ASCII even though the FOT section
names are stored XOR 0x80. The tool has been corrected.

The high whole-image entropy is now explained by the encrypted XCOD/XRES/tail
occupying almost all of the image; it must not be used to classify the
plaintext metadata/header as random data.


Added \`tools/analyze-g3v2-fot-crypto-surface.py\` for a focused read-only
verification of the corrected container model: region hashes/entropy,
directory-copy equality, KEY bytes, XCOD/XRES section headers, repeated
16-byte block counts and plaintext strings from metadata/config only.


### Confirmed from G3V2 v1.48 region-aware crypto scan

The focused analyzer was run on the exact official updater
SHA256 f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255.

Confirmed FOT SHA256:
0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085

Confirmed metadata:
- logical size 0x5A400
- hardware ID a1ca51da724cb6103658c05d7d4e2267
- version 148
- data_start 0x200
- data_len 0x59000
- directory_copy 0x59200
- config_start 0x59400
- DATA end equals directory_copy exactly

Confirmed XOR-0x80 tag offsets:
- INFO 0x54 / 0x59254
- DEV  0x8C / 0x5928C
- KEY  0xA0 / 0x592A0
- DATA 0xB0 / 0x592B0
- XCOD 0x200
- XRES 0x58600
- DOWN 0x594C0

The first 0x200 bytes and the directory copy at 0x59200 are byte-identical,
with identical SHA256 6a8f80e9b9d5aa6336b565589700f0fb212aa850053b379e4a3edf364b8f8a52.

KEY record:
- length 8
- bytes 00 00 00 01 EA E3 2E 09
- matches the same-family ZXD/EPIC constant reported publicly.

Encrypted-region statistics:
- XCOD: 361472 bytes, entropy 7.999503, chi-square 248.90, zero repeated aligned 16-byte blocks
- XCOD payload after 16-byte section header: entropy 7.999503, chi-square 249.25
- XRES: 3072 bytes, entropy 7.941931, chi-square 250.50, zero repeated aligned 16-byte blocks
- XRES payload after 16-byte section header: entropy 7.943922, chi-square 235.81

This strongly supports a high-quality stream/CTR/OFB-like encrypted payload and rules out
simple static-key ECB. It does not identify the cipher.

Plaintext/config strings include BT-BOX and LE-Remoter. DOWN is located inside the
plaintext configuration area at 0x594C0.

Important distinction: the official v1.48 FOT is the firmware package corresponding to
the controller's installed version, but it is not a plaintext dump of the controller's
internal flash. XCOD/XRES remain encrypted in the package; obtaining the installed
plaintext image still requires either a live flash/debug dump or recovery of the
bootloader-side decryption mechanism.

Same-family public work on ZXD2055/ZXD2271 independently reports the same FOT layout,
same KEY constant, byte-for-byte host transfer and bootloader-side decryption. It also
reports keystream reuse across same-hardware-ID firmware versions on an EasySMX X20 pair.
This is external family evidence, not direct proof yet for G3V2/ZXD2267.


Added \`tools/scan-g3v2-local-fot-corpus.py\`, a read-only recursive scanner for
complete FOT containers in local files/updaters. It highlights the exact G3V2
hardware ID \`a1ca51da724cb6103658c05d7d4e2267\`, deduplicates by FOT SHA256 and
reports whether more than one distinct same-HWID version exists locally.


### Two distinct same-HWID / same-version G3V2 FOT images found locally

Read-only local corpus scan covered 19,091 files in the project tree and Downloads.
It found four complete FOT occurrences, all for the exact G3V2 HWID
\`a1ca51da724cb6103658c05d7d4e2267\`, but only two distinct FOT SHA256 values:

1. \`firmware-original/G3V2-1006-2011-v1.48.fot\`
   - version 148
   - SHA256 \`0b97b5e38fc706182110cdfa1682b1c252a1c0dac8314f5c836d3f037a67e836\`

2. official updater FOT
   - version 148
   - SHA256 \`0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085\`
   - duplicate copies occur in the extracted FOT and downloaded official updater.

This is a confirmed same-HWID, same-version, different-image pair. The provenance of
\`firmware-original/G3V2-1006-2011-v1.48.fot\` is currently not documented well enough
to call it a controller flash dump, so that interpretation is intentionally withheld.

Added \`tools/compare-g3v2-same-hwid-fots.py\` to perform a section-aware differential
analysis of the pair. It reports byte equality, XOR entropy/chi-square, nonzero XOR
ranges, aligned-block repetition and header/directory invariants for header, XCOD,
XRES, directory-copy, config and tail.

Decision rule:
- overwhelmingly identical XCOD/XRES with localized differences -> strong candidate for
  same-keystream reuse and useful differential analysis;
- random-looking XCOD/XRES XOR with ~1/256 equal bytes -> more consistent with changed
  keystream/encryption context, radically different plaintext, or a transformed source;
- identical XCOD/XRES -> distinct whole-FOT hash is only metadata/config/tail and the pair
  is not useful as a ciphertext-difference test.


### Same-HWID v1.48 pair resolved: payload is byte-identical

The section-aware differential comparison between:
- \`firmware-original/G3V2-1006-2011-v1.48.fot\`
  SHA256 \`0b97b5e38fc706182110cdfa1682b1c252a1c0dac8314f5c836d3f037a67e836\`
- official updater FOT
  SHA256 \`0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085\`

shows that the actual firmware/resource payload is identical:

- XCOD: 361472 / 361472 bytes identical (100%)
  SHA256 \`3ddaec3bb4d3723d4d1c55392473e6830872674ce3c33f6ab401b857844821fe\`
- XRES: 3072 / 3072 bytes identical (100%)
  SHA256 \`173740c39fce50700eea71a2616afbf445feabb0217ec4bc6c3ab57919377bb9\`
- config: 100% identical
- tail: 100% identical

Only four bytes differ in the 0x200-byte FOT header:
- header offsets \`0x101\`
- header offsets \`0x115..0x117\`

The directory copy at \`0x59200\` mirrors the header exactly in each file, so those
same four changes appear again at \`0x59301\` and \`0x59315..0x59317\`.
Therefore the whole-image SHA difference is caused by exactly eight bytes of duplicated
container metadata, not by different G3V2 firmware code/resources.

Whole FOT:
- 369656 / 369664 bytes identical = 99.997836%
- longest identical run: 365033 bytes

Consequence: this pair is **not** useful for cross-ciphertext/same-keystream recovery,
because the XCOD and XRES ciphertexts are byte-identical. It also means the local
\`firmware-original\` file must not be described as a distinct firmware build on the basis
of SHA256 alone.

Added \`tools/inspect-g3v2-fot-header-delta.py\` to classify the remaining four canonical
header-byte differences, dump their exact values/context, test aligned LE fields,
mirror invariants and common CRC32/Adler32 candidates.


### FOT header delta reclassified: Bluetooth/gamepad address personalization

The previously unexplained three-byte delta at header offsets \`0x115..0x117\` is part of a
six-byte field at \`0x112..0x117\`:

- local FOT A: \`98:B6:E9:B0:25:BE\`
- official FOT B: \`98:B6:E9:CE:4B:03\`

This strongly reclassifies the field from "possible checksum/timestamp" to a controller
address/identity field:

- \`98:B6:E9\` is used by the KeyLinker/X20 ecosystem as the standard-gamepad address
  prefix (documented in the public \`AmjadAAYD/x20ctl\` protocol notes / AgreementConfig
  reconstruction).
- An independent public FOT from the same ZXD/EPIC family (Manba One V2, ZXD2055) contains
  the same six-byte pattern at the exact same offset:
  \`98:B6:E9:1A:78:60\`.
- The common checksum probe already ruled out CRC32/Adler32 matches for the changed aligned
  fields.

Therefore the two G3V2 v1.48 images are best described as the same encrypted firmware
payload with different personalization metadata, not as different firmware builds.

The remaining byte delta at \`0x101\` is still unresolved. It sits inside the six-byte
sequence \`0xFE..0x103\`:
- A: \`5C C1 01 66 43 01\`
- B: \`5C C1 01 DD 43 01\`

Do not label that field as a checksum, timestamp or second MAC without further evidence.

Best non-destructive discriminator: compare the controller's observed Bluetooth/gamepad
address against the two \`98:B6:E9:xx:xx:xx\` candidates. This can identify which
personalized FOT is associated with the physical unit without touching firmware storage.


### Live Bluetooth observation corrects prior FOT-address interpretation

Active BlueZ discovery with the physical G3V2 produced:

\`98:B6:ED:<redacted>  G3V2 APP\`

Neither of the two FOT header fields at \`0x112..0x117\` matched the live address:
- local FOT A: \`98:B6:E9:B0:25:BE\`
- official FOT B: \`98:B6:E9:CE:4B:03\`

Therefore the earlier idea that the FOT \`98:B6:E9:xx:xx:xx\` field is simply the
controller's currently advertising BLE address is rejected.

Family-level public KeyLinker/X20 reverse engineering gives a plausible explanation:
- \`98:B6:E9\` is classified as a standard-gamepad prefix;
- \`98:B6:ED\` is classified as a Switch-mode/device-family prefix;
- the configuration peripheral can be distinct from the gameplay interface.

For G3V2 specifically, \`G3V2 APP\` at an ED address may therefore be a separate
configuration/app personality, while the E9 FOT field may refer to another radio identity,
personality, factory-programming address or inactive mode. This is now a hypothesis, not
a confirmed mapping.

Added \`tools/inspect-g3v2-bluetooth-identities.py\` to scan FOT header/config regions for
all \`98:B6:E*\` six-byte sequences and compare them against the observed live address,
suffix and reversed forms without any device I/O.


### G3V2_APP identified as a separate configuration BLE personality

The physical G3V2 advertised live as:
- \`98:B6:ED:<redacted> G3V2 APP\`

The observed live MAC/suffix does not occur anywhere in either known FOT, including
header/config, reversed forms, or under E9/EC/ED prefixes.

External G3V2 manual evidence clarifies this: the KeyLinker instructions state that
\`G3V2_APP\` is discoverable when the controller is started in any mode and that this
configuration function does not require the controller to be paired to the phone as the
gamepad Bluetooth device. This strongly supports a separate app/configuration BLE
personality.

The same manual documents distinct gameplay Bluetooth personalities:
- X + HOME: PC/mobile Bluetooth, shown as Xbox Wireless Controller;
- Y + HOME: Switch Bluetooth, shown as Pro Controller;
- A + HOME: Android/DInput Bluetooth, shown as MACHENIKE G3 V2.

Therefore the live ED \`G3V2 APP\` address must not be compared directly against the FOT
E9 field as if they were the same interface.

FOT family evidence remains notable: G3V2 and a public same-family ZXD2055 image both show,
at the same offsets, a concrete \`98:B6:E9:xx:xx:xx\` value plus
\`98:B6:E9:00:00:00\` and \`98:B6:E9:FF:FF:FF\`. This is consistent with a
standard-gamepad identity/range/template structure, but the precise field semantics are
still unproven.

Next safe discriminator: scan each gameplay Bluetooth mode separately and record the
advertised address/name in addition to the always-visible \`G3V2 APP\` peripheral.


### Mode-map correction and X+HOME discovery result

User-verified correction:
- B + HOME is the dongle mode.
- In dongle mode the controller enumerates as an Xbox 360 Controller.

This overrides the earlier external-manual interpretation that had been treated too
confidently.

Read-only Bluetooth discovery while the controller was started with X + HOME found only:
- \`98:B6:ED:<redacted> G3V2 APP\`

No second \`98:B6:E*\` gameplay identity was visible in that scan, and neither known FOT
E9 address appeared.

Interpretation:
- the always-visible \`G3V2 APP\` configuration peripheral remains independently confirmed;
- the X+HOME gameplay personality was not discoverable in this scan;
- do not infer from this that the FOT E9 field is absent from the physical controller,
  because the gameplay personality may use a different transport, may require a different
  discoverable/pairing state, or may not be exposed by the same BlueZ discovery path.


### X+HOME automatic scan did not expose gameplay Bluetooth identity

User test in X+HOME mode:
- active BlueZ scan showed only \`98:B6:ED:<redacted> G3V2 APP\`
- no \`Xbox Wireless Controller\`
- no additional \`98:B6:E*\` device

External G3V2 manuals corroborate the user-verified mode map:
- B+HOME = 2.4 GHz dongle mode
- X+HOME = PC/mobile Bluetooth mode, expected pairing name \`Xbox Wireless Controller\`
- pairing state requires holding X+HOME from power-off until the indicator flashes rapidly

Because BlueZ automatic discovery may interleave LE and BR/EDR, the next read-only test is
to force BR/EDR-only and LE-only discovery separately. Do not interpret the absent gameplay
device from the auto scan as evidence that the FOT E9 identity is not used by the controller.


### Method correction — transport-specific bluetoothctl scan vs device cache/listing

A subsequent X+HOME test ran separate \`bluetoothctl scan bredr\` and
\`bluetoothctl scan le\` passes but \`bluetoothctl devices\` showed only the already-known
\`98:B6:ED:<redacted> G3V2 APP\` after both passes.

Important correction: \`scan bredr\` / \`scan le\` correctly select the discovery
transport, but a later \`bluetoothctl devices\` listing is not a per-scan evidence log.
Therefore seeing the APP device after a BR/EDR pass does **not** prove that the APP
peripheral was discovered through BR/EDR.

Current X+HOME evidence is only:
- no additional gameplay identity became visible to the BlueZ device list;
- the persistent APP/config peripheral remains known;
- transport of that APP observation is not established by the post-scan \`devices\` list.

Next read-only diagnostic is HCI/MGMT monitoring with \`btmon\`, which can show the actual
discovery events and transport-specific reports in real time while X+HOME is placed in
rapid-flash pairing mode.


### Root cause found: previous one-shot bluetoothctl scan stopped immediately

The btmon capture proved that the prior non-interactive command
\`bluetoothctl scan on\` did not remain alive for the requested shell timeout.

Observed timeline:
- Start Discovery success at btmon t=2.303496
- Stop Discovery command at t=2.303523
- Inquiry Cancel immediately followed

So discovery was active for only ~27 microseconds at the MGMT command level before the
client teardown requested Stop Discovery. The earlier shell wrapper
\`timeout 22s bluetoothctl scan on\` is therefore invalid for this BlueZ behavior because
the bluetoothctl process itself exits immediately.

BlueZ documents \`--timeout/-t <seconds>\` specifically for non-interactive mode.
Use:
\`bluetoothctl --timeout 22 scan on\`
or the transport-specific equivalents:
\`bluetoothctl --timeout 22 scan bredr\`
\`bluetoothctl --timeout 22 scan le\`

This keeps the bluetoothctl client alive and lets discovery persist for the intended
window. All previous conclusions based on "X+HOME gameplay identity absent from scan"
must be treated as unproven until repeated with this corrected invocation.


### X+HOME Bluetooth identities confirmed on the physical G3V2

A corrected 25-second BlueZ discovery with simultaneous btmon capture resolved the
controller's two live Bluetooth personalities.

Configuration/app personality:
- transport: LE
- public address: \`98:B6:ED:<redacted>\`
- complete name: \`G3V2 APP\`
- legacy advertising PDU: ADV_IND
- address type: Public

Gameplay personality in X+HOME:
- transport: BR/EDR
- public address: \`98:B6:EA:<redacted>\`
- complete name: \`Xbox Wireless Controller\`
- class: \`0x000508\` (Peripheral)
- observed through Extended Inquiry Result / MGMT Device Found

The shared suffix \`<redacted>\` strongly ties both personalities to the same physical unit.
Only the third prefix byte differs:
- APP/config: ED
- X+HOME gameplay: EA

This directly corrects the earlier assumption that the live gameplay identity would
necessarily use the FOT's \`98:B6:E9:xx:xx:xx\` field.

The two known v1.48 FOTs contain:
- \`98:B6:E9:B0:25:BE\`
- \`98:B6:E9:CE:4B:03\`

Neither suffix matches the live unit (\`<redacted>\`), so those FOT values are not the
current live Bluetooth addresses of this controller.

Family-level corroboration is notable: public X20 tooling comments that AgreementConfig
lists E9/EC/ED family prefixes while an actual X20 device can appear live as \`98:B6:EA\`.
This makes it plausible that the FOT E9 field is a template/base/factory family value
rather than the exact runtime address used by every personality. Exact semantics remain
unproven.

Next safe mapping step: repeat the same real discovery for Y+HOME and A+HOME and record
whether the live suffix remains \`<redacted>\` while the prefix changes by mode.


### Y+HOME capture: no connection to the Bazzite host

A read-only btmon review of the Y+HOME test showed:
- only the persistent LE \`98:B6:ED:<redacted> G3V2 APP\` advertisements;
- no HCI \`Connection Complete\`, \`Device Connected\`, ACL connection, authentication,
  remote-name or link-key event for a gameplay personality;
- \`bluetoothctl devices Connected\` returned no connected device on the Bazzite host.

Therefore the Y+HOME gameplay personality did **not** connect to this Bazzite machine
during the test. If the controller's LEDs indicated a successful connection, the likely
explanation is automatic reconnection to another previously paired host outside this HCI
capture.

The Y+HOME gameplay Bluetooth address/name remains unmapped. To capture it non-destructively,
prevent the previously paired host from accepting the connection (e.g. temporarily disable
Bluetooth on that host) and repeat the same real discovery/btmon capture while forcing
Y+HOME into pairing mode. No controller pairing records need to be erased.


### Correction: Y+HOME test ended connected to Bazzite through the 2.4 GHz dongle path

User clarified that at the end of the Y+HOME test the controller did connect to the same
Bazzite machine, but it appeared as an Xbox 360 Controller.

This resolves the apparent contradiction with the btmon/BlueZ evidence:
- btmon on hci0 showed no Bluetooth connection event;
- BlueZ showed no connected gameplay device;
- the controller nevertheless became usable on the same PC as Xbox 360 Controller.

The consistent explanation is that the final connection occurred through the separate
2.4 GHz USB dongle path, which does not traverse the Bazzite Bluetooth HCI adapter and is
therefore invisible to btmon/bluetoothctl.

This supersedes the previous inference that the controller likely reconnected to another
host.

Implication for further Y+HOME Bluetooth identity mapping:
- the USB dongle must be temporarily unplugged/disabled during the scan, otherwise an
  automatic 2.4 GHz reconnection can terminate or obscure the intended Bluetooth pairing
  state.
- no controller pairing data needs to be erased.


### Y+HOME confirms E9 as the live Pro Controller BR/EDR personality

With the 2.4 GHz dongle removed, a real 25-second discovery + btmon capture in Y+HOME
resolved the gameplay personality:

- LE config/app:
  - \`98:B6:ED:<redacted>\`
  - \`G3V2 APP\`
- BR/EDR gameplay:
  - \`98:B6:E9:<redacted>\`
  - \`Pro Controller\`
  - class \`0x002508\`
  - Limited Discoverable Mode

This is direct G3V2 evidence that the E9 prefix is used by the Pro Controller personality.
It supersedes any same-family semantic mapping that assigned E9/ED differently.

The suffix remains exactly \`<redacted>\`, matching the X+HOME EA personality and the
persistent ED APP personality. Current live mapping for this physical unit:

- ED: \`G3V2 APP\` (LE configuration)
- EA: \`Xbox Wireless Controller\` (BR/EDR, X+HOME)
- E9: \`Pro Controller\` (BR/EDR, Y+HOME)

This substantially changes the interpretation of FOT header offset \`0x112..0x117\`.
Both known v1.48 FOTs carry E9-form addresses there:

- local FOT A: \`98:B6:E9:B0:25:BE\`
- official-updater FOT B: \`98:B6:E9:CE:4B:03\`

Since the physical controller's live Pro Controller address is instead
\`98:B6:E9:<redacted>\`, neither FOT header is personalized with this unit's current E9
identity.

Because A and B have byte-identical XCOD/XRES/config/tail but differ only at:
- header byte \`0x101\`, and
- the last three bytes of the E9 address at \`0x115..0x117\`,
with the same four-byte delta mirrored in the directory copy, the strongest current
interpretation is per-image/per-device Bluetooth personalization metadata layered over a
shared firmware payload.

The exact role of \`0x101\` is still unknown. Its co-variation with the E9 address makes it
a strong candidate for a dependent checksum/integrity/derivation byte, but this is not
proven and should not be labeled as a checksum without additional samples or algorithmic
confirmation.

Important provenance implication: the local \`firmware-original\` file must not be called
a dump of the user's current controller. Its E9 identity does not match the live unit.
Likewise, the official updater image appears to contain a different pre-personalized or
reference identity rather than the user's current live Pro Controller address.


### Important constraint: generic updater image means the E9 field is not necessarily the final runtime BD_ADDR

The Y+HOME test proves that live G3V2 Pro Controller mode uses the \`98:B6:E9\` address
namespace. However, the official updater distributes one generic FOT image containing one
fixed E9-form value at \`0x112..0x117\`.

A generic update image cannot safely assign the same final Bluetooth address to every
controller. Therefore the current interpretation must stay narrower:

- \`0x112..0x117\` is strongly associated with the E9 / Pro Controller identity domain;
- it is **not yet proven** to be the final runtime BD_ADDR programmed into the radio;
- plausible mechanisms include:
  - device-specific identity stored separately in NVM and preserved across firmware updates;
  - bootloader/firmware patching or deriving the runtime address from another per-unit source;
  - the FOT field acting as provisioning/reference/default metadata rather than the live address.

The fact that the two same-payload v1.48 FOTs differ only in this E9-form suffix plus byte
\`0x101\` still proves that this block is personalization/identity-related container
metadata, but not necessarily user-unit MAC programming data.

Do not describe either FOT as belonging to a specific physical unit solely from the
\`0x112\` value until a direct device/FOT correspondence is demonstrated.


### A+HOME identity confirmed; live mode/address matrix now nearly complete

With the 2.4 GHz dongle out of the way, A+HOME advertised over BR/EDR as:
- prefix: \`98:B6:E8\`
- complete name: \`MACHENIKE G3V2\`
- class: \`0x002504\`
- Limited Discoverable Mode

The lower 24-bit per-unit suffix matched the same physical unit's other Bluetooth
personalities and is intentionally redacted from persistent documentation.

Current confirmed live identity map for this physical G3V2:

| Function / startup mode | Transport | Prefix | Advertised identity |
|---|---|---|---|
| Configuration peripheral | BLE | \`98:B6:ED\` | \`G3V2 APP\` |
| X+HOME | BR/EDR | \`98:B6:EA\` | \`Xbox Wireless Controller\` |
| Y+HOME | BR/EDR | \`98:B6:E9\` | \`Pro Controller\` |
| A+HOME | BR/EDR | \`98:B6:E8\` | \`MACHENIKE G3V2\` |
| B+HOME | 2.4 GHz USB dongle | not BlueZ/HCI | appears to host as \`Xbox 360 Controller\` |

The same per-unit suffix is preserved across the E8/E9/EA/ED Bluetooth personalities.
This strongly supports a model of one device-specific lower-24-bit identity plus a
personality/mode-specific prefix.

### Lighting state persistence observed across shutdown and mode changes

The controller remained in the previously configured blue breathing lighting state after:
- powering off and restarting into other startup modes;
- changing Bluetooth personalities / pairing mode;
- repeating multiple mode-discovery tests.

Therefore the lighting configuration itself is persistent across normal controller
power-off/on and mode changes.

Important limitation: this observation does **not yet isolate which write path persists
the state**. Previous software flows can use both \`0x37\` (lighting write) and
\`0x3B\` (CustomData save/write) in related save operations. The observation proves that
the resulting lighting state is stored persistently, but not that a standalone \`0x37\`
write by itself commits to NVM.

Practical Ambilight implication:
- do not assume high-frequency \`0x37\` writes are volatile/safe for flash endurance;
- before using the normal lighting command as a realtime Ambilight transport, perform the
  controlled one-write persistence test that isolates \`0x37\` from \`0x3B\`.


### Stronger identity-storage inference after E8/E9/EA/ED mapping

The same physical controller preserves one lower-24-bit device suffix across all observed
Bluetooth personalities (E8/E9/EA/ED), while the previously completed whole-FOT scan
found that live suffix nowhere in either known v1.48 FOT, including raw, reversed and
family-prefix-combined forms.

This makes a separate per-unit identity source substantially more likely than direct
storage of the runtime address in the generic update image.

Current leading possibilities:
1. dedicated NVM/OTP/efuse or reserved MCU flash outside the update payload;
2. a bootloader-preserved per-device configuration region;
3. deterministic derivation from another hardware-unique identifier.

This also explains how one generic official FOT can update many controllers without
cloning their Bluetooth identities.

The fixed E9-form value at FOT offset 0x112 should therefore be treated as
identity/provisioning metadata or a default/reference value until its lifecycle is proven.


### Isolated 0x37 LampLight persistence test prepared

Added \`tools/g3v2-light-0x37-persistence-test.py\` to isolate the remaining Ambilight
persistence question without involving CustomData writes.

Test design:
- \`--apply\`
  - replays the already reconstructed official read-only warm-up;
  - captures the current one-lamp B7 payload;
  - saves a local restoration snapshot before mutation;
  - preserves effect/breath bytes and physical brightness;
  - changes RGB only to a distinct marker color;
  - emits exactly one native \`0x37\` frame;
  - emits zero \`0x3B\` writes;
  - verifies the immediate state with B7.
- real controller power-cycle;
- \`--check\`
  - read-only warm-up/B7 comparison against original and marker;
- \`--restore\`
  - restores the original snapshot with at most one \`0x37\` write and verifies by B7.

The tool refuses to mutate unless the live B7 logical payload is exactly six bytes
(one lamp). It contains no OTA or SCSI path and no realtime/write loop.

Protocol basis:
- cold E670 write phase is zero;
- read-only warm-up does not advance E670;
- write header uses the current E67C/E68C state after warm-up plus the next CRT-rand byte;
- one-lamp stream is \`06 + six LampLight bytes\`;
- one-frame plaintext size is 12 bytes;
- post-write E67C/E68C state is advanced before B7 readback.

This test is intended to decide whether high-frequency use of the documented 0x37 path
is unsafe for NVM endurance.


### Confirmed: standalone LampLight 0x37 write persists across real power-cycle

The guarded isolated persistence test has now been completed successfully on the physical
G3V2.

Observed sequence:
1. B7 baseline before mutation: \`00 FF FF 00 00 FF\`
2. snapshot original: \`00 FF FF 00 00 FF\`
3. standalone RGB marker written with exactly one native \`0x37\` frame;
4. zero \`0x3B\` CustomData writes were emitted by the test;
5. immediate B7 readback matched the marker;
6. controller was physically powered off/on;
7. read-only \`--check\` B7 returned the marker unchanged:
   \`FF 00 00 00 00 FF\`;
8. therefore the standalone \`0x37\` state survived a real power-cycle;
9. \`--restore\` then sent one standalone \`0x37\` restoring
   \`00 FF FF 00 00 FF\`;
10. post-restore B7 matched the original exactly and the local snapshot was deleted.

The write ACK during restore was also observed through the managed outer envelope:
- \`81 F9 <seq> 01 02 ...\`
- flags bit 0 set, consistent with the previously reconstructed ACK path.

Conclusion:
- **0x37 is independently persistent.**
- 0x3B is not required to make a LampLight RGB change survive power loss.
- The documented MixDrive/KeyLinker LampLight writer must NOT be used at Ambilight/realtime
  update rates until the storage mechanism and endurance characteristics are known.

This supersedes the earlier persistence uncertainty around 0x37.

### Ambilight direction after persistence confirmation

The project should now treat command 0x37 as a configuration/persistent-state writer, not
as a safe realtime transport.

Priority shifts to finding a volatile lighting path, in this order:
1. search firmware/KeyLinker command families for an undocumented preview/live RGB command;
2. inspect whether any command can update LED output without touching the persistent
   LampLight object;
3. if no software-visible volatile path exists, investigate whether the MCU lighting engine
   exposes a RAM-only register/command that is absent from MixDrive;
4. keep high-frequency 0x37 streaming disabled.

The original lighting state was successfully restored at the end of the test.


### LampLight path works even when CustomData reads are empty

During the successful standalone 0x37 persistence/restore test, every CustomData read in
the official warm-up returned logical length zero, while:
- B7 LampLight reads succeeded;
- the standalone 0x37 write was accepted;
- the post-write B7 readback matched;
- persistence survived power-cycle;
- the restore 0x37 write and B7 verification also succeeded.

Therefore the LampLight read/write path can operate independently of a valid 20-byte
CustomData result in the same session. CustomData is not a prerequisite for basic B7/0x37
lighting control on this unit.


### Correction: B7/0x37 snapshot does not capture the complete visual lighting mode

User visual observation during the isolated persistence test adds a critical correction:

- before the test the controller was visually in a blue breathing mode;
- the standalone marker \`FF 00 00 00 00 FF\` displayed as **solid red**;
- restoring the saved B7 payload \`00 FF FF 00 00 FF\` displayed as a
  **solid cyan / light-blue / blue-green** color, not the original breathing effect.

Therefore the prior statement that the original lighting state was fully restored is too
strong. What was restored and verified was the exact **B7 LampLight payload**, not the
complete visual lighting configuration.

Direct consequences:
- RGB byte order is empirically confirmed as literal R,G,B:
  - \`FF 00 00\` -> red;
  - \`00 FF FF\` -> cyan-like output.
- with the current active mode after the test, packed effect bytes \`00 00\` display solid.
- the breathing behavior that existed before the test is not represented solely by the
  six-byte B7 LampLight payload.

This aligns with the already proven managed split:
- B7/0x37 carries RGB, lamp brightness and the LampLight breath fields;
- CustomData \`data[1]\` carries \`LightModeIndex\`.

Because all CustomData reads during the Linux persistence test returned zero length, the
pre-test LightModeIndex was not captured and could not be restored by the isolated 0x37
snapshot.

Revised persistence conclusion:
- the **0x37/B7 LampLight payload itself** survives a real power-cycle;
- this does not prove that the complete lighting mode/effect state is encoded or restored
  by 0x37 alone;
- high-frequency 0x37 streaming should still remain disabled because its payload is
  demonstrably persistent across power loss.

The original breathing mode should be restored through the normal MixDrive save path (or
after its CustomData mode value is safely recovered), rather than by guessing a 20-byte
CustomData write.


### FN+Right proves visual effect state is independent of the 6-byte B7 LampLight payload

After the isolated 0x37 test had left the controller visually in a solid cyan/light-blue
state with B7 payload:

\`00 FF FF 00 00 FF\`

the user restored the original-looking blue breathing effect directly on the controller
with FN+Right, without using MixDrive/KeyLinker.

A subsequent **read-only** Linux capture returned the exact same B7 payload:

\`00 FF FF 00 00 FF\`

Decoded:
- R=0
- G=255
- B=255
- packed=0x0000
- breathSwitch=0
- breathSpeed=0
- lamp brightness=255

At the same time the controller was visibly breathing.

Every BB CustomData read in the same session still returned logical length 0.

This is direct empirical proof that the current visible breathing-vs-solid distinction is
**not encoded by the six-byte B7 LampLight payload**. The exact same B7 bytes can correspond
to at least two different visual states:
- solid cyan/light-blue
- blue/cyan breathing

Therefore the prior semantic labels \`breathSwitch\` / \`breathSpeed\` describe how the
native DLL serializes those struct fields, but they are not sufficient to identify the
controller's currently active visual effect in this G3V2 state.

Current strongest model:
- B7/0x37 = persistent base color / lamp parameters;
- active lighting effect/mode = separate firmware state;
- that state may correspond to the known CustomData LightModeIndex concept, but BB returning
  zero means it is not currently observable through this USB BB read path/session;
- FN+Right can mutate the effect state internally without altering B7.

Do NOT infer that the effect state is absent from CustomData storage globally; only that the
current BB read path does not expose a populated CustomData object.

Next high-value experiment: capture host/controller HID/USB traffic while pressing FN+Right.
If no host->device command occurs, the effect transition is entirely controller-local and
should be investigated as firmware-internal/NVM state rather than a host-side command.


### External manual corroborates FN+Right as lighting-mode control

Public G3 V2 manual mirrors describe:
- FN + D-pad Right = light mode adjustment / cycles lighting modes;
- FN + D-pad Left = lighting brightness adjustment.

This independently matches the live observation that FN+Right restored the breathing
effect without changing the B7 LampLight payload.

### Read-only USB capture prepared for FN+Right

Added \`tools/capture-g3v2-fn-light-usbmon.sh\`.

The capture:
- detects the live USB 413D:2131 device dynamically;
- reads usbmon only;
- sends no HID command;
- asks for one FN+Right press during a short capture window;
- prints all matching G3V2 traffic;
- separately summarizes output URBs carrying host->device traffic.

Interpretation target:
- if FN+Right changes the visible effect and zero host->device payload appears, the mode
  transition is controller-local rather than initiated by software on the host;
- if output traffic appears, inspect it before making any conclusion.


### Correction: FN+Right usbmon test had already been completed

A prior live usbmon capture during FN+Right was recovered from project history and makes
the newly proposed duplicate capture unnecessary.

Observed in that earlier capture:
- 15,634 USB transfers observed;
- 7,817 received packets;
- **0 OUT / host-to-device transfers** during the FN+Right lighting-mode change;
- recurring input-side patterns included \`00140400\` / \`00140800\`;
- no RGB/configuration command was emitted by the host.

Combined with the newer observation that FN+Right changes solid -> breathing while B7
remains byte-identical (\`00 FF FF 00 00 FF\`), the evidence now strongly supports:

- FN+Right lighting-mode switching is handled locally by controller firmware;
- the active visual mode is independent of the six-byte B7 LampLight payload;
- no host-originated USB command is required for that mode transition.

Do not repeat the usbmon FN+Right experiment unless a new hypothesis specifically requires
a different transport/interface or higher-resolution timing.


## Lighting investigation guardrail — do not repeat closed experiments

The following live experiments are already closed and should not be proposed again unless
a new hypothesis specifically requires a different transport or measurement:

- FN+Right cycles the controller through 13 physical lighting effects.
- Passive USB capture during FN+Right showed no host-to-device OUT transfer associated
  with the lighting-mode change; the transition is controller-local.
- B7/BB fingerprints were compared across physical FN+Right lighting-mode changes and did
  not expose the active effect state.
- The same six-byte B7 payload can correspond to both solid and breathing visual output.
- Current USB BB reads can return zero-length CustomData while physical effect switching
  still works.
- The documented 0x37 LampLight path is persistent across a real power-cycle and therefore
  must not be treated as a safe realtime Ambilight transport.
- MixDrive/KeyLinker static analysis already mapped normal exposed lighting writes; no
  exposed preview/realtime RGB writer was found.

Previously identified but NOT executed live:
- \`0x1B = CODE_TEST_SET_MODE\`
- \`0x1C = CODE_TEST_NORMAL_MODE\`
- corresponding test-mode infrastructure includes \`enterDeviceTestMode\` /
  \`exitDeviceTestMode\`.

These test-mode commands were intentionally not sent to the G3V2 because their arguments
and side effects were not sufficiently understood, and there is no proof that they emulate
FN+Right or provide a volatile RGB path.

### Current first unresolved lighting target

Stop doing state/fingerprint experiments. The first useful unresolved target is offline
reverse engineering aimed at locating an internal/undocumented lighting-mode or volatile
LED path:

1. finish/corroborate all callers and dispatch context around the common KeyLinker transform
   helper previously labeled \`FUN_180001080\`, especially any caller not already assigned
   to known read/write command families;
2. inspect the KeyLinker 3.73/APK/native libraries for G3V2-specific lighting/test-mode
   handlers, mode tables and any command adjacent to the 13 firmware-local FN+Right
   effects;
3. fully disassemble \`enterDeviceTestMode\` / \`exitDeviceTestMode\` and determine exact
   framing/arguments before considering any live test of 0x1B/0x1C;
4. only after a candidate command is statically understood should another live probe be
   considered.

This section exists specifically to prevent the project from looping back through already
completed usbmon/B7/BB/FN+Right experiments.


### Verification correction: several "next" offline tasks were already completed

A review of recovered project artifacts and prior analysis confirms that some work recently
listed as pending had already been performed:

**Already completed**
- \`FUN_180001080\` was fully analyzed statically in Ghidra:
  - body \`0x180001080..0x1800011B3\`, 308 bytes;
  - signature recovered as \`byte * __fastcall FUN_180001080(byte *, int)\`;
  - pseudocode/assembly/def-use were captured;
  - it was established as the common KeyLinker frame transform used by many read/write
    builders, including CustomData and other command families.
- Its call graph was also inspected; recovered callers include, among others,
  \`FUN_180004820\`, \`exitDeviceTestMode\`, \`readDeviceAfterKeyChangeList\`,
  \`writeDeviceCustomData\`, and other protocol builders.
- KeyLinker Android APK 3.73 (\`versionCode 373\`, package
  \`com.pulsenet.inputset\`) was extracted and statically inspected with JADX/REA.
- The Android lighting path was already traced:
  \`HostActivity.O9()\` -> \`b1.A1()\` / \`b1.z1()\` -> \`e.l0()\` -> opcode \`0x37\`
  -> queue/write path -> GATT characteristic \`d7f010e1\`; notify uses
  \`d7f010e2\`.
- That APK analysis found no second exposed RGB-live writer; the normal RGB path converges
  on persistent command \`0x37\`.

**Only partially completed / still genuinely unresolved**
- \`exitDeviceTestMode\` is present at \`0x180004920..0x1800049D3\` and was identified as a
  caller of \`FUN_180001080\`.
- A neighboring unnamed function \`FUN_180004820\` at
  \`0x180004820..0x1800048F0\` was also recovered and calls the same transform; prior
  discussion associated this region with enter-test-mode infrastructure, but the recovered
  evidence does not justify renaming it conclusively to \`enterDeviceTestMode\`.
- \`0x1B = CODE_TEST_SET_MODE\` and \`0x1C = CODE_TEST_NORMAL_MODE\` were identified as
  protocol constants/names, but recovered evidence does **not** yet prove their exact
  binding to the two functions above, their argument layout, or that either command maps to
  FN+Right lighting modes.
- No live 0x1B/0x1C test was performed.

Therefore the project must not repeat:
- generic analysis of \`FUN_180001080\`;
- generic APK 3.73/JADX lighting-path analysis;
- searches for an already-known alternate Android RGB writer.

The remaining offline target is much narrower: resolve the exact semantics/framing of
\`FUN_180004820\`, \`exitDeviceTestMode\`, and the 0x1B/0x1C command family, and determine
whether the still-unassigned write path around \`0x180004EA9\` belongs to key-change/test
mode rather than lighting.


### Test-mode framing closed; 0x180004EA9 assigned to key-change writer

Focused static audit of `KeyLinkerlib.dll` with
`tools/dump-keylinker-test-mode.py` closed the previously unresolved test-mode area.

Confirmed exports:
- `enterDeviceTestlevelMode` @ `0x180004900`
- `exitDeviceTestMode` @ `0x180004920`
- `keyTestBeforeEffect` @ `0x1800049E0`
- `keyTestAfterEffect` @ `0x180004A00`

The unnamed builder `FUN_180004820` is conclusively the shared builder used by
`enterDeviceTestlevelMode`, `keyTestBeforeEffect`, and `keyTestAfterEffect`.

Its plain pre-transform frame is exactly 9 bytes:
- byte 0 = `0x1B` = CODE_TEST_SET_MODE
- byte 1 = `0x09`
- byte 2 = KeyLinker state byte
- byte 3 = random byte
- byte 4 = `0x03`
- byte 5 = `0xDF`
- byte 6 = `0xAB`
- byte 7 = mode/effect argument supplied in EDX/SIL
- byte 8 = checksum/derived byte
- then `KeyLinkerEncode(..., 9)`

Callers prove two concrete mode arguments:
- `keyTestBeforeEffect` calls the builder with EDX=0
- `keyTestAfterEffect` calls the builder with EDX=1

`enterDeviceTestlevelMode` forwards the caller-provided EDX value unchanged to the same
builder.

`exitDeviceTestMode` independently constructs an 8-byte plain frame:
- byte 0 = `0x1C` = CODE_TEST_NORMAL_MODE
- byte 1 = `0x08`
- byte 2 = KeyLinker state byte
- byte 3 = random byte
- byte 4 = `0x02`
- byte 5 = `0xDF`
- byte 6 = `0xAC`
- byte 7 = checksum/derived byte
- then `KeyLinkerEncode(..., 8)`

Therefore the exact binding of 0x1B/0x1C to the test-mode functions and their frame lengths
is no longer unresolved.

The previously unassigned site `0x180004EA9` is also closed: it lies inside the exported
`writeDeviceKeyChangeData` routine beginning at `0x180004E00`, where it calls
`HeaderBuilder` with command `0x36`. It is part of the fragmented key-change write path,
not a lighting/test-mode writer.

Lighting implications:
- 0x1B/0x1C are now statically understood as test-mode control frames, not evidence of an
  undiscovered RGB writer.
- No evidence in this audit links their mode byte to the 13 firmware-local FN+Right lighting
  effects.
- Do not run 0x1B/0x1C live merely to search for lighting behavior; there is still no static
  evidence that they are a volatile RGB path.
- The remaining lighting target is outside this closed region: locate a genuinely separate
  undocumented LED/effect transport or firmware-internal handler, rather than revisiting
  B7/BB/FN+Right/0x37/test-mode framing.


### 0x37 timing test and BLE/X20 Pro family clue

A controlled USB timing test from the same G3 V2 unit added useful empirical data without
changing any previously established safety conclusion.

Observed:
- B7 read baseline: about 4–5 ms median;
- four isolated 0x37 writes: 3.84 / 9.94 / 7.95 / 9.97 ms to first ACK;
- median 0x37 write-to-ACK: 8.95 ms;
- immediate B7 readback after each write: 4.01 / 30.00 / 6.00 / 4.01 ms;
- final B7 state matched the original exactly.

Interpretation:
- 0x37 writes often take roughly one additional USB polling interval compared with B7 reads;
- one post-write B7 read took 30 ms;
- this is compatible with extra controller-side work after a 0x37 write, but it does NOT
  prove that flash/NVM programming occurs synchronously before ACK;
- no flash-endurance estimate should be promoted to a confirmed number because the
  ZC32RF1M endurance and the firmware's persistence implementation remain unknown.

The timing test must not be repeated merely to gather more samples unless a new hypothesis
requires it.

External KeyLinker-family corroboration was also reviewed in AmjadAAYD/x20ctl current main.
The X20 Pro implementation uses the same KeyLinker GATT service:
- service d7f010e0-660d-46e9-96c3-19c4148bdab5
- write d7f010e1-660d-46e9-96c3-19c4148bdab5
- notify d7f010e2-660d-46e9-96c3-19c4148bdab5

Its BLE transport sends the encoded KeyLinker frame directly to the write characteristic
without the USB-only 80 F9 outer envelope and receives encoded replies by notification.
This independently corroborates the transport model already inferred from the G3 V2 APK.

Important model difference:
- X20 Pro lighting is a 24-byte logical record read through B7 in two pieces and written
  through 0x37 in two chunks;
- the G3 V2 USB B7 path currently exposes a six-byte LampLight object;
- therefore the X20 Pro ambient implementation cannot be copied byte-for-byte to G3 V2.

The X20 Pro code nevertheless creates a genuinely new research lead: command 0x37 is a
model-dependent multipart lighting-record writer in at least one related KeyLinker device,
and current x20ctl main contains software-generated BLE lighting effects / screen ambient
streaming using it. This is family corroboration only, not proof of a volatile or wear-safe
G3 V2 path.

Next safe live target:
1. enumerate the G3V2 APP BLE GATT service without application writes;
2. if present, replay only already-known read requests over BLE and confirm direct
   KeyLinker-frame transport and B7 record shape/latency;
3. do not send 0x37 over BLE until read-only behavior is confirmed and the G3 V2-specific
   persistence risk is still explicitly accounted for.


### First live BLE enumeration attempt: advertisement confirmed, GATT connect timed out

The first execution of `tools/g3v2-ble-probe-v1.py --enumerate-only` produced a new
transport-level result without sending any KeyLinker application frame.

Confirmed from the live scan:
- exactly one `G3V2 APP` candidate was seen;
- RSSI was approximately -68 dBm;
- the advertisement explicitly included both:
  - `0000ff12-0000-1000-8000-00805f9b34fb`
  - `d7f010e0-660d-46e9-96c3-19c4148bdab5`
- therefore the controller really advertises the expected KeyLinker configuration service
  while operating wirelessly.

The subsequent Bleak/BlueZ connection attempt ended with `TimeoutError` before GATT
enumeration completed. Because v1 did not distinguish LE-link timeout from BlueZ service
resolution timeout, no conclusion about characteristic accessibility can yet be drawn.

No application write, notification subscription, pairing, 0x37, 0x3B, OTA or SCSI command
was sent during this failed run.

A new read-only diagnostic revision, `tools/g3v2-ble-probe-v2.py`, was added. It:
- retries the BLE/GATT connection up to three times;
- refreshes the BLEDevice advertisement between attempts;
- reports each failure stage;
- queries `bluetoothctl info` read-only after repeated failure to reveal whether BlueZ is
  still holding a connection;
- never disconnects a pre-existing BlueZ connection automatically;
- preserves the same read-only opcode guard and `--enumerate-only` behavior.

This is a targeted follow-up to the first failed connection, not a repeat of the already
closed USB lighting experiments.


### BLE v2 result: advertisement refresh works; BlueZ drops temporary device object after failed connect

Second live run with `g3v2-ble-probe-v2.py --enumerate-only` confirmed:
- one `G3V2 APP` candidate was repeatedly rediscovered;
- RSSI was approximately -64 dBm;
- all three BLE/GATT connection attempts timed out;
- advertisement refresh succeeded between attempts;
- after the final failure, `bluetoothctl info` reported the device object as not available.

The second scan did not include service UUIDs in the final advertisement snapshot, but this
does not contradict the previous run where the exact KeyLinker service was advertised:
BLE advertisements and scan responses are not guaranteed to contain identical fields on
every received packet.

The official G3 V2 manual states that Keylinker should find and connect to `G3V2_APP` with
the controller started in any mode and without ordinary Bluetooth pairing. Therefore no
special pairing mode is currently required by the evidence.

The next targeted Linux/BlueZ hypothesis is whether the unpaired temporary Device1 object
or the controller's connectability depends on discovery remaining active. This is motivated
by current Bleak/BlueZ behavior where a device object may disappear when discovery stops,
and by recent BlueZ reports of LE peripherals that fail to connect unless discovery remains
active.

Added `tools/g3v2-ble-probe-v3.py`:
- keeps active BLE discovery running through the entire connection attempt;
- uses the freshest BLEDevice object observed by that still-active scan;
- on failure queries `bluetoothctl info` before stopping discovery;
- remains enumerate-only safe: no notify subscription, pairing, KeyLinker frame, 0x37,
  0x3B, OTA or SCSI command is sent.


### G3 V2 BLE v3 — active discovery does not resolve connect timeout

Observed in a live `tools/g3v2-ble-probe-v3.py --enumerate-only` run:
- one `G3V2 APP` advertisement was tracked during continuously active discovery;
- RSSI approximately -74 dBm during the probe, -78 dBm in BlueZ diagnostics;
- three 20-second `BleakClient.connect()` attempts all ended with `TimeoutError`;
- *before stopping discovery*, `bluetoothctl info` showed the unpaired public-address Device object, `Connected: no`, `Blocked: no`, and `AdvertisingFlags: 02`;
- `uuids=[]` in this scan does not cancel the earlier advertisement where KeyLinker service `d7f010e0` was explicitly present; advertisement payloads vary;
- no GATT characteristics or ATT exchanges were observed, and no application-level KeyLinker requests were sent.

**Resolved/closed hypothesis:** keeping BlueZ discovery active does **not** by itself fix the G3 V2 connection. Do not run another general scan/timeout retry as the next experiment.

**Remaining diagnostic question:** does Linux issue HCI LE Create Connection, and what controller HCI status / BlueZ management outcome follows? A bounded `btmon` capture during exactly one read-only v3 enumeration attempt is the next step. Decode error/status codes before proposing pairing, kernel changes, or further BLE behavior. Raw btmon files may contain device addresses; keep them local and redact before posting or committing.


### HCI capture: MGMT Connect Failed on BR/EDR address type

A bounded live `btmon` capture around one v3 `--enumerate-only` attempt yielded a
more specific failure than the Bleak `TimeoutError`:

```text
@ MGMT Event: Connect Failed (0x000d)
    BR/EDR Address: [redacted]
    Status: Connect Failed (0x04)
```

The BlueZ MGMT protocol defines `Connect Failed (0x04)` as a management-layer status,
**not** the Bluetooth HCI `0x04 Page Timeout` reason. Importantly, the event labels
the address type `BR/EDR` (management address type 0), whereas the intended KeyLinker
configuration transport is BLE (LE Public=1, LE Random=2).

This is a new, unverified **wrong-bearer hypothesis**: the connect request may have
selected classic BR/EDR despite seeing `G3V2 APP` via BLE advertising. Do not assume
the MGMT event alone establishes exactly which HCI connection command was issued.

The earlier extraction regex selected the MGMT failure event but did **not** show the
preceding HCI commands or the lower-layer HCI status. **Next step: inspect the already
saved local `/tmp/g3v2-hci-raw.txt` for HCI `Create Connection` vs
`LE [Extended] Create Connection`, Command Status, Connection Complete and related
Bluetoothd messages, without repeating any live Bluetooth probe or making system changes.**


### HCI root cause of tested BLE connect attempt: BR/EDR Page Timeout (confirmed)

Full inspection of the *already captured* `/tmp/g3v2-hci-raw.txt` confirmed
the HCI command and failure (times from the capture):

```text
23:15:55.172894  HCI Command: Create Connection (0x01|0x0005)
23:15:55.175138  HCI Event: Command Status — Success (0x00)
23:16:00.295198  HCI Event: Connect Complete (0x03)
                 Status: Page Timeout (0x04)
23:16:00.295226  MGMT Event: Connect Failed (0x000d)
                 BR/EDR Address, Status: Connect Failed (0x04)
```

**Confirmed for this one captured attempt:** BlueZ/controller initiated a **BR/EDR
(classic) Create Connection**, and its HCI result was **Page Timeout**. This is not
an LE Create Connection attempt. The MGMT status code (also `0x04`) is a separate
namespace from HCI Page Timeout.

This is stronger evidence than the earlier bearer-selection hypothesis: a wrong-bearer
connection was actually attempted. The cause of BlueZ selecting BR/EDR is not yet proven;
avoid stating that controller firmware or GATT/KeyLinker is defective.

Vendor documentation/previous APK analysis indicates the G3V2 APP configuration channel
uses BLE KeyLinker GATT. BlueZ's Device1.Connect may select between LE and BR/EDR for
dual-mode identities according to bearer selection/last-used/last-seen rules.

**Next experiment (not yet executed):** use the official BlueZ `bluetoothctl`
`menu le` -> `connect <device>` command while an LE advertisement is active.
This explicitly requests LE, without changing OS installation, kernel, firmware, or
pairing/bonding. It is not the same as merely changing the scan transport. If the
installed BlueZ lacks the LE submenu, check whether `bearer <dev> le` is exposed;
that property is experimental and only available for dual-mode devices. Do not disable
BR/EDR globally or change daemon startup settings as a first workaround.

The success criterion is establishing an LE link and enumerating GATT service
`d7f010e0`; read requests and lighting writes remain deferred until that happens.
Retain raw HCI logs locally, redact addresses before publishing.


### BLE LE submenu: interface unavailable; malformed/misinterpreted advertisement flags hypothesis

The user initiated an LE-only scan with `bluetoothctl`, discovered `G3V2 APP`,
then ran `menu le` -> `connect <controller>`. The command returned
`org.bluez.Bearer.LE1 is not available on <controller>`.

The BlueZ `bluetoothctl` source checks for a live D-Bus proxy implementing
`org.bluez.Bearer.LE1` on the Device object and prints this exact message
when it is missing. **The explicit LE command did not initiate an LE radio
connection; this is not another HCI timeout.** The existence of the LE submenu
alone does not prove the optional/experimental LE bearer D-Bus interface
is exposed on that device.

A stronger explanation has emerged for the earlier unwanted BR/EDR attempt.
The G3V2's `bluetoothctl info` showed:
- `AddressType = public` (from the Device display)
- `AdvertisingFlags = 0x02` (LE General Discoverable, but without bit 0x04
  BR/EDR Not Supported)

Current BlueZ `btd_adapter_device_found` source infers BR/EDR support for
a discovered **LE public** address whose nonzero advertising flags omit the
`EIR_BREDR_UNSUP` bit. In this situation it may attempt classic SDP/ACL
connection; our actual HCI trace confirmed classic `Create Connection` and
`Page Timeout`.

BlueZ upstream commit `9965f36c6318e3a6f9879f5b0d842791a290e3ee`
(2026-07-20) fixed analogous **LE random address** false BR/EDR inference,
but explicitly preserves inference for **LE public addresses**, so it is
not a proven fix for the G3V2 case.

The hypothesis of incorrectly inferred dual-mode capability is now
strongly supported, **not yet confirmed** as the sole reason BLE is
unreachable. We still lack explicit evidence of a successful LE connection,
the exact GATT service map, and whether the firmware intentionally supports
BR/EDR or incorrectly advertises flags.

Next strictly non-mutating check with the existing `bluetoothctl` session:
`menu le` -> `list`, `show <device>`; `back` -> `info <device>`,
`bearer <device>` (query only), then `scan off`, `quit`.
Do not perform another ordinary `connect`, enable bluetoothd experimental
mode, modify the immutable base, pair/bond, or write 0x37/0x3B.


### 2026-11-20 — Device1 present, LE1 absent and PreferredBearer query empty (live)

A new interactive `bluetoothctl` `scan le` found the same `G3V2 APP` again.
The LE submenu explicitly returned **no devices** for `list` and
`LE bearer not found on <controller>` for `show <controller>`. Its
`connect` had earlier reported `org.bluez.Bearer.LE1 is not available`.
In the main menu `info` displayed the device as `public`, `Connected: no`,
and `AdvertisingFlags: 02`. The query `bearer <controller>`, without a new
value, printed **nothing**.

Source checked: BlueZ `client/main.c` `cmd_bearer` only calls
`print_property(proxy, "PreferredBearer")` on an omitted value. No
output is consistent with this optional experimental D-Bus property being
absent, not evidence that the preferred bearer is already `le`.
BlueZ `doc/org.bluez.Device.rst` confirms the property is optional and
experimental. As a result, **do not assume** `bearer <controller> le`
will work, and **do not** interpret `menu le` being present as proof the
LE1 object is registered.

The normal BlueZ D-Bus route currently cannot force LE for this device
using the exposed interfaces. The wrong-bearer evidence and public-address /
flags 0x02 correlation remain strong; the BLE peripheral itself has not
yet been tested with an explicitly LE-address-typed link.

Next safe path: **check only local availability** of an LE-public direct
client (`btgatt-client` or legacy `gatttool`) and the Python version /
`socket.BDADDR_LE_PUBLIC` support. `btgatt-client` source establishes
an ATT (L2CAP CID 4) socket with explicit `BDADDR_LE_PUBLIC`, bypassing
BlueZ Device1 automatic bearer choice; Python 3.14+ also supports the typed
L2CAP socket address tuple. If suitable tooling exists, attempt **one**
bounded LE ATT socket connection with no KeyLinker writes, without pairing,
disabling BR/EDR globally, or editing the immutable OS base. No device MAC
is published.


### Direct Linux LE L2CAP/ATT probe is available; not yet run on hardware

The user's Bazzite installation reported:
- `python3` version **3.15.2**;
- `socket.AF_BLUETOOTH=31`, `socket.BTPROTO_L2CAP=0`,
  `socket.BDADDR_LE_PUBLIC=1`;
- no `btgatt-client` or `gatttool` in `PATH`;
- `rpm -q bluez bluez-deprecated` did not find those RPM packages,
  despite a working BlueZ daemon/`bluetoothctl`. **This does not prove
  BlueZ is absent** (Bazzite may provide it through a different package
  management/layout route).

Python 3.14+ officially supports the L2CAP destination tuple
`(bdaddr, psm, cid, bdaddr_type)`, enabling direct, address-typed LE
connection attempts. The BlueZ reference client `btgatt-client` opens
`SOCK_SEQPACKET/BTPROTO_L2CAP` with ATT fixed `CID=4`,
`BDADDR_LE_PUBLIC` for a public-address BLE device.

Added `tools/g3v2-le-att-connect-readonly.py`: asks for MAC interactively
(never hardcodes or writes the address to GitHub), binds an ATT CID=4 socket,
attempts **one** direct LE Public connect with a 12s default timeout,
prints success or errno, then closes. **No ATT/GATT request data, no
KeyLinker packet, no pairing request, no write to controller settings**.

This tool is a new transport-path test, not a proven solution; **actual
socket permission, kernel behavior and G3V2 LE connectability still require
the user's single live run**. Do not call the result confirmed until captured.


### DIRECT LE connection validated on real G3 V2: 462.9 ms (2026-11-20)

**Major confirmed milestone:** The user ran `tools/g3v2-le-att-connect-readonly.py`
on Bazzite KDE (Python 3.15.2) and successfully connected to the live
`G3V2 APP` via a direct Linux L2CAP fixed ATT channel:

```text
SUCESSO: conexão L2CAP/ATT estabelecida em 462.9 ms
Destino: CID=4, tipo=1 (LE Public=1)
Nenhuma chamada send/sendall, recv ou alteração GATT realizada.
```

This confirms **the device accepts an actual LE Public link from the laptop
without BLE pairing**. It overturns any claim that the device cannot connect
over LE on Bazzite; the repeated Bleak/`bluetoothctl` failures were specific
to those BlueZ automatic-bearer/interface paths, while direct, explicitly
typed LE ATT sockets work.

What is **not yet proven**: access to the remote GATT attribute database,
the location/properties of KeyLinker e0/e1/e2 services/characteristics,
successful KeyLinker read responses, safe high-rate LED streaming, flash
endurance, or sustained 2.4 GHz/BLE coexistence. The direct LE test performed
no ATT application data exchange.

**Next tool added:** `tools/g3v2-le-gatt-discover-readonly.py`. It reuses
the proven `SOCK_SEQPACKET/BTPROTO_L2CAP`, LE Public, CID=4 connection and
sends exclusively ATT read-only discovery requests:
- `0x10` Read By Group Type for primary services (`0x2800`);
- `0x08` Read By Type for characteristic declarations (`0x2803`), **only**
  within a discovered KeyLinker service `d7f010e0`.
The tool decodes standard and Bluetooth little-octet-order 128-bit UUIDs,
prints attribute handles and characteristic properties; it never writes an
attribute value, enables notify, sends any KeyLinker `0x37` or `0x3B`,
pairs/bonds, or retries a failed connection. It prompts for the device
address at runtime; no personal Bluetooth address is committed.

Script syntax and UUID/response-record smoke tests were completed locally.
**No real GATT service discovery has yet been executed**; await user's output.


### G3V2 BLE GATT verified on real hardware: KeyLinker service and handles (2026-11-20)

**Confirmed physical measurement** using `tools/g3v2-le-gatt-discover-readonly.py`
over the direct Linux LE Public ATT CID=4 socket:
- LE connection established in **459.9 ms**, no pairing;
- exactly **two primary services** were enumerated:
  - `0x0005–0x000A`: `d7f010e0-660d-46e9-96c3-19c4148bdab5` (KeyLinker);
  - `0x000B–0x0012`: `0xFF12` (do not assume purpose without further evidence);
- **KeyLinker characteristic declarations** in service `0x0005–0x000A`:
  - declaration `0x0006`, **value handle `0x0007`**,
    `d7f010e1-660d-46e9-96c3-19c4148bdab5`, property `0x08` (Write);
  - declaration `0x0008`, **value handle `0x0009`**,
    `d7f010e2-660d-46e9-96c3-19c4148bdab5`, property `0x10` (Notify).

This conclusively proves accessible BLE/GATT service discovery and verifies the
exact KeyLinker service/characteristics previously reconstructed from the app;
prior Bleak/`bluetoothctl` timeouts were transport-selection/API failures,
not an inability to access the controller's GATT database from Bazzite.
The run issued **only ATT 0x10/0x08 discovery requests**. No KeyLinker
application-level request or BLE characteristic/descriptor write was sent.

The only remaining handle in the KeyLinker service, `0x000A`, **might**
be the Client Characteristic Configuration Descriptor (`0x2902`) for
the Notify e2 characteristic. **It has not yet been discovered or verified**:
do not infer a CCCD from a remaining handle. A current BlueZ 2026 issue
highlights the risk of synthesized/unverified CCCDs.

**Next guarded tool published:** `tools/g3v2-le-keylinker-b7-readonly.py`
(commit 2aca8d2). It independently:
1. re-discovers the KeyLinker service and e1/e2 properties;
2. verifies the real CCCD with ATT Find Information (`0x04`) and reads
   its value with ATT Read (`0x0A`); `--inspect-only` stops here with
   absolutely no ATT writes;
3. optional `--b7`: enables e2 notification by ATT Write Request (`0x12`)
   to the **verified CCCD only**, sends exactly one preexisting KeyLinker
   **read opcode `B7`** as the value of e1 using an ATT Write Request,
   receives the e2 ATT notification (`0x1B`), validates encoded
   KeyLinker CRC and reconstructs one non-fragmented Lamplight read result;
   attempts to restore the CCCD to its original value before disconnecting.

Important semantics: `--b7` **does send ATT writes** (to the temporary
subscription descriptor and to e1 carrying a **read-only query**). This is
*not* an application-level lighting writer (no `0x37`), nor OTA/SCSI.
It is guarded to only one B7 request and no repeats; no writes occur in
`--inspect-only`. Neither mode has yet been executed on hardware.


### G3V2 actual CCCD identified, but returns nonstandard 1-byte value (B7 not sent)

The first live execution of `tools/g3v2-le-keylinker-b7-readonly.py --b7`
connected via direct LE Public in **665.0 ms** and re-confirmed both
KeyLinker characteristics:
- `e1` ATT value handle `0x0007` (write property `0x08`);
- `e2` ATT value handle `0x0009` (notify property `0x10`).

**New physical finding:** ATT Find Information explicitly identifies
descriptor handle **`0x000A` as UUID `0x2902` (CCCD)**. This is
not inferred from its handle number.

ATT Read Request for that descriptor returned **`0B 01`**: a valid
ATT Read Response opcode (`0B`) with **one byte of value `01`**.
The Bluetooth Core GATT CCCD definition specifies a **two-octet**
value, so the device's returned value is nonstandard/truncated. The
original script stopped before sending any ATT Write Request or
application KeyLinker B7 query. No CCCD configuration, lighting,
firmware, pairing, OTA or SCSI operation was performed.

Do not interpret `01` as proof the notify subscription is active:
although bit 0 corresponds to notification configuration in a normal
two-byte CCCD, this nonstandard response's actual semantics are
still unverified. Do not blindly send `01 00` to the CCCD.

**Follow-up script:** `tools/g3v2-le-keylinker-b7-v2.py` commit
`d491755`. It tolerates precisely one- or two-byte CCCD read values
and logs the anomaly. For `--b7`, only when the value is exactly
`01` or `01 00`, it makes **one** guarded ATT Write Request to the
verified `e1` value handle containing a CRC-checked, encoded
KeyLinker *read opcode* `B7`; it waits for an `e2` notification
without any CCCD writes, retries or session-initialization sequence.
The CCCD is never modified. If no notification arrives, it reports
the timeout without changing the subscription. No physical B7
response has been measured yet.

New CLI option `--address` allows the user to provide the known
controller MAC explicitly on the command line; keep that MAC off
public GitHub files and documentation. Earlier B7 probe is retained
for reproducibility but should not be rerun without understanding
the one-byte CCCD behavior.


### BLE B7 first ATT transport acceptance but no notification (2026-11-20)

User executed `tools/g3v2-le-keylinker-b7-v2.py --b7` using explicit
LE Public L2CAP CID 4 on the live G3 V2; connection succeeded in
**398.6 ms**.

GATT primary service and e1/e2 characteristic declarations were unchanged;
verified CCCD handle `0x000A` again identified UUID `0x2902` and its
ATT Read Response was `0B 01` (1 value byte `01`, rather than
standard 2-octet CCCD encoding).

A **single B7 KeyLinker read** was encoded from cold session state:
- plain: `B7 06 00 29 00 A4`
- wire:  `CC B3 40 70 99 15`
- written to e1 ATT value handle `0x0007` via ATT Write Request;
- ATT Write Response `13` was received (transport-level acceptance);
- **no ATT Handle Value Notification arrived at e2** before timeout.

No CCCD write, lighting configuration write, 0x37, 0x3B,
OTA, SCSI, pairing or request retry occurred. **Do not claim
KeyLinker accepted the command or that BLE read works**: only the
GATT write was acknowledged. Lack of response is consistent with
a missing effective notify subscription OR model/session initialization
requirements; neither explanation is confirmed.

Bluetooth Core GATT specification defines CCCD as a two-octet
per-client configuration field and enables notify with `01 00`.
The observed one-byte `01` cannot be treated as proof the server
activated notifications, especially before an explicit client write.

New targeted tool `tools/g3v2-le-b7-subscribe-v3.py`
(final commit `157cac8`) does the following on one connection:
- re-discovers the KeyLinker service/e1/e2 characteristic and CCCD;
- verifies the original CCCD value is the previously observed
  `01` or canonical `01 00`; refuses any other initial state;
- sends **one ATT Write Request** to the verified CCCD with the
  standard notify-enable value `01 00` (not a persistent lighting write);
- checks the ATT Write Response and re-reads the CCCD;
- only if readback is `01` or `01 00`, submits a **single guarded
  B7 read request** on the verified e1 and listens on e2, without
  retries or firmware/configuration mutations.

The canonical CCCD write is a connection-scoped subscription. This
script does not explicitly restore the original one-byte encoding;
it closes the LE socket on exit. Neither successful notification
subscription nor B7 response has been tested yet. The MAC is
provided at runtime and intentionally not stored in GitHub.


### 2026-11-20 — CCCD explicitly subscribed; B7 transport ACK but still no e2 notification

Physical execution of `tools/g3v2-le-b7-subscribe-v3.py`:
- direct LE Public connect **639.3 ms**;
- KeyLinker service e0 and characteristic handles confirmed unchanged:
  e1 Write = `0x0007`, e2 Notify = `0x0009`, CCCD `0x2902` = `0x000A`;
- CCCD ATT read before subscription = `0B 01` (one-byte value `01`);
- explicit **standard `01 00` CCCD subscription via ATT Write Request** returned
  ATT Write Response (transport ACK);
- CCCD ATT read after subscription was **still `0B 01`**;
- exactly one native `B7` read frame
  `B7 06 00 29 00 A4` encoded by the existing **USB/MixDrive codec** to
  `CC B3 40 70 99 15`, sent on e1, produced ATT Write Response;
- **no e2 notification** before the timeout, even after explicit subscription.

**New conclusion:** a missing explicit standard CCCD write is *not by itself*
enough to explain the observed B7 silence. The descriptor's abnormal
single-byte `01` readback persists across the write; ATT ACK does not
prove the server internally honors notifications. Session initialization,
BLE-specific frame encryption, or firmware filtering remain possible.
Do not rerun B7 with the same encoded bytes/CCCD sequence.

**Potential protocol incompatibility discovered by offline comparison:**
The EasySMX X20 Pro's KeyLinker Android-app-derived `CodeHelper.encrypt`
as independently reproduced in
`AmjadAAYD/x20ctl/x20ctl/protocol.py` does **not** use the same scramble
operation as the G3V2 Windows MixDrive's `KeyLinkerlib.dll`
(`tools/g3v2-warmup-readonly.py`).

For the **same plaintext frame** `B7 06 00 29 00 A4`, CRC included:
- our verified **Windows USB/MixDrive** codec -> `CC B3 40 70 99 15`;
- x20ctl's **Android KeyLinker-family** codec -> `71 A2 A2 90 2C 8E`.

The common CRC table and logical opcode do **not** prove the
scramble/cipher bytes match. We were sending the USB-verified wire
frame over BLE, but have **not yet established** which codec is used
by the *actual* G3V2 Android KeyLinker APK 3.73.

**Prioritize an offline differential inspection of the G3V2 Android
APK's `CodeHelper.encrypt`/GATT send path** against the actual
`KeyLinkerlib.dll` codec and the X20 Pro Android-derived version.
Do **not** transmit X20 Pro frames to the G3V2 without validating the
model-specific APK path first. This is a distinct explanatory hypothesis,
not a claimed root cause.

**Additional user-reported physical behavior (separate hypothesis):**
The user noticed that **manually powering the controller off and leaving
it on the charging dock for an unmeasured time** causes the most recent
lighting configuration to appear forgotten. Earlier rapid restart tests
where a 0x37-configured color survived should no longer be described
as definitive nonvolatile/flash persistence. Charging behavior might
override a transient lighting mode, extended power-off could clear
volatile RAM, or the firmware may reload saved/default profiles.
No cause or timing established. Avoid claims about flash writes per
0x37 ACK and flash wear until a controlled off+dock versus off+no-dock
comparison is performed (prefer only normal controls, no repeated
0x37 writes). Document the observed behavior, not a proven reset of
flash contents.


### Android KeyLinker base.apk located locally; APK codec verification queued

The user located the Android KeyLinker reference APK at
`keylinker-referencia/base.apk` in the local `machenike-control` project.
The several `split_config.*.apk` files are localization/density APK
splits; **start with `base.apk`** because app Java/Kotlin DEX normally
resides there. Ignore unrelated Android app APKs.

The local APK's exact package/version and actual G3V2-compatible
`CodeHelper.encrypt` implementation have **not yet been verified**. The contrasting X20 Pro
Android-derived codec is evidence of a possible BLE/USB codec split,
not proof of the correct G3V2 BLE bytes.

Published `tools/inspect-g3v2-keylinker-apk-codec.py` (commit
`8c245da`): an **offline-only** helper to hash/list DEX within
the local APK, reuse installed JADX, and print focused decompiled
Java sections for `CodeHelper`, `encrypt`/`decode`, app session
serial logic, service UUIDs e0/e1/e2, and GATT notification/writes.
It can optionally fetch the official portable JADX release to the
user's cache when absent (`--fetch-portable-jadx`). It never opens
Bluetooth, never modifies the immutable OS base, never uploads the APK
and does not commit extracted copyrighted app code to the public repo.
Wait for the actual output to decide whether to change KeyLinker wire
encoding; **do not send unverified alternate frames**.

### APK audit environment blocker resolved in script (Temurin portable; not yet executed)

The user's first offline run of `tools/inspect-g3v2-keylinker-apk-codec.py` confirmed:
- local `keylinker-referencia/base.apk`;
- SHA256 `09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee`;
- `classes.dex` size 8,002,728 bytes;
- embedded strings `d7f010e0`, `d7f010e1`, `d7f010e2`,
  `KeyLinker`, `encrypt`, `BluetoothGatt`.

The tool stopped **before decompilation** because no `java` executable
was available on PATH. Do not interpret this as missing BLE logic in the APK.

Updated the script to support `--fetch-portable-java` as well as
`--fetch-portable-jadx` (commit `27c1027`): it can obtain an official
Temurin 21 Linux x64 JRE via Eclipse Adoptium's assets API, verify the
reported SHA-256, and extract into the user's home cache, setting
`JAVA_HOME`/`PATH` for JADX only. No rpm-ostree, system base changes,
pairing or Bluetooth calls. This Java bootstrap is **not yet user-tested**.
The JADX release ZIP is also downloaded into cache, verified by GitHub
release-provided SHA-256 if available.

The APK code itself has **not yet been decompiled or compared to the USB
codec**. Do not mark any Bluetooth application-level protocol as proven.


### 2026-11-20 — Android APK JADX decompilation usable; exact BLE codec class located (not yet extracted)

User reran `tools/inspect-g3v2-keylinker-apk-codec.py` with
`--fetch-portable-java --fetch-portable-jadx`. The script obtained
the official Temurin 21.0.13+2 JRE (SHA-256 validated) and JADX 1.5.7
portably into `~/.cache/machenike-keylinker-analysis/`, without OS
package changes. The reference APK's SHA256 is
`09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee`;
it includes `classes.dex` (8,002,728 bytes).

JADX **exit code 3**: incomplete decompilation/possible warnings, but
**4,314 Java files were produced**. Focused search found obfuscated
`com/pulsenet/inputset/util/b.java`, which establishes the actual
KeyLinker Android GATT dispatch path:
- `BluetoothGattCallback.onCharacteristicChanged` takes the
  notification's raw bytes, decodes them with
  `com.pulsenet.inputset.util.e.A().g(getValue())`, delivers the
  result to the application listener, and removes its in-flight record
  using decoded byte index 2.
- Writes to the exact G3V2-compatible KeyLinker GATT characteristic
  `d7f010e1-660d-46e9-96c3-19c4148bdab5` call
  `characteristic.setValue(com.pulsenet.inputset.util.e.A().h(bArr))`,
  then `BluetoothGatt.writeCharacteristic`.
- Service discovery searches for `d7f010e2` in KeyLinker service
  `d7f010e0` and calls `K(true, characteristic)`;
  `K` registers local notifications and uses standard
  `BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE` via the
  verified 0x2902 CCCD.

**Most actionable new fact:** `com/pulsenet/inputset/util/e.java`'s
obfuscated `h()` (outgoing transform) and `g()` (incoming
transform) are the **exact Android APK codepaths** to inspect next.
Previous automated ranking did not list that file because it was
looking for unobfuscated identifiers `encrypt/decode`, not `g/h`.
This is NOT proof of any codec divergence until those method bodies
are read and test-vector compared with the known MixDrive USB cipher.
No new BLE frame has been transmitted. Continue static inspection
first; do not re-run identical B7 tests.


### 2026-11-20 — CONFIRMED Android BLE vs Windows USB KeyLinker codec mismatch; v4 ready

**Root-level protocol error in the earlier BLE probes confirmed offline:**
The user provided the complete decompiled
`com/pulsenet/inputset/util/e.java` from APK SHA256
`09408b714d319461e6a29293d2170d2e7a6e4bcd60438998cd2e6c2f100ebdee`.
The BLE writer in `b.java` first transforms the outgoing
KeyLinker bytes with `e.A().h(bArr)`, and the e2 notification is
decoded with `e.A().g(getValue())`.
The actual method bodies `h` (lines 384–409) and `g`
(lines 356–378) use **different XOR/substitution keys and arithmetic**
from the proven MixDrive/Windows USB `KeyLinkerlib.dll` implementation.

The Android APK also uses `e.K()` serial sequence
`01, 82, 03, 84...` (not the USB cold session `00`),
and `e.E()` random `nextInt(256)` (not CRT `rand()`).
`e.L()` builds a read-only identity opcode `0x91` with empty body;
`e.q(0)` builds a read-only lighting opcode `0xB7` with selector zero.
CRC table is consistent with reflected CRC8 0xEB.

**Deterministic offline illustration** (nonce fixed to `29`):
- Same logical B7 frame `B7 06 00 29 00 A4`:
  USB/MixDrive -> `CC B3 40 70 99 15`,
  Android KeyLinker -> `71 A2 A2 90 2C 8E`.
- Android identity frame `91 05 01 29 FB`
  -> `48 A0 A5 90 21`.
- Android B7 *after identity* frame `B7 06 82 29 00 96`
  -> `EF 20 24 90 A2 BC`.

Therefore, the previous BLE `B7` attempts sent the
**wrong USB-encoded packet bytes and serial** and cannot be used
to conclude the G3V2 BLE application parser rejects a correctly
encoded KeyLinker read. This is a definite host-side defect;
successful communication with the correct Android format is
**not yet demonstrated** on hardware.

Created:
- `tools/g3v2-android-keylinker-codec.py` (commit
  `a7ad96e`), a pure Python Android e.h/e.g CRC codec and serial
  generator with offline vectors.
- `tools/g3v2-le-android-identity-b7.py` (commit
  `9544e24`), an explicitly LE Public ATT probe that verifies
  actual service/e1/e2/CCCD, subscribes once with standard
  CCCD `01 00`, sends only Android-coded identity `0x91` once,
  validates e2 notification CRC and **matching serial** (opcode
  may be response `0x19`), then sends exactly one Android-coded
  read `0xB7` only after identity succeeds. No retries, no
  light/OTA config writes. This probe has **not yet been run**.

Current priorities: run Python offline selftest and syntax check,
then a single v4 direct BLE identity/B7 test. If identity still
times out, investigate Android model-specific initial transport
handshake rather than reflexively repeating B7 or reconfiguring
BlueZ. Keep the BLE MAC in local commands, out of public docs.


### 2026-11-20 — SUCCESS: full Android KeyLinker BLE read path on real G3V2

**Supersedes all earlier BLE "no reply" conclusions:** the APK-derived
codec, serial generation and 0x91-first request sequence worked.

Observed with `tools/g3v2-le-android-identity-b7.py` on Bazzite:
- direct LE Public GATT connect `505.1 ms`;
- characteristic handles e1 `0x0007`, e2 `0x0009`, CCCD `0x000A`;
- CCCD Read Response still single-byte `0B 01`; standard `01 00`
  subscribe Write Response was successful and real e2 notifications
  arrived after that;
- identity 0x91, host serial 01:
  - TX plain `91 05 01 CB 33`;
  - TX Android-coded `02 30 4A F2 C6`;
  - RX Android-coded `C8 A0 BD 88 22 F5 C0 14 A5 37 C0 E1 16`;
  - RX decoded `19 0D 01 31 07 10 06 20 11 01 48 46 B8`;
  - CRC/serial validated, 23.0 ms query RTT;
  - 7-byte logical identity `10 06 20 11 01 48 46`:
    logical VID:PID `1006:2011`, firmware `1.48`, flags `46`;
- read-only LampLight B7, host serial 82:
  - TX plain `B7 06 82 FA 00 E7`;
  - TX Android-coded `1C 71 F7 C1 93 CD`;
  - RX Android-coded `32 FB 77 41 15 B3 F3 F1 FE 40 BD 46`;
  - RX decoded `19 0C 82 7A 06 00 FF FF 00 00 FF F9`;
  - CRC/serial validated, 22.0 ms query RTT;
  - logical LampLight `00 FF FF 00 00 FF`:
    RGB=(0,255,255), packed breath/switch=0, brightness=255.
    **Identical to prior physical G3V2 USB B7 baseline.**

This is **not merely an ATT write acknowledgement**: actual native
KeyLinker `0x19` response notifications arrived from e2, decoded
with valid CRC and request-matching serial. Previous B7 silence was
caused at least by host-side use of the wrong USB codec/serial in
the Android BLE context; the exact contribution of each mismatch
was not isolated.

The successful 22–23 ms query RTTs establish read-query feasibility
for this session; they do NOT establish continuous update throughput,
LED frame rate, 2.4 GHz coexistence, or BLE write latency.

Canonical concise reference: `docs/G3V2-BLE-VERIFIED.md`. Hardware
status: Bluetooth **read path complete**. Lighting mutation/volatility
research remains open; never stream 0x37 without flash safety evidence.
The user's earlier charging-dock "forgets lighting" observation
remains a separate, unresolved persistence clue.

Updated `tools/g3v2-le-android-identity-b7.py` (commit `0fd686d`)
so future output will unpack the identity and six-byte LampLight
record correctly; **no repeat run is needed** for validation.


### 2026-11-21 — Next engineering task: trace Android lighting call sites (read-only)

The hardware now provides direct LE GATT KeyLinker read responses
(identity 0x91; LampLight B7). The Bluetooth connection/codec path is
**closed as a research blocker**. Do not repeat link discovery or B7
tests without a distinct unresolved hypothesis.

**Ambilight blocker remains**: continuously writing native lighting
opcode 0x37 is not known to be volatile/wear-safe. One power cycle
retained a 0x37 marker, but the user later observed configuration loss
after an unmeasured shutdown+charging-dock interval. This is an unresolved
persistence-mechanism question, not evidence either for or against flash
writes on each change. Do NOT stream 0x37 at frame rate.

The Android APK e.java methods supply concrete static targets:
- `e.l0(int[], int)`: request opcode decimal 55 = 0x37 (configuration lighting write);
- `e.X(byte[], int)`: request opcode decimal 37 = 0x25 (**unknown purpose**, not proven LED preview);
- `e.h0(int)`: request opcode decimal 37 = 0x25 (**unknown purpose**);
- `e.q(int)`: request opcode decimal 183 = 0xB7 (lighting read).

Published `tools/audit-keylinker-android-light-callers.py` to
cross-reference direct `e.A().l0/X/h0/q` calls in the user's **existing
JADX cache** and flag obfuscated indirect candidates, with limited
surrounding code. It sends no BLE/USB traffic and makes no source edits.
The actual call sites have **not yet been collected**. Only after
finding the call chains and any potentially distinct ephemeral command
should a guarded hardware experiment be designed.

Related x20ctl X20 Pro code uses repeated 0x37 lighting-record writes in
a persistent BLE session, but that proves neither a distinct volatile
opcode nor G3V2 flash endurance. Its behavior must not be generalized to
this device.


### 2026-11-21 — Android lighting call-site audit: 0x37 confirmed, 0x25 still unidentified

The user ran `tools/audit-keylinker-android-light-callers.py` against
142 `com/pulsenet/inputset` Java sources in the local JADX cache.

**Direct singleton `e.A()` cross-references**, distinguished from false
positives on unrelated obfuscated methods:

- `e.A().l0(int[],i)` -> native **0x37** lighting writer:
  `util/b1.java` lines **1449, 1460, 2445, 2454**.
  They appear in **two separate loops**, each partitioning its
  data into up-to-15-byte chunks before calling
  `b.P().w(...)` for BLE transport. The two routines' semantic
  differences are **not yet known**; inspect their enclosing methods,
  full payload assembly, and callers before assuming a realtime path.
- `e.A().X(byte[],i)` -> **0x25** (**decimal 37**, NOT hex 0x37):
  `util/f.java` line **107** (building a collected representation)
  and `util/b1.java` line **2055** (actual transport dispatch).
  Both have preceding 3-byte copying logic; exact purpose unproven.
- `e.A().h0(int)` -> **0x25**: `util/b1.java` line **2012**,
  within an adjacent transport sequence, purpose unproven.
- `e.A().q(i)` -> **0xB7** read: `util/b1.java` line **1791**.

The loose matcher reported many unrelated `.q()`/`.l0()` method
names from UI/other object types. **Do not interpret them as
KeyLinker opcodes**; the direct `e.A()` hits above are the
meaningful verified cross-references.

**Current conclusion:** no RAM-only preview or realtime LED opcode
has been demonstrated by this search. `0x25` is not a justified
Ambilight candidate until its payload role is established. Continue
with a targeted offline extraction of `b1.java` and `f.java`
surrounding these calls. No BLE hardware or 0x37 writing required.


### 2026-11-21 — Android G3V2 lighting writer formats reconciled; 0x25 is Turbo

The user supplied the **full target sections** from the locally
decompiled KeyLinker APK (`com/pulsenet/inputset/util/b1.java`,
`util/f.java`) after the previous call-site audit. These now settle
the two `0x37` Android caller paths:

- `b1.A1(List)`, around lines **1427–1465**, takes
  `HostMenuBean.LightingDataBean` entries, emits a leading logical
  length = number of entries × 6 and then exactly
  `lighting_r, lighting_g, lighting_b, lighting_data4,
  lighting_data5, lighting_light` per entry.
  The result is sent in slices of up to 15 integers via
  `e.A().l0(chunk, index)`, underlying command **0x37**.
- `b1.z1(ArrayList×4)`, around lines **2396–2459**, gathers up to four
  six-element lighting inputs (via helper `k`), converts each
  record's original positions 3/4/5 into
  `speed_low, packed_high, brightness` and prepends the length.
  Its local bit packing is:
  `speed_low = speed & 0xFF`;
  `packed_high = ((speed >> 8) & 0x0F) | (breath_flag ? 0x80 : 0)`.
  The app uses a 12-bit string of speed in this path.
  It then sends the same **0x37** fragments, up to 15 bytes at
  a time. The earlier Windows native
  `LampLightStruct` reconstruction agrees on the 6-byte wire
  record: `R G B packed_low packed_high brightness`.
  These are *two input/serialization paths to the same command*,
  not evidence of volatile vs persistent lighting transports.

- `b1.f1(...)`, around lines **1976–2070**, is a **bulk configuration**
  dispatch: starts with `e.Y()` (0x17),
  `e.Z()`/ `e.V()` (0x18), then `e.h0()` (0x25)
  and groups of up to 5 × 3-byte elements via `e.X()`
  (0x25), followed by more other configuration command
  families. `util/f.java` similarly packs 3-byte records
  for 0x25. This does **not** resemble the 6-byte LampLight
  format or per-frame RGB updates.

**New external family corroboration:** The reverse-engineered
KeyLinker command table in
`AmjadAAYD/x20ctl/docs/01-protocol.md` explicitly names
**0x25 = `CODE_TOOBLE_MODE` (Turbo mode)**; “TOOBLE”
is the manufacturer's Turbo spelling. This strongly
corroborates the Android 3-byte grouped bulk-config context.
This exact opcode meaning is validated by the family app's
reverse-engineering, not by a new isolated G3V2 hardware test.
`0x25` is **not** a justified LED/ambilight candidate.

**Resulting status:** The inspected Android APK contains a
confirmed RGB writer `0x37`; no separate RAM-only/preview RGB
writer has been identified in the inspected app paths.
Absence in the APK is *not proof* the firmware lacks such a
command. The next bottleneck is firmware-side persistence and
alternate LED control, not Bluetooth connection/codec/CCCD.

The timing and physical charging-dock behavior are unresolved:
the user observed a light-setting loss after being powered off
on the charging dock for some time, yet a single isolated `0x37`
marker survived a quick power-cycle. Since visible breathing mode
is not fully represented in six-byte B7, a controlled comparison
must log both **B7 RGB bytes** and **visible effect** before and
after dock, without a new high-rate write.


### 2026-11-21 — New physical LED baseline: solid green, dock-off loss affects color AND mode

The user clarified the earlier charging-dock observation: after **manually
turning the gamepad off and leaving it on the dock for several hours**
(precise duration not measured), **both the last LED color and visible
mode/effect** were forgotten. This was user-visible behavior; there is
NO B7 log from immediately before/after that earlier dock session. Do
not claim that the B7 register itself was read and proven cleared in
that earlier event. The cause remains unverified (long power-off versus
charging/dock state versus profile reload).

**Current live baseline (green solid, user visually observed):**
- direct LE Public connection: **474.9 ms**;
- KeyLinker identity response: `19 0D 01 52 07 10 06 20 11 01 48 46 3D`;
  logical VID:PID `1006:2011`, firmware **1.48**, matching serial
  and validated CRC; identity query RTT **23.0 ms**;
- B7 read response: `19 0C 82 AA 06 15 F6 05 00 00 FF 7D`;
  CRC valid, serial 0x82, B7 query RTT **21.9 ms**;
- extracted LampLight logical six-byte record
  **`15 F6 05 00 00 FF`**:
  RGB **(21,246,5)**, packed breath field 0x0000, brightness 255;
- visible effect: **solid green** (observed, cannot be inferred solely
  from the packed breath field because FN+Right has previously changed
  visible mode without changing B7).

**Next controlled physical test** (do not mutate lighting or repeat
codec/GATT discovery as a research objective):
1. Treat the above live B7 read + visible solid green as the
   pre-dock baseline. Do not send 0x37 or set a new RGB marker.
2. Manually power off, put on dock for a *recorded* period (initially
   several hours, as in the observed event). Note dock indicator and
   whether it appears to turn back on.
3. Remove from dock before assessing; power on normally, record the
   **visible color/effect first** before running any command.
4. Send one read-only identity + B7 query with the verified
   `g3v2-le-android-identity-b7.py` script; compare six bytes with
   `15 F6 05 00 00 FF`. The only ATT configuration write is the
   connection-scoped CCCD 0x2902 subscription.
5. If both color and visible mode changed, record the actual
   B7 bytes separately instead of automatically attributing the
   change to persistent flash. If B7 remains equal but the visual
   mode changes, the missing state is outside the 6-byte LampLight.
6. A later *off-without-dock* comparison can discriminate charging
   behavior from extended power-off without introducing new 0x37
   writes. Exact threshold-time search is not needed initially.

No statement is made that this experiment has yet happened.
Keep realtime/high-frequency 0x37 writes disabled until persistence
and wear-safety are understood. The current BLE MAC should not be
committed to public documentation.


### 2026-11-21 — Correction: fully charged, LEDs OFF on restart after hours on dock

**New user clarification of the earlier, uncontrolled event:**
The controller was **100% charged** when the user restarted it
after a few hours in the charging dock, and **the LEDs were entirely
off**. The planned prospective experiment has **not** yet happened.

The previous description "both the RGB color and visual effect were
forgotten" recorded the user's *impression*, not two independent
measurements. With all LEDs off, neither the stored RGB values nor
the stored/effective mode can be visually determined. There was
**no B7 read immediately after the earlier event**, so the true
state of the six-byte LampLight record is **unknown**.
Do not claim a proven reset, clearing of RGB, loss of both
settings, NVM erase, or a proven full-charge auto-off feature.

Competing hypotheses remain:
(a) illumination disabled at charge completion or dock event;
(b) firmware reloaded a default LED-off mode on startup;
(c) long power-off cleared volatile state; (d) the B7 RGB
record actually changed. No causal distinction has been made.

For a future controlled repetition, preserve the current
**solid-green B7 baseline `15 F6 05 00 00 FF`**; after the
dock/restart event, **observe the initial LED-off state without
pressing lighting hotkeys, changing settings, or making a 0x37 write**.
Then run one read-only Android BLE B7 query. If B7 is unchanged
while LEDs are off, RGB persistence can coexist with an off mode;
if B7 differs, record exact contents. Avoid setting an arbitrary
several-hour threshold. Compare with power-off *without* dock
only if the result warrants it. A full battery reading alone does
not prove the charging transition was the trigger.


### 2026-11-21 — User video: joystick-reactive RGB effect suggests internal realtime LED engine

User provided a 28.482-second VP9 WebM screen recording (not added
to the repository). In the video, one of the G3 V2 built-in lighting
effects visibly changes the illuminated joystick-ring colors as the
sticks are moved (~2–14 s); another effect shows a chase/circulating
light (~16–27 s). The recording includes a PC with an Xbox-controller
input/test page, so **host independence was not directly tested**.

Important interpretation:
- A **stored effect program/mode** and **realtime LED output** can
  coexist. The firmware may store a fixed algorithm/table in program
  flash and use live Hall-stick axes to compute changing RGB values in
  MCU RAM without per-frame nonvolatile writes.
- This is strongly suggested by the user-video response and the
  already-observed physical Fn+D-pad-Right mode changes **without any
  B7 payload change**. Manuals corroborate Fn+Right selects modes.
  But no packet capture or receiver-disconnected video yet rules out
  host participation conclusively.
- A "dynamic" color transition does **not mean each frame was
  transmitted via USB/BLE**, and does **not imply 0x37 is safe for
  streaming**.
- For Ambilight, investigate the firmware's internal effect-render
  function, LED/PWM/WS2812 driver, stick-position/mode dispatcher and
  whether an *external volatile RGB input* exists. Native joystick
  animation by itself does not expose a host-accessible realtime
  pixel interface.
- A simple reversible hardware discriminator is to activate the
  joystick-reactive lighting mode, unplug only the 2.4 GHz receiver
  (or otherwise disconnect gameplay host), then move the sticks
  while the controller stays powered. If reaction continues
  disconnected, host-per-frame RGB updates are ruled out for this
  built-in effect. If it stops, sleep/disconnect policy is an
  alternative explanation; not proof that a host streamed RGB.
  Do not use any new 0x37 writes.
- Optional separate static/read-only analysis: observe whether B7
  RGB logical bytes remain static when the joystick changes visible
  RGB. Prior Fn+Right observation already showed mode can change
  independently of B7, so this is not mandatory or urgent.

The G3V2 v1.48 FOT files have an XCOD firmware container; code
extraction/architecture remains a separate capability question.
Do not claim that program firmware can already be modified or
that volatile RGB writes have been identified.


### 2026-11-21 — FOT decrypt track explicitly prioritized at user's request

User decided to pursue recovering executable code from the encrypted
G3V2 FOT, rather than hardware memory dump or volatile-LED write
experiments. Detailed strategy: `docs/FOT-RECOVERY-PLAN.md`.

Fresh public-source comparison:
- `LopeKinz/Manba-One-V2-Firmware-tools` (ZXD2055) reports
  same-HWID X20 v1.18/v1.21 section-relative keystream-reuse
  signatures; its hardware-ID-different devices do not share
  ciphertext keystreams. This is family corroboration, not a
  ready G3V2 decryptor.
- `LopeKinz/Manba-One-V2-Firmware-tools/issues/2` (created
  2026-10-08) independently reports unsuccessful deterministic
  AES/SM4/ChaCha20/RC4/etc sweeps and notes missing public
  X20 same-ID pair. Do not duplicate generic cipher sweeps.
- Gamepadla reports physical G3 V2 firmware `1.25`
  in tests on 2025-01-28. This is a potential historical
  version lead only; same HWID and availability of updater
  remain unverified.
- The two G3V2 v1.48 FOTs **do not form a cryptanalytic
  differential pair**: XCOD and XRES are byte-identical,
  despite differing header-specific identity metadata.
- A second DIFFERENT-CODE FOT with exact
  `a1ca51da724cb6103658c05d7d4e2267` is the
  first meaningful firmware comparison target.
- The official API latest endpoint serves only v1.48.
  A small, existing *GET-only* history route script
  `tools/probe-firmware-history-api.py` was previously
  prepared but its live results have **not** been logged.
  Run that once before further archive hunting.
- The current `tools/compare-g3v2-same-hwid-fots.py`
  requires equal layouts/sizes. If a historical image
  is located, adapt the comparison to relative XCOD/XRES
  section offsets instead of interpreting layout shifts
  as changed ciphertext.

No new physical-controller interaction, OTA transition,
SCSI, firmware changes, or unsafe rewrite was performed.
Public search did not independently locate a download
of G3V2 1.25/another different-code image.



### 2026-11-21 — Historical G3V2 MixDrive API probe completed: all eight guessed GETs returned 405

The user ran `tools/probe-firmware-history-api.py --product-id 8 --firmware-id 11`.
All eight routes returned HTTP `405 Method Not Allowed` with
application/json body `{"message":"405 Method Not Allowed"}`:
`/firmware/11`, `/firmware?id=11`,
`/firmware?product_id=8`,
`/firmware/list?product_id=8`,
`/firmware/history?product_id=8`,
`/firmware/by-product-id?product_id=8`,
`/firmware/by-product?product_id=8`, and
`/firmware/all?product_id=8`.

**Interpretation:** those exact GET requests did not expose a
historical firmware list. The homogeneous 405s do **not**
establish that history does not exist, that the endpoints
exist with POST-only semantics, or that guessed alternative
methods should be tried. The already-recovered official
`/firmware/by-product-info` GET continues to be the only
verified catalog route, serving v1.48. Do not rerun the eight
GET routes without fresh evidence of a real documented API.

Targeted public-web searches and GitHub code search for
`ZXD2267`, `JRH_G3V2`, older G3V2 firmware versions
and Chinese equivalents found no verified earlier
`ZXD2267` installer/FOT. The old
`https://global.machenike.com/shoubing/download`
address mentioned in a G3V2 manual is not currently
reachable via our web access; this is not proof no archive
exists.

**Important version-domain caution:** Gamepadla's Jan 2025
G3 V2 `1.25` is explicitly labeled **XInput Firmware**.
Our updater FOT `1.48` is the vendor's ZXD2267
firmware-package version. They might refer to related
versions but their equality/domain is **unverified**.
Do not claim a firmware package version 1.25 is known
to have existed solely from Gamepadla metadata.

**Next acquisition path:** find a genuine older
ZXD2267/G3V2 updater from manufacturer/retail support
archives or an independent researcher's collection;
verify its embedded `FOT` hardware ID and actual
XCOD hash **before** concluding it is distinct.
If support is contacted, ask for historical *offline
installer for analysis only*, not for instructions
to downgrade the physical controller.

Neither the USB/BLE controller nor the Bazzite base
was accessed or modified by this historical API probe.



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



### 2026-11-21 — User Wayback audit returned no usable metadata; independent Common Crawl track prepared

User executed `tools/audit-g3v2-wayback.py --max-per-page 3`.
For each of `machenike.com/shoubing/download`,
`www.machenike.com/shoubing/download`,
`global.machenike.com/shoubing/download`, and
`global.machenike.com/download`:
- CDX returned `CDX: empty response`;
- Wayback availability-API fallback found `Selected: 0` captures.
The script concluded no **accessible archive metadata**, NOT that Wayback
had never captured those pages. No archived URL or binary was recovered.

Independent source check:
- Internet Archive availability API is reported broken/returning empty
  `archived_snapshots` by a 2026-04-18 external developer report
  (`https://github.com/browser-use/browser-harness/blob/main/agent-workspace/domain-skills/wayback-machine/scraping.md`).
  Treat this as a plausible explanation for the fallback failure,
  not proof of Machenike archival absence.
- The independent Common Crawl index `https://index.commoncrawl.org/`
  publicly lists historical 2024–2026 crawl collections.
- Direct per-URL Common Crawl archive queries were blocked by the
  assistant's web-retrieval tool, so **no Machenike Common Crawl capture
  has yet been verified**.

Added `tools/audit-g3v2-commoncrawl.py`, a bounded GET-only alternative:
representative 2024–2026 crawl indices, exact `/shoubing/`
prefix patterns, print archived URL captures and optionally fetch a
small number of historical HTML WARC records via HTTP byte range
to identify download links. The tool never downloads/executes
updater binaries and does not touch the G3 V2.

The user should run this **independent archive search once**
before further historical archive work. If empty/blocked, stop
automated archive cycling and use archived vendor installer collections,
manufacturer support, or directly available RE artifacts instead.



### 2026-11-21 — BREAKTHROUGH: new genuine same-HWID G3 V2 v1.59 image from user's Chinese OEM ZIP

User found `https://www.machenike.com/driver/download/?id=190`
and supplied downloaded `MKG_FW_G3V2.zip`.
We inspected the **uploaded bytes directly**, without executing
any executable or contacting the G3V2. The vendor download-page
URL is confirmed to be Machenike's downloads site; the exact
v1.59 offering on that page was not independently readable
in the web-rendered page content, but the supplied binary's
internal FOT metadata unambiguously confirms the G3V2.

ZIP SHA256:
`54a98a5e568989275f3b6264c518341609240e70b85a511de4b09266a5e5d9c5`
(zip size 2,403,883 bytes).

Contains:
- `MKG_FW_G3V2.exe` — **controller updater**,
  2,267,040 bytes, SHA256
  `d511557ff30542b1513ca645ebd8631a9287757d160225687e2bc0c8cf0f66d9`;
- `MKG_FW_G3V2.Receiver.exe` — **receiver updater**,
  2,267,048 bytes, SHA256
  `aa6c5e16d2297e32a9b5765040e63ed18a1bef6cb03c473589e575d7d5cff41c`;
  no `FOT\\0` string detected in that executable and its
  firmware format was not analyzed;
- two Chinese UTF-8 instructional TXT files, one with
  bilingual controller instructions. They tell the user
  to power off, hold left stick (LS) and attach via USB
  before running updater. **DO NOT perform these instructions
  for our read-only FOT analysis.**

Exact controller FOT:
- begins in `MKG_FW_G3V2.exe` at file offset `0x1712F0`;
- complete padded FOT size **0x5B400 = 373,760 bytes**;
- SHA256 `ebb611eb8d4d730e991fa1f8027d653152515c4e47903c07537a6d7fadd4e849`;
- hardware ID = **`A1CA51DA724CB6103658C05D7D4E2267`**,
  BYTE-IDENTICAL to our v1.48 official updater;
- version field `0x9F` = decimal 159 = **v1.59**.
- valid FOT metadata: DATA start `0x200`, DATA length
  `0x5A000`, directory duplicate start `0x5A200`,
  config start `0x5A400`. Header first 0x200 bytes
  **identical** to mirror at FOT +`0x5A200`.
- XOR-0x80 XCOD tag at `0x200`,
  XRES tag at `0x59600`;
- full XCOD section `0x200..0x59600` length
  **365,568 bytes** (v1.48: 361,472 bytes),
  SHA256 `65b9ed83fdcead0628ccdf4b25ba20ad8dd753c3df5ae9a73f4cc64416b04148`;
- XCOD payload excluding first 16-byte section header,
  SHA256 `fea8cb07ed98b32276aff31494260b7f84745c81881eef336fac1e7076b8f3cb`;
- full XRES section `0x59600..0x5A200`,
  length **3,072 bytes** (same length as v1.48),
  SHA256 `13f6dbea6dc1347f3a7927ef25815fe74c8568acec68f1bb61f5ed400f428bcb`;
- v1.48 full XCOD/XRES hashes, from prior verified
  comparison, respectively
  `3ddaec3bb4d3723d4d1c55392473e6830872674ce3c33f6ab401b857844821fe`
  and
  `173740c39fce50700eea71a2616afbf445feabb0217ec4bc6c3ab57919377bb9`;
- **Both sections differ by hash from v1.48**; XCOD
  length is greater by **4,096 bytes (4 KiB)**.
- 8-byte KEY record remains the same family constant
  `00000001eae32e09` — this is NOT a recovered
  decryption key.
- XCOD entropy ~**7.99946 bits/byte**, consistent with
  encrypted or otherwise transformed data.
- The second FOT magic in the EXE at `0x1CB4F0`
  equals the FOT directory's **duplicated header**, NOT
  a second independent firmware image. Do not carve
  a second 373,760-byte image starting there.

**This fulfills the missing same-HWID, genuinely
different-content encrypted firmware requirement!**
It does not yet prove the section-relative XOR keystream
is reused; raw ciphertext overlap statistics across
v1.48/1.59 must be computed before claiming that.
We have not recovered plaintext or a cryptographic key.

The user-provided ZIP stays in offline analysis,
**not committed to GitHub**. We committed the
new safe CLI `tools/compare-g3v2-cross-version-fots.py`
which accepts ZIP/EXE/FOT, handles changed XCOD sizes,
validates the FOT header/directory mirror, and compares
ciphertext aligned by section rather than whole file.
Run against the user's already-stored original v1.48
updater and local v1.59 ZIP; no hardware needed.

**Do NOT update/downgrade hardware** just to compare,
especially since this ZIP has separate controller
and receiver updaters. Do not confuse the receiver
image with the gamepad firmware or claim their version
fields match without parsing.



### 2026-11-21 — ACTUAL v1.48 ↔ v1.59 FOT differential executed: mixed section behavior, plaintext NOT recovered

User ran `tools/compare-g3v2-cross-version-fots.py` against:
- official v1.48 FOT SHA256 `0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085`;
- G3V2 v1.59 (controller) FOT SHA256 `ebb611eb8d4d730e991fa1f8027d653152515c4e47903c07537a6d7fadd4e849`.

The same exact 16-byte G3V2 HWID and static KEY ID
`00000001eae32e09` were confirmed by direct file parsing.
No execution, flashes, gamepad access, or output binaries.

**XCOD**:
- v1.48: 361472 bytes, v1.59: 365568 (+4096).
- On common section-relative offsets, only **2002/361472
  equal bytes (0.5538%)**, XOR entropy **7.99908**.
- **Exactly one 512-byte unchanged ciphertext run**:
  offset **+0x800** within whole XCOD section, 512 bytes;
  no exact matching 4096-byte blocks; the 32 matching
  aligned 16-byte blocks are contained in this one
  512-byte run.
- Apart from this run, **1490/360960** bytes coincide
  (~0.4128%), near the 1/256 = 0.390625% random
  equality baseline. Thus the original 0.5538%
  should **not** be interpreted as 0.55% actual
  plaintext code preserved.
- No exact aligned 512-byte blocks were found at
  shifted positions within ±32 KiB (using the
  comparator's bounded shift scan). This does not
  rule out arbitrarily shifted matches or alternative
  alignment.
- XCOD payload excluding the 16-byte header also
  shows near-uniform XOR (7.99910 bits/byte).
  The result is NOT the sparse, easy same-keystream
  delta pattern from the separate X20 v1.18/v1.21
  research example. It cannot on its own
  distinguish section re-encryption/rekeying,
  reordered/compressed plaintext, or wholesale
  compilation changes.

**XRES**:
- Both total sections 3072 bytes.
- **2074/3072 (67.513%)** equal; XOR entropy **3.29729**
  reflects many zero differences caused by unchanged
  ciphertext, **NOT low-entropy plaintext**.
- Exact **2048-byte unchanged ciphertext span at
  XRES relative +0x200..+0x9FF**, four consecutive
  512-byte blocks.
- Other matching bytes include first 24 bytes of
  the XRES section (structural header); the other
  2 single-byte coincidences are not meaningful.
  Of the XRES_DATA alone, the first 8 bytes
  after the 16-byte section header are equal,
  plus that same 2048-byte unchanged span.
- Remaining bytes differ, mostly in the first
  512 and last 512 of the XRES section.
  This is compatible with reuse of encrypted
  unchanged assets, but by itself cannot prove
  identical keystream for changed XRES pages.

**CONFIG**: 957/1024 equal (93.457%),
unchanged last 768 bytes. This is explicit
plaintext config metadata; do **not** count it
as cryptographic keystream evidence.

**Key research decision:** no decrypted firmware,
no key or usable keystream recovered.
Stop treating the existence of 1.48 and 1.59
as equivalent to a broken cipher.
The next high-value artifact is an
**intermediate same-HWID G3V2 revision**
(e.g., 1.58 or another historical image) that
may have more similar XCOD; or a known-plaintext
image of the same exact HWID. Prefer archived
official installers and OEM/researcher support.
Do not rerun generalized cipher brute force
or broad source searches with the same queries.

A new or closer revision may still fail to
give plaintext if its entire code stream differs.
All analysis remains offline and read-only.


### 2026-11-21 — Additional real-world LED tests imported from user-supplied Claude conversation

See full evidence/measurements and guards in [AMBILIGHT-TESTES-2026-11-21.md](AMBILIGHT-TESTES-2026-11-21.md).

The user supplied a full PDF transcript containing further completed G3V2 experiments, previously **absent from this repository**. Do NOT repeat them just to rebuild evidence.

- **Persistent B7 versus dark LEDs:** user applied green via official KeyLinker BEFORE docking; hours later lights were off, but three off-dock BLE reads still returned unchanged RGB(21,246,5), B7 `15 F6 05 00 00 FF`. FN+Right activated rainbow. Correct prior speculation: the green was NOT autonomously set by the charging dock. This proves that B7 may remain programmed while the visible LEDs are off; it does NOT prove the MCU saved B7 to Flash or establish whether charging/sleep causes light-off.
- **Official app Apply forces solid:** from rainbow, user pressed Apply with purple selected. Visual effect changed to solid purple, and subsequent BLE B7 returned `80 00 FF 00 00 FF` (RGB 128,0,255; brightness 255). This is a host/app initiated action, not a demonstrated RAM-only streaming opcode.
- **New v2 equal-vs-changed USB timing:** one warm-up write plus 8 repeated-same and 8 color-changing `0x37` writes; final B7 state restored to white `FF FF FF 00 00 FF` and snapshot removed. ACK medians 5.88ms (same) vs 6.88ms (changed), mean difference changed−same = −2.26ms with permutation p≈0.830. B7 post-write medians 5.02ms vs 8.02ms, mean difference +1.51ms, p≈0.076. Single same-value ACK outlier 31.89ms. No reliable flash-write discriminator found. **Do not repeat writing/timing experiments** to refine marginal p-values.
- **Safety audit needed:** exported transcript shows an invocation of `g3v2-light-write-timing-v2.py --dry-run` adjacent to 0x37 TX logs. The v2 source is not in GitHub and transcript provenance does not prove command/output alignment. It could be a bug or later full run output. DO NOT run this script or trust its dry-run without reviewing the actual Python source offline. No new hardware writes were performed in this documentation review.
- The G3V2 firmware XCOD remains encrypted, independent of these lighting observations. No wear-safe high-rate RGB writer has been demonstrated.

This updates the experimental state, NOT the known no-streaming safety policy.


### 2026-11-21 — Imported v2 scripts: offline static + behavioral simulation complete

The user's previously missing `g3v2-light-write-timing-v2.py` and `g3v2-le-connect-diag-v1.py` sources were provided and inspected, along with the repository versions of the persistence and warmup helper modules. See `docs/G3V2-SCRIPT-AUDIT-2026-11-21.md`.

The prior **unreviewed script safety warning** is now **resolved for the supplied source**: the `--dry-run` branch is correctly guarded. Tests with all HID calls mocked showed **0 0x37 writes and 6 synthetic B7 reads** in `--dry-run --reads 3`. Full write-path simulation produced 17 *synthetic* 0x37 operations, 24 B7 reads and restored the original six-byte lamp state. BLE diagnostics with a fake socket connected and closed once, and issued no packets. Both sources passed Python syntax compilation.

This validates *software control flow only*. It does not prove safety of physical writes to flash, complete restoration of effect mode, or a volatile RGB register. The user had already performed the physical timing v2 test; **do not repeat it**. The previously published claim that v2 had not been audited and should not be trusted is superseded. The need for a true RAM-only lighting method remains unresolved.


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


## v0.4.1 — physical brightness validation

User confirmed visible reduction of LED intensity after the manual brightness operation. Exact requested level and post-write B7 are not supplied. Brightness control is physically supported; response curve and independent brightness levels remain unmeasured. Next: native breathing and speed validation. User requires automatic BLE discovery, connection, read and apply in one action; no periodic polling. Earlier MAC and support-path failures were addressed by local patches and launcher reuse.


## v0.5 Alpha — automatic apply and native breathing physical validation

User reports automatic BLE discovery/apply works but is slow; brightness adjustment without restoration, native breathing, solid mode and original restoration all work. Speed changes were not visually perceptible. B7 confirms RGB F6 61 51, brightness 40 then FF, breathing packed 8000, 8BB8 (3000), 83E8 (1000), 81F4 (500), 80C8 (200), 8032 (50), solid 0001 and final restored FF FF FF 00 00 FF. All observed writes had matching serial response 19 08 81 ... 02 DF AA and subsequent B7 readback. Identity/B7 query roundtrips approximately 21–44 ms; full discovery and connection timing not logged. Speed storage confirmed, physical speed control not confirmed. Avoid further arbitrary speed sweeps and continuous persistent writes. Optimize cached verified address and discovery only on demand; measure full stage timing before claiming latency improvement. No MAC retained.


## v0.6 — cached BLE latency validation

Physical user test: first apply 4.904 s including discovery 4.015 s, BLE connection 0.577 s, GATT/subscription 0.204 s; second cached apply 0.975 s including connection 0.667 s and GATT/subscription 0.204 s. B7 verified both colors (86 5E 3C and E0 1B 24) at brightness FF. No need to change the validated protocol. Cache survival across application restart and profile persistence still pending. User requests convenient reopening; add KDE launcher. No MAC retained.


## v0.6 — restart and profile acceptance

User confirms all requested tests passed: KDE launcher reopening, cached color application below one second after reopening, saving a custom profile and profile persistence after restart. Discovery perceived faster. No new quantitative timing log supplied. Treat v0.6 as functional rollback baseline, not a published release. Next: measure idle resource consumption and design event-driven game profiles with no GPU capture or continuous persistent RGB writes; verify persistence safety before enabling automatic writes.


## v0.7 — Steam library acceptance

User reports six of seven functional tests passed: GUI, installed game discovery, additional disks, loading suggested settings, saving suggested profiles and previous functionality. Suggested colors did not make sense to user. Actual game list and palette examples not supplied. Next: improve offline palette relevance with curated AppID-based themes where available and ranked chromatic alternatives; preserve user overrides, avoid runtime analysis and automatic hardware writes. Automatic game activation remains pending.


## v0.8 — intermittent BLE rediscovery latency

User reports v0.8 running and occasional slow color changes. Four verified B7 writes took 0.764, 0.997, 0.721 and 5.271 s. Fourth operation logged cached address then discovery 4.013 s, connection 0.278 s and GATT 0.260 s. Exact reason for falling back to discovery not logged. Three direct operations average 0.8273 s. All four B7 confirmations matched requested RGB. Proposed bounded pre-write direct retry and early discovery termination, with explicit failure logging; no automatic retry after uncertain write, no persistent connection or polling. Palette satisfaction not explicitly confirmed.


## v0.8.1 — direct connection acceptance

Four physical RGB writes confirmed by B7: FF AA 22, 7B 96 38, CD AB 8F, FF 18 28; brightness BF preserved. Total times 0.827, 0.881, 0.897, 0.899 s (mean 0.876 s). All used cached address, no rediscovery or direct connection failure observed. Direct path validated; new retry and early discovery paths remain untested on hardware. Keep v0.8.1 as candidate baseline for v0.9 read-only running-game detection. No automatic RGB writes until persistence safety and explicit opt-in policy are addressed.


## v0.10 — automatic profile acceptance

User reports all seven checklist tests passed: manual apply without authorization, saving Origins profile, enabling automation, automatic apply on game start, stable lighting during play, restoration on exit, disabling automation and manual operation. This is user-reported physical validation for one game; no raw trace or CPU/RAM measurements supplied. Preserve v0.10 as rollback baseline. Next: audit idle and active monitor resource consumption, lifecycle and recovery, KDE/Gamescope coverage. Persistent write endurance remains unknown; avoid continuous reactive writes.
