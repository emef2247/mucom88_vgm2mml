"""Small authored decoder-epoch checks; no private regression conversion."""
from dataclasses import replace
from fractions import Fraction
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_adpcm import AdpcmAnalysis, AdpcmSample, AdpcmSegment, decode_adpcm_b
from opna_adpcm_waveform import render, project


def source():
    sample = AdpcmSample(0, 'yamaha_adpcm_b_high_nibble_first', b'\x12\x34', '', 0, 2, ())
    def segment(identity, start, end, rate, level=128, pan=3):
        registers = [0] * 17
        registers[1] = pan << 6
        registers[9] = 1 if rate else 0
        registers[11] = level
        return AdpcmSegment(identity, 0, identity, 0, start, end, (identity,),
            0, start, rate, 1, level, pan, False, 'source_end', None, None,
            False, ('controls_during_decoder_epoch',), tuple(registers))
    segments = (segment(0, 0, 4, 22050), segment(1, 4, 8, 0),
                segment(2, 8, 14, 22050), segment(3, 14, 20, 22050, 255, 2))
    return AdpcmAnalysis(20, 8000000, 8000000, 1, (), (), (sample,), segments, (),
                         hashlib.sha256(b'authored').hexdigest())


class ContinuousAdpcmWaveformTests(unittest.TestCase):
    def test_pause_keeps_position_and_natural_end_prevents_restart(self):
        with patch('opna_adpcm_waveform.decode_adpcm_b', wraps=decode_adpcm_b) as decoder:
            result = render(source())
        self.assertEqual(decoder.call_count, 1)
        self.assertEqual(result.gates[0].end_vgmticks, Fraction(12))
        self.assertEqual(result.epochs[0].positions[1].position_start, Fraction(2))
        self.assertEqual(result.epochs[0].positions[1].position_end, Fraction(2))
        self.assertEqual(len(set(result.samples[8:16])), 1)
        self.assertTrue(all(value == 0 for value in result.samples[24:]))

    def test_live_pan_and_level_are_applied_once(self):
        analysis = source()
        # First control slice is left-only, half level. No decoder reset occurs.
        first = analysis.segments[0]
        registers = list(first.registers)
        registers[1] = 128
        first = replace(first, pan=2, registers=tuple(registers))
        result = render(replace(analysis, segments=(first, *analysis.segments[1:])))
        self.assertTrue(any(result.samples[0:8:2]))
        self.assertEqual(list(result.samples[1:8:2]), [0] * 4)
        decoded = decode_adpcm_b(analysis.samples[0].encoded_bytes)
        expected = round(decoded[0] * .25 * .5)
        self.assertEqual(result.samples[0], expected)
        target = project(analysis, source_raw=b'authored')
        self.assertFalse(target.normalization_omission_safe)
        self.assertEqual(target.target_mapping[0]['source_event_ids'], (0, 1, 2, 3))
        self.assertTrue(all('segapcm' not in row['code'] for row in target.target_evidence))

    def test_initial_zero_delta_holds_reset_predictor(self):
        analysis = source()
        first = analysis.segments[0]
        registers = list(first.registers)
        registers[9] = 0
        first = replace(first, rate_num=0, registers=tuple(registers),
            issues=(*first.issues, 'zero_delta_n_or_clock'))
        result = render(replace(analysis, segments=(first, *analysis.segments[1:])))
        self.assertTrue(all(value == 0 for value in result.samples[:16]))
        self.assertEqual(result.epochs[0].positions[0].position_end, Fraction(0))

    def test_repeat_and_oversized_waveform_are_rejected(self):
        analysis = source()
        repeated = replace(analysis.segments[0], repeat=True)
        with self.assertRaisesRegex(ValueError, 'repeat'):
            render(replace(analysis, segments=(repeated, *analysis.segments[1:])))
        with self.assertRaisesRegex(ValueError, 'bounded frame'):
            render(analysis, max_frames=10)


if __name__ == '__main__':
    unittest.main()
