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
            self.assertEqual(row['muc'], 'nested/tracks/song.muc')
            self.assertEqual(row['mub'], 'nested/tracks/song.mub')
            self.assertEqual(row['pcm_bank'], 'nested/tracks/song_pcm.bin')
            self.assertEqual(row['voice_bank'], 'nested/tracks/song.dat')
            self.assertEqual(set(p.name for p in (out / 'nested/tracks').iterdir()),
                             {'song.muc', 'song.mub', 'song_pcm.bin', 'song.dat'})
            self.assertTrue((out / row['diagnostics'] / 'song.segments.csv').exists())
            self.assertTrue((out / row['conversion_log']).exists())
            self.assertTrue((out / row['compilation_log']).exists())
            with (out / 'nested/results.csv').open(newline='', encoding='utf-8') as stream:
                self.assertEqual(list(csv.DictReader(stream))[0]['status'], 'success')

    def test_cli_forwards_default_and_explicit_stage_timeout(self):
        for flags, expected in (([], 1200), (['--timeout', '600'], 600)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                with patch('sys.argv', ['export_mucom.py', str(source), '--outdir', str(out),
                                        '--no-song-loop', *flags]), \
                        patch('export_mucom.subprocess.run', side_effect=convert) as converter, \
                        patch('export_mucom.compile_muc', side_effect=compile_score) as compiler:
                    self.assertEqual(main(), 0)
                self.assertIn('--no-song-loop', converter.call_args.args[0])
                self.assertEqual(converter.call_args.kwargs['timeout'], expected)
                self.assertEqual(compiler.call_args.kwargs['timeout'], expected)

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
            self.assertFalse((out / 'tracks/song.mub').exists())

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

    def test_failed_rerun_preserves_prior_bundle_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('export_mucom.subprocess.run', side_effect=convert), \
                    patch('export_mucom.compile_muc', side_effect=compile_score):
                run_batch(source, out)
            unrelated = out / 'tracks/notes.txt'
            unrelated.write_text('user notes')
            with patch('export_mucom.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'bad')):
                row = run_batch(source, out)[0]
            self.assertEqual(row['status'], 'conversion_failed')
            self.assertTrue((out / 'tracks/song.mub').exists())
            self.assertEqual(unrelated.read_text(), 'user notes')

    def test_rerun_refuses_modified_or_unowned_artifacts(self):
        for modified in (True, False):
            with self.subTest(modified=modified), tempfile.TemporaryDirectory() as tmp:
                source, out = self.prepare(Path(tmp))
                if modified:
                    with patch('export_mucom.subprocess.run', side_effect=convert):
                        run_batch(source, out, muc_only=True)
                folder = out / 'tracks'
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'song.muc').write_text('user edit')
                with patch('export_mucom.subprocess.run') as converter, self.assertRaises(ValueError):
                    run_batch(source, out, muc_only=True)
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

class PackageLayoutTests(unittest.TestCase):
    prepare = ExportMucomTests.prepare

    def test_adjacent_output_rerun_prunes_generated_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self.prepare(Path(tmp), ('game/song.vgm',))
            with patch('export_mucom.subprocess.run', side_effect=convert):
                first = run_batch(source, muc_only=True)
                second = run_batch(source, muc_only=True)
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(first[0]['muc'], 'game/tracks/song.muc')
            self.assertEqual((source / 'game/_source_inputs/song.vgm').read_bytes(),
                             (source / 'game/song.vgm').read_bytes())

    def test_package_results_are_local_and_keep_other_song_on_single_rerun(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('game/a.vgm', 'game/b.vgm'))
            with patch('export_mucom.subprocess.run', side_effect=convert):
                run_batch(source, out, muc_only=True)
                run_batch(source / 'game/a.vgm', out / 'game', muc_only=True)
            with (out / 'game/results.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([r['input'] for r in rows], ['a.vgm', 'b.vgm'])
            self.assertEqual([r['muc'] for r in rows], ['tracks/a.muc', 'tracks/b.muc'])
            manifest = json.loads((out / 'game/listening_manifest.json').read_text())
            self.assertEqual(set(manifest['entries']), {'a.vgm', 'b.vgm'})
            self.assertFalse((out / 'game/tracks/a').exists())

    def test_same_package_casefold_stems_fail_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('song.vgm', 'song.vgz'))
            with patch('export_mucom.subprocess.run') as converter, self.assertRaisesRegex(ValueError, 'collision'):
                run_batch(source, out, muc_only=True)
            converter.assert_not_called()
            self.assertFalse(out.exists())

    def test_dependency_collision_does_not_overwrite_other_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('a.vgm', 'b.vgm'))
            def bank(command, **kwargs):
                result = convert(command, **kwargs)
                stem = Path(command[2]).stem
                folder = Path(command[command.index('--outdir') + 1])
                (folder / (stem + '.muc')).write_text('#voice shared.dat\nA c4\n')
                (folder / 'shared.dat').write_bytes(stem.encode())
                return result
            with patch('export_mucom.subprocess.run', side_effect=bank):
                rows = run_batch(source, out, muc_only=True)
            self.assertEqual([r['status'] for r in rows], ['muc_only', 'publication_failed'])
            self.assertEqual((out / 'tracks/shared.dat').read_bytes(), b'a')
            self.assertFalse((out / 'tracks/b.muc').exists())

    def test_modified_staged_source_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('export_mucom.subprocess.run', side_effect=convert):
                run_batch(source, out, muc_only=True)
            staged = out / '_source_inputs/song.vgm'
            staged.write_bytes(b'user edit')
            with patch('export_mucom.subprocess.run') as converter, self.assertRaisesRegex(ValueError, 'staged input'):
                run_batch(source, out, muc_only=True)
            converter.assert_not_called()
            self.assertEqual(staged.read_bytes(), b'user edit')


    def test_metadata_file_cannot_also_be_package_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp), ('a.vgm', 'results.csv/b.vgm'))
            with patch('export_mucom.subprocess.run') as converter, self.assertRaisesRegex(ValueError, 'file/directory collision'):
                run_batch(source, out, muc_only=True)
            converter.assert_not_called()
            self.assertFalse(out.exists())

    def test_manifest_failure_restores_previous_bundle_and_allows_retry(self):
        import hashlib
        import json
        import export_mucom
        with tempfile.TemporaryDirectory() as tmp:
            source, out = self.prepare(Path(tmp))
            with patch('export_mucom.subprocess.run', side_effect=convert), patch('export_mucom.compile_muc', side_effect=compile_score):
                run_batch(source, out)
            before = {p.name: p.read_bytes() for p in (out / 'tracks').iterdir()}
            original_manifest = json.loads((out / 'listening_manifest.json').read_text())
            old_source_hash = original_manifest['entries']['song.vgm']['source_sha256']
            (source / 'song.vgm').write_bytes(b'new source revision')
            failed = False
            real_save = export_mucom._atomic_json
            def fail_commit(path, data):
                nonlocal failed
                entry = data.get('entries', {}).get('song.vgm', {}) if isinstance(data, dict) else {}
                if path.name == 'listening_manifest.json' and entry.get('source_sha256') != old_source_hash and entry.get('published') and not failed:
                    failed = True
                    raise OSError('injected ownership commit failure')
                return real_save(path, data)
            def changed(command, **kwargs):
                result = convert(command, **kwargs)
                folder = Path(command[command.index('--outdir') + 1])
                (folder / 'song.muc').write_text('A d4\n')
                return result
            with patch('export_mucom.subprocess.run', side_effect=changed), patch('export_mucom.compile_muc', side_effect=compile_score), patch('export_mucom._atomic_json', side_effect=fail_commit):
                row = run_batch(source, out)[0]
            self.assertTrue(failed)
            self.assertEqual(row['status'], 'publication_failed')
            self.assertEqual(before, {p.name: p.read_bytes() for p in (out / 'tracks').iterdir()})
            restored = json.loads((out / 'listening_manifest.json').read_text())['entries']['song.vgm']
            self.assertEqual(restored['published'], original_manifest['entries']['song.vgm']['published'])
            self.assertEqual(restored['source_sha256'], old_source_hash)
            self.assertEqual(restored['status'], 'success')
            self.assertEqual(restored['latest_attempt_status'], 'publication_failed')
            self.assertEqual(restored['staged_sha256'], hashlib.sha256(b'new source revision').hexdigest())
            self.assertFalse(list((out / 'tracks').glob('*.tmp')))
            with patch('export_mucom.subprocess.run', side_effect=changed), patch('export_mucom.compile_muc', side_effect=compile_score):
                self.assertEqual(run_batch(source, out)[0]['status'], 'success')


class MucomCompilerTests(unittest.TestCase):
    def setUp(self):
        adapter = patch('mucom_compiler._tool_path', side_effect=lambda path, compiler: str(path.resolve()))
        adapter.start()
        self.addCleanup(adapter.stop)

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
