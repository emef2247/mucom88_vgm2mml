"""Authored dynamic FM capacity/state tests; no private songs or compiler."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts'),str(ROOT/'tests/scripts')]
from test_opm_opna import tone
from test_opm_mdx import analyze,write,wait
from test_opm_reader import vgm
from opm_opna import project
from opna_muc import convert_vgm


class AllocationTests(unittest.TestCase):
    def test_eight_source_channels_reuse_slots_and_restore_latest_voice(self):
        data=b''.join(tone(ch) for ch in range(8))
        for ch in range(8):
            data+=write(0x60+ch,40+ch)+write(8,120|ch)+wait(1000)+write(8,ch)+wait(1000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'EIGHT.vgm';src.write_bytes(vgm(data))
            native=analyze(src,root/'source');before=repr(native)
            result=project(native,force=True)
            self.assertEqual(repr(native),before)
            self.assertEqual(result.report['channel_policy'],'dynamic')
            self.assertEqual(result.report['dynamic_allocation']['peak_ordered_source_keyed_voices'],1)
            acquisitions=[r for r in result.report['dynamic_allocation']['rows'] if r['action'] in ('acquire','reuse')]
            self.assertEqual([r['source_channel'] for r in acquisitions],list(range(8)))
            self.assertFalse(any(r['action']=='omit_fm_gate' for r in result.report['omissions']))
            self.assertTrue(any(r['action']=='truncate_possible_release' for r in result.report['omissions']))
            for claim in acquisitions:
                rows=[w for w in result.writes if w['source_event_id']==claim['source_event_id']]
                self.assertEqual(sum(w['reason']=='dynamic owner full operator restore' for w in rows),24)
                self.assertEqual(sum(w['reason']=='dynamic owner SSG-EG neutral restore' for w in rows),4)
                target=claim['target_channel'];port,local=divmod(target,3)
                self.assertTrue(any(w['target_port']==port and w['target_register']==0x40+local
                                    and w['target_data']==40+claim['source_channel'] for w in rows))
            report=convert_vgm(src,root/'out',force=True,song_loop=False)
            self.assertEqual(report['musical_note_units'],8)
            self.assertTrue(Path(report['muc_path']).is_file())
            with (root/'out/EIGHT.opm_opna.allocation.csv').open() as stream:
                self.assertTrue(any(row['source_channel']=='7' for row in csv.DictReader(stream)))

    def test_seventh_gate_omitted_until_full_keyoff_then_can_reenter(self):
        data=b''.join(tone(ch) for ch in range(8))
        data+=b''.join(write(8,120|ch) for ch in range(6))+wait(1000)
        data+=write(8,126)+wait(1000)+write(8,0)+wait(1000)
        data+=write(8,14)+wait(1000)+write(0x66,71)+write(8,6)+wait(1000)
        data+=write(8,126)+wait(1000)+write(8,6)
        data+=b''.join(write(8,ch) for ch in range(1,6))+wait(1000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'CAP.vgm';src.write_bytes(vgm(data))
            native=analyze(src,root/'source')
            with self.assertRaisesRegex(ValueError,'simultaneous'):project(native,channel_policy='dynamic')
            result=project(native,force=True)
            drops=[r for r in result.report['omissions'] if r['action']=='omit_fm_gate']
            self.assertEqual(len(drops),1)
            self.assertEqual((drops[0]['source_channel'],drops[0]['source_start_vgmticks'],drops[0]['source_end_vgmticks']),(6,1000,4000))
            self.assertFalse(any(w['source_channel']==6 and w['target_register']==0x28
                                 and 1000<=w['source_vgmticks']<5000 for w in result.writes))
            self.assertTrue(any(w['source_channel']==6 and w['source_vgmticks']==5000
                                and w.get('source_state_data')==71 for w in result.writes))
            report=convert_vgm(src,root/'out',force=True,song_loop=False)
            self.assertEqual(report['musical_note_units'],7)
            self.assertEqual(report['force_projection']['omissions'][0]['action'],'omit_fm_gate')

    def test_ordered_same_sample_conflict_and_loop_ownership_are_explicit(self):
        data=b''.join(tone(ch) for ch in range(7))
        data+=b''.join(write(8,120|ch) for ch in range(6))+wait(1000)
        data+=write(8,126)+write(8,0)+wait(1000)+write(8,6)+wait(1000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'ORDER.vgm';src.write_bytes(vgm(data))
            native=analyze(src,root/'source')
            result=project(native,force=True,loop_start=0)
            self.assertTrue(any(r['reason']=='opm_same_sample_order_capacity' for r in result.report['omissions']))
            self.assertFalse(result.report['dynamic_allocation']['loop']['stable'])
            self.assertTrue(any(r['action']=='omit_song_loop' for r in result.report['omissions']))

    def test_dynamic_loop_and_finite_fallback_share_pcm_timeline(self):
        import struct
        from test_msm6258 import run
        for unstable in (False,True):
            with self.subTest(unstable=unstable),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);src=root/'LOOP.vgm'
                intro=b''.join(tone(ch) for ch in range(8))
                if unstable:
                    intro+=b''.join(write(8,120|ch)+wait(1000) for ch in (5,6,2,0,1,3))
                    pairs=((4,15),(6,15),(1,0),(3,0),(4,0),(4,0),(7,15))
                    body=b''.join(write(8,(mask<<3)|ch)+wait(1000) for ch,mask in pairs)
                else:
                    body=b''.join(write(8,120|ch)+wait(1000)+write(8,ch)+wait(1000) for ch in range(8))
                body+=run()+wait(1000)
                raw=vgm(intro+body)
                struct.pack_into('<I',raw,0x90,8000000);raw[0x94]=6
                struct.pack_into('<I',raw,0x1c,0xa0+len(intro)-0x1c)
                src.write_bytes(raw)
                report=convert_vgm(src,root/'out',force=True,pcm_policy='best-effort')
                self.assertEqual(report['song_loop']['source_traversals'],2)
                self.assertEqual(report['opm_projection']['dynamic_allocation']['loop']['stable'],not unstable)
                self.assertEqual(report['song_loop']['enabled'],not unstable)
                self.assertEqual(report['pcm_projection']['source_end_vgmticks'],report['song_loop']['playback_end_samples'])
                score=Path(report['muc_path']).read_bytes().decode('cp932')
                loop_tokens=[token for line in score.splitlines() if line[:2] in tuple(p+' ' for p in 'ABCDEFGHIJK')
                             for token in line.split()[1:] if token=='L']
                if unstable:
                    self.assertEqual(loop_tokens,[])
                    self.assertTrue(any(r['action']=='omit_song_loop' for r in report['force_projection']['omissions']))
                else:self.assertTrue(loop_tokens)

    def test_cli_and_batch_preserve_auto_and_explicit_policy(self):
        import contextlib,io,importlib.util
        spec=importlib.util.spec_from_file_location('allocation_cli',ROOT/'vgm2mml.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        result=dict(muc_path='authored.muc',musical_note_units=0,timing=dict(timer_b=200))
        for extra,expected in (([], 'auto'),(['--opm-channel-policy','omit'],'omit'),(['--opm-channel-policy','dynamic'],'dynamic')):
            with patch('opna_muc.convert_vgm',return_value=result) as convert,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['authored.vgm','--force',*extra]),0)
            self.assertEqual(convert.call_args.kwargs['opm_channel_policy'],expected)
        import export_mucom
        with patch.object(sys,'argv',['export_mucom.py','authored.vgm','--outdir','unused','--force']),patch.object(export_mucom,'run_batch',return_value=[]) as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(export_mucom.main(),0)
        self.assertEqual(run.call_args.kwargs['opm_channel_policy'],'auto')

    def test_released_owner_controls_and_unsupported_snapshot_are_retained(self):
        data=b''.join(tone(ch) for ch in range(8))
        data+=write(8,120)+wait(1000)+write(8,0)+write(0x60,99)+wait(1000)
        data+=write(0xc7,65)+write(8,127)+wait(1000)+write(8,7)+wait(1000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'STATE.vgm';src.write_bytes(vgm(data))
            native=analyze(src,root/'source');result=project(native,force=True,channel_policy='dynamic')
            self.assertTrue(any(w['source_channel']==0 and w['source_vgmticks']==1000 and w['target_data']==99 for w in result.writes))
            self.assertTrue(any(r['reason']=='opm_dt2_not_projected' and r.get('source_state_register')==0xc7 for r in result.report['omissions']))
            report=convert_vgm(src,root/'out',force=True,opm_channel_policy='dynamic',song_loop=False)
            self.assertEqual(report['musical_note_units'],2)
            # Full voice ownership restoration must not borrow missing source fields.
            broken=vgm(tone(0)+write(8,120)+wait(1000)+write(8,0)+wait(1000)+write(0x27,0xc7)+write(8,127)+wait(1000))
            src.write_bytes(broken);native=analyze(src,root/'incomplete')
            with self.assertRaisesRegex(ValueError,'complete observed'):project(native,force=True,channel_policy='dynamic')


if __name__=='__main__':unittest.main()
