# Research log and evidence policy

This file is the chronological/conceptual record of the reverse-engineering effort. It should be updated whenever a material discovery changes the model of the protocol.

## Methodology

The project follows this evidence order:

1. search existing public information, prioritizing GitHub and reproducible community findings;
2. inspect official software and firmware artifacts when public information is incomplete;
3. prefer passive/static analysis before live hardware experiments;
4. cross-check independent evidence sources;
5. use the fastest reversible test that can falsify the current hypothesis;
6. keep destructive firmware writes as a last stage.

## Confirmed milestones

### HID interface identified

Normal G3 V2 USB identity is `413D:2131`. The protocol interface is MI_01 / sysfs `:1.1`.

### KeyLinker codec reconstructed

Tables and encode/decode behavior were recovered from `KeyLinkerlib.dll`. Real vectors and CRC behavior match the reconstructed codec.

### Feature report decoded

A real feature request/response was decoded and CRC-validated. Native code mapped the OTA feature bit, confirming OTA support on the G3 V2.

### CustomData communication validated

The `BB` CustomData path was read successfully using the corrected selector. Real readback confirmed the transport, and at least one breathing-blue lighting configuration was exercised.

### Firmware identity handshake closed

Official `GamepadDeviceManager::OnReport` IL resolved the six-byte tuple returned after the `ZXPC_APP` marker.

Real tuple:

```text
01 48 10 06 20 11
```

Confirmed interpretation:
- firmware: **1.48**
- ID #1: `0x1006`
- ID #2: `0x2011`

An earlier simplistic interpretation of raw bytes as a decimal version was discarded.

### MixDrive updater architecture closed

MixDrive:
- checks firmware through its HTTP API;
- downloads a model-specific updater EXE;
- sends a gamepad-specific pre-launch message;
- launches the updater;
- waits for exit.

The actual flash transport is therefore in the native updater, not in the managed MixDrive code.

### DeviceUsb transport reconstructed

`DeviceUsb.dll` was identified as a Windows storage/SCSI wrapper using `CreateFileW` and `DeviceIoControl`.

Its request path was reconstructed as SCSI pass-through-direct with retries.

### F0/F1/F2 update loop reconstructed

The v1.48 updater:
- uses F1 for hardware/version information;
- sends the complete FOT through sequential F0 transfers;
- checks F2 after the first F0 and after the complete transfer.

The FOT transfer is 722 blocks of exactly 512 bytes.

## Open questions

### 1. HID -> update mode transition

This is the highest-priority unresolved question.

Likely source: the gamepad-specific messenger/event sent by MixDrive immediately before it starts the updater.

Need to determine:
- message type/value;
- receiver;
- exact HID request;
- expected disconnect/re-enumeration;
- timeout/retry behavior.

### 2. Update-mode USB identity

Unknown for the G3 V2:
- VID/PID;
- USB class/subclass/protocol;
- whether it presents as disk, CD-ROM, or another storage/SCSI personality;
- Linux `/dev/sgN` / block-device mapping.

### 3. F1 full response semantics

Known:
- first 16 bytes: hardware ID comparison
- two bytes used as version

Still to document exactly:
- byte order and exact indexes for version in a live G3 V2 update-mode response;
- meaning of remaining response bytes.

### 4. F2 status codes

Only success criterion is currently established from updater code:
- byte 0 == 0 -> success

Nonzero values need semantic mapping.

### 5. Recovery behavior

Before any write test, determine:
- whether update mode can be re-entered after interruption;
- whether F0 writes staging flash or active flash directly;
- whether verification/commit occurs internally;
- whether dual-bank/rollback exists;
- what happens on cable loss.

## External corroboration

Useful related project:

- LopeKinz/Manba-One-V2-Firmware-tools  
  https://github.com/LopeKinz/Manba-One-V2-Firmware-tools

It independently reports a closely related `UsbUpdateAppX + DeviceUsb.dll` / FOT / F0-F1-F2 design.

Policy: use it to identify patterns and save time, but never replace G3 V2-specific static or live evidence with assumptions from another controller.

## Documentation rule going forward

Whenever a new fact is established:
1. update the relevant focused document;
2. update `STATUS.md` if it changes the current state or roadmap;
3. add the milestone or correction here when historically significant;
4. explicitly mark superseded hypotheses as superseded instead of silently deleting the history.
