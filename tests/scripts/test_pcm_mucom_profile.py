"""Authored low-rate target packaging checks; no compiler/private regression."""
import contextlib
from fractions import Fraction
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts')]
from pcm_mucom import _project, project_input, TARGET_RATE, DELTA_N, _chunk_boundaries, MAX_SAMPLE_BYTES
from mucom_aux import TARGET_CLOCK_HZ, PCM_N, NOTES
from opna_muc import convert_vgm
from export_mucom import run_batch

class PcmLowProfileTests(unittest.TestCase):
    def test_half_rate_bytes_pitch_duration_and_no_global_mutation(self):
        row=dict(start=0,end=4410,samples=[1000,-1000]*5000,rate=TARGET_RATE,
            origin=0,pan=3,source_ids=(7,),events=(12,),exhaustion_clipped=False)
        def project(profile):
            return _project(4410,[row],dict(source_kind='msm6258',losses=[]),None,pcm_profile=profile)
        standard=project('standard');low=project('low');ultra=project('ultra_low');again=project('standard')
        self.assertEqual(standard.bank,again.bank)
        self.assertEqual(standard.items,again.items)
        self.assertEqual(standard.report['target_delta_n'],DELTA_N)
        self.assertLess(ultra.report['target_bank_payload_bytes'],standard.report['target_bank_payload_bytes']*.26)
        ultra_delta=round(Fraction(15625,4)*144*65536/TARGET_CLOCK_HZ)
        self.assertEqual(ultra.report['target_delta_n'],ultra_delta)
        self.assertEqual(ultra.build_units()[0].source_key[2],ultra_delta)
        spelling=ultra.items[0][2].split()
        octave=int(next(t[1:] for t in spelling if t.startswith('o')))
        detune=int(next(t[1:] for t in spelling if t.startswith('D')))
        self.assertEqual((PCM_N[NOTES.index(spelling[-1])]+detune)>>(octave-1),ultra_delta)
        self.assertEqual(ultra.items[0][:2],standard.items[0][:2])
        self.assertLess(low.report['target_bank_payload_bytes'],standard.report['target_bank_payload_bytes']*.51)
        delta=low.report['target_delta_n']
        self.assertEqual(delta,round(Fraction(15625,2)*144*65536/TARGET_CLOCK_HZ))
        self.assertEqual(low.build_units()[0].source_key[2],delta)
        spelling=low.items[0][2].split()
        octave=int(next(t[1:] for t in spelling if t.startswith('o')))
        detune=int(next(t[1:] for t in spelling if t.startswith('D')))
        note=NOTES.index(spelling[-1])
        self.assertEqual((PCM_N[note]+detune)>>(octave-1),delta)
        mapping=low.report['mapping'][0]
        self.assertLessEqual(abs(mapping['valid_duration_error_vgmticks']),44100/float(TARGET_RATE/2))
        self.assertEqual(low.items[0][:2],standard.items[0][:2])
        self.assertEqual(mapping['source_event_ids'],[12])
        rate=Fraction(low.report['target_rate_num'],low.report['target_rate_den'])
        for start,end in _chunk_boundaries(0,44100*30,None,target_rate=rate):
            count=round(Fraction(end-start,44100)*rate)
            self.assertLessEqual(((count+7)//8)*4,MAX_SAMPLE_BYTES)

    def test_both_source_adapters_preserve_ir_and_report_smaller_assets(self):
        from test_msm6258 import source,run
        sources=[('msm6258',source(run())),('segapcm',
            (ROOT/'tests/fixtures/public/opm_segapcm/SEGAONE.vgm').read_bytes())]
        for kind,raw in sources:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                a=project_input(raw,kind=kind,outdir=root/'a',stem='tiny')
                b=project_input(raw,kind=kind,outdir=root/'b',stem='tiny',pcm_profile='ultra_low')
                self.assertEqual(a.normalization_view.source_end_vgmticks,b.normalization_view.source_end_vgmticks)
                self.assertTrue(b.report['source_ir_unchanged'])
                self.assertLessEqual(b.report['target_bank_payload_bytes'],a.report['target_bank_payload_bytes'])
                report=json.loads((root/'b/tiny.pcm_mucom_capacity.json').read_text())
                self.assertEqual(report['pcm_profile'],'ultra_low')
                self.assertEqual(report['limits']['bank_payload_bytes'],262144)
                self.assertEqual(report['limits']['sample_slots'],32)
                self.assertEqual(report['target_delta_n'],b.report['target_delta_n'])

    def test_canonical_and_batch_flags_and_native_nonapplicability(self):
        spec=importlib.util.spec_from_file_location('profile_cli',ROOT/'vgm2mml.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        result=dict(muc_path='tiny.muc',musical_note_units=0,timing=dict(timer_b=200))
        with patch('opna_muc.convert_vgm',return_value=result) as converter,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['authored.vgm','--pcm-profile','ultra_low']),0)
        self.assertEqual(converter.call_args.kwargs['pcm_profile'],'ultra_low')
        from export_mucom import main as batch_main
        with patch('sys.argv',['export_mucom.py','authored.vgm','--outdir','outputs/test','--pcm-profile','ultra_low']), patch('export_mucom.run_batch',return_value=[]) as batch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch_main(),0)
        self.assertEqual(batch.call_args.kwargs['timeout'],600)
        self.assertEqual(batch.call_args.kwargs['pcm_profile'],'ultra_low')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'tiny.vgm';source.write_bytes(b'authored')
            def convert(command,**kwargs):
                self.assertEqual(kwargs['timeout'],600)
                self.assertEqual(command[command.index('--pcm-profile')+1],'ultra_low')
                Path(command[command.index('--outdir')+1],'tiny.muc').write_text('A c4\n')
                return subprocess.CompletedProcess(command,0,'','')
            with patch('export_mucom.subprocess.run',side_effect=convert):
                self.assertEqual(run_batch(source,root/'batch',muc_only=True,pcm_profile='ultra_low')[0]['status'],'muc_only')
            from opna_pcm_fixture import authored_pcm
            source.write_bytes(authored_pcm(()))
            with contextlib.redirect_stdout(io.StringIO()):
                native=convert_vgm(source,root/'native',pcm_profile='ultra_low')
                native_standard=convert_vgm(source,root/'native_standard')
            self.assertFalse(native['pcm_profile']['applicable'])
            self.assertEqual(Path(native['muc_path']).read_bytes(),Path(native_standard['muc_path']).read_bytes())
            self.assertEqual((root/'native/tiny_pcm.bin').read_bytes(),(root/'native_standard/tiny_pcm.bin').read_bytes())
            self.assertEqual(native['pcm_profile']['requested'],'ultra_low')

if __name__=='__main__':unittest.main()
