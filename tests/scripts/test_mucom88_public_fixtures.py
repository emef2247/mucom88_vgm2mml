"""Public OPNA source-fixture contract; external compilers are not needed."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
import wave

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'py'))
sys.path.insert(0,str(ROOT/'scripts'))
from generate_mucom88_public_fixtures import encode
from opna_adpcm import analyze, decode_adpcm_b
from vgm_timing import command_times

FOLDER=ROOT/'tests/fixtures/public/opna/mucom88'

class Mucom88PublicFixturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads((FOLDER/'manifest.json').read_text(encoding='utf-8'))

    def test_authored_set_and_checked_in_hashes(self):
        self.assertEqual(len(self.manifest['fixtures']),10)
        for item in self.manifest['fixtures']:
            for suffix in ('muc','vgm'):
                raw=(FOLDER/(item['name']+'.'+suffix)).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),item[suffix+'_sha256'])
            text=(FOLDER/(item['name']+'.muc')).read_text(encoding='ascii')
            self.assertEqual(text.count('L'),1)
            self.assertNotIn('y', ' '.join(line.split(' ',1)[1] for line in text.splitlines()
                if line[:2] in ('A ','B ','C ','D ','E ','F ','H ','I ','J ','K ')))

    def test_declared_loop_targets_positive_time_and_replays(self):
        for item in self.manifest['fixtures']:
            raw=(FOLDER/(item['name']+'.vgm')).read_bytes()
            offset=struct.unpack_from('<I',raw,0x1c)[0]
            self.assertGreater(offset,0)
            events=list(command_times(raw,loop_count=2))
            start=next(e for e in events if e.address==0x1c+offset)
            self.assertEqual(start.vgmticks,item['loop_start_samples'])
            self.assertGreater(start.vgmticks,0)
            self.assertEqual(events[-1].vgmticks,item['total_samples']+item['loop_samples'])
            original=[(e.address,e.source_event_id) for e in events
                      if e.loop_iteration==0 and e.address>=start.address and e.command!=0x66]
            replay=[(e.address,e.source_event_id) for e in events if e.loop_iteration==1 and e.command!=0x66]
            self.assertEqual(original,replay)

    def test_all_six_fm_and_rhythm_parts_are_exercised(self):
        for name,register,expected in (('fm_six_channels',0x28,{0,1,2,4,5,6}),
                                       ('rhythm_six',0x10,{1,2,4,8,16,32})):
            raw=(FOLDER/(name+'.vgm')).read_bytes()
            data=[raw[e.address+2] for e in command_times(raw)
                  if e.command==0x56 and raw[e.address+1]==register]
            actual={v&7 for v in data if v&0xf0} if register==0x28 else {v&63 for v in data if not v&0x80}
            self.assertTrue(expected<=actual)

    def test_hardware_envelope_and_noise_source_writes(self):
        raw=(FOLDER/'ssg_hwenv.vgm').read_bytes()
        regs=[(raw[e.address+1],raw[e.address+2]) for e in command_times(raw) if e.command==0x56]
        self.assertTrue({9,10,13}<={v for r,v in regs if r==13})
        self.assertTrue(any(r==8 and v&16 for r,v in regs))
        raw=(FOLDER/'ssg_noise.vgm').read_bytes()
        noise={raw[e.address+2] for e in command_times(raw) if e.command==0x56 and raw[e.address+1]==6}
        self.assertTrue({4,16}<=noise)

    def test_pcm_payload_is_present_and_bound_to_expected_sample_slots(self):
        bank=(FOLDER/'synthetic_pcm.bin').read_bytes()
        for name,slots in (('pcm_samples',{1,2,3,4}),('pcm_rate_level',{1}),('mixed_intro',{2})):
            raw=(FOLDER/(name+'.vgm')).read_bytes()
            native=analyze(raw,loop_count=2)
            self.assertEqual(len(native.blocks),1)
            self.assertTrue(native.segments)
            expected={self.manifest['samples'][slot-1]['encoded_sha256'] for slot in slots}
            self.assertEqual({s.sha256 for s in native.samples},expected)
            for sample in native.samples:
                self.assertIn(sample.encoded_bytes,bank[1024:])
            self.assertTrue(all(s.sample_id is not None for s in native.segments))
            if name=='mixed_intro':
                self.assertEqual(len({s.playback_id for s in native.segments}),1)
                loop=next(x for x in self.manifest['fixtures'] if x['name']==name)['loop_start_samples']
                self.assertTrue(all(s.end_vgmticks<loop for s in native.segments))

    def test_pcm_bank_address_length_and_original_wave_forms(self):
        bank=(FOLDER/'synthetic_pcm.bin').read_bytes()
        for item in self.manifest['samples']:
            position=(item['slot']-1)*32
            start,size=struct.unpack_from('<HH',bank,position+28)
            self.assertEqual(start*4,item['bank_start_byte'])
            self.assertEqual(size,item['encoded_bytes'])
            chunk=bank[1024+start*4:1024+start*4+size]
            self.assertEqual(hashlib.sha256(chunk).hexdigest(),item['encoded_sha256'])
            with wave.open(str(FOLDER/item['wav']),'rb') as stream:
                self.assertEqual((stream.getnchannels(),stream.getsampwidth(),stream.getframerate()),(1,2,16000))
                values=struct.unpack('<'+'h'*stream.getnframes(),stream.readframes(stream.getnframes()))
                self.assertLess(min(values),0)
                self.assertGreater(max(values),0)

    def test_nominal_codec_reset_sign_and_high_nibble_order(self):
        # Manual Yamaha ADPCM-B step127: code7 adds238, newstep303,
        # code0 adds37; negative codesf/8 subtract those values.
        self.assertEqual(encode([238,275]),bytes.fromhex('70'))
        self.assertEqual(encode([-238,-275]),bytes.fromhex('f8'))
        self.assertEqual(decode_adpcm_b(bytes.fromhex('70')),[238,275])
        self.assertEqual(decode_adpcm_b(bytes.fromhex('f8')),[-238,-275])

if __name__=='__main__':
    unittest.main()
