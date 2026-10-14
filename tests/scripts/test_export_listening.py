"""Listening naming changes staging names, never source bytes or MDX binary names."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'py')]
from export_listening import run_listening_batch, safe_stem


def fixture(path):
    raw = bytearray(64)
    raw[:4] = b'Vgm '
    raw += b'\x62\x66'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return bytes(raw)


def core(source, out, **kwargs):
    folder = out / 'tracks' / source.name
    folder.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    mml = folder / (stem + '.mdx.mml')
    mml.write_text('#title "' + kwargs['_title'] + '"\n#pcm "' + stem + '.pdx"\n')
    mdx = folder / (stem + '.mdx')
    mdx.write_bytes((stem + '.pdx').encode('ascii'))
    pdx = folder / (stem + '.pdx')
    pdx.write_bytes(b'PDX')
    report = folder / (stem + '.report.txt')
    report.write_text('success')
    return [dict(input=source.name, status='success', detail='', compiler='typed_pcm_mmlx',
                 mml=str(mml.relative_to(out)), mdx=str(mdx.relative_to(out)),
                 pdx=str(pdx.relative_to(out)), report=str(report.relative_to(out)),
                 vgm='', report_status='success', report_error='')]


class ExportListeningTests(unittest.TestCase):
    def test_identical_unregistered_staging_is_adopted_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'a.vgm'
            raw = fixture(original)
            out = root / 'out'
            staged = out / '_source_inputs/a.vgm'
            staged.parent.mkdir(parents=True)
            staged.write_bytes(raw)
            from shutil import copyfile
            def checked_copy(source, target):
                self.assertNotEqual(Path(target), staged)
                return copyfile(source, target)
            with patch('export_listening.shutil.copyfile', side_effect=checked_copy):
                result = run_listening_batch(original, out, core=core, options={})[0]
            self.assertEqual(result['status'], 'success')
            self.assertEqual(staged.read_bytes(), raw)
            entry = json.loads((out / 'listening_manifest.json').read_text())['entries']['a.vgm']
            self.assertEqual(entry['staged_sha256'], entry['source_sha256'])

    def test_different_unregistered_and_modified_registered_staging_stay_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'a.vgm'
            raw = fixture(original)
            out = root / 'out'
            staged = out / '_source_inputs/a.vgm'
            staged.parent.mkdir(parents=True)
            staged.write_bytes(b'user content')
            with self.assertRaisesRegex(ValueError, 'unowned or modified staged source'):
                run_listening_batch(original, out, core=core, options={})
            self.assertEqual(staged.read_bytes(), b'user content')
            staged.write_bytes(raw)
            run_listening_batch(original, out, core=core, options={})
            staged.write_bytes(raw + b'changed')
            original.write_bytes(staged.read_bytes())
            with self.assertRaisesRegex(ValueError, 'unowned or modified staged source'):
                run_listening_batch(original, out, core=core, options={})

    def test_existing_portable_stems_are_preserved_and_reserved_names_are_hashed(self):
        for name in ('BOSCON06', 'CLOCK', 'GRA1_01', 'lower'):
            self.assertEqual(safe_stem(name + '.vgm'), name)
        for name in ('CON', 'nul', 'LPT1', 'COM9', 'long_fixture_name', 'extra.dot'):
            self.assertRegex(safe_stem(name + '.vgm'), '^[A-Z0-9]{8}$')
            self.assertNotEqual(safe_stem(name + '.vgm').casefold(), name.casefold())

    def test_short_single_extension_names_preserve_bytes_title_and_pdx_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'input' / 'long name.with.dots 日本語.vgm'
            raw = fixture(original)
            calls = []
            def checked(staged, out, **kwargs):
                calls.append((staged, kwargs))
                self.assertEqual(staged.read_bytes(), raw)
                self.assertEqual(kwargs['_title'], original.stem)
                return core(staged, out, **kwargs)
            out = root / 'output'
            rows = run_listening_batch(original, out, core=checked, options=dict(no_vgm=True))
            row = rows[0]
            stem = row['safe_stem']
            self.assertRegex(stem, '^[A-Z0-9]{8}$')
            self.assertEqual(stem, safe_stem(original.name))
            folder = out / 'tracks' / stem
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             sorted(stem + ext for ext in ('.mml', '.mdx', '.pdx', '.txt')))
            self.assertEqual((out / row['mdx']).read_bytes(), (stem + '.pdx').encode())
            self.assertIn(original.stem, (out / row['mml']).read_text())
            manifest = json.loads((out / 'listening_manifest.json').read_text())
            entry = manifest['entries'][original.name]
            self.assertEqual(entry['source_sha256'], row['source_sha256'])
            self.assertEqual((out / entry['staged_input']).read_bytes(), raw)
            self.assertEqual(entry['title'], original.stem)
            self.assertFalse(calls[0][1]['listening_layout'])

    def test_title_uses_original_gd3_not_short_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'a.vgm'
            fixture(original)
            with patch('export_listening.select_mdx_route', return_value='psg-scc-to-opm'), \
                    patch('export_listening.title_from_gd3', return_value='Original GD3 title') as title:
                row = run_listening_batch(original, root / 'out', core=core, options={})[0]
            title.assert_called_once_with(original, 'a', 'ja')
            self.assertIn('Original GD3 title', (root / 'out' / row['mml']).read_text())

    def test_native_gd3_title_policy_is_not_replaced_by_composed_title(self):
        from export_listening import original_title
        with patch('export_listening.inspect_source', return_value={}), \
                patch('export_listening.select_mdx_route', return_value='native-opm-pcm'), \
                patch('export_listening.read_gd3', return_value=['English track', '日本語曲名']), \
                patch('export_listening.title_from_gd3', side_effect=AssertionError('Wrong route title policy')):
            override, actual = original_title(Path('original.vgm'), 'mdx')
            self.assertIsNone(override)
            self.assertEqual(actual, '日本語曲名')

    def test_current_failure_removes_only_owned_previous_publications(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'a.vgm'
            fixture(original)
            out = root / 'out'
            row = run_listening_batch(original, out, core=core, options={})[0]
            mdx = out / row['mdx']
            keep = mdx.parent / 'user.txt'
            keep.write_text('keep')
            def failed(staged, diagnostic, **kwargs):
                self.assertTrue(mdx.exists())
                result = core(staged, diagnostic, **kwargs)[0]
                result['status'] = 'compilation_failed'
                return [result]
            result = run_listening_batch(original, out, core=failed, options={})[0]
            self.assertFalse(mdx.exists())
            self.assertEqual(result['mdx'], '')
            self.assertTrue(keep.exists())
            self.assertTrue((out / result['mml']).exists())

    def test_core_preflight_exception_preserves_publications_and_recovers_staging_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'a.vgm'
            fixture(original)
            out = root / 'out'
            row = run_listening_batch(original, out, core=core, options={})[0]
            manifest_path = out / 'listening_manifest.json'
            before = json.loads(manifest_path.read_text())['entries']['a.vgm']
            mdx = out / row['mdx']
            original.write_bytes(original.read_bytes() + b'new source bytes')
            for error in ('Missing generator', 'Invalid timeout'):
                with self.subTest(error=error):
                    with self.assertRaisesRegex(ValueError, error):
                        run_listening_batch(original, out, core=lambda *args, **kwargs:
                                            (_ for _ in ()).throw(ValueError(error)), options={})
                    after = json.loads(manifest_path.read_text())['entries']['a.vgm']
                    self.assertTrue(mdx.exists())
                    self.assertEqual(after['published'], before['published'])
                    self.assertEqual(after['source_sha256'], before['source_sha256'])
                    self.assertNotEqual(after['staged_sha256'], before['source_sha256'])
            retry = run_listening_batch(original, out, core=core, options={})[0]
            self.assertEqual(retry['status'], 'success')
            self.assertTrue((out / retry['mdx']).exists())

    def test_modified_or_unowned_artifacts_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'a.vgm'
            fixture(original)
            out = root / 'out'
            row = run_listening_batch(original, out, core=core, options={})[0]
            mdx = out / row['mdx']
            mdx.write_bytes(b'user change')
            with self.assertRaisesRegex(ValueError, 'modified listening artifact'):
                run_listening_batch(original, out, core=core, options={})
            self.assertEqual(mdx.read_bytes(), b'user change')
            (out / 'listening_manifest.json').unlink()
            with self.assertRaisesRegex(ValueError, 'unowned listening artifact'):
                run_listening_batch(original, out, core=core, options={})

    def test_casefold_name_collision_is_rejected_before_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root / 'input/a.vgm')
            fixture(root / 'input/b.vgm')
            with patch('export_listening.safe_stem', side_effect=['AB123456', 'ab123456']):
                with self.assertRaisesRegex(ValueError, 'filename collision'):
                    run_listening_batch(root / 'input', root / 'out', core=core, options={})


if __name__ == '__main__':
    unittest.main()
