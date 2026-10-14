"""Check authored controls against the independently parsed public FMSTATE."""
import copy
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

from generate_fmstate_clock_controls import (
    CLOCKS, FIXTURE, ROOT, generate, performance, repeated_expectation, score, validate,
)
from mdx_reference_expectations import read_expectations


class AuthoredClockTests(unittest.TestCase):
    def setUp(self):
        self.baseline = read_expectations(FIXTURE / 'FMSTATE.MDX')
        self.text = (FIXTURE / 'FMSTATE.MML').read_text(encoding='utf-8')

    def test_phrase_preserves_held_state_changes_and_finite_repeats(self):
        for clock in CLOCKS:
            text = score(clock, self.text)
            ticks = 12 * 4096 // clock
            self.assertIn(f'c%{ticks}&v8p1c%{ticks}&p2c%{ticks}', text)
            self.assertIn(f'[c%{ticks} ', text)
            self.assertIn(']2 ', text)
            self.assertIn(']8\nP ', text)
            self.assertIn('#pcmfile "PUBPCM.PDX"', text)
            self.assertEqual(text.count('@0 = {'), 1)
            self.assertEqual(text.count('@1 = {'), 1)
            self.assertNotIn(' y', text)
        with self.assertRaises(ValueError):
            score(1000, self.text)

    def test_oracle_retains_in_note_changes_and_attack_boundaries(self):
        oracle = repeated_expectation(self.baseline)
        self.assertEqual(len(oracle['attacks']), 48)
        self.assertEqual(len(oracle['releases']), 48)
        self.assertEqual(oracle['releases'][0], Fraction(36 * 4096, 1000000))
        # The three tied c notes have one attack but three separate state intervals.
        first = oracle['intervals'][:3]
        self.assertEqual([row[2][3:] for row in first], [(12, 3), (8, 1), (8, 2)])
        modified = copy.deepcopy(self.baseline['tracks'][0]['events'])
        modified[1]['pan'] = 3
        self.assertNotEqual(performance(modified, 4096),
                            performance(self.baseline['tracks'][0]['events'], 4096))


GENERATOR = ROOT / 'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator'
MXC = ROOT / 'outputs/research/mxc_tools/extracted/mxc.x'
RUN68 = ROOT / 'outputs/research/run68x/build/run68'


@unittest.skipUnless(all(path.is_file() for path in (GENERATOR, MXC, RUN68)),
                     'native public MXC toolchain not available')
class CompiledClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.outdir = Path(cls.temp.name)
        cls.report = generate(cls.outdir, GENERATOR)
        cls.baseline = read_expectations(FIXTURE / 'FMSTATE.MDX')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_baseline_and_empty_pdx_isolation(self):
        for name in ('FMSTATE.MML', 'FMSTATE.MDX', 'PUBPCM.PDX'):
            self.assertEqual((self.outdir / name).read_bytes(), (FIXTURE / name).read_bytes())
        isolated = read_expectations(self.outdir / 'FMONLY.MDX')
        self.assertEqual(isolated['pdx_name'], '')
        self.assertEqual((self.outdir / 'FMONLY.MDX').read_bytes()[isolated['data_base_file_offset']:],
                         (FIXTURE / 'FMSTATE.MDX').read_bytes()[self.baseline['data_base_file_offset']:])

    def test_clock_variants_match_reference_phrase_and_reject_corruption(self):
        for clock in CLOCKS:
            result = read_expectations(self.outdir / f'FM{clock:04d}.MDX')
            validate(result, clock, self.baseline)
            self.assertEqual(result['tracks'][0]['duration_ticks'] * clock, 6291456)
        damaged = read_expectations(self.outdir / 'FM8192.MDX')
        damaged['tracks'][0]['events'][1]['volume_encoded'] += 1
        with self.assertRaises(AssertionError):
            validate(damaged, 8192, self.baseline)
        damaged = read_expectations(self.outdir / 'FM8192.MDX')
        damaged['tracks'][-1]['duration_ticks'] -= 1
        with self.assertRaises(AssertionError):
            validate(damaged, 8192, self.baseline)
        damaged = read_expectations(self.outdir / 'FM8192.MDX')
        tempo = next(control for control in damaged['tracks'][0]['controls']
                     if control['kind'] == 'tempo')
        tempo['values'][0] += 1
        with self.assertRaises(AssertionError):
            validate(damaged, 8192, self.baseline)
        self.assertTrue(all(case['native_playback'] == 'unverified' for case in self.report['cases']))


if __name__ == '__main__':
    unittest.main()
