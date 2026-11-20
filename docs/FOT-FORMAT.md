# FOT firmware container and updater payload

## Extracted image

Static analysis of the official G3 V2 v1.48 updater identified and extracted the complete embedded FOT image.

Updater location:

```text
file offset : 0x001712F0
RVA         : 0x001732F0
VA          : 0x005732F0
```

Complete FOT:

```text
size        : 0x5A400 = 369664 bytes
SHA-256     : 0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085
entropy     : 7.996828
blocks      : 722 x 512 bytes
remainder   : 0
```

The internal size field is:

```text
0x5A3F8 = 369656
```

which is exactly:

```text
0x5A400 - 8
```

So the internal field describes bytes after the 8-byte `FOT\0 + size` prefix, while the updater metadata describes the complete transfer image.

## Embedded identity

Known values:

```text
Hardware ID:
A1 CA 51 DA 72 4C B6 10 36 58 C0 5D 7D 4E 22 67

Version field:
94 00 00 00
= 0x94
= 148
= firmware 1.48
```

This identity is compared against the F1 response before transfer.

## Header tags

Several directory/header tags are stored with bit 7 set and decode by XOR `0x80`:

```text
raw C9 CE C6 CF -> INFO
raw C4 C5 D6 00 -> DEV
raw CB C5 D9 00 -> KEY
raw C4 C1 D4 C1 -> DATA
raw D8 C3 CF C4 -> XCOD
raw D8 D2 C5 D3 -> XRES
```

Known offsets in the FOT:

```text
INFO : 0x00054
DEV  : 0x0008C
KEY  : 0x000A0
DATA : 0x000B0
XCOD : 0x00200
XRES : 0x58600
DOWN : 0x594C0
```

The KEY field declares length 8 and contains:

```text
00 00 00 01 EA E3 2E 09
```

Its exact semantic role is still unresolved.

## Internal layout

Recovered v1.48 layout:

```text
header           0x00000-0x00200  size=0x00200
xcod             0x00200-0x58600  size=0x58400
xres             0x58600-0x59200  size=0x00C00
directory_copy   0x59200-0x59400  size=0x00200
config           0x59400-0x59800  size=0x00400
tail             0x59800-0x5A400  size=0x00C00
```

Hashes:

```text
header
6a8f80e9b9d5aa6336b565589700f0fb212aa850053b379e4a3edf364b8f8a52

xcod
3ddaec3bb4d3723d4d1c55392473e6830872674ce3c33f6ab401b857844821fe

xres
173740c39fce50700eea71a2616afbf445feabb0217ec4bc6c3ab57919377bb9

directory_copy
6a8f80e9b9d5aa6336b565589700f0fb212aa850053b379e4a3edf364b8f8a52

config
8c248c6b0c78fc227d5863bc5de7bd4027aeead8c840dba469c25e07f49362d0

tail
973c89eda5b13cb3f16366b829f5d79f3f5a0a87eb8e00679c540c256497543c
```

The 512-byte `directory_copy` is byte-for-byte identical to the initial header.

## XCOD

At `0x200`:

```text
tag             = XCOD
field +0x04     = 0x600
field +0x08     = 0x57E00
sum             = 0x58400
region size     = 0x58400
```

The XCOD region has near-maximal entropy, approximately 7.9995 bits/byte.

This is consistent with compressed, encrypted, transformed, or otherwise high-entropy code data. Static evidence alone does not distinguish those possibilities.

## XRES

At `0x58600`:

```text
tag             = XRES
region size     = 0xC00
```

It is also high entropy.

## Transfer behavior

The updater copies firmware data directly from the embedded FOT image into a temporary 512-byte buffer and immediately passes that buffer to F0.

No host-side XCOD/XRES decrypt/transform step appears in the observed write loop.

Therefore the bootloader receives the complete packaged FOT bytes exactly in their stored representation.

For v1.48:

```text
total bytes = 369664
chunk       = 512
chunks      = 722
remainder   = 0
```

There is no short final transfer.

## Hardware compatibility check

The F1 response is compared byte-for-byte with the embedded 16-byte hardware ID.

That means the firmware package is not selected only by marketing model name; the updater enforces a binary hardware-identity check before transfer.

Any future firmware experiment must preserve this check rather than bypass it casually.

## Known shared-family clue

The independent Manba One V2 work reports a closely related hardware-ID prefix and the same FOT/update architecture. This suggests a shared firmware/update framework, but not interchangeable firmware.

Do **not** treat another controller's FOT as compatible merely because the updater framework matches.
