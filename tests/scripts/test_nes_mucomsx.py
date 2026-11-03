"""NES APU sources through the MUCOMSX (OPNA) route.

Synthetic byte-built VGMs only; no game data.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))

from mucomsx_conversion import ConversionError, convert_vgm


def build_vgm(body, *, dpcm_block=None):
    data = bytearray()
    if dpcm_block is not None:
        payload = (0xC000).to_bytes(2, 'little') + dpcm_block
        data += b'\x67\x66\xc0' + len(payload).to_bytes(4, 'little') + payload
    data += body
    header = bytearray(0x100)
    header[0:4] = b'Vgm '
    header[4:8] = (0x100 + len(data) - 4).to_bytes(4, 'little')
    header[8:12] = (0x171).to_bytes(4, 'little')
    header[0x84:0x88] = (1789773).to_bytes(4, 'little')
    header[0x34:0x38] = (0x100 - 0x34).to_bytes(4, 'little')
    return bytes(header) + bytes(data)


def write(register, value):
    return bytes((0xB4, register, value))


MELODIC_BODY = b''.join((
    write(21, 0x0F),   # enable first: length loads require it
    write(0, 0xBF),    # pulse1: duty 50%, halt, constant volume 15
    write(2, 0xAC),    # pulse1 period low
    write(3, 0x01),    # pulse1 period high + length load
    write(8, 0xFF),    # triangle: control + linear reload
    write(10, 0xAC),   # triangle period low
    write(11, 0x01),   # triangle period high + length load
    write(12, 0x3F),   # noise: halt, constant volume 15
    write(14, 0x03),   # noise period index 3
    write(15, 0x08),   # noise length load
    b'\x62' * 20,
    write(21, 0x00),
    b'\x62',
    b'\x66',
))

DPCM_BODY = b''.join((
    write(21, 0x01),   # enable pulse1 only; DMC setup comes first
    write(0, 0xBF),    # pulse1: duty 50%, halt, constant volume 15
    write(2, 0xAC),
    write(3, 0x01),
    write(16, 0x0F),   # DMC rate index 15, no loop, no IRQ
    write(18, 0x00),   # sample address $C000
    write(19, 0x03),   # sample length (3*16)+1 bytes
    write(21, 0x11),   # enable DMC with 0 bytes remaining -> trigger
    b'\x62' * 30,
    write(21, 0x00),
    b'\x66',
))


class NesMucomsxTests(unittest.TestCase):
    def convert(self, body, *, dpcm_block=None, **options):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'song.vgm'
            source.write_bytes(build_vgm(body, dpcm_block=dpcm_block))
            report = convert_vgm(source, Path(tmp) / 'out', **options)
            muc = Path(report['muc_path'])
            return report, muc.read_bytes() if muc.is_file() else b''

    def test_melodic_and_noise_lanes_generate(self):
        report, muc = self.convert(MELODIC_BODY)
        self.assertEqual(report['status'], 'generated')
        routing = {(row['source_chip'], row['source_channel']): row['target_part']
                   for row in report['routing']}
        self.assertEqual(routing[('nesapu', 0)], 'A')
        self.assertEqual(routing[('nesapu', 2)], 'C')
        self.assertEqual(routing[('nesapu', 3)], 'D')
        self.assertIn(b'A ', muc.replace(b'\r\n', b'\n').replace(b'\n', b'\n '))

    def test_dpcm_projects_to_adpcm_b_part_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'song.vgm'
            source.write_bytes(build_vgm(DPCM_BODY, dpcm_block=bytes([0xAA] * 64)))
            report = convert_vgm(source, Path(tmp) / 'out')
            self.assertEqual(report['status'], 'generated')
            routing = {(row['source_chip'], row['source_channel']): row['target_part']
                       for row in report['routing']}
            self.assertEqual(routing[('nesapu', 5)], 'K')
            self.assertEqual(report['nes_dpcm']['sample_count'], 1)
            bank = Path(report['pcm_bank'])
            self.assertTrue(bank.is_file())
            self.assertGreater(len(bank.read_bytes()), 1024)
            muc = Path(report['muc_path']).read_bytes()
            self.assertIn(b'#pcm song_pcm.bin', muc)
            self.assertIn(b'@1 v160 p0', muc)


if __name__ == '__main__':
    unittest.main()
