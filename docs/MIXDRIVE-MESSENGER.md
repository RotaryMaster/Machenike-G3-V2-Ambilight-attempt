# MixDrive messenger / OTA transition investigation

## Purpose

The official G3 V2 updater itself only speaks to the controller after it has entered a Windows storage/SCSI update personality.

The normal HID -> update-mode transition is performed by MixDrive before the updater process starts.

## Confirmed sender path

Raw ECMA-335 MethodSpec decoding and direct inspection of `<<UpdateFirmwareAsync>b__0>d::MoveNext` closed the pre-launch message type.

For a firmware-update item whose device has `DeviceType == 2`:

```text
IL+0x0282  FirmwareUpdateModel::get_Device
IL+0x028E  DeviceModel::get_DeviceType
IL+0x029E  ObservableRecipient::get_Messenger
IL+0x02A3  EnterUpdateModeMessage::.ctor
IL+0x02AF  FirmwareUpdateModel::get_Device
IL+0x02B4  EnterUpdateModeMessage::set_Device
IL+0x02B9  IMessengerExtensions::Send<EnterUpdateModeMessage>
IL+0x02BF  ProcessStartInfo::.ctor
IL+0x02CB  ProcessStartInfo::set_FileName
IL+0x02D2  ProcessStartInfo::set_UseShellExecute
IL+0x02D8  Process::.ctor
IL+0x02E2  Process::set_StartInfo
IL+0x02E9  Process::Start
IL+0x02F1  Process::WaitForExit
```

The concrete message is therefore:

```text
Machenik.Messages.EnterUpdateModeMessage
```

and its payload is the exact `DeviceModel` associated with the firmware-update item, assigned through:

```text
EnterUpdateModeMessage::set_Device
```

This happens immediately before the external updater is launched.

## Generic metadata evidence

The relevant MethodSpec is:

```text
token 0x2B00031E
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::Send<
    Machenik.Messages.EnterUpdateModeMessage
>
```

Matching registration MethodSpecs also exist:

```text
0x2B00001E  Register<EnterUpdateModeMessage>
0x2B000022  Register<EnterUpdateModeMessage, !!0>
```

This gives a deterministic path for locating the recipient.

## Correction of earlier hypothesis

`Machenik.Messages.CheckFirmwareUpdateMessage` is a separate message type.

It has its own Send/Register MethodSpecs:

```text
Send<CheckFirmwareUpdateMessage>      = 0x2B00007F
Register<CheckFirmwareUpdateMessage>  = 0x2B00001F / 0x2B000023
```

It is **not** the gamepad pre-updater transition message.

The pre-updater transition message is definitively `EnterUpdateModeMessage`.

## Tooling note

The original high-level `dncil` body pass produced compressed-integer errors and falsely returned no useful contexts.

Raw metadata and direct method-body token scanning were successful and are now the preferred path for this part of the investigation.

## Current target

The next step is entirely receiver-side:

1. locate every call to MethodSpec `0x2B00001E` and `0x2B000022`;
2. inspect `InterfaceImpl` / `IRecipient<EnterUpdateModeMessage>` metadata;
3. identify the recipient class and its `Receive`/handler method;
4. follow the handler's calls into the gamepad device manager;
5. reconstruct the exact HID packet used to enter OTA/update mode;
6. only after static reconstruction, consider passive/live enumeration of the resulting USB personality.

No controller access is required for these steps.


## Receiver now confirmed

The generated messenger registration metadata points to:

```text
Machenik.ViewModels.MainViewModel
```

which explicitly implements:

```text
CommunityToolkit.Mvvm.Messaging.IRecipient<Machenik.Messages.EnterUpdateModeMessage>
```

The concrete async receive state machine that reads `EnterUpdateModeMessage.Device` is:

```text
<Receive>d__21::MoveNext
RID   = 2291
token = 0x060008F3
```

Important calls in that state machine:

```text
IL+0x001F  EnterUpdateModeMessage::get_Device
IL+0x002B  DeviceModel::get_HidDevice
IL+0x0037  HidDevice::get_DevicePath

IL+0x0053  DeviceManagerCache
IL+0x0059  ContainsKey
IL+0x0067  get_Item

IL+0x007A  EnterUpdateModeMessage::get_Device
IL+0x0086  DeviceModel::get_HidDevice
IL+0x008B  GamepadDeviceManager::.ctor
IL+0x00A1  DeviceManagerCache.Add

IL+0x00AE  GamepadDeviceManager::GetDeviceCustomDataAsync
...
IL+0x0122  GamepadDeviceManager::WriteDeviceCustomDataAsync
```

Therefore the official pre-updater path is now narrowed to:

```text
Send<EnterUpdateModeMessage>
        ↓
MainViewModel.Receive
        ↓
GamepadDeviceManager.GetDeviceCustomDataAsync
        ↓
modify CustomData
        ↓
GamepadDeviceManager.WriteDeviceCustomDataAsync
        ↓
external updater launch
```

This is a major correction to the earlier expectation that the transition might be a separate special HID command. The current evidence instead strongly indicates that MixDrive uses the already-known normal **CustomData** channel to request OTA/update mode.

The exact mutation is now confirmed by the full IL dump.

The handler calls:

```text
GetDeviceCustomDataAsync(10)
```

and stores the returned byte array in a local. If the result is non-null, it performs exactly:

```text
data[11] = (byte)(data[11] | 0x80);
```

before calling:

```text
WriteDeviceCustomDataAsync(data)
```

Relevant IL:

```text
IL+0x00AC  ldc.i4.s 10
IL+0x00AE  GamepadDeviceManager::GetDeviceCustomDataAsync
...
IL+0x0103  GetResult
IL+0x0108  stloc.2
IL+0x0109  ldloc.2
IL+0x010A  brfalse.s
IL+0x010C  ldloc.2
IL+0x010D  ldc.i4.s 11
IL+0x010F  ldloc.2
IL+0x0110  ldc.i4.s 11
IL+0x0112  ldelem.u1
IL+0x0113  ldc.i4 128
IL+0x0118  or
IL+0x0119  conv.u1
IL+0x011A  stelem.i1
IL+0x011C  <gamepadDeviceManager>5__2
IL+0x0121  ldloc.2
IL+0x0122  GamepadDeviceManager::WriteDeviceCustomDataAsync
```

So the official MixDrive transition is not a separate special HID command at this level. It is a read-modify-write operation on the normal CustomData byte array, setting bit `0x80` of array index `11`.

## Current exact target

The remaining mapping problem is now one layer lower:

1. inspect `GamepadDeviceManager.GetDeviceCustomDataAsync` in `Machenik.Infrastructure.dll`;
2. inspect `GamepadDeviceManager.WriteDeviceCustomDataAsync`;
3. determine how the returned byte array is laid out relative to the already-known 10-byte CustomData payload;
4. map array index `11` to the exact on-wire CustomData byte and packet;
5. reconstruct the precise normal-HID OTA request without sending it.

The apparent mismatch between the argument/value `10`, the known 10-byte CustomData payload, and access to array index `11` means the returned managed byte array likely includes protocol bytes or a wider decoded structure. That mapping must be confirmed from the manager implementation rather than guessed.


## Managed CustomData wrapper semantics clarified

Static IL of `Machenik.Infrastructure.Manager.GamepadDeviceManager` resolves the earlier ambiguity around the value `10`.

`GetDeviceCustomDataAsync(int timeoutSeconds)` stores its argument in a field explicitly named `timeoutSeconds`.

Its state machine performs:

```text
CreateTaskCompletionSource(28)
readDeviceCustomData(0)
GetResultAsync(..., timeoutSeconds)
```

Therefore the call seen in `MainViewModel.Receive`:

```text
GetDeviceCustomDataAsync(10)
```

means **10-second timeout**, not “request 10 bytes”.

The read-side native call receives selector/value `0`:

```text
readDeviceCustomData(0)
```

The write-side state machine performs:

```text
CreateTaskCompletionSource(29)
writeDeviceCustomData(data, data.Length)
GetResultAsync(..., 10)
```

So the write timeout is also 10 seconds, while the complete managed byte-array length is passed unchanged to the native interop layer.

This removes the apparent contradiction between the earlier 10-byte CustomData interpretation and `data[11]`. The next target is to determine the exact byte-array layout returned by native `receiveDeviceCustomData` and how managed index 11 maps to the BB/CustomData packet.


## Native KeyLinker CustomData layer: first reconstruction

The P/Invoke mappings are confirmed as cdecl exports from:

```text
Libs\Gamepad\KeyLinkerlib.dll
```

Relevant exports:

```text
receiveDeviceCustomData  RVA 0x00004DD0 / VA 0x180004DD0
readDeviceCustomData     RVA 0x00008DB0 / VA 0x180008DB0
writeDeviceCustomData    RVA 0x00008EB0 / VA 0x180008EB0
```

### readDeviceCustomData

The native read function receives the managed `index` as its real argument and constructs a six-byte BB command beginning with:

```text
BB 06 ...
```

It then passes all six bytes through the already-identified KeyLinker transform helper at `0x180001080` and returns a 16-byte native result descriptor containing a pointer plus length `6`.

This matches the known six-byte plaintext BB read request family.

### receiveDeviceCustomData

The exported function itself is only a thin wrapper. It calls:

```text
0x180001760
```

and copies exactly 16 bytes from the helper result to the exported return object. Therefore the actual received CustomData buffer/descriptor is assembled by `0x180001760`, not by the export body at `0x180004DD0`.

### writeDeviceCustomData

The write path reveals an internal length prefix:

```text
[length][data...]
```

At function entry the native code writes the low byte of `length` to the first local byte and copies the managed input bytes immediately after it. It then processes `length + 1` bytes.

That length-prefixed stream is split into chunks of at most 15 bytes. For each chunk, the DLL:
- obtains a four-byte protocol header from helper `0x180001C90`;
- appends the chunk;
- calculates and appends one CRC byte;
- passes the complete block through KeyLinker helper `0x180001080`.

So the native write transport is now structurally:

```text
managed CustomData bytes
        ↓
[length][data...]
        ↓
chunks <= 15 bytes
        ↓
4-byte packet header + chunk + CRC
        ↓
KeyLinker transform
```

The still-unresolved step is the exact buffer returned by `0x180001760`, which determines how managed index `11` maps to CustomData.


## receiveDeviceCustomData ABI and inverse framing

Further static analysis resolves the apparent argument mismatch in `receiveDeviceCustomData`.

On Windows x64 the native function returns a 16-byte descriptor/struct through a hidden structure-return pointer in `RCX`. The explicit managed arguments therefore arrive shifted:

```text
RCX = hidden return descriptor
RDX = datas
R8D = length
```

The export at `0x180004DD0` preserves `RDX/R8D` while replacing only `RCX` with a local output descriptor before calling `0x180001760`.

Inside `0x180001760`:

```text
R12  <- RDX   input byte buffer
R13D <- R8D   input length
R15  <- RCX   output descriptor
```

The helper allocates `length` bytes, copies the input, and for packets of at most 20 bytes calls `0x1800011C0`, which is the receive-side inverse/decoder corresponding to the send-side KeyLinker transform.

The branch threshold is exactly `0x15`: lengths below 21 follow the single-frame path; lengths of 21 or more take the multi-frame path. This matches the write-side frame maximum of 20 bytes:

```text
4-byte header + <=15-byte stream chunk + 1-byte CRC = <=20 bytes
```

After decoding a single frame, the helper shifts the decoded buffer left five times and returns length `input_length - 6`. The five leading bytes removed are the four-byte frame header plus the one-byte total logical-length prefix; the final CRC byte is excluded by the returned length. Therefore the single-frame result is exactly `decoded[5 : input_length-1]`.

For multi-frame input, the first 20-byte frame follows the same rule: remove four header bytes plus the total-length byte and exclude its CRC. Later frames remove only the four-byte header and exclude their CRC, then their payload bytes are concatenated.

This is the exact inverse of `writeDeviceCustomData`.

For the G3 V2 20-byte CustomData object, the writer first creates `[0x14][data0..data19]`, then fragments it as 15 + 6 bytes. The first plaintext frame is therefore:

```text
offset 0..3   four-byte 0x3B frame header
offset 4      0x14 total logical length
offset 5..18  data[0]..data[13]
offset 19     CRC
```

So the managed OTA mutation maps exactly as:

```text
data[11]  <=>  first plaintext 0x3B frame byte offset 16 (0x10)
```

The second frame contains `data[14]..data[19]` after its four-byte header and before its CRC.

Consequence for the OTA path: `data[11] |= 0x80` modifies a logical CustomData byte that lives in the first write fragment, not any HID/KeyLinker framing byte. CRC is then recalculated and the whole frame is KeyLinker-encoded.


### Managed HID dispatch chain identified

The managed `WriteDeviceCustomDataAsync` state machine creates operation/result type `29`, invokes native `writeDeviceCustomData(data, data.Length)`, then passes the returned encoded buffer into `GetResultAsync(..., timeout=10)`. The same manager contains the transport methods `SendDeviceData`, `SendNextPacketOrFail`, and `SendDeviceDataAsync`; these are now the exact next bodies to decode, together with `OnReport`, to close the HID envelope and ACK path. The corresponding read path uses operation/result type `28`.

A raw binary search for the two-byte sequences `80 F9` / `81 F9` is explicitly considered non-evidence because the pattern appears accidentally throughout unrelated assemblies. Future envelope reconstruction must come from IL/dataflow or a longer, context-validated signature.


### HID envelope closed from managed IL

The managed transport is now statically proven. `GamepadDeviceManager.SendDeviceData` computes `n = min(native_length, 20)`, constructs a byte array of `n + 5`, and writes:

```text
[0] = 0x80                 # HID output report ID
[1] = 0xF9                 # protocol marker
[2] = seq                  # managed sequence number
[3] = 0x00                 # flags/reserved on host TX
[4] = n                     # n <= 20
[5..] = native payload[0:n]
```

It then calls `HidLibrary.HidDevice.Write(byte[])` directly. This closes the previously inferred `80 F9` envelope with direct managed IL evidence.

The receive callback `OnReport` requires `HidReport.ReportId == 0x81` and `report.Data[0] == 0xF9`. In the generic receive path it uses `Data[1]` as the sequence, tests bit 0 of `Data[2]`, uses `Data[3]` as payload length, and extracts `Data[4:4+length]`. Therefore the generic incoming wire form is:

```text
81 F9 <seq> <flags> <len> <payload...>
```

If `flags & 1 != 0`, the callback resolves the matching entry in `AckMap` with success; otherwise the payload is passed into the normal receive/deframe path. This establishes the ACK path for writes, including CustomData task type 29.

`GetNextSeq` increments an 8-bit counter under a lock and skips zero, so valid managed sequence values are 1..255 with wraparound back to 1.


The 20-byte cap is statically tied to `Math.Min(source_length, 20)`. The two `GetPacketCount` overloads compute ceiling division by 20, confirming that longer native results are handled as 20-byte transport units rather than one oversized HID payload.


Correction: values 28/29 are not task IDs. `CreateTaskCompletionSource` stores the supplied value in `SeqMap[seq]`; they are internal operation/result type values associated with a sequence. The actual object-return completion source is stored in `ResultMap`, while write acknowledgements are tracked separately in `AckMap`.

### CustomData managed dispatcher cases 28 and 29

The managed CustomData operation types are now tied to their concrete receive behavior.

For type 28 (read), `HandleGenericDeviceData` first calls `CheckPacketIndex` so the outer managed HID packets are reassembled before the native decoder is invoked. When the final managed packet has arrived, the accumulated `DataMap` bytes are converted to an array and passed to:

```text
GamepadInterop.receiveDeviceCustomData(bytes, count)
```

The native result is a `DeviceDataStruct`. The dispatcher checks `data != IntPtr.Zero`, `length > 0`, and `length <= 100`. The invalid branch removes the pending result/sequence and resolves the task with null.

For type 29 (write), no native receive decoder is called. The dispatcher removes the pending transport maps and resolves the result completion source with boxed boolean `true`.

The exact valid type-28 branch after IL offset `0x153B` still needs one focused dump because the previous extraction helper stopped on a branch-local `ret` before printing the fall-through target.

### Operation 28 valid branch and multi-read consequence

The operation-28 valid branch is fully decoded. A valid native `DeviceDataStruct` is converted to the managed result exactly as:

```text
byte[] result = new byte[native.length];
Marshal.Copy(native.data, result, 0, native.length);
ResultMap.Remove(seq);
SeqMap.Remove(seq);
task.SetResult(result);
```

The same branch also proves how multi-packet CustomData reads continue. If the current packet is not the last expected packet, managed code updates `IndexMap`, migrates to a fresh managed sequence with `MigrateSeq`, calls `readDeviceCustomData(currentIndex + 1)`, and immediately sends the returned native request with `SendNextPacketOrFail`.

For the G3 V2 20-byte CustomData object, the native encoded result is 31 bytes, so the outer 20-byte managed HID transport requires two native read requests. This means the official OTA read-to-write path consumes two native sequence/rand steps, not one.

Fresh/default observed progression:

```text
read selector 0: BB 06 00 29 00 27
read selector 1: BB 06 81 23 01 B9
encoded selector 1 request: C2 37 F2 B7 A4 28
```

After those requests, the native sequence state entering `writeDeviceCustomData` is `E67C=0, E68C=2`; the next default CRT rand low byte is `BE`.

At this point the exact `E670` writer-entry value still needed confirmation. The older one-read candidates `3B F4 81 23` / `3B CB 0A BE` are superseded.



### Exact 0x1C90 header builder and CRC

Further static disassembly closes the native four-byte frame-header builder and the table checksum used by both CustomData read and write.

Helper `0x180001400` is a direct three-string concatenator: it allocates enough space, copies argument 1, appends argument 2, then appends argument 3. This removes the remaining ambiguity in the bit ordering used by `0x180001C90`.

For `0x180001C90(command, fragment_index, frame_len)`, the returned four bytes are exactly:

```text
header[0] = command
header[1] = ((E670 & 0x07) << 5) | (frame_len & 0x1F)
header[2] = ((E67C & 1) << 7) | ((fragment_index & 0x0F) << 3) | (E68C & 0x07)
header[3] = rand() & 0xFF
```

The apparently complicated ASCII construction is only binary-string packing. `E670` is emitted as three bits, `frame_len` as five bits, `E67C` as one bit, `fragment_index` as four bits, and `E68C` as three bits.

The writer passes `frame_len = chunk_length + 5`, where the five extra bytes are the four-byte header plus the final checksum byte. For a 20-byte G3 V2 CustomData object, the length-prefixed logical stream is 21 bytes and is fragmented as 15 + 6 bytes, producing plaintext frame lengths 20 and 11.

After each encoded write fragment, native state advances as:

```text
E67C = !E67C
E68C = (E68C + 1) mod 8
E670 = (E670 - 1) mod 8
```

The checksum table at `0x18000B320` is used with the exact recurrence:

```text
crc = 0
for each byte in header + payload:
    crc = TABLE[crc XOR byte]
append crc
```

This rule reproduces the observed read packets exactly:

```text
CRC(BB 06)             = 60
CRC(BB 06 00 29 00)    = 27
CRC(BB 06 81 23 00)    = C0
```

and is the same loop used by `writeDeviceCustomData` before passing the complete plaintext frame through KeyLinker encoder `0x180001080`.

Because the official G3 V2 20-byte read uses two native read requests before the write, the writer-entry state is `E67C=0, E68C=2` for the fresh/default observed sequence. A later PE-state audit established that `E670` is not file-initialized to 7; see the cold-state correction below.


### Cold native state: E670 starts at zero

A complete PE section/state audit corrects the earlier conditional `E670=7` assumption.

The file-backed `.data` section contains only `0x200` bytes beginning at VA `0x18000E000`, while the protocol globals are at:

```text
E670 = 0x18000E670
E678 = 0x18000E678
E67C = 0x18000E67C
E684 = 0x18000E684
E68C = 0x18000E68C
```

These addresses have no initialized bytes in the file-backed image data. They are zero-initialized runtime storage/padding in the loaded image. The complete xref scan also found no immediate initialization that writes `7` into `E670`; all confirmed writes to `E670` are the normal post-frame decrement operation:

```text
if E670 == 0:
    E670 = 8
E670 -= 1
```

Therefore, for a cold KeyLinker DLL state with no earlier write-style frame generation:

```text
E670 = 0
E67C = 0
E68C = 0
```

The two native CustomData read requests advance only `E67C/E68C`, so immediately before the OTA write:

```text
E670 = 0
E67C = 0
E68C = 2
next rand low byte = BE
```

The first 20-byte 0x3B plaintext frame therefore has the cold header:

```text
3B 14 02 BE
```

After that frame, the writer updates state to `E670=7, E67C=1, E68C=3`. The second 11-byte frame therefore begins:

```text
3B EB 8B 84
```

Thus the correct **cold/default** two-frame header pair is:

```text
3B 14 02 BE
3B EB 8B 84
```

The previously recorded pair `3B F4 02 BE / 3B CB 8B 84` is not the cold sequence. It would require writer entry with `E670=7`, which can only occur after prior write-style traffic has advanced the process-global state.

This distinction matters for byte-identical reproduction: `E670` is process-global runtime phase, not a fixed OTA constant. The header formulas remain exact, while the concrete byte pair in a live MixDrive session depends on prior native write traffic. The two CustomData read requests themselves do not change `E670`.


### E670 is a write-frame phase counter

A focused xref audit of every write to `0x18000E670` shows that the state is advanced only by frame-producing write routines. Every confirmed update has the same wrap/decrement form:

```text
if E670 == 0:
    E670 = 8
E670 -= 1
```

Therefore, if `W` is the number of native write frames emitted since cold DLL initialization, then:

```text
E670 = (-W) mod 8
```

The audit identified seven native writer regions that decrement `E670`. One is the already-known `writeDeviceCustomData` path using command `0x3B`. Two other multipart writers explicitly call the same `0x180001C90` header builder with command bytes `0x36` and `0x37`. Four additional fixed-size/single-frame generators also decrement `E670`; their exact export names still need RVA-to-export mapping.

This changes how the cold OTA header pair should be interpreted. `3B 14 02 BE / 3B EB 8B 84` is the correct cold-state pair, but a normal MixDrive session may have advanced `E670` before firmware update through earlier configuration writes such as lighting, motor, turbo, remapping, or other device writes. Exact byte-for-byte reproduction of a specific vendor session therefore requires counting prior native write frames modulo 8.

`E67C` and `E68C` are broader packet-phase state: both the read generator `0x1800014C0` and write-frame generators toggle/increment them. The CRT `rand()` stream is likewise consumed by packet construction. Thus the concrete OTA header bytes in an already-active MixDrive process depend on prior protocol traffic, even though the header formulas themselves are fully reconstructed.


### Exact E670 writer-to-export map

The PE export table closes the seven previously identified E670-writing regions unambiguously:

| Export | Export RVA | E670 write RVA | Offset inside export |
|---|---:|---:|---:|
| `writeRockerData` | `0x2600` | `0x292A` | `+0x32A` |
| `writeTriggerData` | `0x2960` | `0x2C69` | `+0x309` |
| `writeDeviceMotorData` | `0x2E30` | `0x33DC` | `+0x5AC` |
| `writeDeviceKeyChangeData` | `0x4E00` | `0x4FAD` | `+0x1AD` |
| `writeDeviceLamplightData` | `0x56D0` | `0x5B3D` | `+0x46D` |
| `writeDeviceTurboData` | `0x6800` | `0x6E4E` | `+0x64E` |
| `writeDeviceCustomData` | `0x8EB0` | `0x905E` | `+0x1AE` |

No E670 write was found in `writeDeviceMacroData` (RVA `0x83A0`) or `writeDeviceResetFactory` (RVA `0x4110`) in the focused xref audit. This means the E670 phase is advanced by the seven operations above, once per generated native frame. In particular, the multipart writers for KeyChange, Lamplight and CustomData use the common frame builder `0x180001C90`; the observed command bytes are `0x36`, `0x37` and `0x3B` respectively.


### Cold-start native state and remaining write command IDs

PE section metadata proves that the native protocol globals live in the zero-filled tail of `.data`: `.data` has RVA `0xE000`, VirtualSize `0x6A8`, and RawSize `0x200`. Therefore `E670`, `E674`, `E678`, `E67C`, `E680`, `E684`, `E688`, and `E68C` are initialized to zero by the PE loader on a cold DLL load.

The remaining single-frame write command IDs are visible directly in their export bodies:

| Export | Native command |
|---|---:|
| `writeRockerData` | `0x31` |
| `writeTriggerData` | `0x32` |
| `writeDeviceMotorData` | `0x33` |
| `writeDeviceTurboData` | `0x34` |
| `writeDeviceKeyChangeData` | `0x36` |
| `writeDeviceLamplightData` | `0x37` |
| `writeDeviceCustomData` | `0x3B` |

The first four above use specialized builders around helpers `0x1800012D0`, `0x180001370`, and `0x180001400`, rather than the multipart frame builder `0x180001C90`. KeyChange, Lamplight, and CustomData use `0x180001C90` and fragment payloads at 15 bytes.

For the cold OTA path already established in managed code, the preliminary `readDeviceCustomData(0)` leaves `E670` unchanged at 0 while advancing `E67C/E68C` from `0/0` to `1/1`. Therefore the following 20-byte CustomData write begins with native plaintext headers:

- fragment 0 (15-byte chunk, frame length 20): `3B 14 81 <rand>`
- fragment 1 (6-byte chunk, frame length 11): `3B EB 0A <rand>`

If the CRT `rand()` stream is still at cold default state and no other native call consumes it between the read and write, the observed default sequence gives read rand `0x29`, then write rand bytes `0x23` and `0xBE`, yielding headers `3B 14 81 23` and `3B EB 0A BE`. This last rand assignment is conditional on no intervening `rand()` consumption; the state/header fields themselves are statically proven.
