"""Native compiler isolation, strict source encoding and output validation."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from mdx_compiler import compile_mxc, compiler_evidence_paths, prepare_mxc, restore_mxc_title


def minimal_mdx(title=b'', pdx=b''):
    # Offsets address the data section after the PDX name, not the title.
    data = bytes.fromhex('00150012') + b'\0\0' * 7 + b'\x7f\xf1\0'
    return title + b'\r\n\x1a' + pdx + b'\0' + data


class MxcCompilerTests(unittest.TestCase):
    def test_default_tools_work_without_outputs_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, options = self.prepare(root)
            for option, relative in (('mxc', '.tools/mxc/mxc.x'),
                                     ('run68', '.tools/run68x/build/run68')):
                installed = root / relative
                installed.parent.mkdir(parents=True, exist_ok=True)
                options.pop(option).replace(installed)
            self.assertFalse((root / 'outputs').exists())
            with patch('mdx_compiler.ROOT', root), \
                    patch('mdx_compiler.shutil.which', return_value=None), \
                    patch('mdx_compiler.subprocess.run', side_effect=self.successful):
                compile_mxc(source, output, **options)
            self.assertEqual(output.read_bytes(), minimal_mdx('曲'.encode('cp932')))

    def test_persistent_tools_take_priority_over_legacy_outputs(self):
        from mdx_compiler import _tool
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed, legacy = root / 'installed', root / 'legacy'
            for path in (installed, legacy):
                path.write_bytes(b'tool')
            with patch('mdx_compiler.shutil.which', return_value=None):
                self.assertEqual(_tool(None, ('mxc.x',), (installed, legacy), '--mxc'),
                                 installed.resolve())
                installed.unlink()
                self.assertEqual(_tool(None, ('mxc.x',), (installed, legacy), '--mxc'),
                                 legacy.resolve())
                with self.assertRaisesRegex(ValueError, 'Missing --mxc tool'):
                    _tool(installed, ('mxc.x',), (legacy,), '--mxc')

    def test_upper_octave_boundary_is_explicit_without_clamping(self):
        self.assertEqual(prepare_mxc('A o7 c4 > c4\n'), 'A o7 c4 o8 c4\n')
        self.assertEqual(prepare_mxc('A o6 c4 >> c4\n'), 'A o6 c4 >o8 c4\n')
        self.assertEqual(prepare_mxc('A o8 c4 > c4\n'), 'A o8 c4 > c4\n')
        self.assertEqual(prepare_mxc('A o7 c4 < c4 > c4\n'), 'A o7 c4 < c4 > c4\n')

    def test_octave_tracking_is_per_track_and_survives_wrapping(self):
        self.assertEqual(prepare_mxc('A o7 c4\nB o4 c4\nA > c4\nB > c4\n'),
                         'A o7 c4\nB o4 c4\nA o8 c4\nB > c4\n')
        self.assertEqual(prepare_mxc('A o7 c4 ; >\nA > c4\n'),
                         'A o7 c4 ; >\nA o8 c4\n')

    def test_unknown_and_loop_octaves_are_not_inferred(self):
        source = ('#title "o7 > c4"\nA > c4\nA o7 [c4 > c4]2 > c4\n'
                  'A [o7 c4 > c4 / > c4]2 > c4\n')
        self.assertEqual(prepare_mxc(source),
                         '#title "o7 > c4"\nA > c4\nA o7 [c4 > c4]2 > c4\n'
                         'A [o7 c4 o8 c4 / > c4]2 > c4\n')

    def test_music_ties_and_long_rests_preserve_order_and_ticks(self):
        source = 'A [c16 & c%32 & r%384]2 r%257\nA c4\nA & c4\n'
        prepared = prepare_mxc(source)
        self.assertEqual(prepared,
                         'A [c16& c%32& r%128 r%128 r%128]2 r%128 r%128 r%1\n'
                         'A c4& \nA  c4\n')
        self.assertEqual(source.count('c'), prepared.count('c'))
        self.assertIn(']2', prepared)

    def test_headers_comments_strings_and_voice_blocks_are_untouched(self):
        protected = ('#title "c16 & r%384"\n; A c16 & r%384\n'
                     '/* A c16 & r%384\nA c16 & r%384 */\n'
                     '@0 = {\nA c16 & r%384\n}\n')
        source = protected + 'A c16 & r%384 ; c16 & r%384\n'
        self.assertEqual(prepare_mxc(source), protected +
                         'A c16& r%128 r%128 r%128 ; c16 & r%384\n')
        self.assertEqual(prepare_mxc('A "r%384 &" c4 & /* r%384 & */ c4\n'),
                         'A "r%384 &" c4& /* r%384 & */ c4\n')

    def prepare(self, root):
        paths = [root / name for name in ('曲.mdx.mml', '曲.mdx', 'MXC.X', 'run68', 'helper')]
        source, output, mxc, run68, helper = paths
        source.write_text('#title "曲"\nA r4\n', encoding='utf-8')
        for tool in (mxc, run68, helper):
            tool.write_bytes(b'tool')
        return source, output, dict(mxc=mxc, run68=run68, generator=helper, timeout=17)

    def successful(self, command, **kwargs):
        workspace = Path(kwargs['cwd'])
        if Path(command[1]).name == 'MXC.X':
            self.assertEqual(Path(command[1]).parent, workspace)
            self.assertEqual(command[2], 'SCORE.MML')
            self.assertEqual((workspace / 'SCORE.MML').read_bytes(),
                             '#title "曲"\r\nA r4\r\n'.encode('cp932'))
            (workspace / 'SCORE.mdx').write_bytes(minimal_mdx('曲'.encode('cp932')))
        else:
            self.assertEqual(command[1], '--inspect-mdx')
            Path(command[3]).write_text('track,command\nA,rest\n')
        self.assertEqual(kwargs['timeout'], 17)
        return subprocess.CompletedProcess(command, 0, '', '')

    def test_short_encoded_source_and_validated_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self.prepare(Path(tmp))
            prepared = Path(tmp) / 'compiler_inputs/source.mxc.mml'
            with patch('mdx_compiler.subprocess.run', side_effect=self.successful) as calls:
                compile_mxc(source, output, prepared_output=prepared, **options)
            self.assertEqual(output.read_bytes(), minimal_mdx('曲'.encode('cp932')))
            self.assertEqual(prepared.read_bytes(), '#title "曲"\r\nA r4\r\n'.encode('cp932'))
            self.assertEqual(calls.call_count, 2)
            self.assertFalse(Path(calls.call_args_list[0].kwargs['cwd']).exists())
            native, metadata = compiler_evidence_paths(prepared)
            self.assertEqual(native.read_bytes(), output.read_bytes())
            report = json.loads(metadata.read_text(encoding='utf-8'))
            self.assertEqual(report['title_status'], 'unchanged')
            self.assertEqual(report['mdx_validation'], 'pass')
            self.assertEqual(report['native_arguments'], ['SCORE.MML'])
            self.assertEqual(report['source_encoding'], 'cp932')

    def test_title_boundary_uses_cp932_bytes_and_keeps_pdx_offsets_and_music(self):
        for title in ('A' * 64, '曲' * 32, 'A' * 65, '曲' * 32 + 'A'):
            encoded = title.encode('cp932')
            native_title = encoded if len(encoded) <= 64 else b''
            raw = minimal_mdx(native_title, b'SAMPLE.PDX')
            with self.subTest(title_bytes=len(encoded)):
                final, report = restore_mxc_title(raw, '#title "' + title + '"\nA r4\n')
                self.assertEqual(final, minimal_mdx(encoded, b'SAMPLE.PDX'))
                self.assertEqual(report['title_status'], 'unchanged' if len(encoded) <= 64 else 'restored')
                self.assertTrue(report['payload_unchanged'])
                before = raw.index(b'\r\n\x1a')
                after = final.index(b'\r\n\x1a')
                self.assertEqual(raw[before:], final[after:])

    def test_unexpected_title_mismatch_is_not_silently_repaired(self):
        for expected, native in (('Short', b''), ('A' * 65, b'Other'), ('Short', b'Other')):
            with self.subTest(expected=expected, native=native):
                with self.assertRaisesRegex(RuntimeError, 'unexpectedly differs'):
                    restore_mxc_title(minimal_mdx(native), '#title "' + expected + '"\n')

    def test_title_inside_comments_is_not_used_and_missing_title_is_preserved(self):
        raw = minimal_mdx(b'Native')
        final, report = restore_mxc_title(raw, '/*\n#title "Ignored"\n*/\n; #title "Ignored"\nA r4\n')
        self.assertEqual(final, raw)
        self.assertEqual(report['title_status'], 'not_requested')

    def test_malformed_header_is_rejected_before_title_repair(self):
        for raw in (b'not MDX', b'\r\n\x1aPDX', b'\r\n\x1a\0\0\0'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                restore_mxc_title(raw, '#title "' + 'A' * 65 + '"\n')

    def test_restored_title_is_validated_before_publication_with_native_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self.prepare(Path(tmp))
            title = '曲' * 32 + 'A'
            source.write_text('#title "' + title + '"\nA r4\n', encoding='utf-8')
            prepared = Path(tmp) / 'compiler_inputs/source.mxc.mml'
            raw = minimal_mdx(b'', b'PCM.PDX')
            def run(command, **kwargs):
                if Path(command[1]).name == 'MXC.X':
                    (Path(kwargs['cwd']) / 'SCORE.mdx').write_bytes(raw)
                else:
                    self.assertEqual(Path(command[2]).read_bytes(), minimal_mdx(title.encode('cp932'), b'PCM.PDX'))
                    Path(command[3]).write_text('track,command\nA,rest\n')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('mdx_compiler.subprocess.run', side_effect=run):
                compile_mxc(source, output, prepared_output=prepared, **options)
            native, metadata = compiler_evidence_paths(prepared)
            self.assertEqual(native.read_bytes(), raw)
            self.assertEqual(output.read_bytes(), minimal_mdx(title.encode('cp932'), b'PCM.PDX'))
            report = json.loads(metadata.read_text(encoding='utf-8'))
            self.assertEqual(report['title_status'], 'restored')
            self.assertEqual(report['mdx_validation'], 'pass')

    def test_partial_nonzero_and_zero_without_output_are_failures(self):
        for code in (0, 3):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                source, output, options = self.prepare(Path(tmp))
                def run(command, **kwargs):
                    if code:
                        (Path(kwargs['cwd']) / 'SCORE.mdx').write_bytes(b'partial')
                    return subprocess.CompletedProcess(command, code, '', 'compile diagnostic')
                with patch('mdx_compiler.subprocess.run', side_effect=run) as calls:
                    with self.assertRaises(RuntimeError):
                        compile_mxc(source, output, **options)
                self.assertFalse(output.exists())
                self.assertEqual(calls.call_count, 1)

    def test_parser_rejection_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self.prepare(Path(tmp))
            def run(command, **kwargs):
                if command[1] == '--inspect-mdx':
                    return subprocess.CompletedProcess(command, 1, '', 'invalid MDX')
                return self.successful(command, **kwargs)
            prepared = Path(tmp) / 'source.mxc.mml'
            with patch('mdx_compiler.subprocess.run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'invalid MDX'):
                    compile_mxc(source, output, prepared_output=prepared, **options)
            self.assertFalse(output.exists())
            _, metadata = compiler_evidence_paths(prepared)
            self.assertEqual(json.loads(metadata.read_text(encoding='utf-8'))['mdx_validation'], 'fail')

    def test_unrepresentable_source_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self.prepare(Path(tmp))
            source.write_text('#title "🙂"\nA r4\n', encoding='utf-8')
            with patch('mdx_compiler.subprocess.run') as calls:
                with self.assertRaises(UnicodeEncodeError):
                    compile_mxc(source, output, **options)
                calls.assert_not_called()
            self.assertFalse(output.exists())

    def test_missing_dependency_is_explicit_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self.prepare(Path(tmp))
            options['mxc'] = Path(tmp) / 'missing'
            with patch('mdx_compiler.subprocess.run') as calls:
                with self.assertRaisesRegex(ValueError, '--mxc'):
                    compile_mxc(source, output, **options)
                calls.assert_not_called()

    def test_native_and_loader_diagnostics_use_their_respective_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self.prepare(Path(tmp))
            result = subprocess.CompletedProcess([], 1, '音色エラー'.encode('cp932'),
                                                 'loader error: 曲'.encode('utf-8'))
            with patch('mdx_compiler.subprocess.run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, '音色エラーloader error: 曲'):
                    compile_mxc(source, output, **options)


if __name__ == '__main__':
    unittest.main()
