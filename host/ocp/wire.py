# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aleksei Tveritinov (Kartun)
"""Wire format of the Oscuro Companion Protocol v1 (docs/PROTOCOL_V1.md sections 2-3).

One message is one USB transfer: a 4-byte header (type, flags, req) followed by tagged
fields. This module only encodes and decodes; it knows no transport and no service.
"""
import struct
from dataclasses import dataclass, field

# ---- message types (section 4) ------------------------------------------------------------
HELLO, CAPS, PING, ERR, NAK, SUBSCRIBE, EVENT, CREDIT = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08
LIST, STAT, MKDIR, RENAME, DELETE, COPY, OPEN, DATA, PROGRESS, COMMIT, ABORT, CLOSE = range(0x10, 0x1C)
ANNOUNCE, ENT_GET, ENT_LIST, ENT_VALUE, INPUT, FOCUS = 0x20, 0x21, 0x22, 0x23, 0x24, 0x25
SCREEN_GET, SCREEN, SCREEN_DELTA, ACT, SNAPSHOT_GET, SNAPSHOT = range(0x26, 0x2C)

# ---- flags -------------------------------------------------------------------------------------
F_RESP, F_MORE, F_PAD = 0x01, 0x02, 0x04

# ---- common tags (section 3.1) ----------------------------------------------------------------
T_RAW = 0x00
(T_PROTO, T_PEER, T_MAX_MSG, T_ROLE, T_FEATURES, T_CODE, T_TEXT, T_NS, T_ID, T_NAME, T_SIZE, T_MTIME,
 T_FORMAT, T_OFLAGS, T_REV, T_HANDLE, T_OFFSET, T_LENGTH, T_CREDITS, T_CRC32, T_MODE, T_PARENT,
 T_RETRY_MS, T_START, T_COUNT, T_MORE, T_TOTAL, T_IF_REV, T_NEW_NAME, T_FILTER_FMT, T_SORT, T_PATH,
 T_REQ_REF) = range(0x01, 0x22)
# group tags: the value is itself a field sequence
G_LIMITS, G_NS, G_FORMAT, G_UI, G_RECORD = 0x3A, 0x3B, 0x3C, 0x3D, 0x3E
GROUP_TAGS = (G_LIMITS, G_NS, G_FORMAT, G_UI, G_RECORD)
# service tags used by the reference implementation
T_MAX_NAME, T_MAX_PATH, T_MAX_HANDLES, T_MAX_INFLIGHT, T_MAX_DATA, T_WRITE_POLICY, T_ANNOUNCE_QUEUE = range(0x40, 0x47)
T_NS_ROLE, T_ACCESS, T_CAPACITY, T_FREE, T_EXT, T_MIME, T_FMT_CAPS, T_FIN, T_KIND, T_VALUE = range(0x47, 0x51)
T_SHORT, T_PRIO, T_CAT, T_EID, T_LANG, T_SEQ, T_ENT_KIND, T_VTYPE, T_MIN, T_MAX, T_STEP, T_SCALE, T_UNIT, \
    T_OPTIONS, T_GROUP, T_EFLAGS, T_REASON, T_VAL, T_SPEAK, T_SRC = range(0x60, 0x74)

# ---- error codes (section 11) -----------------------------------------------------------------
(E_UNSUPPORTED, E_UNSUPPORTED_VERSION, E_BAD_REQUEST, E_BAD_NAME, E_NOT_FOUND, E_EXISTS, E_ACCESS, E_NO_SPACE,
 E_TOO_BIG, E_NO_HANDLE, E_TOO_MANY, E_BUSY, E_BUSY_PLAYING, E_IO, E_CRC_MISMATCH, E_ABORTED, E_OUT_OF_ORDER,
 E_REV_MISMATCH, E_CONFIRM_REQUIRED, E_DENIED, E_AUTH, E_PROTOCOL) = range(1, 23)

# ---- feature ids (section 6) ------------------------------------------------------------------
(FT_LIST, FT_READ, FT_WRITE, FT_DELETE, FT_RENAME, FT_MKDIR, FT_COPY, FT_SEEK, FT_RESUME, FT_CRC32, FT_PATHS,
 FT_EVENTS, FT_FREE_SPACE, FT_VIRTUAL, FT_CONFIRM, FT_LARGE_MSG) = range(1, 17)
FT_UI_ANNOUNCE, FT_UI_ENTITIES, FT_UI_SCREEN, FT_UI_CONTROL, FT_UI_SNAPSHOT = range(32, 37)

# object flags
OF_DIR, OF_READONLY, OF_VIRTUAL, OF_SIZE_UNKNOWN, OF_BUSY = 1, 2, 4, 8, 16
# namespace access bits
AC_READ, AC_WRITE, AC_DELETE, AC_CREATE, AC_VOLATILE, AC_REMOVABLE, AC_CONFIRM = 1, 2, 4, 8, 16, 32, 64
# open modes
M_READ, M_CREATE, M_REPLACE, M_APPEND, M_RESUME = range(5)
# event kinds
EV_NS_REV, EV_OBJECT_CHANGED, EV_STORAGE_LOW, EV_CONFIRMED, EV_DENIED, EV_BUS_RESET, EV_RECORDING_DONE, \
    EV_REMOTE_CONTROL = range(1, 9)

HDR = struct.Struct('<BBH')


class WireError(ValueError):
    """Malformed bytes. A receiver drops the message."""


def u(n):
    """Minimal little-endian unsigned integer (1, 2, 4 or 8 bytes)."""
    if n < 0:
        raise ValueError('negative')
    for width in (1, 2, 4, 8):
        if n < 1 << (8 * width):
            return n.to_bytes(width, 'little')
    raise ValueError('does not fit u64')


def s(text):
    return text.encode('utf-8')


def encode_fields(fields):
    out = bytearray()
    for i, (tag, val) in enumerate(fields):
        if tag == T_RAW:
            if i != len(fields) - 1:
                raise WireError('raw tail must be the last field')
            out.append(0)
            out += val
            continue
        if not 1 <= tag <= 0xFF:
            raise WireError('bad tag %r' % tag)
        val = bytes(val)
        if len(val) < 0xFF:
            out += bytes((tag, len(val)))
        elif len(val) <= 0xFFFF:
            out += bytes((tag, 0xFF)) + struct.pack('<H', len(val))
        else:
            raise WireError('field too long')
        out += val
    return bytes(out)


def decode_fields(data):
    fields, i, n = [], 0, len(data)
    while i < n:
        tag = data[i]
        i += 1
        if tag == T_RAW:
            fields.append((T_RAW, bytes(data[i:])))
            break
        if i >= n:
            raise WireError('truncated field header')
        ln = data[i]
        i += 1
        if ln == 0xFF:
            if i + 2 > n:
                raise WireError('truncated extended length')
            (ln,) = struct.unpack_from('<H', data, i)
            i += 2
        if i + ln > n:
            raise WireError('field overruns message')
        fields.append((tag, bytes(data[i:i + ln])))
        i += ln
    return fields


def group(fields):
    """Encode a nested field sequence as a value."""
    return encode_fields(fields)


@dataclass
class Msg:
    type: int
    req: int = 0
    flags: int = 0
    fields: list = field(default_factory=list)

    def add(self, tag, value):
        self.fields.append((tag, value))
        return self

    def get(self, tag, default=None):
        for t, v in self.fields:
            if t == tag:
                return v
        return default

    def all(self, tag):
        return [v for t, v in self.fields if t == tag]

    def uint(self, tag, default=None):
        v = self.get(tag)
        return default if v is None else int.from_bytes(v, 'little')

    def text(self, tag, default=None):
        v = self.get(tag)
        return default if v is None else v.decode('utf-8', 'replace')

    def groups(self, tag=G_RECORD):
        return [decode_fields(v) for v in self.all(tag)]

    @property
    def resp(self):
        return bool(self.flags & F_RESP)

    @property
    def more(self):
        return bool(self.flags & F_MORE)


def fget(fields, tag, default=None):
    for t, v in fields:
        if t == tag:
            return v
    return default


def fuint(fields, tag, default=None):
    v = fget(fields, tag)
    return default if v is None else int.from_bytes(v, 'little')


def fstr(fields, tag, default=None):
    v = fget(fields, tag)
    return default if v is None else v.decode('utf-8', 'replace')


def encode(msg, mps=64):
    """One transfer. If its length is a multiple of the packet size, set PAD and append one zero byte."""
    flags = msg.flags & ~F_PAD & 0xFF
    body = encode_fields(msg.fields)
    if (HDR.size + len(body)) % mps == 0:
        flags |= F_PAD
        body += b'\0'
    return HDR.pack(msg.type, flags, msg.req) + body


def decode(raw):
    if len(raw) < HDR.size:
        raise WireError('short message')
    typ, flags, req = HDR.unpack_from(raw)
    body = bytes(raw[HDR.size:])
    if flags & F_PAD:
        if not body:
            raise WireError('PAD without pad byte')
        body = body[:-1]
    return Msg(typ, req, flags & ~F_PAD & 0xFF, decode_fields(body))
