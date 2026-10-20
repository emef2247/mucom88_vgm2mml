"""Authored MUCOMSX installer/job safety checks; no DOS or private songs."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import setup_mucomsx as setup
import prepare_mucomsx as prepare


def archive(extra=None):
    files = {name: ('authored tool stub ' + name).encode() for name in setup.PROGRAMS}
    files.update({'NOTICE.txt': b'authored notice stub', 'FMVOICE.DAT': b'factory bytes',
                  'LICENSES/MUCOM88-CC-BY-NC-SA-4.0.txt': b'authored test placeholder'})
    files.update(extra or {})
    manifest = dict(package_kind='runtime', files={name: dict(bytes=len(data), sha256=setup.sha256(data))
                                                  for name, data in files.items()})
    files['manifest.json'] = json.dumps(manifest).encode()
    files['SHA256SUMS.txt'] = ''.join(setup.sha256(data) + '  ' + name + '\n'
                                     for name, data in files.items()).encode('ascii')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return stream.getvalue()


class InstallerTests(unittest.TestCase):
    def test_bad_download_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as name:
            dest = Path(name); (dest / 'FMVOICE.DAT').write_bytes(b'my edited voice')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                setup.install(b'bad archive', dest)
            self.assertEqual((dest / 'FMVOICE.DAT').read_bytes(), b'my edited voice')
            self.assertEqual(len(list(dest.iterdir())), 1)

    def test_pinned_archive_install_is_idempotent_and_protects_edits(self):
        data = archive(); digest = setup.sha256(data)
        with tempfile.TemporaryDirectory() as name:
            dest = Path(name)
            setup.install(data, dest, digest)
            setup.install(data, dest, digest)
            edited = dest / 'FMVOICE.DAT'; edited.write_bytes(b'edited')
            (dest / 'MUCPLAY.COM').unlink()
            with self.assertRaisesRegex(ValueError, 'modified'):
                setup.install(data, dest, digest)
            self.assertFalse((dest / 'MUCPLAY.COM').exists())
            self.assertEqual(edited.read_bytes(), b'edited')

    def test_member_cannot_escape_even_with_matching_archive_digest(self):
        data = archive({'../outside.txt': b'bad'})
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            setup.archive_files(data, setup.sha256(data))

    def test_nextor_kernel_is_an_alternative_not_a_claim_of_execution(self):
        with tempfile.TemporaryDirectory() as name:
            dest = Path(name)
            for filename in ('COMMAND2.COM', 'NEXTOR.SYS'):
                (dest / filename).write_bytes(b'test')
            report = setup.environment_report(dest, dest)
            self.assertEqual(report['missing_dos_files'], [])
            self.assertEqual(report['dos_kernel'], 'NEXTOR.SYS')
            self.assertFalse(report['runtime_executed'])


class StagingTests(unittest.TestCase):
    def job(self, root, score):
        source = root / 'long source name.muc'; source.write_text(score, encoding='cp932')
        tools = root / 'tools'; tools.mkdir()
        files = {'MUC2MUB.COM': b'compiler stub', 'MUCPLAY.COM': b'player stub',
                 'NOTICE.txt': b'notice', 'LICENSES/NOTICE.txt': b'license'}
        for name, data in files.items():
            p = tools / name; p.parent.mkdir(exist_ok=True); p.write_bytes(data)
        (tools / 'source.json').write_text(json.dumps({'files': list(files)}))
        return source, tools

    @patch.object(prepare, 'verify_installation')
    def test_local_assets_get_unique_83_names_and_original_is_unchanged(self, verify):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source, tools = self.job(root, '#voice LongVoiceName.dat\nD o4 c4\n')
            (root / 'LongVoiceName.dat').write_bytes(b'my voice')
            original = source.read_bytes()
            report = prepare.stage_score(source, root / 'job', tools)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((root / 'job/A00.DAT').read_bytes(), b'my voice')
            self.assertIn(b'#voice A00.DAT\r\n', (root / 'job/SOURCE.MUC').read_bytes())
            batch = (root / 'job/RUN.BTM').read_bytes()
            self.assertLess(batch.index(b'/LOAD'), batch.index(b'/COMPILE'))
            self.assertIn(b'/RELEASE', batch)
            self.assertEqual(report['status'], 'staged_not_executed')
            self.assertTrue((root / 'job/LICENSES/NOTICE.txt').is_file())
            with self.assertRaisesRegex(ValueError, 'empty'):
                prepare.stage_score(source, root / 'job', tools)

    @patch.object(prepare, 'verify_installation')
    def test_compile_only_job_does_not_load_play_or_export_vgm(self, verify):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source, tools = self.job(root, 'MSXD o4 c4\n')
            report = prepare.stage_score(source, root / 'job', tools, compile_only=True)
            batch = (root / 'job/RUN.BTM').read_text()
            self.assertIn('MUC2MUB SOURCE.MUC CHECK.MUB\nIF ERRORLEVEL 1 GOTO FAIL', batch)
            self.assertNotIn('MUCPLAY', batch)
            self.assertNotIn('CHECK.VGM', batch)
            self.assertEqual(report['expected_artifacts'], ['CHECK.MUB', 'DONE.OK'])

    @patch.object(prepare, 'verify_installation')
    def test_missing_or_escaping_asset_does_not_create_job(self, verify):
        for asset in ('missing.dat', '../elsewhere.dat'):
            with self.subTest(asset=asset), tempfile.TemporaryDirectory() as name:
                root = Path(name); source, tools = self.job(root, '#voice ' + asset + '\nD c4\n')
                with self.assertRaises(ValueError):
                    prepare.stage_score(source, root / 'job', tools)
                self.assertFalse((root / 'job').exists())

    @patch.object(prepare, 'verify_installation')
    def test_implicit_banks_are_source_owned_and_missing_fm_bank_is_an_error(self, verify):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source, tools = self.job(root, 'A @1 o4 c4\nK @1 o4 c4\n')
            with self.assertRaisesRegex(ValueError, 'Implicit VOICE.DAT'):
                prepare.stage_score(source, root / 'missing', tools)
            (root / 'VOICE.DAT').write_bytes(b'original voice bank')
            (root / 'MUCOMPCM.BIN').write_bytes(b'original pcm bank')
            report = prepare.stage_score(source, root / 'job', tools)
            self.assertEqual((root / 'job/VOICE.DAT').read_bytes(), b'original voice bank')
            self.assertEqual((root / 'job/MUCOMPCM.BIN').read_bytes(), b'original pcm bank')
            self.assertTrue(all(dep['implicit'] for dep in report['dependencies']))
            batch = (root / 'job/RUN.BTM').read_text()
            self.assertIn('IF EXIST DONE.OK DEL DONE.OK', batch)
            self.assertIn('MUCPLAY /RELEASE\nIF ERRORLEVEL 1 GOTO FAIL', batch)

    @patch.object(prepare, 'verify_installation')
    def test_dos_newlines_count_toward_input_limit(self, verify):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source, tools = self.job(root, ';' + 'x' * 998 + '\n')
            source.write_text((';' + 'x' * 998 + '\n') * 66, encoding='cp932')
            with self.assertRaisesRegex(ValueError, '65536'):
                prepare.stage_score(source, root / 'job', tools)
            self.assertFalse((root / 'job').exists())


if __name__ == '__main__':
    unittest.main()
