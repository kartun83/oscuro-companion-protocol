# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aleksei Tveritinov (Kartun)
"""In-memory device simulator for the Oscuro Companion Protocol v1.

A reference for the device side and a test double for host code. It speaks the wire format of
docs/PROTOCOL_V1.md, serves namespaces of objects and a small entity registry, and
reproduces the behaviours hosts must cope with: paging, credits, atomic commit, busy devices,
virtual objects, events and priorities. Feature-gated: whatever it does not advertise it refuses.

Usage: feed raw messages with receive(); take the next message to send with poll().
"""
import zlib
from collections import deque
from dataclasses import dataclass, field

from . import wire as w
from .wire import Msg, u, s

DEFAULT_FEATURES = (w.FT_LIST, w.FT_READ, w.FT_WRITE, w.FT_DELETE, w.FT_RENAME, w.FT_CRC32, w.FT_EVENTS,
                    w.FT_FREE_SPACE, w.FT_VIRTUAL, w.FT_LARGE_MSG, w.FT_UI_ANNOUNCE, w.FT_UI_ENTITIES,
                    w.FT_UI_CONTROL)
SUPPORTED_PROTOS = (0x0100,)


@dataclass
class Obj:
    id: int
    name: str
    data: bytes = b''
    format: int = 0
    oflags: int = 0
    rev: int = 1
    mtime: int = 0
    gen: object = None              # callable -> bytes, for VIRTUAL objects

    def content(self):
        return self.gen() if self.gen else self.data

    def fields(self):
        f = [(w.T_ID, u(self.id)), (w.T_NAME, s(self.name)), (w.T_FORMAT, u(self.format)),
             (w.T_OFLAGS, u(self.oflags)), (w.T_REV, u(self.rev)), (w.T_MTIME, u(self.mtime))]
        if not self.oflags & w.OF_SIZE_UNKNOWN:
            f.append((w.T_SIZE, u(len(self.content()))))
        return f


@dataclass
class Namespace:
    id: int
    name: str
    role: int = 1
    access: int = w.AC_READ | w.AC_WRITE | w.AC_DELETE | w.AC_CREATE
    capacity: int = 4096
    objects: dict = field(default_factory=dict)
    next_id: int = 1

    def used(self):
        return sum(len(o.data) for o in self.objects.values() if not o.gen)

    def add(self, name, data=b'', **kw):
        o = Obj(self.next_id, name, data, **kw)
        self.objects[o.id] = o
        self.next_id += 1
        return o


@dataclass
class Entity:
    eid: int
    name: str
    short: str
    kind: int = 30
    vtype: int = 1                  # INT
    vmin: int = 0
    vmax: int = 127
    unit: str = ''
    value: int = 0
    eflags: int = 0

    def text(self):
        return ('%d %s' % (self.value, self.unit)).strip()


@dataclass
class _Handle:
    num: int
    ns: Namespace
    write: bool
    obj: Obj = None
    name: str = ''
    replace: bool = False
    snap: bytes = b''
    pos: int = 0
    credits: int = 0
    announced: int = None
    staging: bytearray = field(default_factory=bytearray)


class SimDevice:
    def __init__(self, namespaces=(), entities=(), features=DEFAULT_FEATURES, max_msg=64, mps=64, max_handles=2,
                 max_data=48, credits=4, announce_queue=4, name='Sim Device 1.0'):
        self.name, self.features = name, set(features)
        self.max_msg, self.mps = max_msg, mps
        self.max_handles, self.max_data, self.credits = max_handles, max_data, credits
        self.announce_queue = announce_queue
        self.ns = {n.id: n for n in namespaces}
        self.entities = {e.eid: e for e in entities}
        self.formats = [(1, 'syx', 'audio/sysex', 3), (2, 'mid', 'audio/midi', 1), (3, 'txt', 'text/plain', 1)]
        self.busy_playing = False
        self.busy_style = 'nak'                 # or 'err'
        self.remote_control = False
        self.host_max_msg = 64
        self._handles = {}
        self._next_handle = 1
        self._q = (deque(), deque(), deque())   # control, ui, files
        self._subs = set()
        self._ev_credits = 0
        self._events = deque(maxlen=16)
        self._announces = []
        self._pushes = {}
        self._seq = 0
        self.dropped = 0

    # ---- I/O ---------------------------------------------------------------------------------
    def receive(self, raw):
        try:
            m = w.decode(raw)
        except w.WireError:
            self.dropped += 1
            return
        fn = {w.HELLO: self._hello, w.CAPS: self._caps, w.PING: self._ping, w.SUBSCRIBE: self._subscribe,
              w.CREDIT: self._credit, w.LIST: self._list, w.STAT: self._stat, w.DELETE: self._delete,
              w.RENAME: self._rename, w.OPEN: self._open, w.DATA: self._data_in, w.COMMIT: self._commit,
              w.ABORT: self._abort, w.CLOSE: self._close, w.ENT_GET: self._ent_get,
              w.ENT_VALUE: self._ent_set}.get(m.type)
        if fn is None or not self._gated(m.type):
            if m.req:
                self._err(m, w.E_UNSUPPORTED)
            return
        fn(m)

    def poll(self):
        for q in self._q:
            if q:
                return q.popleft()
        return None

    def _gated(self, t):
        need = {w.LIST: w.FT_LIST, w.STAT: w.FT_LIST, w.DELETE: w.FT_DELETE, w.RENAME: w.FT_RENAME,
                w.OPEN: w.FT_READ, w.ENT_GET: w.FT_UI_ENTITIES, w.ENT_VALUE: w.FT_UI_ENTITIES}.get(t)
        return need is None or need in self.features

    def _put(self, m):
        svc = 0 if m.type < 0x10 else 1 if 0x20 <= m.type < 0x30 else 2
        self._q[svc].append(w.encode(m, self.mps))

    def _resp(self, req, typ, fields=(), more=False):
        self._put(Msg(typ, req.req, w.F_RESP | (w.F_MORE if more else 0), list(fields)))

    def _err(self, req, code, extra=()):
        f = [(w.T_CODE, u(code)), (w.T_REQ_REF, u(req.req))] + list(extra)
        self._put(Msg(w.ERR, req.req, w.F_RESP, f))

    # ---- control -----------------------------------------------------------------------------
    def _hello(self, m):
        self.host_max_msg = m.uint(w.T_MAX_MSG, 64)
        common = [p for p in map(lambda b: int.from_bytes(b, 'little'), m.all(w.T_PROTO)) if p in SUPPORTED_PROTOS]
        if not common:
            return self._err(m, w.E_UNSUPPORTED_VERSION,
                             extra=[(w.T_PROTO, u(SUPPORTED_PROTOS[0]))])
        ids = b''.join(f.to_bytes(2, 'little') for f in sorted(self.features))
        self._resp(m, w.HELLO, [(w.T_PROTO, u(max(common))), (w.T_PEER, s(self.name)), (w.T_MAX_MSG, u(self.max_msg)),
                                (w.T_ROLE, u(1)), (w.T_FEATURES, ids)])

    def _caps(self, m):
        lim = [(w.T_MAX_MSG, u(self.max_msg)), (w.T_MAX_NAME, u(40)), (w.T_MAX_PATH, u(40)),
               (w.T_MAX_HANDLES, u(self.max_handles)), (w.T_MAX_INFLIGHT, u(self.credits)),
               (w.T_MAX_DATA, u(self.max_data)), (w.T_WRITE_POLICY, u(1)), (w.T_ANNOUNCE_QUEUE, u(self.announce_queue))]
        out = [[(w.G_LIMITS, w.group(lim))]]
        for n in self.ns.values():
            out.append([(w.G_NS, w.group([(w.T_NS, u(n.id)), (w.T_NAME, s(n.name)), (w.T_NS_ROLE, u(n.role)),
                                          (w.T_ACCESS, u(n.access)), (w.T_CAPACITY, u(n.capacity)),
                                          (w.T_FREE, u(max(0, n.capacity - n.used())))]))])
        for fid, ext, mime, caps in self.formats:
            out.append([(w.G_FORMAT, w.group([(w.T_FORMAT, u(fid)), (w.T_EXT, s(ext)), (w.T_MIME, s(mime)),
                                              (w.T_FMT_CAPS, u(caps))]))])
        for i, f in enumerate(out):
            self._resp(m, w.CAPS, f, more=i < len(out) - 1)

    def _ping(self, m):
        if m.req:
            self._resp(m, w.PING, [(w.T_TEXT, m.get(w.T_TEXT, b''))])

    def _subscribe(self, m):
        self._subs = {int.from_bytes(v, 'little') for v in m.all(w.T_KIND)}
        self._resp(m, w.SUBSCRIBE)

    def _credit(self, m):
        h = m.uint(w.T_HANDLE)
        n = m.uint(w.T_CREDITS, 0)
        if h is None:
            self._ev_credits += n
            return self._flush_unsolicited()
        hd = self._handles.get(h)
        if hd and not hd.write:
            hd.credits += n
            self._send_read(hd)

    # ---- unsolicited traffic -----------------------------------------------------------------
    def emit_event(self, kind, fields=()):
        if kind not in self._subs:
            return
        f = [(w.T_KIND, u(kind))] + list(fields)
        self._events.append(Msg(w.EVENT, 0, 0, f))
        self._flush_unsolicited()

    def announce(self, text, short=None, prio=1, cat=3):
        if w.FT_UI_ANNOUNCE not in self.features:
            return
        self._seq += 1
        f = [(w.T_TEXT, s(text)), (w.T_PRIO, u(prio)), (w.T_CAT, u(cat)), (w.T_SEQ, u(self._seq))]
        if short:
            f.append((w.T_SHORT, s(short)))
        self._announces.append((prio, Msg(w.ANNOUNCE, 0, 0, f)))
        while len(self._announces) > self.announce_queue:      # drop the oldest of the lowest priority
            low = min(p for p, _ in self._announces)
            self._announces.remove(next(a for a in self._announces if a[0] == low))
            self.dropped += 1
        self._flush_unsolicited()

    def set_local(self, eid, value):
        """The player moved a control on the device: push the new value (latest wins)."""
        e = self.entities[eid]
        e.value = max(e.vmin, min(e.vmax, value))
        self._pushes[eid] = self._value_msg(e, src=1)
        self._flush_unsolicited()

    def _flush_unsolicited(self):
        while self._ev_credits > 0:
            if self._events:
                m = self._events.popleft()
            elif self._pushes:
                m = self._pushes.pop(next(iter(self._pushes)))
            elif self._announces:
                m = self._announces.pop(0)[1]
            else:
                return
            self._ev_credits -= 1
            self._put(m)

    # ---- files -------------------------------------------------------------------------------
    def _ns(self, m):
        n = self.ns.get(m.uint(w.T_NS, 0))
        if n is None:
            self._err(m, w.E_NOT_FOUND)
        return n

    def _list(self, m):
        n = self._ns(m)
        if not n:
            return
        fmts = {int.from_bytes(v, 'little') for v in m.all(w.T_FILTER_FMT)}
        objs = [o for o in n.objects.values() if not fmts or o.format in fmts]
        sort = m.uint(w.T_SORT, 0)
        key = {1: lambda o: o.name, 2: lambda o: len(o.content()), 3: lambda o: o.mtime}.get(sort & 0x7F)
        if key:
            objs.sort(key=key, reverse=bool(sort & 0x80))
        start, count = m.uint(w.T_START, 0), m.uint(w.T_COUNT, 0xFFFF)
        recs = [(w.G_RECORD, w.group(o.fields())) for o in objs[start:start + count]]
        self._resp_paged(m, w.LIST, [(w.T_NS, u(n.id))], recs,
                         [(w.T_TOTAL, u(len(objs))), (w.T_MORE, u(int(start + count < len(objs))))])

    def _resp_paged(self, req, typ, head, recs, tail):
        """Answer with as many messages as the host's max_msg needs; `tail` rides on the last one."""
        def size(fields):
            return len(w.encode(Msg(typ, req.req, w.F_RESP | w.F_MORE, fields), self.mps))
        msgs, cur = [], []
        for rec in recs:
            if cur and size(head + cur + [rec] + tail) > self.host_max_msg:
                msgs.append(cur)
                cur = []
            cur.append(rec)
        msgs.append(cur)
        for i, part in enumerate(msgs):
            last = i == len(msgs) - 1
            self._resp(req, typ, head + part + (tail if last else []), more=not last)

    def _find(self, m, n):
        oid = m.uint(w.T_ID)
        o = n.objects.get(oid) if oid is not None else next((x for x in n.objects.values() if x.name == m.text(w.T_NAME)), None)
        if o is None:
            self._err(m, w.E_NOT_FOUND)
        return o

    def _stat(self, m):
        n = self._ns(m)
        o = n and self._find(m, n)
        if o:
            self._resp(m, w.STAT, [(w.G_RECORD, w.group(o.fields()))])

    def _check_rev(self, m, o):
        want = m.uint(w.T_IF_REV)
        if want is not None and want != o.rev:
            self._err(m, w.E_REV_MISMATCH)
            return False
        return True

    def _delete(self, m):
        n = self._ns(m)
        o = n and self._find(m, n)
        if not o:
            return
        if not n.access & w.AC_DELETE or o.oflags & w.OF_READONLY:
            return self._err(m, w.E_ACCESS)
        if not self._check_rev(m, o):
            return
        del n.objects[o.id]
        self._changed(n, o)
        self._resp(m, w.DELETE)

    def _rename(self, m):
        n = self._ns(m)
        o = n and self._find(m, n)
        if not o:
            return
        new = m.text(w.T_NEW_NAME, '')
        if not n.access & w.AC_WRITE or o.oflags & w.OF_READONLY:
            return self._err(m, w.E_ACCESS)
        if not new or '/' in new or len(new.encode()) > 40:
            return self._err(m, w.E_BAD_NAME)
        if not self._check_rev(m, o):
            return
        if any(x.name == new for x in n.objects.values()):
            return self._err(m, w.E_EXISTS)
        o.name, o.rev = new, o.rev + 1
        self._changed(n, o)
        self._resp(m, w.RENAME, [(w.G_RECORD, w.group(o.fields()))])

    def _changed(self, n, o):
        self.emit_event(w.EV_OBJECT_CHANGED, [(w.T_NS, u(n.id)), (w.T_ID, u(o.id))])
        self.emit_event(w.EV_NS_REV, [(w.T_NS, u(n.id))])

    def _busy(self, m):
        if not self.busy_playing:
            return False
        if self.busy_style == 'err':
            self._err(m, w.E_BUSY_PLAYING, extra=[(w.T_RETRY_MS, u(200))])
        else:
            self._put(Msg(w.NAK, m.req, w.F_RESP, [(w.T_REQ_REF, u(m.req)), (w.T_RETRY_MS, u(100))]))
        return True

    def _open(self, m):
        n = self._ns(m)
        if not n:
            return
        mode = m.uint(w.T_MODE, w.M_READ)
        if len(self._handles) >= self.max_handles:
            return self._err(m, w.E_TOO_MANY)
        if mode == w.M_READ:
            if not n.access & w.AC_READ:
                return self._err(m, w.E_ACCESS)
            o = self._find(m, n)
            if not o:
                return
            h = _Handle(self._alloc(), n, False, o, snap=o.content(), credits=min(self.credits, m.uint(w.T_CREDITS, 99)))
            self._handles[h.num] = h
            f = [(w.T_HANDLE, u(h.num)), (w.T_ID, u(o.id)), (w.T_REV, u(o.rev)), (w.T_CREDITS, u(self.credits)),
                 (w.T_MAX_DATA, u(self.max_data))]
            if not o.oflags & w.OF_SIZE_UNKNOWN:
                f.append((w.T_SIZE, u(len(h.snap))))
            self._resp(m, w.OPEN, f)
            return self._send_read(h)
        if mode not in (w.M_CREATE, w.M_REPLACE):
            return self._err(m, w.E_UNSUPPORTED)
        if w.FT_WRITE not in self.features:
            return self._err(m, w.E_UNSUPPORTED)
        if self._busy(m):
            return
        need = w.AC_CREATE if mode == w.M_CREATE else w.AC_WRITE
        if not n.access & need:
            return self._err(m, w.E_ACCESS)
        o = None
        if mode == w.M_REPLACE:
            o = self._find(m, n)
            if not o:
                return
            if o.oflags & (w.OF_READONLY | w.OF_VIRTUAL):
                return self._err(m, w.E_ACCESS)
            if not self._check_rev(m, o):
                return
            name = o.name
        else:
            name = m.text(w.T_NAME, '')
            if not name or '/' in name or len(name.encode()) > 40:
                return self._err(m, w.E_BAD_NAME)
            if any(x.name == name for x in n.objects.values()):
                return self._err(m, w.E_EXISTS)
        size = m.uint(w.T_SIZE)
        free = n.capacity - n.used() + (len(o.data) if o else 0)
        if size is not None and size > free:
            return self._err(m, w.E_NO_SPACE)
        h = _Handle(self._alloc(), n, True, o, name, mode == w.M_REPLACE, announced=size, credits=self.credits)
        self._handles[h.num] = h
        self._resp(m, w.OPEN, [(w.T_HANDLE, u(h.num)), (w.T_CREDITS, u(self.credits)),
                               (w.T_MAX_DATA, u(self.max_data))])

    def _alloc(self):
        while self._next_handle in self._handles or self._next_handle in (0, 256):
            self._next_handle = self._next_handle % 255 + 1
        h, self._next_handle = self._next_handle, self._next_handle % 255 + 1
        return h

    def _send_read(self, h):
        while h.credits > 0 and h.num in self._handles:
            msg = None
            chunk = min(self.max_data, len(h.snap) - h.pos)
            while True:
                fin = h.pos + chunk >= len(h.snap)
                fields = [(w.T_HANDLE, u(h.num)), (w.T_OFFSET, u(h.pos))] + ([(w.T_FIN, u(1))] if fin else []) \
                    + [(w.T_RAW, h.snap[h.pos:h.pos + chunk])]
                msg = Msg(w.DATA, 0, 0, fields)
                if len(w.encode(msg, self.mps)) <= self.host_max_msg or chunk == 0:
                    break
                chunk -= 1
            self._put(msg)
            h.pos += chunk
            h.credits -= 1
            if fin:
                return

    def _handle(self, m, write):
        h = self._handles.get(m.uint(w.T_HANDLE, 0))
        if not h or h.write != write:
            if m.req:
                self._err(m, w.E_NO_HANDLE)
            return None
        return h

    def _data_in(self, m):
        h = self._handle(m, True)
        if not h:
            return
        off, data = m.uint(w.T_OFFSET, 0), m.get(w.T_RAW, b'')
        if off != len(h.staging):
            del self._handles[h.num]
            return self._err(m, w.E_OUT_OF_ORDER, extra=[(w.T_OFFSET, u(len(h.staging)))])
        free = h.ns.capacity - h.ns.used() + (len(h.obj.data) if h.obj else 0)
        if len(h.staging) + len(data) > free:
            del self._handles[h.num]
            return self._err(m, w.E_NO_SPACE)
        h.staging += data
        self._put(Msg(w.CREDIT, 0, 0, [(w.T_HANDLE, u(h.num)), (w.T_CREDITS, u(1))]))

    def _commit(self, m):
        h = self._handle(m, True)
        if not h:
            return
        del self._handles[h.num]
        if m.uint(w.T_SIZE) != len(h.staging):
            return self._err(m, w.E_BAD_REQUEST)
        if w.FT_CRC32 in self.features and m.uint(w.T_CRC32) != zlib.crc32(bytes(h.staging)):
            return self._err(m, w.E_CRC_MISMATCH)
        if h.replace:
            h.obj.data, h.obj.rev = bytes(h.staging), h.obj.rev + 1
            o = h.obj
        else:
            o = h.ns.add(h.name, bytes(h.staging))
        self._changed(h.ns, o)
        self._resp(m, w.COMMIT, [(w.G_RECORD, w.group(o.fields()))])

    def _abort(self, m):
        h = self._handles.pop(m.uint(w.T_HANDLE, 0), None)
        if m.req:
            self._resp(m, w.ABORT) if h else self._err(m, w.E_NO_HANDLE)

    _close = _abort

    # ---- entities ----------------------------------------------------------------------------
    def _ent_get(self, m):
        start, count = m.uint(w.T_START, 0), m.uint(w.T_COUNT, 0xFFFF)
        ents = sorted(self.entities.values(), key=lambda e: e.eid)[start:start + count]
        recs = [(w.G_RECORD, w.group([(w.T_EID, u(e.eid)), (w.T_NAME, s(e.name)), (w.T_SHORT, s(e.short)),
                                      (w.T_ENT_KIND, u(e.kind)), (w.T_VTYPE, u(e.vtype)), (w.T_MIN, u(e.vmin)),
                                      (w.T_MAX, u(e.vmax)), (w.T_UNIT, s(e.unit)), (w.T_EFLAGS, u(e.eflags))]))
                for e in ents]
        self._resp_paged(m, w.ENT_LIST, [], recs, [(w.T_TOTAL, u(len(self.entities)))])

    def _value_msg(self, e, src, req=0):
        return Msg(w.ENT_VALUE, req, w.F_RESP if req else 0,
                   [(w.T_EID, u(e.eid)), (w.T_VAL, u(e.value)), (w.T_TEXT, s(e.text())),
                    (w.T_SPEAK, s('%s, %s' % (e.name, e.text()))), (w.T_SRC, u(src))])

    def _ent_set(self, m):
        e = self.entities.get(m.uint(w.T_EID, 0))
        if e is None:
            return self._err(m, w.E_NOT_FOUND)
        if w.FT_UI_CONTROL not in self.features or not self.remote_control:
            return self._err(m, w.E_DENIED)
        if e.eflags & 1:
            return self._err(m, w.E_ACCESS)
        e.value = max(e.vmin, min(e.vmax, m.uint(w.T_VAL, 0)))
        self._put(self._value_msg(e, src=2, req=m.req))
