"""Projected intermediate provenance and reuse of the canonical OPM path."""
import csv
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import struct

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts'), str(ROOT)]
from chip_segments import PsgSegment, SccAnalysis
from opm_mdx import projected_samples
from opm_performance import build_performance
from opm_performance_verify import compare_performance
from opm_target_vgm import write_target_vgm
from psg_scc_conversion import convert
from psg_scc_opm import project
from vgm_timing import command_times
from pcm_mdx import default_generator


def jittered_psg(path):
    from test_opm_mdx import wait
    commands = bytes.fromhex('a0 07 3e a0 08 00 a0 00 c8 a0 01 00')
    cursor = 0
    for index in range(40):
        start = round((index + 1) * 5419.008) + (index % 5 - 2) * 25
        end = start + round(9 * 451.584) + (index % 3 - 1) * 17
        commands += wait(start - cursor) + bytes((0xa0, 0, (200, 180, 160, 150)[index % 4], 0xa0, 8, 15))
        commands += wait(end - start) + bytes((0xa0, 8, 0))
        cursor = end
    commands += wait(1355) + b'\x66'
    header = bytearray(0x100)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 8, 0x171)
    struct.pack_into('<I', header, 0x34, 0xcc)
    struct.pack_into('<I', header, 0x74, 1789772)
    raw = header + commands
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    path.write_bytes(raw)


def jittered_scc(path):
    from test_opm_mdx import wait
    wave = bytes(round(120 * math.sin(2 * math.pi * i / 32)) & 255 for i in range(32))
    commands = b''.join(bytes((0xd2, 0, i, value)) for i, value in enumerate(wave))
    commands += bytes.fromhex('d2 01 00 c8 d2 01 01 00 d2 02 00 00 d2 03 00 01')
    cursor = 0
    for index in range(40):
        start = round((index + 1) * 5419.008) + (index % 5 - 2) * 25
        end = start + round(9 * 451.584) + (index % 3 - 1) * 17
        commands += wait(start - cursor) + bytes((0xd2, 1, 0, (200, 180, 160, 150)[index % 4],
                                                0xd2, 2, 0, 15))
        commands += wait(end - start) + bytes((0xd2, 2, 0, 0))
        cursor = end
    commands += wait(1355) + b'\x66'
    header = bytearray(0x100)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 8, 0x171)
    struct.pack_into('<I', header, 0x34, 0xcc)
    struct.pack_into('<I', header, 0x9c, 1789772)
    raw = header + commands
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    path.write_bytes(raw)


class TargetVgmTests(unittest.TestCase):
    def test_scc_and_additive_psg_share_default_output_normalization(self):
        for chip, generate, options in (('scc', jittered_scc, {}),
                                        ('psg', jittered_psg, {'psg_model': 'additive'})):
            with self.subTest(chip=chip), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / 'jitter.vgm'
                generate(source)
                original = source.read_bytes()
                before, native = convert(source, root / 'before', normalize_lengths=False, **options)
                after, normalized = convert(source, root / 'after', **options)
                report = normalized.structured_context.normalization
                self.assertTrue(report['enabled'])
                self.assertTrue(report['adopted'], report)
                self.assertTrue(report['source_projection_check']['accepted'])
                self.assertEqual(native.rows, normalized.rows)
                self.assertEqual((root / 'before' / f'jitter.{chip}.segments.csv').read_bytes(),
                                 (root / 'after' / f'jitter.{chip}.segments.csv').read_bytes())
                self.assertEqual(source.read_bytes(), original)
                self.assertNotEqual(before.read_bytes(), after.read_bytes())

    @unittest.skipUnless(default_generator().is_file(), 'Build external MDX helper for integration checks')
    def test_normalized_psg_target_survives_semantic_roundtrip(self):
        from scripts.psg_scc_to_mdx import compile_and_verify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'jitter.vgm'
            jittered_psg(source)
            mml, plan = convert(source, root / 'out')
            self.assertTrue(plan.structured_context.normalization['adopted'])
            report = compile_and_verify(default_generator(), mml, plan, root / 'out', 'returned')
            self.assertTrue(report['passed'], report)
            self.assertTrue(report['key_commands_match'])

    def test_psg_normalization_checks_original_boundaries_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'jitter.vgm'
            jittered_psg(source)
            before, plan_before = convert(source, root / 'before', normalize_lengths=False)
            after, plan_after = convert(source, root / 'after')
            report = json.loads((root / 'after/jitter.mdx.normalization.json').read_text())
            self.assertTrue(report['adopted'], report)
            self.assertTrue(report['source_projection_check']['accepted'])
            self.assertEqual(plan_before.rows, plan_after.rows)
            self.assertEqual((root / 'before/jitter.psg.segments.csv').read_bytes(),
                             (root / 'after/jitter.psg.segments.csv').read_bytes())
            baseline_native = '\n'.join(before.read_text().splitlines()[2:]) + '\n'
            self.assertEqual(baseline_native,
                             (root / 'after/projected_opm/jitter.mdx.before.normalize.mml').read_text())
            self.assertNotEqual(before.read_bytes(), after.read_bytes())
            provenance = json.loads((root / 'after/projected_opm/provenance.json').read_text())
            self.assertLessEqual(provenance['max_abs_source_to_final_error_samples'],
                                 provenance['source_to_final_timing_bound_samples'])

    def test_original_source_check_rejection_keeps_structured_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'jitter.vgm'
            jittered_psg(source)
            baseline, _ = convert(source, root / 'before', normalize_lengths=False)
            with patch('psg_scc_conversion._source_normalization_check', return_value=dict(
                    accepted=False, reason='Original source boundary would collapse')) as checked:
                rejected, _ = convert(source, root / 'after')
            checked.assert_called_once()
            self.assertEqual(baseline.read_bytes(), rejected.read_bytes())
            report = json.loads((root / 'after/jitter.mdx.normalization.json').read_text())
            self.assertFalse(report['adopted'])
            self.assertEqual(report['reason'], 'Original source boundary would collapse')
            self.assertEqual(report['selected'], report['before'])
            self.assertTrue((root / 'after/projected_opm/jitter.mdx.structure.units.csv').is_file())

    def test_registers_holding_is_an_internal_compatibility_mode(self):
        source = ROOT / 'tests/fixtures/public/psg/short_pulses/short_pulses.vgm'
        with tempfile.TemporaryDirectory() as temporary:
            _, plan = convert(source, temporary, notation='registers')
            self.assertEqual(plan.settings['projection_mode'], 'held-register-compatibility')
            self.assertIsNone(plan.structured_context)
            with self.assertRaisesRegex(ValueError, 'incompatible'):
                convert(source, temporary, notation='registers', projection_mode='musical')

    def test_serialization_maps_real_command_ids_and_preserves_end(self):
        first = PsgSegment('v', 0., 0, 0, 1, 200, 15, 4, 'c', 0, (),
                           1, 0, 0, 0, 0, 0x3e, 15, vgmticks=70000, vgmticks_end=71000)
        baseline = project({0: [first]}, SccAnalysis({}, ()), psg_clock=1789772,
                           scc_clock=0, end_vgmticks=80000, psg_model='fm')
        performance = build_performance(baseline)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_target_vgm(performance, root/'projected.vgm', mapping_csv=root/'mapping.csv')
            raw = path.read_bytes()
            commands = list(command_times(raw))
            observed = [(e.vgmticks, raw[e.address+1], raw[e.address+2]) for e in commands if e.command == 0x54]
            expected = [(projected_samples(w.mdx_tick), w.register, w.data) for w in performance.scheduled_writes()]
            self.assertEqual(observed, expected)
            self.assertEqual(commands[-1].vgmticks, projected_samples(performance.end_tick))
            with (root/'mapping.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                event = commands[int(row['target_source_event_id'])]
                self.assertEqual(event.address, int(row['target_command_address']))
                self.assertEqual(event.vgmticks, int(row['target_vgmticks']))
                self.assertEqual(row['state_origin'], 'projected_opm')

    def test_canonical_pipeline_and_baseline_are_separate_and_traceable(self):
        source = ROOT/'tests/fixtures/public/psg/short_pulses/short_pulses.vgm'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mml, baseline = convert(source, root/'structured')
            raw_mml, raw_plan = convert(source, root/'registers', notation='registers')
            self.assertEqual(baseline.writes, raw_plan.writes)
            self.assertEqual(baseline.rows, raw_plan.rows)
            self.assertIn('/* Track F */', mml.read_text())
            self.assertIn('@0 = {', mml.read_text())
            self.assertNotEqual(mml.read_text(), raw_mml.read_text())
            c = baseline.structured_context
            self.assertEqual(sum(bool(e.rising_mask) for e in c.analysis.events), 4)
            folder = root/'structured/projected_opm'
            provenance = json.loads((folder/'provenance.json').read_text())
            self.assertEqual(provenance['opm_pipeline'], 'opm_conversion.convert')
            self.assertLessEqual(provenance['max_abs_source_to_final_error_samples'],
                                 provenance['source_to_final_timing_bound_samples'])
            with (folder/'short_pulses.opm.segments.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(all(r['state_origin'] == 'projected_opm' for r in rows))

    def test_validator_rejects_extra_same_mask_key_command(self):
        source = ROOT/'tests/fixtures/public/psg/short_pulses/short_pulses.vgm'
        with tempfile.TemporaryDirectory() as tmp:
            _, baseline = convert(source, tmp, normalize_lengths=False)
            c = baseline.structured_context
            self.assertTrue(compare_performance(c, c.analysis, initialization=[])['passed'])
            on = next(e for e in c.analysis.events if e.rising_mask)
            index = c.analysis.events.index(on)
            duplicate = replace(on, rising_mask=0, falling_mask=0)
            actual = replace(c.analysis, events=c.analysis.events[:index+1]+(duplicate,)+c.analysis.events[index+1:])
            self.assertFalse(compare_performance(c, actual, initialization=[])['key_commands_match'])

    def test_validator_rejects_wrong_state_at_key_even_if_boundary_recovers(self):
        source = ROOT/'tests/fixtures/public/psg/short_pulses/short_pulses.vgm'
        with tempfile.TemporaryDirectory() as tmp:
            _, baseline = convert(source, tmp, normalize_lengths=False)
            c = baseline.structured_context
            events = list(c.analysis.events)
            index = next(i for i,e in enumerate(events) if e.rising_mask)
            on = events[index]
            events[index] = replace(on, state=replace(on.state, kc_raw=0))
            events.insert(index+1, replace(on, source_event_id=max(e.source_event_id for e in events)+1,
                          register=0x28+on.ch, data=on.state.kc_raw, rising_mask=0, falling_mask=0))
            actual = replace(c.analysis, events=tuple(events))
            report = compare_performance(c, actual, initialization=[])
            self.assertTrue(report['key_commands_match'])
            self.assertEqual(report['known_state_mismatches'], 0)
            self.assertGreater(report['key_point_state_mismatches'], 0)
            self.assertFalse(report['passed'])

    def test_frontend_supports_register_baseline_and_no_loops(self):
        source = ROOT/'tests/fixtures/public/psg/short_pulses/short_pulses.vgm'
        with tempfile.TemporaryDirectory() as tmp:
            for flags in (['--notation','registers'], ['--no-loops']):
                out = Path(tmp)/flags[-1]
                run = subprocess.run([sys.executable,str(ROOT/'vgm2mml.py'),str(source),
                                      '--target','opm','--outdir',str(out),*flags],capture_output=True,text=True)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertEqual(sorted(p.name for p in out.iterdir()),
                                 ['short_pulses.conversion.json', 'short_pulses.mdx.mml', 'short_pulses.mdx.normalization.json'])


if __name__ == '__main__':
    unittest.main()
