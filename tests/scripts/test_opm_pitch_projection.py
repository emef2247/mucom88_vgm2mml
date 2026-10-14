"""Public synthetic evidence for clock-aware Segment -> MDX pitch projection."""
import csv
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from opm_conversion import convert
from opm_mdx import project_segments
from opm_pitch_projection import corrected_pitch, pitch_units, target_segments
from opm_roundtrip import compare
from opm_mdx_structure import build_structure, note_spelling
from opm_mdx_music import build_music
from opm_target_pruning import omit_short_gates
from test_opm_mdx import analyze, wait, write
from test_opm_reader import vgm


def tone():
    return write(0x20, 0xc7) + b''.join(write(base + bank * 8, value)
        for bank in range(4)
        for base, value in ((0x40, 1), (0x60, 16), (0x80, 31),
                            (0xa0, 0), (0xc0, 0), (0xe0, 15)))


def melody():
    return tone() + write(0x28, 0x4a) + write(8, 120) + wait(4410) + write(8, 0) + wait(4410)


class PitchProjectionTests(unittest.TestCase):
    def plan(self, root, commands, clock=3579580, policy='clamp'):
        path = root / 'clock.vgm'
        path.write_bytes(vgm(commands, clock))
        analysis = analyze(path, root / 'trace')
        projection = project_segments(analysis.segments, end_vgmticks=analysis.source_end_vgmticks,
                                      pitch_policy=policy)
        return path, analysis, projection

    def test_frequency_ratio_rounding_aliases_and_octave_carry(self):
        for clock in (2000000, 3579580, 8000000):
            for kc in (0, 3, 0x1e, 0x20, 0x4a, 0x7f):
                for kf in (0, 3, 128, 252, 255):
                    source = pitch_units(kc, kf)
                    expected = source + math.floor(768 * math.log2(clock / 4000000) + 0.5)
                    if not 0 <= expected <= 6207:
                        with self.assertRaisesRegex(ValueError, 'outside'):
                            corrected_pitch(kc, kf, clock)
                        continue
                    a, b, error = corrected_pitch(kc, kf, clock)
                    self.assertEqual(pitch_units(a, b), expected)
                    # Independent exponential frequency-ratio check.
                    ratio = (4000000 / clock) * 2 ** ((pitch_units(a, b) - source) / 768)
                    self.assertAlmostEqual(1200 * math.log2(ratio), error, places=10)
                    self.assertLessEqual(abs(error), 0.78125001)
        self.assertEqual(corrected_pitch(0x4a, 128, 2000000)[:2], (0x3a, 128))

    def test_kc_only_kf_only_and_pair_order_leave_source_segments_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, source, plan = self.plan(root, write(0x28, 0x4a) + write(8, 120) + wait(101)
                + write(0x30, 252) + wait(101) + write(0x30, 0) + write(8, 0))
            before = source.segments
            self.assertEqual([(w.register, w.data) for w in plan.writes[:3]],
                             [(0x28, 0x48), (0x30, 20), (8, 120)])
            pair = [w for w in plan.writes if w.source_register == 0x30 and w.source_data == 252]
            self.assertEqual([(w.register, w.data, w.target_sequence) for w in pair],
                             [(0x28, 0x49, 0), (0x30, 16, 1)])
            target = target_segments(plan, source.segments)
            on = next(s for s in target if s.rising_mask)
            self.assertEqual((on.state.kc_raw, on.state.kf_raw), (0x48, 20))
            self.assertIsNone(next(s for s in source.segments if s.rising_mask).state.kf_raw)
            self.assertEqual(source.segments, before)
            self.assertTrue(plan.pitch_evidence[0]['assumed_reset_kf'])

    def test_all_notations_and_layouts_use_one_target_pitch_and_emit_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, source, plan = self.plan(root, melody())
            desired = note_spelling(next(s.state for s in target_segments(plan, source.segments) if s.rising_mask))
            for renderer in (build_music, build_structure):
                score = renderer(plan, source.segments, loops=False)
                self.assertGreater(score.summary()['note_units'], 0)
                self.assertIn(f'o{desired[0]}', score.text)
                self.assertIn(f'D{desired[2]}', score.text)
            for notation, layout in (('structured', 'channels'), ('legacy', 'channels'),
                                      ('registers', 'channels'), ('registers', 'conductor')):
                folder = root / (notation + layout)
                _, returned_source, projected = convert(path, folder, notation=notation,
                    track_layout=layout, dump_passes=True, normalize_lengths=False)
                self.assertEqual(projected.writes, plan.writes)
                self.assertEqual(returned_source.segments, source.segments)
                with (folder / 'clock.mdx.pitch.csv').open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(rows[0]['source_clock_hz'], '3579580')
                self.assertEqual(rows[0]['target_kc'], '72')
                report = json.loads((folder / 'clock.mdx.timing.json').read_text())
                self.assertTrue(report['pitch_projection']['applied'])
            normalized = convert(path, root / 'normalized', dump_passes=True)[2]
            self.assertEqual([(w.register, w.data) for w in normalized.writes],
                             [(w.register, w.data) for w in plan.writes])

    def test_identity_clock_keeps_raw_reserved_bits_and_no_added_controls(self):
        commands = write(0x28, 0xce) + write(0x30, 255) + write(8, 120) + wait(101)
        with tempfile.TemporaryDirectory() as tmp:
            _, source, plan = self.plan(Path(tmp), commands, 4000000)
            self.assertEqual([(w.register, w.data) for w in plan.writes],
                             [(0x28, 0xce), (0x30, 255), (8, 120)])
            self.assertEqual(target_segments(plan, source.segments), source.segments)
            self.assertFalse(plan.timing_report()['pitch_projection']['applied'])

    def test_unknown_pitch_initialized_before_first_keyon_at_higher_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, plan = self.plan(Path(tmp), write(8, 120) + wait(101), 8000000)
            self.assertEqual([(w.register, w.data) for w in plan.writes],
                             [(0x28, 0x10), (0x30, 0), (8, 120)])
            self.assertTrue(plan.pitch_evidence[0]['assumed_reset_kc'])

    def test_out_of_range_initialization_deferred_but_attack_and_released_tail_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, plan = self.plan(root, write(0x28, 0) + write(0x28, 0x4a) + write(8, 120) + wait(101), policy='error')
            self.assertEqual(plan.pitch_evidence[0]['status'], 'deferred_before_first_keyon')
            for body in (write(0x28, 0) + write(8, 120) + wait(101),
                         melody() + write(0x28, 0)):
                with self.assertRaisesRegex(ValueError, 'outside.*event'):
                    self.plan(root, body, policy='error')

    def test_explicit_clamp_reports_actual_loss_and_strict_policy_rejects_all_notations(self):
        for kc, clock, expected in ((0, 3579580, 0), (127, 8000000, 6207)):
            a, b, error = corrected_pitch(kc, 252 if kc else 0, clock, policy='clamp')
            self.assertEqual(pitch_units(a, b), expected)
            ratio = (4000000 / clock) * 2 ** ((pitch_units(a, b)-pitch_units(kc, 252 if kc else 0))/768)
            self.assertAlmostEqual(1200*math.log2(ratio), error)
            self.assertGreater(abs(error), 100)
        with self.assertRaisesRegex(ValueError, 'positive'):
            corrected_pitch(0, 0, 0, policy='clamp')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A long low pitch while played cannot be dismissed as silent setup.
            path, source, _ = self.plan(root, melody() + write(0x28, 0) + write(8, 120) + wait(44100))
            for notation, layout in (('structured', 'channels'), ('legacy', 'channels'),
                                    ('registers', 'channels'), ('registers', 'conductor')):
                _, returned, plan = convert(path, root / (notation+layout), notation=notation,
                    track_layout=layout, dump_passes=True, normalize_lengths=False)
                report = plan.timing_report()['pitch_projection']
                self.assertEqual(returned, source)
                self.assertGreater(report['clamped_events'], 0)
                self.assertGreater(report['max_abs_clamped_error_cents'], 190)
                self.assertTrue(report['known_pitch_loss'])
                with self.assertRaisesRegex(ValueError, 'outside.*event'):
                    convert(path, root / ('strict'+notation+layout), notation=notation,
                            track_layout=layout, pitch_policy='error', normalize_lengths=False)

    def test_short_gate_omission_keeps_injected_pitch_for_next_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, source, plan = self.plan(Path(tmp), write(8, 120) + wait(100)
                + write(8, 0) + wait(500) + write(8, 120) + wait(1000) + write(8, 0), 8000000)
            pruned, _ = omit_short_gates(source.events, plan)
            self.assertEqual([(w.register, w.data) for w in pruned.writes[:2]], [(0x28, 0x10), (0x30, 0)])
            target = target_segments(pruned, source.segments)
            self.assertEqual(next(s.state.kc_raw for s in reversed(target) if s.rising_mask), 0x10)
            from opm_note_normalization import normalize_projection
            _, report, _ = normalize_projection(source.segments, plan, output_short_note_policy=True,
                                                 source_events=source.events)
            self.assertEqual(report['anchor_counts']['opm'], 1)

    def test_register_roundtrip_compares_completed_pairs_and_detects_wrong_target_pitch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, source, plan = self.plan(root, melody())
            body = bytearray(); cursor = 0
            for control in plan.writes:
                body += wait(control.projected_vgmticks - cursor)
                body += write(control.register, control.data)
                cursor = control.projected_vgmticks
            body += wait(plan.end_projected_vgmticks - cursor)
            path = root / 'returned.vgm'; path.write_bytes(vgm(body))
            actual = analyze(path, root / 'returned')
            self.assertTrue(compare(plan, source.segments, actual, initialization=[])['passed'])
            wrong = replace(plan, writes=tuple(replace(w, data=w.data ^ 4) if w.register == 0x30 else w
                                               for w in plan.writes))
            self.assertFalse(compare(wrong, source.segments, actual, initialization=[])['passed'])


if __name__ == '__main__':
    unittest.main()
