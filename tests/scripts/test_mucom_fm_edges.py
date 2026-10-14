"""Source-ordered FM key edges must not vanish through positive intervals."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_muc import convert_vgm
from opna_music import audit_fm_key_edges, build_fm_gates
from vgm_reader import parse_vgm


def authored_vgm(pattern):
    header = bytearray(256)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 8, 0x171)
    struct.pack_into('<I', header, 0x34, 256 - 0x34)
    struct.pack_into('<I', header, 0x48, 7987200)
    body = bytearray()
    def write(register, value):
        body.extend((0x56, register, value))
    for bank in range(4):
        for base, value in ((0x30, 1), (0x40, 20), (0x50, 31), (0x60, 8),
                            (0x70, 3), (0x80, 15), (0x90, 0)):
            write(base + 4 * bank, value)
    for reg, value in ((0x22, 0), (0xb0, 7), (0xb4, 192), (0xa4, 26), (0xa0, 106)):
        write(reg, value)
    total = 0
    for mask, wait in pattern:
        write(0x28, mask)
        if wait:
            body.extend(b'\x61' + struct.pack('<H', wait))
            total += wait
    body.append(0x66)
    struct.pack_into('<I', header, 4, len(header) + len(body) - 4)
    struct.pack_into('<I', header, 0x18, total)
    return bytes(header + body)


class FmOrderedEdgeTests(unittest.TestCase):
    def convert_pattern(self, pattern, **options):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / 'authored.vgm'
        source.write_bytes(authored_vgm(pattern))
        return convert_vgm(source, root / 'converted', **options)

    def test_normal_gates_and_same_sample_retrigger_survive(self):
        result = self.convert_pattern(((0xf0, 800), (0, 0), (0xf0, 800), (0, 800)))
        self.assertEqual(result['status'], 'converted')

    def test_zero_pulse_after_normal_gate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'zero-duration/unmatched key transition'):
            self.convert_pattern(((0xf0, 800), (0, 800), (0xf0, 0), (0, 800)),normalize_lengths=False)

    def test_same_sample_partial_key_before_full_gate_is_preserved(self):
        report=self.convert_pattern(((0xf0,800),(0,800),(0x80,0),(0xf0,800),(0,800)))
        text=Path(report['muc_path']).read_bytes().decode('cp932')
        self.assertIn('y$28,128',text)
        self.assertIn('y$28,240',text)
        self.assertEqual(report['musical_note_units'],2)

    def test_gate_held_until_eof_is_valid(self):
        result = self.convert_pattern(((0xf0, 800),))
        self.assertEqual(result['status'], 'converted')
        self.assertTrue(any('EOF truncates' in text for text in result['diagnostics']))

    def test_held_end_cannot_retrigger_at_song_loop_seam(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            raw=bytearray(authored_vgm(((0xf0,800),)))
            attack=raw.index(bytes((0x56,0x28,0xf0)),256)
            struct.pack_into('<I',raw,0x1c,attack-0x1c)
            struct.pack_into('<I',raw,0x20,800)
            source=root/'loop.vgm';source.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'loop seam'):
                convert_vgm(source,root/'converted')

    def test_no_song_loop_accepts_otherwise_rejected_held_seam(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            raw=bytearray(authored_vgm(((0xf0,800),)))
            attack=raw.index(bytes((0x56,0x28,0xf0)),256)
            struct.pack_into('<I',raw,0x1c,attack-0x1c)
            struct.pack_into('<I',raw,0x20,800)
            source=root/'loop.vgm';source.write_bytes(raw)
            report=convert_vgm(source,root/'converted',song_loop=False)
            self.assertEqual(report['status'],'converted')
            self.assertEqual(report['song_loop']['source_start_samples'],0)
            self.assertIsNone(report['song_loop']['target_start_samples'])
            self.assertFalse(report['song_loop']['enabled'])
            self.assertEqual(report['fm_loop_seams'],[])
            self.assertFalse(report['tracks']['A']['loop'])
            self.assertNotIn('L',Path(report['muc_path']).read_bytes().decode('cp932').split())
            self.assertTrue(any('EOF truncates' in text for text in report['diagnostics']))

    def test_existing_public_fm_and_mucom_patterns_keep_key_edges(self):
        inputs = sorted((ROOT / 'tests/fixtures/public/opna/from_fm').rglob('*.vgm'))
        inputs += sorted((ROOT / 'tests/fixtures/public/opna/mucom88').rglob('*.vgm'))
        self.assertTrue(inputs)
        with tempfile.TemporaryDirectory() as folder:
            for index, source in enumerate(inputs):
                with self.subTest(source=source.name):
                    metadata = {}
                    parse_vgm(str(source), str(Path(folder) / str(index)),
                              opna_metadata=metadata, dump_opna_segments=True, loop_count=1)
                    analysis = metadata['analysis']
                    audit_fm_key_edges(analysis, build_fm_gates(analysis))


if __name__ == '__main__':
    unittest.main()

