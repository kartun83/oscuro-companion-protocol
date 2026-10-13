# Integration — building and running the prototype

The Android app is self-contained (see `android/README.md`). The keyboard
payload is a RAM payload that is built and uploaded with the **Oscuro**
firmware tooling: LVGL 9.6, the JieLi pi32v2 toolchain, and the ROM-UBOOT RAM
loader used by the recovery dongle. It therefore lives inside an Oscuro
checkout.

Placeholders below: `<oscuro>` = your Oscuro checkout, `<build-vm>` = the Lima
VM used for Oscuro builds, `<venv>` = the Oscuro probe virtualenv.

## 1. Add the payload to the Oscuro checkout

Copy the payload sources next to the other RAM payloads:

```sh
cp firmware/usbfiles.c firmware/usbven.c firmware/usbven.h \
   firmware/usbfiles_proto.h firmware/run_usbfiles.py \
   <oscuro>/firmware/ram-lvgl/
```

Then teach the payload builder about the app:

```sh
cd <oscuro>
git apply /path/to/oscuro-file-bridge/firmware/build.py.patch
```

The patch adds `usbfiles` to `build.py`'s app choices, its output-directory
kind, its 240×240-only check, its LVGL-9-only guard, and links `usbven.c` into
the image.

## 2. Build the payload

```sh
limactl shell <build-vm> -- python3 <oscuro>/firmware/ram-lvgl/build.py --app usbfiles --version 9 --opt 2
```

Output: `<oscuro>/firmware/ram-lvgl/build/v9/usbfiles-wl82/demo.bin`.

## 3. Run it on the keyboard

One run per UBOOT entry: power-cycle, enter WL82 ROM UBOOT1.00 (recovery
dongle), then:

```sh
sudo <venv>/bin/python <oscuro>/firmware/ram-lvgl/run_usbfiles.py --opt 2
```

The runner uploads `demo.bin` and jumps to it. The payload takes the USB port
over and the UBOOT session ends at the jump (there is no result block to read
back). The keyboard now shows the list window and enumerates as
`1209:0003 "Oscuro File Bridge"`.

Nothing is written to flash. To leave, power-cycle — stock firmware returns.

## 4. Talk to it

**From the Mac** (before moving to the phone):

```sh
pip install pyusb          # and: brew install libusb / apt install libusb-1.0-0
python tools/usbfiles_host.py --seconds 60
```

You should see the device `HELLO`, its fake file list, and
`ack for message type 0x04`; the keyboard's window fills with the Mac's files.

**From the phone:** install the Android app, then **do not power-cycle**. Unplug
the keyboard from the dongle/Mac and connect it to the phone through a USB-OTG
adapter. The payload keeps running; the phone becomes the host. Open the app,
grant USB permission, and use **Push list** / **Ask keyboard**.

## 5. Host-side details

- The Python mock lives in `tools/usbfiles_host.py` (`--list-device` fetches the
  keyboard's list and exits; `--seconds N` runs a full session).
- The Android host is in `android/`; the protocol is implemented once in
  `FileBridge.kt` and shared by both list views.

## Troubleshooting

- **Nothing enumerates / "keyboard not found"** — the payload is not running, or
  the cable/adapter is not a host/OTG cable.
- **The chip reboots to stock** — the payload never took the USB port over
  cleanly, or it was power-cycled. Re-enter UBOOT and re-run; keep the payload
  running while swapping cables to the phone.
- **`claim_interface` fails on Linux** — detach the kernel driver for that
  interface first (`usb_modeswitch`/`usbfs`); macOS normally needs nothing.
