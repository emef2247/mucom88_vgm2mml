"""Source-linked inner/outer structure and native MUCOM notation preservation."""
import csv
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts'),str(ROOT/'tests/scripts')]
from opna_muc import Unit,convert_vgm,render_track
from mucom_structure import limit_repeat_depth,repeat_depth,structure_track
from source_loop_plan import expanded_tokens
from test_mucom_fm_edges import authored_vgm
from mucom_compiler import compile_muc
from verify_mucom_native import verify
from vgm_io import read_vgm_bytes
from vgm_timing import command_times

TOOL=ROOT/'.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe'


def held_pattern(mask=15):
    raw=bytearray(authored_vgm(((mask<<4,0),)))
    body=bytearray(raw[256:-1]);total=0
    for index in range(12):
        if index:
            body.extend((0x56,0xa0,182 if index%2 else 106))
            body.extend((0x56,0x40,24 if index%2 else 20))
        body.extend(b'\x61'+struct.pack('<H',10991));total+=10991
    body.extend((0x56,0x28,0,0x61));body.extend(struct.pack('<H',686));total+=686
    body.append(0x66)
    raw=raw[:256]+body
    struct.pack_into('<I',raw,4,len(raw)-4)
    struct.pack_into('<I',raw,0x18,total)
    return bytes(raw)


def register_trace(path):
    raw=read_vgm_bytes(path)
    return [(event.vgmticks,event.command,tuple(raw[event.address+1:event.address+3]))
            for event in command_times(raw)
            if event.command in (0x56,0x57)]


class MucomStructureTests(unittest.TestCase):
    def test_source_inner_loops_keep_one_held_gate_and_all_members(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'held.vgm';source.write_bytes(held_pattern())
            report=convert_vgm(source,root/'out',song_loop=False,timing_bound_ms=.1,normalize_lengths=False)
            self.assertEqual(report['musical_note_units'],1)
            self.assertGreater(report['structure']['selected_inner_loops_before_depth_limit'],0)
            self.assertLess(report['structure']['structured_muc_chars'],report['structure']['plain_muc_chars'])
            for suffix in ('plain.muc','uncompacted.muc','control_units.csv','inner_loops.csv','inner_repeat_candidates.csv','compaction.csv'):
                self.assertTrue((root/'out'/('held.mucom.'+suffix)).is_file())
            with (root/'out/held.mucom.control_units.csv').open(encoding='utf-8') as stream:
                controls=list(csv.DictReader(stream))
            self.assertTrue(all(row['source_segment_ids'] for row in controls))
            self.assertGreater(len(controls),10)
            score=Path(report['muc_path']).read_bytes().decode('cp932')
            tracks=[line for line in score.splitlines() if line.startswith('A ')]
            self.assertGreater(len(tracks),1)
            self.assertTrue(all(len(line)<=110 for line in tracks))
            self.assertIn('[',score)
            self.assertNotIn(' L ',score)

    def test_projected_timing_selects_retained_wider_source_repeat(self):
        # Native period2 survives; projected length period3 requires width6.
        slices=tuple((i*100,(i+1)*100,'@1' if i==0 else '', 'c' if i%2==0 else 'd') for i in range(19))
        keys=tuple((100,('attack',) if i==0 else ('held',i%2)) for i in range(19))
        unit=Unit('A',0,1900,'fm',slices,('held_source',),control_keys=keys)
        clock={0:0}
        for i in range(19):clock[(i+1)*100]=clock[i*100]+(11,10,11)[i%3]
        audit={}
        _,_,_,text,_,_=render_track([unit],1900,None,clock,audit=audit)
        rows=[row for row in audit['inner_rows'] if row['status']=='applied' and row['target_selected']]
        self.assertTrue(any(row['planning_basis']=='target_timing' and row['unit_width']==6 for row in rows))
        self.assertEqual(expanded_tokens(audit['plain']),expanded_tokens(audit['uncompacted']))
        self.assertIn('[',text)

    def test_released_control_trajectory_has_inner_structure_without_note_ties(self):
        units=[];clock={i:i for i in range(65)}
        for sample in range(0,64,8):
            value=20 if sample%16==0 else 24
            units.append(Unit('A',sample,sample,'release_controls',
                ((sample,sample,f'yTL,1,{value}','r'),),((0x40,value),),(),(sample,)))
        audit={}
        full,_,_,text,_,_=render_track(units,64,None,clock,audit=audit)
        self.assertEqual(len(full),1)
        self.assertEqual(full[0].source_event_ids,tuple(range(0,64,8)))
        self.assertIn('[',text)
        self.assertNotIn('&',text)
        self.assertTrue(any(row['status']=='applied' for row in audit['inner_rows']))
        self.assertEqual(expanded_tokens(audit['plain']),expanded_tokens(audit['uncompacted']))

    def test_inner_spelling_cannot_hide_better_whole_phrase_structure(self):
        # Each gate contains an inner source repeat, with initialization differing.
        units=[];clock={i:i for i in range(0,97,8)}
        for start in (0,32,64):
            slices=tuple((start+i*8,start+(i+1)*8,'@1' if i==0 else '', 'c' if i%2==0 else 'd') for i in range(4))
            keys=tuple((8,('source_pitch',i%2)) for i in range(4))
            units.append(Unit('A',start,start+32,'fm',slices,('phrase',),control_keys=keys))
        audit={}
        _,_,folds,text,_,_=render_track(units,96,None,clock,audit=audit)
        self.assertTrue(any(row['status']=='applied' for row in folds))
        self.assertEqual(expanded_tokens(audit['plain']),expanded_tokens(audit['uncompacted']))
        self.assertLessEqual(repeat_depth(text),16)

    def test_each_outer_projection_report_belongs_to_its_own_tree(self):
        units=[Unit('A',i*100,(i+1)*100,'fm',((i*100,(i+1)*100,'@1','c' if i%2==0 else 'd'),),('pitch',i%2)) for i in range(18)]
        clock={0:0}
        for i in range(18):clock[(i+1)*100]=clock[i*100]+(11,10,11)[i%3]
        audit={}
        _,_,selected,_,_,_=render_track(units,1800,None,clock,audit=audit)
        native=audit['outer_projections']['native']
        feasible=audit['outer_projections']['target_timing']
        self.assertTrue(all(row['unit_width']==2 for row in native))
        self.assertTrue(any(row['unit_width']==6 for row in feasible))
        self.assertTrue(all(not row['target_selected'] for row in native))
        self.assertTrue(any(row['target_selected'] for row in feasible))
        self.assertTrue(any(row['unit_width']==6 for row in selected))

    def test_normalized_inner_view_removes_jitter_but_keeps_native_catalog(self):
        lengths=(101,99,102,98,103,97,104,96)
        starts=[0]
        for length in lengths:starts.append(starts[-1]+length)
        keys=tuple((length,('held_control',('pitch',index%2))) for index,length in enumerate(lengths))
        slices=tuple((starts[i],starts[i+1],'','c' if i%2==0 else 'd') for i in range(8))
        unit=Unit('A',0,starts[-1],'fm',slices,('source_gate',),
                  (7,8),tuple(range(8)),keys)
        commands=tuple('yTL,1,20 c%8 &' if i%2==0 else 'yTL,1,24 d%8 &' for i in range(8))
        plain=[' '.join(commands)]
        native,_,text,audit=structure_track([unit],plain,[commands],[keys],[(8,)*8],normalize_lengths=True)
        self.assertEqual(native.keys,(unit.key,))
        self.assertEqual(audit['children'][0].keys,keys)
        self.assertTrue(any(row['planning_basis']=='normalized' and row['target_selected']
                            and row['status']=='applied' for row in audit['inner_rows']))
        self.assertEqual(expanded_tokens(text),expanded_tokens(plain[0]))
        self.assertEqual(unit.source_segment_ids,(7,8))
        self.assertEqual(unit.source_event_ids,tuple(range(8)))
        # A differently accepted duration must still prevent that repeat.
        _,_,_,different=structure_track([unit],plain,[commands],[keys],[(8,9,10,11,12,13,14,15)],normalize_lengths=True)
        self.assertFalse(any(row['status']=='applied' for row in different['inner_rows']))

    def test_normalized_outer_view_keeps_attack_state_and_order(self):
        units=[];keys=[];commands=[];cursor=0
        for index,length in enumerate((101,99,102,98,103,97,104,96)):
            pitch=index%2
            key=((length,('attack',('pitch',pitch))),)
            units.append(Unit('A',cursor,cursor+length,'fm',
                ((cursor,cursor+length,'@1','c' if pitch==0 else 'd'),),('native',index),
                (index,),(index,),key))
            keys.append(key);commands.append(('@1 c%8' if pitch==0 else '@1 d%8',))
            cursor+=length
        plain=[row[0] for row in commands]
        native,rows,text,audit=structure_track(units,plain,commands,keys,[(8,)]*8,normalize_lengths=True)
        self.assertEqual(native.keys,tuple(unit.key for unit in units))
        self.assertEqual(audit['selected'],'normalized_outer_only')
        self.assertTrue(any(row['status']=='applied' and row['planning_basis']=='normalized' for row in rows))
        self.assertEqual(expanded_tokens(text),expanded_tokens(' '.join(plain)))
        self.assertFalse(audit['outer_projections']['native'])
        altered=list(keys);altered[2]=((102,('held_control',('pitch',0))),)
        _,_,_,changed=structure_track(units,plain,commands,altered,[(8,)]*8,normalize_lengths=True)
        self.assertNotEqual(changed['normalized_outer'].keys[0],changed['normalized_outer'].keys[2])

    def test_normalized_no_loops_keeps_views_and_auxiliary_raw_identity(self):
        units=[Unit('D',i*100,(i+1)*100,'ssg',((i*100,(i+1)*100,'','c'),),('source_time',i)) for i in range(4)]
        keys=[((100,('source_slice',unit.source_key,0)),) for unit in units]
        commands=[('c%8',)]*4
        _,rows,text,audit=structure_track(units,['c%8']*4,commands,keys,[(8,)]*4,
                                        loops=False,normalize_lengths=True)
        self.assertEqual(text,'c%8 c%8 c%8 c%8')
        self.assertEqual(rows,[])
        self.assertIn('normalized',audit['outer_projections'])
        self.assertEqual(len(set(audit['normalized_outer'].keys)),4)
        self.assertTrue(all(not row['target_selected'] for row in audit['inner_rows']))

    def test_depth_limit_expands_only_excess_and_retains_exact_sequence(self):
        source='['*17+'c%16 &'+']2'*17
        result,changes=limit_repeat_depth(source)
        self.assertEqual(repeat_depth(result),16)
        self.assertEqual(expanded_tokens(result),expanded_tokens(source))
        self.assertTrue(changes)

    def test_no_loops_retains_catalog_but_expands_all_finite_repeats(self):
        unit=Unit('A',0,64,'fm',tuple((i,i+8,'','c') for i in range(0,64,8)),
                  ('held',),control_keys=tuple((8,('pitch',)) for _ in range(8)))
        audit={}
        _,_,_,text,_,_=render_track([unit],64,None,{i:i for i in range(0,65,8)},loops=False,audit=audit)
        self.assertNotIn('[',text)
        self.assertEqual(audit['selected'],'expanded')
        self.assertTrue(audit['inner_rows'])
        self.assertTrue(all(not row['target_selected'] for row in audit['inner_rows']))

    @unittest.skipUnless(sys.platform=='win32' and TOOL.is_file(),'native MUCOM compiler/replay unavailable')
    def test_native_plain_and_structured_fm_masks_keep_register_timeline(self):
        for mask in (15,12):
            with self.subTest(mask=mask),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/'held.vgm';source.write_bytes(held_pattern(mask))
                out=root/'out';report=convert_vgm(source,out,song_loop=False,timing_bound_ms=.1,normalize_lengths=False)
                self.assertGreater(report['structure']['selected_inner_loops_before_depth_limit'],0)
                for name,score in (('plain',out/'held.mucom.plain.muc'),('structured',out/'held.muc')):
                    mub=out/(name+'.mub');compile_muc(score,mub)
                    result=subprocess.run([str(TOOL),str(mub)],cwd=out,capture_output=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(register_trace(out/'plain.vgm'),register_trace(out/'structured.vgm'))
                comparison=verify(source,out/'structured.mub',out/'comparison',projection=report['manifest_path'])
                self.assertEqual(comparison['status'],'passed',comparison['errors'])


if __name__=='__main__':unittest.main()
