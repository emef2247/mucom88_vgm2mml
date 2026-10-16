"""Small authored OPM->OPNA musical projection checks; no private regression."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts'),str(ROOT/'tests/scripts')]
from opm_opna import project,nominal_frequency,opna_pitch,TARGET_CLOCK
from opna_muc import convert_vgm,ConversionError
from test_opm_mdx import analyze,write,wait
from test_opm_reader import vgm


def tone(ch=0):
    return write(0x20+ch,0xc7)+b''.join(write(base+bank*8+ch,value)
        for bank in range(4) for base,value in
        ((0x40,bank+1),(0x60,16+bank),(0x80,31),
         (0xa0,0),(0xc0,0),(0xe0,15)))+write(0x28+ch,0x4a)+write(0x30+ch,0)


class OpmOpnaTests(unittest.TestCase):
    def test_pitch_units_operator_banks_partial_keys_and_held_controls(self):
        data=(tone()+write(8,120)+wait(1000)+write(8,16)+wait(1000)
              +write(0x70,55)+wait(1000)+write(0x30,128)+wait(1000)+write(8,0)+wait(1000))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'FM.vgm';source.write_bytes(vgm(data,3579545))
            native=analyze(source,root/'source');before=repr(native)
            result=project(native)
            self.assertEqual(repr(native),before)
            keys=[r['target_data'] for r in result.writes if r['target_register']==0x28]
            self.assertEqual(keys,[0xf0,0x20,0])
            state=next(e.state for e in result.analysis.events if e.register==0x28 and e.state.key_mask==15)
            self.assertEqual([op.mul for op in state.operators],[1,2,3,4])
            self.assertTrue(any(e.state.operators[2].tl==55 and e.state.key_mask==2 for e in result.analysis.events))
            self.assertAlmostEqual(nominal_frequency(0x4a,0,3579545),440)
            self.assertLess(max(abs(r.get('error_cents',0)) for r in result.writes),1)
            report=convert_vgm(source,root/'out',song_loop=False)
            self.assertEqual(report['musical_note_units'],1)
            self.assertEqual(report['source_clock_hz'],TARGET_CLOCK)
            self.assertEqual(report['native_opm_clock_hz'],3579545)
            self.assertTrue((root/'out/FM.opm.segments.csv').is_file())
            self.assertTrue((root/'out/FM.opm_opna.target.segments.csv').is_file())
            score=Path(report['muc_path']).read_bytes().decode('cp932')
            self.assertIn('yTL,2,55',score)
            self.assertIn('KD128,0,128,128',score)

    def test_shared_high_latch_is_rewritten_for_interleaved_channels(self):
        data=(tone(0)+tone(1)+write(8,120)+write(8,121)+wait(1000)
              +write(0x29,0x5a)+wait(1000)+write(0x28,0x4a)+wait(1000))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'LATCH.vgm';source.write_bytes(vgm(data,3579545))
            result=project(analyze(source,root/'source'))
            end0=[e.state for e in result.analysis.events if e.ch==0][-1]
            end1=[e.state for e in result.analysis.events if e.ch==1][-1]
            freq=lambda s:s.fnum*2**s.block*TARGET_CLOCK/(144*2**20)
            self.assertLess(abs(freq(end0)-440),.3)
            self.assertLess(abs(freq(end1)-880),.6)

    def test_excess_channels_require_explicit_policy_even_with_force(self):
        data=b''.join(tone(ch)+write(8,120|ch) for ch in range(7))+wait(1000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'SEVEN.vgm';source.write_bytes(vgm(data))
            native=analyze(source,root/'source')
            for force in (False,True):
                with self.assertRaisesRegex(ValueError,'opm-channel-policy omit'):project(native,force=force)
            report=convert_vgm(source,root/'out',opm_channel_policy='omit',song_loop=False)
            self.assertEqual(report['musical_note_units'],6)
            self.assertEqual(report['opm_projection']['omitted_source_channels'],[6])
            self.assertFalse(report['force_projection']['force_requested'])
            from mucom_force import console_lines
            self.assertIn('omit_fm_channel','\n'.join(console_lines(root/'out','SEVEN')))
            self.assertTrue(any(r['action']=='omit_fm_channel' for r in report['force_projection']['omissions']))

    def test_force_diagnoses_and_neutralizes_dt2_lfo_noise_and_pan0(self):
        data=(tone(7)+write(0xd7,65)+write(0x0f,128)+write(0x19,255)+write(0x3f,16)
              +write(8,127)+wait(1000)+write(0x27,7)+wait(1000)
              +write(0x0f,0)+write(0x27,0xc7)+wait(1000)+write(8,7)+wait(1000))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'FORCE.vgm';source.write_bytes(vgm(data))
            with self.assertRaisesRegex(ConversionError,'DT2'):convert_vgm(source,root/'strict')
            report=convert_vgm(source,root/'out',force=True,song_loop=False)
            reasons={r['reason'] for r in report['force_projection']['omissions']}
            self.assertTrue({'opm_dt2_not_projected','opm_noise_not_projected',
                'opm_hardware_lfo_not_projected','native_muc_pan0_not_encodable'}<=reasons)
            with (root/'out/FORCE.opm_opna.target.state.csv').open() as stream:rows=list(csv.DictReader(stream))
            self.assertTrue(any(r['vgmticks']=='1000' and r['op1_tl']=='127' for r in rows))
            self.assertTrue(any(r['vgmticks']=='2000' and r['op4_tl']=='19' for r in rows))
            self.assertTrue(Path(report['muc_path']).is_file())

    def test_pitch_outside_target_range_is_explicit(self):
        data=tone()+write(0x28,0x7e)+write(8,120)+wait(1000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'RANGE.vgm';source.write_bytes(vgm(data,100000000))
            native=analyze(source,root/'source')
            with self.assertRaisesRegex(ValueError,'outside OPNA'):project(native)
            result=project(native,force=True)
            self.assertTrue(any(row.get('pitch_clamped') for row in result.writes))
            self.assertTrue(any(row['action']=='clamp_pitch_to_opna_range' for row in result.report['omissions']))

    def test_mixed_msm_loop_uses_shared_replay_and_existing_pcm_projection(self):
        from test_msm6258 import run
        intro=tone();body=write(8,120)+run()+wait(989)+write(8,0)+wait(1000)
        raw=vgm(intro+body);struct.pack_into('<I',raw,0x90,8000000);raw[0x94]=6
        struct.pack_into('<I',raw,0x1c,0xa0+len(intro)-0x1c)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'MIX.vgm';source.write_bytes(raw)
            report=convert_vgm(source,root/'out',pcm_policy='best-effort')
            self.assertEqual(report['song_loop']['source_traversals'],2)
            self.assertEqual(report['song_loop']['target_start_samples'],2000)
            self.assertEqual(report['pcm_projection']['source_loop_vgmticks'],2000)
            self.assertEqual(report['pcm_projection']['source_end_vgmticks'],4000)
            self.assertTrue(Path(report['pcm_path']).is_file())
            self.assertIn(' L',Path(report['muc_path']).read_bytes().decode('cp932'))


if __name__=='__main__':unittest.main()
