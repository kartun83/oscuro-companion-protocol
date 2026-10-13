# Oscuro File Bridge — protocol

A deliberately tiny, symmetric protocol over a USB vendor bulk pipe. Either side
may serve a list of files and either side may ask the peer for its list, so the
same code runs on the device and on the host.

## USB identity

- VID:PID `1209:0003` (pid.codes shared test space).
- One vendor-specific interface (class `0xFF`), one bulk IN (`0x81`) and one
  bulk OUT (`0x01`) endpoint, 64-byte packets, full speed.
- Vendor-class interfaces get no kernel driver on macOS or Android, so libusb
  (pyusb) and the Android USB Host API can claim them directly.

## Framing

One USB bulk packet carries exactly one message, little-endian:

```
+0  u8  'O' (0x4F)     +4  u16 payload length
+1  u8  'F' (0x46)     +6  u8  seq        (0, reserved)
+2  u8  type           +7  u8  reserved   (0)
+3  u8  flags (0)      +8  ..  payload
```

Maximum payload is 56 bytes (`64 - 8`); file names are up to 40 bytes. A larger
list is sent as many `LIST_ENTRY` messages — a message is never split across
packets. A receiver drops a packet with a bad magic or an over-long length.

## Messages

| Type | Name | Payload |
|---|---|---|
| `0x01` | `HELLO` | ascii sender name (≤ 32 bytes) |
| `0x02` | `LIST_BEGIN` | `u16 count` of entries that follow |
| `0x03` | `LIST_ENTRY` | `u8 namelen`, `name[namelen]`, `u32 size`, `u8 kind` (0 file, 1 dir) |
| `0x04` | `LIST_END` | `u16 count` actually sent |
| `0x05` | `LIST_REQ` | empty — "send me your list" |
| `0x06` | `ACK` | `u8` type being acknowledged |
| `0x07` | `PING` | empty |
| `0x08` | `PONG` | empty |

## Session

1. When the host configures the device (`SET_CONFIGURATION`), the keyboard sends
   `HELLO("SMK-37 OSCURO")` then `LIST_REQ`.
2. The host replies `HELLO`, sends its own fake list
   (`LIST_BEGIN … LIST_ENTRY* … LIST_END`), and may send its own `LIST_REQ`.
3. The keyboard draws the host's list in its scroll window and answers the
   host's `LIST_REQ` with its own fake list (same three messages).
4. On receiving a `LIST_END`, a side replies `ACK(0x04)`.

Both directions run at once: host → device (the list drawn on screen) and
device → host (the list served to the phone). A bus reset (unplug/replug)
resets the session; the device re-sends `HELLO` + `LIST_REQ` when configured
again.

## Fake lists

- **Keyboard serves** (fixed, in `firmware/usbfiles.c`): `LIVE_SET.MID`,
  `PATCH_01.SYX`, `SONG_A.WAV`, `SCALE.SCL`, `README.TXT`, `DRUMS.OSCBANK`.
- **Host exposes** (in the Android app and the Python mock): `phone_notes.txt`,
  `recording_01.wav`, `presets_backup.bak`, `setlist_a.m3u`, `demo.mp3`, plus
  directories `inbox` and `exports`.

## Device-side behaviour

- Polled USB, no interrupts; `usbfiles.c` and `usbven.c` share one main loop
  with the LVGL refresh. Interrupts are masked (`cli`) before the USB port is
  taken over from UBOOT, matching the Oscuro live payload.
- The list UI uses only plain LVGL labels on the screen (a fixed row pool that
  is re-labelled), not a scrolling container: that is the pattern proven on the
  hardware.
