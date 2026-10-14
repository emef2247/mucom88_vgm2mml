"""Reject unintentional content changes in extended-mode diagnostics."""
import copy
import struct
import unittest

from generate_mdx_mode_order_trials import verify_mode
from mdx_reference_expectations import ReferenceError, read_mdx


def score(extended=False, *, tone=b'', tone_first=False, no_tones=False, padding=b''):
    tracks=[b'\xfd\x00\xfb\x0c\xfc\x01\xa0\x0b\xf1\x00']+[b'\xf1\x00']*7+[b'\xed\x04\x81\x0b\xf1\x00']
    if extended:
        tracks[0]=b'\xe8'+tracks[0]
        tracks.extend([b'\xf1\x00']*7)
    header_size=2+len(tracks)*2
    offset=header_size+(len(tone) if tone_first else 0)
    starts=[]
    for track in tracks:
        starts.append(offset); offset+=len(track)
    tone_offset=0 if no_tones else header_size if tone_first else offset+len(padding)
    body=tone+b''.join(tracks)+padding if tone_first else b''.join(tracks)+padding+tone
    return b'Authored\r\n\x1aTEST\0'+struct.pack('>'+str(len(tracks)+1)+'H',tone_offset,*starts)+body


class ModeTrialTests(unittest.TestCase):
    def setUp(self):
        self.before=read_mdx(score())
        self.after=read_mdx(score(True),allow_pcm8=True)

    def test_only_mode_and_inactive_channels_change(self):
        verify_mode(self.before,self.after,True)

    def test_note_state_or_control_order_changes_rejected(self):
        altered=copy.deepcopy(self.after)
        altered['tracks'][0]['events'][0]['volume_encoded']=8
        with self.assertRaises(ValueError):
            verify_mode(self.before,altered,True)
        altered=copy.deepcopy(self.after)
        altered['tracks'][0]['commands'][1:3]=reversed(altered['tracks'][0]['commands'][1:3])
        with self.assertRaises(ValueError):
            verify_mode(self.before,altered,True)

    def test_active_extra_track_rejected(self):
        self.after['tracks'][9]['events'].append(dict(kind='note'))
        with self.assertRaises(ValueError):
            verify_mode(self.before,self.after,True)

    def test_no_tone_pointer_and_track_tail_padding(self):
        for extended in (False, True):
            with self.subTest(extended=extended):
                result=read_mdx(score(extended,no_tones=True,padding=b'\xee'*27),allow_pcm8=extended)
                self.assertTrue(result['complete'])
                self.assertEqual(result['tones'],[])
                self.assertIsNone(result['tone_start_file_offset'])
                self.assertEqual(result['tracks'][-1]['termination']['kind'],'finite_end')

    def test_tone_bank_before_or_after_tracks(self):
        tone=bytes([7,0x2f,15]+[0x31]*24)
        for extended in (False, True):
            for tone_first in (False, True):
                with self.subTest(extended=extended,tone_first=tone_first):
                    result=read_mdx(score(extended,tone=tone,tone_first=tone_first,padding=b'\xee'*27),allow_pcm8=extended)
                    self.assertTrue(result['complete'])
                    self.assertEqual([item['raw_hex'] for item in result['tones']],[tone.hex()])
                    self.assertEqual(result['tracks'][0]['events'][0]['duration_ticks'],12)

    def test_tone_region_overlap_and_partial_tone_rejected(self):
        data=bytearray(score(True))
        base=data.index(b'\0')+1
        struct.pack_into('>H',data,base,34)
        with self.assertRaises(ReferenceError):
            read_mdx(bytes(data),allow_pcm8=True)
        with self.assertRaises(ReferenceError):
            read_mdx(score(True,tone=b'\0'),allow_pcm8=True)


if __name__=='__main__':
    unittest.main()
