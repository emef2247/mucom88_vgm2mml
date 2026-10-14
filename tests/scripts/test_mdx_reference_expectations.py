"""Authored binary cases for the converter-independent reference reader."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from mdx_reference_expectations import ReferenceError, read_expectations, read_mdx, read_pdx, write_expectations


def score(p=b'\xf1\x00', a=b'\xf1\x00', tone=b''):
    tracks = [a] + [b'\xf1\x00'] * 7 + [p]
    starts, offset = [], 20
    for track in tracks:
        starts.append(offset)
        offset += len(track)
    return b'Authored test\r\n\x1aTEST\x00' + struct.pack('>10H', offset, *starts) + b''.join(tracks) + tone


def pdx(payload=b'\x12\x34\x56', slot=1):
    table = bytearray(768)
    struct.pack_into('>II', table, slot * 8, 768, len(payload))
    return bytes(table) + payload


class ReferenceExpectationsTests(unittest.TestCase):
    def test_gate_default_controls_and_hold_are_separate(self):
        data = score(p=b'\x81\x0b\xf8\x04\xf7\x81\x0b\x81\x0b\x03\xf1\x00')
        track = read_mdx(data)['tracks'][8]
        self.assertTrue(track['complete'])
        first, held, continuation, rest = track['events']
        self.assertEqual((first['duration_ticks'], first['gate_ticks']), (12, 12))
        self.assertEqual(first['q_origin'], 'standard_driver_profile_default')
        self.assertIsNone(first['pan'])
        self.assertEqual(held['gate_ticks'], 6)
        self.assertTrue(held['hold'])
        self.assertIsNone(held['release_tick'])
        self.assertTrue(continuation['continuation'])
        self.assertFalse(continuation['retrigger_intent'])
        self.assertEqual(continuation['release_tick'], 30)
        self.assertEqual(rest['start_tick'], 36)
        self.assertFalse(continuation['physical_stop_verified'])
        self.assertIsNone(continuation['decoder_reset_known'])
        self.assertEqual(track['termination']['kind'], 'finite_end')

    def test_repeat_escape_and_one_song_cycle(self):
        # [n1,2 / n2,3]3 then L to repeat start, relative to end.
        p = b'\x00\xf6\x03\x00\x81\x01\xf4\x00\x03\x82\x02\xf5\xff\xf6\xf1\xff\xf0'
        track = read_mdx(score(p=p))['tracks'][8]
        self.assertTrue(track['complete'], track['diagnostics'])
        notes = [e for e in track['events'] if e['kind'] == 'note']
        self.assertEqual([e['slot'] for e in notes], [1, 2, 1, 2, 1])
        self.assertEqual([e['start_tick'] for e in notes], [1, 3, 6, 8, 11])
        self.assertEqual(track['termination']['loop_start_tick'], 1)
        self.assertEqual(track['termination']['cycle_ticks'], 12)
        escapes = [c for c in track['controls'] if c['kind'] == 'repeat_escape']
        self.assertEqual([c['taken'] for c in escapes], [False, False, True])

    def test_nested_repeats(self):
        p = b'\xf6\x02\x00\xf6\x03\x00\x80\x00\xf5\xff\xfb\xf5\xff\xf5\xf1\x00'
        track = read_mdx(score(p=p))['tracks'][8]
        self.assertTrue(track['complete'], track['diagnostics'])
        self.assertEqual(len(track['events']), 6)
        self.assertEqual(track['duration_ticks'], 6)

    def test_unknown_truncation_and_bound_report_incomplete(self):
        for p, detail in ((b'\xee\xf1\x00', 'unsupported'),
                          (b'\xff', 'truncated'),
                          (b'\xf5\xff\xff\xf1\x00', 'repeat end')):
            with self.subTest(p=p):
                track = read_mdx(score(p=p))['tracks'][8]
                self.assertFalse(track['complete'])
                self.assertIn(detail, track['diagnostics'][0]['detail'])
                self.assertEqual(len(track['commands']), 1)
        track = read_mdx(score(p=b'\x80\x00\xf1\x00'), max_commands=1)['tracks'][8]
        self.assertFalse(track['complete'])
        self.assertIn('bound exceeded', track['diagnostics'][0]['detail'])

    def test_signed_gate_native_branch_and_lfo_controls(self):
        a = b'\xec\x01\x00\x04\x00\x08\xe9\x02\xf8\xff\xa0\x0b\xeb\x80\xea\x81\xf1\x00'
        track = read_mdx(score(a=a))['tracks'][0]
        self.assertTrue(track['complete'], track['diagnostics'])
        self.assertEqual(track['events'][0]['gate_ticks'], 11)
        self.assertEqual([c['kind'] for c in track['controls']],
                         ['pitch_lfo', 'lfo_key_delay', 'gate', 'amplitude_lfo', 'opm_lfo'])
        self.assertEqual(track['controls'][0]['values'], [1, 0, 4, 0, 8])
        track = read_mdx(score(a=b'\xf8\x80\xa0\x00\xf1\x00'))['tracks'][0]
        self.assertEqual(track['events'][0]['gate_ticks'], 1)
    def test_fm_tone_raw_fields_and_controls(self):
        raw = bytes([7, 0x2f, 15] + [0x31] * 4 + [0x20] * 4 +
                    [0x9f] * 4 + [0x85] * 4 + [0x46] * 4 + [0xa7] * 4)
        result = read_mdx(score(a=b'\xfd\x07\xf3\xff\xc0\xf0\x02\xfc\x01\xfb\x0c\xa0\x0b\xf1\x00', tone=raw))
        tone = result['tones'][0]
        self.assertEqual((tone['voice'], tone['feedback'], tone['algorithm']), (7, 5, 7))
        self.assertEqual(tone['operators'][0]['dt1'], 3)
        event = result['tracks'][0]['events'][0]
        self.assertEqual((event['midi_note'], event['voice'], event['detune_64ths']), (35, 7, -64))
        self.assertEqual((event['key_delay_ticks'], event['pan'], event['volume_encoded']), (2, 1, 12))
        self.assertEqual(tone['raw_hex'], raw.hex())

    def test_pdx_exact_bytes_dedup_bounds_and_exports(self):
        payload = b'\x12\x34\x56'
        data = bytearray(pdx(payload))
        struct.pack_into('>II', data, 2 * 8, 768, 3)
        parsed, encoded = read_pdx(bytes(data))
        self.assertEqual(len(parsed['slots']), 96)
        self.assertEqual(len(parsed['samples']), 1)
        sample = parsed['samples'][0]
        self.assertEqual(sample['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(parsed['slots'][1]['sample_id'], parsed['slots'][2]['sample_id'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'x.mdx').write_bytes(score(p=b'\xed\x04\x81\x0b\xf1\x00'))
            (root / 'x.pdx').write_bytes(bytes(data))
            result = read_expectations(root / 'x.mdx', root / 'x.pdx')
            event = result['tracks'][8]['events'][0]
            self.assertEqual(event['binding_status'], 'valid')
            self.assertEqual(event['nominal_sample_nibbles'], 6)
            self.assertAlmostEqual(event['nominal_sample_exhaustion_seconds'], 6 / 15625)
            target = write_expectations(result, root / 'expected')
            saved = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual((target.parent / sample['encoded_file']).read_bytes(), payload)
            self.assertEqual(saved['pdx']['samples'][0]['codec'], 'msm6258-adpcm4-low-first')
            self.assertNotIn('_encoded_payloads', saved)
            pcm_expected = json.loads((target.parent / 'pcm_ir.expected.json').read_text())
            self.assertEqual(pcm_expected['variant'], 'decoded-mdx-pdx-requests')
            self.assertIsNone(pcm_expected['observed_source_fields']['physical_stop'])
        struct.pack_into('>II', data, 3 * 8, 767, 3)
        struct.pack_into('>II', data, 4 * 8, 769, 99)
        parsed, _ = read_pdx(bytes(data))
        self.assertFalse(parsed['slots'][3]['bounds_valid'])
        self.assertFalse(parsed['slots'][4]['bounds_valid'])
        with self.assertRaises(ReferenceError):
            read_pdx(b'\x00' * 767)

    def test_reject_extended_header_and_bad_loop(self):
        with self.assertRaises(ReferenceError):
            read_mdx(b'X\r\n\x1a\x00' + struct.pack('>HH', 100, 34) + b'\x00' * 100)
        track = read_mdx(score(p=b'\xf1\xff\xff'))['tracks'][8]
        self.assertFalse(track['complete'])
        self.assertIn('not an executed command', track['diagnostics'][0]['detail'])


if __name__ == '__main__':
    unittest.main()

