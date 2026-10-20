"""Authored public MSX-to-MUCOMSX source/structure projection checks."""
from pathlib import Path
import csv
import json
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'py'))
from mucomsx_conversion import convert_vgm,ConversionError,clipped
from mucomsx_source import load_source
from test_mucomsx_source import vgm

ROOT=Path(__file__).resolve().parents[2]
FIXTURES=ROOT/'tests/fixtures/public/mucomsx/source'

class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def convert(self,raw,**options):
        source=self.root/'authored.vgm';source.write_bytes(raw)
        return convert_vgm(source,self.root/'out',**options)

    def test_public_psg_preserves_decay_with_one_attack_per_gate(self):
        r=convert_vgm(FIXTURES/'PSGSRC.vgm',self.root,song_loop=False)
        with (self.root/'PSGSRC.mucomsx.units.csv').open() as f:rows=list(csv.DictReader(f))
        sounding=[n for n in rows if n['rest']=='False']
        self.assertEqual(len(sounding),10)
        self.assertTrue(all(n['command'].count('E255')==1 for n in sounding))
        self.assertTrue(all('v15' in n['command'] and 'v10' in n['command'] and '&' in n['command'] for n in sounding))
        # Source repeats remain candidates even when differing quantized clocks
        # prevent an exact target fold; do not require a lossy emitted loop.
        with (self.root/'PSGSRC.psg0.source_structure.csv').open() as f:
            structure=list(csv.DictReader(f))
        self.assertLess(sum(int(row['structural_representative']) for row in structure),len(structure))
        self.assertTrue(all(proof['token_identity'] for proof in r['macros']['expansion_proof'].values()))
        self.assertTrue((self.root/'PSGSRC.psg0.source_structure.csv').is_file())
        with (self.root/'PSGSRC.msx.envelope_candidates.csv').open() as f:curves=list(csv.DictReader(f))
        self.assertEqual(json.loads(curves[0]['volume_runs']),[[15,1000],[10,2000]])

    def test_public_scc_uses_fm_and_keeps_original_wave_evidence(self):
        r=convert_vgm(FIXTURES/'SCCSRC.vgm',self.root,song_loop=False)
        text=Path(r['muc_path']).read_text(encoding='cp932')
        self.assertNotIn('#SSGExtend',text)
        self.assertNotIn('@W',text)
        self.assertEqual(set(r['tracks']),{'A'})
        self.assertEqual(r['native_scc_target_channels'],0)
        self.assertEqual(r['scc_projection_policy'],'all_opna_fm_additive4')
        self.assertTrue(all(route['target_chip']=='opna_fm' for route in r['routing']))
        bundle=load_source(FIXTURES/'SCCSRC.vgm',self.root/'source',song_loop=False)
        source_waves={n.segment.waveform_hex for n in bundle.notes['scc'][0] if not n.rest}
        self.assertEqual({v['source_waveform_hex'] for v in r['scc_opna_voices']},source_waves)
        with (self.root/'SCCSRC.mucomsx.units.csv').open() as f:units=list(csv.DictReader(f))
        sounding=[u for u in units if u['rest']=='False']
        self.assertTrue(sounding)
        self.assertTrue(all('yTL,1,' in u['command'] and 'yTL,4,' in u['command'] for u in sounding))
        self.assertTrue(Path(r['voice_bank']).is_file())

    def test_public_opll_uses_native_part_with_explicit_voice_assessment(self):
        r=convert_vgm(FIXTURES/'OPLLSRC.vgm',self.root,song_loop=False)
        self.assertIn('MSXA',r['tracks'])
        self.assertTrue(Path(r['voice_bank']).is_file())
        self.assertTrue(any(a['action']=='approximate_opll_voice' for a in r['assessments']))
        with (self.root/'OPLLSRC.opll.ch0.source_structure.csv').open() as f:
            structure=list(csv.DictReader(f))
        self.assertLess(sum(int(row['structural_representative']) for row in structure),len(structure))
        self.assertTrue(all(proof['token_identity'] for proof in r['macros']['expansion_proof'].values()))

    def test_all_five_scc_voices_use_fm_and_psg_remains_native(self):
        commands=bytearray()
        for i in range(128):commands.extend((0xd2,0,i,i&127))
        for ch in range(5):
            commands.extend((0xd2,1,ch*2,254,0xd2,1,ch*2+1,0,0xd2,2,ch,15))
        commands.extend(bytes.fromhex('a0 07 38 a0 00 fe a0 01 00 a0 02 ee a0 03 00 a0 04 de a0 05 00 a0 08 0f a0 09 0f a0 0a 0f'))
        commands.extend((0xd2,3,0,31,0x61,0xb8,0x0b,0x66))
        raw=vgm(commands)
        r=self.convert(raw,song_loop=False)
        self.assertEqual(r['native_scc_channels_retained'],5)
        self.assertEqual(set(r['tracks']),{'MSXD','MSXE','MSXF','A','B','C','H','I'})
        self.assertEqual(r['omissions'],[])
        self.assertEqual({x['source_channel'] for x in r['routing'] if x['target_chip']=='opna_fm'},set(range(5)))
        self.assertEqual({x['target_part'] for x in r['routing'] if x['source_chip']=='psg'},
                         {'MSXD','MSXE','MSXF'})
        self.assertTrue(all(x['target_chip']=='psg' for x in r['routing'] if x['source_chip']=='psg'))
        text=Path(r['muc_path']).read_text(encoding='cp932')
        self.assertNotIn('#SSGExtend',text)
        self.assertFalse(any(line.startswith(('D ','E ','F ')) for line in text.splitlines()))

    def test_short_omission_is_whole_source_gate_before_target_timing(self):
        commands=bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 64 00 a0 08 09 61 b8 0b a0 08 00 61 e8 03 a0 08 0f 61 60 00 a0 08 00 61 e8 03 66')
        r=self.convert(vgm(commands),song_loop=False)
        omissions=[a for a in r['omissions'] if a['action']=='omit_short_gate']
        self.assertEqual(len(omissions),1)
        self.assertEqual(omissions[0]['source_end']-omissions[0]['source_start'],96)
        self.assertEqual(r['musical_note_units'],1)
        self.assertEqual(r['source_end_sample'],r['target_end_source_sample'])
        with (self.root/'out/authored.msx.notes.csv').open() as f:source=list(csv.DictReader(f))
        self.assertGreater(len(source),r['musical_note_units'])

    def test_opll_split_keeps_matching_state_and_patch_item(self):
        source=self.root/'source.vgm'
        source.write_bytes(vgm(bytes.fromhex('51 30 10 51 10 80 51 20 15 61 e8 03 51 10 81 61 d0 07 51 20 05 66')))
        bundle=load_source(source,self.root/'passes',song_loop=False)
        note=next(n for n in bundle.notes['opll'][0] if not n.rest)
        view=clipped(note,1500,2500)
        self.assertEqual(len(view.sample_slices),len(view.sample_items))
        self.assertEqual(view.sample_slices[0].fnum,385)
        self.assertIs(view.sample_items[0],note.sample_items[1])
        self.assertEqual(note.start,0)

    def test_opll_excess_never_implicitly_becomes_opna(self):
        commands=bytearray()
        for ch in range(9):
            commands.extend((0x51,0x30+ch,0x10,0x51,0x10+ch,0x80,0x51,0x20+ch,0x17))
        commands.extend((0x61,0xb8,0x0b,0x66))
        with self.assertRaisesRegex(ConversionError,'no automatic OPNA'):
            self.convert(vgm(commands),song_loop=False)
        r=self.convert(vgm(commands),song_loop=False,force=True)
        self.assertEqual(set(r['tracks']),{'MSXA','MSXB','MSXC','MSXH','MSXI','MSXJ'})
        self.assertFalse(any(x['target_chip']=='opna_fm' for x in r['routing']))
        self.assertEqual({x['channel'] for x in r['omissions']},{6,7,8})

    def test_rhythm_uses_documented_native_part_and_assessment(self):
        commands=bytes.fromhex('51 36 00 51 0e 30 61 b8 0b 51 0e 20 61 b8 0b 66')
        r=self.convert(vgm(commands),song_loop=False)
        self.assertIn('MSXG',r['tracks'])
        self.assertFalse(r['rhythm_projection']['exact'])
        self.assertEqual(len(r['rhythm_projection']['attacks']),1)
        self.assertIn('@1 c%',Path(r['muc_path']).read_text(encoding='cp932'))

    def test_rhythm_loop_seam_continues_without_inventing_an_attack(self):
        commands=bytes.fromhex('51 36 00 51 0e 30 61 e8 03 51 0e 20 61 e8 03 51 0e 30 61 e8 03 66')
        r=self.convert(vgm(commands,loop=9))
        self.assertEqual(r['target_loop_source_sample'],3000)
        self.assertEqual([a['source_start_vgmticks'] for a in r['rhythm_projection']['attacks']],
                         [0,2000,4000])
        with (self.root/'out/authored.mucomsx.units.csv').open() as f:units=list(csv.DictReader(f))
        seam=next(u for u in units if u['part']=='MSXG' and int(u['source_start'])==3000)
        self.assertEqual(seam['continuation'],'True')
        self.assertTrue(seam['command'].startswith('& c%'))
        self.assertNotIn('@',seam['command'])
        self.assertIn(' L ',Path(r['muc_path']).read_text(encoding='cp932'))

    def test_actual_rhythm_attack_at_loop_seam_is_retained(self):
        commands=bytes.fromhex('51 36 00 51 0e 30 61 e8 03 51 0e 20 61 e8 03 66')
        r=self.convert(vgm(commands,loop=0))
        loop=r['target_loop_source_sample']
        with (self.root/'out/authored.mucomsx.units.csv').open() as f:units=list(csv.DictReader(f))
        seam=next(u for u in units if u['part']=='MSXG' and int(u['source_start'])==loop)
        self.assertEqual(seam['continuation'],'False')
        self.assertIn('@1 c%',seam['command'])
        self.assertFalse(seam['command'].startswith('&'))

    def test_repeatable_held_song_loop_keeps_L_and_source_first_pass(self):
        commands=bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 e8 03 61 d0 07 66')
        r=self.convert(vgm(commands,loop=15))
        self.assertEqual(r['target_loop_source_sample'],3000)
        self.assertEqual(r['target_end_source_sample'],5000)
        self.assertIn(' L ',Path(r['muc_path']).read_text(encoding='cp932'))

if __name__=='__main__':unittest.main()
