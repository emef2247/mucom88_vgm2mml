"""Authored OPN2 (YM2612) S1 checks: inventory and register evidence only."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from conversion_config import inspect_source as inspect_frontend
from musical_hal.native_imports import analyze_source, inspect_source
from musical_hal.native_imports.opn2 import key_counts, register_channel
from musical_hal.native_imports.opn2_music import build_opn2_gates
from musical_hal.native_imports.opna_music import audit_fm_key_edges

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


COMMANDS = b''.join((
    write(0x22, 0x08),            # LFO on (global)
    write(0x27, 0x00),            # ch3 normal mode (global)
    write(0x2b, 0x00),            # DAC off (global for attribution)
    write(0x30, 0x71),            # ch1 operator DT/ML
    write(0x34, 0x0d, port=1),    # ch4 operator DT/ML
    write(0xa4, 0x22, port=1),    # ch4 frequency high
    write(0xb0, 0x32),            # ch1 feedback/algorithm
    write(0x2a, 0x80),            # DAC data (no channel attribution yet)
    write(0x28, 0xf2),            # key on ch3 (index 2)
    write(0x28, 0xf6),            # key on ch6 (index 5)
    bytes((0x61, 0x10, 0x00)),    # wait
    bytes((0x50, 0x9f)),          # SN76489 write
))


class Opn2InventoryTests(unittest.TestCase):
    def frontend(self, raw):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'authored.vgm'
            path.write_bytes(raw)
            return inspect_frontend(path)

    def test_frontend_inventory_and_clock(self):
        usage = self.frontend(authored(COMMANDS, {0x2c: OPN2_CLOCK, 0x0c: DCSG_CLOCK}))
        self.assertEqual(usage['used_chips'], ['opn2'])
        self.assertEqual(usage['declared_clocks']['opn2'], OPN2_CLOCK)
        self.assertEqual(usage['command_counts']['0x52'], 8)
        self.assertEqual(usage['command_counts']['0x53'], 2)
        self.assertFalse(any('clock declaration' in entry['reason']
                             for entry in usage['unsupported_commands']))

    def test_frontend_missing_clock_is_reported(self):
        usage = self.frontend(authored(COMMANDS, {}))
        self.assertTrue(any('OPN2' in entry['reason'] and 'clock declaration' in entry['reason']
                            for entry in usage['unsupported_commands']))

    def test_register_channel(self):
        self.assertEqual(register_channel(0x28, 0xf0, 0), 0)
        self.assertEqual(register_channel(0x28, 0xf2, 0), 2)
        self.assertEqual(register_channel(0x28, 0xf6, 0), 5)
        self.assertIsNone(register_channel(0x28, 0xf3, 0))
        self.assertEqual(register_channel(0x31, 0x00, 0), 1)
        self.assertEqual(register_channel(0x31, 0x00, 1), 4)
        self.assertIsNone(register_channel(0x33, 0x00, 0))
        self.assertEqual(register_channel(0xa8, 0x00, 0), 2)
        self.assertIsNone(register_channel(0x22, 0x08, 0))
        self.assertIsNone(register_channel(0x2a, 0x80, 0))
        self.assertIsNone(register_channel(0x2b, 0x80, 0))

    def test_native_inventory_and_evidence(self):
        raw = authored(COMMANDS, {0x2c: OPN2_CLOCK, 0x0c: DCSG_CLOCK})
        bundle = inspect_source(raw)
        self.assertEqual(bundle['active_chips'], ('dcsg', 'opn2'))
        self.assertEqual(bundle['command_counts']['opn2'], 10)
        self.assertEqual(bundle['header']['opn2_clock_raw'], OPN2_CLOCK)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = analyze_source(raw, root, 'authored')
            self.assertEqual(result['errors'], {})
            self.assertTrue(result['report']['analysis_available']['opn2'])
            self.assertTrue((root / 'authored.native.opn2.states.csv').is_file())
            self.assertTrue((root / 'authored.native.opn2.segments.csv').is_file())
            unmodeled = (root / 'authored.native.opn2.unmodeled.csv').read_text()
            self.assertIn('dac_enable', unmodeled)
            self.assertIn('dac_data', unmodeled)
            path = root / 'authored.native.opn2.registers.csv'
            self.assertTrue(path.is_file())
            with path.open(newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 10)
            by_register = {(int(r['register']), int(r['port'])): r for r in rows}
            self.assertEqual(by_register[(0x30, 0)]['ch'], '0')
            self.assertEqual(by_register[(0x34, 1)]['ch'], '3')
            self.assertEqual(by_register[(0xa4, 1)]['ch'], '3')
            self.assertEqual(by_register[(0x2a, 0)]['ch'], '')
            key_rows = [r for r in rows if int(r['register']) == 0x28]
            self.assertEqual([r['ch'] for r in key_rows], ['2', '5'])
            self.assertTrue(all(r['chip_type'] == 'YM2612' for r in rows))
            report = json.loads((root / 'authored.native.source.json').read_text())
            self.assertEqual(report['command_counts']['opn2'], 10)


NOTE = b''.join((
    write(0xb0, 0x32),             # ch1 feedback/algorithm
    write(0xa4, 0x22),             # frequency high latch (block 4)
    write(0xa0, 0x8d),             # ch1 frequency low
    write(0x40, 0x20),             # op1 total level
    write(0x28, 0xf0),             # key on ch1, all operators
    bytes((0x61, 0xde, 0x02)),     # wait 734 samples
    write(0x28, 0x00),             # key off
    bytes((0x61, 0x64, 0x00)),     # wait 100 samples
))


class Opn2GateTests(unittest.TestCase):
    def test_gate_extraction(self):
        raw = authored(NOTE, {0x2c: OPN2_CLOCK})
        with tempfile.TemporaryDirectory() as folder:
            result = analyze_source(raw, Path(folder), 'note')
            self.assertEqual(result['errors'], {})
            analysis = result['opn2']
            self.assertEqual(key_counts(analysis),
                             {'key_writes': 2, 'channel_attack_events': 1,
                              'operator_keyons': 4, 'operator_keyoffs': 4})
            gates = build_opn2_gates(analysis)
            self.assertEqual(list(gates), ['A'])
            (gate,) = gates['A']
            self.assertEqual((gate.start, gate.end), (0, 734))
            state = gate.controls[-1].state
            self.assertEqual(state.fnum, 0x28d)
            self.assertEqual(state.block, 4)
            self.assertEqual(state.algorithm, 2)
            audit_fm_key_edges(analysis, gates)


if __name__ == '__main__':
    unittest.main()
