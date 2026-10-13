# Oscuro File Bridge — Android host (prototype)

A minimal USB-host app for the keyboard payload in `../firmware/usbfiles.c`. It
exposes a fake file list to the keyboard and fetches the keyboard's own fake
list. The wire protocol is in `../docs/PROTOCOL.md`.

No third-party libraries and no AndroidX; it uses only `android.hardware.usb`
(the platform USB Host API).

## Build

Open the `android/` directory in Android Studio and let it sync, then Run on a
phone. The project uses AGP **9.4.1** with its built-in Kotlin support (no
`org.jetbrains.kotlin.android` plugin needed), Gradle **9.8.0** via the included
wrapper, `compileSdk 36`, `minSdk 21`.

From the command line:

```sh
cd android
./gradlew :app:assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

(`local.properties` with `sdk.dir` is generated locally and git-ignored; Android
Studio writes its own.) The phone needs USB-host support (most have it) and you
need a USB-OTG adapter.

## Use

1. On the keyboard, run the payload (`../firmware/run_usbfiles.py` inside an
   Oscuro checkout — see `../docs/INTEGRATION.md`).
2. Plug the keyboard into the phone through the OTG adapter. Android may offer
   to open the app (the manifest filters `1209:0003`); otherwise open it and it
   scans for the device.
3. Grant the USB permission dialog. The top list is what the phone exposes to
   the keyboard; the bottom list is what the keyboard serves back.
4. **Push list** re-sends the phone's list; **Ask keyboard** requests the
   keyboard's list again.

## Notes

- `device_filter.xml` matches VID 4617 / PID 3 (0x1209 / 0x0003). Change both it
  and `Proto.VID/PID` if the firmware PID changes.
- If the app shows "keyboard not found", the payload is not running, the cable
  is not an OTG/host cable, or the phone did not grant USB host permission.
