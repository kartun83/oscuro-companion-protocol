#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aleksei Tveritinov

"""Run the Oscuro File Bridge keyboard payload (firmware/ram-lvgl/usbfiles.c)
from WL82 ROM UBOOT1.00 RAM.

Uploads build/v9/usbfiles-wl82/demo.bin, then jumps to it. The payload takes the
USB port over and re-enumerates as the vendor device 1209:0003 "Oscuro File
Bridge"; it never returns to UBOOT, so there is no result block to read back and
the JUMP command ends this session. After that, talk to it with the host side:

    tools/venv/bin/python tools/usbfiles_host.py --list-device   # Mac mock
    (or run the Android app on a phone, keyboard plugged in over OTG)

One run per UBOOT session: power-cycle and re-enter UBOOT before every run.
No flash erase/program commands are issued.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'dongle/host'))
import jlmac
import scsiio_macos
sys.path.insert(1, str(jlmac.TOOL))
from run_scan import JL_MSCDevice, JL_UBOOT, write_verified

LOAD = 0x1c02000


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--opt', choices=['s', '2'], default='2')
    args = ap.parse_args()

    out = HERE / 'build' / 'v9' / 'usbfiles-wl82'
    if not (out / 'demo.bin').exists():
        raise SystemExit('build it first: build.py --app usbfiles --version 9 --opt %s' % args.opt)
    blob = (out / 'demo.bin').read_bytes()
    manifest = json.loads((out / 'manifest.json').read_text())
    if manifest.get('app') != 'usbfiles':
        raise SystemExit('that build is %r, not usbfiles; rebuild' % manifest.get('app'))
    if hashlib.sha256(blob).hexdigest() != manifest['sha256']:
        raise SystemExit('Build artifacts differ from their manifest; rebuild before running')
    symbols = {p[2]: int(p[0], 16) for line in (out / 'demo.sym').read_text().splitlines()
               if len(p := line.split()) == 3}
    if symbols['entry'] != LOAD or not LOAD + len(blob) <= symbols['__bss_start'] < symbols['__bss_end'] < int(manifest['ram_limit'], 16):
        raise SystemExit('Unexpected RAM layout')
    print('usbfiles build %s (%d bytes)' % (manifest['sha256'][:16], len(blob)), flush=True)

    path = jlmac.choose_jl_device()
    if not path:
        return 1
    with JL_MSCDevice(path) as dev:
        vendor, product, _ = dev.inquiry()
        print('Device:', vendor, product, flush=True)
        if vendor.strip() != 'WL82' or product.strip() != 'UBOOT1.00':
            raise SystemExit('Re-enter WL82 ROM UBOOT1.00 before running this payload')
        boot = JL_UBOOT(dev.dev)
        for offset in range(0, len(blob), 512):
            write_verified(boot, LOAD + offset, blob[offset:offset + 512])
        print('Verified %d RAM bytes.' % len(blob), flush=True)
        print('Starting. The USB port becomes 1209:0003 "Oscuro File Bridge".', flush=True)
        normal = scsiio_macos.TIMEOUT_MS
        scsiio_macos.TIMEOUT_MS = 3000
        try:
            boot.mem_jump(LOAD, 0)
        except Exception as e:
            print('jump ended the UBOOT session as expected (%s)' % type(e).__name__, flush=True)
        finally:
            scsiio_macos.TIMEOUT_MS = normal

    print('\nThe keyboard should now show the file list and enumerate on USB as')
    print('1209:0003 "Oscuro File Bridge". Talk to it with tools/usbfiles_host.py')
    print('(Mac) or the Android app (phone over OTG). Power-cycle to leave.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
