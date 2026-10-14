# Feasibility on the SMK-37 (Oscuro firmware)

Status 2026-10-14: desk evaluation against the Oscuro repository (`docs/FLASH_MAP.md`,
`docs/STATUS.md`, roadmap §14/§22/§26, `firmware/ram-lvgl/usbven.c`) and the AC79 SDK. Nothing
here was run on a unit unless it says so. "Verified" means read in code or docs; "open" means it
needs a measurement, listed in §6. Protocol: [PROTOCOL_V1.md](PROTOCOL_V1.md).

## 1. Verdict

| Area | Verdict |
|---|---|
| File service (phases 1-2) | **Feasible**, as an object store over the patch bank, project slots and a few virtual objects. Not a general filesystem. |
| Audio/MIDI capture (phase 3) | **Not worth building** on this hardware: no recording storage; live capture already works over the USB audio and MIDI interfaces. |
| Remote UI, tiers 0-2 | **Feasible** if built over existing tables, tier by tier. |
| Remote UI, tier 3 (control) | Feasible later; same input path as local controls, off by default. |
| Full speed | Enough for everything above. High speed is an open hardware question, tracked separately (`docs/test-specs/R9_husb_probe.md` in the Oscuro repo) and not required. |

## 2. Storage reality (verified)

The unit has a 1 MiB SPI NOR (`FLASH_MAP.md`). Writable by Oscuro: the patch bank in `USRFLASH`
(164 KiB, 512 slots in a record store), 4 sequencer project slots (`ks_project_store`,
`KS_PROJECT_STORAGE`), a 32 KiB settings area, a 4 KiB scratch sector. No SD card, no
filesystem. A minute of mono 16-bit 44.1 kHz audio is about 5 MB, so a `recordings` namespace
cannot exist. Everything the bridge exposes is therefore **objects over record stores**, which
is why PROTOCOL_V1 uses ids first and makes paths optional.

Flash behaviour that shapes the protocol (verified in `STATUS.md`/roadmap §15): erase is 4 KiB
sectors; a whole-area restore takes about 10 s; a 512-patch import once stalled the main loop
for 1.34 s on inline record-store compaction, and the fix was a `patch_idle` hook that pre-erases
between stores. So **flash time, not USB speed, is the bottleneck**, and writes must be sliced
and may need to pause (`NAK`, `BUSY_PLAYING`, `write_policy`).

## 3. Proposed SMK-37 profile

| Namespace | Objects | Access | Formats | Notes |
|---|---|---|---|---|
| `patches` | patch bank slots | read, write, delete | `syx`, `oscpatch` | record store keys become object ids; atomic commit = append record then publish |
| `projects` | 4 sequencer slots | read, write, delete | `ksproj` | fixed slot count: create fails when full |
| `exports` | virtual | read | `mid`, `syx` | generated from a project / patch; size unknown until generated |
| `area` | the whole `.ksua` image | read; write with on-device confirm | `ksua` | reuses `ks_userarea` classify/CRC/write session, never formats foreign data |
| `settings` | settings snapshot | read | `bin` | read-only; writes stay on the config SysEx |
| `log` | diagnostics (test builds) | read | `txt`, `ksrep` | replaces the 6-request `ks_report.py` read with one virtual object |

The bridge SHOULD be a second front end for the existing config services (`PATCH_COMMIT`,
`AREA_*`, caps), not a parallel implementation: same validation, same status codes mapped to
PROTOCOL_V1 error codes. The config SysEx remains the path for BLE MIDI and browsers.

## 4. USB (verified from code unless noted)

- The prototype is a RAM payload: a polled vendor device on the FS controller `0x11800`,
  EP1 bulk 64 B, no interrupts, run from ROM UBOOT and lost at power-off.
- The shipping firmware is a composite device (MIDI EP1; HID; USB audio in is built,
  isochronous EP3; roadmap §22 plans CDC next to it). The WL82 controller's total endpoint and
  FIFO budget is **open** (R-9: "limits beyond EP1 still open").
- Hence a two-stage plan: **stage A, mode switch**: a "USB Files" setting re-enumerates the
  keyboard as the bridge device only (like Felucca's USB Serial setting, no composite risk);
  files and logs work, live playing does not. **Stage B, composite**: add the bridge interface
  next to MIDI/audio; required by the remote-UI tiers, because an extra display must work while
  playing. Stage B waits on the endpoint measurement and the macOS composite-driver check
  (Felucca issue 67).
- Endpoint count (from code): the controller exposes **EP0-EP4** (SDK `USB_MAX_HW_EPNUM 5`; Felucca's
  `fm1_usb.h` has registers for EP1-EP4 only). A MIDI + HID + audio-in composite already uses most of
  them, so stage B may have to carry the bridge over an existing pipe (the CDC bulk pair or SysEx),
  see PROTOCOL_V1 section 2, "Alternative carriers". FIFO sizes per endpoint remain open.
- Power: the keyboard is bus powered on this path; whether a phone's OTG supply carries the
  keyboard (display, LEDs, synth) is **open** and decides whether stage A needs a powered hub.
- High speed: full speed now (macOS shows 12 Mb/s for the Oscuro firmware); whether the
  connector can run HS is open and independent. The protocol works at either speed.

## 5. Remote UI against the firmware

- Tier 0 (announce): needs only a string ring and hooks where the UI already changes page,
  value or shows a popup. Small RAM. The existing LVGL layer is label-only on the live path,
  and the UI state is kept in `ui_model` (`firmware/ui/ui_model.c`), which is the natural
  place to emit from. **To inspect** before sizing: how pages/values are modelled there.
- Tier 1 (entities): the parameter model already has names and ranges for settings pages,
  synth parameters and mixer. The work is a stable id scheme and a speakable long name for
  every label that is abbreviated on the 240x240 display ("SEL 2" is not speakable).
- Tier 2 (screen tree): a serializer over `ui_model`; RAM is the constraint. Some builds have
  under 1 KB (`dx7 --bt`, 960 B) to about 6 KB of SRAM left, others tens of KB. Tier 2 is
  therefore optional per build via the feature list; tier 0-1 should fit everywhere or be
  compiled out.
- Tier 3 (control): routed through the normal key/pad/knob event path; off by default.
- Accessibility companion: needs tiers 0-1 plus `INPUT`/`FOCUS`. These are the cheapest
  tiers and are independent of the file service.

## 6. What is not verified and how to settle it

| Item | Why it matters | Measurement |
|---|---|---|
| WL82 endpoint/FIFO budget in a composite device | stage B, remote UI while playing | extend `usbven.c` to N interrupt/bulk endpoints; enumerate; record per-EP FIFO sizes |
| Bridge as a second interface next to MIDI on macOS/Android | composite driver conflicts | enumerate the composite device on macOS 13-15 and 26, Android 10+ |
| Phone powering the keyboard over OTG | stage A may need a hub | measure bus current at idle/with display/with synth; try a phone and a powered hub |
| Android `bulkTransfer` vs queued `UsbRequest` throughput | message size, credit window | host app benchmark at 64 and 512 B, 100 KB transfer |
| Flash write pacing while playing | `write_policy` and `NAK` design | write 164 KiB through the record store while the synth renders; count audio overruns |
| `ui_model` structure | tier 0-2 cost | read-only code survey, then a size estimate per tier |
| HS on the connector | speed headroom only | `docs/test-specs/R9_husb_probe.md` |

## 7. Suggested order of work

1. **Spec review** (this and PROTOCOL_V1). Fix the open questions that block code: `max_msg`
   above 64, entity id stability, localisation.
2. **Host reference** in the standalone repo: a v1 client library and a device simulator
   (Python) so the protocol is tested without hardware; byte-level vectors from §14.
3. **Stage A firmware** (RAM payload, then flash build behind a setting): hello/caps,
   `patches` and `log` read, then write with the config-service backend.
4. **Android app**: Files-app (`DocumentsProvider`) integration and a small viewer; move it into
   this repo from the Oscuro worktree in the same step.
5. **Measurements** from §6 in parallel; their results decide stage B and tiers 2-3.
6. **Tier 0-1 UI** with the accessibility companion as the first consumer, independent of 3-4.

## 8. Felucca / SLOOP (FM-1 firmware)

Both target the M-VAVE FM-1, not the SMK-37. The FM-1 firmware has real storage
(`storage.c`, `project.c`, `editor_backup.c`) and a fixed composite endpoint layout, so a file
service would fit it better than it fits the SMK-37, but their web editor already covers backup
and restore. The contribution rules there are: small, tested on a real FM-1, ideas via a
Discussion first. The plan is a Discussion post (concept + this spec) and a PR only if the
maintainer wants it and an FM-1 is available to test. Not drafted yet.
