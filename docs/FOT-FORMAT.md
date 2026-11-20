# FOT firmware container and updater payload

## Embedded metadata around the updater image

Static analysis of the official G3 V2 v1.48 updater identified metadata immediately before the embedded FOT image.

Known values:

```text
Hardware ID:
A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E 22 67

Version field:
94 00 00 00
= 0x94
= 148
= firmware 1.48

Embedded transfer size:
00 A4 05 00
= 0x0005A400
= 369664 bytes
```

The FOT image starts with:

```text
46 4F 54 00
F  O  T \0
```

The first eight bytes are:

```text
46 4F 54 00 F8 A3 05 00
```

The internal size at `FOT+4` is:

```text
0x5A3F8 = 369656
```

That is exactly:

```text
0x5A400 - 8
```

So the internal FOT size describes the bytes after the 8-byte `FOT\0 + size` header, while the updater's transfer-size metadata describes the complete 369664-byte FOT file.

## Transfer behavior

The updater copies firmware data directly from the embedded FOT image into a temporary 512-byte buffer and immediately passes that buffer to F0.

No host-side XCOD/XRES decrypt/transform step appears in the observed write loop.

Therefore the bootloader receives the FOT bytes as packaged by the updater.

For v1.48:

```text
total bytes = 369664
chunk       = 512
chunks      = 722
remainder   = 0
```

This exact divisibility is important: there is no short final transfer in this updater.

## Hardware compatibility check

The F1 response is compared byte-for-byte with the embedded 16-byte hardware ID.

That means the firmware package is not selected only by marketing model name; the updater enforces a binary hardware-identity check before transfer.

Any future firmware experiment must preserve this check rather than bypass it casually.

## Known shared-family clue

The independent Manba One V2 work reports a closely related hardware-ID prefix and the same FOT/update architecture. This suggests a shared firmware/update framework, but not interchangeable firmware.

Do **not** treat another controller's FOT as compatible merely because the updater framework matches.
