"""Target PCM eligibility must distinguish boundary edits and unused supplies."""
from dataclasses import replace
from dataclasses import replace
from pathlib import Path
import hashlib
import sys
from types import SimpleNamespace
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'py'))
from mucom_aux import project_pcm,ProjectionError
from opna_adpcm import AdpcmSample,AdpcmSegment,AdpcmIssue


def segment(index,start,end,continuation):
    return AdpcmSegment(index,0,continuation,0,start,end,(index,),0,start,
        16000,1,127,3,False,'parameter_change',None,None,False,
        ('controls_during_decoder_epoch','unsupported_clock_or_chip_configuration'),tuple([0]*17))


def analysis(segments):
    data=bytes(range(16))*4
    sample=AdpcmSample(0,'yamaha_adpcm_b_high_nibble_first',data,
        hashlib.sha256(data).hexdigest(),0,64,(0,))
    return SimpleNamespace(segments=tuple(segments),samples=(sample,),clock_raw=7987200,
        clock_hz=7987200,controls=(SimpleNamespace(register=0,data=128),),
        issues=(AdpcmIssue('unsupported_clock_or_chip_configuration',None,0,'extra header'),
                AdpcmIssue('controls_during_decoder_epoch',0,10,'boundary writes')))


def volume_header():
    raw=bytearray(256);raw[:4]=b'Vgm ';raw[8:12]=(0x170).to_bytes(4,'little')
    raw[0x34:0x38]=(0xe0-0x34).to_bytes(4,'little')
    raw[0xbc:0xc0]=(4).to_bytes(4,'little');raw[0xc0:0xc4]=(12).to_bytes(4,'little')
    raw[0xc8:0xcc]=(4).to_bytes(4,'little');raw[0xcc:0xd1]=bytes((1,0x87,0,0xa0,0x80))
    return bytes(raw)


class MucomPcmProjectionTests(unittest.TestCase):
    def test_unused_data_does_not_block_other_engines(self):
        source=analysis(());source.controls=()
        diagnostics=[]
        self.assertEqual(project_pcm(source,diagnostics=diagnostics),([],b'',[]))
        self.assertTrue(diagnostics)
        self.assertTrue(source.issues)

    def test_zero_time_boundary_edits_project_one_native_playback(self):
        source=analysis((segment(0,10,10,0),segment(1,10,100,1),segment(2,100,100,2)))
        before=source.segments;diagnostics=[]
        items,bank,bindings=project_pcm(source,raw=volume_header(),diagnostics=diagnostics)
        self.assertEqual(items[0][:2],(10,100))
        self.assertEqual(len(items),1)
        self.assertEqual(bank[1024:1088],source.samples[0].encoded_bytes)
        self.assertEqual(bindings[0]['source_sha256'],source.samples[0].sha256)
        self.assertEqual(source.segments,before)
        self.assertFalse(source.segments[1].independently_playable)
        self.assertTrue(any('boundary' in d for d in diagnostics))

    def test_positive_duration_unresolved_address_changes_remain_explicit(self):
        source=analysis((segment(0,10,50,0),segment(1,50,100,1)))
        source.segments=(source.segments[0],replace(source.segments[1],registers=(0,0,1)+tuple([0]*14)))
        with self.assertRaisesRegex(ProjectionError,'unsupported register'):
            project_pcm(source,raw=volume_header())

    def test_extra_clocks_are_not_ignored_as_volume_metadata(self):
        source=analysis((segment(0,10,100,0),))
        raw=bytearray(volume_header());raw[0xc4:0xc8]=(8).to_bytes(4,'little')
        with self.assertRaisesRegex(ProjectionError,'unsupported_clock_or_chip_configuration'):
            project_pcm(source,raw=raw)


if __name__=='__main__':unittest.main()
