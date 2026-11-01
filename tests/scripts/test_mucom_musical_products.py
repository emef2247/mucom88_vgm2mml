"""Authored musical-product publication checks without compilers or private data."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'py'))
import mucom_musical_products as products


def source_vgm():
    raw = bytearray(256)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x34, 256-0x34)
    struct.pack_into('<I', raw, 0x30, 4000000)
    raw.extend(bytes.fromhex('54 20 c7 54 28 4a 54 08 78 61 11 00 54 08 00 66'))
    struct.pack_into('<I', raw, 4, len(raw)-4)
    return bytes(raw)


def bundle(version=b'first'):
    return [dict(relative='blueprint/song'+extension, data=version+extension.encode(), kind=kind)
            for extension, kind in (('.muc','blueprint'),('.midi','blueprint_midi'),('.dat','blueprint_voice'))]


class MusicalProductsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'input.vgm'
        self.source.write_bytes(source_vgm())
        self.output = self.root/'output'
        self.renderer = SimpleNamespace(render=lambda *args, **kwargs: (bundle(), {'parts':1}))
        self.mock = patch.dict(sys.modules, {'mucom_blueprint': self.renderer})
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def prepare(self, **kwargs):
        return products.prepare(self.source, self.output, 'song', **kwargs)

    def test_success_and_optional_normalized_input_keep_original_evidence(self):
        report = self.prepare(score_normalize=True, score_guided_loops=True)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(len(report['products']), 4)
        self.assertEqual(report['products'][0]['relative'], 'tracks/song.vgm')
        self.assertTrue(Path(report['conversion_input']).is_file())
        evidence = json.loads((self.output/'_musical_source/song/song.native_music.json').read_text())
        self.assertEqual(evidence['end_sample'], 17)
        self.assertEqual(self.source.read_bytes(), source_vgm())
        self.assertEqual(json.loads((self.output/'song.musical_layer.json').read_text())['status'], 'passed')

    def test_optional_normalized_input_preserves_requested_source_song_loop(self):
        import struct
        raw=bytearray(source_vgm())
        struct.pack_into('<I',raw,0x1c,0x100-0x1c)
        self.source.write_bytes(raw)
        report=self.prepare(score_normalize=True)
        self.assertEqual(report['native_reference']['status'],'passed')
        rebuilt=Path(report['conversion_input']).read_bytes()
        self.assertNotEqual(int.from_bytes(rebuilt[0x1c:0x20],'little'),0)
        self.assertTrue(all(d['reason']=='source_song_loop_requires_shared_boundary_validation'
                            for d in report['normalization']['devices']))

    def test_blueprint_failure_keeps_reference(self):
        def fail(*args, **kwargs):
            raise ValueError('authored blueprint failure')
        self.renderer.render = fail
        report = self.prepare()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['native_reference']['status'], 'passed')
        self.assertEqual(report['blueprint']['status'], 'failed')
        self.assertEqual(len(report['products']), 1)

    def test_reference_collision_keeps_blueprint(self):
        self.output.mkdir()
        destination = self.output/'song.vgm'
        destination.write_bytes(b'unowned')
        report = self.prepare(force=True)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['native_reference']['status'], 'failed')
        self.assertEqual(report['blueprint']['status'], 'passed')
        self.assertEqual(destination.read_bytes(), b'unowned')

    def test_edited_companion_protects_whole_bundle_even_force(self):
        self.assertEqual(self.prepare()['status'], 'passed')
        edited = self.output/'blueprint/song.midi'
        edited.write_bytes(b'user edit')
        before = {p.name:p.read_bytes() for p in (self.output/'blueprint').iterdir()}
        self.renderer.render = lambda *args, **kwargs: (bundle(b'second'), {})
        report = self.prepare(force=True)
        self.assertEqual(report['blueprint']['status'], 'failed')
        self.assertEqual(report['native_reference']['status'], 'passed')
        self.assertEqual({p.name:p.read_bytes() for p in (self.output/'blueprint').iterdir()}, before)
        self.assertFalse(any(p['kind'].startswith('blueprint') for p in report['products']))

    def test_failed_bundle_rename_rolls_back_and_retry_succeeds(self):
        self.prepare()
        manifest = self.output/'song.musical_products.ownership.json'
        old = json.loads(manifest.read_text())['blueprint']
        before = {p.name:p.read_bytes() for p in (self.output/'blueprint').iterdir()}
        self.renderer.render = lambda *args, **kwargs: (bundle(b'second'), {})
        replace = os.replace
        def fail(source, destination):
            if Path(destination).name == 'song.midi':
                raise OSError('authored rename failure')
            return replace(source, destination)
        with patch.object(products.os, 'replace', side_effect=fail):
            report = self.prepare()
        self.assertEqual(report['blueprint']['status'], 'failed')
        self.assertEqual(json.loads(manifest.read_text())['blueprint'], old)
        self.assertEqual({p.name:p.read_bytes() for p in (self.output/'blueprint').iterdir()}, before)
        self.assertEqual(self.prepare()['blueprint']['status'], 'passed')

    def test_manifest_rename_failure_preserves_files_and_previous_hashes(self):
        self.prepare()
        manifest = self.output/'song.musical_products.ownership.json'
        before_manifest = manifest.read_bytes()
        before = {p.name:p.read_bytes() for p in (self.output/'blueprint').iterdir()}
        self.renderer.render = lambda *args, **kwargs: (bundle(b'second'), {})
        replace = os.replace
        def fail(source, destination):
            if Path(destination) == manifest:
                raise OSError('authored manifest failure')
            return replace(source, destination)
        with patch.object(products.os, 'replace', side_effect=fail):
            report = self.prepare()
        self.assertEqual(report['native_reference']['status'], 'failed')
        self.assertEqual(report['blueprint']['status'], 'failed')
        self.assertEqual(manifest.read_bytes(), before_manifest)
        self.assertEqual({p.name:p.read_bytes() for p in (self.output/'blueprint').iterdir()}, before)
        self.assertEqual(self.prepare()['status'], 'passed')

    def test_rollback_failure_keeps_durable_copies_and_restores_other_files(self):
        self.prepare()
        self.renderer.render = lambda *args, **kwargs: (bundle(b'second'), {})
        original_replace = os.replace
        original_write = Path.write_bytes
        def replace(source, destination):
            if Path(destination).name == 'song.dat':
                raise OSError('publication failure')
            return original_replace(source, destination)
        def write(path, data):
            if path == self.output/'blueprint/song.muc' and data.startswith(b'first'):
                raise OSError('rollback failure')
            return original_write(path, data)
        with patch.object(products.os, 'replace', side_effect=replace), patch.object(Path, 'write_bytes', write):
            report = self.prepare()
        self.assertEqual(report['blueprint']['status'], 'failed')
        self.assertIn('Rollback incomplete', report['blueprint']['detail'])
        self.assertEqual((self.output/'blueprint/song.midi').read_bytes(), bundle()[1]['data'])
        folders = list(self.output.glob('.publication-recovery-*'))
        self.assertEqual(len(folders), 1)
        records = json.loads((folders[0]/'recovery.json').read_text())
        record = next(r for r in records if r['destination'].endswith('song.muc'))
        self.assertEqual((folders[0]/record['backup']).read_bytes(), bundle()[0]['data'])

    def test_reduced_bundle_removes_only_owned_stale_companions(self):
        self.prepare()
        unowned = self.output/'blueprint/user.muc'
        unowned.write_bytes(b'user file')
        self.renderer.render = lambda *args, **kwargs: (bundle(b'second')[:1], {})
        report = self.prepare()
        self.assertEqual(report['blueprint']['status'], 'passed')
        self.assertFalse((self.output/'blueprint/song.midi').exists())
        self.assertFalse((self.output/'blueprint/song.dat').exists())
        self.assertEqual(unowned.read_bytes(), b'user file')

    def test_source_failure_report_and_input_filename_guard(self):
        with patch.object(products, 'build_native_performance', side_effect=ValueError('authored source failure')):
            report = self.prepare()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['products'], [])
        with self.assertRaises(ValueError):
            products.prepare(self.source, self.output, '../input')
        report = products.prepare(self.source, self.root, 'input')
        self.assertEqual(report['native_reference']['status'], 'failed')
        self.assertEqual(self.source.read_bytes(), source_vgm())


if __name__ == '__main__':
    unittest.main()
