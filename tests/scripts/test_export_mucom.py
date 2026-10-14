"""Authored batch-export tests: stage failures, diagnostics and publications."""
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_mucom import main, run_batch


def convert(command, **kwargs):
    source = Path(command[2])
    folder = Path(command[command.index('--outdir') + 1])
    (folder / (source.stem + '.muc')).write_text('A c4\n', encoding='utf-8')
    (folder / (source.stem + '.segments.csv')).write_text('channel,note\nA,C\n')
    return subprocess.CompletedProcess(command, 0, 'converted\n', '')


def compile_score(muc, mub, **kwargs):
    mub.write_bytes(b'compiled MUB')
    kwargs['log_path'].write_text('compiled\n')


class ExportMucomTests(unittest.TestCase):
    def prepare(self, folder, names=('song.vgm',)):
        source = folder / 'inputs'
        source.mkdir()
        for name in names:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'authored test placeholder')
        return source, folder / 'out'

    def test_recursive_success_separates_passes_logs_and_pcm(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('nested/song.vgm',))
            def convert_pcm(command, **kwargs):
                result = convert(command, **kwargs)
                folder = Path(command[command.index('--outdir') + 1])
                (folder / 'song.muc').write_text('#pcm song_pcm.bin\n#voice song.dat\nA c4\n')
                (folder / 'song.dat').write_bytes(b'owned voices')
                (folder / 'song_pcm.bin').write_bytes(b'owned bank')
                return result
            with patch('export_mucom.subprocess.run', side_effect=convert_pcm) as converter, \
                    patch('export_mucom.compile_muc', side_effect=compile_score):
                row = run_batch(source, out)[0]
            self.assertIn('--dump-passes', converter.call_args.args[0])
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['input'], 'nested/song.vgm')
            self.assertEqual(row['muc'], 'tracks/song/song.muc')
            self.assertEqual(row['mub'], 'tracks/song/song.mub')
            self.assertEqual(row['pcm_bank'], 'tracks/song/song_pcm.bin')
            self.assertEqual(row['voice_bank'], 'tracks/song/song.dat')
            self.assertEqual(set(p.name for p in (out / 'tracks/song').iterdir()),
                             {'song.muc', 'song.mub', 'song_pcm.bin', 'song.dat'})
            self.assertTrue((out / row['diagnostics'] / 'song.segments.csv').exists())
            self.assertTrue((out / row['conversion_log']).exists())
            self.assertTrue((out / row['compilation_log']).exists())
            with (out / 'results.csv').open(newline='', encoding='utf-8') as stream:
                self.assertEqual(list(csv.DictReader(stream))[0]['status'], 'success')

    def test_cli_forwards_no_song_loop_and_600_second_stage_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('sys.argv', ['export_mucom.py', str(source), '--outdir', str(out),
                                    '--no-song-loop', '--timeout', '600']), \
                    patch('export_mucom.subprocess.run', side_effect=convert) as converter, \
                    patch('export_mucom.compile_muc', side_effect=compile_score) as compiler:
                self.assertEqual(main(), 0)
            self.assertIn('--no-song-loop', converter.call_args.args[0])
            self.assertEqual(converter.call_args.kwargs['timeout'], 600)
            self.assertEqual(compiler.call_args.kwargs['timeout'], 600)

    def test_cli_forwards_normalization_default_explicit_and_off(self):
        for flags, enabled, milliseconds in (([], True, 8.0),
                (['--normalize-lengths', '--normalization-ms', '16'], True, 16.0),
                (['--no-normalize-lengths', '--normalization-ms', '4'], False, 4.0)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                with patch('sys.argv', ['export_mucom.py', str(source), '--outdir', str(out),
                                        '--muc-only', *flags]), \
                        patch('export_mucom.subprocess.run', side_effect=convert) as converter:
                    self.assertEqual(main(), 0)
                command = converter.call_args.args[0]
                self.assertIn('--normalize-lengths' if enabled else '--no-normalize-lengths', command)
                self.assertNotIn('--no-normalize-lengths' if enabled else '--normalize-lengths', command)
                self.assertEqual(float(command[command.index('--normalization-ms') + 1]), milliseconds)

    def test_run_batch_rejects_invalid_normalization_before_output_changes(self):
        for value in (0, -1, float('nan'), float('inf'), .001):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                with patch('export_mucom.subprocess.run') as converter, self.assertRaises(ValueError):
                    run_batch(source, out, normalization_ms=value)
                converter.assert_not_called()
                self.assertFalse(out.exists())

    def test_conversion_failed_continues_and_never_calls_compiler(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('bad.vgm', 'good.vgm'))
            def run(command, **kwargs):
                if Path(command[2]).stem == 'bad':
                    return subprocess.CompletedProcess(command, 1, '', 'unsupported source')
                return convert(command, **kwargs)
            with patch('export_mucom.subprocess.run', side_effect=run), \
                    patch('export_mucom.compile_muc', side_effect=compile_score) as compiler:
                rows = run_batch(source, out)
            self.assertEqual([r['status'] for r in rows], ['conversion_failed', 'success'])
            self.assertEqual(compiler.call_count, 1)
            self.assertIn('unsupported source', rows[0]['detail'])
            self.assertFalse(rows[0]['muc'] or rows[0]['mub'])

    def test_compilation_failure_preserves_muc_and_removes_partial_mub(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            def fail(muc, mub, **kwargs):
                mub.write_bytes(b'partial')
                raise RuntimeError('compiler rejected score')
            with patch('export_mucom.subprocess.run', side_effect=convert), \
                    patch('export_mucom.compile_muc', side_effect=fail):
                row = run_batch(source, out)[0]
            self.assertEqual(row['status'], 'compilation_failed')
            self.assertTrue(row['muc'])
            self.assertFalse(row['mub'])
            self.assertFalse((out / 'tracks/song/song.mub').exists())

    def test_empty_muc_and_missing_pcm_are_conversion_failures(self):
        for text in ('', '#pcm missing.bin\nA c4\n', '#pcm ../escape.bin\nA c4\n'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                def run(command, **kwargs):
                    result = convert(command, **kwargs)
                    folder = Path(command[command.index('--outdir') + 1])
                    (folder / 'song.muc').write_text(text)
                    return result
                with patch('export_mucom.subprocess.run', side_effect=run), \
                        patch('export_mucom.compile_muc') as compiler:
                    row = run_batch(source, out)[0]
                self.assertEqual(row['status'], 'conversion_failed')
                compiler.assert_not_called()

    def test_timeout_reports_persisted_converter_stage(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            source,out=self.prepare(Path(tmp))
            def interrupted(command,**kwargs):
                folder=Path(command[command.index('--outdir')+1])
                (folder/'song.mucom.json').write_text(json.dumps(dict(stage='output_normalization',
                    stage_timings={'native_state_segments_pcm_ir':1.5},current_stage_started_at_unix=100)))
                raise subprocess.TimeoutExpired(command,3,output=b'normalizing')
            with patch('export_mucom.subprocess.run',side_effect=interrupted):
                row=run_batch(source,out,timeout=3)[0]
            self.assertIn('MUC stage: output_normalization',row['detail'])
            self.assertIn('MUC stage: output_normalization',(out/row['report']).read_text())
            self.assertTrue((out/row['diagnostics']/'song.mucom.json').is_file())

    def test_timeout_classifies_stage_and_retains_output_log(self):
        for stage in ('conversion', 'compilation'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                error = subprocess.TimeoutExpired(['tool'], 3, output=b'partial log')
                with patch('export_mucom.subprocess.run', side_effect=error if stage == 'conversion' else convert), \
                        patch('export_mucom.compile_muc', side_effect=error):
                    row = run_batch(source, out, timeout=3)[0]
                self.assertEqual(row['status'], stage + '_failed')
                self.assertIn('timed out after 3 seconds', row['detail'])
                if stage == 'conversion':
                    self.assertIn('partial log', (out / row['conversion_log']).read_text())

    def test_muc_only_is_explicit_and_does_not_claim_compilation_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('export_mucom.subprocess.run', side_effect=convert), \
                    patch('export_mucom.compile_muc') as compiler:
                row = run_batch(source, out, muc_only=True)[0]
            compiler.assert_not_called()
            self.assertEqual(row['status'], 'muc_only')
            self.assertFalse(row['mub'])
            with patch('sys.argv', ['export_mucom.py', str(source), '--outdir', str(out), '--muc-only']), \
                    patch('export_mucom.run_batch', return_value=[row]):
                self.assertEqual(main(), 0)

    def test_rerun_removes_owned_stale_binary_but_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('export_mucom.subprocess.run', side_effect=convert), \
                    patch('export_mucom.compile_muc', side_effect=compile_score):
                run_batch(source, out)
            unrelated = out / 'tracks/song/notes.txt'
            unrelated.write_text('user notes')
            with patch('export_mucom.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'bad')):
                row = run_batch(source, out)[0]
            self.assertEqual(row['status'], 'conversion_failed')
            self.assertFalse((out / 'tracks/song/song.mub').exists())
            self.assertEqual(unrelated.read_text(), 'user notes')

    def test_rerun_refuses_modified_or_unowned_artifacts(self):
        for modified in (True, False):
            with self.subTest(modified=modified), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                if modified:
                    with patch('export_mucom.subprocess.run', side_effect=convert):
                        run_batch(source, out, muc_only=True)
                folder = out / 'tracks/song'
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'song.muc').write_text('user edit')
                with patch('export_mucom.subprocess.run') as converter:
                    row = run_batch(source, out, muc_only=True)[0]
                self.assertEqual(row['status'], 'conversion_failed')
                converter.assert_not_called()
                self.assertEqual((folder / 'song.muc').read_text(), 'user edit')

    def test_report_failure_is_separate_from_export_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('export_mucom.subprocess.run', side_effect=convert), \
                    patch('export_mucom.compile_muc', side_effect=compile_score), \
                    patch('export_mucom.write_export_report', side_effect=OSError('report unavailable')):
                row = run_batch(source, out)[0]
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['report_status'], 'failed')
            self.assertIn('report unavailable', row['report_error'])

    def test_same_stem_sources_get_distinct_publication_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('one/song.vgm', 'two/song.vgz'))
            with patch('export_mucom.subprocess.run', side_effect=convert):
                rows = run_batch(source, out, muc_only=True)
            self.assertNotEqual(rows[0]['muc'], rows[1]['muc'])
            self.assertTrue(all(r['status'] == 'muc_only' for r in rows))

    def test_output_tree_must_be_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'separate trees'):
                run_batch(source, source / 'out')


if __name__ == '__main__':
    unittest.main()

class MucomCompilerTests(unittest.TestCase):
    def test_native_command_requires_binary_and_nonempty_total_counts(self):
        from mucom_compiler import compile_muc as native_compile
        for mode in ('valid', 'empty', 'missing', 'bad_header', 'failure', 'error_log'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                exe, muc, mub = folder / 'mucomDotNETConsole.exe', folder / 'score.muc', folder / 'score.mub'
                exe.write_bytes(b'authored executable placeholder')
                muc.write_text('A C192t120v12o4l4q4r4 L c4d4\n')
                mub.write_bytes(b'old binary')
                log = folder / 'diagnostics/compiler.log'
                def run(command, **kwargs):
                    self.assertEqual(command[0], str(exe))
                    prepared = Path(command[1])
                    self.assertEqual(prepared.name, muc.name)
                    self.assertEqual(kwargs['cwd'], prepared.parent)
                    self.assertNotEqual(prepared.parent, folder)
                    generated = prepared.with_suffix('.mub')
                    self.assertEqual(kwargs['timeout'], 12)
                    if mode != 'missing':
                        generated.write_bytes(b'MUB8' + bytes(100) if mode != 'bad_header' else b'not MUB')
                    total = 144 if mode not in ('empty', 'failure') else 0
                    stdout = f'[INFO   ] [ Total count ]\n[INFO   ] A:{total} B:0 C:0\n'.encode()
                    if mode == 'error_log':
                        stdout += b'[ERROR  ] incomplete score\n'
                    return subprocess.CompletedProcess(command, 1 if mode == 'failure' else 0, stdout, b'')
                with patch('mucom_compiler.subprocess.run', side_effect=run):
                    if mode == 'valid':
                        self.assertEqual(native_compile(muc, mub, mucom88=exe, timeout=12, log_path=log), mub)
                    else:
                        with self.assertRaises(RuntimeError):
                            native_compile(muc, mub, mucom88=exe, timeout=12, log_path=log)
                        self.assertFalse(mub.exists())
                self.assertTrue(log.exists())

    def test_native_timeout_saves_partial_log_and_removes_binary(self):
        from mucom_compiler import compile_muc as native_compile
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            exe, muc, mub = folder / 'mucomDotNETConsole.exe', folder / 'score.muc', folder / 'score.mub'
            exe.write_bytes(b'executable placeholder')
            muc.write_text('A c4\n')
            def run(command, **kwargs):
                Path(command[1]).with_suffix('.mub').write_bytes(b'partial')
                raise subprocess.TimeoutExpired(command, 1, output=b'partial log')
            with patch('mucom_compiler.subprocess.run', side_effect=run), self.assertRaises(subprocess.TimeoutExpired):
                native_compile(muc, mub, mucom88=exe, timeout=1)
            self.assertFalse(mub.exists())
            self.assertIn(b'partial log', muc.with_suffix('.compile.log').read_bytes())


    def test_native_prepared_input_resolves_tags_and_preserves_published_score(self):
        from mucom_compiler import compile_muc as native_compile
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            exe, muc, mub = folder / 'compiler.exe', folder / 'score.muc', folder / 'score.mub'
            exe.write_bytes(b'test compiler')
            original = '#voice score.dat\n#pcm score_pcm.bin\nA C192t120c4\n'
            muc.write_bytes(original.encode('cp932'))
            for name in ('score.dat', 'score_pcm.bin'):
                (folder / name).write_bytes(b'owned dependency')
            def run(command, **kwargs):
                prepared = Path(command[1])
                text = prepared.read_bytes().decode('cp932')
                self.assertIn('#voice ' + str(folder / 'score.dat'), text)
                self.assertIn('#pcm ' + str(folder / 'score_pcm.bin'), text)
                self.assertIn('\r\nA C192t120c4\r\n', text)
                prepared.with_suffix('.mub').write_bytes(b'MUB8' + bytes(100))
                return subprocess.CompletedProcess(command, 0, b'[ Total count ]\nA:48 B:0\n', b'')
            with patch('mucom_compiler.subprocess.run', side_effect=run):
                native_compile(muc, mub, mucom88=exe)
            self.assertEqual(muc.read_bytes().decode('cp932'), original)
            self.assertTrue(mub.exists())


if __name__ == '__main__':
    unittest.main()
