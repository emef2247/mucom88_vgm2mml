"""Semantic checks of the authored public native-MXC reference assets."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import unittest

from mdx_reference_expectations import read_expectations

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/public/mdx_reference'
TICK = Fraction(16 * 1024, 4000000)


def track(stem, name='P'):
    result = read_expectations(FIXTURES/(stem+'.MDX'), FIXTURES/'PUBPCM.PDX')
    return next(t for t in result['tracks'] if t['track'] == name)


class PublicMdxReferenceTests(unittest.TestCase):
    def test_packed_bytes_match_precompile_authored_samples(self):
        expected = json.loads((FIXTURES/'GATEEND.pcm_ir.expected.json').read_text())
        self.assertEqual(expected['variant'], 'authored-score-intent')
        result = read_expectations(FIXTURES/'GATEEND.MDX', FIXTURES/'PUBPCM.PDX')
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['pdx']['slots']), 96)
        self.assertEqual(sum(not s['empty'] for s in result['pdx']['slots']), 2)
        for source, sample in zip(expected['samples'], result['pdx']['samples']):
            raw = (FIXTURES/source['encoded_file']).read_bytes()
            self.assertEqual(sample['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(sample['byte_length'], len(raw))
            self.assertIsNone(expected['playback_requests'][0]['physical_stop_observed'])

    def test_gate_end_sample_exhaustion_and_finite_end_are_distinct(self):
        p = track('GATEEND')
        notes = [e for e in p['events'] if e['kind'] == 'note']
        first, short = notes[0], notes[2]
        self.assertGreater(first['nominal_sample_exhaustion_seconds'], float(first['gate_ticks']*TICK))
        self.assertGreater(short['nominal_sample_exhaustion_seconds'], float(short['gate_ticks']*TICK))
        self.assertEqual([e['start_tick'] for e in notes], [0,24,48,96])
        self.assertTrue(all(e['retrigger_intent'] for e in notes))
        self.assertEqual(p['termination']['kind'], 'finite_end')
        self.assertEqual(p['termination']['tick'], 168)
        self.assertFalse(p['termination']['physical_stop_verified'])
        self.assertLess(notes[-1]['release_tick'], p['termination']['tick'])

    def test_hold_continues_without_retrigger_and_last_note_releases(self):
        p = track('HOLDEND')
        notes = [e for e in p['events'] if e['kind'] == 'note']
        self.assertEqual([(n['start_tick'],n['duration_ticks']) for n in notes], [(0,192),(192,128)])
        self.assertTrue(notes[0]['hold'])
        self.assertIsNone(notes[0]['release_tick'])
        self.assertTrue(notes[1]['continuation'])
        self.assertFalse(notes[1]['retrigger_intent'])
        self.assertFalse(notes[1]['hold'])
        self.assertEqual(notes[1]['release_tick'], 320)
        self.assertEqual(p['duration_ticks'], 384)
        self.assertGreater(notes[0]['nominal_sample_exhaustion_seconds'],
                           float(p['duration_ticks']*TICK))

    def test_rate_and_pan_requests_and_held_fm_controls(self):
        notes = [e for e in track('RATES')['events'] if e['kind']=='note']
        self.assertEqual([e['sample_id'] for e in notes], [1,1,1,1])
        self.assertEqual([e['rate_code'] for e in notes], [4,0,4,4])
        self.assertEqual([e['pan'] for e in notes], [1,2,3,3])
        self.assertLess(notes[0]['nominal_sample_exhaustion_seconds'], float(notes[0]['gate_ticks']*TICK))
        self.assertGreater(notes[1]['nominal_sample_exhaustion_seconds'], float(notes[1]['gate_ticks']*TICK))
        fm = track('FMSTATE', 'A')
        notes = [e for e in fm['events'] if e['kind']=='note']
        self.assertEqual([e['midi_note'] for e in notes[:3]], [48,48,48])
        self.assertEqual([e['continuation'] for e in notes[:3]], [False,True,True])
        self.assertEqual([e['volume_encoded'] for e in notes[:3]], [12,8,8])
        self.assertEqual([e['pan'] for e in notes[:3]], [3,1,2])
        self.assertEqual([e['voice'] for e in notes[:4]], [0,0,0,1])
        self.assertEqual(fm['duration_ticks'], 192)


if __name__ == '__main__':
    unittest.main()
