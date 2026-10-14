"""Independent negative checks and external diagnostic OPNA FM roundtrip."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
sys.path.insert(0, str(ROOT / 'scripts'))
from opna_mucom import project
from opna_mucom_roundtrip import compare, compiler_controls
from test_opna_reader import parse, vgm, write
from verify_opna_mucom_roundtrip import calibration, verify, raw_controls

INIT = ((0, 0, 0x22, 0), (0, 0, 0x27, 0), (0, 0, 0x28, 0), (0, 0, 0x29, 0x80))


def encode_controls(controls, end):
    stream = bytearray()
    cursor = 0
    for tick, port, reg, data in controls:
        if tick > cursor:
            stream += b'\x61' + struct.pack('<H', tick - cursor)
        stream += write(reg, data, port=port)
        cursor = tick
    if cursor < end:
        stream += b'\x61' + struct.pack('<H', end - cursor)
    return vgm(stream, opna=7987200)


class OpnaMucomComparisonTests(unittest.TestCase):
    def source(self, root):
        commands = (write(0x30, 1) + write(0xa4, 0x1a) + write(0xa0, 0x69)
                    + write(0x28, 0xf0) + b'\x70' + write(0x28, 0)
                    + write(0x28, 0xf0) + b'\x62')
        _, facts = parse(root, vgm(commands, opna=7987200), dump=True)
        a = facts['analysis']
        return project(a.segments, end_vgmticks=a.source_end_vgmticks, clock_hz=7987200)

    def returned(self, root, projection, controls=None, end=None):
        predicted = compiler_controls(projection.writes, INIT)
        controls = INIT + tuple(row[1:] for row in predicted) if controls is None else controls
        end = projection.source_end_vgmticks if end is None else end
        _, facts = parse(root, encode_controls(controls, end), dump=True)
        return compare(projection, facts['analysis'], raw_controls(facts['csv_path']),
                       initialization=INIT, returned_clock_hz=facts['clock_hz'])

    def test_full_boundaries_edges_and_held_final_interval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'source').mkdir(); (root / 'returned').mkdir()
            report = self.returned(root / 'returned', self.source(root / 'source'))
            self.assertTrue(report['passed'], report)
            self.assertTrue(report['end_matches'])
            self.assertEqual(report['source_key_edge_events'], 3)
            self.assertEqual(report['source_operator_keyons'], 8)
            self.assertEqual(report['source_operator_keyoffs'], 4)

    def test_target_bytes_are_not_allowed_to_self_validate_wrong_segment_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'source').mkdir(); (root / 'returned').mkdir()
            projection = self.source(root / 'source')
            corrupted = replace(projection, writes=tuple(replace(w, data=3) if w.register == 0x30 else w
                                                        for w in projection.writes))
            report = self.returned(root / 'returned', corrupted)
            self.assertTrue(report['compiler_control_sequence_matches'])
            self.assertGreater(report['known_state_mismatches'], 0)
            self.assertFalse(report['passed'])

    def test_equal_edge_totals_do_not_hide_same_sample_order_or_timing_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'source').mkdir(); (root / 'returned').mkdir()
            projection = self.source(root / 'source')
            controls = list(INIT + tuple(row[1:] for row in compiler_controls(projection.writes, INIT)))
            indices = [i for i, row in enumerate(controls) if row[0] == 1 and row[2] == 0x28]
            controls[indices[0]], controls[indices[1]] = controls[indices[1]], controls[indices[0]]
            report = self.returned(root / 'returned', projection, tuple(controls))
            self.assertFalse(report['operator_edge_sequence_matches'])
            self.assertFalse(report['passed'])

    def test_initializer_and_final_wait_are_not_silently_stripped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'source').mkdir(); (root / 'returned').mkdir()
            projection = self.source(root / 'source')
            controls = list(INIT + tuple(row[1:] for row in compiler_controls(projection.writes, INIT)))
            controls[0] = (0, 0, 0x22, 15)
            report = self.returned(root / 'returned', projection, tuple(controls))
            self.assertFalse(report['initialization_prefix_matches'])
            self.assertFalse(report['passed'])
            report = self.returned(root / 'returned', projection, end=projection.source_end_vgmticks - 1)
            self.assertFalse(report['end_matches'])
            self.assertFalse(report['passed'])

    def test_unknown_register_or_stream_effects_cannot_be_reported_as_fm_success(self):
        cases = (write(0x21, 1), b'\x90\x01\x07\x00\x28')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, unknown in enumerate(cases):
                source = root / f'unknown{index}.vgm'
                source.write_bytes(vgm(write(0x28, 0xf0) + unknown + b'\x70', opna=7987200))
                with self.assertRaisesRegex(ValueError, 'cannot be verified'):
                    verify(source, root / str(index), Path('unused.exe'), INIT)


class MucomVgmExternalTests(unittest.TestCase):
    def test_six_channels_cross_port_ch3_strobes_zero_time_and_final_held_keys(self):
        tool = ROOT / '.tools/mucomvgm-0.0.5/mucomvgm/mucomvgm.exe'
        if not tool.is_file() or (sys.platform != 'win32' and 'WSL_INTEROP' not in __import__('os').environ):
            self.skipTest('Local MUCOMVGM Windows executable/WSL interop is unavailable')
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            initialization = calibration(tool, folder / 'profile')
            commands = bytearray(write(0x29, 0x80))
            for ch in range(6):
                port, local = divmod(ch, 3)
                commands += write(0x30 + local, 1 + ch, port=port)
                commands += write(0xa4 + local, 0x1a + ch, port=port)
                commands += write(0xa0 + local, 0x60 + ch, port=port)
                commands += write(0x28, 0xf0 | local | (4 if port else 0))
            commands += b'\x70' + write(0x28, 0) + write(0x28, 0xf0)
            commands += write(0xa4, 0x22, port=1) + write(0xa1, 0x44)
            commands += write(0x27, 0x40) + write(0xac, 0x1a) + write(0xa8, 0x66)
            commands += write(0x2d, 0) + write(0x2e, 0) + write(0x2d, 0) + b'\x71'
            source = folder / 'source.vgm'
            source.write_bytes(vgm(commands, opna=7987200))
            report = verify(source, folder / 'roundtrip', tool, initialization)
            self.assertTrue(report['passed'], report)
            self.assertTrue(report['operator_edge_sequence_matches'])
            self.assertTrue(report['end_matches'])
            loop_source = folder / 'loop.vgm'
            loop_source.write_bytes(vgm(write(0x28, 0xf0) + b'\x70' + write(0xb0, 3) + b'\x71',
                                        opna=7987200, loop_offset=4))
            loop_report = verify(loop_source, folder / 'loop_roundtrip', tool, initialization, loop_count=2)
            self.assertTrue(loop_report['passed'], loop_report)
            self.assertEqual(loop_report['source_end_vgmticks'], 5)
            self.assertEqual(loop_report['source_attack_events'], 1)


if __name__ == '__main__':
    unittest.main()
