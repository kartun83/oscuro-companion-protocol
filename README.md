# Oscuro File Bridge

A small, working prototype for **keyboard ↔ smartphone communication over USB**,
where the **phone is the USB host** and the **keyboard is the USB device**.

- The keyboard runs a RAM payload (no flashing) that shows a small **scrollable
  file list** on its 240×240 LCD and speaks a tiny vendor-bulk protocol.
- A minimal **Android app** (USB Host API) exposes a **fake file list** to the
  keyboard and reads the keyboard's own fake list back.
- A **Mac/Linux mock** (`tools/usbfiles_host.py`, pyusb) does the same, so the
  firmware can be exercised without a phone.

The keyboard side targets the **M-Vave SMK-37** (JieLi AC791N / WL82) as part of
the [Oscuro](https://github.com/) open-firmware project. The payload is uploaded
into SRAM from the chip's ROM UBOOT and lives only until power-off.

```
   phone / Mac  (USB host)                     SMK-37 keyboard  (USB device)
   ┌────────────────────┐   bulk EP1 0x01/0x81  ┌──────────────────────────┐
   │ Android app        │ ────────────────────► │ usbfiles.c (LVGL list)   │
   │  fake file list    │ ◄──────────────────── │ usbven.c (vendor device) │
   │ Python mock        │   Oscuro File Bridge  │  fake file list          │
   └────────────────────┘                       └──────────────────────────┘
```

## Layout

| Path | What |
|---|---|
| `firmware/` | The keyboard RAM payload sources (`usbfiles.c`, `usbven.c/.h`, `usbfiles_proto.h`) and the UBOOT runner. Built inside an Oscuro checkout — see `firmware/README.md`. |
| `android/` | The Android USB-host app (Kotlin, platform `android.hardware.usb` only, no third-party deps). |
| `tools/usbfiles_host.py` | Mac/Linux host mock (pyusb). |
| `docs/PROTOCOL.md` | The wire protocol and the two-way session. |
| `docs/PROPOSAL.md` | Proposal for extending it: listing, fetch, create/rename/delete, two-way transfer, capability/format filtering, and audio/MIDI capture. |
| `docs/INTEGRATION.md` | How to build/run the firmware and where each piece plugs in. |

## Quick start

**Android (self-contained):** open `android/` in Android Studio and Run on a
phone with USB-host support (see `android/README.md`).

**Firmware (needs an Oscuro checkout):** copy the four payload sources into
`<oscuro>/firmware/ram-lvgl/`, apply `firmware/build.py.patch`, build and run —
full steps in `docs/INTEGRATION.md`.

**Mac mock (no phone):** `pip install pyusb`, then
`python tools/usbfiles_host.py --seconds 60` while the payload runs.

## Status

Verified on hardware **2026-10-13**: the payload enumerates as the vendor device
`1209:0003` "Oscuro File Bridge", the scrollable list renders on the SMK-37 LCD,
and fake lists are exchanged in both directions (phone app and Mac mock).

## Roadmap

The prototype exchanges fake lists only. `docs/PROPOSAL.md` is the design for a
real file service (directory listing, fetch, create/rename/delete, two-way
resumable transfer, capability/format filtering, and later USB audio/MIDI
capture). It is a proposal, not implemented.

## Limitations

- Prototype only: fake files, no real storage, no file transfer.
- The firmware half builds only inside an Oscuro checkout (LVGL 9.6 + the JieLi
  pi32v2 toolchain + the ROM-UBOOT upload path); only the Android app stands
  alone.
- VID:PID `1209:0003` uses pid.codes' shared test space — get a real PID before
  anything resembling a release.

## License

**Apache License, Version 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Permissive by design: implement the protocol or use/modify this code freely; the
only requirements are attribution (keep the copyright and `NOTICE`) and the
usual no-misrepresentation terms. The goal is wide adoption and
interoperability, so there is no copyleft and no restriction on using it in
closed products.
