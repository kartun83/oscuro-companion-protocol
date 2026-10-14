# Oscuro Companion Protocol — v1 (draft)

Status: **draft for review, nothing implemented.** Supersedes the message layouts in
[PROPOSAL.md](PROPOSAL.md) and the wire section (§10) of [REMOTE_UI.md](REMOTE_UI.md); the
goals, the capability idea, the role/state/binding model and the "what, not how" rule of
those documents stay. [PROTOCOL.md](PROTOCOL.md) (v0) is the prototype that ran on a unit and
stays valid for old devices (§12). Feasibility on the SMK-37: [FEASIBILITY.md](FEASIBILITY.md).

Keywords MUST / SHOULD / MAY are used as in RFC 2119.

## 1. Design rules

1. **Not restrictive.** Every payload is tagged fields; unknown tags, features and message
   types are ignored, never fatal. New capabilities add tags and feature ids, not new framings.
2. **Capability-driven.** A host branches on advertised features, namespaces and limits,
   never on a device name or VID/PID.
3. **The device serves; the host asks.** Namespaces, entities and screens live on the device.
   A device never serves a list to the host (v0's symmetric exchange is legacy, §12).
4. **Small-device friendly.** A conforming device needs only 64-byte messages, a handful of
   handles and no heap. Larger messages and deeper features are negotiated, never assumed.
5. **Control is never starved by data.** Announcements and UI events overtake file data at
   message boundaries (§5).
6. **Writes are atomic or absent, and polite.** A device may refuse or delay writes while it
   is busy (playing audio) and says so; the host retries (§7.5).
7. **No firmware update through this protocol.** Out of scope by design; stays a separate,
   explicitly confirmed path.

## 2. Transport

A USB interface with class `0xFF` (vendor), subclass `0x4F`, protocol `0x01` (v1), one bulk IN
and one bulk OUT endpoint. Hosts MUST find it by class triple, not VID/PID, so it can live as
one interface of a composite device (MIDI, audio and HID next to it). The interface string
SHOULD be human readable ("Oscuro Companion").

Full speed uses 64-byte packets; high speed 512. The protocol does not depend on the speed.

**Alternative carriers.** Small controllers have few endpoints (the WL82 family exposes EP0-EP4, and
a MIDI + CDC + audio composite already uses them all), so a dedicated interface is not always
possible. The message format is carrier-independent; a device MAY carry the same messages
over (a) an existing bulk pipe such as the CDC-ACM data pair, with each message prefixed by a u16
length because a byte stream has no transfer boundaries and the PAD rule does not apply, or
(b) USB-MIDI SysEx, packed 7 bits per byte between `F0 <manufacturer/product ids> ... F7` (about 14%
overhead; the 64-byte baseline message becomes about 74 SysEx bytes). The carrier is chosen
by what the host finds; the HELLO exchange is identical. Only the dedicated interface is
specified byte for byte in this draft; (a) and (b) need their own short appendices.

A **message is exactly one USB transfer**: a sequence of full packets ended by a short packet.
Messages never span transfers and transfers never carry two messages. The first message a
device receives after `SET_CONFIGURATION` or a bus reset starts a fresh session.

**Zero-length-packet avoidance.** Android's `bulkTransfer` and some stacks do not send ZLPs.
If a message length is a multiple of the endpoint's max packet size, the sender sets the PAD
flag and appends one zero byte; the receiver drops the last byte of a PAD message.

**Size.** Every device MUST accept messages up to 64 bytes. `max_msg` (HELLO) raises the limit
per direction; senders never exceed the peer's value.

## 3. Message header

All integers are little-endian.

```
+0  u8   type       message type (§4); the range implies the service
+1  u8   flags      bit0 RESP   this message answers request `req`
                    bit1 MORE   further messages follow for the same `req`
                    bit2 PAD    one trailing pad byte (§2)
                    bit3..7     reserved, zero; receivers ignore them
+2  u16  req        request id chosen by the requester; 0 = unsolicited / no reply expected
+4  ..   fields     tagged fields (§3.1)
```

A requester picks non-zero `req` values and keeps them unique among its outstanding requests.
A responder copies `req` and sets RESP. A multi-message answer sets MORE on all but the last.
A request that gets no answer within 2 s (or the peer's `retry_ms`) MAY be retried with the
same `req`; operations that change state MUST be idempotent per `req` or carry an explicit
precondition (§7.4).

### 3.1 Tagged fields

```
tag u8 | len u8 | value[len]      len 0..254; len 0xFF = extended, followed by u16 length
```

- Fields may appear in any order; a tag that is not defined for a message is ignored.
- A tag MAY repeat (lists); order is preserved.
- **Integers** are minimal little-endian (1, 2, 4 or 8 bytes). A receiver accepts any length
  up to the field's maximum and zero-extends.
- **Strings** are UTF-8, no terminator.
- **Group** (tags `0x3A..0x3E`): the value is itself a sequence of fields. Lists use one group per
  record, so a message can carry many records (this also answers small-file efficiency). The tag
  says what the record is: `0x3A` limits, `0x3B` namespace, `0x3C` format, `0x3D` ui caps,
  `0x3E` generic record (object, entity, option, node).
- **Raw tail** (tag `0x00`): the rest of the message after the tag byte is raw data with no
  length byte. Only `DATA` and a few snapshot types use it; it MUST be the last field.
- Ranges: `0x01..0x3F` common, `0x40..0x7F` per service (§8-§10), `0x80..0xBF` reserved,
  `0xC0..0xFF` vendor/experimental.

Common tags:

| Tag | Name | Type | Meaning |
|---|---|---|---|
| 0x01 | `proto` | u16 | version `major<<8 \| minor`; repeatable (list) |
| 0x02 | `peer` | str | sender name, for logs |
| 0x03 | `max_msg` | u16 | largest message the sender will accept |
| 0x04 | `role` | u8 | 0 host, 1 device |
| 0x05 | `features` | u16[] | packed list of feature ids (§6) |
| 0x06 | `code` | u16 | status / error code (§11) |
| 0x07 | `text` | str | free text |
| 0x08 | `ns` | u8 | namespace id |
| 0x09 | `id` | u32 | object id |
| 0x0A | `name` | str | object name, one path component |
| 0x0B | `size` | u64 | bytes; absent = unknown (§7.6) |
| 0x0C | `mtime` | u32 | unix seconds |
| 0x0D | `format` | u8 | format id (CAPS) |
| 0x0E | `oflags` | u16 | object flags: 1 DIR, 2 READONLY, 4 VIRTUAL, 8 SIZE_UNKNOWN, 16 BUSY |
| 0x0F | `rev` | u32 | object revision; changes on every content change |
| 0x10 | `handle` | u8 | open-file / stream handle |
| 0x11 | `offset` | u64 | byte offset |
| 0x12 | `length` | u32 | byte count |
| 0x13 | `credits` | u16 | flow-control credit (messages) |
| 0x14 | `crc32` | u32 | CRC-32 (IEEE 802.3, init/final 0xFFFFFFFF) |
| 0x15 | `mode` | u8 | open mode (§7.3) |
| 0x16 | `parent` | u32 | parent object id (0 = namespace root) |
| 0x17 | `retry_ms` | u16 | retry hint |
| 0x18 | `start` | u32 | list paging start |
| 0x19 | `count` | u16 | records wanted / sent |
| 0x1A | `more` | u8 | 1 = more pages exist |
| 0x1B | `total` | u32 | total records |
| 0x1C | `if_rev` | u32 | precondition: object must still have this `rev` |
| 0x1D | `new_name` | str | rename / copy target name |
| 0x1E | `filter_fmt` | u8 | repeatable format filter |
| 0x1F | `sort` | u8 | 0 none, 1 name, 2 size, 3 mtime; +0x80 = descending |
| 0x20 | `path` | str | optional `/`-separated path under `parent` (feature PATHS) |
| 0x21 | `req_ref` | u16 | the `req` an event or error refers to |

## 4. Message types

The type range selects the service; no channel field is needed. Senders SHOULD interleave
by service priority (§5). Direction: H→D host to device, D→H device to host.

| Type | Name | Dir | Service |
|---|---|---|---|
| 0x01 | `HELLO` | both | control |
| 0x02 | `CAPS_GET` / `CAPS` | H→D / D→H | control |
| 0x03 | `PING` / `PONG` | both | control (PONG = PING with RESP) |
| 0x04 | `ERR` | both | control |
| 0x05 | `NAK` | both | control, "busy, retry" |
| 0x06 | `SUBSCRIBE` | H→D | control, event kinds wanted |
| 0x07 | `EVENT` | D→H | control |
| 0x08 | `CREDIT` | both | control, grant flow credit |
| 0x10 | `LIST` | H→D | files |
| 0x11 | `STAT` | H→D | files |
| 0x12 | `MKDIR` / `0x13 RENAME` / `0x14 DELETE` / `0x15 COPY` | H→D | files |
| 0x16 | `OPEN` | H→D | files |
| 0x17 | `DATA` | both | files |
| 0x18 | `PROGRESS` | D→H | files |
| 0x19 | `COMMIT` | H→D | files |
| 0x1A | `ABORT` / `CLOSE` | both | files (`0x1B` CLOSE) |
| 0x20 | `ANNOUNCE` | D→H | ui |
| 0x21 | `ENT_GET` / `0x22 ENT_LIST` | H→D / D→H | ui, entity registry |
| 0x23 | `ENT_VALUE` | both | ui, value push or set |
| 0x24 | `INPUT` | D→H | ui, physical-control interaction |
| 0x25 | `FOCUS` | D→H | ui, navigation focus |
| 0x26 | `SCREEN_GET` / `0x27 SCREEN` / `0x28 SCREEN_DELTA` | both | ui, screen tree |
| 0x29 | `ACT` | H→D | ui, act on an entity or node |
| 0x2A | `SNAPSHOT_GET` / `0x2B SNAPSHOT` | both | ui, optional raster |
| 0x30 | `STREAM_OPEN` .. `0x34 STREAM_CLOSE` | both | streams (§10) |
| 0xC0..0xFF | | | vendor |

A device receiving an unknown type with non-zero `req` answers `ERR(code=UNSUPPORTED)`;
with `req = 0` it drops the message. A host does the same.

## 5. Priorities and flow control

Services in priority order: control (`0x00-0x0F`), ui (`0x20-0x2F`), files/streams. A sender
that has more than one message queued MUST send higher-priority ones first **between**
messages (no preemption inside a transfer). To bound the delay, `DATA` payloads are small
(§7.3): at full speed a 64-byte message is about 0.1 ms on the wire.

- **Files**: credit window in messages per handle (§7.3).
- **Events, announcements and UI pushes** are limited by the host: `SUBSCRIBE` chooses event
  kinds and the host grants `CREDIT` with `credits = n` and no `handle`; every unsolicited
  message (`EVENT`, `ANNOUNCE`, a pushed `ENT_VALUE`) costs one credit. The device sends at most
  that many and then coalesces (latest value wins for `ENT_VALUE`; `ANNOUNCE`
  queues keep the newest `announce_queue` entries, dropping the oldest low-priority ones).
- A device that cannot accept a request now answers `NAK` (a RESP with the request's `req`,
  `req_ref` and `retry_ms`); the host retries with a **new** `req` after the delay.
- Messages that belong to a handle (`DATA`, per-handle `CREDIT`) carry `req = 0`; they are
  matched by `handle`. A device never sends a message larger than the host's `max_msg`, and a
  host drops and reports one that is.

## 6. Session and capabilities

```
host:    HELLO  req=1  proto=0x0100 [proto=...]  peer="Android" max_msg=512
device:  HELLO  req=1 RESP  proto=0x0100  peer="SMK-37 Oscuro 0.69" max_msg=64
                       role=1  features=[...]
host:    CAPS_GET req=2   (code = section: 0 all, 1 limits, 2 ns, 3 formats, 4 ui)
device:  CAPS req=2 RESP|MORE ... CAPS req=2 RESP
```

- The host lists the versions it speaks; the device answers with the highest it also speaks,
  or `ERR(UNSUPPORTED_VERSION)` and a list. Minor versions only add; a major bump is allowed
  to break.
- `features` is a list of u16 **feature ids**, an open registry (below). A host MUST NOT use a
  message or option whose feature id was not advertised.
- `CAPS` answers use one group per record (tags `0x3A..0x3D`, §3.1), several per message as they
  fit `max_msg`; `CAPS_GET` takes an optional `code` section (0 all, 1 limits, 2 ns, 3 formats, 4 ui):
  - **limits** group: `max_msg`, `max_name` (0x40), `max_path` (0x41), `max_handles`
    (0x42), `max_inflight` (0x43, messages), `max_data` (0x44, bytes per `DATA`),
    `write_policy` (0x45: 0 anytime, 1 when idle, 2 never), `announce_queue` (0x46).
  - **namespace** group: `ns`, `name`, `oflags` defaults, `role` (0x47: 1 patches, 2 projects,
    3 samples, 4 recordings, 5 settings, 6 system, 7 scratch, 8 log, 9 exports),
    `access` (0x48 bits: 1 read, 2 write, 4 delete, 8 create, 16 volatile, 32 removable,
    64 confirm-on-device), `capacity` (0x49 u64), `free` (0x4A u64).
  - **format** group: `format`, `ext` (0x4B), `mime` (0x4C), `caps` (0x4D bits: 1 read, 2 write,
    4 stream-source, 8 stream-sink), `role`.
  - **ui** group: see §9.

Feature ids (u16; unknown ids ignored; 0x8000+ vendor):

| Id | Name | Needs |
|---|---|---|
| 1 | `LIST` | listing and `STAT` |
| 2 | `READ` | `OPEN(read)` |
| 3 | `WRITE` | `OPEN(write)`, `COMMIT` |
| 4 | `DELETE`, 5 `RENAME`, 6 `MKDIR`, 7 `COPY` | the matching message |
| 8 | `SEEK` | read at `offset` |
| 9 | `RESUME` | write resume (§7.3) |
| 10 | `CRC32` | crc at commit and `PROGRESS` |
| 11 | `PATHS` | `path` addressing and directories |
| 12 | `EVENTS` | `SUBSCRIBE`/`EVENT` |
| 13 | `FREE_SPACE` | `capacity`/`free` kept current |
| 14 | `VIRTUAL` | generated, possibly size-unknown objects |
| 15 | `CONFIRM` | on-device confirmation for risky writes |
| 16 | `LARGE_MSG` | peer accepts `max_msg` > 64 |
| 32 | `UI_ANNOUNCE`, 33 `UI_ENTITIES`, 34 `UI_SCREEN`, 35 `UI_CONTROL`, 36 `UI_SNAPSHOT` | §9 |
| 48 | `STREAM_MIDI`, 49 `STREAM_AUDIO` | §10 |

## 7. Files service

### 7.1 Model

A device exposes **namespaces**; a namespace holds **objects**. An object is a record with a
stable `id` (u32, unique within its namespace for as long as it exists), a `name`, optional
`parent` (feature PATHS builds directories), `size`, `mtime`, `format`, `oflags` and a `rev`
that changes on every content change. Ids are the reliable handle; names are the human one.
A flat namespace (no PATHS) is valid and is what flash-backed record stores expose.

Names: UTF-8, at most `max_name` bytes, no `/`, no NUL, not `.` or `..`. A device MAY
restrict the character set; it answers `ERR(BAD_NAME)`.

### 7.2 Listing and metadata

`LIST`: `ns`, optional `parent`, `start`, `count`, `filter_fmt`*, `sort`. Answer: one or more
`LIST` RESP messages, each with groups (tag `0x3E`) of object fields (`id`, `name`, `size`,
`mtime`, `format`, `oflags`, `rev`), and `total`, `more`. Paging by `start` is stable only while
the namespace `rev` (a `NS_REV` event, §8) is unchanged; hosts re-list on change.
`STAT`: `ns` + `id` (or `parent` + `name`, or `path`) -> one object group.
`MKDIR`/`RENAME`/`DELETE`/`COPY`: `ns`, `id` (or name/path), `new_name`/`parent` as needed;
answer is the resulting object group or `ERR`.

### 7.3 Open, read, write

`OPEN`: `ns`, `id` or (`parent`+`name`) or `path`, `mode`, optional `offset`, optional `if_rev`,
optional `size` (announced size for writes), optional `crc32`.

| `mode` | Meaning |
|---|---|
| 0 | read |
| 1 | create (fails if exists) |
| 2 | replace (atomic; old content stays until `COMMIT`) |
| 3 | append |
| 4 | resume an interrupted replace/create at `offset` |

Answer `OPEN` RESP: `handle`, `size`, `rev`, `id`, `credits` (messages the device will take
before another `CREDIT`), `max_data`. Reads: the device sends `DATA` messages up to the
credit it was given and waits for `CREDIT` from the host. Writes: the host sends `DATA` up to
`credits`; the device returns `CREDIT` as it drains.

`DATA`: `handle`, `offset`, raw tail (tag `0x00`). With minimal encodings this is
4 + 3 + (2 + offset width) + 1 = 12 to 14 bytes of overhead, leaving 50 to 52 bytes in a 64-byte
message (offsets under 64 KiB use two bytes). A receiver rejects
out-of-order or overlapping offsets with `ERR(OUT_OF_ORDER)` carrying the offset it expects.
`PROGRESS` (optional, device): `handle`, `offset` (bytes durably accepted), `crc32` (of
`[0, offset)`); a host resumes from the last `PROGRESS`.
`COMMIT`: `handle`, `size`, `crc32`. The device verifies, publishes atomically and answers with
the object group (new `rev`). `CRC_MISMATCH` leaves the previous object untouched. `ABORT`
(either side) discards a write; `CLOSE` ends a read or a finished write.

### 7.4 Preconditions and idempotence

`if_rev` on `OPEN(replace)`/`DELETE`/`RENAME` fails with `ERR(REV_MISMATCH)` if the object
changed (for example a patch edited on the keyboard meanwhile). Retrying `COMMIT` with the
same `req` after a lost answer returns the stored result without re-applying.

### 7.5 Busy devices

If `write_policy` is "when idle", a write `OPEN` while the device is busy gets
`NAK(retry_ms)` or `ERR(BUSY_PLAYING)`. A device that must pause for a slow flash erase MAY
delay a `CREDIT` or answer `NAK` mid-transfer; the host waits. Writes MUST NOT hold the
device's real-time work: erase/program in bounded slices between other work.

### 7.6 Virtual and growing objects

A `VIRTUAL` object is generated at read time (for example a `.mid` rendered from a sequencer
project, or a diagnostics log). Its `size` MAY be absent (`SIZE_UNKNOWN`): the stream ends when the
device sends `DATA` with the final-flag field `fin` (0x4E, u8 = 1) or answers `CLOSE`. Virtual
objects are read-only unless the namespace says otherwise.

### 7.7 Risky writes

A namespace with `access` bit 64 (confirm-on-device) answers a write/delete `OPEN` with
`ERR(CONFIRM_REQUIRED)` and shows a confirmation on the device; the host repeats the `OPEN`
after an `EVENT(CONFIRMED)`, or gets `ERR(DENIED)`. This guards whole-area images and
anything that overwrites user data wholesale.

## 8. Control service

- `ERR`: `code`, `req_ref`, optional `text`. `NAK`: `req_ref`, `retry_ms`.
- `SUBSCRIBE`: repeatable `kind` (0x4F u8); empty = none. `EVENT`: `kind`, `req_ref`
  optional, `id`/`ns` optional, `value` (0x50 u32). Kinds: 1 `NS_REV` (namespace changed),
  2 `OBJECT_CHANGED`, 3 `STORAGE_LOW`, 4 `CONFIRMED`, 5 `DENIED`, 6 `BUS_RESET`,
  7 `RECORDING_DONE`, 8 `REMOTE_CONTROL_STATE` (§9.4). Unknown kinds are ignored.
- `PING`/`PONG` carry an opaque `text`; a host SHOULD ping after 3 s of silence.

## 9. UI service (extra display, accessibility, optional control)

Built in tiers so a small device implements only what it can hold in RAM, and an
accessibility companion works with the first two. The role/state/binding vocabulary is the
one in [REMOTE_UI.md](REMOTE_UI.md) §2-§9; this section is its wire form.

| Tier | Feature | Purpose | Device cost |
|---|---|---|---|
| 0 | `UI_ANNOUNCE` | text events: what changed, what the screen says | a small ring of strings |
| 1 | `UI_ENTITIES` | named parameters and controls with values | a table that already exists in most firmwares |
| 2 | `UI_SCREEN` | the current screen as a node tree | a serializer over the UI model |
| 3 | `UI_CONTROL` | `ACT` on entities and nodes | routed through the normal input path |
| - | `UI_SNAPSHOT` | optional raster of a region | usually too large; opt-in |

### 9.1 Tier 0: announcements

`ANNOUNCE` (D→H, unsolicited): `text` (the **speakable** form: full words, units spelled out),
`short` (0x60, the on-screen form, may be abbreviated), `prio` (0x61: 0 background, 1
normal, 2 important, 3 urgent/interrupt), `cat` (0x62: 1 navigation, 2 value, 3 notice, 4 error,
5 transport, 6 mode), `eid` (0x63 u16, optional entity), `lang` (0x64, BCP-47, optional),
`seq` (0x65 u16, increases per device). Meaning: "an assistive companion would say this now".
The device decides what is announced from its own UI events (page change, value change, popups);
the companion decides how (speech, braille, haptics, large text) and may drop by priority.

### 9.2 Tier 1: entities

An **entity** is anything with a name and a value or action: a parameter, a physical control,
a transport function, a patch slot. `ENT_GET` (pages) answers `ENT_LIST` groups:
`eid` (0x63), `name` (0x0A, **long speakable name**), `short` (0x60), `kind` (0x66: the roles of
REMOTE_UI.md §5), `vtype` (0x67: BOOL, INT, ENUM, FLOAT, TEXT, TRIGGER, DURATION), `min`/`max`/`step`
(0x68-0x6A, i32), `scale` (0x6B), `unit` (0x6C, str), `options` (0x6D, repeatable group
`idx`+`name` for ENUM), `group` (0x6E, parent eid), `eflags` (0x6F: 1 READONLY, 2 HIDDEN, 4 NOT_APPLICABLE,
8 DANGER), `reason` (0x70, REMOTE_UI.md §6). The registry has a `reg_rev`; a change is signalled as
`EVENT(OBJECT_CHANGED)` with `ns = 0xFF`.

`ENT_VALUE`: D→H push (subscribed or after change) and H→D set (needs `UI_CONTROL`): `eid`,
`val` (0x71, typed per `vtype`), `text` (display string formatted by the device, e.g. "-12.5 dB"),
`speak` (0x72, optional spoken form), `src` (0x73: 0 firmware, 1 physical control, 2 remote,
3 automation). A host that sets a value gets the device's resulting `ENT_VALUE` back (the
value may be clamped or refused).

### 9.3 Physical interaction events

`INPUT` (D→H): the player touched or moved something. `eid` (the control's entity), `act`
(0x74: 1 touch, 2 release, 3 press, 4 turn, 5 long-press), `val` (current value, optional), `text`,
`speak`. `FOCUS` (D→H): the device's navigation focus moved: `eid` or `node`, `text`, `speak`,
`path` (breadcrumb). These two are what an accessibility app needs to say "Filter cutoff,
64" when a knob is touched or "Settings, USB" when a menu moves.

### 9.4 Tier 2 and 3

`SCREEN_GET`/`SCREEN`/`SCREEN_DELTA`: node tree with `node` (0x75 u16), `parent` (0x16), `role` (0x7A), `state` (0x7B), `reason` (0x70),
`bind` (0x7C), `bind_id` (0x7D), `rev` (0x76 u16), optional hint group (0x7E), exactly the model of REMOTE_UI.md,
with `SCREEN_DELTA` carrying only changed nodes and the new `rev`.
`ACT` (H→D, needs `UI_CONTROL`): `node` or `eid`, `op` (0x77: 1 invoke, 2 set, 3 adjust(+/-`val`),
4 navigate(up/down/left/right/ok/back), 5 select), `val`. The device applies it through
the **same path as local input**, then reports via `ENT_VALUE`/`SCREEN_DELTA`.

**Gating.** Control is off unless the player enables "remote control" on the device. The
device advertises the state (`EVENT(REMOTE_CONTROL_STATE)`) and answers `ERR(DENIED)` when
off. Read-only tiers (0-2) are always available while connected. Local input always wins and
the device never blocks real-time work for a remote request.

`SNAPSHOT_GET`/`SNAPSHOT`: `region` (0x78 four u16), `fmt` (0x79: RGB565, RGB332, 1bpp, RLE),
chunked with the raw tail. Opt-in.

## 10. Streams (optional, capture and transfer only)

Feature `STREAM_MIDI`/`STREAM_AUDIO`, types `0x30..0x34`: `STREAM_OPEN` (`dir`, `media`, `codec`,
`rate`, `chan`, `bits`, `block`), `STREAM_READY` (`handle`, `credits`), `STREAM_DATA` (`handle`,
`seq`, `ts`, raw tail), `STREAM_CTL`, `STREAM_CLOSE`. Live monitoring and DAW audio/MIDI stay on
the device's USB audio and MIDI interfaces, which every host OS already supports with
host-side clocking. The stream service exists for devices with storage or a recording buffer
that want to hand a take to the host. **A device without such a buffer does not advertise
it** (the SMK-37 does not, see FEASIBILITY.md).

## 11. Error codes (`code`, u16)

| Code | Name | Code | Name |
|---|---|---|---|
| 1 | `UNSUPPORTED` | 12 | `BUSY` |
| 2 | `UNSUPPORTED_VERSION` | 13 | `BUSY_PLAYING` |
| 3 | `BAD_REQUEST` | 14 | `IO` |
| 4 | `BAD_NAME` | 15 | `CRC_MISMATCH` |
| 5 | `NOT_FOUND` | 16 | `ABORTED` |
| 6 | `EXISTS` | 17 | `OUT_OF_ORDER` |
| 7 | `ACCESS` (read-only) | 18 | `REV_MISMATCH` |
| 8 | `NO_SPACE` | 19 | `CONFIRM_REQUIRED` |
| 9 | `TOO_BIG` | 20 | `DENIED` |
| 10 | `NO_HANDLE` | 21 | `AUTH` |
| 11 | `TOO_MANY` | 22 | `PROTOCOL` |

`0x8000+` vendor. Unknown codes are treated as generic failure.

## 12. Compatibility with v0

v0 devices expose interface protocol `0x00` and 'OF'-framed 64-byte messages
([PROTOCOL.md](PROTOCOL.md)). A host that finds interface protocol `0x00` speaks v0
(list exchange only). A device MAY expose both interfaces. v1 never uses the 'OF' magic;
the two cannot be confused because the interface protocol differs.

## 13. Security and safety

- The bridge is available only to a physically attached host. Optional authentication
  (`AUTH`, `ERR(AUTH)`) is reserved; v1 defines no scheme.
- No firmware/OTA, boot, or radio-calibration data through the files service. System
  namespaces are read-only.
- Names and lengths are validated before use; offsets must be in order; sizes are checked
  against `capacity`; the device bounds its handles and queues.
- Risky writes need on-device confirmation (§7.7). Remote control needs an on-device switch (§9.4).
- Rate limits: a device MAY `NAK` floods; it never lets host traffic delay its real-time
  path (audio, MIDI, scan).

## 14. Example (byte level)

Host `HELLO`, version 1.0, name "Android", `max_msg` 512, `req` 1:

```
01 00 01 00                      type=HELLO flags=0 req=0x0001
01 02 00 01                      proto = 0x0100 (LE bytes 00 01)
02 07 41 6E 64 72 6F 69 64       peer = "Android"
03 02 00 02                      max_msg = 0x0200
```

(21 bytes, one transfer, one short packet.) A `DATA` message carrying 52 bytes at offset 0x1000
on handle 5 is `17 00 00 00 | 10 01 05 | 11 02 00 10 | 00 <52 raw bytes>`: 4 + 3 + 4 + 1 + 52 = 64
bytes exactly, a multiple of the full-speed packet size, so the PAD flag is set and one zero byte
follows: header `17 04 00 00`, 65 bytes on the wire (a full packet plus a 1-byte short packet).

## 15. Decisions and open questions

| | |
|---|---|
| D1-D7 | unchanged from PROPOSAL (device-agnostic; no OTA; atomic writes; realtime audio/MIDI stays on class interfaces; semantic UI) |
| D8 | tagged fields everywhere; no fixed positional payloads |
| D9 | one message = one transfer; header carries `req`; no channel field |
| D10 | device always serves; v0's symmetric list is legacy |
| D11 | ids first, paths optional (flat namespaces are first-class) |
| D12 | UI in tiers; announcements and entities carry a speakable form separate from the display form |
| D13 | control (`ACT`) is off by default on the device |

Open:
- Android throughput of synchronous `bulkTransfer` versus queued `UsbRequest` at 64 and 512 bytes.
- Whether `max_msg` above 64 is worth its per-handle buffers on small devices.
- Entity id stability across firmware versions (needed by a stored accessibility profile).
- A shared registry for entity kinds beyond REMOTE_UI.md roles.
- Localisation of `text`/`speak`: device-supplied strings only, or message ids plus a table.
- Authentication scheme, if ever needed.
