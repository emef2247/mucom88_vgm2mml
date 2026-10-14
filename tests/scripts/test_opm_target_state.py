"""Projected register-state evidence; no private audio or note expectations."""
import csv
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT)]
from chip_segments import PsgSegment, SccAnalysis
from opm import OpmRegisterState
from opm_target_state import build_target_trajectory
from psg_scc_opm import TargetWrite, project


def write(identifier, tick, register, data, *, ch=5, sample=None):
    return TargetWrite(identifier, 'psg', ch - 5, identifier,
                       tick * 100 if sample is None else sample, tick, ch, register, data, 'test')


def replay(writes, end=10, source_end=1000):
    return build_target_trajectory(writes, end_tick=end, source_end_vgmticks=source_end)


class TargetTrajectoryTests(unittest.TestCase):
    def test_fm_projection_held_pitch_volume_mute_unmute(self):
        first = PsgSegment('v', 0., 0, 0, 1, 200, 15, 4, 'c', 0, (),
                           1, 0, 0, 0, 0, 0x3e, 15, vgmticks=0, vgmticks_end=441)
        rows = [first, replace(first, tone_period=190, volume=12, vgmticks=441, vgmticks_end=882),
                replace(first, volume=0, vgmticks=882, vgmticks_end=1323),
                replace(first, vgmticks=1323, vgmticks_end=1764)]
        plan = project({0: rows}, SccAnalysis({}, ()), psg_clock=1789772,
                       scc_clock=0, end_vgmticks=1764, psg_model='fm')
        before = tuple(plan.writes)
        trajectory = replay(plan.scheduled_writes(), plan.end_tick, plan.source_end)
        self.assertEqual(tuple(plan.writes), before)
        self.assertEqual([e.write_id for e in trajectory.events[1:]],
                         [w.write_id for w in plan.scheduled_writes()])
        self.assertEqual(len(trajectory.intervals), 4)
        self.assertEqual([i.state.key_mask for i in trajectory.intervals], [15] * 4)
        self.assertEqual([i.state.left_enabled for i in trajectory.intervals], [1, 1, 0, 1])
        self.assertEqual([i.state.right_enabled for i in trajectory.intervals], [1, 1, 0, 1])
        self.assertEqual([i.state.algorithm for i in trajectory.intervals], [4] * 4)
        self.assertEqual([i.state.operators[2].tl for i in trajectory.intervals], [8, 20, 20, 8])
        self.assertNotEqual(trajectory.intervals[0].state.kc_raw, trajectory.intervals[1].state.kc_raw)
        self.assertEqual(trajectory.intervals[1].state.kc_raw, trajectory.intervals[2].state.kc_raw)
        self.assertEqual([e.rising_mask for e in trajectory.events if e.rising_mask], [15])
        self.assertEqual(trajectory.events[-1].state.key_mask, 0)
        self.assertEqual(trajectory.events[-1].mdx_tick, plan.end_tick)
        # Independent register dictionary replay verifies all interval raw controls.
        for interval in trajectory.intervals:
            registers, key = {}, None
            for w in plan.scheduled_writes():
                if w.mdx_tick > interval.mdx_tick:
                    break
                if w.register == 8:
                    key = w.data
                else:
                    registers[w.register] = w.data
            self.assertEqual(interval.state.channel_registers, tuple(sorted(registers.items())))
            self.assertEqual(interval.state.key_register_raw, key)

    def test_same_tick_transients_nonchanges_and_terminal_events(self):
        writes = [write(0, 0, 8, 5), write(1, 2, 8, 0x7d),
                  write(2, 2, 0x2d, 0x4a), write(3, 2, 0x2d, 0x4b),
                  write(4, 2, 0x2d, 0x4b), write(5, 2, 8, 5),
                  write(6, 2, 8, 0x7d), write(7, 10, 8, 5)]
        trajectory = replay(writes)
        self.assertEqual(len(trajectory.events), len(writes) + 1)
        self.assertEqual([e.state.kc_raw for e in trajectory.events[3:6]], [0x4a, 0x4b, 0x4b])
        self.assertFalse(trajectory.events[5].changed)
        self.assertEqual(trajectory.events[1].state.key_register_raw, 5)
        self.assertTrue(trajectory.events[1].changed)  # First observed cleared Key.
        self.assertEqual([(i.mdx_tick, i.mdx_tick_end) for i in trajectory.intervals], [(0, 2), (2, 10)])
        self.assertEqual(trajectory.intervals[1].write_ids, (1, 2, 3, 4, 5, 6))
        self.assertEqual(trajectory.intervals[1].state.kc_raw, 0x4b)
        self.assertEqual(trajectory.intervals[1].state.key_mask, 15)
        self.assertEqual(trajectory.events[-1].falling_mask, 15)
        self.assertFalse(any(7 in i.write_ids for i in trajectory.intervals))

    def test_muted_positive_duration_pitch_and_level_controls_survive(self):
        writes = [write(0, 0, 0x25, 0x3c), write(1, 0, 8, 0x7d),
                  write(2, 2, 0x2d, 0x41), write(3, 4, 0x75, 8),
                  write(4, 6, 0x25, 0xfc), write(5, 10, 8, 5)]
        trajectory = replay(writes)
        self.assertEqual([e.write_id for e in trajectory.events[1:]], list(range(6)))
        self.assertEqual([(i.mdx_tick, i.mdx_tick_end) for i in trajectory.intervals],
                         [(0, 2), (2, 4), (4, 6), (6, 10)])
        self.assertEqual([i.state.left_enabled for i in trajectory.intervals], [0, 0, 0, 1])
        self.assertEqual([i.state.key_mask for i in trajectory.intervals], [15] * 4)
        self.assertEqual([i.state.kc_raw for i in trajectory.intervals], [None, 0x41, 0x41, 0x41])
        self.assertEqual([i.state.operators[2].tl for i in trajectory.intervals], [None, None, 8, 8])
        self.assertEqual([e.write_id for e in trajectory.events if e.rising_mask], [1])

    def test_multiple_tracks_origin_coverage_and_native_decoder(self):
        writes = [write(0, 1, 0x25, 0xc4), write(1, 1, 0x26, 0xc7, ch=6),
                  write(2, 3, 0x75, 20), write(3, 5, 0x46, 3, ch=6)]
        trajectory = replay(writes)
        decoder = OpmRegisterState()
        for w, event in zip(writes, trajectory.events[2:]):
            decoder.write(w.register, w.data)
            self.assertEqual(event.state, decoder.snapshot(w.target_ch))
        for ch in (5, 6):
            intervals = [i for i in trajectory.intervals if i.target_ch == ch]
            self.assertEqual(intervals[0].mdx_tick, 0)
            self.assertEqual(intervals[-1].mdx_tick_end, 10)
            self.assertEqual(sum(i.duration_ticks for i in intervals), 10)
            self.assertEqual([i.mdx_tick_end for i in intervals[:-1]],
                             [i.mdx_tick for i in intervals[1:]])

    def test_provenance_immutability_unknowns_and_csv(self):
        w = replace(write(7, 3, 0x2d, 0x4a, sample=321), source_row=2)
        trajectory = replay([w])
        initial, event = trajectory.events
        self.assertEqual(initial.state.key_mask, 0)
        self.assertIsNone(initial.state.key_register_raw)
        self.assertIsNone(initial.vgmticks)
        self.assertIsNone(initial.state.algorithm)
        self.assertTrue(all(op.tl is None for op in initial.state.operators))
        self.assertEqual((event.source_chip, event.source_ch, event.source_row, event.vgmticks),
                         ('psg', 0, 2, 321))
        with self.assertRaises(FrozenInstanceError):
            event.mdx_tick = 4
        with self.assertRaises(FrozenInstanceError):
            event.state.operators[0].tl = 0
        with tempfile.TemporaryDirectory() as temp:
            state, intervals = Path(temp) / 'state.csv', Path(temp) / 'intervals.csv'
            trajectory.dump(state_csv=state, intervals_csv=intervals)
            with state.open(newline='', encoding='utf-8') as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual(records[1]['state_origin'], 'projected_opm')
            self.assertEqual(records[1]['vgmticks'], '321')
            self.assertEqual(records[1]['source_row'], '2')
            self.assertEqual(records[0]['m1_tl'], '')
            self.assertEqual(json.loads(records[1]['channel_registers']), [[45, 74]])
            with intervals.open(newline='', encoding='utf-8') as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual(json.loads(records[1]['write_ids']), [7])
            self.assertEqual(records[1]['duration_ticks'], '7')
            self.assertEqual(records[1]['source_end_vgmticks'], '1000')
        self.assertEqual(trajectory.summary()['target_state_write_events'], 1)

    def test_empty_and_zero_end_emit_headers_without_fake_intervals(self):
        for trajectory in (replay([]), replay([write(0, 0, 8, 5)], 0)):
            self.assertEqual(trajectory.intervals, ())
            with tempfile.TemporaryDirectory() as temp:
                state, intervals = Path(temp) / 'state.csv', Path(temp) / 'intervals.csv'
                trajectory.dump(state_csv=state, intervals_csv=intervals)
                self.assertIn('state_origin', state.read_text())
                self.assertIn('duration_ticks', intervals.read_text())
        self.assertEqual(replay([]).events, ())

    def test_repeated_key_controls_remain_as_evidence(self):
        trajectory = replay([write(0, 0, 8, 5), write(1, 0, 8, 5),
                             write(2, 10, 8, 5)])
        self.assertEqual([e.write_id for e in trajectory.events], [None, 0, 1, 2])
        self.assertEqual([e.changed for e in trajectory.events], [False, True, False, False])
        self.assertTrue(all(e.rising_mask == e.falling_mask == 0 for e in trajectory.events))
        self.assertEqual(trajectory.intervals[0].write_ids, (0, 1))
        self.assertEqual(trajectory.summary()['target_state_nonchange_writes'], 2)

    def test_invalid_evidence_is_rejected(self):
        w = write(0, 1, 0x2d, 0x4a)
        for field, value in [('write_id', -1), ('mdx_tick', 11), ('vgmticks', 1001),
                             ('target_ch', 6), ('register', 256), ('register', 0x18),
                             ('data', -1), ('source_row', -1), ('source_ch', 3),
                             ('source_chip', 'opm'), ('mdx_tick', True)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replay([replace(w, **{field: value})])
        for writes in ([w, w], [replace(w, write_id=1), replace(w, mdx_tick=0)]):
            with self.assertRaises(ValueError):
                replay(writes)
        for end, source_end in ((-1, 1000), (10, -1), (1.5, 1000)):
            with self.assertRaises(ValueError):
                replay([], end, source_end)


if __name__ == '__main__':
    unittest.main()
