"""Whole FM gates and ordered partial-operator source edges."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_music import build_fm_gates, audit_fm_key_edges


def snapshot(mask):
    return SimpleNamespace(key_mask=mask, fnum=618, block=4, algorithm=2,
        feedback=0, left_enabled=1, right_enabled=1, ams=0, pms=0,
        operators=(), lfo_enabled=0, lfo_rate=0, fm_prescale_divisor=6,
        channel3_special_enabled=0, csm_enabled=0)


def analysis(sequence):
    records, previous = [], 0
    for identifier, (start, end, mask, continuity) in enumerate(sequence):
        records.append(SimpleNamespace(segment_id=identifier,
            source_event_id=identifier, vgmticks=start, vgmticks_end=end,
            duration_samples=end-start, continuity_id=continuity, ch=0,
            chip_instance=0, register=0x28, state=snapshot(mask),
            rising_mask=mask & ~previous, falling_mask=previous & ~mask))
        previous = mask
    return SimpleNamespace(segments=records, events=records)


class PartialGateTests(unittest.TestCase):
    def test_same_sample_partial_onset_and_full_retrigger_are_distinct(self):
        source = analysis([(0, 0, 8, 1), (0, 10, 15, 2),
                           (10, 20, 12, 2), (20, 20, 0, 2),
                           (20, 30, 15, 3), (30, 30, 0, 3)])
        tracks = build_fm_gates(source)
        audit_fm_key_edges(source, tracks)
        first, second = tracks['A']
        self.assertEqual((first.start, first.end), (0, 20))
        self.assertEqual([control.state.key_mask for control in first.controls], [8, 15, 12])
        self.assertEqual(first.source_event_ids, (0, 1, 2, 3))
        self.assertEqual(second.source_event_ids, (4, 5))
        self.assertEqual((second.start, second.end), (20, 30))

    def test_zero_time_full_off_pulse_remains_explicitly_unsupported(self):
        source = analysis([(0, 0, 8, 1), (0, 0, 0, 1),
                           (0, 10, 15, 2), (10, 10, 0, 2)])
        with self.assertRaisesRegex(ValueError, 'zero-duration/unmatched'):
            audit_fm_key_edges(source, build_fm_gates(source))


if __name__ == '__main__':
    unittest.main()
