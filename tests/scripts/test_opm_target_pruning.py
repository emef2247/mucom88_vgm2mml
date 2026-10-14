"""Source-time gate omission, distinct from target-clock quantization."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from opm_conversion import convert
from opm_mdx import project_segments
from opm_note_normalization import normalize_projection
from opm_output_normalization import bounded_ticks
from opm_mdx import projected_samples
from opm_target_pruning import omit_short_gates
from test_opm_mdx import analyze, write, wait
from test_opm_reader import vgm
from conversion_config import normalization_samples


def setup():
    body = write(0x20, 0xc7) + write(0x28, 0x40) + write(0x30, 0) + write(0x38, 0)
    for slot in range(4):
        for base, value in ((0x40, 1), (0x60, 20), (0x80, 31), (0xa0, 0), (0xc0, 0), (0xe0, 15)):
            body += write(base + slot * 8, value)
    return body


class ShortOutputGateTests(unittest.TestCase):
    def test_terminal_unclosed_gates_get_bounded_positive_ticks_without_omission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            end = 748139
            prefix = wait(65535) * ((end-11)//65535) + wait((end-11)%65535)
            source, analysis, before = self.fixture(root, prefix + write(8, 121)
                + wait(2) + write(0x28, 0x40)  # nonchanging source event
                + wait(2) + write(8, 123) + wait(4) + write(8, 125) + wait(3))
            _, returned, after = convert(source, root / 'normalized', dump_passes=True)
            report = json.loads((root / 'normalized/gates.mdx.normalization.json').read_text())
            self.assertEqual(returned, analysis)
            self.assertEqual(report['status'], 'applied')
            self.assertEqual(report['boundary_allocation'], 'bounded_ordered')
            self.assertEqual(report['short_note_policy']['omitted_gate_count'], 0)
            self.assertGreater(after.sample_multiplier, 1)
            self.assertLessEqual(report['max_abs_correction_samples'], 352)
            self.assertEqual(after.mdx_tick(end), after.end_mdx_tick)
            for event in analysis.events:
                self.assertIn(event.vgmticks, dict(after.boundary_ticks))
            event_ticks = [after.mdx_tick(e.vgmticks) for e in analysis.events]
            self.assertEqual(event_ticks, sorted(event_ticks))
            for w in after.writes:
                self.assertEqual(after.mdx_tick(w.source_vgmticks), w.mdx_tick)
                if w.register == 8:
                    self.assertLess(w.mdx_tick, after.end_mdx_tick)
            self.assertEqual([w.data for w in before.writes if w.register == 8],
                             [w.data for w in after.writes if w.register == 8])
            from opm_mdx_structure import compare_hybrid
            replay, cursor = b'', 0
            for control in after.writes:
                gap = control.projected_vgmticks-cursor
                replay += wait(65535)*(gap//65535) + wait(gap%65535) + write(control.register, control.data)
                cursor = control.projected_vgmticks
            replay += wait(after.end_projected_vgmticks-cursor)
            replay_path = root / 'replay.vgm'; replay_path.write_bytes(vgm(replay))
            returned = analyze(replay_path, root / 'replay')
            check = compare_hybrid(after, analysis, returned, initialization=[],
                                   source_timing_tolerance_samples=352)
            self.assertTrue(check['passed'], check)

    def test_ordered_allocation_feasibility_matches_exhaustive_integer_domains(self):
        from itertools import product
        for times in ([0, 1, 3], [0, 4, 8, 14], [0, 10, 11, 20]):
            protected = [dict(start_vgmticks=a, end_vgmticks=b) for a, b in zip(times, times[1:])]
            for bound in (0, 3, 8, 12):
                feasible = any(all(abs(projected_samples(tick)-t) <= bound for t, tick in zip(times, ticks))
                               and all(a < b for a, b in zip(ticks, ticks[1:]))
                               for ticks in product([0], *[range(5) for _ in times[1:]]))
                result = bounded_ticks(times, protected, 1, bound)
                self.assertEqual(result is not None, feasible, (times, bound))
                if result is not None:
                    self.assertEqual(result[0], 0)
                    self.assertTrue(all(abs(projected_samples(result[t])-t) <= bound for t in times))
                    self.assertTrue(all(result[a] < result[b] for a, b in zip(times, times[1:])))

    def test_configurable_thresholds_use_original_samples_and_preserve_off_mode(self):
        for milliseconds, threshold in ((4, 176), (8, 352), (16, 705)):
            with self.subTest(milliseconds=milliseconds), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, analysis, _ = self.fixture(root, wait(451) + write(8, 120)
                    + wait(threshold) + write(8, 0) + wait(882) + write(8, 120)
                    + wait(threshold + 1) + write(8, 0) + wait(882))
                _, returned, after = convert(source, root / 'on', normalize_lengths=True,
                    normalization_ms=milliseconds, dump_passes=True)
                report = json.loads((root / 'on/gates.mdx.normalization.json').read_text())
                self.assertEqual(report['normalization_ms'], milliseconds)
                self.assertEqual(report['correction_bound_samples'], threshold)
                self.assertEqual(report['short_note_policy']['omitted_gate_count'], 1)
                self.assertEqual(returned, analysis)
                self.assertEqual(len([w for w in after.writes if w.register == 8]), 2)
                small, _, _ = convert(source, root / 'off4', normalize_lengths=False, normalization_ms=4)
                large, _, _ = convert(source, root / 'off16', normalize_lengths=False, normalization_ms=16)
                self.assertEqual(small.read_bytes(), large.read_bytes())

    def test_normalization_parameter_rejects_invalid_and_subsample_values(self):
        for value in (0, -1, float('nan'), float('inf'), float('-inf'), .001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalization_samples(value)
        self.assertEqual(normalization_samples(4), 176)
        self.assertEqual(normalization_samples(16), 705)
        self.assertGreater(normalization_samples(1e308), 0)

    def fixture(self, folder, body):
        source = folder / 'gates.vgm'
        source.write_bytes(vgm(setup() + body))
        analysis = analyze(source, folder / 'trace')
        projection = project_segments(analysis.segments, end_vgmticks=analysis.source_end_vgmticks)
        return source, analysis, projection

    def test_352_omitted_353_retained_and_long_note_control_slice_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, analysis, before = self.fixture(root, wait(441) + write(8, 120) + wait(352) + write(8, 0)
                + wait(441) + write(8, 120) + wait(353) + write(8, 0)
                + wait(441) + write(8, 120) + wait(50) + write(0x28, 0x45)
                + wait(8770) + write(8, 0) + wait(441))
            after, report = omit_short_gates(analysis.events, before)
            self.assertEqual(report['omitted_gate_count'], 1)
            self.assertEqual(report['omitted_duration_samples'], 352)
            self.assertEqual([w.data for w in after.writes if w.register == 8], [120, 0, 120, 0])
            self.assertIn((0x28, 0x45), [(w.register, w.data) for w in after.writes])
            self.assertEqual(after.source_end_vgmticks, before.source_end_vgmticks)
            self.assertEqual(after.end_mdx_tick, before.end_mdx_tick)
            self.assertEqual([w for w in after.writes if w.register != 8],
                             [w for w in before.writes if w.register != 8])

    def test_partial_retrigger_redundant_and_unclosed_operations_not_short_notes(self):
        bodies = (write(8, 120) + wait(50) + write(8, 8) + wait(50) + write(8, 0),
                  write(8, 120) + wait(50) + write(8, 120) + wait(50) + write(8, 0),
                  write(8, 120) + wait(50))
        for body in bodies:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temp:
                _, analysis, before = self.fixture(Path(temp), body)
                after, report = omit_short_gates(analysis.events, before)
                self.assertEqual(report['omitted_gate_count'], 0)
                self.assertEqual(after, before)

    def test_omission_before_quantization_keeps_original_source_and_total_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, original, _ = self.fixture(root, wait(441) + write(8, 120)
                                               + wait(352) + write(8, 0) + wait(882))
            source_bytes = source.read_bytes()
            _, off, p = convert(source, root / 'off', normalize_lengths=False, dump_passes=True)
            mml, on, q = convert(source, root / 'on', normalize_lengths=True, dump_passes=True)
            report = json.loads((root / 'on/gates.mdx.normalization.json').read_text())
            self.assertEqual(original, on)
            self.assertEqual(on, off)
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(report['short_note_policy']['omitted_gate_count'], 1)
            self.assertFalse(any(w.register == 8 for w in q.writes))
            self.assertTrue(any(w.register == 8 for w in p.writes))
            self.assertEqual(q.source_end_vgmticks, p.source_end_vgmticks)
            self.assertLessEqual(abs(q.end_projected_vgmticks-p.source_end_vgmticks), 352)
            self.assertGreater(q.sample_multiplier, 1)
            self.assertIn('r', mml.read_text())
            self.assertNotIn('y8,120', mml.read_text())
            self.assertTrue((root / 'on/gates.mdx.normalization.csv').is_file())

    def test_side_effect_pulse_preserves_positive_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            _, analysis, before = self.fixture(Path(temp), write(8, 120) + wait(1000) + write(8, 0)
                + wait(40) + write(8, 120) + wait(1000) + write(8, 0)
                + write(1, 2) + wait(20) + write(1, 0) + wait(441))
            after, report, _ = normalize_projection(analysis.segments, before,
                output_short_note_policy=True, source_events=analysis.events)
            keys = [w.mdx_tick for w in after.writes if w.register == 8]
            pulse = [w.mdx_tick for w in after.writes if w.register == 1]
            self.assertLess(keys[0], keys[1])
            self.assertLessEqual(keys[1], keys[2])
            self.assertLess(keys[2], keys[3])
            self.assertLess(pulse[0], pulse[1])
            self.assertEqual(report['collapsed_protected_intervals'], 0)

    def test_short_rest_coalesces_but_both_key_requests_survive(self):
        with tempfile.TemporaryDirectory() as temp:
            _, analysis, before = self.fixture(Path(temp), write(8, 120) + wait(1000) + write(8, 0)
                + wait(12) + write(8, 120) + wait(1000) + write(8, 0) + wait(441))
            after, report, _ = normalize_projection(analysis.segments, before,
                output_short_note_policy=True, source_events=analysis.events)
            keys = [w for w in after.writes if w.register == 8]
            self.assertEqual([w.data for w in keys], [120, 0, 120, 0])
            self.assertLess(keys[0].mdx_tick, keys[1].mdx_tick)
            self.assertEqual(keys[1].mdx_tick, keys[2].mdx_tick)
            self.assertLess(keys[2].mdx_tick, keys[3].mdx_tick)
            self.assertEqual(report['coalesced_rest_duration_samples'], 12)
            self.assertTrue(report['intentional_timing_loss'])

    def test_original_mapped_352_353_cutoff_precedes_intermediate_rounding(self):
        for original_duration, intermediate_duration, omitted in ((352, 361, 1), (353, 350, 0)):
            with self.subTest(duration=original_duration), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, analysis, _ = self.fixture(root, wait(451) + write(8, 120)
                    + wait(intermediate_duration) + write(8, 0) + wait(882))
                on, off = [e for e in analysis.events if e.register == 8]
                mapping = {on.source_event_id: 447, off.source_event_id: 447 + original_duration}
                _, unchanged, result = convert(source, root / 'out', normalize_lengths=True,
                    normalization_source_times=mapping, dump_passes=True)
                report = json.loads((root / 'out/gates.mdx.normalization.json').read_text())
                self.assertEqual(report['short_note_policy']['omitted_gate_count'], omitted)
                self.assertEqual(len([w for w in result.writes if w.register == 8]), 2 * (1 - omitted))
                self.assertEqual(unchanged, analysis)
                self.assertEqual(report['short_note_policy']['timing_basis'], 'original_source_before_normalization')

    def test_positive_song_with_all_notes_omitted_keeps_positive_end(self):
        with tempfile.TemporaryDirectory() as temp:
            _, analysis, before = self.fixture(Path(temp), write(8, 120) + wait(100) + write(8, 0))
            after, report, _ = normalize_projection(analysis.segments, before,
                output_short_note_policy=True, source_events=analysis.events)
            self.assertEqual(report['short_note_policy']['omitted_gate_count'], 1)
            self.assertGreater(after.end_mdx_tick, 0)
            self.assertFalse(any(w.register == 8 for w in after.writes))

    def test_loop_crossing_short_gate_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            _, analysis, before = self.fixture(Path(temp), wait(400) + write(8, 120)
                                               + wait(352) + write(8, 0) + wait(1000))
            after, report, _ = normalize_projection(analysis.segments, before,
                output_short_note_policy=True, source_events=analysis.events,
                loop_metadata=dict(status='valid', loop_offset=1, loop_start_samples=500,
                                   decoded_end_samples=before.source_end_vgmticks))
            self.assertEqual(report['short_note_policy']['omitted_gate_count'], 0)
            self.assertEqual(len([w for w in after.writes if w.register == 8]), 2)


if __name__ == '__main__':
    unittest.main()
