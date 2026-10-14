"""Independent MDX decompiler evidence, not an exact-text VGM conversion oracle."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'py'))
from decompile_mdx_references import audit_structure, decompile
from opm import build_segments
from vgm_reader import parse_vgm

FIXTURES = ROOT / 'tests/fixtures/public/opm/mdx_decompiler'


class DecompilerEvidenceTests(unittest.TestCase):
    def test_nested_repeat_counts_and_exit_attachment_are_compared(self):
        mml = '/* [ ignored ] */\nA [[c16/d16]3 e16]4 L f16\n'
        dump = ('RepeatStart 4 0\nRepeatStart 3 0\nRepeatEscape 4\n'
                'RepeatEnd -9\nRepeatEnd -15\nPerformanceEnd -4\n')
        self.assertEqual(audit_structure(mml, dump)['issues'], [])
        self.assertIn('repeat nesting/count/exit mismatch',
                      audit_structure(mml.replace(']3', ']2'), dump)['issues'])
        self.assertIn('song loop marker count mismatch',
                      audit_structure(mml.replace(' L ', ' '), dump)['issues'])

    def test_unclosed_repeat_and_lossy_commands_need_review(self):
        result = audit_structure('A [c4\n', 'RepeatStart 3 0\nFadeOut 8\n')
        self.assertTrue(result['mml']['errors'])
        self.assertTrue(result['mdx_dump']['errors'])
        self.assertTrue(any('FadeOut' in issue for issue in result['issues']))

    def test_failed_tool_preserves_partial_output_without_trusting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input.mdx'
            source.write_bytes(b'original input')
            partial = b'#title "partial"\n/* Track A */\nA c4\n'
            with patch('decompile_mdx_references.subprocess.run', side_effect=[
                    subprocess.CompletedProcess([], 2, partial, b'decode error'),
                    subprocess.CompletedProcess([], 0, b'', b'')]):
                result = decompile(source, root / 'evidence', Path('mdx2mml'), Path('mdxdump'))
            self.assertEqual(result['status'], 'tool_failed')
            self.assertEqual((root / 'evidence/decompiled.mml').read_bytes(), partial)
            self.assertEqual((root / 'evidence/mdx2mml.stderr').read_bytes(), b'decode error')
            self.assertEqual(source.read_bytes(), b'original input')

    def test_timeout_preserves_output_and_empty_success_needs_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input.mdx'
            source.write_bytes(b'input')
            with patch('decompile_mdx_references.subprocess.run', side_effect=[
                    subprocess.TimeoutExpired([], 1, output=b'partial', stderr=b'waiting'),
                    subprocess.CompletedProcess([], 0, b'', b'')]):
                result = decompile(source, root / 'timeout', Path('mdx2mml'), Path('mdxdump'))
            self.assertEqual(result['mdx2mml']['status'], 'timeout')
            self.assertEqual((root / 'timeout/decompiled.mml').read_bytes(), b'partial')
            with patch('decompile_mdx_references.subprocess.run', return_value=
                       subprocess.CompletedProcess([], 0, b'', b'')):
                result = decompile(source, root / 'empty', Path('mdx2mml'), Path('mdxdump'))
            self.assertEqual(result['status'], 'needs_review')

    def test_empty_successful_dump_cannot_certify_repeat_free_mml(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input.mdx'
            source.write_bytes(b'input')
            mml = b'#title "valid-looking"\n/* Track A */\nA c4\n'
            for dump in (b'', b'unrecognized output\n', b'title test\npdxfile \n'):
                with self.subTest(dump=dump), patch('decompile_mdx_references.subprocess.run', side_effect=[
                        subprocess.CompletedProcess([], 0, mml, b''),
                        subprocess.CompletedProcess([], 0, dump, b'')]):
                    result = decompile(source, root / 'evidence', Path('mdx2mml'), Path('mdxdump'))
                self.assertEqual(result['status'], 'needs_review')
                self.assertIn('mdxdump', result['error'])


class PublicDecompilerFixtureTests(unittest.TestCase):
    def test_encoded_structures_and_expanded_vgm_attacks(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            for name, expected in manifest['cases'].items():
                with self.subTest(case=name):
                    for relative, digest in expected['sha256'].items():
                        self.assertEqual(hashlib.sha256((FIXTURES / relative).read_bytes()).hexdigest(), digest)
                    folder = FIXTURES / name
                    reference = folder / 'reference'
                    mml = (reference / (name + '.mdxtools.mml')).read_text(encoding='utf-8')
                    dump = (reference / (name + '.mdxdump.txt')).read_text(encoding='utf-8')
                    self.assertEqual(audit_structure(mml, dump)['issues'], [])
                    metadata = {}
                    parse_vgm(str(folder / (name + '.vgm')), str(Path(temp) / name), opm_metadata=metadata)
                    analysis = build_segments(metadata['csv_path'], end_vgmticks=metadata['source_end_vgmticks'])
                    attacks = [e for e in analysis.events if e.rising_mask]
                    self.assertEqual(len(attacks), expected['channel_attack_events'])
                    # Keep compiled MDX note numbers and observed KC bytes distinct.
                    self.assertEqual([e.state.kc_raw for e in attacks], expected['attack_kc_raw'])
                    # @t216 at 4 MHz: one tick is 40 * 1024 chip clock cycles.
                    samples = lambda tick: tick * 40 * 1024 * 44100 // 4000000
                    self.assertEqual([e.vgmticks for e in attacks],
                                     [samples(t) for t in expected['attack_mdx_ticks']])
                    self.assertEqual(analysis.source_end_vgmticks, samples(expected['end_mdx_tick']))

    def test_song_loop_displacement_is_relative_to_command_end(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))
        expected = manifest['cases']['song_loop']
        track = bytes.fromhex(expected['track_a_hex'])
        loop = expected['song_loop']
        pos = loop['command_offset']
        self.assertEqual(track[pos], 0xf1)
        self.assertEqual(int.from_bytes(track[pos + 1:pos + 3], 'big', signed=True), loop['displacement'])
        self.assertEqual(pos + 3 + loop['displacement'], loop['target_offset'])
        self.assertEqual(track[loop['target_offset']:loop['target_offset'] + 3], bytes([0xf6, 3, 0]))
        mml = (FIXTURES / 'song_loop/reference/song_loop.mdxtools.mml').read_text(encoding='utf-8')
        self.assertIn(' L [', mml)


if __name__ == '__main__':
    unittest.main()
