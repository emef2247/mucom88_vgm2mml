"""Batch source selection, unsupported clocks and retained failure evidence."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import verify_opm_mdx_roundtrip as batch
from test_opm_reader import vgm


class OpmMdxBatchTests(unittest.TestCase):
    def test_roundtrip_forwards_auto_on_and_off_normalization_choices(self):
        for flags, expected in [([], None), (['--normalize-lengths'], True), (['--no-normalize-lengths'], False)]:
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / 'source.vgm'
                source.write_bytes(vgm(bytes.fromhex('54 08 78 62 54 08 00')))
                generator = root / 'helper'
                generator.touch()
                argv = ['verify', str(source), '--generator', str(generator), '--outdir', str(root / 'out'), *flags]
                with patch.object(sys, 'argv', argv), patch.object(batch, 'generate', return_value=SimpleNamespace(events=[])), \
                        patch.object(batch, 'convert', side_effect=ValueError('stop after option resolution')) as convert, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(batch.main(), 1)
                self.assertIs(convert.call_args.kwargs['normalize_lengths'], expected)

    def test_mxc_roundtrip_compiles_then_replays_without_recompiling_mml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mml = root / 'score.mml'
            mml.write_text('A r4\n')
            def compile_score(source, output, **kwargs):
                self.assertEqual(source, mml)
                self.assertEqual(kwargs['mxc'], root / 'MXC.X')
                self.assertEqual(kwargs['run68'], root / 'run68')
                output.write_bytes(b'native MDX')
                kwargs['prepared_output'].write_bytes(b'A r4\r\n')
            def replay(command, **kwargs):
                self.assertEqual(command[1], '--from-mdx')
                self.assertEqual(command[-2:], ['--max-ticks', '7'])
                self.assertEqual(Path(command[2]).read_bytes(), b'native MDX')
                Path(command[3]).write_bytes(b'VGM')
                return subprocess.CompletedProcess(command, 0, '', '')
            def parse(source, output, **kwargs):
                kwargs['opm_metadata'].update(clock_hz=4000000, csv_path=root / 'trace.csv',
                                             source_end_vgmticks=17)
            analysis = object()
            with patch.object(batch, 'compile_mxc', side_effect=compile_score) as compiler, \
                    patch.object(batch.subprocess, 'run', side_effect=replay), \
                    patch.object(batch, 'parse_vgm', side_effect=parse), \
                    patch.object(batch, 'build_segments', return_value=analysis), \
                    patch.object(batch, 'dump_analysis'):
                returned = batch.generate(root / 'helper', mml, root, 'returned',
                                          compiler='mxc', mxc=root / 'MXC.X', run68=root / 'run68',
                                          max_ticks=7)
            self.assertIs(returned, analysis)
            compiler.assert_called_once()
            self.assertTrue((root / 'returned.mxc.mml').is_file())

    def test_mxc_error_removes_stale_evidence_and_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('returned.mdx', 'returned.vgm', 'returned.mxc.mml'):
                (root / name).write_bytes(b'stale')
            with patch.object(batch, 'compile_mxc', side_effect=ValueError('Missing --mxc tool')), \
                    patch.object(batch.subprocess, 'run') as replay:
                with self.assertRaisesRegex(ValueError, '--mxc'):
                    batch.generate(root / 'helper', root / 'score.mml', root, 'returned', compiler='mxc')
                replay.assert_not_called()
            self.assertFalse((root / 'returned.mdx').exists())
            self.assertFalse((root / 'returned.vgm').exists())
            self.assertIn('--mxc', (root / 'returned.compile.log').read_text())

    def test_failed_rerun_does_not_report_prior_mml_or_compiler_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.vgm'
            source.write_bytes(vgm(b'\x54\x08\x08'))
            generator = root / 'helper'
            generator.touch()
            out = root / 'out'
            folder = out / 'source'
            folder.mkdir(parents=True)
            old_names = ('source.mdx.mml', 'returned.mdx', 'returned.vgm',
                         'returned.mxc.mml', 'returned.compile.log')
            for name in old_names:
                (folder / name).write_bytes(b'previous successful run')
            argv = ['verify', str(source), '--generator', str(generator), '--outdir', str(out)]
            with patch.object(sys, 'argv', argv), patch.object(batch, 'generate',
                    return_value=SimpleNamespace(events=[])) as generate, \
                    patch.object(batch, 'convert', side_effect=ValueError('conversion stopped')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(batch.main(), 1)
                self.assertEqual(generate.call_count, 1)  # Initialization only.
            row = json.loads((out / 'results.json').read_text())[0]
            self.assertEqual(row['status'], 'conversion_failed')
            self.assertEqual(row['compiler'], 'mxc')
            for key in ('mml', 'mdx', 'vgm', 'compile_log', 'compiler_input'):
                self.assertNotIn(key, row)
            for name in old_names:
                self.assertFalse((folder / name).exists())
            self.assertIn('conversion stopped', (folder / 'conversion.log').read_text())

    def test_expected_replays_are_not_reused_as_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('from_mdx/a/a.vgm', 'from_fm/b/b.VGZ',
                         'mdx_roundtrip/a.vgm', 'from_mdx/a/reference/expected.vgm'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual([p.relative_to(root).as_posix() for p in batch.source_files(root)],
                             ['from_fm/b/b.VGZ', 'from_mdx/a/a.vgm'])

    def test_gzip_header_preflight_uses_native_version_aware_clock_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'compressed.vgm'
            path.write_bytes(gzip.compress(vgm(b'\x54\x08\x08', 3579580)))
            self.assertEqual(batch.source_facts(path),
                             dict(clock_hz=3579580, chip_type='YM2151', dual_chip=False))
            path.write_bytes(vgm(b'\x54\x08\x08', 0xc0000000 | 4000000))
            self.assertEqual(batch.source_facts(path),
                             dict(clock_hz=4000000, chip_type='YM2164', dual_chip=True))

    def test_unsupported_sources_have_reports_without_building_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.vgm'
            source.write_bytes(vgm(b'\x54\x08\x08', 3579580))
            generator = root / 'generator'
            generator.touch()
            out = root / 'out'
            argv = ['verify', str(source), '--generator', str(generator), '--outdir', str(out)]
            with patch.object(sys, 'argv', argv), patch.object(batch, 'generate',
                    return_value=SimpleNamespace(events=[])) as generate, patch.object(batch, 'convert') as convert, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(batch.main(), 1)
                convert.assert_not_called()
                self.assertEqual(generate.call_args.kwargs['compiler'], 'mxc')
            row = json.loads((out / 'results.json').read_text())[0]
            self.assertEqual(row['status'], 'unsupported_target')
            self.assertEqual(row['clock_hz'], 3579580)
            self.assertTrue((out / 'source/conversion.log').is_file())
            self.assertTrue((out / 'results.csv').is_file())

    def test_timeout_retains_both_compiler_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failure = subprocess.TimeoutExpired(['generator'], 180,
                                                  output=b'compiled MDX\n', stderr=b'replay stalled\n')
            with patch.object(batch.subprocess, 'run', side_effect=failure):
                with self.assertRaises(subprocess.TimeoutExpired):
                    batch.generate(root / 'generator', root / 'input.mml', root, 'returned')
            log = (root / 'returned.compile.log').read_text()
            self.assertIn('compiled MDX', log)
            self.assertIn('replay stalled', log)
            self.assertIn('180 seconds', log)


if __name__ == '__main__':
    unittest.main()
