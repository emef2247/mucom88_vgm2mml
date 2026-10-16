"""Authored first-versus-repeated PSG state; no private or compiler regression."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'tests/scripts')]
from opna_muc import convert_vgm, source_occurrences
from psg_mucom import build_source
from test_psg_mucom import vgm,w,wait


class PsgReplayTests(unittest.TestCase):
    def test_delayed_restoration_keeps_first_pass_and_replays_carried_state(self):
        intro=w(0,32)+w(1,1)+w(7,0x3e)+w(8,12)+wait(2000)
        body=wait(1000)+w(0,64)+wait(1000)+w(0,96)+wait(1000)+w(14,7)
        raw=vgm(intro+body,loop_offset=len(intro))
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);path=root/'CARRY.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out')
            self.assertEqual(report['song_loop']['source_start_samples'],2000)
            self.assertEqual(report['song_loop']['source_end_samples'],5000)
            self.assertEqual(report['song_loop']['target_start_samples'],5000)
            self.assertEqual(report['song_loop']['playback_end_samples'],8000)
            self.assertTrue(report['ssg_loop_seams'])
            self.assertTrue(all(s['accepted'] for s in report['ssg_loop_analysis']))
            controls=json.loads((root/'out/CARRY.psg_ssg.source.json').read_text())['controls']
            first=[c for c in controls if c['loop_iteration']==0]
            repeated=[c for c in controls if c['loop_iteration']==1]
            self.assertEqual(len({c['source_event_id'] for c in controls}),len(controls))
            self.assertTrue(set(c['original_event_id'] for c in repeated)<=set(c['original_event_id'] for c in first))
            self.assertTrue(any(c['vgmticks']==5000 and c['register']==14 for c in first))
            self.assertTrue(any(c['vgmticks']==8000 and c['register']==14 for c in repeated))
            with (root/'out/CARRY.vgm.timing.csv').open() as f:
                reader={int(r['occurrence_event_id']):r for r in csv.DictReader(f)}
            for c in controls:
                row=reader[c['source_event_id']]
                self.assertEqual(int(row['source_event_id']),c['original_event_id'])
                self.assertEqual(int(row['vgmticks']),c['vgmticks'])
                self.assertEqual(int(row['original_vgmticks']),c['original_vgmticks'])
            native=build_source(raw,source_occurrences(raw,loop_count=2),8000)
            head=lambda t:next(s for s in native.segments[0] if s.vgmticks<=t<s.vgmticks_end)
            self.assertEqual(head(2000).tone_period,288)
            self.assertEqual(head(5000).tone_period,352)
            score=Path(report['muc_path']).read_bytes().decode('cp932')
            self.assertIn(' L',score)
            finite=convert_vgm(path,root/'finite',song_loop=False)
            self.assertEqual(finite['song_loop']['source_traversals'],1)
            self.assertEqual(finite['song_loop']['playback_end_samples'],5000)
            self.assertIsNone(finite['song_loop']['target_start_samples'])
            self.assertNotIn(' L',Path(finite['muc_path']).read_bytes().decode('cp932'))

    def test_wait_override_carries_into_replayed_body(self):
        intro=w(0,32)+w(1,1)+w(7,0x3e)+w(8,12)+b'\x62'
        body=b'\x62\x64\x62'+struct.pack('<H',882)
        raw=vgm(intro+body,loop_offset=len(intro))
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);path=root/'WAIT.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out')
            self.assertEqual(report['song_loop']['source_end_samples'],1470)
            self.assertEqual(report['song_loop']['target_start_samples'],1470)
            self.assertEqual(report['song_loop']['playback_end_samples'],2352)

    def test_short_fm_omission_selects_only_the_replayed_occurrence(self):
        from test_mucom_fm_edges import authored_vgm
        raw=bytearray(authored_vgm(()))[:-1]
        struct.pack_into('<I',raw,0x74,1996800);raw[0x79]=1
        raw+=w(0,32)+w(1,1)+w(7,0x3e)+w(8,12)+wait(1000)
        loop_address=len(raw)
        # First gate735samples, replay100samples after the carried wait override.
        raw+=b'\x56\x28\xf0\x62\x56\x28\x00'+wait(1000)+b'\x64\x62'+struct.pack('<H',100)+b'\x66'
        struct.pack_into('<I',raw,4,len(raw)-4)
        struct.pack_into('<I',raw,0x1c,loop_address-0x1c)
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);path=root/'FM.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out')
            self.assertTrue(report['normalization']['applied'])
            omitted=report['normalization']['omitted_fm_gates']
            self.assertEqual(len(omitted),1)
            self.assertEqual(omitted[0]['duration_samples'],100)
            self.assertGreater(omitted[0]['on_event_id'],omitted[0]['original_on_event_id'])
            with (root/'out/FM.mucom.output.music.csv').open() as f:
                gates=list(csv.DictReader(f))
            self.assertTrue(gates)
            self.assertFalse(any(int(g['source_start'])>=2735 for g in gates))

    def test_mixed_psg_msm_share_replay_end_and_boundary(self):
        from test_msm6258 import run
        intro=w(0,32)+w(1,1)+w(7,0x3e)+w(8,12)+wait(1000)
        raw=vgm(intro+run()+wait(989),loop_offset=len(intro))
        struct.pack_into('<I',raw,0x90,8000000);raw[0x94]=6
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);path=root/'MIX.vgm';path.write_bytes(raw)
            report=convert_vgm(path,root/'out',pcm_policy='best-effort')
            pcm=report['pcm_projection']
            self.assertEqual(report['song_loop']['playback_end_samples'],3000)
            self.assertEqual(pcm['source_end_vgmticks'],3000)
            self.assertEqual(pcm['source_loop_vgmticks'],2000)
            self.assertTrue(any(r['start_vgmticks']>=2000 for r in pcm['mapping']))
            self.assertTrue(Path(report['pcm_path']).is_file())


if __name__=='__main__':unittest.main()
