"""Profile dispatch and independent MUC-blueprint publication contracts."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'py'), str(ROOT/'scripts'), str(ROOT/'tests/scripts')]
import export_mucom as batch
from mucom_profiles import get_target_profile, target_names
import test_export_mucom as fixtures

spec = importlib.util.spec_from_file_location('profile_cli_entry', ROOT/'vgm2mml.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def convert_blueprint(command, **kwargs):
    result = fixtures.SourceProductTests.convert_products(command, **kwargs)
    folder = Path(command[command.index('--outdir')+1])
    report = folder/(Path(command[2]).stem+'.musical_layer.json')
    data = json.loads(report.read_text())
    data.update(status='passed', native_reference={'status':'passed'}, blueprint={'status':'passed'})
    report.write_text(json.dumps(data))
    return result


class MucomProfileTests(unittest.TestCase):
    def test_registry_keeps_requested_target_separate_from_blueprint_dialect(self):
        self.assertEqual(target_names(), ('mucom','mucomsx','mucomsx-opn','md'))
        self.assertTrue(all(get_target_profile(name).blueprint_dialect=='mucom88' for name in target_names()))
        with self.assertRaises(ValueError):
            get_target_profile('invented')

    def test_generation_only_profiles_require_explicit_muc_only_before_mutation(self):
        for target in ('md','mucomsx-opn'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored')
                with patch.object(batch.subprocess,'run') as converter, self.assertRaisesRegex(ValueError,'--muc-only'):
                    batch.run_batch(source,root/'out',target=target)
                converter.assert_not_called()
                self.assertFalse((root/'out').exists())

    def test_generation_only_profiles_forward_dialect_without_wrong_compiler(self):
        for target in ('md','mucomsx-opn'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored')
                with patch.object(batch.subprocess,'run',side_effect=fixtures.convert) as converter, \
                        patch.object(batch,'compile_muc') as compiler, patch.object(batch,'compile_mucomsx') as sx:
                    row=batch.run_batch(source,root/'out',target=target,muc_only=True)[0]
                self.assertEqual(row['status'],'muc_only')
                self.assertEqual(row['target_profile'],target)
                command=converter.call_args.args[0]
                self.assertEqual(command[command.index('--target')+1],target)
                self.assertNotIn('--pcm-policy',command)
                self.assertNotIn('--scc-output',command)
                compiler.assert_not_called();sx.assert_not_called()

    def test_cli_source_only_bypasses_target_routing_for_every_profile(self):
        report=dict(source={'status':'passed'},native_reference={'status':'passed'},blueprint={'status':'passed'})
        for target in target_names():
            with self.subTest(target=target), patch('mucom_musical_products.prepare',return_value=report) as prepare, \
                    patch('conversion_config.inspect_source',side_effect=AssertionError('production routing called')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(entry.main(['authored.vgm','--target',target,'--blueprint-only']),0)
                self.assertEqual(prepare.call_args.kwargs['target_profile'],target)

    def test_source_only_returned_stage_failure_is_not_success(self):
        for failed in ('source','native_reference','blueprint'):
            report={key:{'status':'passed'} for key in ('source','native_reference','blueprint')}
            report[failed]={'status':'failed','detail':'authored failure'}
            with self.subTest(failed=failed), patch('mucom_musical_products.prepare',return_value=report), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(entry.main(['authored.vgm','--blueprint-only']),1)

    def test_source_only_preserves_edited_production_and_manifest_for_every_profile(self):
        for target in target_names():
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored')
                out=root/'out'
                with patch.object(batch.subprocess,'run',side_effect=fixtures.convert), \
                        patch.object(batch,'compile_muc',side_effect=fixtures.compile_score):
                    batch.run_batch(source,out)
                manifest=out/'listening_manifest.json'
                before=json.loads(manifest.read_text())['entries']['song.vgm']
                production=out/'tracks/song.muc';production.write_bytes(b'user edited production')
                with patch.object(batch.subprocess,'run',side_effect=convert_blueprint) as converter, \
                        patch.object(batch,'compile_muc') as compiler,patch.object(batch,'compile_mucomsx') as sx:
                    row=batch.run_batch(source,out,target=target,blueprint_only=True)[0]
                self.assertEqual(row['status'],'blueprint_only')
                self.assertEqual(row['blueprint_dialect'],'mucom88')
                self.assertEqual(row['source_products_status'],'published')
                self.assertFalse(row['muc']);self.assertFalse(row['mub'])
                self.assertTrue((out/row['blueprint']).is_file())
                self.assertEqual(production.read_bytes(),b'user edited production')
                after=json.loads(manifest.read_text())['entries']['song.vgm']
                for field in ('published','published_source_sha256','status','source_sha256'):
                    self.assertEqual(after.get(field),before.get(field),field)
                self.assertEqual(after['latest_attempt_status'],'blueprint_only')
                self.assertIn('--blueprint-only',converter.call_args.args[0])
                compiler.assert_not_called();sx.assert_not_called()

    def test_source_only_publication_failure_fails_cli_and_preserves_user_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored');out=root/'out'
            with patch.object(batch.subprocess,'run',side_effect=convert_blueprint):
                batch.run_batch(source,out,blueprint_only=True)
            blueprint=out/'blueprint/song.part01.muc';blueprint.write_bytes(b'user blueprint')
            with patch.object(batch.subprocess,'run',side_effect=convert_blueprint), \
                    patch.object(sys,'argv',['export_mucom.py',str(source),'--outdir',str(out),'--blueprint-only']):
                self.assertEqual(batch.main(),1)
            self.assertEqual(blueprint.read_bytes(),b'user blueprint')
            row=json.loads((out/'_diagnostics/results.json').read_text())[0]
            self.assertEqual(row['status'],'publication_failed')

    def test_pcm_only_source_explicitly_reports_no_blueprint(self):
        report=dict(source={'status':'passed'},native_reference={'status':'passed'},
                    blueprint={'status':'not_applicable','reason':'PCM has no score'})
        with patch('mucom_musical_products.prepare',return_value=report), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['authored.vgm','--blueprint-only']),0)

    def test_source_only_rejects_compiler_and_conflicting_modes_before_mutation(self):
        for options in (dict(muc_only=True),dict(pcm_only=True),dict(mucom88='unused'),dict(runtime_profile='unused')):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored')
                with self.assertRaises(ValueError):
                    batch.run_batch(source,root/'out',blueprint_only=True,**options)
                self.assertFalse((root/'out').exists())

    def test_successful_empty_blueprint_retires_old_published_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored');out=root/'out'
            with patch.object(batch.subprocess,'run',side_effect=convert_blueprint):
                batch.run_batch(source,out,blueprint_only=True)
            def pcm_only(command,**kwargs):
                result=convert_blueprint(command,**kwargs)
                directory=Path(command[command.index('--outdir')+1])
                report=directory/'song.musical_layer.json';data=json.loads(report.read_text())
                data['blueprint']={'status':'not_applicable','reason':'PCM-only'}
                data['products']=[p for p in data['products'] if p['kind']=='native_reference']
                report.write_text(json.dumps(data));return result
            with patch.object(batch.subprocess,'run',side_effect=pcm_only):
                row=batch.run_batch(source,out,blueprint_only=True)[0]
            self.assertEqual(row['status'],'blueprint_only')
            self.assertEqual(row['blueprint_status'],'not_applicable')
            self.assertFalse((out/'blueprint/song.part01.muc').exists())
            ownership=json.loads((out/'listening_manifest.json').read_text())['entries']['song.vgm']['source_products']
            self.assertEqual(set(ownership),{'tracks/song.vgm'})

    def test_source_only_manifest_failure_preserves_production_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'song.vgm';source.write_bytes(b'authored');out=root/'out'
            with patch.object(batch.subprocess,'run',side_effect=fixtures.convert),patch.object(batch,'compile_muc',side_effect=fixtures.compile_score):
                batch.run_batch(source,out)
            before=json.loads((out/'listening_manifest.json').read_text())['entries']['song.vgm']
            original_save=batch._atomic_json
            failed=False
            def save(path,data):
                nonlocal failed
                current=data.get('entries',{}).get('song.vgm',{}) if isinstance(data,dict) else {}
                if path.name=='listening_manifest.json' and current.get('source_products') and not failed:
                    failed=True;raise OSError('authored source manifest failure')
                return original_save(path,data)
            with patch.object(batch.subprocess,'run',side_effect=convert_blueprint),patch.object(batch,'_atomic_json',side_effect=save):
                row=batch.run_batch(source,out,blueprint_only=True)[0]
            self.assertEqual(row['status'],'publication_failed')
            after=json.loads((out/'listening_manifest.json').read_text())['entries']['song.vgm']
            self.assertEqual(after['published'],before['published'])
            self.assertEqual(after['status'],before['status'])
            self.assertEqual((out/'tracks/song.mub').read_bytes(),b'compiled MUB')
            self.assertFalse((out/'blueprint/song.part01.muc').exists())


if __name__=='__main__':
    unittest.main()
