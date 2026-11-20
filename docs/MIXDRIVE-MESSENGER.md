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
