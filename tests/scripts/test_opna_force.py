"""Scoped forced omissions preserve native evidence and normal failure policy."""
from dataclasses import replace
import csv
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_opm import convert, project as project_fm, dump_fm_omissions
from opna_ssg_opm import project, dump_omissions
import test_opna_ssg_opm as helpers
from test_opna_ssg_opm import body
from test_opna_opm import vgm
import test_opna_opm as fm_helpers


class OpnaForceTests(unittest.TestCase):
    def test_force_mutes_only_unsupported_intervals_and_preserves_source(self):
        _, native = helpers.OpnaSsgOpmTests().analyze(body(()))
        template = next(s for s in native.segments[0] if s.mode == 1 and s.volume == 15)
        intervals = tuple(replace(template, vgmticks=i*1000, vgmticks_end=(i+1)*1000,
            event_id=i, original_event_id=i, mode=mode, envelope_enabled=envelope)
            for i, (mode, envelope) in enumerate(((1,0), (3,0), (1,0), (1,1), (1,0))))
        native = replace(native, segments={0: intervals}, source_end=5000)
        with self.assertRaisesRegex(ValueError, 'noise/hardware EG'):
            project(native, {0: 3})
        rows, baseline, performance = project(native, {0: 3}, force=True)
        self.assertEqual(native.segments[0], intervals)
        self.assertEqual([s.mode for s in intervals], [1,3,1,1,1])
        self.assertEqual([s.envelope_enabled for s in intervals], [0,0,0,1,0])
        self.assertEqual([r['target_audible'] for r in baseline.rows], [True,False,True,False,True])
        edges = [(r['source_vgmticks'], bool(r['target_data'] & 0x78)) for r in rows
                 if r['target_register'] == 8 and r['inferred_key']]
        self.assertEqual(edges, [(0,True),(1000,False),(2000,True),(3000,False),(4000,True),(5000,False)])
        omissions = baseline.settings['forced_omissions']
        self.assertEqual([r['reason'] for r in omissions], ['ssg_noise','ssg_hardware_envelope'])
        self.assertEqual([(r['start_vgmticks'],r['end_vgmticks']) for r in omissions],
                         [(1000,2000),(3000,4000)])
        self.assertEqual(performance.source_end, 5000)
        with tempfile.TemporaryDirectory() as temporary:
            baseline.dump(temporary, 'AUTH')
            performance.dump(temporary, 'AUTH')
            summary = dump_omissions(omissions, temporary, 'AUTH', force=True)
            self.assertEqual(summary['omitted_channel_samples'], 2000)
            with (Path(temporary)/'AUTH.opna_ssg.omissions.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)
            self.assertEqual(json.loads((Path(temporary)/'AUTH.opna_ssg.omissions.json').read_text())
                             ['omissions'], omissions)

    def test_strict_rerun_clears_previous_forced_omission_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'AUTH.vgm'
            source.write_bytes(vgm(body((0,)) + bytes.fromhex('56 07 30 61 64 00')))
            out = root/'out'
            convert(source, out, force=True, song_loop=False, normalize_lengths=False)
            for suffix in ('.csv', '.json'):
                self.assertTrue((out/('AUTH.opna_ssg.omissions'+suffix)).is_file())
            with self.assertRaisesRegex(ValueError, 'noise/hardware EG'):
                convert(source, out, song_loop=False, normalize_lengths=False)
            for suffix in ('.csv', '.json'):
                self.assertFalse((out/('AUTH.opna_ssg.omissions'+suffix)).exists())
            self.assertFalse((out/'AUTH.mdx.mml').exists())

    def test_unknown_register_writes_are_only_omitted_with_force(self):
        writes = bytes(v for port in (0x56,0x57) for reg in (0x83,0x87,0x8b)
                       for v in (port,reg,255)) + bytes.fromhex('56 21 01')
        helper = fm_helpers.OpnaOpmTests()
        native = helper.analyze(writes + body((0,)))
        before = native.unmodeled_writes
        with self.assertRaisesRegex(ValueError, 'unknown register'):
            project_fm(native, clock_hz=7987200)
        forced, losses = project_fm(native, clock_hz=7987200, force=True)
        ordinary, _ = project_fm(helper.analyze(body((0,))),clock_hz=7987200)
        trajectory = lambda rows: [(r['source_vgmticks'],r['target_register'],r['target_data']) for r in rows]
        self.assertEqual(trajectory(forced), trajectory(ordinary))
        self.assertEqual(native.unmodeled_writes, before)
        self.assertEqual(losses['forced_unmodeled_register_writes'],7)
        with tempfile.TemporaryDirectory() as temporary:
            report = dump_fm_omissions(native, temporary, 'AUTH', force=True)
            # Other already omitted rhythm/IRQ writes may be present in other fixtures.
            self.assertEqual(report['omitted_writes'],7)
            self.assertIn('unsupported_register_effect:uninterpreted',
                          {r['reason'] for r in report['omissions']})

    def test_unsupported_fm_modes_gate_only_affected_channel_without_new_keys(self):
        prefix = bytes.fromhex('56 29 80 56 b6 c0 56 a6 22 56 a2 69 56 28 f2 61 e8 03')
        for enable, disable in ((bytes.fromhex('56 27 40'),bytes.fromhex('56 27 00')),
                                (bytes.fromhex('56 27 80'),bytes.fromhex('56 27 00')),
                                (bytes.fromhex('56 92 08'),bytes.fromhex('56 92 00'))):
            with self.subTest(enable=enable.hex()):
                data = prefix + enable + bytes.fromhex('61 e8 03 56 b6 c0 61 e8 03') + disable + bytes.fromhex('61 e8 03 56 28 02 61 e8 03')
                native = fm_helpers.OpnaOpmTests().analyze(data)
                rows, _ = project_fm(native,clock_hz=7987200,force=True)
                pan = [(r['source_vgmticks'],r['target_data'] & 0xc0) for r in rows if r['target_register']==0x22]
                self.assertEqual(pan,[(0,0xc0),(1000,0),(3000,0xc0)])
                keys = [(r['source_vgmticks'],r['target_data']) for r in rows if r['target_register']==8]
                self.assertEqual(keys,[(0,0x7a),(4000,2)])

    def test_zero_binding_artifact_report_does_not_claim_stale_binaries(self):
        from opna_force import pcm_artifact_lines
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out/'AUTH.pcm.timing.json').write_text(json.dumps({'pcm_pdx_file':None}))
            (out/'AUTH.mdx').write_bytes(b'old')
            (out/'AUTH.pdx').write_bytes(b'old')
            lines = pcm_artifact_lines(out, 'AUTH')
            self.assertIn('PDX: not generated (no retained PCM samples)',lines)
            self.assertFalse(any(str(out/'AUTH.mdx') in line or str(out/'AUTH.pdx') in line for line in lines))

    def test_cli_lists_registers_and_strict_retry_clears_diagnostics(self):
        writes = bytes(v for port in (0x56,0x57) for reg in (0x83,0x87,0x8b)
                       for v in (port,reg,255))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);source=root/'AUTH.vgm';out=root/'out'
            source.write_bytes(vgm(writes+body((0,))))
            command=[sys.executable,str(ROOT/'vgm2mml.py'),str(source),'--target','mdx','--outdir',str(out),
                     '--no-song-loop','--no-normalize-lengths']
            run=subprocess.run([*command,'--force'],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('0x83',run.stdout)
            self.assertIn('omit_write_from_target',run.stdout)
            self.assertTrue((out/'AUTH.mdx.mml').is_file())
            with (out/'AUTH.opna.unmodeled.csv').open() as f:
                self.assertEqual(sum(int(r['register']) in (0x83,0x87,0x8b) for r in csv.DictReader(f)),6)
            registry=json.loads((out/'AUTH.opna_force.skipped_registers.json').read_text())
            self.assertTrue(registry['records'])
            strict=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(strict.returncode,0)
            self.assertIn('unknown register effect',strict.stderr)
            for suffix in ('.csv','.json'):
                self.assertFalse((out/('AUTH.opna_force.skipped_registers'+suffix)).exists())
                self.assertFalse((out/('AUTH.opna_fm.omissions'+suffix)).exists())


if __name__ == '__main__':
    unittest.main()
