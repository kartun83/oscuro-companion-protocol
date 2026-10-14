# Reference host library and device simulator (Oscuro Companion Protocol v1)

Pure Python 3, standard library only. Implements the draft in `../docs/PROTOCOL_V1.md`.

| File | What |
|---|---|
| `ocp/wire.py` | Message and tagged-field codec, PAD rule, constants (types, tags, errors, features). No I/O. |
| `ocp/device.py` | `SimDevice`: an in-memory device. Namespaces of objects (including virtual, size-unknown ones), an entity registry, credits, atomic commit, busy devices, events, announcements, priorities. Refuses whatever it does not advertise. |
| `ocp/client.py` | `Client`: blocking host. `hello`, `caps`, `list`, `stat`, `read`, `write`, `delete`, `rename`, `subscribe`, `entities`, `set_value`. `LoopbackTransport` connects it to a `SimDevice` and enforces the transfer rules. |
| `tests/` | 43 tests: both byte vectors of the spec, malformed input, every error path above, 64 and 512 byte messages. |

```sh
PYTHONPATH=host python3 -m unittest discover -s host/tests
```

```python
from ocp.client import Client, LoopbackTransport
from ocp.device import SimDevice, Namespace
dev = SimDevice([Namespace(1, 'patches')])
cl = Client(LoopbackTransport(dev))
cl.hello(); obj = cl.write(1, b'data', name='a.syx'); print(cl.read(1, obj['id']))
```

## What it covers and what it does not

Covered: framing, PAD, tagged fields, HELLO/CAPS, LIST (paged to the host's `max_msg`), STAT,
OPEN/DATA/CREDIT/COMMIT/ABORT/CLOSE for read, create and replace, `if_rev`, CRC32, DELETE,
RENAME, NAK/busy, EVENT/SUBSCRIBE/credits, ANNOUNCE (bounded queue), entities, gated `ENT_VALUE`
set, priority ordering.

Not covered (the simulator does not advertise them and answers `UNSUPPORTED`): `MKDIR`, `COPY`,
`SEEK`, `RESUME`, `PATHS`, `PROGRESS`, `CONFIRM`, screen tree, `ACT`, snapshots, streams.

Not tested against hardware or a real USB stack: there is no pyusb transport yet. A transport
needs only `send(bytes)` (one USB transfer) and `recv() -> bytes | None` (one transfer). Android's
`bulkTransfer` does not send zero-length packets, which is what the PAD flag is for.

## Findings that changed the spec

Writing the simulator exposed gaps, now fixed in PROTOCOL_V1.md: group records had no way to say
what they were (group tags `0x3A..0x3E` added); announcements and pushes were not tied to the
credit rule; handle-bound messages had no stated `req`; `NAK` retry semantics were unstated; and
`ENT_LIST` ignored the host's `max_msg` in the first draft of the simulator (a test caught it).
