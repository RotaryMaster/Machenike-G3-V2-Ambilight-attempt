# OTA updater / DeviceUsb / SCSI protocol

## Scope

This document describes static reconstruction of the official G3 V2 v1.48 Windows updater.

No F0 firmware write was intentionally executed during this analysis.

## Official updater

Known SHA-256:

```text
f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255
```

The updater dynamically resolves these `DeviceUsb.dll` exports:

```text
deviceUsb_OpenByDrv
deviceUsb_Close
deviceUsb_GetInfo
deviceUsb_Request
```

## DeviceUsb transport

`DeviceUsb.dll` imports:
- `CreateFileW`
- `DeviceIoControl`
- `CloseHandle`
- `GetLastError`
- `Sleep`

Device paths constructed by the DLL include:

```text
\\.\%c:
\\.\PHYSICALDRIVE%i
\\.\CDROM%i
```

This confirms the updater talks through the Windows storage stack rather than the normal HID path.

### Drive enumeration

`deviceUsb_GetDrvID` opens candidate drive paths and uses:

```text
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
```

The return value encodes the selected storage-device type/index used later by `deviceUsb_Open`.

### GetInfo

`deviceUsb_GetInfo` uses:

```text
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
```

and parses a storage-device descriptor.

## deviceUsb_Request

Reconstructed signature:

```c
bool deviceUsb_Request(
    HANDLE handle,
    BYTE   DataIn,
    BYTE  *Cdb,
    DWORD  CdbLength,
    void  *DataBuffer,
    DWORD  DataTransferLength
);
```

The DLL wraps this in a `SCSI_PASS_THROUGH_DIRECT` request.

Confirmed wrapper values:

```text
SCSI_PASS_THROUGH_DIRECT.Length = 0x2C
SenseInfoLength                 = 0x20
SenseInfoOffset                 = 0x30
wrapper total size              = 0x50
TimeOutValue                    = 5
PathId                          = 0
TargetId                        = 1
Lun                             = 0
IOCTL                           = 0x4D014
retries                         = 10
retry delay                     = 20 ms
```

`0x4D014` is the Windows SCSI pass-through-direct IOCTL used by the DLL.

## CDB opcodes

The updater constructs three local 10-byte CDBs whose first bytes are:

```text
F0
F1
F2
```

A clean representation is:

```text
F0 00 00 00 00 00 00 00 00 00
F1 00 00 00 00 00 00 00 00 00
F2 00 00 00 00 00 00 00 00 00
```

The original binary only explicitly initializes part of the local stack storage, strongly suggesting the firmware only relies on the opcode and ignores reserved trailing bytes.

### F1 — information query

Call shape:

```text
direction             = IN
CDB                   = F1...
CDB length            = 10
response buffer       = 20 bytes
data transfer length  = 20
```

The updater compares the first 16 response bytes with its embedded hardware ID.

It then reads two bytes near the end of the 20-byte response as the device version and compares that version with the embedded updater version.

Observed updater logic therefore treats F1 as the initial hardware/version compatibility query.

### F0 — firmware transfer

Call shape:

```text
direction             = OUT
CDB                   = F0...
CDB length            = 10
data                   = next firmware block
maximum block size     = 0x200 = 512 bytes
```

There is no block address encoded into the F0 CDB by the updater.

The host advances its local source pointer by 512 bytes while repeatedly sending the same F0 CDB, which means the device/bootloader maintains the sequential write position internally.

For this v1.48 image:

```text
payload size = 0x5A400 = 369664 bytes
block size   = 0x200   = 512 bytes
block count  = 722
```

Every block is exactly 512 bytes.

### F2 — status query

Call shape:

```text
direction             = IN
CDB                   = F2...
CDB length            = 10
response buffer       = 20 bytes
data transfer length  = 20
```

For the G3 V2 updater, F2 is called:
1. after the first F0 transfer;
2. again after the complete F0 loop.

The updater checks response byte 0:
- `0` -> accepted/success path
- nonzero -> error path with the returned value included in the UI message

This is more precise than calling F2 merely a "finalize" command: it is a status query used at least at those two checkpoints.

## Reconstructed updater sequence

```text
device already in storage/SCSI update personality
        |
        v
open target
        |
        v
F1 IN, 20 bytes
        |
        +-- compare 16-byte hardware ID
        +-- compare version
        |
        v
prepare transfer/progress
        |
        v
F0 OUT, block #0 (512 bytes)
        |
        v
F2 IN, 20 bytes
        |
        +-- byte[0] must be 0
        |
        v
F0 OUT, remaining 721 sequential blocks
        |
        v
F2 IN, 20 bytes
        |
        +-- byte[0] must be 0
        |
        v
success UI / close target
```

## Linux port implications

The Windows `DeviceUsb.dll` is not fundamentally required on Linux. Once update-mode enumeration is understood, the same CDBs should be expressible through the Linux SCSI generic interface (for example SG_IO) against the appropriate update-mode device.

The first Linux implementation must be read-only:
- enumerate candidate update-mode storage/SCSI target;
- send F1;
- parse hardware ID and version;
- send F2;
- never expose F0 unless an explicit unsafe/write flag is added later.

## External cross-check

A separate reverse-engineering project for the Manba One V2 reports the same updater family, `DeviceUsb.dll`, FOT structure, and F0/F1/F2 pattern:

https://github.com/LopeKinz/Manba-One-V2-Firmware-tools

This is useful corroboration, but G3 V2 behavior is documented here from the G3 V2 updater itself. Differences such as F2 timing must follow local evidence.
