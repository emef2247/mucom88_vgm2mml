"""Source musical units, target timing and native output integration."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts')]
from opna_muc import Unit,ConversionError,choose_clock,convert_vgm,render_track
from source_loop_plan import expanded_tokens
from verify_mucom_native import verify,_compare_timelines

FIXTURE=ROOT/'tests/fixtures/public/opna/mucom88'


def unit(start,end,kind='fm',slices=None):
    return Unit('A',start,end,kind,slices or ((start,end,'@1','c'),),('source',))


class MucomIntegrationTests(unittest.TestCase):
    def test_canonical_default_is_the_explicit_mucom_route(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,flags in (('default',()),('explicit',('--target','mucom'))):
                run=subprocess.run([sys.executable,str(ROOT/'vgm2mml.py'),
                    str(FIXTURE/'fm_notes.vgm'),'--outdir',str(root/name),*flags],
                    capture_output=True,text=True,timeout=30)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertTrue((root/name/'fm_notes.opna.music.csv').is_file())
            self.assertIn(b'#voice fm_notes.dat', (root/'default/fm_notes.muc').read_bytes())
            self.assertEqual((root/'default/fm_notes.muc').read_bytes(),
                             (root/'explicit/fm_notes.muc').read_bytes())
            self.assertEqual((root/'default/fm_notes.dat').read_bytes(),
                             (root/'explicit/fm_notes.dat').read_bytes())

    def test_no_song_loop_cli_keeps_source_evidence_and_finite_auxiliary_tracks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            run=subprocess.run([sys.executable,str(ROOT/'vgm2mml.py'),
                str(FIXTURE/'mixed_intro.vgm'),'--outdir',str(root),
                '--dump-passes','--no-song-loop'],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            report=json.loads((root/'mixed_intro.mucom.json').read_text(encoding='utf-8'))
            self.assertFalse(report['song_loop']['enabled'])
            self.assertIsNotNone(report['song_loop']['source_start_samples'])
            self.assertIsNone(report['song_loop']['target_start_samples'])
            self.assertFalse(any(track['loop'] for track in report['tracks'].values()))
            self.assertEqual(report['fm_loop_seams'],[])
            self.assertEqual(report['ssg_loop_seams'],[])
            self.assertTrue(report['pcm_bindings'])
            self.assertNotIn('L',(root/'mixed_intro.muc').read_bytes().decode('cp932').split())
            loop_files=list(root.glob('*.loop.csv'))
            self.assertEqual(len(loop_files),1)
            with loop_files[0].open(encoding='utf-8') as stream:
                self.assertEqual(next(csv.DictReader(stream))['status'],'valid')
            with (root/'mixed_intro.mucom.units.csv').open(encoding='utf-8') as stream:
                units=list(csv.DictReader(stream))
            end=report['timing']['source_boundaries'][-1]['source_sample']
            for part in report['tracks']:
                self.assertEqual(max(int(row['source_end']) for row in units if row['part']==part),end)

    def test_internal_slices_can_collapse_but_the_gate_remains(self):
        source=unit(0,44100,slices=((0,1,'@1','c'),(1,44100,'yTL,1,3','d')))
        ticks,report=choose_clock({'A':[source]},44100,None,8)
        self.assertEqual(ticks[0],ticks[1])
        self.assertGreater(ticks[44100],ticks[0])
        self.assertLessEqual(report['max_absolute_error_samples'],352.8)
        full,plan,folds,text,loop,depth=render_track([source],44100,None,ticks)
        self.assertEqual(len(full),1)
        self.assertIn('@1 yTL,1,3 d2',text)
        self.assertNotIn('&',text)

    def test_a_short_whole_gate_is_protected(self):
        with self.assertRaisesRegex(ConversionError,'positive gates'):
            choose_clock({'A':[unit(0,1)]},44100,None,8)

    def test_common_song_loop_and_held_seam_rejection(self):
        clock={0:0,44100:64,88200:128}
        _,_,_,a,owner,_=render_track([unit(44100,88200)],88200,44100,clock)
        intro=unit(0,44100)
        _,_,_,b,companion,_=render_track([intro],88200,44100,clock)
        self.assertTrue(owner)
        self.assertFalse(companion)
        self.assertEqual(a.count('L'),1)
        self.assertNotIn('L',b)
        with self.assertRaisesRegex(ConversionError,'held gate'):
            render_track([unit(0,88200)],88200,44100,clock)

    def test_phrase_repeats_keep_native_count_limits_and_exact_tokens(self):
        units=[unit(i*100,(i+1)*100) for i in range(300)]
        clock={i*100:i*8 for i in range(301)}
        _,_,folds,text,_,_=render_track(units,30000,None,clock)
        self.assertTrue(folds)
        self.assertIn(']255',text)
        self.assertEqual(expanded_tokens(text),expanded_tokens(' '.join(['@1 c16']*300)))

    def test_held_patch_source_is_two_gates_with_internal_control_evidence(self):
        source=ROOT/'tests/fixtures/public/opna/from_fm/legato_patch_mix/legato_patch_mix.vgm'
        with tempfile.TemporaryDirectory() as folder:
            report=convert_vgm(source,folder)
            self.assertEqual(report['musical_note_units'],2)
            self.assertEqual(report['source_sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
            with (Path(folder)/'legato_patch_mix.opna.music.csv').open(encoding='utf-8') as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),2)
            self.assertGreater(len(json.loads(rows[0]['control_trajectory'])),1)
            self.assertTrue(json.loads(rows[0]['source_segment_ids']))
            self.assertTrue(report['collapsed_control_intervals'])
            text=Path(report['muc_path']).read_bytes().decode('cp932')
            self.assertIn('&',text)
            self.assertIn('yTL,',text)
            self.assertNotIn('#pdx',text)

    def test_auxiliary_output_includes_source_pcm_bank_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            report=convert_vgm(FIXTURE/'mixed_intro.vgm',folder)
            self.assertTrue({'A','D','K'}.issubset(report['tracks']))
            self.assertTrue(report['pcm_bindings'])
            text=Path(report['muc_path']).read_bytes().decode('cp932')
            self.assertIn('#pcm mixed_intro_pcm.bin',text)
            self.assertTrue(Path(report['pcm_path']).is_file())
            self.assertTrue(Path(report['voice_path']).is_file())

    def test_native_timeline_rejects_shifted_and_extra_internal_controls(self):
        # The midpoint values would match despite this ten-sample boundary shift.
        expected=[(0,50,'quiet'),(50,100,'loud')]
        shifted=[(0,60,'quiet'),(60,100,'loud')]
        checks,errors=_compare_timelines(expected,shifted,'A',0)
        self.assertTrue(errors)
        self.assertTrue(any(c['error_samples']==10 for c in checks))
        _,extra=_compare_timelines(expected,[(0,25,'quiet'),(25,50,'other'),(50,100,'loud')],'A',0)
        self.assertTrue(any('interval count' in error for error in extra))

    def test_wrong_projection_source_is_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            report=convert_vgm(FIXTURE/'fm_notes.vgm',folder)
            with self.assertRaisesRegex(ValueError,'source hash'):
                verify(FIXTURE/'fm_voice_pan.vgm',Path(folder)/'missing.mub',
                       Path(folder)/'check',projection=report['manifest_path'])


if __name__=='__main__':unittest.main()
