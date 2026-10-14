import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'py'))
from opna_muc import Unit,analyze_fm_seams,choose_clock,render_track,ConversionError,analyze_ssg_seams

class LoopSeamTests(unittest.TestCase):
    def state(self,mask=15,fnum=600):
        return NS(key_mask=mask,fnum=fnum,block=4,algorithm=7,feedback=0,
          left_enabled=1,right_enabled=1,ams=0,pms=0,operators=(),lfo_enabled=0,
          lfo_rate=0,fm_prescale_divisor=6,channel3_special_enabled=False,csm_enabled=False)
    def evidence(self,head_mask=0,offset=8,stable=False):
        a=self.state();b=self.state(fnum=600 if stable else 700)
        events=[NS(ch=0,register=40,vgmticks=1000+offset,state=self.state(head_mask),source_event_id=1),
                NS(ch=0,register=64,vgmticks=9000,state=b,source_event_id=2)]
        gate=NS(channel=0,start=0 if stable else 2000,end=1100 if stable else 10000,
                controls=(NS(start=0,end=1100,state=a),),source_segment_ids=('fm:1',))
        return NS(events=events),{'A':(gate,)}
    def test_ordered_full_head_off_is_candidate_constraint(self):
        analysis,gates=self.evidence()
        seams=analyze_fm_seams(analysis,gates,1000,10000)
        unit=Unit('A',2000,10000,'fm',((2000,10000,'','c'),),())
        ticks,report=choose_clock({'A':[unit]},10000,1000,8,seams=seams)
        self.assertEqual(ticks[1000],ticks[1008])
        self.assertEqual(seams[0]['head_key_event_id'],1)
    def test_distant_head_off_cannot_be_hidden(self):
        analysis,gates=self.evidence(offset=2000)
        seams=analyze_fm_seams(analysis,gates,1000,10000)
        unit=Unit('A',2000,10000,'fm',((2000,10000,'','c'),),())
        with self.assertRaises(ConversionError):choose_clock({'A':[unit]},10000,1000,8,seams=seams)
    def test_head_on_does_not_prove_off(self):
        analysis,gates=self.evidence(head_mask=15)
        with self.assertRaises(ConversionError):analyze_fm_seams(analysis,gates,1000,10000)
    def test_crossing_gate_closing_on_marker_clock(self):
        unit=Unit('A',0,1010,'fm',((0,1010,'@1','c'),),())
        full,plan,folds,text,*_=render_track([unit],2000,1000,{0:0,1000:10,1010:10,2000:20})
        self.assertEqual(text,'@1 c%10 L r%10')
    def test_stable_carry_ties_both_marker_and_eof(self):
        analysis,gates=self.evidence(stable=True)
        seam=analyze_fm_seams(analysis,gates,1000,10000)[0]
        first=Unit('A',0,1100,'fm',((0,1100,'@1','c'),),())
        last=Unit('A',2000,10000,'fm',((2000,10000,'@2','c'),),())
        _,_,_,text,*_=render_track([first,last],10000,1000,{0:0,1000:10,1100:11,2000:20,10000:100},seam=seam)
        self.assertIn('c%10 & L c128',text)
        self.assertTrue(text.endswith('c%80 &'))
        self.assertEqual(text.count('@1'),1)
    def test_partial_mask_refresh_survives_loop_split_and_native_replay(self):
        from opna_muc import Unit
        from source_loop_plan import expanded_tokens
        kd='KD128,128,0,0'
        unit=Unit('A',0,346,'fm',((0,346,kd,'c'),),())
        _,_,_,text,*_=render_track([unit],346,16,{0:0,16:16,346:346},
                                   seam={'mode':'stable_carry'})
        body=text.split('L',1)[1]
        self.assertGreaterEqual(expanded_tokens(body).count(kd),3)
        self.assertTrue(text.endswith('&'))
        tool=ROOT/'.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe'
        if sys.platform!='win32' or not tool.is_file():
            self.skipTest('native compiler/replay unavailable')
        import tempfile,subprocess
        sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests/scripts')]
        from test_mucom_fm import state
        from mucom_fm import fm_voice
        from mucom_compiler import compile_muc
        from vgm_timing import command_times
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            (out/'voice.dat').write_bytes(bytes(32)+fm_voice(state()))
            muc=out/'loop.muc';mub=out/'loop.mub'
            muc.write_bytes(('#title Partial held loop\r\n#driver mucomDotNET\r\n#voice voice.dat\r\n'
                'A C128 t202 q0 @1 v15 p3 o4 D0 '+text+'\r\n').encode('cp932'))
            compile_muc(muc,mub)
            run=subprocess.run([str(tool),str(mub)],cwd=out,capture_output=True,timeout=30)
            self.assertEqual(run.returncode,0)
            raw=mub.with_suffix('.vgm').read_bytes()
            active=[raw[e.address+2] for e in command_times(raw)
                    if e.command==0x56 and raw[e.address+1]==0x28
                    and raw[e.address+2]&7==0 and raw[e.address+2]&0xf0]
            self.assertTrue(active)
            self.assertEqual(set(active),{0xc0})

    def test_true_crossing_without_carry_is_rejected(self):
        unit=Unit('A',0,1100,'fm',((0,1100,'','c'),),())
        with self.assertRaises(ConversionError):render_track([unit],2000,1000,{0:0,1000:10,1100:11,2000:20})
    def test_ssg_semantic_equality_is_required(self):
        def segment(start,end,period):
            return NS(vgmticks=start,vgmticks_end=end,tone_period=period,mode=1,
             noise_period=0,envelope_enabled=0,envelope_shape=0,envelope_period=0,
             amplitude_register=15,prescale=6,scale='',source_event_id=start)
        unit=Unit('D',0,1100,'ssg',((0,1100,'P1','c'),),(),('ssg:0',))
        tracks={'D':[unit]}
        source=NS(segments={0:(segment(0,1100,392),segment(2000,10000,392)),1:(),2:()})
        seam=analyze_ssg_seams(source,tracks,1000,10000)[0]
        self.assertEqual(seam['mode'],'stable_carry')
        last=Unit('D',2000,10000,'ssg',((2000,10000,'','c'),),())
        _,_,_,text,*_=render_track([unit,last],10000,1000,{0:0,1000:10,1100:11,2000:20,10000:100},seam=seam)
        self.assertIn('c%10 & L c128',text)
        self.assertTrue(text.endswith('c%80 &'))
        source.segments[0][-1].tone_period=556
        self.assertEqual(analyze_ssg_seams(source,tracks,1000,10000),[])

    def test_rhythm_conductor_splits_without_tie(self):
        unit=Unit('G',0,2000,'rhythm_controls',((0,2000,'y$11,63','r'),),())
        _,_,_,text,*_=render_track([unit],2000,1000,{0:0,1000:10,2000:20})
        self.assertEqual(text,'y$11,63 r%10 L r%10')

if __name__=='__main__':unittest.main()
