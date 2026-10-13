# Oscuro File Bridge — extension proposal (v1)

Status: **proposal only, not implemented.** This describes how the prototype
protocol in [PROTOCOL.md](PROTOCOL.md) (referred to here as **v0**) could grow
into a full device↔host file service: directory listing, fetch, create/rename/
delete, two-way transfer, capability/format filtering, and — later — USB audio
and MIDI capture.

The design is **device-agnostic**: everything a host needs to know is advertised
by the device at connect time. Oscuro/SMK-37 is one implementer, not the spec.

## 1. Goals and non-goals

**Goals**

- Browse, read, write, create, rename and delete files on the device over the
  existing vendor bulk pipe.
- Two-way, resumable, integrity-checked transfer.
- Let different firmwares advertise different capabilities, namespaces and file
  formats so one host app adapts without per-firmware code.
- A path to pulling audio/MIDI recordings off the device.
- Stay small enough for a polled, interrupt-free device loop.

**Non-goals**

- **No firmware/OTA update over this protocol.** Updating is deliberately out of
  scope; it stays a separate, explicitly-confirmed path (and is `while(1)`-safe
  to refuse). The bridge must be safe to expose to a phone.
- No POSIX completeness (no symlinks, no permissions model beyond read-only vs
  read-write namespaces, no file locking beyond a session).
- No guarantee of hard real-time delivery on the bulk pipe (see §9).

## 2. Principles

1. **Backward compatible.** v0 message types keep their meaning; a v1 host that
   sees no `CAPS` falls back to the v0 list exchange. New types are ignored by
   old firmware.
2. **Capability-driven, not version-driven.** Hosts branch on feature bits and
   namespace/format descriptors, never on a firmware name.
3. **Control stays single-packet.** Keep every control message inside one 64-byte
   bulk packet (≤ 56-byte payload). Bulk data uses explicit `DATA` frames with a
   handle and offset — no stream reassembly state on either side. This is much
   easier to make robust on a polled device than a byte-stream.
4. **Offsets and checksums, not sequencing.** Transfers are addressed by byte
   offset and verified at commit, so lost/reordered frames are recoverable and
   resume is trivial.
5. **Writes are atomic or absent.** A file is written to a temporary name and
   committed by rename; an interrupted write leaves the previous content intact.

## 3. Capability manifest (negotiation)

At connect (or on demand) the host asks for the manifest:

```
GET_CAPS (0x10)                    -> CAPS_BEGIN (0x11)
                                      CAPS_FLAGS (0x12) *      feature bits, limits, protocol version
                                      CAPS_NS    (0x13) *      namespace descriptor
                                      CAPS_FORMAT(0x14) *      file-format descriptor
                                      CAPS_END   (0x15)
```

`CAPS_FLAGS` carries a u16 protocol version (major/minor) and a u64 feature
bitmask (split across two u32 fields), plus scalar limits:

| Field | Meaning |
|---|---|
| `proto_major/minor` | v1.0 = 1,0. Host must not use a message the device did not advertise. |
| `features` | bitmask below |
| `max_path` | max path length in bytes (UTF-8) |
| `max_name` | max single path component |
| `max_file` | max file size (0 = unbounded) |
| `max_inflight` | max unacked `DATA` frames a sender may have in flight |
| `max_data` | max payload bytes per `DATA` frame (≤ 48) |

### 3.1 Feature bits (initial set)

| Bit | Name | Meaning |
|---|---|---|
| 0 | `DIR_LIST` | namespaces can be listed |
| 1 | `FILE_READ` | existing files can be downloaded |
| 2 | `FILE_WRITE` | files can be created/overwritten |
| 3 | `FILE_APPEND` | append without rewriting the whole file |
| 4 | `FILE_DELETE` | delete files |
| 5 | `FILE_RENAME` | rename/move within a namespace |
| 6 | `DIR_CREATE` | create directories |
| 7 | `SEEK_READ` | random-access reads (partial download) |
| 8 | `RESUME` | open at an existing offset/checksum |
| 9 | `CHECKSUM_CRC32` | commit/verify with CRC32 |
| 10 | `ATOMIC_COMMIT` | temp-file + rename commit |
| 11 | `MKDIR_IF_ABSENT` | idempotent directory creation |
| 12 | `FILTER_FORMAT` | listing filtered by format id |
| 13 | `FILTER_ROLE` | listing filtered by role (preset/sample/audio/…) |
| 14 | `EVENTS` | unsolicited `EVENT` notifications (file created, storage low) |
| 15 | `STREAM_AUDIO` | audio stream channel (§9) |
| 16 | `STREAM_MIDI` | MIDI stream channel (§9) |
| 17 | `COPY_IN_DEVICE` | server-side copy without round-tripping bytes |
| 18 | `STAT_MTIME` | metadata carries modification time |
| 19 | `QUERY_FREE_SPACE` | storage-free reporting |
| 20 | `UI_MODEL` | whole-device semantic UI model (§12, REMOTE_UI.md) |
| 21 | `UI_CONTROL` | companion may drive the UI/controls (`UI_ACT`) |
| 22 | `UI_EVENTS` | subscribe + pushed `UI_VALUE`/`UI_STATE`/`UI_EVENT` |
| 23 | `UI_SNAPSHOT` | optional device-rendered snapshot of a region (§12) |

Unknown bits are ignored. A host offers a feature list it supports and the
device answers with the intersection it implements — or, simpler for v1, the
host just reads the device's bits and disables UI accordingly.

### 3.2 Namespace descriptor (`CAPS_NS`)

A device exposes one or more **namespaces** — the unit of storage and
permission, not a POSIX mount:

```
{ id:u8, flags:u8, role:u8, name_len:u8, name[], max_file:u32 }
```

- `id` — handle for later messages (0 = root namespace, always present).
- `flags` — `READ_ONLY`, `WRITABLE`, `REMOVABLE`, `VOLATILE`, `INTERNAL`.
- `role` — `PRESETS`, `SAMPLES`, `RECORDINGS`, `PROJECTS`, `SCRATCH`, `SYSTEM`, `MIXED`.
- `name` — human label, e.g. `presets`, `recordings`, `samples`.

Suggested SMK-37 mapping: `presets` (read-write, PRESETS), `recordings`
(read-only in Phase 1, RECORDINGS), `scratch` (volatile, for transfer staging),
`system` (read-only, SYSTEM).

### 3.3 Format descriptor (`CAPS_FORMAT`)

Each advertised format tells the host what a file of that kind is:

```
{ id:u8, caps:u8, role:u8, ext_len:u8, ext[], mime_len:u8, mime[] }
```

- `caps` bit0 read, bit1 write, bit2 stream-source, bit3 stream-sink.
- Example formats: `syx` (MIDI presets), `kspreset`, `wav` (audio),
  `mid`/`smf` (MIDI), `oscbank`, `blob` (engine patch blobs).

**Filtering / firmware differences** fall out of these descriptors:

- A synth-only firmware exposes `presets` writable and no `recordings`.
- A recorder firmware exposes `recordings` read+delete and `wav` files, no
  preset writes.
- A dev firmware adds `scratch` writable and exposes everything.
- The host shows only operations whose feature bit *and* namespace/format
  permissions are present, and offers format filters from the advertised list.

## 4. Path and handle model

- Paths are UTF-8, `/`-separated, relative to a namespace, no leading `/`, no
  `..`, no NUL; length ≤ `max_path`, component ≤ `max_name`.
- The device assigns a stable **file id** (`u32`, inode-like) per file. Handles
  returned by `OPEN` refer to ids, so a rename during a transfer is harmless.
- Names are the human interface; ids are the reliable one. `LIST_ENTRY` carries
  both.

## 5. Message catalogue (v1 additions)

Type numbers are chosen to leave v0 (0x01–0x08) untouched.

### 5.1 Listing and metadata

| Type | Name | Direction | Payload |
|---|---|---|---|
| 0x20 | `LIST_REQ2` | host→dev | `ns:u8, filter_flags:u8, format_mask:u32, role:u8, start:u16, max:u8, sort:u8` |
| 0x21 | `LIST_BEGIN` | dev→host | `ns:u8, total:u16` |
| 0x22 | `LIST_ENTRY` | dev→host | `id:u32, size:u32, mtime:u32, format:u8, flags:u8, name_len:u8, name[]` |
| 0x23 | `LIST_END` | dev→host | `sent:u8, more:u8` |
| 0x28 | `STAT_REQ` | host→dev | `ns:u8, id_or_path` |
| 0x29 | `STAT` | dev→host | same fields as `LIST_ENTRY` |
| 0x2A | `MKDIR` | host→dev | `ns:u8, path[]` |
| 0x2B | `RENAME` | host→dev | `ns:u8, from_path[], to_path[]` |
| 0x2C | `DELETE` | host→dev | `ns:u8, path[]` |
| 0x2D | `COPY` | host→dev | `ns:u8, from_path[], to_path[]` (feature `COPY_IN_DEVICE`) |

`LIST_ENTRY` at 55 bytes max fits one packet with a 40-byte name. Entries are
paged with `start`/`max`; `LIST_END.more` says whether another page exists.

`filter_flags`: bit0 files, bit1 directories, bit2 hidden. `format_mask` selects
formats (0 = all). `sort`: name/size/date, ascending/descending.

### 5.2 Open / IO

| Type | Name | Direction | Payload |
|---|---|---|---|
| 0x30 | `OPEN` | host→dev | `ns:u8, mode:u8, flags:u8, path_or_id, [offset:u32]` |
| 0x31 | `OPENED` | dev→host | `handle:u8, status:u8, size:u32, credits:u8, id:u32` |
| 0x32 | `CLOSE` | both | `handle:u8` |
| 0x33 | `DATA` | both | `handle:u8, flags:u8, offset:u32, data[]` (≤ 48 bytes) |
| 0x34 | `CREDIT` | both | `handle:u8, credits:u8` |
| 0x35 | `PROGRESS` | dev→host | `handle:u8, next_offset:u32, crc32:u32` |
| 0x36 | `COMMIT` | host→dev | `handle:u8, size:u32, crc32:u32` |
| 0x37 | `ABORT` | both | `handle:u8, reason:u8` |
| 0x38 | `SEEK_READ` | host→dev | `handle:u8, offset:u32, length:u32` (feature `SEEK_READ`) |

`mode`: `READ`, `WRITE_CREATE`, `WRITE_TRUNCATE`, `WRITE_APPEND`, `WRITE_RESUME`.
`OPEN` accepts a path (for create) or an id (for existing).

**Read** (download): `OPEN(read)` → `OPENED(handle, size, credits)`; device sends
`DATA` frames up to `credits`; host replenishes with `CREDIT`; host `CLOSE`.
`PROGRESS` (optional) lets the device report its committed offset + running CRC
so the host can resume or verify mid-stream.

**Write** (upload): `OPEN(write_create)` → `OPENED(handle, credits)`; host sends
`DATA` up to credits; device `CREDIT`s; host `COMMIT(size, crc32)`; device
verifies and atomically publishes; `ABORT` discards the temp file.

**Flow control** is a credit window of `DATA` frames (not bytes) — small state,
easy on a polled loop. `credits` is the receiver's remaining buffer slots for
that handle; each received `DATA` spends one; the receiver issues `CREDIT` when
it drains.

### 5.3 Events and errors

| Type | Name | Direction | Payload |
|---|---|---|---|
| 0x70 | `EVENT` | dev→host | `kind:u8, arg:u32` — `FILE_CREATED`, `STORAGE_LOW`, `RECORDING_DONE`, `BUS_RESET` |
| 0x60 | `ERR` | both | `ref_type:u8, ref_id:u8, code:u8` |
| 0x61 | `NAK` | both | `ref_type:u8, reason:u8` — "busy, retry" |

Error codes: `E_UNSUPPORTED`, `E_BADPATH`, `E_NOT_FOUND`, `E_EXISTS`,
`E_ACCESS` (read-only namespace), `E_NOSPACE`, `E_TOO_BIG`, `E_BUSY`, `E_IO`,
`E_CRC`, `E_ABORTED`, `E_PROTO`, `E_AUTH`.

## 6. Transfer semantics

- **Integrity**: `COMMIT`/`PROGRESS` carry CRC32 over the whole file. A mismatch
  yields `E_CRC`; the host re-opens with `WRITE_RESUME` at the last good
  `PROGRESS.next_offset`.
- **Resume**: `OPEN(WRITE_RESUME, offset)` continues an interrupted upload;
  `SEEK_READ`/`OPEN(READ, offset)` continues a download.
- **Atomicity**: `ATOMIC_COMMIT` (feature bit 10) means the visible file only
  changes at `COMMIT`. Without it, hosts should treat partially-written files as
  disposable and recreate.
- **Cancel**: `ABORT` frees the handle and removes any temp file.
- **Concurrency**: one transfer per handle, up to a small advertised handle
  count. The device may `NAK` new `OPEN`s while busy.

## 7. Utilization (what this buys)

- **Phone app**: browse device presets and samples, share/hand off files, back
  up and restore patch banks, drop a WAV into a sampler slot, pull tonight's
  recordings, free space by deleting takes.
- **DAW / desktop**: retrieve recordings and load patches without a vendor
  tool; a "device" pane that mirrors the advertised namespaces.
- **Capability UI**: buttons/operations enabled only where features +
  permissions exist; the same app drives a synth firmware, a recorder firmware
  and a dev firmware with different visible capabilities and format filters.
- **Field service**: a `scratch` namespace for logs/diagnostics the firmware can
  write and the phone can pull, without exposing anything else.

## 8. Backward compatibility with v0

- A v1 host first sends `GET_CAPS`. If no `CAPS_BEGIN` arrives within a timeout,
  it drops to the v0 `HELLO`/`LIST_REQ` flow and offers read-only listing only.
- v0 firmware ignores unknown types, so a v1 host that probes then falls back
  costs one round trip.
- The prototype's v0 messages remain valid; this document only adds around them.

## 9. Realtime audio / MIDI (Phase 3)

Two channels, both optional and gated by `STREAM_AUDIO` / `STREAM_MIDI`.

```
STREAM_OPEN (0x40)  host->dev  ns:u8, dir:u8, media:u8, codec:u8,
                               sample_rate:u32, channels:u8, bit_depth:u8,
                               block_frames:u16, duration_ms:u32
STREAM_READY(0x41)  dev->host  handle:u8, credits:u8, frame_bytes:u16, clock:u8
STREAM_DATA (0x42)  both       handle:u8, seq:u8, ts:u32, frames:u16, data[]
STREAM_CTL  (0x43)  both       handle:u8, op:u8   (start/stop/pause/seek/flush)
STREAM_CLOSE(0x44)  both       handle:u8
```

- `media`: `AUDIO` (PCM or a codec id from `CAPS_FORMAT`) or `MIDI` (timestamped
  events, plus an option to serialize to an SMF file on commit).
- `ts`: running sample position (audio) or monotonic microsecond timestamp
  (MIDI), used for drift correction and seeking.

**Honest transport note.** USB full-speed bulk has no bandwidth/timing
guarantee, and this device is polled with other work sharing the loop. So:

- **Live monitoring / DAW I/O** should stay on the USB class interfaces the
  device already exposes — **UAC1 audio** (in/out) and **USB-MIDI** — where the
  host's audio/MIDI stack handles clocking and buffering. The bridge is not a
  replacement for those.
- **The bridge's stream channel is for capture/transfer**: pull a recording that
  the device (or host) buffered to a file, push a sample into a slot, or move a
  near-real-time block with the host providing the jitter buffer. Timestamps let
  the host detect gaps/overruns and pad/trim.
- A future hard-real-time path would want a dedicated isochronous endpoint on a
  separate interface — i.e. widen the UAC interface, not the bulk pipe.

Recording workflow: device captures (audio via its codec path, MIDI via its
input/sequencer) into `recordings/`; on stop it emits `EVENT(RECORDING_DONE)`;
the host lists `recordings/` and downloads the `wav`/`mid` with the §5–§6
transfer. If a firmware streams directly, `STREAM_OPEN(RECORD)` writes the file
as data arrives, then commits through the normal file path.

## 10. Safety and security

- **No OTA/upgrade** through this protocol — out of scope by design (§1).
- Namespaces carry explicit read-only/writable flags; `E_ACCESS` on violation.
- Paths are validated (`..`, NUL, absolute, over-long → `E_BADPATH`).
- All writes go through temp+commit; the settings/system namespace is read-only
  to the bridge.
- Long operations must keep the watchdog fed and must not block the audio path;
  transfers are chunked by construction so the device loop stays responsive.
- The bridge is optional and can be refused entirely by a firmware (no `CAPS`).

## 11. Phasing

| Phase | Scope |
|---|---|
| **0 (done)** | v0: `HELLO`, `LIST_REQ`, list exchange, both directions. |
| **1** | `GET_CAPS` + manifest; filtered/paged listing; `STAT`; read download with credits; `CREATE`/write upload with atomic commit; `RENAME`, `DELETE`, `MKDIR`; CRC32. Read-only namespaces first (`recordings`, `system`), then writable `presets`. |
| **2** | `SEEK_READ`, resume (`WRITE_RESUME`), `PROGRESS`; `COPY_IN_DEVICE`; `EVENT`s; `QUERY_FREE_SPACE`; format-role filtering. |
| **3** | `STREAM_AUDIO` / `STREAM_MIDI` capture, alongside the existing UAC/USB-MIDI realtime interfaces. |
| **4** | Remote UI (`UI_MODEL`/`UI_CONTROL`/`UI_EVENTS`), then optional `UI_SNAPSHOT` — see §12. |

## 12. Remote UI / whole-device model

The device can also describe its **whole UI and controls semantically** — what is
shown, what values exist, what is available, and what can be acted on — so a
companion app can mirror it or build its own layout. It deliberately says
**what**, not **how**: no pixels, no coordinates, no LVGL/toolkit names. It is
covered by feature bits 20–23 above and specified in
[REMOTE_UI.md](REMOTE_UI.md).

In one paragraph: the device exposes **surfaces** (`SCREEN`, `CONTROLS`,
`PARAMETERS`, `INDICATORS`, `TRANSPORT`); a surface is a tree of **nodes** with a
generic **role** (label, value, range, toggle, choice, key, pad, encoder, …),
**state/availability**, and a **binding** to an entity; values/state stream via
subscribe (`0x80–0x8E`); a companion acts with `UI_ACT` (invoke/set/adjust/
navigate), routed through the device's normal input path so local and remote stay
in sync. **Presentation hints** (widget/emphasis/order/group) are advisory and
ignorable, so the companion may lay the UI out however it wants. An **optional
snapshot** capability can rasterise a region for exact fidelity, and is never
required. Nothing in the model assumes LVGL or any GUI toolkit — a firmware with
no screen exposes only `CONTROLS`/`PARAMETERS`.

## 13. Open questions

- **Handles per connection vs persistent file ids**: is a stable id worth the
  firmware bookkeeping, or are paths enough for Phase 1?
- **Directory semantics**: flat namespaces (easier on flash) vs real nested
  directories; the path model allows both.
- **Codecs**: ship PCM/WAV only, or advertise compressed formats (which shifts
  CPU cost onto the device)?
- **Auth**: none for a physically-connected host, or an optional pairing step?
- **Small-file efficiency**: batching many `LIST_ENTRY`/`STAT` in one packet
  (multiple messages per packet) may be worth a v1.1 flag.
- **Interop with the existing Oscuro config SysEx and DAW caps**: keep the
  bridge standalone (as chosen here) but reserve a bridge namespace for the
  config service, or leave them entirely separate?
