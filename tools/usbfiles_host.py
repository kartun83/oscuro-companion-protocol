#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aleksei Tveritinov (Kartun)

"""Oscuro File Bridge — Mac/Linux host mock for the keyboard RAM payload.

Talks to the keyboard payload built from firmware/usbfiles.c over the vendor
bulk pipe. It stands in for the Android app when you only have a Mac: it serves
a fake file list to the keyboard (which shows it in its scroll window) and asks
the keyboard for its own fake list.

Run the payload first (firmware/run_usbfiles.py, inside an Oscuro checkout); the keyboard then
enumerates as 1209:0003 "Oscuro File Bridge".

Deps: pyusb against libusb:
    pip install pyusb
    brew install libusb            # macOS; apt install libusb-1.0-0 on Linux

Examples:
    python tools/usbfiles_host.py --list-device
    python tools/usbfiles_host.py --seconds 60
"""
import argparse
import struct
import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.stderr.write('pyusb is not installed:  tools/venv/bin/pip install pyusb\n')
    raise SystemExit(2)

VID, PID = 0x1209, 0x0003
EP_IN, EP_OUT = 0x81, 0x01
PKT = 64
HDR = 8
MAGIC = b'OF'
PAYLOAD_MAX = PKT - HDR
NAME_MAX = 40

M_HELLO, M_LIST_BEGIN, M_LIST_ENTRY, M_LIST_END, M_LIST_REQ, M_ACK, M_PING, M_PONG = range(1, 9)


def fake_host_files():
    """The fake list this side (the phone/Mac) 'exposes' to the keyboard."""
    return [
        ('phone_notes.txt', 1200),
        ('recording_01.wav', 4_200_000),
        ('presets_backup.bak', 88_000),
        ('setlist_a.m3u', 320),
        ('demo.mp3', 3_100_000),
        ('/inbox', 0),
        ('/exports', 0),
    ]


def frame(mtype, payload=b''):
    if len(payload) > PAYLOAD_MAX:
        raise ValueError('payload too long')
    return bytes([MAGIC[0], MAGIC[1], mtype, 0, len(payload) & 0xFF, len(payload) >> 8, 0, 0]) + payload


def parse(buf):
    if len(buf) < HDR or buf[0:2] != MAGIC:
        return None, b''
    n = buf[4] | buf[5] << 8
    if n > PAYLOAD_MAX or HDR + n > len(buf):
        return None, b''
    return buf[2], buf[HDR:HDR + n]


def entry_payload(name, size, kind=0):
    nb = name.encode('ascii', 'replace')[:NAME_MAX]
    return bytes([len(nb)]) + nb + struct.pack('<I', size & 0xFFFFFFFF) + bytes([kind])


def parse_entry(p):
    if len(p) < 6:
        return None
    n = p[0]
    if n == 0 or 6 + n > len(p):
        return None
    return p[1:1 + n].decode('ascii', 'replace'), struct.unpack_from('<I', p, 1 + n)[0], p[5 + n]


class Session:
    def __init__(self, dev):
        self.dev = dev
        self.ep_in = None
        self.ep_out = None
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        for ep in intf:
            if usb.util.endpoint_type(ep.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                continue
            if ep.bEndpointAddress & 0x80:
                self.ep_in = ep
            else:
                self.ep_out = ep
        if self.ep_in is None or self.ep_out is None:
            raise RuntimeError('no bulk endpoints on interface 0')

    def send(self, mtype, payload=b''):
        self.dev.write(self.ep_out.bEndpointAddress, frame(mtype, payload), timeout=1000)

    def send_list(self, files):
        self.send(M_LIST_BEGIN, struct.pack('<H', len(files)))
        for name, size, *rest in files:
            kind = rest[0] if rest else 0
            self.send(M_LIST_ENTRY, entry_payload(name, size, kind))
        self.send(M_LIST_END, struct.pack('<H', len(files)))

    def read(self, timeout_ms=300):
        try:
            return self.dev.read(self.ep_in.bEndpointAddress, PKT, timeout=timeout_ms)
        except usb.core.USBTimeoutError:
            return None


def connect():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.stderr.write('Device %04x:%04x not found. Is the payload running and plugged in? '
                         '(firmware/run_usbfiles.py)\n' % (VID, PID))
        return None
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    try:
        usb.util.claim_interface(dev, 0)
    except usb.core.USBError as e:
        sys.stderr.write('Could not claim interface 0: %s\n' % e)
        sys.stderr.write('On Linux, detach the kernel driver first; on macOS this should just work.\n')
    return dev


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list-device', action='store_true', help='ask the keyboard for its list and exit')
    ap.add_argument('--seconds', type=float, default=30.0, help='how long to stay connected in the normal session')
    args = ap.parse_args()

    dev = connect()
    if dev is None:
        return 1
    s = Session(dev)
    files = fake_host_files()

    if args.list_device:
        s.send(M_HELLO, b'PYTHON MOCK')
        s.send(M_LIST_REQ)
        deadline = time.time() + 5
        collecting, got = False, []
        while time.time() < deadline:
            raw = s.read()
            if raw is None:
                continue
            t, p = parse(bytes(raw))
            if t == M_LIST_BEGIN:
                collecting, got = True, []
            elif t == M_LIST_ENTRY and collecting:
                e = parse_entry(p)
                if e:
                    got.append(e)
            elif t == M_LIST_END:
                break
        if not got:
            print('No list from the device (did it enumerate as 1209:0003?).')
            return 2
        print('Keyboard serves %d files:' % len(got))
        for name, size, kind in got:
            print('  %s%s  %d' % (name, '/' if kind else '', size))
        return 0

    # Normal session: serve our fake list, request and print the device's list.
    print('Connected. Serving %d fake files; Ctrl-C to stop.' % len(files))
    s.send(M_HELLO, b'PYTHON MOCK')
    s.send_list(files)
    time.sleep(0.2)
    s.send(M_LIST_REQ)

    deadline = time.time() + args.seconds
    asked = False
    collecting, device_list = False, []
    last_push = time.time()
    try:
        while time.time() < deadline:
            raw = s.read(200)
            if raw is not None:
                t, p = parse(bytes(raw))
                if t == M_HELLO:
                    print('  device hello: %s' % p.decode('ascii', 'replace'))
                elif t == M_LIST_BEGIN:
                    collecting, device_list = True, []
                elif t == M_LIST_ENTRY and collecting:
                    e = parse_entry(p)
                    if e:
                        device_list.append(e)
                elif t == M_LIST_END:
                    collecting = False
                    s.send(M_ACK, bytes([M_LIST_END]))
                    print('  device list (%d):' % len(device_list))
                    for name, size, kind in device_list:
                        print('    %s%s  %d' % (name, '/' if kind else '', size))
                elif t == M_LIST_REQ:
                    s.send_list(files)
                elif t == M_ACK:
                    print('  ack for message type 0x%02x' % p[0])
                elif t == M_PING:
                    s.send(M_PONG)
            # Re-push our list periodically so the keyboard window stays fresh.
            if time.time() - last_push > 5:
                s.send_list(files)
                last_push = time.time()
    except KeyboardInterrupt:
        print('\nstopping')
    print('Done.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
