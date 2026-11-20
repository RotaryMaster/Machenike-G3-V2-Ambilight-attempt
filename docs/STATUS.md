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
- expected CustomData length: 10 bytes
- real A/B readback was successful
- a breathing-blue configuration was successfully exercised

The goal remains to understand the complete lighting model well enough to support effects such as Ambilight without depending on MixDrive.

### Official firmware updater

The official v1.48 updater has been analyzed statically.

Known updater SHA-256:

```text
f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255
```

The updater dynamically loads `DeviceUsb.dll` and resolves:
- `deviceUsb_OpenByDrv`
- `deviceUsb_Close`
- `deviceUsb_GetInfo`
- `deviceUsb_Request`

The actual firmware-transfer protocol is SCSI pass-through using opcodes `F0`, `F1`, and `F2`.

## Current critical unknown

The updater itself expects the device to already be available through the Windows storage/SCSI path. It does not contain the normal HID command that performs the transition.

MixDrive, however, sends a gamepad-specific messenger/event immediately before launching the updater.

**Highest-priority next target:** trace that messenger to the exact HID command that makes the G3 V2 leave `413D:2131` normal mode and re-enumerate in firmware-update mode.

## Next steps

1. Trace the pre-updater MixDrive messenger/event to its receiver.
2. Recover the exact normal-HID OTA/bootloader command without sending it.
3. Determine the update-mode USB identity and interface class from code or passive observation.
4. Implement a Linux read-only probe for the update personality.
5. Issue only `F1` and `F2` in the first live SCSI test.
6. Compare returned hardware ID/version with the embedded updater metadata.
7. Document recovery/rollback possibilities before any `F0` write.
8. Only after the above, implement a Linux updater with explicit write safeguards.

## Conditions before a write test

A firmware write should not be attempted until all of these are known:
- exact update-mode entry sequence;
- update-mode VID/PID or storage path;
- read-only F1 response structure verified on the G3 V2;
- F2 status semantics verified;
- recovery path or at least a defensible re-entry path;
- exact firmware payload selected for the attached hardware ID;
- write loop and failure handling reproduced deterministically.
