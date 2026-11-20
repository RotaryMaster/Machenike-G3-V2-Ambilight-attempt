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

## API

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

Base API observed in the updater flow:

```text
https://mixdriver.thunderobot.com/api
```

Therefore the effective request is:

```text
GET https://mixdriver.thunderobot.com/api/firmware/by-product-info
    ?brand=<device.DeviceName>
    &product_name=<device.DeviceMode>
    &product_type=2
```

The exact strings used as `DeviceName` and `DeviceMode` must be taken from the official device catalog/runtime object and should not be guessed from marketing names.

Other routes identified in the HTTP assembly include:
- `/software/latest`
- `/device-custom-configs`

## Update execution flow

`FirmwareUpdateViewModel.UpdateFirmwareAsync` performs approximately:

1. user confirmation;
2. normalize relative download URL against the MixDrive API base;
3. download updater to a temporary `.exe`;
4. for DeviceType 2, send a gamepad-specific messenger/event;
5. launch the downloaded updater with shell execution;
6. wait for the updater process to exit;
7. remove the update item.

This proves the real flashing implementation is outside MixDrive, in the downloaded updater and its native dependencies.

## Why the messenger is important

The official v1.48 updater opens a storage/SCSI-style device through `DeviceUsb.dll`. It does not contain the normal HID-to-bootloader transition.

Therefore the pre-launch gamepad messenger/event is currently the strongest lead for the official OTA transition path.

Next RE target:
- identify the message type/value;
- find every receiver;
- follow the receiver to the gamepad manager;
- determine the exact HID packet sent before updater launch;
- recover expected re-enumeration behavior.

This should be done statically before any live transition test.
