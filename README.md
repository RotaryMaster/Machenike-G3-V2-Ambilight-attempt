# Machenike G3 V2 — Ambilight / Reverse Engineering

Private research repository for documenting and reproducing the Machenike G3 V2 controller protocol on Linux, with emphasis on lighting control, the MixDrive/KeyLinker HID protocol, OTA capability, and the official firmware update path.

> Current status: the normal HID protocol and firmware identity are substantially understood, the official firmware API identity is confirmed, the Windows updater's SCSI transport and embedded FOT layout have been reconstructed statically, and MixDrive's exact pre-updater message has been identified as `EnterUpdateModeMessage`. The remaining critical gap is the receiver-side HID command that transitions the controller into its firmware-update USB personality.

## Confirmed device identity

- USB VID:PID in normal mode: `413D:2131`
- Relevant HID interface on Linux: interface `:1.1` / MI_01
- Runtime HID node: dynamic `/dev/hidrawN`
- Firmware reported by the real device handshake: **1.48**
- Internal identifiers returned by the handshake:
  - `0x1006`
  - `0x2011`
- OTA feature bit: **present**
- Application marker returned by the device: `ZXPC_APP`
- Official MixDrive catalog identity:
  - brand: `MACHENIKE`
  - product: `G3V2`
  - product type: `2`

## Documentation

- [Current status and roadmap](docs/STATUS.md)
- [HID / KeyLinker protocol](docs/HID-KEYLINKER.md)
- [MixDrive firmware architecture and API](docs/MIXDRIVE-FIRMWARE.md)
- [MixDrive messenger / OTA transition investigation](docs/MIXDRIVE-MESSENGER.md)
- [Windows updater, DeviceUsb and SCSI F0/F1/F2](docs/OTA-SCSI-PROTOCOL.md)
- [FOT firmware container and updater payload](docs/FOT-FORMAT.md)
- [Research log and evidence policy](docs/RESEARCH-LOG.md)

## Safety state

No firmware write command has been intentionally executed during the SCSI reverse-engineering work documented here. The F0/F1/F2 reconstruction so far comes from static analysis of the official updater and its bundled `DeviceUsb.dll`.

Read-only validation should precede any flashing experiment:
1. identify the official HID-to-update-mode transition;
2. observe the re-enumerated USB device;
3. validate read-only `F1` and `F2`;
4. confirm the G3 V2 hardware ID and firmware response;
5. only then consider implementing `F0` writes.

## Repository policy

Facts are marked as confirmed only when supported by at least one of:
- real traffic captured from the G3 V2;
- decompiled/disassembled official binaries;
- direct readback from the controller;
- deterministic reconstruction with known test vectors.

External projects are used as cross-checks, not as substitutes for G3 V2 evidence.


### Latest protocol milestone — 2026-11-20

The normal G3V2 hardware-loader path is now mapped far enough to separate
lighting state from shared configuration: RGB is read through the LampLight
structure, while the 20-byte CustomData/0xBB object supplies light
mode/brightness and several rocker/motor settings. The current read-only
warm-up work is now focused on mapping each managed manager getter to its
native KeyLinkerlib call before another live CustomData probe.
