# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aleksei Tveritinov (Kartun)
"""Reference host client for the Oscuro Companion Protocol v1.

Transport-agnostic: anything with send(bytes) and recv() -> bytes | None works (a pyusb or
Android bulk pipe, or ocp.device.SimDevice through LoopbackTransport). The client is
blocking and single-threaded; unsolicited messages that arrive while it waits are kept in
`self.unsolicited`.
"""
import time
import zlib

from . import wire as w
from .wire import Msg, u, s


class DeviceError(Exception):
    def __init__(self, code, msg):
        super().__init__('device error %d' % code)
        self.code, self.msg = code, msg

    @property
    def retry_ms(self):
        return self.msg.uint(w.T_RETRY_MS)


class ProtocolError(Exception):
    pass


class LoopbackTransport:
    """Connects a Client to a SimDevice and enforces the transfer rules of PROTOCOL_V1 section 2."""

    def __init__(self, device, mps=64):
        self.dev, self.mps = device, mps
        self.sent = []          # raw transfers, for inspection

    def send(self, raw):
        assert len(raw) % self.mps or raw[1] & w.F_PAD, 'full-packet message without PAD would need a ZLP'
        self.sent.append(raw)
        self.dev.receive(raw)

    def recv(self):
        return self.dev.poll()


class Client:
    def __init__(self, transport, peer='ocp-host', max_msg=64, mps=64, sleep=time.sleep, nak_retries=5):
        self.t, self.peer, self.max_msg, self.mps = transport, peer, max_msg, mps
        self.sleep, self.nak_retries = sleep, nak_retries
        self.unsolicited = []
        self.features, self.limits, self.dev_max_msg = set(), {}, 64
        self._req = 0

    # ---- plumbing ----------------------------------------------------------------------------
    def _next_req(self):
        self._req = self._req % 0xFFFF + 1
        return self._req

    def _send(self, msg):
        self.t.send(w.encode(msg, self.mps))

    def _recv(self):
        raw = self.t.recv()
        if raw is None:
            return None
        if len(raw) > self.max_msg:
            raise ProtocolError('device sent %d bytes, host max_msg is %d' % (len(raw), self.max_msg))
        try:
            return w.decode(raw)
        except w.WireError:
            return self._recv()        # drop malformed, as a receiver must

    def request(self, typ, fields=(), expect=None):
        """Send a request and return the list of RESP messages (all MORE chunks)."""
        for attempt in range(self.nak_retries + 1):
            req = self._next_req()
            self._send(Msg(typ, req, 0, list(fields)))
            out = []
            while True:
                m = self._recv()
                if m is None:
                    raise ProtocolError('no answer to type 0x%02x' % typ)
                if m.type == w.NAK and m.req == req:
                    self.sleep((m.uint(w.T_RETRY_MS, 100)) / 1000)
                    break           # retry with a new request id
                if m.req == req and m.resp:
                    if m.type == w.ERR:
                        raise DeviceError(m.uint(w.T_CODE, 0), m)
                    out.append(m)
                    if not m.more:
                        return out
                else:
                    self.unsolicited.append(m)
        raise ProtocolError('device stayed busy')

    # ---- control -----------------------------------------------------------------------------
    def hello(self, protos=(0x0100,)):
        f = [(w.T_PROTO, u(p)) for p in protos] + [(w.T_PEER, s(self.peer)), (w.T_MAX_MSG, u(self.max_msg))]
        (m,) = self.request(w.HELLO, f)
        ids = m.get(w.T_FEATURES, b'')
        self.features = {int.from_bytes(ids[i:i + 2], 'little') for i in range(0, len(ids) - 1, 2)}
        self.dev_max_msg = m.uint(w.T_MAX_MSG, 64)
        return {'proto': m.uint(w.T_PROTO), 'peer': m.text(w.T_PEER), 'max_msg': self.dev_max_msg,
                'features': self.features}

    def caps(self):
        res = {'limits': {}, 'namespaces': [], 'formats': []}
        for m in self.request(w.CAPS, [(w.T_CODE, u(0))]):
            for tag, val in m.fields:
                f = w.decode_fields(val)
                if tag == w.G_LIMITS:
                    res['limits'] = {t: int.from_bytes(v, 'little') for t, v in f}
                elif tag == w.G_NS:
                    res['namespaces'].append({'ns': w.fuint(f, w.T_NS), 'name': w.fstr(f, w.T_NAME),
                                              'role': w.fuint(f, w.T_NS_ROLE), 'access': w.fuint(f, w.T_ACCESS),
                                              'capacity': w.fuint(f, w.T_CAPACITY), 'free': w.fuint(f, w.T_FREE)})
                elif tag == w.G_FORMAT:
                    res['formats'].append({'format': w.fuint(f, w.T_FORMAT), 'ext': w.fstr(f, w.T_EXT),
                                           'mime': w.fstr(f, w.T_MIME), 'caps': w.fuint(f, w.T_FMT_CAPS)})
        self.limits = res['limits']
        return res

    def ping(self, text=''):
        (m,) = self.request(w.PING, [(w.T_TEXT, s(text))])
        return m.text(w.T_TEXT, '')

    def subscribe(self, kinds):
        self.request(w.SUBSCRIBE, [(w.T_KIND, u(k)) for k in kinds])

    def grant(self, n):
        """Grant the device n credits for unsolicited messages (events, announcements, pushes)."""
        self._send(Msg(w.CREDIT, 0, 0, [(w.T_CREDITS, u(n))]))

    def drain(self):
        """Collect whatever the device has queued without a request."""
        while (m := self._recv()) is not None:
            self.unsolicited.append(m)
        out, self.unsolicited = self.unsolicited, []
        return out

    # ---- files -------------------------------------------------------------------------------
    @staticmethod
    def _obj(fields):
        return {'id': w.fuint(fields, w.T_ID), 'name': w.fstr(fields, w.T_NAME), 'size': w.fuint(fields, w.T_SIZE),
                'format': w.fuint(fields, w.T_FORMAT), 'oflags': w.fuint(fields, w.T_OFLAGS),
                'rev': w.fuint(fields, w.T_REV), 'mtime': w.fuint(fields, w.T_MTIME)}

    def list(self, ns, formats=(), sort=0, start=0, count=0xFFFF):
        f = [(w.T_NS, u(ns)), (w.T_START, u(start)), (w.T_COUNT, u(count)), (w.T_SORT, u(sort))]
        f += [(w.T_FILTER_FMT, u(x)) for x in formats]
        objs, total, more = [], 0, 0
        for m in self.request(w.LIST, f):
            objs += [self._obj(g) for g in m.groups()]
            total = m.uint(w.T_TOTAL, total)
            more = m.uint(w.T_MORE, more)
        return {'objects': objs, 'total': total, 'more': bool(more)}

    def stat(self, ns, id=None, name=None):
        f = [(w.T_NS, u(ns))] + ([(w.T_ID, u(id))] if id is not None else []) + \
            ([(w.T_NAME, s(name))] if name is not None else [])
        (m,) = self.request(w.STAT, f)
        return self._obj(m.groups()[0])

    def delete(self, ns, id, if_rev=None):
        self.request(w.DELETE, [(w.T_NS, u(ns)), (w.T_ID, u(id))] + ([(w.T_IF_REV, u(if_rev))] if if_rev else []))

    def rename(self, ns, id, new_name, if_rev=None):
        (m,) = self.request(w.RENAME, [(w.T_NS, u(ns)), (w.T_ID, u(id)), (w.T_NEW_NAME, s(new_name))]
                            + ([(w.T_IF_REV, u(if_rev))] if if_rev else []))
        return self._obj(m.groups()[0])

    def read(self, ns, id):
        """Download an object. Works for unknown-size (virtual) objects: ends on the fin field."""
        (op,) = self.request(w.OPEN, [(w.T_NS, u(ns)), (w.T_ID, u(id)), (w.T_MODE, u(w.M_READ))])
        handle, credits = op.uint(w.T_HANDLE), op.uint(w.T_CREDITS, 1)
        size, buf, got = op.uint(w.T_SIZE), bytearray(), 0
        while True:
            m = self._recv()
            if m is None:
                raise ProtocolError('read stalled')
            if m.type != w.DATA or m.uint(w.T_HANDLE) != handle:
                self.unsolicited.append(m)
                continue
            if m.uint(w.T_OFFSET) != len(buf):
                raise ProtocolError('out-of-order DATA')
            buf += m.get(w.T_RAW, b'')
            got += 1
            if m.uint(w.T_FIN):
                break
            if got % credits == 0:
                self._send(Msg(w.CREDIT, 0, 0, [(w.T_HANDLE, u(handle)), (w.T_CREDITS, u(credits))]))
        self._send(Msg(w.CLOSE, 0, 0, [(w.T_HANDLE, u(handle))]))
        if size is not None and size != len(buf):
            raise ProtocolError('size mismatch')
        return bytes(buf)

    def write(self, ns, data, name=None, id=None, if_rev=None, mode=None, crc=None):
        """Create (name) or atomically replace (id) an object. Returns the new object record."""
        mode = mode if mode is not None else (w.M_REPLACE if id is not None else w.M_CREATE)
        f = [(w.T_NS, u(ns)), (w.T_MODE, u(mode)), (w.T_SIZE, u(len(data)))]
        f += [(w.T_NAME, s(name))] if name is not None else []
        f += [(w.T_ID, u(id))] if id is not None else []
        f += [(w.T_IF_REV, u(if_rev))] if if_rev is not None else []
        (op,) = self.request(w.OPEN, f)
        handle, credits = op.uint(w.T_HANDLE), op.uint(w.T_CREDITS, 1)
        pos = 0
        while pos < len(data):
            while credits == 0:
                m = self._recv()
                if m is None:
                    raise ProtocolError('write stalled')
                if m.type == w.CREDIT and m.uint(w.T_HANDLE) == handle:
                    credits += m.uint(w.T_CREDITS, 0)
                elif m.type == w.ERR:
                    raise DeviceError(m.uint(w.T_CODE, 0), m)
                else:
                    self.unsolicited.append(m)
            n = min(len(data) - pos, op.uint(w.T_MAX_DATA, 48))
            while True:         # largest chunk that fits the device's max_msg
                msg = Msg(w.DATA, 0, 0, [(w.T_HANDLE, u(handle)), (w.T_OFFSET, u(pos)), (w.T_RAW, data[pos:pos + n])])
                if len(w.encode(msg, self.mps)) <= self.dev_max_msg:
                    break
                n -= 1
            self._send(msg)
            pos += n
            credits -= 1
            # in this blocking client the device's reply is read lazily, an ERR surfaces at the next wait
        crc = zlib.crc32(data) if crc is None else crc
        for attempt in range(1):
            req = self._next_req()
            self._send(Msg(w.COMMIT, req, 0, [(w.T_HANDLE, u(handle)), (w.T_SIZE, u(len(data))), (w.T_CRC32, u(crc))]))
            while True:
                m = self._recv()
                if m is None:
                    raise ProtocolError('no answer to COMMIT')
                if m.req == req and m.resp:
                    if m.type == w.ERR:
                        raise DeviceError(m.uint(w.T_CODE, 0), m)
                    return self._obj(m.groups()[0])
                if m.type == w.ERR:             # error for an earlier DATA, for example OUT_OF_ORDER
                    raise DeviceError(m.uint(w.T_CODE, 0), m)
                if m.type != w.CREDIT:
                    self.unsolicited.append(m)

    # ---- ui ----------------------------------------------------------------------------------
    def entities(self):
        out = []
        for g in (g for m in self.request(w.ENT_GET, [(w.T_START, u(0))]) for g in m.groups()):
            out.append({'eid': w.fuint(g, w.T_EID), 'name': w.fstr(g, w.T_NAME), 'short': w.fstr(g, w.T_SHORT),
                        'kind': w.fuint(g, w.T_ENT_KIND), 'min': w.fuint(g, w.T_MIN), 'max': w.fuint(g, w.T_MAX),
                        'unit': w.fstr(g, w.T_UNIT), 'eflags': w.fuint(g, w.T_EFLAGS)})
        return out

    def set_value(self, eid, value):
        (m,) = self.request(w.ENT_VALUE, [(w.T_EID, u(eid)), (w.T_VAL, u(value))])
        return {'val': m.uint(w.T_VAL), 'text': m.text(w.T_TEXT), 'speak': m.text(w.T_SPEAK), 'src': m.uint(w.T_SRC)}
