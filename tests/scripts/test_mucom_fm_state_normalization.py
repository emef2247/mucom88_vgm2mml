"""Public/authored checks for configurable FM state normalization and release tails."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'py'))
from mucom_fm_state_normalization import filter_fm_states
from opna import OpnaRegisterState
from opna_music import Control, Gate


class FmShortStateTests(unittest.TestCase):
    def states(self):
        state=OpnaRegisterState().snapshot(0)
        state=replace(state,key_mask=15,fnum=1000,block=4)
        return state,replace(state,fnum=1001)

    def filter(self, states, lengths, events=(), *, max_samples=44):
        controls=[];start=0
        for index,(state,length) in enumerate(zip(states,lengths)):
            controls.append(Control(start,start+length,state,(index,),(index,)))
            start+=length
        gate=Gate('A',0,0,0,start,tuple(controls),tuple(range(len(states))),tuple(range(len(states))))
        result,rows=filter_fm_states({'A':(gate,)},SimpleNamespace(events=events),max_samples=max_samples)
        return gate,result['A'][0],rows

    def test_44_drops_and_45_stays_without_mutating_evidence(self):
        a,b=self.states()
        for length,expected in ((44,1),(45,3)):
            before,after,rows=self.filter((a,b,a),(1000,length,1000))
            self.assertEqual(len(after.controls),expected)
            self.assertEqual(before.controls[1].state,b)
            self.assertEqual((after.start,after.end),(before.start,before.end))
            self.assertEqual(after.source_segment_ids,before.source_segment_ids)
            self.assertEqual(sum(row['action']=='omit_short_state' for row in rows),length==44)

    def test_prefix_tail_and_all_short_preserve_original_gate_edges(self):
        a,b=self.states()
        for states,lengths in (((b,a),(44,1000)),((a,b),(22,22))):
            before,after,_=self.filter(states,lengths)
            self.assertEqual(len(after.controls),1)
            self.assertEqual((after.controls[0].start,after.controls[0].end),(0,before.end))

    def test_short_tail_preserves_native_release_state(self):
        a,b=self.states()
        before,after,rows=self.filter((a,b),(1000,44))
        self.assertEqual(after.controls[-1].state,b)
        self.assertEqual(after.end,before.end)
        self.assertEqual(rows[-1]['action'],'retain')
        self.assertIn('release_tail_state',rows[-1]['protection_reason'])

    def test_independent_1ms_default_and_explicit_state_bounds(self):
        from fm_state_runs import FM_SHORT_STATE_SAMPLES, fm_short_state_samples
        self.assertEqual(FM_SHORT_STATE_SAMPLES,44)
        self.assertEqual(fm_short_state_samples(12),529)
        a,b=self.states()
        for threshold,length,omitted in ((352,352,True),(352,353,False),
                                          (529,529,True),(529,530,False)):
            _,_,rows=self.filter((a,b,a),(1000,length,1000),max_samples=threshold)
            self.assertEqual(rows[1]['action']=='omit_short_state',omitted)
            self.assertEqual(rows[1]['threshold_samples'],threshold)

    def test_tone_pan_partial_key_and_nonuniform_levels_are_protected(self):
        a,b=self.states()
        changes=(replace(b,feedback=2),replace(b,left_enabled=1),replace(b,key_mask=7),
                 replace(b,operators=(replace(b.operators[0],tl=20),)+b.operators[1:]))
        for changed in changes:
            _,after,rows=self.filter((a,changed,a),(1000,1,1000))
            self.assertEqual(len(after.controls),3)
            self.assertTrue(rows[1]['protected'])

    def test_global_effect_and_zero_time_partial_controls_survive(self):
        a,b=self.states()
        event=SimpleNamespace(vgmticks=1000,register=0x22)
        _,after,rows=self.filter((a,b,a),(1000,1,1000),(event,))
        self.assertEqual(len(after.controls),3)
        _,after,_=self.filter((a,replace(b,key_mask=7),a),(1000,0,1000))
        self.assertEqual(len(after.controls),3)

    def test_conversion_stage_default_and_explicit_bypass(self):
        import csv
        import struct
        import tempfile
        from opna_muc import convert_vgm
        from test_mucom_fm_edges import authored_vgm
        raw=bytearray(authored_vgm(((0xf0,2000),(0,800))))
        pattern=bytes((0x56,0x28,0xf0,0x61,0xd0,0x07))
        start=raw.index(pattern,256)+3
        raw[start:start+3]=(bytes((0x61,0xe8,0x03,0x56,0xa0,107,0x61,44,0,
                                   0x56,0xa0,106,0x61,0xbc,0x03)))
        struct.pack_into('<I',raw,4,len(raw)-4)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'authored.vgm';source.write_bytes(raw)
            for enabled,milliseconds in ((True,8),(False,8)):
                out=root/(str(enabled)+str(milliseconds))
                report=convert_vgm(source,out,normalize_lengths=enabled,normalization_ms=milliseconds)
                self.assertEqual(report['status'],'converted')
                artifacts=list(out.rglob('*.mucom.fm_states.decisions.csv'))
                self.assertEqual(bool(artifacts),enabled)
                if enabled:
                    from fm_state_runs import fm_short_state_samples
                    self.assertEqual(report['normalization']['fm_short_states']['threshold_samples'],44)
                    self.assertEqual(report['normalization']['fm_short_states']['threshold_ms'],1)
                    with artifacts[0].open(newline='',encoding='utf-8') as stream:
                        rows=list(csv.DictReader(stream))
                    self.assertEqual(sum(row['action']=='omit_short_state' for row in rows),1)


    def test_mapped_opm_pitch_state_uses_same_filter(self):
        import csv
        import tempfile
        from opna_muc import convert_vgm
        from test_opm_opna import tone, write, wait, vgm
        data=(tone()+write(8,120)+wait(1000)+write(0x30,4)+wait(44)
              +write(0x30,0)+wait(1000)+write(8,0)+wait(1000))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'mapped.vgm';source.write_bytes(vgm(data,3579545))
            out=root/'out';report=convert_vgm(source,out,song_loop=False)
            self.assertEqual(report['status'],'converted')
            with next(out.rglob('*.mucom.fm_states.decisions.csv')).open(newline='',encoding='utf-8') as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(sum(row['action']=='omit_short_state' for row in rows),1)
            self.assertTrue((out/'mapped.opm.segments.csv').is_file())


if __name__=='__main__':unittest.main()
