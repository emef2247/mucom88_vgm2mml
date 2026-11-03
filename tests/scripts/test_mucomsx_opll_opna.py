"""Measured OPLL-to-OPNA voice law and OPNA FM routing checks."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
sys.path.insert(0, str(ROOT / 'tests' / 'scripts'))
from mucomsx_opll import (patch_voice, patch_voice_opna, preset_patch,
                          patch_projection_report_opna)
from mucomsx_conversion import convert_vgm, ConversionError
from conversion_config import inspect_source
from test_mucomsx_source import vgm

FIXTURES = ROOT / 'tests/fixtures/public/mucomsx/source'
# Established-set Piano (preset 3): register bytes and the OPNA table
# row generated from them through the measured law.
PIANO_PATCH = bytes.fromhex('13 01 99 00 f2 d4 21 23')
PIANO_OPNA_ROW = {
    'fb': 0, 'alg': 4, 'pms': 0, 'ams': 0,
    'ops': [
        {'mul': 3, 'tl': 25, 'ks': 2, 'ar': 31, 'd1r': 7, 'd2r': 5,
         'd1l': 2, 'rr': 2, 'dt': 0, 'am': 0},
        {'mul': 1, 'tl': 0, 'ks': 0, 'ar': 30, 'd1r': 11, 'd2r': 9,
         'd1l': 2, 'rr': 4, 'dt': 0, 'am': 0},
        {'mul': 1, 'tl': 127, 'ks': 0, 'ar': 31, 'd1r': 0, 'd2r': 0,
         'd1l': 0, 'rr': 15, 'dt': 0, 'am': 0},
        {'mul': 1, 'tl': 127, 'ks': 0, 'ar': 31, 'd1r': 0, 'd2r': 0,
         'd1l': 0, 'rr': 15, 'dt': 0, 'am': 0},
    ],
}
PRESETS = {'opna': [PIANO_OPNA_ROW] * 15,
           'base': [list(PIANO_PATCH)] * 18}


class MeasuredLawTests(unittest.TestCase):
    def test_table_and_law_paths_agree(self):
        from_table = patch_voice_opna(bytes(8), 3, PRESETS)
        from_law = patch_voice_opna(PIANO_PATCH, 0, False)
        self.assertEqual(from_table, from_law)
        self.assertEqual(len(from_table), 32)

    def test_second_pair_silenced_and_feedback_byte(self):
        record = patch_voice_opna(
            bytes.fromhex('13 01 99 05 f2 d4 21 23'), 0, False)
        self.assertEqual(record[25], 5 * 8 + 4)
        self.assertEqual(record[3 + 4], 127)   # M2 TL
        self.assertEqual(record[4 + 4], 127)   # C2 TL
        self.assertEqual(record[1], 3)         # M1 MULTI via ratio map
        self.assertEqual(record[2], 1)         # C1 MULTI

    def test_eg_zero_stays_zero(self):
        record = patch_voice_opna(
            bytes.fromhex('10 10 00 00 00 00 00 00'), 0, False)
        self.assertEqual(record[1 + 8], 2 << 6)      # M1 KS|AR: KS2, AR 0
        self.assertEqual(record[1 + 12], 0)          # M1 AM|D1R
        self.assertEqual(record[1 + 16], 0)          # M1 D2R
        self.assertEqual(record[1 + 20], 0)          # M1 SL|RR

    def test_fallback_report_names_its_preset_source(self):
        report = patch_projection_report_opna(bytes(8), 3, False)
        self.assertEqual(report['preset_source'],
                         'ymfm_reconstruction_via_measured_law')
        record = patch_voice_opna(bytes(8), 3, False)
        self.assertEqual(len(record), 32)
        self.assertNotEqual(record, patch_voice(preset_patch(3)))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_public_opll_on_opna_fm_parts(self):
        r = convert_vgm(FIXTURES / 'OPLLSRC.vgm', self.root,
                        song_loop=False, opll_output='opna-fm')
        self.assertIn('A', r['tracks'])
        self.assertNotIn('MSXA', r['tracks'])
        self.assertTrue(any(a['action'] == 'measured_opll_opna_voice'
                            for a in r['assessments']))
        self.assertTrue(any(x['target_chip'] == 'opna_fm'
                            for x in r['routing']))
        self.assertEqual(r['opll_projection_policy'], 'opna_fm_measured_law')

    def test_opna_fm_capacity_is_explicit(self):
        commands = bytearray()
        for ch in range(9):
            commands.extend((0x51, 0x30 + ch, 0x10, 0x51, 0x10 + ch, 0x80,
                             0x51, 0x20 + ch, 0x17))
        commands.extend((0x61, 0xb8, 0x0b, 0x66))
        source = self.root / 'authored.vgm'
        source.write_bytes(vgm(commands))
        with self.assertRaisesRegex(ConversionError, 'OPNA FM channel capacity'):
            convert_vgm(source, self.root / 'out', song_loop=False,
                        opll_output='opna-fm')
        r = convert_vgm(source, self.root / 'out', song_loop=False,
                        opll_output='opna-fm', force=True)
        self.assertEqual(set(r['tracks']), {'A', 'B', 'C', 'H', 'I', 'J'})
        self.assertEqual(
            {a['channel'] for a in r['assessments']
             if a['action'] == 'omit_part'}, {6, 7, 8})


class Pre110ClockTests(unittest.TestCase):
    def _write(self, body, version=0x101):
        raw = bytearray(0x100)
        raw[0:4] = b'Vgm '
        raw[8:12] = version.to_bytes(4, 'little')
        raw[0x10:0x14] = 3579540 .to_bytes(4, 'little')
        raw[0x34:0x38] = (0x40 - 0x34).to_bytes(4, 'little')
        raw[0x40:0x40 + len(body)] = body
        path = Path(self.root) / 'pre110.vgm'
        path.write_bytes(raw)
        return path

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def test_opll_only_pre110_attributes_0x10_to_opll(self):
        path = self._write(bytes((0x51, 0x20, 0x1C, 0x61, 0x10, 0x00, 0x66)))
        info = inspect_source(path)
        self.assertIn('opll', info['used_chips'])
        self.assertEqual(info['declared_clocks']['opll'] & 0x3FFFFFFF, 3579540)
        self.assertFalse(any('OPLL' in u['reason']
                             for u in info['unsupported_commands']))

    def test_opm_present_pre110_keeps_0x10_for_opm(self):
        path = self._write(bytes((0x51, 0x20, 0x1C, 0x54, 0x28, 0x78,
                                  0x61, 0x10, 0x00, 0x66)))
        info = inspect_source(path)
        self.assertEqual(info['declared_clocks']['opll'], 0)
        self.assertTrue(any('OPLL' in u['reason']
                            for u in info['unsupported_commands']))


if __name__ == '__main__':
    unittest.main()
