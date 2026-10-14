"""Auxiliary structure identities are derived from native source evidence."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'py'))
from opna_muc import merge_items
from vgm_timing import CommandTime
from mucom_aux import ProjectionError, project_rhythm


class AuxiliarySourceIdentityTests(unittest.TestCase):
    def test_ssg_identity_does_not_use_target_spelling(self):
        segment=SimpleNamespace(vgmticks=10,vgmticks_end=30,registers=(1,2),
            prescale=6,envelope_trigger=False,ch=0,source_event_id=7)
        first=merge_items('D',[(10,30,'o4 D0 c',('ssg',0,0))],[segment])[0]
        second=merge_items('D',[(10,30,'o3 D100 b',('ssg',0,0))],[segment])[0]
        self.assertEqual(first.source_key,second.source_key)
        self.assertEqual(first.source_segment_ids,('ssg:0:0',))
        self.assertEqual(first.source_event_ids,(7,))

    def test_pcm_identity_retains_encoded_source_and_members(self):
        segment=SimpleNamespace(start_vgmticks=10,end_vgmticks=30,sample_id=2,
            rate_num=16000,rate_den=1,level=127,pan=3,repeat=False,memory_mode='rom',
            registers=(1,2),continuation_index=0,termination='natural_end',
            segment_id=4,source_event_ids=(7,8))
        sample=SimpleNamespace(sample_id=2,sha256='source-content-hash')
        first=merge_items('K',[(10,30,'@1 o4 c',None)],[segment],pcm_samples=[sample])[0]
        second=merge_items('K',[(10,30,'@9 o3 b',None)],[segment],pcm_samples=[sample])[0]
        self.assertEqual(first.source_key,second.source_key)
        self.assertIn('source-content-hash',first.source_key[0])
        self.assertEqual(first.source_segment_ids,(4,))
        self.assertEqual(first.source_event_ids,(7,8))

    def test_rhythm_identity_links_setup_trigger_and_dump(self):
        raw=bytes((0x56,0x11,23,0x56,0x18,0xdf,0x56,0x10,1,0x56,0x10,0x81))
        events=[CommandTime(i*3,0x56,t,0,i) for i,t in enumerate((0,0,10,30))]
        first=merge_items('G',[(10,30,'v23,31 @1 c',None)],events,raw=raw)[0]
        second=merge_items('G',[(10,30,'v0 @2 d',None)],events,raw=raw)[0]
        self.assertEqual(first.source_key,second.source_key)
        self.assertEqual(first.source_segment_ids,('rhythm:2',))
        self.assertEqual(first.source_event_ids,(0,1,2))
        changed=bytearray(raw);changed[2]=24
        other=merge_items('G',[(10,30,'v23,31 @1 c',None)],events,raw=changed)[0]
        self.assertNotEqual(first.source_key,other.source_key)


class RhythmOrderingTests(unittest.TestCase):
    @staticmethod
    def source(writes):
        raw=bytes(value for _,register,data in writes for value in (0x56,register,data))
        events=[CommandTime(index*3,0x56,tick,0,index)
                for index,(tick,_,_) in enumerate(writes)]
        return events,raw

    def test_overlapping_hits_partial_dump_and_controls_remain_ordered(self):
        events,raw=self.source([(0,0x18,0xdf),(10,0x10,1),(10,0x11,42),
                                (20,0x10,2),(20,0x18,0x9f),(30,0x10,0x81)])
        items=project_rhythm(events,raw,40)
        self.assertEqual([item[0] for item in items],[0,10,20,30])
        self.assertEqual(items[1][2],'@0 y16,1 y17,42 r')
        self.assertEqual(items[2][2],'@0 y16,2 y24,159 r')
        self.assertEqual(items[3][2],'@0 y16,129 r')
        units=merge_items('G',items,events,raw=raw)
        self.assertTrue(all(unit.kind=='rhythm_controls' for unit in units))
        self.assertEqual(units[1].source_event_ids,(0,1,2))

    def test_zero_duration_source_trigger_dump_and_final_controls_retained(self):
        events,raw=self.source([(10,0x10,1),(10,0x10,0x81),(40,0x10,2)])
        items=project_rhythm(events,raw,40)
        self.assertEqual(items[0][2],'@0 y16,1 y16,129 r')
        self.assertEqual(items[-1][:2],(40,40))
        self.assertEqual(len(merge_items('G',items,events,raw=raw)),2)

    def test_ssg_shapes_restore_after_native_note_attacks(self):
        from mucom_aux import project_ssg
        events,raw=self.source([(10,13,0)])
        segment=SimpleNamespace(scale='c',vgmticks=10,vgmticks_end=30,
            prescale=6,tone_period=500,noise_period=0,mode=1,envelope_enabled=1,
            envelope_shape=0,envelope_period=512,volume=0,source_event_id=0)
        corrections=[]
        tracks=project_ssg(SimpleNamespace(segments={0:[segment]}),events,raw,
                           global_controls=corrections)
        self.assertIn('S0 m512',tracks['D'][0][2])
        self.assertEqual(corrections,[(10,13,0,0)])
        self.assertIn('y13,0',project_rhythm(events,raw,30,extra_controls=corrections)[0][2])

    def test_fixed_volume_to_hardware_mode_sets_native_hardware_flag(self):
        from mucom_aux import project_ssg
        common=dict(scale='c',prescale=6,tone_period=500,noise_period=0,mode=1,
                    envelope_shape=0,envelope_period=512,volume=15)
        segments=[SimpleNamespace(vgmticks=10,vgmticks_end=20,envelope_enabled=0,
                                  source_event_id=0,**common),
                  SimpleNamespace(vgmticks=20,vgmticks_end=40,envelope_enabled=1,
                                  source_event_id=1,**common)]
        corrections=[]
        tracks=project_ssg(SimpleNamespace(segments={0:segments}),[],b'',
                           global_controls=corrections)
        self.assertIn('S0 m512',tracks['D'][1][2])
        self.assertEqual(corrections,[(20,13,0,1)])

    def test_shared_hardware_envelope_join_remains_explicit(self):
        from mucom_aux import project_ssg
        common=dict(scale='c',prescale=6,tone_period=500,noise_period=0,mode=1,
                    envelope_enabled=1,envelope_shape=0,envelope_period=512,
                    volume=0,source_event_id=0)
        analysis=SimpleNamespace(segments={0:[SimpleNamespace(vgmticks=10,vgmticks_end=40,**common)],
                   1:[SimpleNamespace(vgmticks=20,vgmticks_end=30,**common)]})
        with self.assertRaisesRegex(ProjectionError,'Overlapping SSG hardware-envelope'):
            project_ssg(analysis,[],b'',global_controls=[])

    def test_native_ordered_rhythm_actions_and_shape0(self):
        import tempfile,subprocess
        root=Path(__file__).resolve().parents[2]
        tool=root/'.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe'
        if sys.platform!='win32' or not tool.is_file():
            self.skipTest('Windows native MUCOM tools unavailable')
        sys.path.insert(0,str(root/'scripts'))
        from mucom_compiler import compile_muc
        from vgm_timing import command_times
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);muc=out/'probe.muc';mub=muc.with_suffix('.mub')
            muc.write_bytes(('#title ordered aux probe\r\n#driver mucomDotNET\r\n'
                'D C128 t202 q0 P1 S0 m512 o4 c%24 r%8\r\n'
                'G C128 q0 @0 y13,0 y17,63 y24,223 y25,223 y16,1 r%8 '
                'y16,2 y17,42 r%8 y16,129 r%8 y16,130 r%8\r\n').encode('cp932'))
            compile_muc(muc,mub)
            run=subprocess.run([str(tool),str(mub)],cwd=out,capture_output=True,timeout=60)
            self.assertEqual(run.returncode,0)
            raw=mub.with_suffix('.vgm').read_bytes()
            writes=[(event.vgmticks,raw[event.address+1],raw[event.address+2])
                    for event in command_times(raw) if event.command==0x56]
            shape=[(t,val) for t,reg,val in writes if reg==13]
            self.assertEqual([val for _,val in shape],[8,8,0])
            self.assertEqual(len(set(t for t,_ in shape)),1)
            hits=[val for _,reg,val in writes if reg==16 and val&63]
            self.assertEqual(hits,[1,2,129,130])
            actions=[(t,reg,val) for t,reg,val in writes if reg==16 and val&63 or reg==17]
            self.assertEqual([val for _,reg,val in actions if reg==17],[63,42])


if __name__=='__main__':
    unittest.main()

