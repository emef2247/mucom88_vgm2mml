"""MUCOM score-only blueprint checks without external MIDI libraries."""
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'py'))
from musical_hal import NativeDevice,NativePerformance,NativeUnit,NativeVoice,build_score_guide
from mucom_blueprint import render


def source(count=1):
    device=NativeDevice('opll:0','opll',0,3579545)
    units=tuple(NativeUnit(f'u{ch}:{i}','opll:0','fm',ch,i*4410,i*4410+4000,
        ((i*4410,440),),((i*4410,15),(i*4410+1000,3)),((i*4410,'voice'),),
        role='melody') for ch in range(count) for i in range(4))
    voice=NativeVoice('voice','opll:0','fm',(('preset',4),))
    return NativePerformance((device,),units,(),(voice,),(),(),17640,'hash','')


class BlueprintTests(unittest.TestCase):
    def test_score_loops_midi_and_neutral_voice_are_emitted_without_native_mutation(self):
        p=source();before=asdict(p)
        products,report=render(p,build_score_guide(p,repeats=True),'score')
        self.assertEqual(asdict(p),before)
        self.assertEqual({r['kind'] for r in products},{'blueprint','blueprint_midi','blueprint_voice'})
        muc=next(r['data'].decode('cp932') for r in products if r['kind']=='blueprint')
        self.assertIn('source opll:0 fm CH1',muc)
        self.assertIn(']4',muc)
        self.assertNotIn('%',muc)
        self.assertIn('#voice score.dat',muc)
        midi=next(r['data'] for r in products if r['kind']=='blueprint_midi')
        self.assertEqual(midi[:4],b'MThd')
        self.assertEqual(len(report['score_notes']),4)
        self.assertTrue(all(r['step_start']%3==r['step_end']%3==0 for r in report['score_notes']))
        self.assertEqual(len(next(r['data'] for r in products if r['kind']=='blueprint_voice')),8192)

    def test_extra_voices_split_into_pages_instead_of_omission(self):
        p=source(12)
        products,report=render(p,build_score_guide(p),'score')
        self.assertEqual(report['file_count'],2)
        self.assertEqual(report['sounding_parts'],12)
        self.assertEqual(len(report['score_notes']),48)
        self.assertIn('blueprint/score.part02.muc',{r['relative'] for r in products})

    def test_ssg_uses_native_flat_envelope_for_audition(self):
        from dataclasses import replace
        p=source()
        p=replace(p,devices=(NativeDevice('opll:0','psg',0,1789772),))
        products,_=render(p,build_score_guide(p),'ssg')
        muc=next(r['data'].decode('cp932') for r in products if r['kind']=='blueprint')
        self.assertIn('E255,255,255,255,0,255',muc)

    def test_internal_pitch_controls_do_not_invent_polyphony(self):
        from dataclasses import replace
        p=source()
        unit=replace(p.units[0],start_sample=0,end_sample=2205,
            pitch_curve=((0,261.626),(100,293.665),(200,329.628)))
        p=replace(p,units=(unit,),end_sample=2205)
        _,report=render(p,build_score_guide(p),'pitch')
        self.assertEqual(report['sounding_parts'],1)
        self.assertEqual(len(report['score_notes']),1)
        self.assertEqual(report['score_notes'][0]['midi_note'],64)
        self.assertEqual(report['score_notes'][0]['step_end'],9)
        self.assertEqual(report['decisions'][0]['action'],'collapse_internal_pitch_controls')

    def test_duration_remainder_is_dropped_independent_of_start_grid_phase(self):
        from dataclasses import replace
        p=source()
        for end,expected in ((800,0),(1500,3)):
            unit=replace(p.units[0],start_sample=700,end_sample=end,pitch_curve=((700,440),))
            trial=replace(p,units=(unit,),end_sample=end)
            _,report=render(trial,build_score_guide(trial),'gate')
            rows=report.get('score_notes',[])
            self.assertEqual(sum(r['step_end']-r['step_start'] for r in rows),expected)

    def test_slow_tempo_is_explicit_instead_of_silently_clamped(self):
        p=source()
        with self.assertRaisesRegex(ValueError,'Timer-B'):
            render(p,build_score_guide(p),'slow',tempo=1)

    def test_pcm_only_has_no_blueprint_or_replacement_audio_bank(self):
        p=NativePerformance((NativeDevice('c140:0','c140',0,8000000),),(),(),(),(),(),100,'hash','')
        products,report=render(p,build_score_guide(p),'pcm')
        self.assertEqual(products,[])
        self.assertEqual(report['status'],'not_applicable')


if __name__=='__main__':unittest.main()
