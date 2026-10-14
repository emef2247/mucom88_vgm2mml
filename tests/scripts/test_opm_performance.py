"""Musical gate inference preserves baseline controls and source provenance."""
import copy
import csv
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT)]
from chip_segments import PsgSegment, SccAnalysis, SccSegment
from opm_performance import build_performance
from opm_target_state import build_target_trajectory
from psg_scc_opm import project


def psg(**changes):
    segment = PsgSegment('v', 0., 0, 0, 1, 200, 15, 4, 'c', 0, (),
                         1, 0, 0, 0, 0, 0x3e, 15, vgmticks=0, vgmticks_end=441)
    return replace(segment, **changes)


def baseline(rows, end=None):
    return project({0: rows}, SccAnalysis({}, ()), psg_clock=1789772,
                   scc_clock=0, end_vgmticks=rows[-1].vgmticks_end if end is None else end,
                   psg_model='fm')


class PerformanceTests(unittest.TestCase):
    def test_audibility_edges_only_and_setup_before_attack_release_before_mute(self):
        source = baseline([psg(volume=0), psg(vgmticks=441, vgmticks_end=882),
                           psg(vgmticks=882, vgmticks_end=1323, tone_period=190, volume=12),
                           psg(vgmticks=1323, vgmticks_end=1764, volume=0),
                           psg(vgmticks=1764, vgmticks_end=2205)])
        before = copy.deepcopy(source)
        plan = build_performance(source)
        self.assertEqual(source, before)
        self.assertEqual([r['inferred_transition'] for r in plan.rows],
                         ['rest', 'attack', 'continuation', 'release', 'attack'])
        keys = [w for w in plan.writes if w.register == 8]
        self.assertEqual([(w.source_row, bool(w.data & 0x78), w.inferred_key) for w in keys],
                         [(None, False, False), (1, True, True), (3, False, True),
                          (4, True, True), (None, False, True)])
        for row_index in (1, 4):
            writes = [w for w in plan.writes if w.source_row == row_index]
            self.assertTrue(writes[-1].inferred_key)
            self.assertTrue(writes[-1].data & 0x78)
        release = [w for w in plan.writes if w.source_row == 3]
        self.assertTrue(release[0].inferred_key)
        self.assertEqual(release[1].register, 0x25)
        terminal = [w for w in plan.writes if w.vgmticks == source.source_end]
        self.assertTrue(terminal[0].inferred_key)
        self.assertEqual(terminal[1].reason, 'terminal mute at VGM end')
        dropped = {w.write_id for w in source.writes if w.register == 8 and
                   (w.data & 0x78 or w.reason == 'terminal key off')}
        linked = [w for w in plan.writes if w.baseline_write_id is not None]
        self.assertEqual({w.baseline_write_id for w in linked},
                         {w.write_id for w in source.writes} - dropped)
        for w in linked:
            original = source.writes[w.baseline_write_id]
            self.assertEqual((w.register, w.data, w.vgmticks, w.mdx_tick, w.source_row),
                             (original.register, original.data, original.vgmticks,
                              original.mdx_tick, original.source_row))
        with self.assertRaises(FrozenInstanceError):
            plan.writes[0].data = 1

    def test_zero_target_duration_and_same_sample_gate_edges_survive(self):
        source = baseline([psg(vgmticks_end=0), psg(volume=0, vgmticks_end=1),
                           psg(vgmticks=1, vgmticks_end=2),
                           psg(volume=0, vgmticks=2, vgmticks_end=441)])
        plan = build_performance(source)
        self.assertEqual([r['inferred_transition'] for r in plan.rows],
                         ['attack', 'release', 'attack', 'release'])
        inferred = [w for w in plan.writes if w.inferred_key]
        self.assertEqual([w.vgmticks for w in inferred], [0, 0, 1, 2])
        self.assertEqual([w.mdx_tick for w in inferred], [0, 0, 0, 0])
        self.assertEqual([bool(w.data & 0x78) for w in inferred], [True, False, True, False])
        trajectory = build_target_trajectory(plan.scheduled_writes(), end_tick=plan.end_tick,
                                             source_end_vgmticks=plan.source_end)
        self.assertEqual(sum(bool(e.rising_mask) for e in trajectory.events), 2)
        self.assertEqual(sum(bool(e.falling_mask) for e in trajectory.events), 2)
        self.assertTrue(all(not i.state.key_mask for i in trajectory.intervals))

    def test_silent_parts_create_no_oscillator_or_gate(self):
        plan = build_performance(baseline([psg(volume=0)]))
        self.assertFalse(plan.writes)
        self.assertEqual(plan.rows[0]['inferred_transition'], 'rest')
        self.assertFalse(plan.settings['phase_preserved'])
        self.assertFalse(plan.settings['source_key_observed'])

    def test_all_eight_channels_unique_ids_and_independent_gates(self):
        wave = bytes([100] * 16 + [156] * 16).hex()
        scc = SccSegment('v', 0., 0, 0, 1, 200, 15, 4, 'c', 0, (),
                         200, 1, 0, 0, wave, 1, vgmticks=0, vgmticks_end=441)
        source = project({ch: [replace(psg(), ch=ch)] for ch in range(3)},
                         SccAnalysis({ch: [replace(scc, ch=ch)] for ch in range(5)}, (wave,)),
                         psg_clock=1789772, scc_clock=1789772,
                         end_vgmticks=441, psg_model='fm')
        plan = build_performance(source)
        self.assertEqual(len({w.write_id for w in plan.writes}), len(plan.writes))
        for ch in range(8):
            keys = [w for w in plan.writes if w.target_ch == ch and w.inferred_key]
            self.assertEqual([w.data for w in keys], [0x78 | ch, ch])
            self.assertTrue(all(w.source_chip == ('scc' if ch < 5 else 'psg') for w in keys))
        trajectory = build_target_trajectory(plan.scheduled_writes(), end_tick=plan.end_tick,
                                             source_end_vgmticks=plan.source_end)
        self.assertEqual(len(trajectory.intervals), 8)
        self.assertTrue(all(i.state.key_mask == 15 for i in trajectory.intervals))

    def test_dumps_keep_original_row_links_and_inference_explicit(self):
        plan = build_performance(baseline([psg(), psg(volume=0, vgmticks=441, vgmticks_end=882)]))
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            plan.dump(out, 'sample')
            self.assertEqual(len(list(out.iterdir())), 5)
            with (out / 'sample.opm_performance.csv').open(encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([r['source_row'] for r in rows], ['0', '1'])
            self.assertEqual([r['key_origin'] for r in rows], ['musical_attack_inferred'] * 2)
            self.assertEqual(json.loads(rows[0]['baseline_write_ids']),
                             json.loads(rows[0]['target_write_ids']))
            with (out / 'sample.opm_performance.state.csv').open(encoding='utf-8') as stream:
                states = list(csv.DictReader(stream))
            self.assertTrue(all(r['source_key_observed'] == 'False' for r in states))
            report = json.loads((out / 'sample.opm_performance.json').read_text())
            self.assertEqual(report['inferred_attacks'], 1)
            self.assertEqual(report['inferred_releases'], 1)
            self.assertFalse(report['phase_preserved'])


if __name__ == '__main__':
    unittest.main()
