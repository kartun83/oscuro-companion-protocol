import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ocp import wire as w                                    # noqa: E402
from ocp.client import Client, LoopbackTransport             # noqa: E402
from ocp.device import Entity, Namespace, SimDevice          # noqa: E402


def make(host_max_msg=64, mps=64, dev_max_msg=64, **kw):
    patches = Namespace(1, 'patches', role=1, capacity=2048)
    patches.add('lead.syx', bytes(range(200)), format=1)
    patches.add('bass.syx', b'B' * 100, format=1)
    patches.add('notes.txt', b'hello', format=3)
    log = Namespace(2, 'log', role=8, access=w.AC_READ)
    log.add('report.txt', b'', format=3, oflags=w.OF_VIRTUAL | w.OF_SIZE_UNKNOWN, gen=lambda: b'LOG' * 40)
    ents = [Entity(1, 'Filter cutoff', 'CUT', value=64, unit='steps'), Entity(2, 'Master volume', 'VOL', value=100)]
    dev = SimDevice([patches, log], ents, max_msg=dev_max_msg, mps=mps, **kw)
    tr = LoopbackTransport(dev, mps)
    cl = Client(tr, max_msg=host_max_msg, mps=mps, sleep=lambda s: None)
    return dev, cl, tr
