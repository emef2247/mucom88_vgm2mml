"""Authored OPN (YM2203) -> native MUCOM88 MUC checks."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from opn_muc import ConversionError, convert_vgm

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

# FM ch0: fnum 580 at block 5 on a half-rate clock spells as B (o4, D-7)
# once the (fnum, block) pair is re-expressed at the detune-minimal block.
FM_NOTE = VOICE + write(0xa4, 0x2a) + write(0xa0, 0x44) \
    + write(0x28, 0xf0) + wait(22050) + write(0x28, 0x00) + wait(4410)

# SSG ch A: tone period 668 (F#3 at the OPN SSG clock), volume 12.
SSG_TONE = write(0x00, 0x9c) + write(0x01, 0x02) + write(0x07, 0x3e) \
    + write(0x08, 0x0c) + wait(22050) + write(0x08, 0x00) + wait(4410)

# SSG ch A: noise only (mixer enables noise, tone off), volume 12.
SSG_NOISE = write(0x06, 0x10) + write(0x07, 0x3f - 0x08) \
    + write(0x08, 0x0c) + wait(22050) + write(0x08, 0x00) + wait(4410)


class OpnMucTests(unittest.TestCase):
    def convert(self, raw, **options):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / 'authored.vgm'
        source.write_bytes(raw)
        report = convert_vgm(source, root / 'out', **options)
        return root / 'out', report

    def test_fm_note_spelling_uses_detune_minimal_block(self):
        out, report = self.convert(authored(FM_NOTE))
        self.assertEqual(report['status'], 'converted')
        self.assertEqual(report['fm_projection'][0]['note'], 'b')
        self.assertEqual(report['fm_projection'][0]['target_block'], 3)
        muc = (out / 'authored.muc').read_bytes().decode('cp932')
        part_a = next(line for line in muc.splitlines()
                      if line.startswith('A '))
        self.assertIn('o4', part_a)
        self.assertIn('D-7', part_a)
        self.assertIn('b%', part_a)
        bank = (out / 'authored.dat').read_bytes()
        self.assertEqual(len(bank), 32 + 32)
        self.assertEqual(bank[:32], bytes(32))

    def test_ssg_tone_part(self):
        out, report = self.convert(authored(SSG_TONE))
        self.assertEqual(report['ssg_projection'][0]['note'], 'f+')
        self.assertEqual(report['ssg_projection'][0]['octave'], 3)
        muc = (out / 'authored.muc').read_bytes().decode('cp932')
        part_d = next(line for line in muc.splitlines()
                      if line.startswith('D '))
        self.assertIn('v12', part_d)
        self.assertIn('f+%', part_d)

    def test_ssg_noise_requires_force(self):
        with self.assertRaises(ConversionError):
            self.convert(authored(SSG_NOISE))
        out, report = self.convert(authored(SSG_NOISE), force=True)
        self.assertEqual(report['status'], 'converted')
        omissions = report['force_projection']['omissions']
        self.assertTrue(any(entry['reason'] == 'ssg_noise_not_rendered'
                            for entry in omissions))

    def test_non_opn_source_is_rejected(self):
        with self.assertRaises(ConversionError):
            self.convert(authored(FM_NOTE, clocks={0x2c: 7670454}))


if __name__ == '__main__':
    unittest.main()
