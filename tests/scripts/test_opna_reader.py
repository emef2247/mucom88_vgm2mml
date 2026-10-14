"""OPNA raw capture, independent of musical or target interpretation."""
import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from vgm_io import read_vgm_header
from vgm_reader import parse_vgm
from test_opm_reader import read_rows


def vgm(commands, *, opna=0, version=0x171, loop_offset=None):
    raw = bytearray(0x100)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, version)
    struct.pack_into('<I', raw, 0x34, len(raw) - 0x34)
    struct.pack_into('<I', raw, 0x48, opna)
    if loop_offset is not None:
        struct.pack_into('<I', raw, 0x1c, len(raw) + loop_offset - 0x1c)
    raw += commands + b'\x66'
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return raw


def write(register, data, *, port=0, instance=0):
    return bytes([0x56 + port + instance * 0x50, register, data])


def parse(root, raw, *, dump=False, packed=False, metadata=None, loop_count=1):
    root = Path(root)
    source = root / 'source.vgm'
    source.write_bytes(gzip.compress(raw) if packed else raw)
    opna = {} if metadata is None else metadata
    paths = parse_vgm(str(source), str(root / 'passes'),
                      opna_metadata=opna,
                      dump_opna_segments=dump, loop_count=loop_count)
    return paths, opna


class OpnaReaderTests(unittest.TestCase):
    def test_mixed_chips_ports_instances_and_shared_wait_order(self):
        commands = (b'\x62\x55\x28\xf0\x77'
                    + write(0x28, 0xf4)
                    + write(0xa4, 0x21, port=1, instance=1)
                    + write(0xa0, 0x55, port=1, instance=1)
                    + b'\xa5\x28\x00\x7a\x63')
        with tempfile.TemporaryDirectory() as tmp:
            paths, opna = parse(tmp, vgm(commands, opna=0x40000000 | 8000000))
            self.assertEqual(len(paths), 8)
            rows = read_rows(opna['csv_path'])
            self.assertEqual([(int(r['port']), int(r['chip_instance'])) for r in rows],
                             [(0, 0), (1, 1), (1, 1)])
            self.assertEqual([int(r['event_id']) for r in rows], [3, 4, 5])
            self.assertEqual([r['ch'] for r in rows], ['3', '3', '3'])
            self.assertEqual(opna['source_end_vgmticks'], 735 + 8 + 11 + 882)

    def test_header_version_and_command_overlap_boundaries(self):
        raw = vgm(b'\x62', opna=8000000, version=0x150)
        self.assertEqual(read_vgm_header(raw)['opna_clock_raw'], 0)
        struct.pack_into('<I', raw, 8, 0x151)
        self.assertEqual(read_vgm_header(raw)['opna_clock_raw'], 8000000)
        struct.pack_into('<I', raw, 0x34, 0x40 - 0x34)
        self.assertEqual(read_vgm_header(raw)['opna_clock_raw'], 0)

    def test_gzip_and_existing_returned_csv_parity(self):
        legacy = b'\x62\xa0\x08\x0a\x51\x20\x10\x77\x51\x20\x00'
        extended = legacy[:1] + write(0x28, 0xf0) + legacy[1:] + write(0x28, 0)
        with tempfile.TemporaryDirectory() as tmp:
            expected = None
            for name, commands, packed in [('legacy', legacy, False), ('extended', extended, False),
                                           ('gzip', extended, True)]:
                folder = Path(tmp) / name
                folder.mkdir()
                paths, opna = parse(folder, vgm(commands, opna=8000000),
                                         packed=packed)
                actual = [Path(p).read_bytes() for p in paths]
                if expected is None:
                    expected = actual
                else:
                    self.assertEqual(actual, expected)
                    self.assertEqual(opna['write_count'], 2)

    def test_metadata_reuse_clears_owned_results_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, opna = parse(tmp, vgm(write(0x28, 0xf0), opna=8000000), dump=True)
            self.assertEqual(opna['key_counts']['operator_keyons'], 4)
            parse(tmp, vgm(b'\x62'), dump=True, metadata=opna)
            self.assertEqual(opna['write_count'], 0)
            self.assertIsNone(opna['analysis'])
            self.assertIsNone(opna['state_csv_path'])
            self.assertEqual(opna['issues'], ())

    def test_repeated_output_manifest_distinguishes_current_and_old_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = vgm(write(0x28, 0xf0), opna=8000000)
            _, facts = parse(tmp, raw, dump=True)
            old_segment = Path(facts['segments_csv_path'])
            manifest = Path(facts['manifest_path'])
            report = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertIn(str(old_segment), report['current_files'])
            parse(tmp, raw, dump=False)
            report = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(report['fm_analysis_status'], 'raw_only')
            self.assertNotIn(str(old_segment), report['current_files'])
            self.assertTrue(old_segment.exists())  # The manifest avoids deleting other runs' evidence.
            parse(tmp, vgm(b'\x62'), dump=True)
            report = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(report['current_files'], [])
            self.assertEqual(report['fm_analysis_status'], 'no_register_writes')

    def test_opna_irq_control_is_not_pcm_evidence(self):
        commands = write(0x10, 0x80, port=1) + write(0x11, 0x55, port=1)
        with tempfile.TemporaryDirectory() as tmp:
            _, facts = parse(tmp, vgm(commands, opna=8000000), dump=True)
            self.assertEqual([w.domain for w in facts['analysis'].unmodeled_writes],
                             ['irq_flag_control', 'uninterpreted'])

    def test_undeclared_writes_are_retained_and_segment_analysis_rejects(self):
        for commands, clock in [(write(0x28, 0xf0), 0), (write(0x28, 0xf0, instance=1), 4000000)]:
            with self.subTest(clock=clock), tempfile.TemporaryDirectory() as tmp:
                _, opna = parse(tmp, vgm(commands, opna=clock))
                self.assertEqual(len(read_rows(opna['csv_path'])), 1)
                self.assertTrue(opna['issues'])
                with self.assertRaisesRegex(ValueError, 'undeclared'):
                    parse(tmp, vgm(commands, opna=clock), dump=True)

    def test_non_fm_and_adpcm_block_bytes_remain_inspectable(self):
        block = b'\x67\x66\x81' + struct.pack('<I', 10) + struct.pack('<II', 2, 0) + b'\xab\xcd'
        commands = (write(8, 10) + write(0x10, 1)
                    + write(0, 0xa0, port=1) + block
                    + b'\x90\x01\x07\x01\x08\x92\x01\x44\xac\x00\x00\x94\x01\x62')
        with tempfile.TemporaryDirectory() as tmp:
            _, opna = parse(tmp, vgm(commands, opna=8000000), dump=True)
            self.assertEqual(opna['write_count'], 3)
            self.assertEqual(len(read_rows(opna['unmodeled_csv_path'])), 3)
            auxiliary = read_rows(opna['auxiliary_csv_path'])
            self.assertEqual(bytes.fromhex(auxiliary[0]['operands_hex']), block[1:])
            self.assertEqual([r['domain'] for r in auxiliary], ['adpcm_b_data_block'] + ['dac_stream'] * 3)
            self.assertTrue(opna['issues'])
            self.assertEqual(opna['key_counts']['operator_keyons'], 0)


if __name__ == '__main__':
    unittest.main()
