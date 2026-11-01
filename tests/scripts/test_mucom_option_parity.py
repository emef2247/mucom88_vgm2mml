"""Synthetic native-MUC option semantics without a compiler or private data."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'tests/scripts')]
from mucom_structure import planning_plan, structure_track
from mucomsx_macros import compact_tracks
from opna import OpnaRegisterState
from opna_music import semantic_state
from opna_muc import Unit, convert_vgm
from mucomsx_conversion import convert_vgm as convert_msx
from mucomsx_source import load_source
from psg_mucom import musical_notes
from source_loop_plan import expanded_tokens
from test_psg_mucom import source, w, wait
from test_mucomsx_source import vgm
from test_mucom_fm_edges import authored_vgm


class MucomOptionParityTests(unittest.TestCase):
    def test_score_candidates_do_not_replace_source_or_weaken_commands(self):
        state=replace(OpnaRegisterState().snapshot(0),key_mask=15,fnum=1000,block=4)
        units=[]
        command='@1 v15 o4 c%8'
        for i in range(4):
            native=semantic_state(replace(state,feedback=i))
            key=(8,('attack',native))
            units.append(Unit('A',i*8,(i+1)*8,'fm',((i*8,(i+1)*8,'@1 v15 o4','c'),),
                ('native_variant',i),control_keys=(key,)))
        args=(units,[command]*4,[(command,)]*4,[u.control_keys for u in units],[(8,)]*4)
        baseline=structure_track(*args)
        guided=structure_track(*args,score_guided_loops=True)
        self.assertNotIn('[',baseline[2])
        self.assertIn('[',guided[2])
        self.assertEqual(expanded_tokens(guided[2]),expanded_tokens(' '.join([command]*4)))
        self.assertEqual(len({u.source_key for u in units}),4)
        changed=[command,command+' D1',command,command+' D2']
        different=structure_track(units,changed,[(c,) for c in changed],
            [u.control_keys for u in units],[(8,)]*4,score_guided_loops=True)
        self.assertEqual(expanded_tokens(different[2]),expanded_tokens(' '.join(changed)))

    def test_legacy_selects_existing_retained_plan_and_takes_precedence(self):
        plan=planning_plan(('a','b','a','b'),legacy_loops=True,score_keys=('x',)*4)
        self.assertEqual(plan.keys,('a','b','a','b'))
        self.assertIsNone(plan.structure)
        self.assertFalse(hasattr(plan,'guide'))

    def test_native_macro_controls_and_enhanced_search_switch(self):
        body='H3,2,1 y$22,0 y0,1,12,34 yTL,1,24 o4 c%8'
        tracks={'A':[body]*6,'B':[body]*6}
        baseline=compact_tracks(tracks,enhance_macros=False)
        enhanced=compact_tracks(tracks,enhance_macros=True)
        self.assertEqual(baseline[2]['structured_search_status'],'disabled')
        self.assertEqual(enhanced[2]['structured_search_status'],'searched')
        self.assertLessEqual(enhanced[2]['selected_bytes'],baseline[2]['selected_bytes'])
        self.assertTrue(all(row['token_identity'] for row in enhanced[2]['expansion_proof'].values()))

    def test_volume_grouping_optout_retains_both_native_sources(self):
        data=w(0,254)+w(1,0)+w(7,0x3e)+w(8,4)+wait(1000)+w(8,15)+wait(1000)+w(8,8)+wait(1000)
        _,_,native=source(data)
        before=repr(native)
        grouped=musical_notes(native)
        baseline=musical_notes(native,software_volume_curves=False)
        self.assertEqual(len([n for n in grouped[0] if not n.rest]),1)
        self.assertEqual(len([n for n in baseline[0] if not n.rest]),2)
        self.assertEqual(repr(native),before)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'volume.vgm';path.write_bytes(vgm(data+b'\x66'))
            on=load_source(path,root/'on',song_loop=False)
            off=load_source(path,root/'off',song_loop=False,software_volume_curves=False)
            self.assertEqual(on.segments,off.segments)
            self.assertEqual(len([n for n in on.notes['psg'][0] if not n.rest]),1)
            self.assertEqual(len([n for n in off.notes['psg'][0] if not n.rest]),2)
            detail=json.loads((root/'off/volume.psg.source.continuation.json').read_text())
            self.assertFalse(detail['enabled'])

    def test_public_psg_and_scc_score_candidates_preserve_rendering(self):
        fixtures=ROOT/'tests/fixtures/public/mucomsx/source'
        with tempfile.TemporaryDirectory() as folder:
            for name in ('PSGSRC','SCCSRC'):
                report=convert_msx(fixtures/(name+'.vgm'),Path(folder)/name,
                    song_loop=False,score_guided_loops=True,enhance_macros=False)
                self.assertEqual(report['status'],'generated')
                self.assertTrue(list((Path(folder)/name).glob('*.score_candidate.csv')))
                self.assertTrue(all(p['token_identity'] for p in report['macros']['expansion_proof'].values()))

    def test_native_and_msx_conversions_report_effective_options_and_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            native=root/'native.vgm';native.write_bytes(authored_vgm(((0xf0,4000),(0,1000))))
            report=convert_vgm(native,root/'native',song_loop=False,score_guided_loops=True,
                enhance_macros=False,software_volume_curves=False)
            self.assertTrue(report['options']['score_guided_loops'])
            self.assertFalse(report['options']['software_volume_curves'])
            self.assertEqual(report['macros']['structured_search_status'],'disabled')
            self.assertTrue(list((root/'native').glob('*.score_candidate.csv')))
            msx=root/'opll.vgm'
            msx.write_bytes(vgm(bytes.fromhex('51 30 10 51 10 80 51 20 15 61 a0 0f 51 20 05 61 e8 03 66')))
            report=convert_msx(msx,root/'msx',song_loop=False,score_guided_loops=True,
                enhance_macros=False,software_volume_curves=False)
            self.assertTrue(report['options']['score_guided_loops'])
            self.assertEqual(report['macros']['structured_search_status'],'disabled')
            self.assertTrue(list((root/'msx').glob('*.score_candidate.csv')))
            legacy=convert_msx(msx,root/'legacy',song_loop=False,score_guided_loops=True,legacy_loops=True)
            self.assertFalse(legacy['options']['score_guided_loops'])
            self.assertTrue(legacy['options']['legacy_loops'])


if __name__=='__main__':unittest.main()
