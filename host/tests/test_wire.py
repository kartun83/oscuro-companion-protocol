import unittest

from helpers import w


class WireTests(unittest.TestCase):
    def test_spec_hello_vector(self):
        # PROTOCOL_V1.md section 14
        m = w.Msg(w.HELLO, 1, 0, [(w.T_PROTO, w.u(0x0100)), (w.T_PEER, w.s('Android')), (w.T_MAX_MSG, w.u(512))])
        raw = w.encode(m)
        self.assertEqual(raw.hex(' '), '01 00 01 00 01 02 00 01 02 07 41 6e 64 72 6f 69 64 03 02 00 02')
        self.assertEqual(len(raw), 21)
        self.assertEqual(w.decode(raw), m)

    def test_spec_data_vector_with_pad(self):
        data = bytes(range(52))
        m = w.Msg(w.DATA, 0, 0, [(w.T_HANDLE, w.u(5)), (w.T_OFFSET, w.u(0x1000)), (w.T_RAW, data)])
        raw = w.encode(m, mps=64)
        self.assertEqual(raw[:4].hex(' '), '17 04 00 00')                    # PAD flag set
        self.assertEqual(raw[4:11].hex(' '), '10 01 05 11 02 00 10')
        self.assertEqual(raw[11], 0)                                          # raw tail tag
        self.assertEqual(len(raw), 65)
        back = w.decode(raw)
        self.assertEqual(back.get(w.T_RAW), data)
        self.assertEqual(back.flags & w.F_PAD, 0)

    def test_pad_only_when_multiple_of_packet(self):
        for mps in (64, 512):
            for n in range(0, 3 * mps, 7):
                m = w.Msg(w.DATA, 0, 0, [(w.T_RAW, b'x' * n)])
                raw = w.encode(m, mps)
                self.assertNotEqual(len(raw) % mps, 0)
                self.assertEqual(w.decode(raw).get(w.T_RAW), b'x' * n)

    def test_minimal_integers(self):
        self.assertEqual(w.u(0), b'\x00')
        self.assertEqual(w.u(255), b'\xff')
        self.assertEqual(w.u(256), b'\x00\x01')
        self.assertEqual(w.u(1 << 32), (1 << 32).to_bytes(8, 'little'))
        m = w.Msg(w.OPEN, 1, 0, [(w.T_SIZE, b'\x05\x00\x00')])         # a wider encoding is accepted
        self.assertEqual(w.decode(w.encode(m)).uint(w.T_SIZE), 5)

    def test_extended_length_field(self):
        big = b'z' * 600
        raw = w.encode(w.Msg(w.CAPS, 3, 0, [(w.T_TEXT, big)]), mps=512)
        self.assertEqual(w.decode(raw).get(w.T_TEXT), big)

    def test_groups_nest_and_repeat(self):
        recs = [(w.G_RECORD, w.group([(w.T_ID, w.u(i)), (w.T_NAME, w.s('n%d' % i))])) for i in range(3)]
        m = w.decode(w.encode(w.Msg(w.LIST, 1, w.F_RESP, recs)))
        self.assertEqual([w.fuint(g, w.T_ID) for g in m.groups()], [0, 1, 2])

    def test_unknown_tags_are_kept_and_ignorable(self):
        m = w.decode(w.encode(w.Msg(w.HELLO, 1, 0, [(0xC5, b'vendor'), (w.T_PEER, b'x')])))
        self.assertEqual(m.text(w.T_PEER), 'x')
        self.assertEqual(m.get(0xC5), b'vendor')

    def test_malformed_input_is_rejected(self):
        for raw in (b'', b'\x01\x00', b'\x01\x00\x01\x00\x07\x05ab', b'\x01\x04\x01\x00',
                    b'\x01\x00\x01\x00\x07\xff\x01'):
            with self.assertRaises(w.WireError):
                w.decode(raw)

    def test_raw_tail_must_be_last(self):
        with self.assertRaises(w.WireError):
            w.encode_fields([(w.T_RAW, b'a'), (w.T_ID, b'\x01')])


if __name__ == '__main__':
    unittest.main()
