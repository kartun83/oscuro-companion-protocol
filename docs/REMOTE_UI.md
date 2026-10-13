# Oscuro Remote Surface — remote UI extension (proposal)

Status: **proposal only, not implemented.** This extends the File Bridge protocol
([PROTOCOL.md](PROTOCOL.md) v0, [PROPOSAL.md](PROPOSAL.md) v1) with a
**whole-device, semantic remote-UI model**: the device tells a companion app
*what* is shown and *what* is available, and the companion renders it however it
likes.

It is deliberately **implementation-agnostic**: nothing here assumes LVGL (or any
GUI toolkit), object trees, coordinates, fonts, colours or a framebuffer. The
model says **what**, not **how**. A firmware with no GUI at all can still
describe its controls and values with it.

## 1. Purpose and non-goals

**Purpose**

- Let a phone/companion app show the keyboard's on-screen UI and its controls,
  and drive them, over the existing vendor bulk pipe.
- Describe the **whole device** — the screen, the editable parameters, the
  physical controls (keys, pads, encoders, faders, wheels) and indicators — not
  just the current pixel contents.
- Never constrain the companion's layout: the device offers semantics plus
  *advisory* hints; the companion may ignore every hint.

**Non-goals**

- No pixel stream by default. An **optional** snapshot capability (§14) exists
  for companions that want exact fidelity; it is separate, opt-in and may be
  refused.
- No toolkit leakage: no `lv_obj`, no widget class names, no coordinates.
- Not a hard real-time control path; local input always works without a host.

## 2. The agnosticism rules (what, not how)

1. The semantic model carries **roles, values, state, relationships, actions and
   availability** — never rendering primitives.
2. **No coordinates, sizes in pixels, fonts, colours, or z-order** in the
   semantic tree. Presentation hints (§9) are abstract and advisory.
3. **No widget/toolkit names.** A `RANGE` node is a "value you can raise and
   lower", whether the firmware draws a knob, a slider or nothing at all.
4. The device is free to present the *same* underlying state through different
   screens; the companion is free to build its own UI from the entity model
   instead of mirroring the screens (§8, §11).
5. Unknown roles/types/flags **must be ignored**, not treated as errors.

## 3. Model overview

The device exposes **surfaces**. Each surface is a tree of **nodes**. Nodes
either *describe* something (a label, a value, a list) or *bind* to an **entity**
(a parameter, a physical control, a transport function). Values live on entities;
screens are views that reference them.

Surfaces (a firmware exposes whichever it has):

| Surface role | Meaning |
|---|---|
| `SCREEN` | what is currently on the LCD (a `SCREEN`/`DIALOG` tree) |
| `CONTROLS` | physical controls: keys, pads, encoders, faders, wheels |
| `PARAMETERS` | the editable parameter/entity model (engine, mixer, sequencer, …) |
| `INDICATORS` | LEDs and status items |
| `TRANSPORT` | play/stop/tempo/etc. semantic shortcuts |

A companion can adopt any of two strategies (both fully supported):

- **Mirror** — render the `SCREEN` surface, following the device's navigation.
- **Native** — ignore screens and lay out `PARAMETERS`/`CONTROLS` its own way.

## 4. Identifiers and addressing

- Node id: `u16`, scoped to a surface. `0` = surface root.
- Entity id: `u16`, global across the device (0 = none). A node binds to at most
  one entity; many nodes may bind to the same entity.
- A `version:u16` on each surface is bumped when its structure changes, so a
  companion can cache and re-fetch.

## 5. Roles (taxonomy)

Roles are intentionally a small, generic set; a companion maps them to native
widgets. `*` = interactive/bindable.

| Code | Role | Notes |
|---|---|---|
| 0 | `ROOT` | surface root |
| 1 | `SCREEN` | a full screen |
| 2 | `DIALOG` | modal/overlay |
| 3 | `GROUP` | container (tab page, section, panel) |
| 4 | `LIST` | scrollable collection |
| 5 | `LIST_ITEM` | one row of a list |
| 6 | `TABS` / 7 `TAB` | tab set / a tab |
| 8 | `MENU` / 9 `MENU_ITEM` | menu / entry |
| 10 | `LABEL` | static text |
| 11 | `VALUE` | formatted read-out of an entity |
| 12 | `METER` | level/position indicator |
| 13 | `PROGRESS` | progress of a task |
| 14 | `IMAGE` / 15 `ICON` / 16 `BADGE` | visual assets (by semantic id) |
| 17 | `ACTION`* | a command ("press") |
| 18 | `TOGGLE`* | boolean |
| 19 | `CHOICE`* | one of N options |
| 20 | `RANGE`* | continuous value |
| 21 | `NUMERIC`* | numeric entry |
| 22 | `TEXT_ENTRY`* | text entry |
| 23 | `DIRECTIONAL`* | dpad/nav |
| 24 | `KEY`* / 25 `PAD`* / 26 `ENCODER`* / 27 `FADER`* / 28 `WHEEL`* | physical controls |
| 29 | `TRANSPORT`* | play/stop/record/tempo function |
| 30 | `PARAMETER`* | generic bindable parameter |
| 31 | `INDICATOR` | LED/status |
| 32 | `SEPARATOR` / 33 `SPACER` | layout furniture (advisory) |
| 34 | `SELECTOR`* | pick an item from a bound collection |

Firmwares may add roles; unknown codes render as a generic node.

## 6. State, availability and reasons

Each node carries a `state:u8` and an `avail_reason:u8`:

`state` bits: `ENABLED`, `VISIBLE`, `FOCUSED`, `SELECTED`, `ACTIVE`, `BUSY`,
`DANGER` (destructive/irreversible), `READ_ONLY`.

`avail_reason` (why a node is disabled/hidden — "what's available"): `NONE`,
`MODE` (wrong mode), `NOT_APPLICABLE`, `NO_CAPABILITY`, `PERMISSION`, `LOCKED`,
`TEMPORARY` (busy, retry).

A companion can grey out / hide accordingly and explain why.

## 7. Values and formatting

A value has a `type` and a compact encoding:

| Type | Encoding |
|---|---|
| `NONE` | — |
| `BOOL` | 1 byte |
| `INT` | i32, with `min/max/step` in the desc |
| `ENUM` | u8 index; options in the desc |
| `FLOAT` | i32 × 10^-scale (scale in desc) |
| `TEXT` | UTF-8 (short) or a reference to a long-text desc |
| `COLOR` | RGB565 or a palette index |
| `TRIGGER` | no value (fire-on-invoke) |
| `DURATION` | i32 ms |

Descriptions (`UI_DESC`, §10) carry the extra fields lazily: label text, unit
(enum + optional string), min/max/step, precision/scale, enum option labels,
format string, and long text. A companion that only wants to mirror a screen can
skip most descriptions; one that builds a native editor fetches them.

## 8. Bindings (the key to non-restrictive layout)

A node with `*` binds to an **entity**:

```
bind_kind: NONE, PARAMETER, CONTROL, TRANSPORT, ACTION, PATCH, COLLECTION
bind_id:   u16 entity id
```

- `PARAMETER` — an editable value (engine/mixer/sequencer parameter). Reading and
  writing goes through the device's normal parameter path, so a companion edit is
  identical to a local edit and the device's own display updates too.
- `CONTROL` — a physical control (a key id, pad, encoder); its value is the live
  position/pressure and its actions are press/release.
- `TRANSPORT`, `ACTION` — semantic functions.
- `PATCH`/`COLLECTION` — a slot or a list of items.

Because screens *reference* entities rather than owning values, the companion
can render the same state in an entirely different layout (native mode) without
losing sync.

## 9. Presentation hints (advisory — ignorable)

`UI_HINT` (optional, per node) may suggest: `widget` (AUTO/TOGGLE/SLIDER/KNOB/
STEPPER/DROPDOWN/GRID/METER/…), `emphasis` (NORMAL/PRIMARY/SECONDARY/HIDDEN),
`order` (u16 sort key), `group` (u16 tag — the device's own grouping), `color`
(role: accent/warn/danger/success/neutral), `icon` (semantic id), `size_hint`
(S/M/L). A companion **may ignore all of them** and still be correct; they exist
only to make a mirror look intentional.

## 10. Wire messages

Remote-UI messages occupy `0x80–0x8F` (v0 uses 0x01–0x08, v1 uses 0x10–0x70).

### Discovery

| Type | Name | Dir | Payload |
|---|---|---|---|
| 0x80 | `UI_GET` | H→D | `surface:u8, root:u16, depth:u8, flags:u8` |
| 0x81 | `UI_BEGIN` | D→H | `surface:u8, root:u16, version:u16, count:u16` |
| 0x82 | `UI_NODE` | D→H | `node:u16, parent:u16, role:u8, state:u8, reason:u8, bind_kind:u8, bind_id:u16` |
| 0x83 | `UI_DESC` | D→H | `node:u16, kind:u8, len:u8, data[]` (label/unit/options/format/text) |
| 0x84 | `UI_HINT` | D→H | `node:u16, widget:u8, emphasis:u8, order:u16, group:u16, color:u8, icon:u16` |
| 0x85 | `UI_END` | D→H | `sent:u16, more:u8` |

`UI_NODE` is 17 bytes; labels and hints come separately so control frames stay
small and a companion can fetch detail lazily. `more:1` means another page
(`UI_GET` with the next `root`).

### Live state (subscribe / push)

| Type | Name | Dir | Payload |
|---|---|---|---|
| 0x86 | `UI_SUB` | H→D | `node:u16, mask:u8` (value/state/children) |
| 0x87 | `UI_UNSUB` | H→D | `node:u16` |
| 0x88 | `UI_VALUE` | D→H | `node:u16, type:u8, len:u8, value[]` |
| 0x89 | `UI_STATE` | D→H | `node:u16, state:u8, reason:u8` |
| 0x8A | `UI_EVENT` | D→H | `node:u16, kind:u8, arg:u16` — PRESS/RELEASE/CHANGED/ACTIVATED/NAVIGATED |

### Act (remote control, H→D)

| Type | Name | Payload |
|---|---|---|
| 0x8B | `UI_ACT` | `node:u16, action:u8, type:u8, len:u8, value[]` |
| 0x8C | `UI_ACT_ACK` | D→H: `node:u16, action:u8, status:u8` |

`action`: `INVOKE` (press/activate), `SET` (assign value), `ADJUST` (delta),
`FOCUS`, `NAVIGATE` (go to a screen node), `OPEN`/`CLOSE`. `DANGER`-flagged
nodes require the companion to confirm before `INVOKE`.

### Snapshot (optional, §14)

| Type | Name | Dir | Payload |
|---|---|---|---|
| 0x8D | `UI_SNAPSHOT_REQ` | H→D | `region:u8, format:u8, max_bytes:u16` |
| 0x8E | `UI_SNAPSHOT` | D→H | `fmt:u8, w:u16, h:u16, seq:u8, chunk[]` (credit-flowed, like `DATA`) |

## 11. Session flows

**Discover + mirror**

1. `GET_CAPS` advertises `UI_MODEL` and the surface list. `UI_GET(surface=SCREEN)`
   → `UI_BEGIN`/`UI_NODE`*/`UI_HINT`*/`UI_DESC`*/`UI_END`.
2. Companion renders the tree its own way.
3. `UI_SUB` on the nodes it shows; the device pushes `UI_VALUE`/`UI_STATE`/
   `UI_EVENT` on change (coalesced).

**Native layout**

1. `UI_GET(surface=PARAMETERS, depth=all)` to read the entity model once.
2. Companion lays it out freely and binds controls to entity ids.
3. `UI_ACT(SET/ADJUST)` on entity-backed nodes; device echoes `UI_VALUE`.

**Remote control / mirror input**

- A press on the companion's copy of a key: `UI_ACT(node, INVOKE)`. The device
  applies it through its normal input path (so local and remote are the same),
  then emits `UI_EVENT`.

**Navigation**

- The active screen, focus and modal stack are reported as `UI_STATE`/`UI_EVENT`
  on the `SCREEN` surface root, so a mirror follows the device.

## 12. Whole-device entities

| Surface | Examples |
|---|---|
| `SCREEN` | the LVGL *result* only — as roles/values, never as LVGL objects |
| `CONTROLS` | `KEY`/`PAD` (pitch 0–127, velocity), `ENCODER` (relative ±), `FADER`/`WHEEL` (position), each with live state and optional binding to a parameter |
| `PARAMETERS` | engine/mixer/sequencer parameters, grouped via `GROUP`/`TABS`; large sets are paged |
| `INDICATORS` | LED/status nodes with a colour/palette value |
| `TRANSPORT` | play/stop/record/tempo as `TRANSPORT` nodes |

A firmware that isn't graphical simply exposes `CONTROLS`/`PARAMETERS` and no
`SCREEN`.

## 13. Extensibility and versioning

- Roles, value types and flags are additive; unknown values render generically.
- `UI_DESC.kind` and `bind_kind` are open enums.
- The surface `version` invalidates caches; a companion re-fetches on change.
- Optional JSON serialization (an offline mapping of the same tree) is allowed
  for capable hosts, but the on-wire form stays the compact binary above.

## 14. Optional snapshot capability

Feature bit `UI_SNAPSHOT`, off unless advertised. `UI_SNAPSHOT_REQ` asks the
device to render a region to a small raster (e.g. `RGB565`, or `RGB565+RLE`) and
stream it with the §5 credit flow of PROPOSAL.md. Uses: a companion that wants to
show *exactly* what the LCD shows, a screenshot button, or a thumbnail in a
selector. It **may be refused** (`E_UNSUPPORTED`/`E_BUSY`) and is never required;
the semantic model is the contract, the snapshot is a convenience. Because it is
pixel-bearing it is explicitly outside the "what, not how" core.

## 15. Update strategy and performance

- Push only changes: `UI_VALUE`/`UI_STATE`, not whole trees.
- Coalesce high-rate values (encoders, levels) — send at most ~30 Hz per node.
- Page large models; `UI_GET(depth)` limits how deep a fetch walks.
- `DANGER` actions and bulk changes are rate-limited and acknowledged.

## 16. Security and safety

- Describing the UI is harmless; **acting** is gated by capability flags and node
  permissions. Read-only nodes reject `UI_ACT(SET)` with `E_ACCESS`.
- `DANGER` nodes (delete/overwrite/erase) require explicit confirmation and are
  the only place a host can cause irreversible change.
- No firmware/OTA update through this channel (as before).
- Remote input never blocks local input; the device may ignore remote actions
  while a modal/mode forbids them and reports `reason=MODE`.

## 17. Interaction with the rest of the bridge

- Values written via `UI_ACT` go through the same parameter/command path as local
  edits, so the device's display, presets and MIDI output stay consistent.
- Lists of files/presets reuse the v1 listing and transfer; a `SELECTOR` node may
  bind to a `COLLECTION` entity served by `LIST_REQ2`.
- The File Bridge stays usable without any UI model (a device may advertise
  `UI_MODEL` not at all).

## 18. Utilization

- **Companion app mirrors the screen** for reachability/accessibility, or shows a
  large-screen version.
- **Native control surface**: build a phone UI from `PARAMETERS`/`CONTROLS` with
  a completely different layout, still in sync.
- **Remote control / macros**: invoke device actions and keys programmatically.
- **Screenshots** (optional snapshot) for docs/support.
- **Accessibility**: the same tree feeds screen readers and other assistive
  tools.

## 19. Open questions

- Node id model: per-surface u16 vs one global u32 address space?
- How much of the parameter model to expose at once (full vs demand-paged)?
- Should `UI_ACT` ever bypass the device's own mode gating, or always respect it?
- Snapshot format: RGB565 raw only, or also a tiny RLE/PNG-like option?
- Formal mapping to ARIA/WoT concepts for interop (documentation-only)?
