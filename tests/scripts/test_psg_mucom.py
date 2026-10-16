"""Authored direct PSG/SSG and MUC force checks, without a native compiler."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'tests/scripts')]
from psg_mucom import build_source, musical_notes
from mucom_aux import project_ssg,SSG_PERIOD
from opna_muc import convert_vgm
from vgm_timing import command_times


def vgm(data,clock=1996800,chip_type=0,flags=1,loop_offset=None):
    raw=bytearray(0x100)+data+b'\x66';raw[:4]=b'Vgm '
    for offset,value in ((4,len(raw)-4),(8,0x171),(0x34,0xcc),(0x74,clock)):
        struct.pack_into('<I',raw,offset,value)
    raw[0x78]=chip_type;raw[0x79]=flags
    if loop_offset is not None:struct.pack_into('<I',raw,0x1c,0x100+loop_offset-0x1c)
    return raw


def w(register,data):return bytes((0xa0,register,data))
def wait(samples=4410):return b'\x61'+struct.pack('<H',samples)


def source(data,**options):
    raw=vgm(data,**options);events=list(command_times(raw));end=sum(e.wait_samples for e in events)
    return raw,events,build_source(raw,events,end)


class PsgMucomTests(unittest.TestCase):
    def test_pin26_clock_projection_preserves_native_periods(self):
        raw,events,native=source(w(0,32)+w(1,1)+w(7,0x3e)+w(8,15)+wait(),chip_type=0x10,flags=0x11)
        self.assertEqual(native.effective_clock_hz,998400)
        before=native.segments;corrections=[]
        items=project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,global_controls=corrections)
        prefix=items['D'][0][2];words=prefix.split()
        octave=int(next(x[1:] for x in words if x.startswith('o')))
        detune=int(next(x[1:] for x in words if x.startswith('D')))
        from mucom_aux import NOTES
        period=(SSG_PERIOD[NOTES.index(words[-1])] - detune)>>(octave-1)
        self.assertEqual(period,576)
        self.assertEqual(native.segments,before)
        self.assertEqual(next(s for s in native.segments[0] if s.scale!='r').tone_period,288)

    def test_envelope_retrigger_order_is_kept_in_global_lane(self):
        data=w(0,32)+w(1,1)+w(7,0x3e)+w(11,50)+w(12,0)+w(13,10)+w(8,16)+wait()
        loop_offset=len(data);data+=w(13,9)+w(13,10)+wait()+w(8,0)+wait()
        raw,events,native=source(data,loop_offset=loop_offset)
        controls=[];project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,global_controls=controls)
        self.assertEqual([shape for tick,register,shape,eid in controls if tick==4410],[9,10])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'PSG.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out',normalize_lengths=True)
            score=Path(report['muc_path']).read_bytes().decode('cp932')
            self.assertIn('y13,9 y13,10',score)
            self.assertTrue(report['normalization']['applied'])
            self.assertTrue(any(r['kind']=='psg_envelope_trigger' and
                r['start_vgmticks']==0 and r['end_vgmticks']==4410
                for r in report['normalization']['protected_intervals']))
            self.assertIn(' L',score)
            self.assertTrue(report['psg_projection']['source_ir_unchanged'])
            self.assertFalse(report['psg_projection']['via_opm'])
            with (root/'out/PSG.mucom.control_units.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertTrue(any(row['part']=='G' for row in rows))
            self.assertTrue(any('psg:0:' in row['source_segment_ids'] for row in rows))

    def test_shared_msx_notes_keep_decay_and_split_rise_and_zero_time_pitch(self):
        data=(w(0,32)+w(1,1)+w(7,0x3e)+w(8,15)+wait(1000)
              +w(8,10)+wait(500)+w(8,8)+wait(500)+w(8,12)+wait(1000)
              +w(0,64)+w(1,1)+wait(1000))
        raw,events,native=source(data)
        before=repr(native)
        notes=musical_notes(native)
        self.assertEqual([(n.start,n.length,n.runs) for n in notes[0]],
            [(0,2000,[(15,1000),(10,500),(8,500)]),
             (2000,1000,[(12,1000)]),(3000,1000,[(12,1000)])])
        items=project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,
            global_controls=[],musical_notes=notes)['D']
        self.assertEqual(len({item[3] for item in items}),3)
        self.assertEqual(sum('E255,' in item[2] for item in items),3)
        self.assertEqual(items[0][3],items[1][3])
        self.assertNotIn('E255,',items[1][2])
        self.assertEqual(repr(native),before)

    def test_shared_notes_do_not_reattack_held_hardware_envelope(self):
        data=(w(0,32)+w(1,1)+w(7,0x3e)+w(11,50)+w(13,10)+w(8,16)
              +wait(1000)+w(0,64)+w(1,1)+wait(1000))
        raw,events,native=source(data)
        notes=musical_notes(native)
        self.assertEqual(len(notes[0]),2)
        items=project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,
            global_controls=[],musical_notes=notes)['D']
        self.assertEqual(len({item[3] for item in items}),1)
        self.assertEqual(sum('S2 ' in item[2] for item in items),1)

    def test_normalized_loop_maps_redundant_inactive_and_eof_psg_writes(self):
        data=w(0,32)+w(1,1)+w(7,0x3e)+w(8,15)+wait()
        loop_offset=len(data)
        data+=w(8,15)+wait(17)+w(6,5)+wait(19)+w(14,7)+wait(4374)+w(14,8)
        raw,events,native=source(data,loop_offset=loop_offset)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'LOOP.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out')
            self.assertTrue(report['normalization']['applied'])
            mapping={row['source_sample']:row for row in report['timing']['source_boundaries']}
            self.assertFalse({c['vgmticks'] for c in native.controls}-mapping.keys())
            self.assertTrue(all(abs(row['error_samples'])<=352 for row in mapping.values()))
            self.assertTrue(report['ssg_loop_seams'])
            self.assertTrue(all(r['accepted'] for r in report['ssg_loop_analysis']))
            self.assertIn(' L',Path(report['muc_path']).read_bytes().decode('cp932'))
            with (root/'out/LOOP.psg_ssg.music.csv').open() as f:
                music=list(csv.DictReader(f))
            self.assertEqual(len([r for r in music if r['channel']=='0' and r['rest']=='False']),1)
            replay=json.loads((root/'out/LOOP.psg_ssg.source.json').read_text())
            self.assertEqual(report['normalization']['auxiliary_control_count'],len(replay['controls']))
            self.assertEqual(report['song_loop']['source_traversals'],2)

    def test_noise_only_does_not_validate_inactive_tone_period(self):
        raw,events,native=source(w(0,255)+w(1,15)+w(6,3)+w(7,0x37)+w(8,15)+wait(),clock=1000000)
        items=project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,global_controls=[])
        self.assertIn('P2 w6',items['D'][0][2])
        self.assertEqual(next(s for s in native.segments[0] if s.scale!='r').tone_period,4095)

    def test_force_envelope_conflict_only_mutes_overlap(self):
        data=(w(0,32)+w(1,1)+w(2,64)+w(3,1)+w(7,0x3c)+w(11,50)+w(12,0)
              +w(13,10)+w(8,16)+wait()+w(9,16)+wait()+w(9,0)+wait())
        raw,events,native=source(data)
        omissions=[]
        items=project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,global_controls=[],force=True,omissions=omissions)
        self.assertEqual([(a,b) for a,b,_,_ in items['D']],[(0,4410),(8820,13230)])
        self.assertNotIn('E',items)
        self.assertTrue(all(row['source_start_vgmticks']==4410 and row['source_end_vgmticks']==8820 for row in omissions))

    def test_force_omits_unrepresentable_noise_interval_then_recovers(self):
        data=w(0,32)+w(1,1)+w(7,0x3e)+w(8,15)+wait()+w(6,31)+w(7,0x36)+wait()+w(7,0x3e)+wait()
        raw,events,native=source(data,clock=1000000)
        with self.assertRaisesRegex(ValueError,'noise period'):
            project_ssg(native,events,raw,source_command=0xa0,
                effective_source_clock_hz=native.effective_clock_hz,global_controls=[])
        omissions=[];before=native.segments
        items=project_ssg(native,events,raw,source_command=0xa0,
            effective_source_clock_hz=native.effective_clock_hz,global_controls=[],force=True,omissions=omissions)
        self.assertEqual([(a,b) for a,b,_,_ in items['D']],[(0,4410),(8820,13230)])
        self.assertEqual(native.segments,before)
        self.assertEqual([(r['source_start_vgmticks'],r['source_end_vgmticks']) for r in omissions],[(4410,8820)])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'FORCE.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out',force=True,normalize_lengths=False,song_loop=False)
            self.assertEqual(report['force_projection']['omission_count'],1)
            registry=json.loads((root/'out/FORCE.mucom.force.json').read_text())
            self.assertEqual(registry['omissions'][0]['action'],'mute_ssg_interval')
            self.assertTrue(Path(report['muc_path']).is_file())


if __name__=='__main__':unittest.main()
