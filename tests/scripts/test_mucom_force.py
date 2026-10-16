"""Authored registry/frontend checks; no native compiler or private conversion."""
import contextlib
import csv
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
sys.path.insert(0,str(ROOT/'py'))
sys.path.insert(0,str(ROOT/'scripts'))
from mucom_force import dump, console_lines
from export_mucom import run_batch


class MucomForceTest(unittest.TestCase):
    def test_registry_preserves_details_and_groups_console(self):
        rows=[dict(part='K',source_kind='msm6258',action='omit_pcm_chunk',reason='bank_payload_bytes',
            source_gate_ids=[1],source_event_ids=[2,3],actual=40,limit=20,unit='bytes',extra='evidence')]*2
        with tempfile.TemporaryDirectory() as directory:
            summary=dump(rows,directory,'tiny',True)
            self.assertEqual(summary['omission_count'],2)
            saved=json.loads((Path(directory)/'tiny.mucom.force.json').read_text())
            self.assertEqual(saved['omissions'][0]['details_json']['extra'],'evidence')
            with (Path(directory)/'tiny.mucom.force.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))),2)
            text='\n'.join(console_lines(directory,'tiny'))
            self.assertEqual(text.count('bank_payload_bytes'),1)
            self.assertIn('40/20 bytes',text)
            self.assertEqual(console_lines(directory,'absent'),[])

    def test_native_cli_forwards_force_and_default(self):
        spec=importlib.util.spec_from_file_location('authored_mucom_cli',ROOT/'vgm2mml.py')
        cli=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        result=dict(muc_path='tiny.muc',musical_note_units=0,timing=dict(timer_b=200))
        for enabled in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                args=['tiny.vgm','--outdir',directory]+(['--force'] if enabled else [])
                with patch('opna_muc.convert_vgm',return_value=result) as convert, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(args),0)
                self.assertEqual(convert.call_args.kwargs['force'],enabled)

    def test_batch_force_diagnostics_survive_compilation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'tiny.vgm';source.write_bytes(b'authored')
            def convert(command,**kwargs):
                self.assertIn('--force',command)
                folder=Path(command[command.index('--outdir')+1])
                (folder/'tiny.muc').write_bytes(b'A c4\n')
                dump([dict(part='K',source_kind='msm6258',action='omit_pcm_chunk',reason='sample_slot_count',
                    actual=33,limit=32,unit='slots')],folder,'tiny',True)
                return subprocess.CompletedProcess(command,0,'','')
            with patch('export_mucom.subprocess.run',side_effect=convert), \
                    patch('export_mucom.compile_muc',side_effect=RuntimeError('compiler limit')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                row=run_batch(source,root/'out',force=True)[0]
            self.assertEqual(row['status'],'compilation_failed')
            self.assertIn('sample_slot_count',output.getvalue())
            self.assertIn('33/32 slots',(root/'out'/row['report']).read_text())


if __name__=='__main__':
    unittest.main()
