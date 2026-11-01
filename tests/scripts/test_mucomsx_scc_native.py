"""Authored native SCC waveform, pitch and held software-volume projection."""
from copy import deepcopy
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'py'))
from mucomsx_scc_native import (wave_definition,project_pitch,render_note,
    SCC_PERIODS,TARGET_CLOCK_HZ,FLAT_ENVELOPE)


def note(*,continuation=False,runs=None):
    segment=SimpleNamespace(tone_period=253,octave=4,scale='a',waveform_id=0,
        waveform_hex=bytes(range(32)).hex(),enabled=True)
    runs=runs or [(15,1000),(7,500),(3,1500)]
    return SimpleNamespace(segment=segment,start=0,length=sum(n for _,n in runs),
        runs=runs,continuation=continuation)


class NativeSccTests(unittest.TestCase):
    def test_observed_wave_bytes_are_signed_and_exact(self):
        raw=bytes((128,255,0,127))+bytes(range(28))
        line=wave_definition(63,raw.hex())
        self.assertTrue(line.startswith('?WD63={-128,-1,0,127,'))
        values=list(map(int,line.split('{')[1][:-1].split(',')))
        self.assertEqual(bytes(n&255 for n in values),raw)
        for value,wave in ((64,raw.hex()),(-1,raw.hex()),(0,'ff'*31)):
            with self.assertRaises(ValueError):wave_definition(value,wave)

    def test_native_table_and_signed_delta_reconstruct_each_source_period(self):
        for period in range(9,4096):
            pitch=project_pitch(period,TARGET_CLOCK_HZ)
            index=(pitch.octave-1)*12+('c','c+','d','d+','e','f','f+','g','g+','a','a+','b').index(pitch.note)
            self.assertEqual(SCC_PERIODS[index]+pitch.detune,period)
            self.assertEqual(pitch.target_period,period)
            self.assertFalse(pitch.clamped)
            self.assertAlmostEqual(pitch.source_frequency_hz,pitch.target_frequency_hz)
        pitch=project_pitch(253,TARGET_CLOCK_HZ/2)
        self.assertEqual(pitch.target_period,507)
        self.assertAlmostEqual(pitch.source_frequency_hz,pitch.target_frequency_hz)
        self.assertTrue(project_pitch(4095,TARGET_CLOCK_HZ/2).clamped)

    def test_software_runs_are_held_without_new_attacks_or_source_mutation(self):
        source=note();before=deepcopy(vars(source.segment)),deepcopy(source.runs)
        command,key,info=render_note(source,{0:0,1000:10,1500:15,3000:30},0,TARGET_CLOCK_HZ)
        self.assertIn('@0 '+FLAT_ENVELOPE+' D0 ?DB',command)
        self.assertEqual(command.count('&'),2)
        self.assertEqual(re.findall(r'v(\d+)',command),['15','7','3'])
        self.assertEqual(re.findall(r'%([0-9]+)',command),['10','5','15'])
        self.assertEqual(info['projected_opna_period'],253)
        self.assertNotIn('yTL',command)
        self.assertEqual((vars(source.segment),source.runs),before)
        self.assertEqual(key[-2],tuple(source.runs))

    def test_loop_continuation_does_not_reset_flat_envelope(self):
        source=note(continuation=True,runs=[(9,1000)])
        command,_,_=render_note(source,{0:0,1000:10},2,TARGET_CLOCK_HZ)
        self.assertTrue(command.startswith('& @2 '))
        self.assertEqual(command.count('&'),1)
        self.assertNotIn(FLAT_ENVELOPE,command)

    def test_long_hold_chunks_are_tied_and_collapsed_controls_stay_ordered(self):
        source=note(runs=[(15,1000),(9,1000)])
        command,_,_=render_note(source,{0:0,1000:0,2000:280},1,TARGET_CLOCK_HZ)
        self.assertIn('v15 v9',command)
        self.assertEqual(re.findall(r'%([0-9]+)',command),['111','111','58'])
        self.assertEqual(command.count('&'),2)

    def test_invalid_pitch_run_coverage_and_timing_rejected(self):
        for period in (0,8,-1,4096):
            with self.assertRaises(ValueError):project_pitch(period,TARGET_CLOCK_HZ)
        with self.assertRaises(ValueError):project_pitch(253,0)
        source=note(runs=[(16,1000)])
        with self.assertRaises(ValueError):render_note(source,{0:0,1000:10},0,TARGET_CLOCK_HZ)
        source=note(runs=[(15,1000)]);source.length=2000
        with self.assertRaises(ValueError):render_note(source,{0:0,1000:10},0,TARGET_CLOCK_HZ)


if __name__=='__main__':unittest.main()
