"""The native comparator consumes output policy and recorded allocation explicitly."""
import hashlib
import json
from pathlib import Path
import sys
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts'),str(ROOT/'tests/scripts')]
import verify_mucom_native as native


class RecordedTimingTests(unittest.TestCase):
    def timing(self):
        return {'samples_per_clock':100,'source_boundaries':[
            {'source_sample':0,'target_clock':0},
            {'source_sample':151,'target_clock':1},
            {'source_sample':250,'target_clock':3},
            {'source_sample':400,'target_clock':4}]}

    def test_recorded_allocation_differs_from_nearest_and_is_authoritative(self):
        projected=native._projection_transform(self.timing())
        self.assertEqual(projected(151),200)
        self.assertEqual(projected(250),400)
        self.assertEqual(projected(400),500)

    def test_missing_boundary_fails_without_nearest_fallback(self):
        projected=native._projection_transform(self.timing())
        with self.assertRaisesRegex(ValueError,'Missing recorded target clock.*152'):
            projected(152)

    def test_loop_uses_recorded_period_and_keeps_intro_separate(self):
        projected=native._projection_transform(self.timing(),151)
        self.assertEqual(projected(0),100)
        self.assertEqual(projected(400),500)
        self.assertEqual(projected(499),700) # source250 in traversal2
        self.assertEqual(projected(649),800) # end of traversal2
        with self.assertRaisesRegex(ValueError,'Missing recorded target clock.*251'):
            projected(500)

    def test_conflicting_allocation_and_invalid_loop_are_rejected(self):
        timing=self.timing()
        timing['source_boundaries'].append({'source_sample':151,'target_clock':2})
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            native._projection_transform(timing)
        with self.assertRaisesRegex(ValueError,'Missing recorded target clock'):
            native._projection_transform(self.timing(),152)
        with self.assertRaisesRegex(ValueError,'positive recorded loop duration'):
            native._projection_transform(self.timing(),400)


class OmissionComparisonTests(unittest.TestCase):
    def test_disabled_policy_does_not_apply_reported_omissions(self):
        source=object()
        report={'normalization':{'applied':False,
            'omitted_fm_gates':[{'on_event_id':1,'off_event_id':2}]}}
        analysis,omissions=native._normalized_analysis(source,report)
        self.assertIs(analysis,source)
        self.assertEqual(omissions,[])

    def run_silent_comparison(self,authorized):
        on=SimpleNamespace(register=0x28,rising_mask=15,falling_mask=0,
                           ch=0,vgmticks=0,state=SimpleNamespace(key_mask=15))
        off=SimpleNamespace(register=0x28,rising_mask=0,falling_mask=15,
                            ch=0,vgmticks=200,state=SimpleNamespace(key_mask=0))
        original=SimpleNamespace(events=(on,off),source_end_vgmticks=400)
        silence=SimpleNamespace(events=(),source_end_vgmticks=400)
        omissions=[{'on_event_id':1,'off_event_id':2}]
        report={'status':'converted','timing':{'samples_per_clock':100,
                'source_boundaries':[{'source_sample':s,'target_clock':s//100}
                                     for s in (0,200,400)]},
                'song_loop':{'enabled':False},
                'normalization':{'applied':authorized,'omitted_fm_gates':omissions}}
        helper=SimpleNamespace(apply_fm_omissions=lambda analysis,ids:silence)
        def parse(path,prefix,**kwargs):
            kwargs['opna_metadata']['analysis']=original if str(prefix).endswith('source') else silence
        def replay(command,**kwargs):
            (Path(kwargs['cwd'])/'native.vgm').write_bytes(b'native')
            return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'source.vgm';source.write_bytes(b'source')
            mub=root/'source.mub';mub.write_bytes(b'mub')
            tool=root/'tool.exe';tool.write_bytes(b'tool')
            report['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
            projection=root/'projection.json';projection.write_text(json.dumps(report),encoding='utf-8')
            with patch.dict(sys.modules,{'mucom_normalization':helper}), \
                 patch.object(native,'parse_vgm',side_effect=parse), \
                 patch.object(native,'read_vgm_bytes',return_value=b'source'), \
                 patch.object(native,'read_loop_metadata',return_value={'status':'absent'}), \
                 patch.object(native,'build_fm_gates',return_value={}), \
                 patch.object(native,'audit_fm_key_edges'), \
                 patch.object(native,'_tool_argument',side_effect=str), \
                 patch.object(native.subprocess,'run',side_effect=replay):
                result=native.verify(source,mub,root/'check',projection=projection,tool=tool)
            self.assertEqual(original.events,(on,off))
            return result

    def test_all_authorized_gates_omitted_checks_silence_and_passes(self):
        result=self.run_silent_comparison(True)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['expected_gates'],{})
        self.assertEqual(len(result['authorized_omitted_fm_gates']),1)

    def test_unexplained_missing_source_edges_fails_instead_of_not_applicable(self):
        result=self.run_silent_comparison(False)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(any('key edge count' in error for error in result['errors']))


class NativeNormalizationReplayTests(unittest.TestCase):
    def test_repeated_source_loop_uses_original_omitted_edge_ids(self):
        from test_mucom_fm_edges import authored_vgm
        from vgm_reader import parse_vgm
        from mucom_normalization import omit_short_fm
        raw=bytearray(authored_vgm(((0xf0,352),(0,800),(0xf0,900),(0,800))))
        attack=raw.index(bytes((0x56,0x28,0xf0)),256)
        struct.pack_into('<I',raw,0x1c,attack-0x1c)
        struct.pack_into('<I',raw,0x20,2852)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'loop.vgm';source.write_bytes(raw)
            first,repeated={},{}
            parse_vgm(str(source),str(root/'first'),opna_metadata=first,dump_opna_segments=True,loop_count=1)
            parse_vgm(str(source),str(root/'repeated'),opna_metadata=repeated,dump_opna_segments=True,loop_count=2)
            _,omissions=omit_short_fm(first['analysis'],352,0)
            original=repeated['analysis'];events=original.events
            derived,losses=native._normalized_analysis(original,{'normalization':{
                'applied':True,'omitted_fm_gates':omissions}})
            keys=lambda analysis:[e for e in analysis.events if e.register==0x28]
            self.assertEqual(len(keys(original)),8)
            self.assertEqual(len(keys(derived)),4)
            self.assertTrue(all(e.original_event_id not in {
                omissions[0]['on_event_id'],omissions[0]['off_event_id']} for e in keys(derived)))
            self.assertEqual(original.events,events)
            self.assertEqual(losses,omissions)

    @unittest.skipUnless(sys.platform=='win32' and native.DEFAULT_TOOL.is_file(),
                         'native MUCOM compiler/replay unavailable')
    def test_native_all_omitted_and_mixed_gate_outputs(self):
        from test_mucom_fm_edges import authored_vgm
        from opna_muc import convert_vgm
        from mucom_compiler import compile_muc
        cases=(('silent',((0xf0,352),(0,800)),0),
               ('mixed',((0xf0,352),(0,800),(0xf0,900),(0,800)),1))
        for name,pattern,count in cases:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/(name+'.vgm');source.write_bytes(authored_vgm(pattern))
                out=root/'out'
                report=convert_vgm(source,out,song_loop=False,normalization_ms=8)
                self.assertTrue(report['normalization']['applied'])
                self.assertEqual(len(report['normalization']['omitted_fm_gates']),1)
                mub=out/(name+'.mub');compile_muc(Path(report['muc_path']),mub)
                comparison=native.verify(source,mub,out/'check',projection=report['manifest_path'])
                self.assertEqual(comparison['status'],'passed',comparison['errors'])
                self.assertEqual(sum(comparison['expected_gates'].values()),count)


if __name__=='__main__':unittest.main()
