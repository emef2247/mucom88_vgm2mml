"""Authored target chunking/alignment checks, without private source fixtures."""
from array import array
from fractions import Fraction
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from segapcm_playback import PlaybackAnalysis, PlaybackGate
from segapcm_pcm import project, CHUNK_TICKS, TARGET_RATE


def native(end, bounds=None, level=1000):
    bounds = bounds or [(Fraction(0), Fraction(end))]
    gates = tuple(PlaybackGate(i, 0, i, start, stop, i, i, 0, i + 10, 'source_end')
                  for i, (start, stop) in enumerate(bounds))
    return PlaybackAnalysis(Fraction(44100), array('i', [level, level] * max(1, min(end, 1000))),
                            gates, (), end, None, ())


class SegaPcmBridgeTests(unittest.TestCase):
    def test_final_gate_alignment_preserves_source_and_even_encoded_count(self):
        source = native(500, [(Fraction(1, 2), Fraction(499, 2))])
        result = project(source, source_raw=b'', loop_start=100)
        self.assertEqual([(p.start_vgmticks, p.end_vgmticks) for p in result.playbacks],
                         [(0, 100), (100, 250)])
        aligned = result.align_to_clock(lambda time: round(Fraction(time, 12)), 1)
        self.assertIs(aligned.native, source)
        self.assertFalse(aligned.normalization_omission_safe)
        self.assertEqual(len(aligned.transfers), 0)
        self.assertEqual(aligned.controls, ())
        for row in aligned.target_mapping:
            duration = row['target_end_tick'] - row['target_start_tick']
            wanted = Fraction(duration * 256 * TARGET_RATE, 1000000)
            self.assertLessEqual(abs(row['target_sample_count'] - wanted), 1)
            self.assertEqual(row['target_sample_count'] % 2, 0)
            self.assertEqual(row['padding_nibbles'], 0)
        with self.assertRaisesRegex(ValueError, 'collapses'):
            result.align_to_clock(lambda time: 0, 1)

    def test_chunking_retains_short_final_tail(self):
        def encoder(values):
            count = sum(1 for _ in values)
            return bytes(count // 2), 0
        with patch('segapcm_pcm.encode_msm6258', encoder):
            result = project(native(CHUNK_TICKS + 1), source_raw=b'')
        self.assertEqual([(p.start_vgmticks, p.end_vgmticks) for p in result.playbacks],
                         [(0, CHUNK_TICKS), (CHUNK_TICKS, CHUNK_TICKS + 1)])
        self.assertEqual(result.playbacks[-1].nominal_nibbles, 2)
        self.assertTrue(all(len(sample.encoded_bytes) <= 65535 for sample in result.samples))

    def test_mono_clipping_and_exact_payload_dedup(self):
        bounds = [(Fraction(0), Fraction(100)), (Fraction(200), Fraction(300))]
        result = project(native(300, bounds, level=40000), source_raw=b'')
        self.assertEqual(len(result.playbacks), 2)
        self.assertEqual(len(result.samples), 1)
        self.assertTrue(all(row['clipped_target_samples'] > 0 for row in result.target_mapping))
        self.assertTrue(any(row['code'] == 'segapcm_mono_clipping' for row in result.target_evidence))
        self.assertEqual([p.termination for p in result.playbacks], ['projected_chunk_boundary'] * 2)


if __name__ == '__main__':
    unittest.main()
