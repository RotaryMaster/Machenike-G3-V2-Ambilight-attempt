# Machenike G3 V2 — Ambilight / Reverse Engineering

Public research repository for documenting and reproducing the Machenike G3 V2 controller protocol on Linux, with emphasis on lighting control, the MixDrive/KeyLinker HID protocol, OTA capability, and the official firmware update path.

> Current status: the normal HID transport, KeyLinker codec, LampLight/CustomData framing, OTA CustomData mutation, managed HID envelope, Windows updater SCSI transport, and embedded FOT container are substantially reconstructed. The original lighting goal is still open: the known RGB path (`0x37`) is persistent across power loss, while the controller's 13 FN+Right lighting effects are handled locally in firmware and are not exposed by B7/BB state reads. The current target is therefore an undocumented volatile/realtime LED path or firmware-internal lighting handler suitable for Ambilight without repeated persistent writes.

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

## Current protocol milestones

### HID / KeyLinker transport

Confirmed:
- host envelope: `80 F9 <seq> 00 <len> <payload>`;
- receive side accepts the corresponding `0x81` report family;
- KeyLinker encode/decode has been reconstructed and validated against real vectors;
- the common frame/state machinery and CRC/check tables are mapped;
- normal hardware-loader ordering and manager/native bridges are substantially closed.

### CustomData

Confirmed:
- read command family: `0xBB`;
- working sub-selector: `0x00`;
- logical G3 V2 CustomData object: **20 bytes**;
- `writeDeviceCustomData` prefixes the logical length, fragments the stream into at most 15 payload bytes per physical frame, adds the four-byte header + CRC, then applies KeyLinker encoding;
- managed index `data[11]` maps exactly into the first plaintext `0x3B` write frame.

### OTA / update-mode transition

The MixDrive-side transition is no longer an unknown command.

Confirmed managed flow:

```text
EnterUpdateModeMessage
  -> MainViewModel receiver
  -> GetDeviceCustomDataAsync(...)
  -> data[11] |= 0x80
  -> WriteDeviceCustomDataAsync(data)
```

The native CustomData framing and the final normal-HID packetization are reconstructed far enough to reproduce the request offline. No firmware write has been performed as part of this research.

### Windows updater / SCSI transport

The official v1.48 updater and bundled `DeviceUsb.dll` have been analyzed statically.

Confirmed:
- updater firmware transport is SCSI pass-through;
- opcodes `F0`, `F1`, and `F2` are used by the updater;
- the embedded FOT package is **369664 bytes / 0x5A400**;
- transfer layout is **722 blocks x 512 bytes**;
- the official package carries the expected G3 V2 hardware/version metadata;
- the carved FOT payload is high-entropy and is not treated as plaintext firmware.

### Lighting

Confirmed:
- B7/LampLight and `0x37` expose the persistent base lamp/RGB path;
- an isolated `0x37` write survives a real controller power-cycle;
- therefore `0x37` must **not** be assumed safe for high-frequency realtime Ambilight streaming;
- FN+Right cycles **13 physical lighting effects** locally on the controller;
- passive usbmon capture during FN+Right showed **0 host-to-device OUT transfers**;
- B7/BB fingerprints can remain byte-identical while the visible effect changes;
- the same B7 payload was observed for both solid and breathing output;
- normal MixDrive/KeyLinker/Android lighting analysis has not exposed a second realtime RGB writer.

The current lighting target is an undocumented **volatile/realtime LED transport or firmware-internal effect handler**, not another B7/BB/FN+Right fingerprint test.

### Test-mode commands

The previously unresolved native test-mode area is now statically mapped:

- `0x1B = CODE_TEST_SET_MODE`
- `0x1C = CODE_TEST_NORMAL_MODE`
- `enterDeviceTestlevelMode`, `exitDeviceTestMode`, `keyTestBeforeEffect`, and `keyTestAfterEffect` are identified;
- the common `0x1B` builder and its mode argument are reconstructed;
- the former candidate at `0x180004EA9` belongs to `writeDeviceKeyChangeData` / command `0x36`, not lighting.

There is currently **no static evidence** that `0x1B/0x1C` implement the 13 FN+Right lighting modes or provide a volatile RGB path, so they are not being used as blind live probes.

## Documentation

- [Current status and roadmap](docs/STATUS.md)
- [HID / KeyLinker protocol](docs/HID-KEYLINKER.md)
- [KeyLinker codec](docs/KEYLINKER-CODEC.md)
- [MixDrive firmware architecture and API](docs/MIXDRIVE-FIRMWARE.md)
- [MixDrive messenger / OTA transition investigation](docs/MIXDRIVE-MESSENGER.md)
- [Windows updater, DeviceUsb and SCSI F0/F1/F2](docs/OTA-SCSI-PROTOCOL.md)
- [Updater / SCSI notes](docs/UPDATER-SCSI.md)
- [FOT firmware container and updater payload](docs/FOT-FORMAT.md)
- [Research log and evidence policy](docs/RESEARCH-LOG.md)

## Safety state

No firmware `F0` write has been intentionally executed on the controller during the updater/SCSI reverse-engineering work documented here.

Live testing follows a conservative order:
1. prefer static analysis and passive captures first;
2. use read-only device probes where possible;
3. isolate one variable at a time;
4. preserve/restorable state before any persistent write;
5. avoid high-frequency use of commands already proven persistent;
6. do not attempt firmware flashing until update-mode identity, readback semantics, failure behavior, and recovery options are understood.

Some scripts intentionally contain hard safety gates or omit destructive code paths.

## Repository policy

Facts are marked as confirmed only when supported by at least one of:
- real traffic captured from the G3 V2;
- decompiled/disassembled official binaries;
- direct readback from the controller;
- deterministic reconstruction with known test vectors.

External projects are used as cross-checks, not as substitutes for G3 V2 evidence.

Closed experiments and superseded hypotheses are kept in the detailed status/research log so future work does not repeat already completed tests.

## License

The original source code and documentation in this repository are licensed under the
[Apache License 2.0](LICENSE).

This repository documents interoperability and reverse-engineering research involving
third-party hardware, software, protocols, trademarks, and firmware formats. The Apache
License applies only to the original material published in this repository; it does not
grant rights to third-party firmware, binaries, trademarks, or other proprietary material.

No official Machenike/MixDrive firmware, executable, DLL, APK, or other vendor binary is
redistributed by this repository. Analysis tools expect researchers to provide any
third-party artifacts themselves where legally permitted.

Some tools in this repository inspect or exercise low-level HID/USB protocol behavior.
Read the current status and safety notes before running any live hardware test.

This is an independent community research project and is not affiliated with or endorsed by
Machenike, MixDrive, KeyLinker, or their respective owners.
