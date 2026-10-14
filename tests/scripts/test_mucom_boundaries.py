"""Observed boundary recovery with immutable evidence and native replay."""
import csv
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts'),str(ROOT/'tests/scripts')]
from opna_muc import convert_vgm
from opna_ssg_loop import prove_loop_states
from test_mucom_fm_edges import authored_vgm
from verify_mucom_native import verify,DEFAULT_TOOL
from mucom_compiler import compile_muc
from vgm_reader import parse_vgm
from vgm_io import read_vgm_bytes
from opna_ssg import build_segments
from vgm_timing import command_times


def pitchless_prefix(prefix=1,partial=False):
    pattern=((0xf0,0),(0xe0,901),(0,800)) if partial else ((0xf0,901),(0,800))
    raw=bytearray(authored_vgm(pattern))
    for command in (bytes((0x56,0xa4,26)),bytes((0x56,0xa0,106))):
        index=raw.index(command,256);del raw[index:index+3]
    position=raw.index(b'\x61'+struct.pack('<H',901),256)
    raw[position:position+3]=b'\x61'+struct.pack('<H',prefix)+bytes((0x56,0xa4,26,0x56,0xa0,106))+b'\x61'+struct.pack('<H',900)
    struct.pack_into('<I',raw,4,len(raw)-4)
    struct.pack_into('<I',raw,0x18,prefix+1700)
    return bytes(raw)


def ssg_loop(restore_delay=1,*,inactive=False):
    header=bytearray(256);header[:4]=b'Vgm '
    struct.pack_into('<I',header,8,0x171)
    struct.pack_into('<I',header,0x34,256-0x34)
    struct.pack_into('<I',header,0x48,7987200)
    body=bytearray();time=0
    def write(reg,value):body.extend((0x56,reg,value))
    def wait(samples):
        nonlocal time
        body.extend(b'\x61'+struct.pack('<H',samples));time+=samples
    for reg,value in ((0,136),(1,1),(7,62),(8,15),(6,3),(11,10)):
        write(reg,value)
    wait(13739);loop_position=256+len(body);loop_start=time
    wait(restore_delay)
    write(0,136);write(1,1)
    wait(13739)
    if inactive:
        write(6,7);write(11,20)
    else:
        write(0,44);write(1,2)
    wait(13739);body.append(0x66)
    struct.pack_into('<I',header,4,256+len(body)-4)
    struct.pack_into('<I',header,0x18,time)
    struct.pack_into('<I',header,0x1c,loop_position-0x1c)
    struct.pack_into('<I',header,0x20,time-loop_start)
    return bytes(header+body)


class BoundaryRecoveryTests(unittest.TestCase):
    def convert(self,raw,**kwargs):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name);source=root/'boundary.vgm';source.write_bytes(raw)
        return root,source,convert_vgm(source,root/'out',**kwargs)

    def test_unknown_initial_pitch_is_output_only_and_audited(self):
        root,source,report=self.convert(pitchless_prefix(),song_loop=False)
        self.assertTrue(report['fm_initial_pitch_resolution'])
        entry=report['fm_initial_pitch_resolution'][0]
        self.assertEqual(entry['source_end']-entry['source_start'],1)
        self.assertEqual(entry['target_clock'],report['timing']['source_boundaries'][0]['target_clock'])
        with (root/'out/boundary.opna.segments.csv').open(encoding='utf-8') as stream:
            source_rows=list(csv.DictReader(stream))
        self.assertTrue(any(r['key_mask']=='15' and not r['fnum'] for r in source_rows))
        self.assertTrue((root/'out/boundary.mucom.initial_pitch.csv').exists())
        self.assertEqual(report['musical_note_units'],1)
        self.assertEqual(report['normalization']['omitted_fm_gates'],[])

    def test_unknown_prefix_with_positive_target_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'positive target time'):
            self.convert(pitchless_prefix(20000),song_loop=False)

    def test_baseline_does_not_borrow_pitch_without_an_accepted_clock(self):
        with self.assertRaisesRegex(ValueError,'accepted normalization clock'):
            self.convert(pitchless_prefix(),normalize_lengths=False,song_loop=False)

    def test_ssg_same_clock_observed_restore_allows_split_and_keeps_members(self):
        root,source,report=self.convert(ssg_loop())
        self.assertEqual(report['ssg_loop_seams'][0]['mode'],'observed_head_restore')
        self.assertTrue(report['ssg_loop_seams'][0]['head_restore_event_ids'])
        self.assertTrue(all(row['equal'] for row in report['ssg_loop_seams'][0]['register_loop_checks']))
        with (root/'out/boundary.mucom.units.csv').open(encoding='utf-8') as stream:
            rows=list(csv.DictReader(stream))
        self.assertTrue(any(r['kind']=='song_loop' for r in rows))
        self.assertTrue(all(r['source_segment_ids']!='[]' for r in rows if r['kind']=='ssg'))
        text=Path(report['muc_path']).read_bytes().decode('cp932')
        self.assertIn('L',text.split())
        self.assertTrue(text.rstrip().endswith('&'))

    def test_ssg_delayed_restore_is_rejected_and_records_mismatch(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name);source=root/'delayed.vgm';source.write_bytes(ssg_loop(2000))
        with self.assertRaisesRegex(ValueError,'held gate across song-loop'):
            convert_vgm(source,root/'out')
        report=json.loads((root/'out/delayed.mucom.json').read_text())
        self.assertFalse(report['ssg_loop_analysis'][0]['accepted'])
        self.assertIn('positive target time',report['ssg_loop_analysis'][0]['reason'])
        self.assertFalse(report['ssg_loop_analysis'][0]['checks'][-1]['equal'])

    def test_inactive_noise_and_envelope_changes_are_ignored_but_retained(self):
        root,source,report=self.convert(ssg_loop(inactive=True))
        self.assertEqual(report['ssg_loop_seams'][0]['mode'],'stable_carry')
        self.assertTrue((root/'out/boundary.opna_ssg.segments.csv').exists())

    def test_future_activation_of_unrestored_noise_is_not_hidden(self):
        # Initially inactive noise can become active later in the loop.
        head=NS(registers=(136,1,0,0,0,0,3,62,15,0,0,0,0,0,0,0),prescale=6)
        tail=NS(registers=(136,1,0,0,0,0,7,62,15,0,0,0,0,0,0,0),prescale=6)
        writes=[(20,7,54,1),(30,7,62,2),(30,6,7,3)]
        passed,checks,ids,reason=prove_loop_states(head,tail,0,writes,{10:1,20:2,30:3,40:4},10,40)
        self.assertFalse(passed)
        self.assertFalse(checks[-1]['equal'])

    def test_stage_times_are_saved_on_success_and_rejection(self):
        root,source,report=self.convert(pitchless_prefix())
        self.assertTrue(report['stage_timings'])
        self.assertTrue(report['current_stage_completed'])
        self.assertTrue(all(value>=0 for value in report['stage_timings'].values()))
        source=root/'bad.vgm';source.write_bytes(pitchless_prefix(20000))
        with self.assertRaises(ValueError):convert_vgm(source,root/'bad')
        rejected=json.loads((root/'bad/bad.mucom.json').read_text())
        self.assertIn('native_target_projection',rejected['stage_timings'])
        self.assertTrue(rejected['current_stage_started_at_unix'])

    @unittest.skipUnless(sys.platform=='win32' and DEFAULT_TOOL.is_file(),'native tools unavailable')
    def test_native_initial_full_and_partial_key_order(self):
        for partial in (False,True):
            with self.subTest(partial=partial):
                root,source,report=self.convert(pitchless_prefix(partial=partial),song_loop=False)
                mub=root/'out/boundary.mub';compile_muc(Path(report['muc_path']),mub)
                result=verify(source,mub,root/'check',projection=report['manifest_path'])
                self.assertEqual(result['status'],'passed',result['errors'])

    @unittest.skipUnless(sys.platform=='win32' and DEFAULT_TOOL.is_file(),'native tools unavailable')
    def test_native_ssg_positive_states_match_two_source_traversals(self):
        root,source,report=self.convert(ssg_loop())
        mub=root/'out/boundary.mub';compile_muc(Path(report['muc_path']),mub)
        run=subprocess.run([str(DEFAULT_TOOL),str(mub)],cwd=mub.parent,capture_output=True,timeout=30,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(run.returncode,0)
        old,new={},{}
        parse_vgm(str(source),str(root/'source2'),opna_metadata=old,loop_count=2)
        parse_vgm(str(mub.with_suffix('.vgm')),str(root/'native'),opna_metadata=new)
        timing=report['timing'];clock={r['source_sample']:r['target_clock'] for r in timing['source_boundaries']}
        first_end=max(clock);start=report['song_loop']['source_start_samples'];spc=timing['samples_per_clock']
        before=build_segments(old['csv_path'],end_vgmticks=first_end+(first_end-start))
        raw=read_vgm_bytes(mub.with_suffix('.vgm'))
        native_end=next(e.vgmticks for e in command_times(raw,loop_count=1) if e.command==0x66)
        after=build_segments(new['csv_path'],end_vgmticks=native_end)
        def projected(sample):
            iteration=0
            if sample>first_end:
                iteration=(sample-start)//(first_end-start);sample=start+(sample-start)%(first_end-start)
            return (clock[sample]+iteration*(clock[first_end]-clock[start])+1)*spc
        checked=0
        for segment in before.segments[0]:
            a,b=projected(segment.vgmticks),projected(segment.vgmticks_end)
            if b-a<=4:continue
            point=(a+b)/2
            native=next(s for s in after.segments[0] if s.vgmticks<=point<s.vgmticks_end)
            self.assertEqual((segment.mode,segment.volume,segment.tone_period),
                             (native.mode,native.volume,native.tone_period))
            checked+=1
        self.assertGreaterEqual(checked,5)


if __name__=='__main__':unittest.main()
