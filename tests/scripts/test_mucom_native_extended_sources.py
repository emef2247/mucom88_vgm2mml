"""Authored existing OPN-family source integration; no target voice conversion."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'py'))
from musical_hal import build_native_performance, build_score_guide, write_native_vgm
from musical_hal.native_imports.vgm_timing import command_times
from musical_hal.native_source_music import _fm_pitch
from musical_hal.native_vgm import scheduled_native_writes
from mucom_blueprint import render


def authored(commands, clocks):
    raw=bytearray(256)
    raw[:4]=b'Vgm '
    struct.pack_into('<I',raw,8,0x171)
    struct.pack_into('<I',raw,0x34,256-0x34)
    for offset,clock in clocks.items():struct.pack_into('<I',raw,offset,clock)
    raw.extend(commands)
    raw.append(0x66)
    struct.pack_into('<I',raw,4,len(raw)-4)
    return bytes(raw)


def operations(raw):
    events=tuple(command_times(raw))
    return [(event.vgmticks,raw[event.address:following.address])
            for event,following in zip(events,events[1:])
            if event.command not in (0x61,0x62,0x63,0x64) and not 0x70<=event.command<=0x7f]


class ExtendedNativeSourcesTests(unittest.TestCase):
    def check_products(self,raw,expected_devices):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            performance=build_native_performance(raw,root,'authored')
            self.assertEqual({d.device_id for d in performance.devices},set(expected_devices))
            self.assertTrue(all(type(c.sample) is int for c in performance.controls))
            self.assertEqual([(r['sample'],r['command']) for r in scheduled_native_writes(performance)],operations(raw))
            reference=root/'reference.vgm'
            write_native_vgm(performance,reference,mapping_path=root/'mapping.csv')
            self.assertEqual(operations(reference.read_bytes()),operations(raw))
            for device in performance.devices:
                offset=dict(device.parameters)['header_clock_offset']
                self.assertEqual(int.from_bytes(reference.read_bytes()[offset:offset+4],'little'),device.clock_raw)
            guide=build_score_guide(performance,repeats=True)
            artifacts,report=render(performance,guide,'authored')
            self.assertTrue(artifacts)
            self.assertGreater(report['sounding_parts'],0)
            source=json.loads((root/'authored.native.source.json').read_text())
            self.assertFalse(source['errors'])
            for chip in ('opn','opn2'):
                if not any(d.chip==chip for d in performance.devices):continue
                with (root/f'authored.native.{chip}.segments.csv').open(newline='',encoding='utf-8') as stream:
                    segments=list(csv.DictReader(stream))
                self.assertTrue(segments)
                self.assertTrue(any(u.section=='fm' and dict(u.parameters).get('native_segment_ids') for u in performance.units if u.device_id.startswith(chip+':')))
            return performance

    def test_opn_fm_and_ssg_share_integer_source_clock_and_reference(self):
        raw=authored(bytes.fromhex(
            '55 b0 07 55 a4 22 55 a0 69 55 28 f0 '
            '55 00 40 55 01 01 55 07 3e 55 08 0f '
            '61 11 00 55 40 18 55 2f 00 61 60 22 55 28 00 55 08 00'),{0x44:3993600})
        performance=self.check_products(raw,('opn:0',))
        self.assertEqual(performance.end_sample,8817)
        fm=next(u for u in performance.units if u.section=='fm')
        ssg=next(u for u in performance.units if u.section=='ssg')
        self.assertEqual((fm.start_sample,fm.end_sample),(0,8817))
        self.assertEqual((ssg.start_sample,ssg.end_sample),(0,8817))
        self.assertAlmostEqual(ssg.pitch_curve[0][1],3993600/2/16/320)
        self.assertTrue(any(at==17 for at,hz in ssg.pitch_curve))
        self.assertTrue(any(dict(c.parameters).get('total_level')==24 and c.sample==17 for c in performance.controls))

    def test_opn2_ports_dual_instance_and_opm_voice_controls_roundtrip(self):
        raw=authored(bytes.fromhex(
            '52 b0 07 52 a4 22 52 a0 69 52 28 f0 '
            '53 b0 06 53 a4 22 53 a0 69 52 28 f4 '
            'a2 b1 05 a2 a5 22 a2 a1 69 a2 28 f1 '
            '54 20 c7 54 28 4a 54 08 78 '
            '61 11 00 52 40 10 53 b4 c0 a2 44 20 54 60 24 '
            '61 60 22 52 28 00 52 28 04 a2 28 01 54 08 00'),
            {0x2c:7987200|0x40000000,0x30:4000000})
        performance=self.check_products(raw,('opn2:0','opn2:1','opm:0'))
        self.assertEqual({(u.device_id,u.channel) for u in performance.units},
                         {('opn2:0',0),('opn2:0',3),('opn2:1',1),('opm:0',0)})
        self.assertEqual(len({v.voice_id for v in performance.voices}),4)
        self.assertEqual([c.order for c in performance.controls],sorted(c.order for c in performance.controls))
        self.assertTrue(all(u.control_ids for u in performance.units))

    def test_source_frequency_uses_physical_opn_family_denominator(self):
        state=SimpleNamespace(fnum=0x269,block=4,fm_prescale_divisor=6)
        for chip in ('opna','opn','opn2'):
            with self.subTest(chip=chip):
                self.assertAlmostEqual(_fm_pitch(chip,state,7987200),261.1002604166667)
        self.assertIsNone(_fm_pitch('opn2',SimpleNamespace(fnum=None,block=4),7987200))


if __name__=='__main__':unittest.main()
