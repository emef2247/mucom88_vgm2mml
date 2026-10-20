"""Batch MUCOMSX dispatch, isolated compilation and failure publication checks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import export_mucom as batch


def convert(command, **kwargs):
    source = Path(command[2])
    folder = Path(command[command.index('--outdir') + 1])
    (folder / (source.stem + '.muc')).write_bytes(b'MSXD o4 c4\r\n')
    return subprocess.CompletedProcess(command, 0, 'converted', '')


def compiled(muc, job, **options):
    assert options['compile_only'] is True
    artifacts = job / 'artifacts'
    artifacts.mkdir(parents=True)
    (artifacts / 'CHECK.MUB').write_bytes(b'msPb0101' + bytes(20))
    return dict(status='passed', files={'CHECK.MUB': {}}, validation_scope='MUB compilation only')


class MucomsxBatchTests(unittest.TestCase):
    def prepare(self, root, names=('song.vgm',)):
        source = root / 'inputs';source.mkdir()
        for name in names:
            p = source / name;p.parent.mkdir(parents=True, exist_ok=True);p.write_bytes(b'fixture')
        boot = root / 'boot.dsk';boot.write_bytes(b'authored placeholder')
        profile = root / 'runtime.json'
        profile.write_text(json.dumps(dict(boot_disk=str(boot), timeout=180)))
        return source, root / 'out', profile

    def test_recursive_mub_batch_uses_mucomsx_and_fresh_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, profile = self.prepare(Path(tmp), ('one/song.vgm', 'two/song.vgz'))
            with patch.object(batch.subprocess, 'run', side_effect=convert) as converter, \
                 patch('mucomsx_runtime.run_job', side_effect=compiled) as runtime, \
                 patch.object(batch, 'compile_muc') as old_compiler:
                rows = batch.run_batch(source, output, target='mucomsx', runtime_profile=profile, timeout=42)
            self.assertEqual([r['status'] for r in rows], ['success', 'success'])
            self.assertNotEqual(rows[0]['mub'], rows[1]['mub'])
            self.assertNotEqual(runtime.call_args_list[0].args[1], runtime.call_args_list[1].args[1])
            old_compiler.assert_not_called()
            for call in converter.call_args_list:
                self.assertEqual(call.args[0][call.args[0].index('--target') + 1], 'mucomsx')
            for call in runtime.call_args_list:
                self.assertEqual(call.kwargs['timeout'], 42)
                self.assertTrue(call.kwargs['compile_only'])
            for row in rows:
                self.assertEqual((output / row['mub']).read_bytes(), b'msPb0101' + bytes(20))
                self.assertEqual(json.loads((output / row['compilation_log']).read_text())['status'], 'passed')
                self.assertNotIn('CHECK.VGM', (output / row['compilation_log']).read_text())
            manifest = json.loads((output / 'export_manifest.json').read_text())
            self.assertTrue(all(any(name.endswith('.mub') for name in item['files']) for item in manifest.values()))

    def test_failed_dos_job_keeps_muc_and_diagnostics_then_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, profile = self.prepare(Path(tmp), ('bad.vgm', 'good.vgm'))
            def run(muc, job, **options):
                if muc.stem == 'bad':
                    artifacts = job / 'artifacts';artifacts.mkdir(parents=True)
                    (artifacts / 'CHECK.MUB').write_bytes(b'partial')
                    return dict(status='failed', failed_stage='dos_execution', detail='FAILED.ERR')
                return compiled(muc, job, **options)
            with patch.object(batch.subprocess, 'run', side_effect=convert), \
                 patch('mucomsx_runtime.run_job', side_effect=run):
                rows = batch.run_batch(source, output, target='mucomsx', runtime_profile=profile)
            self.assertEqual([r['status'] for r in rows], ['compilation_failed', 'success'])
            self.assertTrue((output / rows[0]['muc']).is_file())
            self.assertFalse(rows[0]['mub'])
            self.assertFalse((output / 'tracks/bad/bad.mub').exists())
            self.assertIn('FAILED.ERR', (output / rows[0]['compilation_log']).read_text())

    def test_bad_runtime_profile_fails_before_conversion_or_output_changes(self):
        for settings in ({'unknown': 1}, {}, {'boot_disk': '/nonexistent/boot.dsk'}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as tmp:
                source, output, profile = self.prepare(Path(tmp))
                profile.write_text(json.dumps(settings))
                with patch.object(batch.subprocess, 'run') as converter, self.assertRaises(ValueError):
                    batch.run_batch(source, output, target='mucomsx', runtime_profile=profile)
                converter.assert_not_called()
                self.assertFalse(output.exists())

    def test_profile_option_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, profile = self.prepare(Path(tmp))
            for options in (dict(target='mucom'), dict(target='mucomsx', muc_only=True)):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, '--runtime-profile'):
                    batch.run_batch(source, output, runtime_profile=profile, **options)
            self.assertFalse(output.exists())

    def test_cli_forwards_runtime_profile(self):
        with patch.object(sys, 'argv', ['export_mucom.py', 'input', '--outdir', 'output',
                                       '--target', 'mucomsx', '--runtime-profile', 'local.json']), \
             patch.object(batch, 'run_batch', return_value=[dict(status='success', report_status='success')]) as run:
            self.assertEqual(batch.main(), 0)
        self.assertEqual(run.call_args.kwargs['runtime_profile'], Path('local.json'))
        self.assertFalse(run.call_args.kwargs['muc_only'])


if __name__ == '__main__':
    unittest.main()
