"""Continuous native PCM control trajectories and their MUCOM target adapter."""
from bisect import bisect_right
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts')]
from opna_adpcm import analyze
from opna_adpcm_playback import build_epochs
from opna_muc import convert_vgm,ConversionError
from mucom_aux import project_pcm,ProjectionError
from mucom_compiler import compile_muc,PCM_COMPILER,PCM_REPLAY,requires_pcm_extension
from vgm_io import read_vgm_bytes
from vgm_timing import command_times


from opna_pcm_fixture import authored_pcm


def pcm_writes(path):
    raw=read_vgm_bytes(path);rows=[]
    for event in command_times(raw):
        if event.command==0x57 and raw[event.address+1]<=16:
            rows.append((event.vgmticks,raw[event.address+1],raw[event.address+2]))
    return rows


class PcmControlTests(unittest.TestCase):
    def test_one_epoch_keeps_controls_and_native_ir(self):
        native=analyze(authored_pcm(((4410,11,80),(8820,1,0x41),(13230,9,0x34),(13230,10,0x12))))
        before=native.segments;projection={}
        items,bank,bindings=project_pcm(native,projection=projection)
        self.assertEqual(native.segments,before)
        self.assertTrue(all(not s.independently_playable for s in native.segments))
        self.assertEqual(len({item[3] for item in items}),1)
        self.assertEqual(sum('@' in item[2] for item in items),1)
        self.assertIn('y0,1,11,80',items[1][2])
        self.assertIn('y0,1,1,64',items[2][2])
        self.assertIn('y0,1,9,52 y0,1,10,18',items[-1][2])
        self.assertEqual(bank[1024:],native.samples[0].encoded_bytes)
        self.assertEqual(bindings[0]['source_sha256'],native.samples[0].sha256)
        self.assertEqual(projection['compiler_requirements'],['multi_port_y'])

    def test_nominal_positions_integrate_rates_and_zero_rate_pause(self):
        native=analyze(authored_pcm(((4410,11,80),(8820,9,0),(8820,10,0),
            (13230,9,0x82),(13230,10,0x4a))))
        epochs=build_epochs(native);self.assertEqual(len(epochs),1)
        e=epochs[0]
        rate=Fraction(7987200*0x4a82,144*65536)
        self.assertEqual(e.positions[-1].position_end,Fraction(17640,44100)*rate)
        paused=[s for s in e.positions if s.rate==0 and s.end_vgmticks>s.start_vgmticks]
        self.assertEqual(len(paused),1)
        self.assertEqual(paused[0].position_start,paused[0].position_end)

    def test_natural_end_is_inferred_without_shortening_source_evidence(self):
        native=analyze(authored_pcm(((100,11,80),),size=256))
        before=native.segments;epoch=build_epochs(native)[0]
        rate=Fraction(7987200*0x4a82,144*65536)
        self.assertEqual(epoch.nominal_end,Fraction(512*44100,1)/rate)
        self.assertEqual(epoch.end_vgmticks,22050)
        self.assertEqual(epoch.positions[-1].position_end,512)
        self.assertEqual(native.segments,before)

    def test_memory_address_repeat_and_prescaler_changes_remain_rejected(self):
        cases=(((4410,11,80),(8820,1,0xc2)),((4410,11,80),(8820,2,1)),
               ((4410,11,80),(8820,8,1)))
        for changes in cases:
            with self.subTest(changes=changes),self.assertRaises(ProjectionError):
                project_pcm(analyze(authored_pcm(changes)))
        native=analyze(authored_pcm(((4410,11,80),)))
        for altered in (replace(native,segments=tuple(replace(s,repeat=True) for s in native.segments)),
                        replace(native,segments=tuple(replace(s,issues=s.issues+('unsupported_adpcm_prescaler',)) for s in native.segments))):
            with self.assertRaises(ProjectionError):project_pcm(altered)

    def test_normalized_unit_retains_one_epoch_and_inspectable_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'controls.vgm'
            source.write_bytes(authored_pcm(((4410,11,80),(4411,1,0x41),(8820,9,0x34),(8820,10,0x12))))
            report=convert_vgm(source,root/'out',song_loop=False)
            self.assertTrue(report['normalization']['enabled'])
            self.assertFalse(report['normalization']['omitted_pcm_gates'])
            self.assertEqual(report['pcm_projection']['source_ir_unchanged'],True)
            muc=Path(report['muc_path']).read_bytes().decode('cp932')
            self.assertEqual(muc.count('@1'),1)
            self.assertIn('&',muc);self.assertIn('y0,1,11,80',muc)
            self.assertTrue(requires_pcm_extension(muc))
            self.assertTrue((root/'out/controls.opna_adpcm.playback.csv').is_file())
            audit=report['pcm_projection']['target_position_audit']
            self.assertEqual(len(audit),1)
            self.assertIn('position_difference_nibbles',audit[0])
            with (root/'out/controls.mucom.units.csv').open(encoding='utf-8') as stream:
                import csv
                units=[r for r in csv.DictReader(stream) if r['kind']=='pcm']
            self.assertEqual(len(units),1)

    def test_a_song_loop_inside_a_pcm_epoch_remains_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'loop.vgm'
            source.write_bytes(authored_pcm(((4410,11,80),(8820,1,0x41)),loop_tick=8820))
            with self.assertRaisesRegex(ConversionError,'held gate'):
                convert_vgm(source,root/'out')

    def test_native_profile_is_required_only_for_multiport_commands(self):
        self.assertTrue(requires_pcm_extension('K y0,1,11,80 c4'))
        self.assertFalse(requires_pcm_extension('A y40,242 c4 ; y0,1,11,80'))


@unittest.skipUnless(sys.platform=='win32' and PCM_COMPILER.is_file() and PCM_REPLAY.is_file(),
                    'extended native PCM compiler/replay profile unavailable')
class NativePcmControlTests(unittest.TestCase):
    def native(self,changes,*,end=22050,memory_mode=0xc1):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'control.vgm'
        source.write_bytes(authored_pcm(changes,end=end,memory_mode=memory_mode))
        report=convert_vgm(source,root/'out',song_loop=False)
        muc=Path(report['muc_path']);mub=muc.with_suffix('.mub')
        compile_muc(muc,mub) # automatic extended-profile selection
        replay=subprocess.run([str(PCM_REPLAY),str(mub)],cwd=muc.parent,capture_output=True,
                              timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(replay.returncode,0,replay.stdout+replay.stderr)
        writes=pcm_writes(mub.with_suffix('.vgm'))
        # Read source/native bus traces independently from the MUC projection.
        def snapshots(rows):
            registers=[0]*17;states=[]
            for tick,register,value in rows:
                registers[register]=value;states.append((tick,tuple(registers)))
            return states
        original=snapshots(pcm_writes(source));actual=snapshots(writes)
        original_times=[row[0] for row in original];actual_times=[row[0] for row in actual]
        mapping={row['source_sample']:row['target_clock'] for row in report['timing']['source_boundaries']}
        spc=report['timing']['samples_per_clock']
        boundaries=sorted({0,end,*[tick for tick,_,_ in pcm_writes(source)]})
        for left,right in zip(boundaries,boundaries[1:]):
            expected=original[bisect_right(original_times,(left+right)/2)-1][1]
            if not expected[0]&128 or expected[0]&1:continue
            lower=mapping[left]*spc+2;upper=mapping[right]*spc-2
            if upper<=lower:continue # intentionally collapsed target control interval
            points=sorted({lower,upper,*[tick for tick in actual_times if lower<tick<upper]})
            for a,b in zip(points,points[1:]):
                observed=actual[bisect_right(actual_times,(a+b)/2)-1][1]
                with self.subTest(source_span=(left,right),native_span=(a,b)):
                    self.assertEqual(observed[11],expected[11])
                    self.assertEqual(observed[1]&192,expected[1]&192)
                    self.assertEqual(observed[9]+256*observed[10],expected[9]+256*expected[10])
        return report,muc.read_bytes().decode('cp932'),writes

    def test_live_volume_pan_rate_pause_and_long_ties_do_not_restart(self):
        changes=((4410,11,80),(8820,1,0x41),(13230,9,0),(13230,10,0),
                 (17640,9,0x34),(17640,10,0x12),(22050,11,255))
        report,score,writes=self.native(changes,end=110250)
        starts=[row for row in writes if row[1]==0 and row[2]&128]
        self.assertEqual(len(starts),1)
        allocation={r['source_sample']:r['target_clock'] for r in report['timing']['source_boundaries']}
        spc=report['timing']['samples_per_clock']
        expected={4410:[(11,80)],8820:[(1,64)],13230:[(9,0),(10,0)],
                  17640:[(9,52),(10,18)],22050:[(11,255)]}
        for source_time,changes in expected.items():
            expected_time=allocation[source_time]*spc
            actual=[(reg,data) for tick,reg,data in writes if abs(tick-expected_time)<=2 and reg in (1,9,10,11)]
            for change in changes:self.assertIn(change,actual)
        self.assertEqual(score.count('@1'),1)
        self.assertTrue(any(reg==0 and val&1 for _,reg,val in writes))
        self.assertGreater(len(score),0)

    def test_initial_one_sample_control_keeps_attack_before_live_updates(self):
        report,score,writes=self.native(((1,11,80),(1,1,0x41),(1,9,0x34),(1,10,0x12)))
        mapping={r['source_sample']:r['target_clock'] for r in report['timing']['source_boundaries']}
        self.assertGreater(mapping[1],mapping[0])
        self.assertEqual(len([r for r in writes if r[1]==0 and r[2]&128]),1)
        self.assertTrue(any(r['kind']=='pcm_attack' for r in report['normalization']['protected_intervals']))
        self.assertLess(score.index('@1'),score.index('y0,1,11,80'))

    def test_same_timestamp_changes_remain_ordered_without_restart(self):
        report,_,writes=self.native(((4410,11,80),(4410,11,60),
                                     (4410,1,0x41),(4410,1,0x81)))
        tick=next(row['target_clock'] for row in report['timing']['source_boundaries'] if row['source_sample']==4410)
        expected=tick*report['timing']['samples_per_clock']
        changes=[(reg,value) for t,reg,value in writes if abs(t-expected)<=2 and reg in (1,11)]
        self.assertEqual(changes,[(11,80),(11,60),(1,64),(1,128)])
        self.assertEqual(len([r for r in writes if r[1]==0 and r[2]&128]),1)

    def test_ram8_source_pan_changes_use_repacked_four_byte_target_mode(self):
        report,_,writes=self.native(((4410,11,80),(8820,1,0x42)),memory_mode=0xc2)
        self.assertEqual(report['pcm_bindings'][0]['encoded_length'],8192)
        self.assertIn(64,[value for _,reg,value in writes if reg==1])
        self.assertFalse(any(value&63 for _,reg,value in writes if reg==1))

    def test_combined_public_fm_and_controlled_pcm_uses_measured_replay_profile(self):
        from vgm_io import read_vgm_header
        from verify_mucom_native import verify
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            fm=read_vgm_bytes(ROOT/'tests/fixtures/public/opna/mucom88/fm_notes.vgm')
            pcm=authored_pcm(((4410,11,80),(8820,1,0x41)))
            fm_end=next(e.address for e in command_times(fm) if e.command==0x66)
            raw=bytearray(pcm[:256]+fm[read_vgm_header(fm)['data_start']:fm_end]+pcm[256:])
            struct.pack_into('<I',raw,4,len(raw)-4)
            struct.pack_into('<I',raw,0x18,int.from_bytes(fm[0x18:0x1c],'little')+22050)
            source=root/'mixed.vgm';source.write_bytes(raw)
            report=convert_vgm(source,root/'out',song_loop=False)
            muc=Path(report['muc_path']);mub=muc.with_suffix('.mub');compile_muc(muc,mub)
            result=verify(source,mub,root/'check',projection=report['manifest_path'])
            self.assertEqual(result['status'],'passed',result['errors'])
            self.assertEqual(result['assumed_native_startup_samples'],0)

    def test_next_epoch_starts_once_and_restores_its_own_controls(self):
        changes=((4410,11,80),(8820,1,0x41),(13230,0,1),(17640,11,120),
                 (17640,1,0xc1),(17640,9,0x82),(17640,10,0x4a),(17640,0,0xa0))
        _,score,writes=self.native(changes,end=26460)
        self.assertEqual(len([r for r in writes if r[1]==0 and r[2]&128]),2)
        self.assertEqual(score.count('@1'),2)
        self.assertIn(120,[value for _,reg,value in writes if reg==11])


if __name__=='__main__':unittest.main()
