import unittest
import zlib

from helpers import make, w
from ocp.client import DeviceError


class SessionTests(unittest.TestCase):
    def test_hello_negotiates_and_lists_features(self):
        dev, cl, _ = make()
        info = cl.hello([0x0200, 0x0100])
        self.assertEqual(info['proto'], 0x0100)
        self.assertIn(w.FT_LIST, info['features'])
        self.assertNotIn(w.FT_MKDIR, info['features'])

    def test_hello_without_common_version(self):
        dev, cl, _ = make()
        with self.assertRaises(DeviceError) as e:
            cl.hello([0x0200])
        self.assertEqual(e.exception.code, w.E_UNSUPPORTED_VERSION)

    def test_caps_describe_limits_namespaces_formats(self):
        dev, cl, _ = make()
        cl.hello()
        caps = cl.caps()
        self.assertEqual(caps['limits'][w.T_MAX_HANDLES], 2)
        self.assertEqual([n['name'] for n in caps['namespaces']], ['patches', 'log'])
        self.assertEqual(caps['namespaces'][1]['access'], w.AC_READ)
        self.assertEqual(caps['namespaces'][0]['free'], 2048 - 305)
        self.assertEqual(caps['formats'][0]['ext'], 'syx')

    def test_unknown_request_gets_unsupported_and_unsolicited_is_dropped(self):
        dev, cl, _ = make()
        cl.hello()
        with self.assertRaises(DeviceError) as e:
            cl.request(0xC7)
        self.assertEqual(e.exception.code, w.E_UNSUPPORTED)
        dev.receive(w.encode(w.Msg(0xC7, 0)))        # req 0: silently dropped
        self.assertIsNone(dev.poll())

    def test_unadvertised_feature_is_refused(self):
        dev, cl, _ = make()
        cl.hello()
        with self.assertRaises(DeviceError) as e:
            cl.request(w.MKDIR, [(w.T_NS, w.u(1)), (w.T_NAME, b'x')])
        self.assertEqual(e.exception.code, w.E_UNSUPPORTED)

    def test_malformed_messages_are_dropped_not_fatal(self):
        dev, cl, _ = make()
        dev.receive(b'\x01')
        dev.receive(b'\x01\x00\x01\x00\x07\x09ab')
        self.assertEqual(dev.dropped, 2)
        self.assertEqual(cl.hello()['peer'], dev.name)

    def test_ping_echoes(self):
        dev, cl, _ = make()
        self.assertEqual(cl.ping('abc'), 'abc')


class ListTests(unittest.TestCase):
    def test_list_pages_into_many_messages_at_64_bytes(self):
        dev, cl, tr = make()
        cl.hello()
        r = cl.list(1)
        self.assertEqual([o['name'] for o in r['objects']], ['lead.syx', 'bass.syx', 'notes.txt'])
        self.assertEqual(r['total'], 3)
        self.assertFalse(r['more'])
        self.assertTrue(all(len(x) <= 64 for x in tr.sent))

    def test_list_one_message_when_host_accepts_large(self):
        dev, cl, _ = make(host_max_msg=512, dev_max_msg=512)
        cl.hello()
        cl.t.dev.mps = 64
        r = cl.list(1)
        self.assertEqual(r['total'], 3)

    def test_list_paging_sort_filter(self):
        dev, cl, _ = make()
        cl.hello()
        r = cl.list(1, start=1, count=1, sort=1)
        self.assertEqual([o['name'] for o in r['objects']], ['lead.syx'])
        self.assertTrue(r['more'])
        self.assertEqual([o['name'] for o in cl.list(1, sort=0x81)['objects']], ['notes.txt', 'lead.syx', 'bass.syx'])
        self.assertEqual([o['name'] for o in cl.list(1, formats=[3])['objects']], ['notes.txt'])

    def test_stat_and_not_found(self):
        dev, cl, _ = make()
        cl.hello()
        self.assertEqual(cl.stat(1, name='bass.syx')['size'], 100)
        with self.assertRaises(DeviceError) as e:
            cl.stat(1, id=99)
        self.assertEqual(e.exception.code, w.E_NOT_FOUND)
        with self.assertRaises(DeviceError):
            cl.list(9)


class ReadTests(unittest.TestCase):
    def test_read_uses_credits_and_is_exact(self):
        dev, cl, tr = make()
        cl.hello()
        data = cl.read(1, 1)
        self.assertEqual(data, bytes(range(200)))
        self.assertTrue(all(len(x) <= 64 for x in tr.sent))

    def test_read_virtual_unknown_size_ends_on_fin(self):
        dev, cl, _ = make()
        cl.hello()
        self.assertEqual(cl.read(2, 1), b'LOG' * 40)
        self.assertIsNone(cl.stat(2, id=1)['size'])

    def test_read_empty_object(self):
        dev, cl, _ = make()
        dev.ns[1].add('empty.txt', b'')
        cl.hello()
        self.assertEqual(cl.read(1, 4), b'')

    def test_read_at_512_byte_messages(self):
        dev, cl, _ = make(host_max_msg=512, dev_max_msg=512, mps=512, max_data=400)
        cl.mps = 512
        big = bytes(i * 7 % 251 for i in range(3000))
        dev.ns[1].capacity = 10000
        o = dev.ns[1].add('big.bin', big)
        cl.hello()
        self.assertEqual(cl.read(1, o.id), big)

    def test_handle_limit(self):
        dev, cl, _ = make()
        cl.hello()
        for _ in range(2):
            cl.request(w.OPEN, [(w.T_NS, w.u(1)), (w.T_ID, w.u(1)), (w.T_MODE, w.u(w.M_READ)), (w.T_CREDITS, w.u(0))])
        with self.assertRaises(DeviceError) as e:
            cl.request(w.OPEN, [(w.T_NS, w.u(1)), (w.T_ID, w.u(1)), (w.T_MODE, w.u(w.M_READ))])
        self.assertEqual(e.exception.code, w.E_TOO_MANY)


class WriteTests(unittest.TestCase):
    def test_create_then_read_back(self):
        dev, cl, tr = make()
        cl.hello()
        payload = bytes(range(256)) * 2
        dev.ns[1].capacity = 4096
        o = cl.write(1, payload, name='new.syx')
        self.assertEqual(o['size'], 512)
        self.assertEqual(cl.read(1, o['id']), payload)
        self.assertTrue(all(len(x) <= 64 for x in tr.sent))

    def test_replace_is_atomic_on_crc_failure(self):
        dev, cl, _ = make()
        cl.hello()
        before = dev.ns[1].objects[3].data
        with self.assertRaises(DeviceError) as e:
            cl.write(1, b'corrupt', id=3, crc=1234)
        self.assertEqual(e.exception.code, w.E_CRC_MISMATCH)
        self.assertEqual(dev.ns[1].objects[3].data, before)
        self.assertEqual(dev.ns[1].objects[3].rev, 1)

    def test_replace_bumps_rev_and_if_rev_guards(self):
        dev, cl, _ = make()
        cl.hello()
        o = cl.write(1, b'v2', id=3, if_rev=1)
        self.assertEqual((o['rev'], o['size']), (2, 2))
        with self.assertRaises(DeviceError) as e:
            cl.write(1, b'v3', id=3, if_rev=1)
        self.assertEqual(e.exception.code, w.E_REV_MISMATCH)
        self.assertEqual(dev.ns[1].objects[3].data, b'v2')

    def test_create_existing_and_bad_name(self):
        dev, cl, _ = make()
        cl.hello()
        with self.assertRaises(DeviceError) as e:
            cl.write(1, b'x', name='notes.txt')
        self.assertEqual(e.exception.code, w.E_EXISTS)
        with self.assertRaises(DeviceError) as e:
            cl.write(1, b'x', name='a/b')
        self.assertEqual(e.exception.code, w.E_BAD_NAME)

    def test_read_only_namespace_and_virtual_object(self):
        dev, cl, _ = make()
        cl.hello()
        with self.assertRaises(DeviceError) as e:
            cl.write(2, b'x', name='y.txt')
        self.assertEqual(e.exception.code, w.E_ACCESS)
        dev.ns[2].access |= w.AC_WRITE
        with self.assertRaises(DeviceError) as e:
            cl.write(2, b'x', id=1)
        self.assertEqual(e.exception.code, w.E_ACCESS)

    def test_no_space(self):
        dev, cl, _ = make()
        cl.hello()
        with self.assertRaises(DeviceError) as e:
            cl.write(1, b'x' * 3000, name='huge.bin')
        self.assertEqual(e.exception.code, w.E_NO_SPACE)

    def test_busy_device_naks_then_succeeds(self):
        dev, cl, _ = make()
        cl.hello()
        dev.busy_playing = True
        naps = []
        cl.sleep = lambda s: (naps.append(s), setattr(dev, 'busy_playing', len(naps) < 2))
        o = cl.write(1, b'ok', name='late.txt')
        self.assertEqual(o['size'], 2)
        self.assertEqual(naps, [0.1, 0.1])

    def test_busy_err_style_reports_retry_hint(self):
        dev, cl, _ = make()
        cl.hello()
        dev.busy_playing, dev.busy_style = True, 'err'
        with self.assertRaises(DeviceError) as e:
            cl.write(1, b'x', name='q.txt')
        self.assertEqual((e.exception.code, e.exception.retry_ms), (w.E_BUSY_PLAYING, 200))

    def test_out_of_order_data_is_rejected_with_expected_offset(self):
        dev, cl, _ = make()
        cl.hello()
        (op,) = cl.request(w.OPEN, [(w.T_NS, w.u(1)), (w.T_MODE, w.u(w.M_CREATE)), (w.T_NAME, b'o.bin')])
        h = op.uint(w.T_HANDLE)
        cl._send(w.Msg(w.DATA, 0, 0, [(w.T_HANDLE, w.u(h)), (w.T_OFFSET, w.u(10)), (w.T_RAW, b'late')]))
        m = cl._recv()
        self.assertEqual((m.type, m.uint(w.T_CODE)), (w.ERR, w.E_OUT_OF_ORDER))
        self.assertEqual(m.uint(w.T_OFFSET), 0)

    def test_abort_discards_and_frees_handle(self):
        dev, cl, _ = make()
        cl.hello()
        (op,) = cl.request(w.OPEN, [(w.T_NS, w.u(1)), (w.T_MODE, w.u(w.M_CREATE)), (w.T_NAME, b'a.bin')])
        cl.request(w.ABORT, [(w.T_HANDLE, op.get(w.T_HANDLE))])
        self.assertEqual(len(dev.ns[1].objects), 3)
        with self.assertRaises(DeviceError) as e:
            cl.request(w.COMMIT, [(w.T_HANDLE, op.get(w.T_HANDLE)), (w.T_SIZE, w.u(0)), (w.T_CRC32, w.u(0))])
        self.assertEqual(e.exception.code, w.E_NO_HANDLE)

    def test_delete_and_rename(self):
        dev, cl, _ = make()
        cl.hello()
        self.assertEqual(cl.rename(1, 2, 'bass2.syx')['name'], 'bass2.syx')
        with self.assertRaises(DeviceError) as e:
            cl.rename(1, 2, 'lead.syx')
        self.assertEqual(e.exception.code, w.E_EXISTS)
        with self.assertRaises(DeviceError) as e:
            cl.delete(1, 2, if_rev=1)
        self.assertEqual(e.exception.code, w.E_REV_MISMATCH)
        cl.delete(1, 2)
        self.assertNotIn(2, dev.ns[1].objects)


class UnsolicitedTests(unittest.TestCase):
    def test_events_need_subscription_and_credit(self):
        dev, cl, _ = make()
        cl.hello()
        cl.write(1, b'a', name='e1.txt')                 # not subscribed: nothing queued
        self.assertEqual(cl.drain(), [])
        cl.subscribe([w.EV_OBJECT_CHANGED])
        cl.write(1, b'b', name='e2.txt')
        self.assertEqual(cl.drain(), [])                  # no credit yet
        cl.grant(1)
        msgs = cl.drain()
        self.assertEqual([(m.type, m.uint(w.T_KIND)) for m in msgs], [(w.EVENT, w.EV_OBJECT_CHANGED)])

    def test_announce_queue_drops_lowest_priority_oldest(self):
        dev, cl, _ = make(announce_queue=3)
        cl.hello()
        dev.announce('low', prio=0)
        dev.announce('hi1', prio=2)
        dev.announce('hi2', prio=2)
        dev.announce('hi3', prio=2)                       # evicts 'low'
        cl.grant(10)
        texts = [m.text(w.T_TEXT) for m in cl.drain()]
        self.assertEqual(texts, ['hi1', 'hi2', 'hi3'])
        self.assertEqual(dev.dropped, 1)

    def test_announce_carries_speakable_and_short_forms(self):
        dev, cl, _ = make()
        cl.hello()
        dev.announce('Filter cutoff, sixty four steps', short='CUT 64', prio=1, cat=2)
        cl.grant(1)
        (m,) = cl.drain()
        self.assertEqual((m.text(w.T_TEXT), m.text(w.T_SHORT), m.uint(w.T_CAT)), ('Filter cutoff, sixty four steps', 'CUT 64', 2))

    def test_control_traffic_overtakes_file_data(self):
        dev, cl, _ = make()
        cl.hello()
        cl.request(w.OPEN, [(w.T_NS, w.u(1)), (w.T_ID, w.u(1)), (w.T_MODE, w.u(w.M_READ))])
        dev.receive(w.encode(w.Msg(w.PING, 9, 0, [(w.T_TEXT, b'p')])))
        self.assertEqual(w.decode(dev.poll()).type, w.PING)   # PONG before the DATA already queued

    def test_local_value_changes_coalesce(self):
        dev, cl, _ = make()
        cl.hello()
        for v in (10, 20, 30):
            dev.set_local(1, v)
        cl.grant(5)
        msgs = cl.drain()
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0].uint(w.T_VAL), msgs[0].uint(w.T_SRC)), (30, 1))
        self.assertIn('Filter cutoff', msgs[0].text(w.T_SPEAK))


class EntityTests(unittest.TestCase):
    def test_registry_has_long_and_short_names(self):
        dev, cl, _ = make()
        cl.hello()
        ents = cl.entities()
        self.assertEqual((ents[0]['name'], ents[0]['short']), ('Filter cutoff', 'CUT'))

    def test_remote_set_is_denied_until_enabled_then_clamped(self):
        dev, cl, _ = make()
        cl.hello()
        with self.assertRaises(DeviceError) as e:
            cl.set_value(1, 5)
        self.assertEqual(e.exception.code, w.E_DENIED)
        dev.remote_control = True
        r = cl.set_value(1, 500)
        self.assertEqual((r['val'], r['src']), (127, 2))
        self.assertEqual(r['speak'], 'Filter cutoff, 127 steps')


if __name__ == '__main__':
    unittest.main()
