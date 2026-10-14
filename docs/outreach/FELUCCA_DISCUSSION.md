# Felucca Discussion: draft (not posted)

Status 2026-10-14: **draft for the owner's review. Nothing has been posted.** Posting is the owner's
decision and action (public content under their GitHub account).

## Where and why

- **Venue:** a new thread in `hugelton/Felucca` Discussions, category **Ideas** (Felucca's CONTRIBUTING:
  features go to Discussions first; issues for features get closed). Also worth a one-line cross-link
  in [#220](https://github.com/hugelton/Felucca/discussions/220) ("Use Mobile Phone as bigger screen
  via USB?"), where the maintainer says `felucca-mirror` has the canvas but "the firmware side and the
  protocol aren't done" and PRs are welcome.
- **Context that shapes the tone** (read 2026-10-14): the maintainer announced in
  [#230](https://github.com/hugelton/Felucca/discussions/230) that active development slows down and
  contributions should be community-driven, small, and tested on real hardware. So the post asks
  for a design opinion and offers to do work; it does not ask the maintainer to do any.
- **Overlaps to acknowledge, not duplicate:** `felucca-mirror` (planned: UI-state stream over USB-MIDI
  SysEx, pixel-exact WASM render), [#70](https://github.com/hugelton/Felucca/discussions/70)
  (single project export/import, shipped in 1.6 via the web editor), [#201](https://github.com/hugelton/Felucca/discussions/201)
  (an SMK-37 port question), `felucca-bsp`.
- **Before posting:** (1) push the new docs, since the links below 404 until `main` has
  `PROTOCOL_V1.md`, `FEASIBILITY.md` and `host/`; (2) decide the AI-disclosure line (Felucca's README
  has its own AI statement and welcomes AI-assisted PRs); (3) say whether an FM-1 is on hand: the
  text below says it is **not** and must be edited if that is wrong; (4) re-read for the owner's voice.

## Title

Idea: a small open companion protocol (files, extra display, spoken UI) that could sit beside the
`felucca-mirror` plan: spec + simulator, looking for an FM-1 reality check

## Body

Hi Leo and everyone,

I'm working on an open firmware for the SMK-37 (same chip family as the FM-1, see #201) and I've
been designing a USB companion protocol for a phone or computer to talk to the keyboard. Since you
already have the mirror plan and #220 asks for the phone-as-screen case, I'd like to share it in
case it is useful, and to ask whether any of it fits the FM-1. I'm not proposing to change
Felucca's direction, and I know development is slowing down, so this is a question and an offer,
not a request.

**What exists today** (all in https://github.com/kartun83/oscuro-companion-protocol, Apache-2.0):

- `docs/PROTOCOL_V1.md`: a draft v1 spec. One message per USB transfer; tagged fields so unknown
  things are ignored and the protocol can grow; found by USB class triple so it can live inside a
  composite device; usable at full speed with 64-byte messages.
- `host/`: a reference client and a device simulator in Python (standard library only), 43 tests,
  including the byte vectors from the spec. It runs without any hardware.
- On the SMK-37 only a throw-away prototype has run (a vendor-bulk device exchanging fake file
  lists with an Android app, 2026-10-13). **Nothing of v1 has run on hardware, and nothing has run on an
  FM-1.**

**What the protocol covers**, in tiers a small firmware can adopt one at a time:

1. **Files/objects**: list, read, create/replace (atomic, CRC-checked), delete, rename, for things
   like projects, kits, samples and user presets. The device can say "busy, retry" while it is
   rendering audio, because a flash erase must not glitch the sound.
2. **Announcements**: short text events ("what the screen would say now") with a spoken form and a
   display form. Useful on their own for a screen-reader style companion app.
3. **Entities**: named parameters with ranges and values, pushed to the phone as they change.
4. **Screen tree / remote control**: optional; control is off unless the player enables it on the
   device.

It does not try to compete with the mirror plan. Mirror is pixel-exact by sharing the UI code with
a WASM build; this is a semantic layer (names, values, state) that a phone app can lay out its own
way. They could share a carrier, or the mirror state stream could be a feature of this protocol.

**Where I'd value your opinion (and where I'm unsure about the FM-1):**

- **Endpoints.** The FM-1's controller exposes EP1-EP4 (`fm1_usb.h`), and the composite already uses
  EP1 MIDI, EP2/EP3 CDC, EP4 UAC. A new vendor interface would not fit next to all of them. The spec
  therefore needs to run over an existing pipe (the CDC bulk pair, or SysEx on the MIDI pipe). Is one of
  those the right carrier for you, or is the mirror's SysEx path the better common ground?
- **Storage.** Projects, kits and samples already move through the web editor over Web MIDI. Is there
  something a file service would add for FM-1 users (background transfer from a phone, the system
  Files app on Android), or does the editor cover it?
- **Appetite.** If this is not wanted in Felucca, that is fine; the spec stays device-agnostic and
  the SMK-37 will use it regardless.

**What I could do, only if useful:** review `felucca-mirror`'s planned message set against the
tagged-field format, write a minimal FM-1 carrier proposal for discussion, or add an FM-1 profile
to the simulator so anyone can test a host app without hardware. I would not open a firmware PR
without a real FM-1 to test on and your go-ahead (CONTRIBUTING).

Questions, objections and "this is a bad idea" are all welcome.

[Optional disclosure line, edit or delete: The spec and simulator were drafted with the help of
an AI coding assistant (Claude Code); I reviewed and am responsible for them, as Felucca's own AI
statement asks of contributions.]

## Short cross-link for #220 (optional)

> Related to this: I've drafted a small open companion protocol that could carry an extra-display
> view (and spoken announcements) to a phone, with a Python simulator so a host app can be tried
> without hardware. It is not implemented on the FM-1; I'm asking for opinions in a new Ideas
> thread: <link to the post once it exists>.

## Not claimed

- That the protocol works on an FM-1 (untested; endpoint budget open).
- That the maintainers want it. The thread asks.
- Any change to Felucca or SLOOP code. SLOOP is a Felucca derivative and not a separate GitHub
  fork; a follow-up there only if this goes anywhere.
