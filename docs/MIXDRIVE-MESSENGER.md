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
