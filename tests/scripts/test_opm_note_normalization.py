"""Bounded MDX correction, shared timing inference and source-preserving notation."""
import csv
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from mdx_duration import duration_spelling, timed
from opm_conversion import convert
from opm_mdx import project_segments
from opm_mdx_music import build_music, infer_clock
from opm_note_normalization import normalize_projection
from source_loop_plan import expanded_tokens
from test_opm_mdx import analyze, write, wait
from test_opm_reader import vgm


def jittered_source(path, *, tiny_control=False):
    # Original complete tone, 4 MHz @t216 lattice, irregular source-sample jitter.
    setup = write(0x20, 0xc7) + write(0x28, 0x40) + write(0x30, 0) + write(0x38, 0)
    for slot in range(4):
        for base, value in ((0x40, 1), (0x60, 20), (0x80, 31), (0xa0, 0), (0xc0, 0), (0xe0, 15)):
            setup += write(base + slot * 8, value)
    if tiny_control:
        setup += wait(10) + write(0x38, 1)
    body, cursor = setup, 10 if tiny_control else 0
    for index in range(40):
        start = round((index + 1) * 5419.008) + (index % 5 - 2) * 25
        end = start + round(9 * 451.584) + (index % 3 - 1) * 17
        body += wait(start - cursor) + write(0x28, (0x40, 0x42, 0x45, 0x49)[index % 4])
        body += write(8, 120) + wait(end - start) + write(8, 0)
        cursor = end
    body += wait(1355)
    path.write_bytes(vgm(body))


def body_duration(text):
    total = Fraction(0)
    for token in text.split():
        if token == '&':
            continue
        match = re.fullmatch(r'[a-gr][+#-]?(?:%(\d+)|(\d+)(\.*))', token)
        if match[1]:
            total += int(match[1])
        else:
            dots = len(match[3])
            total += Fraction(192, int(match[2])) * Fraction((1 << (dots + 1)) - 1, 1 << dots)
    return total


class NativeLengthCorrectionTests(unittest.TestCase):
    def test_long_note_values_keep_exact_duration_and_one_tied_attack(self):
        self.assertEqual(duration_spelling(24), '8')
        self.assertEqual(duration_spelling(18), '16.')
        self.assertEqual(duration_spelling(16), '12')
        self.assertEqual(duration_spelling(2), '96')
        self.assertEqual(duration_spelling(13), '%13')
        self.assertEqual(timed('a', 384), 'a1 & a1')
        self.assertEqual(timed('a', 240), 'a1 & a4')
        for duration in (1, 13, 192, 240, 256, 257, 384, 1024, 65536):
            text = timed('a+', duration, held=True)
            self.assertEqual(body_duration(text), duration)
            self.assertTrue(text.endswith('&'))
            self.assertEqual(text.split().count('&'), sum(t != '&' for t in text.split()))
            self.assertEqual(body_duration(timed('r', duration)), duration)

    def test_correction_preserves_source_and_unlocks_exact_repeated_units(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / 'jitter.vgm'
            jittered_source(source)
            analysis = analyze(source, folder / 'trace')
            original = tuple(analysis.segments)
            clock = infer_clock(original, analysis.source_end_vgmticks)
            before = project_segments(original, end_vgmticks=analysis.source_end_vgmticks,
                                      sample_multiplier=clock['chosen']['multiplier'])
            after, report, rows = normalize_projection(original, before)
            self.assertEqual(report['status'], 'applied', report)
            self.assertEqual(after.sample_multiplier, 40)
            self.assertEqual(original, analysis.segments)
            self.assertEqual([(w.source_event_id, w.source_vgmticks, w.register, w.data) for w in before.writes],
                             [(w.source_event_id, w.source_vgmticks, w.register, w.data) for w in after.writes])
            self.assertEqual(report['collapsed_positive_intervals'], 0)
            self.assertLessEqual(report['max_abs_correction_samples'], report['correction_bound_samples'])
            music_before, music_after = build_music(before, original), build_music(after, original)
            for track, units in music_after.units.items():
                commands = [u.command for u in units]
                folded, _ = music_after.plans[track].render(commands)
                self.assertEqual(expanded_tokens(folded), expanded_tokens(' '.join(commands)))
            old_keys = {u.key for u in music_before.units['A'] if u.kind == 'note'}
            new_keys = {u.key for u in music_after.units['A'] if u.kind == 'note'}
            self.assertLess(len(new_keys), len(old_keys))
            self.assertLess(len(music_after.text), len(music_before.text))
            self.assertTrue(rows)

    def test_positive_control_and_declared_loop_intervals_cannot_collapse(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for tiny in (False, True):
                source = folder / f'jitter{tiny}.vgm'
                jittered_source(source, tiny_control=tiny)
                analysis = analyze(source, folder / f'trace{tiny}')
                before = project_segments(analysis.segments, end_vgmticks=analysis.source_end_vgmticks)
                loop = {'loop_offset': 1, 'status': 'valid',
                        'loop_start_samples': before.source_end_vgmticks - 1,
                        'decoded_end_samples': before.source_end_vgmticks}
                after, report, rows = normalize_projection(analysis.segments, before,
                                                          loop_metadata=None if tiny else loop)
                self.assertIs(after, before)
                self.assertEqual(report['status'], 'unchanged')
                self.assertIn('collapse', report['reason'])
                self.assertTrue(report['first_collisions'])
                self.assertTrue(all(r['projection_status'] == 'unchanged' for r in rows))

    def test_sparse_clock_uses_bounded_output_policy_and_replaces_stale_evidence(self):
        fixture = ROOT / 'tests/fixtures/public/opm/mdx_decompiler/tie_controls/tie_controls.vgm'
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for suffix in ('.mdx.normalization.csv', '.mdx.before.normalize.mml'):
                (folder / ('tie_controls' + suffix)).write_text('old applied evidence')
            mml, _, _ = convert(fixture, folder, normalize_lengths=True, dump_passes=True)
            report = json.loads((folder / 'tie_controls.mdx.normalization.json').read_text())
            self.assertEqual(report['status'], 'applied')
            self.assertEqual(report['clock_selection'], 'bounded_target_quantization')
            self.assertIsNone(report['fitted_clock'])
            self.assertLessEqual(report['max_abs_correction_samples'], 352)
            self.assertTrue((folder / 'tie_controls.mdx.normalization.csv').exists())
            self.assertNotEqual((folder / 'tie_controls.mdx.before.normalize.mml').read_text(),
                                'old applied evidence')
            self.assertIn('/* Track A */', mml.read_text())

    def test_dump_retains_identical_native_cells_and_records_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / 'jitter.vgm'
            jittered_source(source)
            before, a, p = convert(source, folder / 'before', dump_passes=True, normalize_lengths=False)
            after, b, q = convert(source, folder / 'after', dump_passes=True, normalize_lengths=True)
            self.assertEqual(a.segments, b.segments)
            for suffix in ('_trace.opm_regs.csv', '_trace.opm.csv'):
                self.assertEqual((before.parent / ('jitter' + suffix)).read_bytes(),
                                 (after.parent / ('jitter' + suffix)).read_bytes())
            def native_cells(path):
                with path.open() as stream:
                    return [{k: v for k, v in r.items() if k != 'mdx_music_unit_ids'} for r in csv.DictReader(stream)]
            self.assertEqual(native_cells(before.parent / 'jitter.opm.segments.csv'),
                             native_cells(after.parent / 'jitter.opm.segments.csv'))
            self.assertEqual(before.read_bytes(), (after.parent / 'jitter.mdx.before.normalize.mml').read_bytes())
            self.assertTrue((after.parent / 'jitter.mdx.normalization.csv').exists())
            self.assertIn('a1', timed('a', 384))

    def test_track_comments_begin_each_native_track_in_all_notations(self):
        fixture = ROOT / 'tests/fixtures/public/opm/from_mdx/eight_channels_pan/eight_channels_pan.vgm'
        with tempfile.TemporaryDirectory() as temp:
            for notation in ('structured', 'legacy', 'registers'):
                mml, _, _ = convert(fixture, Path(temp) / notation, notation=notation)
                text = mml.read_text()
                for track in 'ABCDEFGH':
                    self.assertEqual(text.count(f'/* Track {track} */'), 1)
                    self.assertRegex(text, rf'/\* Track {track} \*/\n(?:;[^\n]*\n)*{track} ')

    def test_cli_accepts_opt_in_correction_and_rejects_other_notation(self):
        fixture = ROOT / 'tests/fixtures/public/opm/mdx_decompiler/tie_controls/tie_controls.vgm'
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, str(ROOT / 'vgm2mml.py'), str(fixture), '--target', 'mdx', '--outdir', temp, '--normalize-lengths']
            run = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('Note normalization: applied', run.stdout)
            run = subprocess.run(command + ['--notation', 'registers'], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 2)
            self.assertIn('requires --notation structured', run.stderr)


if __name__ == '__main__':
    unittest.main()
