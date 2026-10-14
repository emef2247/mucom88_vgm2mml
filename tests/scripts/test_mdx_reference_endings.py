"""Authored ending comparisons; no private song content in test expectations."""
from pathlib import Path
import struct
import tempfile
import unittest

from mdx_reference_endings import audit, resolve_pdx, summarize
from mdx_reference_expectations import ReferenceError, read_mdx
from test_mdx_reference_expectations import score


class ReferenceEndingsTests(unittest.TestCase):
    def test_rest_ending_does_not_mean_latest_pcm_gate(self):
        result = summarize(read_mdx(score(a=b'\xa0\x07\xf1\x00',
            p=b'\x80\x03\x0f\xf1\x00')))
        self.assertTrue(result['finite'])
        self.assertEqual(result['pcm_sequence_end_tick'], 20)
        self.assertEqual(result['pcm_last_note_end_tick'], 4)
        self.assertEqual(result['pcm_last_release_request_tick'], 4)
        self.assertEqual(result['last_pcm_events'][0]['kind'], 'rest')
        self.assertEqual(result['gate_request_relation'], 'fm_later')
        self.assertFalse(result['finite_pcm_later_candidate'])

    def test_finite_pcm_later_and_equal_are_distinct(self):
        later = summarize(read_mdx(score(a=b'\xa0\x03\xf1\x00',
            p=b'\xf8\x04\x80\x0b\xf1\x00')))
        self.assertTrue(later['finite_pcm_later_candidate'])
        self.assertEqual(later['pcm_last_note_end_tick'], 12)
        self.assertEqual(later['pcm_last_release_request_tick'], 6)
        same = summarize(read_mdx(score(a=b'\xa0\x0b\xf1\x00',
            p=b'\x80\x0b\xf1\x00')))
        self.assertEqual(same['note_end_relation'], 'equal')

    def test_loop_and_unknown_cannot_be_finite_candidates(self):
        loop = summarize(read_mdx(score(p=b'\x80\x00\xf1\xff\xfb')))
        self.assertEqual(loop['loop_tracks'], ['P'])
        self.assertFalse(loop['finite'])
        for op in (0xee, 0xef, 0xe7):
            decoded = read_mdx(score(a=bytes([op]) + b'\xf1\x00',
                                     p=b'\x80\x0b\xf1\x00'))
            incomplete = summarize(decoded)
            self.assertFalse(incomplete['static_decode_complete'])
            self.assertFalse(incomplete['finite_pcm_later_candidate'])
            self.assertEqual(incomplete['incomplete_tracks'], ['A'])

    def test_extended_layout_is_opt_in_and_labels_pcm_correctly(self):
        tracks = [b'\xe8\xf2\x00\x01\xa0\x03\xf1\x00'] + [b'\xf1\x00'] * 7
        tracks += [b'\x80\x07\xf1\x00'] * 8
        starts, offset = [], 34
        for track in tracks:
            starts.append(offset)
            offset += len(track)
        data = b'Test\r\n\x1a\x00' + struct.pack('>17H', offset, *starts) + b''.join(tracks)
        with self.assertRaises(ReferenceError):
            read_mdx(data)
        decoded = read_mdx(data, allow_pcm8=True)
        self.assertTrue(decoded['complete'])
        self.assertEqual([t['track'] for t in decoded['tracks']], list('ABCDEFGHPQRSTUVW'))
        self.assertEqual(decoded['tracks'][15]['events'][0]['slot'], 0)
        self.assertEqual(decoded['tracks'][0]['duration_ticks'], 4)
        self.assertEqual(decoded['tracks'][0]['controls'][0]['kind'], 'pcm8_enable')
        self.assertEqual(decoded['tracks'][0]['controls'][1]['kind'], 'portamento')

    def test_declared_pdx_resolution_and_audit_exports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'a.MDX').write_bytes(score(p=b'\x80\x00\xf1\x00'))
            (root / 'test.Pdx').write_bytes(bytes(768))
            self.assertEqual(resolve_pdx(root / 'a.MDX', 'TEST').name, 'test.Pdx')
            self.assertIsNone(resolve_pdx(root / 'a.MDX', 'DIFFERENT'))
            rows = audit(root, root / 'out')
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]['pdx_pair_exists'])
            self.assertTrue((root / 'out/summary.json').is_file())
            self.assertTrue((root / 'out/overview.csv').is_file())
            self.assertTrue((root / 'out/a/commands.csv').is_file())


if __name__ == '__main__':
    unittest.main()
