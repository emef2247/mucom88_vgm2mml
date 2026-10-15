"""Small authored codec/packaging checks; no private conversion regression."""
import sys
from pathlib import Path
import unittest
import tempfile
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'py'))
from pcm_mucom import (_project, encode_adpcm_b, TARGET_RATE, MAX_SAMPLE_BYTES,
                       _chunk_boundaries, ProjectionError, project_input)
from opna_adpcm import decode_adpcm_b


class PcmMucomTest(unittest.TestCase):
    def test_codec_padding_and_nibble_order(self):
        data,padding=encode_adpcm_b([10000,-10000,0])
        self.assertEqual((len(data),padding),(4,5))
        decoded=decode_adpcm_b(data)
        self.assertGreater(decoded[0],0)
        self.assertLess(decoded[1],decoded[0])

    def test_target_units_and_capacity_packaging(self):
        row=dict(start=100,end=900,samples=[1000,-1000]*500,rate=TARGET_RATE,
                 origin=100,pan=2,source_ids=(7,),events=(12,15),exhaustion_clipped=False)
        result=_project(1000,[row],dict(source_kind='msm6258',losses=[]),500)
        self.assertEqual(len(result.items),2)
        self.assertEqual(result.items[0][1],500)
        self.assertEqual(result.items[1][0],500)
        self.assertFalse(result.normalization_view.segments[0].independently_playable)
        self.assertEqual(result.normalization_view.segments[0].termination,'projected_chunk_boundary')
        self.assertIn('p2',result.items[0][2])
        self.assertEqual(result.build_units()[0].source_event_ids,(12,15))
        self.assertEqual(result.report['mapping'][0]['source_gate_ids'],[7])
        for binding in result.bindings:
            self.assertEqual(binding['encoded_length']%4,0)
            self.assertLessEqual(binding['encoded_length'],MAX_SAMPLE_BYTES)

    def test_chunk_bound_comes_from_mucom_byte_capacity(self):
        bounds=list(_chunk_boundaries(0,44100*30,None))
        self.assertGreater(bounds[0][1],44100*8)
        for start,end in bounds:
            count=round((end-start)*TARGET_RATE/44100)
            self.assertLessEqual(((count+7)//8)*4,MAX_SAMPLE_BYTES)

    def test_msm_adapter_keeps_absolute_onset_and_maps_disable_pan(self):
        from test_msm6258 import source,run,wait,write
        raw=source(wait(20)+run(pan=1))
        with tempfile.TemporaryDirectory() as directory:
            result=project_input(raw,kind='msm6258',outdir=directory,stem='tiny')
            self.assertEqual(result.items[0][:2],(20,31))
            self.assertIn('p2',result.items[0][2])
            report=json.loads((Path(directory)/'tiny.pcm_mucom_projection.json').read_text())
            self.assertTrue(report['source_ir_unchanged'])
            self.assertTrue((Path(directory)/'tiny.pcm_source.json').exists())
            self.assertEqual(result.build_units()[0].part,'K')
        changed=source(write(0,2)+write(1,0x12)+wait(5)+write(2,1)+write(1,0x34)+wait(6)+write(0,1))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProjectionError,'held-pan'):
                project_input(changed,kind='msm6258',outdir=directory,stem='pan')

    def test_global_source_issues_are_not_hidden_by_eligible_playback(self):
        from test_msm6258 import source,run,write
        raw=source(run()+write(7,1))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProjectionError,'unsupported_register'):
                project_input(raw,kind='msm6258',outdir=directory,stem='unknown')

    def test_bank_capacity_failure_has_persisted_exact_counts(self):
        from unittest.mock import patch
        row=dict(start=0,end=100,samples=[100]*100,rate=TARGET_RATE,
                 origin=0,pan=3,source_ids=(7,),events=(12,),exhaustion_clipped=False)
        with tempfile.TemporaryDirectory() as directory:
            with patch('pcm_mucom._msm_source',return_value=(100,[row],dict(source_kind='msm6258',losses=[]))):
                with patch('pcm_mucom.MAX_BANK_BYTES',4):
                    with self.assertRaisesRegex(ProjectionError,'bank_payload_bytes'):
                        project_input(b'authored',kind='msm6258',outdir=directory,stem='capacity')
            report=json.loads((Path(directory)/'capacity.pcm_mucom_capacity.json').read_text())
            failure=report['failure']
            self.assertEqual(report['status'],'blocked')
            self.assertEqual(failure['existing_bank_payload_bytes'],0)
            self.assertEqual(failure['actual'],failure['sample_encoded_bytes'])
            self.assertGreater(failure['actual'],failure['limit'])
            self.assertEqual(failure['limit'],4)
            self.assertEqual(failure['attempted_slot'],1)
            self.assertEqual(failure['source_gate_ids'],[7])

    def test_sample_length_error_is_distinct_from_bank_capacity(self):
        from unittest.mock import patch
        row=dict(start=0,end=100,samples=[100]*100,rate=TARGET_RATE,
                 origin=0,pan=3,source_ids=(0,),events=(0,),exhaustion_clipped=False)
        with patch('pcm_mucom.encode_adpcm_b',return_value=(b'\0'*65540,0)):
            with self.assertRaises(ProjectionError) as caught:
                _project(100,[row],dict(source_kind='msm6258',losses=[]),None)
        self.assertEqual(caught.exception.diagnostics['code'],'sample_length_field')
        self.assertEqual(caught.exception.diagnostics['actual'],65536)
        self.assertEqual(caught.exception.diagnostics['limit'],65535)

    def test_overlap_rejected(self):
        row=dict(start=0,end=20,samples=[1]*20,rate=TARGET_RATE,
                 origin=0,pan=3,source_ids=(0,),events=(0,),exhaustion_clipped=False)
        with self.assertRaisesRegex(ProjectionError,'Overlapping'):
            _project(30,[row,row],dict(source_kind='msm6258',losses=[]),None)


if __name__=='__main__':
    unittest.main()
