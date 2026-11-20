# Official updater SCSI protocol

Static analysis target:

- updater: `ZXD2267_JRH_G3V2_MACHENIKE_v1.48_20250815.exe`
- updater SHA-256: `f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255`
- extracted `DeviceUsb.dll` SHA-256: `fad660bc7e006c58f37db9d75b72bf30c089ef62fc4e72673cbfd424d247b5e5`

This document describes only statically reconstructed behavior. No updater command was sent to a device during this analysis.

## DeviceUsb.dll transport

`DeviceUsb.dll` dynamically opens Windows storage paths and uses:

- `CreateFileW`
- `DeviceIoControl`
- `IOCTL_STORAGE_GET_DEVICE_NUMBER` (`0x002D1080`)
- `IOCTL_STORAGE_QUERY_PROPERTY` (`0x002D1400`)
- `IOCTL_SCSI_PASS_THROUGH_DIRECT` (`0x0004D014`)

It does not import SetupAPI, HID, WinUSB, or cfgmgr32.

Relevant exported updater API:

- `deviceUsb_OpenByDrv`
- `deviceUsb_Close`
- `deviceUsb_GetInfo`
- `deviceUsb_Request`

The updater resolves these dynamically and stores `deviceUsb_Request` at `0x005F9864`.

## deviceUsb_Request ABI

Static disassembly reconstructs the cdecl signature as:

```c
BOOL deviceUsb_Request(
    HANDLE h,
    BYTE dataIn,
    BYTE *cdb,
    DWORD cdbLength,
    void *dataBuffer,
    DWORD dataLength
);
```

The function builds a 32-bit Windows `SCSI_PASS_THROUGH_DIRECT` structure with:

```text
Length             = 0x2C
PathId             = 0
TargetId           = 1
Lun                = 0
CdbLength          = cdbLength
SenseInfoLength    = 0x20
DataIn             = dataIn
DataTransferLength = dataLength
TimeOutValue       = 5
DataBuffer          = dataBuffer
SenseInfoOffset    = 0x30
Cdb                 = memcpy(cdb, cdbLength)
```

The helper at `0x10001D80` is only a thunk to imported `memcpy`.

The SCSI request is submitted with `IOCTL_SCSI_PASS_THROUGH_DIRECT`. On failure, the function retries up to 10 attempts with a 20 ms sleep between attempts, except for `GetLastError() == 0x37`, which aborts immediately.

## Updater CDB setup

The updater function beginning at `0x004024D0` creates three local command buffers whose explicitly initialized bytes are:

```text
F0 00 00 00 00 00
F1 00 00 00 00 00
F2 00 00 00 00 00
```

Every call passes `cdbLength = 10`, even though only six bytes of each local command are explicitly initialized in the visible code.

This is important: the exact values of CDB bytes 6..9 are not statically proven to be zero in the official updater. The surrounding stack layout indicates those bytes can overlap padding or adjacent locals. The device therefore appears to care primarily about the vendor opcode and/or only the explicitly meaningful leading fields.

For future reproduction, a clean 10-byte CDB of `opcode + nine zero bytes` is a reasonable implementation hypothesis, but it is not yet claimed as byte-identical to the updater.

## F1 — identity/version read

Call site: `0x00402644`.

Equivalent call:

```c
deviceUsb_Request(
    handle,
    1,          // DataIn
    cdb_F1,
    10,
    response,
    20
);
```

The updater then interprets the 20-byte response as:

```text
response[0..15]   hardware identity (16 bytes)
response[16..17]  firmware version, little-endian uint16
response[18..19]  not used by the visible comparison logic
```

The first 16 bytes are compared against the embedded package hardware ID.

For firmware v1.48 the package ID is:

```text
A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E 22 67
```

The updater combines bytes 16 and 17 as:

```text
version = response[16] | (response[17] << 8)
```

and compares that value to the embedded package version.

The package version is `0x0094` = decimal 148, displayed as 1.48.

Therefore F1 is conclusively the read-side identity/version command.

## F0 — firmware block write

Call site: `0x00402872`.

Equivalent call:

```c
deviceUsb_Request(
    handle,
    0,          // DataOut
    cdb_F0,
    10,
    block,
    blockLength
);
```

The firmware payload begins at updater VA `0x005732F0`.

Confirmed payload size:

```text
0x5A400 = 369664 bytes
```

The loop copies and transmits blocks of at most:

```text
0x200 = 512 bytes
```

For the v1.48 image the size is exactly divisible by 512:

```text
369664 / 512 = 722 blocks
```

So the official updater performs 722 F0 data-out transfers for this package.

The visible caller does not branch on the Boolean return value of the individual F0 request. Instead, status is checked through F2.

Therefore F0 is conclusively the firmware data-write command.

## F2 — status read

First call site: `0x00402897`, immediately after the first F0 block.

Final call site: `0x004028DE`, after the transfer loop completes.

Equivalent call:

```c
deviceUsb_Request(
    handle,
    1,          // DataIn
    cdb_F2,
    10,
    status,
    20
);
```

The updater checks only:

```text
status[0]
```

Observed control flow:

- after the first firmware block, F2 is issued once;
- if `status[0] != 0`, the update path fails;
- after all firmware blocks have been sent, F2 is issued again;
- final success requires `status[0] == 0`.

The semantics of nonzero status codes are not yet decoded, but zero is conclusively the updater's success value.

## Reconstructed updater transfer flow

```text
open update-mode storage device
        |
        v
F1, DataIn=1, 20-byte response
        |
        +--> compare 16-byte hardware ID
        |
        +--> compare 16-bit firmware version
        |
        v
for each 512-byte firmware block:
        |
        +--> F0, DataOut=0, <=512-byte payload
        |
        +--> after first F0 only:
                F2, DataIn=1, 20-byte status
                require status[0] == 0
        |
        v
after final block:
        |
        +--> F2, DataIn=1, 20-byte status
        |
        +--> require status[0] == 0
        |
        v
success
```

## Safety boundary

No F0 command should be tested before all of the following are known:

- exact HID-to-update-mode transition;
- update-mode USB/storage identity;
- read-only F1 behavior verified on the real controller;
- F2 response behavior verified;
- recovery/re-entry path understood;
- payload hardware ID matched;
- failure behavior documented.

The first live SCSI work, when justified, should remain read-only and limited to F1/F2.


## Update-mode drive discovery and identity

The updater contains a continuous update-device discovery loop at `0x00402A70`.

The loop:

1. calls `GetLogicalDrives()`;
2. iterates all 26 drive-letter positions A..Z;
3. constructs the root path for each present drive;
4. calls `GetDriveTypeW(root)`;
5. accepts only type `2` (`DRIVE_REMOVABLE`);
6. calls `deviceUsb_OpenByDrv(L"<letter>:\\")`;
7. if open succeeds, calls `deviceUsb_GetInfo(handle, infoBuffer)`;
8. closes the handle with `deviceUsb_Close`;
9. converts the low byte of each returned UTF-16 code unit into an ASCII buffer;
10. compares that ASCII string with the literal:

```text
Gamepad Updater         1.00
```

If the string matches, the updater records the drive index (A=0 ... Z=25) in global `0x005F985C`. If no matching removable drive is found after scanning all 26 letters, the global is set to `0xFF`. The discovery loop sleeps 200 ms and repeats while the updater worker remains active.

This closes the official bootloader/update-personality discovery criterion on Windows: it is not matched by USB VID/PID in the updater executable; it is matched as a removable drive whose `deviceUsb_GetInfo` string equals the 28-character signature above.

### SCSI INQUIRY layout hypothesis

The 28-character signature has a highly significant exact length:

```text
"Gamepad "          = 8 bytes
"Updater         "  = 16 bytes
"1.00"              = 4 bytes
                     --------
                     28 bytes
```

That is exactly the standard fixed-width SCSI INQUIRY identification layout:

- Vendor Identification: 8 bytes
- Product Identification: 16 bytes
- Product Revision Level: 4 bytes

Therefore a strong static hypothesis is:

```text
Vendor   = "Gamepad "
Product  = "Updater         "
Revision = "1.00"
```

This is not yet promoted to confirmed protocol behavior until the implementation of `deviceUsb_GetInfo` inside `DeviceUsb.dll` is disassembled and shown to obtain those fields from SCSI INQUIRY or equivalent storage metadata.


### DeviceUsb_GetInfo implementation confirmed

Static disassembly of `DeviceUsb.dll!deviceUsb_GetInfo` (VA `0x10001200`) shows that update-device identity is obtained through Windows storage-property metadata, not by directly issuing an INQUIRY CDB from this function.

The function calls:

```text
DeviceIoControl(
    handle,
    0x002D1400,
    input_query,
    12,
    output_descriptor,
    0x227,
    &bytes_returned,
    NULL
)
```

`0x002D1400` is `IOCTL_STORAGE_QUERY_PROPERTY`.

After the call succeeds, the function checks DWORD offset `0x1C` of the returned descriptor against `7`. In the standard `STORAGE_DEVICE_DESCRIPTOR` layout, offset `0x1C` is `BusType`, and value `7` is `BusTypeUsb`. Non-USB descriptors are rejected.

The code then dereferences the standard descriptor string offsets:

- descriptor + `0x0C`: `VendorIdOffset`
- descriptor + `0x10`: `ProductIdOffset`
- descriptor + `0x14`: `ProductRevisionOffset`

and copies/appends those strings into a temporary ANSI buffer before converting the final string to UTF-16 with `MultiByteToWideChar`.

This confirms that the updater signature

```text
Gamepad Updater         1.00
```

is derived from the USB storage device descriptor as:

```text
VendorId        = "Gamepad "
ProductId       = "Updater         "
ProductRevision = "1.00"
```

The remaining tiny symbol-level check is to resolve IAT slot `0x1000208C`; the call pattern indicates it is the append operation used for ProductId and ProductRevision.

### Separation of metadata query and firmware CDB transport

`deviceUsb_GetInfo` uses `IOCTL_STORAGE_QUERY_PROPERTY` (`0x002D1400`) for identity metadata.

Separately, `deviceUsb_Request` uses Windows IOCTL `0x0004D014`, i.e. `IOCTL_SCSI_PASS_THROUGH_DIRECT`, for the updater's F0/F1/F2 CDB traffic.

Therefore:

- bootloader discovery/identity = storage property descriptor;
- firmware/status protocol = SCSI pass-through direct.
