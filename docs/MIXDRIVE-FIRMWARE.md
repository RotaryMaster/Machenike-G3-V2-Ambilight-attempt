# MixDrive firmware architecture and API

## High-level architecture

MixDrive discovers attached devices and checks firmware versions, but it does **not** directly flash the G3 V2.

For gamepads, `MainViewModel.CheckFirmwareUpdateAsync` calls:

```csharp
FirmwareUpdateApiService.GetLatestVersionAsync(
    2,                 // productType = gamepad
    device.DeviceName, // brand
    device.DeviceMode  // product_name
)
```

The returned firmware record is compared with the connected-device version.

The update item stores at least:
- firmware version
- firmware update URL
- publish time
- device reference

## Official API

From `Machenik.HttpApi.dll`, the firmware service uses:

```text
GET /firmware/by-product-info
```

Parameters:
- `brand`
- `product_name`
- `product_type`

For gamepads:

```text
product_type=2
```

Base API:

```text
https://mixdriver.thunderobot.com/api
```

For the G3 V2 the catalog identity is now confirmed, not inferred:

```text
GET https://mixdriver.thunderobot.com/api/firmware/by-product-info
    ?brand=MACHENIKE
    &product_name=G3V2
    &product_type=2
```

The official response returned:

```text
product.id        = 8
brand             = MACHENIKE
product_name      = G3V2
product_type      = 2

firmware.id       = 11
version           = 1.48
changelog         = 性能优化
file_size         = 2245632
published_at      = 2026-05-08T04:23:53Z
```

Download path:

```text
/download/firmware/69fd65594961c_ZXD2267_JRH_G3V2_MACHENIKE_v1.48_20250815.exe
```

The same endpoint queried with `brand=THUNDEROBOT` returned HTTP 404 for `G3V2`.

Therefore the effective G3 V2 identity is definitively:

```text
brand=MACHENIKE
product_name=G3V2
product_type=2
```

The API result independently agrees with the live-device handshake firmware version **1.48**.

Other routes identified in the HTTP assembly include:
- `/software/latest`
- `/device-custom-configs`

## Update execution flow

`FirmwareUpdateViewModel.UpdateFirmwareAsync` performs approximately:

1. user confirmation;
2. normalize relative download URL against the MixDrive API base;
3. download updater to a temporary `.exe`;
4. for `DeviceType == 2`, send a gamepad-specific messenger/event;
5. launch the downloaded updater with shell execution;
6. wait for the updater process to exit;
7. remove the update item.

This proves the real flashing implementation is outside MixDrive, in the downloaded updater and its native dependencies.

## Managed messenger path: current evidence

A global metadata scan was run across the extracted MixDrive distribution:

```text
PE files found:          146
managed assemblies:      59
```

The important result is that the firmware/messaging path is present in metadata even though the current IL body decoder did not successfully emit contexts.

### Relevant symbols in Machenik.exe

Firmware/update methods:

```text
Machenik.ViewModels.MainViewModel::OnFirmwareUpdate
Machenik.ViewModels.MainViewModel::CheckFirmwareUpdateAsync
Machenik.ViewModels.Settings.SettingViewModel::CheckFirmwareUpdateAsync
Machenik.ViewModels.Notices.FirmwareUpdateViewModel::.ctor
Machenik.ViewModels.Notices.FirmwareUpdateViewModel::OnDialogOpened
Machenik.ViewModels.Notices.FirmwareUpdateViewModel::UpdateFirmwareAsync
<UpdateFirmwareAsync>d__5::MoveNext
<<UpdateFirmwareAsync>b__0>d::MoveNext
```

Concrete message type present:

```text
Machenik.Messages.CheckFirmwareUpdateMessage
```

Relevant messaging references in the same assembly:

```text
CommunityToolkit.Mvvm.Messaging.WeakReferenceMessenger::get_Default
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::Register
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::RegisterAll
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::Send
```

Relevant process-launch references:

```text
System.Diagnostics.ProcessStartInfo::.ctor
System.Diagnostics.ProcessStartInfo::set_FileName
System.Diagnostics.ProcessStartInfo::set_Arguments
System.Diagnostics.ProcessStartInfo::set_UseShellExecute
System.Diagnostics.ProcessStartInfo::set_Verb
System.Diagnostics.Process::Start
```

The assembly also contains many generic `MethodSpec` instances of `Send` and `Register`.

### Important interpretation

This evidence proves:
- the firmware updater view model is present;
- CommunityToolkit messenger registration/sending is used in the same application;
- the async/lambda state machines around `UpdateFirmwareAsync` exist;
- a firmware-check message type exists;
- the earlier scan that reported no relevant methods was a tooling false negative.

It does **not** yet prove that `CheckFirmwareUpdateMessage` is the gamepad pre-updater transition message.

The current parser emitted warnings such as:

```text
invalid compressed int: leading byte ...
```

and returned zero IL contexts despite correctly reading metadata. The next step is therefore to resolve raw ECMA-335 signatures and method bodies using a parser-independent path.

## Why the pre-launch messenger is important

The official v1.48 updater opens a storage/SCSI-style device through `DeviceUsb.dll`. It does not contain the normal HID-to-bootloader transition.

Therefore the pre-launch gamepad message remains the strongest lead for the official OTA transition path.

Next RE target:
- identify the exact `Send<T>` generic type;
- recover its payload/value;
- find every corresponding `Register<T>`/receiver;
- follow the receiver to the gamepad manager;
- determine the exact HID packet sent before updater launch;
- recover expected disconnect/re-enumeration behavior.

This should remain static/passive until the transition packet and recovery implications are understood.
