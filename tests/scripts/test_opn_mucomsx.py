"""Authored OPN (YM2203) -> MUCOMSX dialect (OPN assumptions) checks."""
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from opn_mucomsx import ConversionError, convert_vgm
from opn_muc import convert_vgm as convert_native

OPN_CLOCK = 3993600


def authored(commands, clocks=None):
    raw = bytearray(256)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x34, 256 - 0x34)
    for offset, clock in (clocks or {0x44: OPN_CLOCK}).items():
        struct.pack_into('<I', raw, offset, clock)
    raw.extend(commands)
    raw.append(0x66)
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return bytes(raw)


def write(reg, data):
    return bytes((0x55, reg, data))


def wait(samples):
    return b'\x61' + struct.pack('<H', samples)


VOICE = b''.join(write(reg, val) for reg, val in (
    (0x30, 0x31), (0x34, 0x0d), (0x38, 0x33), (0x3c, 0x0f),
    (0x40, 0x20), (0x44, 0x30), (0x48, 0x28), (0x4c, 0x10),
    (0x50, 0xdf), (0x54, 0xdf), (0x58, 0xdf), (0x5c, 0xdf),
    (0x60, 0x05), (0x64, 0x05), (0x68, 0x05), (0x6c, 0x05),
    (0x70, 0x02), (0x74, 0x02), (0x78, 0x02), (0x7c, 0x02),
    (0x80, 0xf0), (0x84, 0xf0), (0x88, 0xf0), (0x8c, 0xf0),
    (0xb0, 0x24)))

# Same FM evidence as the native route tests: fnum 580 at block 5.
FM_NOTE = VOICE + write(0xa4, 0x2a) + write(0xa0, 0x44) \
    + write(0x28, 0xf0) + wait(22050) + write(0x28, 0x00) + wait(4410)

# SSG ch A: tone period 668 (F#3 at the OPN SSG clock), volume 12.
SSG_TONE = write(0x00, 0x9c) + write(0x01, 0x02) + write(0x07, 0x3e) \
    + write(0x08, 0x0c) + wait(22050) + write(0x08, 0x00) + wait(4410)

# SSG ch A: noise only (mixer enables noise, tone off), volume 12.
SSG_NOISE = write(0x06, 0x10) + write(0x07, 0x3f - 0x08) \
    + write(0x08, 0x0c) + wait(22050) + write(0x08, 0x00) + wait(4410)


def track_line(muc_text, part):
    # wrap_track continues long tracks on further lines that repeat
    # the part letter; the logical track is all of them joined.
    return ' '.join(line for line in muc_text.splitlines()
                    if line.startswith(part + ' '))


def first_octave(line):
    match = re.search(r'\bo(\d)\b', line)
    return int(match.group(1)) if match else None


class OpnMucomsxTests(unittest.TestCase):
    def convert(self, raw, route=convert_vgm, **options):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / 'song.vgm'
        source.write_bytes(raw)
        report = route(source, root / 'out', **options)
        return root, report

    def muc_text(self, root, report):
        return Path(report['muc_path']).read_bytes().decode('cp932')

    def test_fm_spelled_one_octave_above_native_for_4mhz_board(self):
        raw = authored(FM_NOTE)
        _, native = self.convert(raw, route=convert_native)
        root, report = self.convert(raw)
        self.assertEqual(report['status'], 'generated')
        self.assertEqual(report['timing']['clock_hz'], 4000000)
        native_line = track_line(self.muc_text(root, native), 'A')
        board_line = track_line(self.muc_text(root, report), 'A')
        # The native route spells the true pitch; the board route must
        # spell exactly one octave higher with the same detune so the
        # 4 MHz chip sounds the true pitch under an 8 MHz compile.
        self.assertEqual(first_octave(board_line),
                         first_octave(native_line) + 1)
        self.assertIn('D-7', board_line)
        self.assertIn('b%', board_line)

    def test_parts_restricted_to_opn_subset_and_bank_is_mucomsx(self):
        root, report = self.convert(authored(FM_NOTE + SSG_TONE))
        text = self.muc_text(root, report)
        for part in 'ABCDEF':
            self.assertTrue(track_line(text, part), part)
        for part in 'GHIJK':
            self.assertFalse(track_line(text, part), part)
        bank = Path(report['voice_bank']).read_bytes()
        self.assertEqual(len(bank), 8192)
        self.assertTrue(any(bank[:32]))  # first voice at index 0
        line = track_line(text, 'A')
        self.assertTrue(line.startswith('A C128 t'))

    def test_ssg_tone_spelling_matches_native(self):
        root, report = self.convert(authored(SSG_TONE))
        line = track_line(self.muc_text(root, report), 'D')
        self.assertIn('v12', line)
        self.assertIn('o3', line)
        self.assertIn('f+%', line)

    def test_ssg_noise_blocks_without_force_and_omits_with_it(self):
        raw = authored(SSG_NOISE)
        with self.assertRaises(ConversionError):
            self.convert(raw)
        _, report = self.convert(raw, force=True)
        reasons = [row['reason'] for row in report['omissions']]
        self.assertIn('ssg_noise_not_rendered', reasons)

    def test_non_opn_source_rejected(self):
        raw = authored(b'', clocks={0x44: 0})
        with self.assertRaises(ConversionError):
            self.convert(raw)


if __name__ == '__main__':
    unittest.main()
