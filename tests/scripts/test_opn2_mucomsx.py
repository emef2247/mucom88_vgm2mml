"""Authored OPN2 -> MUCOMSX (OPNA mapping) conversion checks."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from opn2_mucomsx import ConversionError, convert_vgm

OPN2_CLOCK = 7670454
DCSG_CLOCK = 3579545


def authored(commands, clocks):
    raw = bytearray(256)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x34, 256 - 0x34)
    for offset, clock in clocks.items():
        struct.pack_into('<I', raw, offset, clock)
    raw.extend(commands)
    raw.append(0x66)
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return bytes(raw)


def write(reg, data, port=0):
    return bytes((0x53 if port else 0x52, reg, data))


def voice_and_note(port=0):
    commands = [write(0xb0, 0x32, port), write(0xb4, 0xc0, port)]
    for bank in range(4):
        commands.append(write(0x30 + bank * 4, 0x71, port))
        commands.append(write(0x40 + bank * 4, 0x20, port))
        commands.append(write(0x50 + bank * 4, 0x5f, port))
        commands.append(write(0x60 + bank * 4, 0x08, port))
        commands.append(write(0x70 + bank * 4, 0x04, port))
        commands.append(write(0x80 + bank * 4, 0x47, port))
    commands += [write(0xa4, 0x22, port), write(0xa0, 0x8d, port),
                 write(0x28, 0xf0 if port == 0 else 0xf4),
                 bytes((0x61,)) + struct.pack('<H', 22050),
                 write(0x28, 0x00 if port == 0 else 0x04),
                 bytes((0x61, 0x64, 0x00))]
    return b''.join(commands)


class Opn2MucomsxTests(unittest.TestCase):
    def convert(self, raw, **kwargs):
        folder = tempfile.TemporaryDirectory()
        root = Path(folder.name)
        source = root / 'authored.vgm'
        source.write_bytes(raw)
        result = convert_vgm(source, root / 'out', **kwargs)
        muc = Path(result['muc_path']).read_text(encoding='cp932')
        return folder, result, muc

    def test_fm_note_maps_to_opna_part_a(self):
        folder, result, muc = self.convert(
            authored(voice_and_note(), {0x2c: OPN2_CLOCK}))
        self.assertEqual(result['status'], 'generated')
        self.assertEqual(result['target'], 'MUCOMSX v1.0-stable')
        self.assertIn('#driver mucomDotNET', muc)
        self.assertIn('#voice authored.dat', muc)
        part_a = next(line for line in muc.splitlines()
                      if line.startswith('A '))
        self.assertIn('C128 t', part_a)
        self.assertIn('@0', muc)
        bank = Path(result['voice_bank'])
        self.assertEqual(bank.stat().st_size, 8192)
        self.assertEqual(result['musical_note_units'], 1)
        self.assertEqual(result['compiler_status'], 'not_run')

    def test_dcsg_requires_force_for_now(self):
        raw = authored(voice_and_note() + bytes((0x50, 0x9f)),
                       {0x2c: OPN2_CLOCK, 0x0c: DCSG_CLOCK})
        with self.assertRaises(ConversionError):
            self.convert(raw)
        folder, result, muc = self.convert(raw, force=True)
        self.assertEqual(result['status'], 'generated')
        self.assertEqual(result['omissions'][0]['chip'], 'dcsg')


if __name__ == '__main__':
    unittest.main()
