# MixDrive messenger / OTA transition investigation

## Purpose

The official G3 V2 updater itself only speaks to the controller after it has entered a Windows storage/SCSI update personality.

The normal HID -> update-mode transition is therefore expected to occur in MixDrive immediately before the updater process is launched.

This document tracks the managed-code path used to find that transition.

## Known update flow

Static analysis already established that `FirmwareUpdateViewModel.UpdateFirmwareAsync`:

1. confirms the update;
2. downloads the updater EXE;
3. performs a gamepad-specific action for `DeviceType == 2`;
4. launches the EXE;
5. waits for it to exit.

The gamepad-specific pre-launch action is the current primary target.

## Global metadata scan

A global scan across the extracted MixDrive application found:

```text
PE files found:       146
managed assemblies:   59
```

Relevant assemblies include:
- `Machenik.exe`
- `Machenik.Shared.dll`
- `Machenik.Core.dll`
- `Machenik.HttpApi.dll`
- `CommunityToolkit.Mvvm.dll`

## Machenik.exe evidence

### Firmware methods

Confirmed MethodDefs include:

```text
Machenik.ViewModels.MainViewModel::OnFirmwareUpdate
Machenik.ViewModels.MainViewModel::CheckFirmwareUpdateAsync
Machenik.ViewModels.Settings.SettingViewModel::CheckFirmwareUpdateAsync

Machenik.ViewModels.Notices.FirmwareUpdateViewModel::.ctor
Machenik.ViewModels.Notices.FirmwareUpdateViewModel::OnDialogOpened
Machenik.ViewModels.Notices.FirmwareUpdateViewModel::UpdateFirmwareAsync

<>c__DisplayClass5_0::<UpdateFirmwareAsync>b__0
<>c__DisplayClass5_0::<UpdateFirmwareAsync>b__1
<UpdateFirmwareAsync>d__5::MoveNext
<<UpdateFirmwareAsync>b__0>d::MoveNext
```

### Message type present

```text
Machenik.Messages.CheckFirmwareUpdateMessage
```

Current evidence only proves this type exists and participates somewhere in the application messaging model. It must not yet be equated with the bootloader-entry message.

### CommunityToolkit messaging references

`Machenik.exe` references:

```text
CommunityToolkit.Mvvm.Messaging.WeakReferenceMessenger::get_Default
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::Register
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::RegisterAll
CommunityToolkit.Mvvm.Messaging.IMessengerExtensions::Send
```

The assembly contains many generic `MethodSpec` instances for both `Send` and `Register`.

### Process-launch references

The same assembly references:

```text
System.Diagnostics.ProcessStartInfo::.ctor
System.Diagnostics.ProcessStartInfo::set_FileName
System.Diagnostics.ProcessStartInfo::set_Arguments
System.Diagnostics.ProcessStartInfo::set_UseShellExecute
System.Diagnostics.ProcessStartInfo::set_Verb
System.Diagnostics.Process::Start
```

This matches the previously reconstructed updater-launch path.

## Important tooling correction

An earlier script reported zero relevant messenger/update hits in `Machenik.exe`.

That result was incorrect.

The corrected metadata scan found all of the expected firmware-update methods and messenger references.

Therefore:

> The earlier zero-hit result is a tooling false negative and is superseded.

A later attempt to parse every IL body with `dncil` emitted warnings such as:

```text
invalid compressed int: leading byte: 0xee
invalid compressed int: leading byte: 0xf8
invalid compressed int: leading byte: 0xec
invalid compressed int: leading byte: 0xf1
invalid compressed int: leading byte: 0xe4
```

Metadata enumeration remained valid, but no useful IL contexts were emitted by that pass.

## Current question

The exact pre-launch call still needs to be reduced to:

```text
Send<TMessage>(payload)
```

where we must recover:
- `TMessage`
- constructor or payload value
- sender method/state-machine location

Then we can resolve:

```text
Register<TMessage>(receiver)
```

and follow the receiver to the HID transport.

## Next static strategy

Do not depend on the failing `dncil` high-level decoder.

Use the raw ECMA-335 metadata instead:

1. parse each relevant `MethodSpec` signature blob;
2. resolve the generic argument of every `IMessengerExtensions::Send` MethodSpec;
3. do the same for `Register`;
4. map matching message types;
5. inspect only the small number of state-machine method bodies associated with `UpdateFirmwareAsync`;
6. decode those IL bodies directly from RVA using a minimal opcode parser or an independent .NET disassembler;
7. find the `Send<T>` immediately before `Process::Start`.

This path should identify the message without touching the controller.
