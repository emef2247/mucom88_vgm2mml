"""Authored MUCOMSX canonical and batch dispatch checks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_mucom import run_batch, main as batch_main
from test_mucomsx_source import vgm


class MucomsxCliTests(unittest.TestCase):
    def source(self, root):
        source = root / 'authored.vgm'
        source.write_bytes(vgm(bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 b8 0b 66')))
        return source

    def test_canonical_dispatch_generates_muc_and_source_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            output = root / 'out'
            result = subprocess.run([sys.executable, str(ROOT / 'vgm2mml.py'), str(source),
                                     '--target', 'mucomsx', '--outdir', str(output),
                                     '--dump-passes', '--no-song-loop'],
                                    capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((output / 'authored.muc').is_file())
            self.assertTrue((output / 'authored.msx.segments.csv').is_file())
            self.assertTrue((output / 'authored.mucomsx.json').is_file())
            self.assertIn('Musical source units:', result.stdout)

    def test_canonical_rejects_explicit_inapplicable_default(self):
        for option in ('--pcm-profile=standard', '--pcm-prof=standard'):
            with self.subTest(option=option):
                result = subprocess.run([sys.executable, str(ROOT / 'vgm2mml.py'), 'unused.vgm',
                                         '--target=mucomsx', option],
                                        capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(result.returncode, 2)
                self.assertIn('does not support --pcm-profile', result.stderr)

    def test_batch_requires_runtime_profile_before_output_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            with self.assertRaisesRegex(ValueError, 'requires a local --runtime-profile'):
                run_batch(source, root / 'out', target='mucomsx',
                          runtime_profile=root / 'missing.json')
            self.assertFalse((root / 'out').exists())

    def test_batch_runs_target_and_protects_modified_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            output = root / 'out'
            with patch('export_mucom.compile_muc') as compiler:
                row = run_batch(source, output, target='mucomsx', muc_only=True)[0]
                compiler.assert_not_called()
            self.assertEqual(row['status'], 'muc_only', row['detail'])
            published = output / row['muc']
            published.write_bytes(b'user edited MUC')
            with self.assertRaisesRegex(ValueError, 'modified artifact'):
                run_batch(source, output, target='mucomsx', muc_only=True)
            self.assertEqual(published.read_bytes(), b'user edited MUC')

    def test_batch_cli_rejects_explicit_mucom_options(self):
        with patch('sys.argv', ['export_mucom.py', 'unused.vgm', '--outdir', 'unused',
                                '--target', 'mucomsx', '--muc-only', '--opm-channel-policy=auto']):
            with self.assertRaises(SystemExit) as error:
                batch_main()
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
