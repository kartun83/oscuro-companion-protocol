# Keyboard payload (RAM, no flash)

The keyboard half of the Oscuro File Bridge: an LVGL list UI plus a vendor-class
USB device that speaks the protocol in `../docs/PROTOCOL.md`.

| File | What |
|---|---|
| `usbfiles.c` | The app: LVGL UI, fake list, protocol session, main loop. |
| `usbven.c` / `usbven.h` | Polled vendor-class USB device (descriptors, EP0, bulk EP1). A trimmed, self-contained copy of the Oscuro MIDI device driver; it does not touch the shipping `smk37_usb` core. |
| `usbfiles_proto.h` | Frame/message constants. |
| `run_usbfiles.py` | Uploads the payload into UBOOT RAM and jumps to it. |
| `build.py.patch` | The change that adds `--app usbfiles` to Oscuro's RAM-payload builder. |

## Build & run

These files are meant to be dropped into `<oscuro>/firmware/ram-lvgl/`; see
`../docs/INTEGRATION.md` for the exact steps. Short version:

```sh
limactl shell <build-vm> -- python3 <oscuro>/firmware/ram-lvgl/build.py --app usbfiles --version 9 --opt 2
sudo <venv>/bin/python <oscuro>/firmware/ram-lvgl/run_usbfiles.py --opt 2
```

## Notes

- LVGL 9 only, 240×240 panel, no native preview.
- Polled USB, no interrupts. Interrupts are masked (`cli`) right before the port
  is taken over from UBOOT, as the Oscuro live payload does.
- The scrolling list is built from plain labels (no container/scrollbar), the
  pattern proven on the unit.
- Nothing is written to flash; the payload is gone after a power-cycle.
