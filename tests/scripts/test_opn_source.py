"""Authored OPN (YM2203) checks: inventory, register evidence, FM gates."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from conversion_config import inspect_source as inspect_frontend
from musical_hal.native_imports import analyze_source
from musical_hal.native_imports.opn import key_counts, register_channel
from musical_hal.native_imports.opn_music import build_opn_gates
from musical_hal.native_imports.opna_music import audit_fm_key_edges

OPN_CLOCK = 3993600


def authored(commands, clocks=None):
    raw = bytearray(256)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x34, 256 - 0x34)
    for offset, clock in (clocks or {}).items():
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

NOTE = VOICE + write(0xa4, 0x2a) + write(0xa0, 0x44) \
    + write(0x28, 0xf0) + wait(22050) + write(0x28, 0x00) + wait(4410)


class OpnInventoryTests(unittest.TestCase):
    def frontend(self, raw):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'authored.vgm'
            path.write_bytes(raw)
            return inspect_frontend(path)

    def test_frontend_inventory_and_clock(self):
        usage = self.frontend(authored(NOTE, {0x44: OPN_CLOCK}))
        self.assertEqual(usage['used_chips'], ['opn'])
        self.assertEqual(usage['declared_clocks']['opn'], OPN_CLOCK)
        self.assertFalse(any('clock declaration' in entry['reason']
                             for entry in usage['unsupported_commands']))

    def test_frontend_missing_clock_is_reported(self):
        usage = self.frontend(authored(NOTE))
        self.assertTrue(any('clock declaration' in entry['reason']
                            for entry in usage['unsupported_commands']))

    def test_register_attribution(self):
        self.assertEqual(register_channel(0x28, 0xf1), 1)
        self.assertIsNone(register_channel(0x28, 0xf3))
        self.assertEqual(register_channel(0x35, 0x00), 1)
        self.assertIsNone(register_channel(0x33, 0x00))
        self.assertEqual(register_channel(0xa9, 0x00), 2)
        self.assertIsNone(register_channel(0xb4, 0xc0))
        self.assertIsNone(register_channel(0x08, 0x0c))


class OpnAnalysisTests(unittest.TestCase):
    def analyze(self, raw):
        with tempfile.TemporaryDirectory() as folder:
            result = analyze_source(raw, folder, 'authored')
            self.assertEqual(result['errors'], {})
            root = Path(folder)
            for suffix in ('native.opn.registers.csv', 'native.opn.states.csv',
                           'native.opn.segments.csv', 'native.opn.unmodeled.csv',
                           'native.source.json'):
                self.assertTrue((root / ('authored.' + suffix)).is_file(), suffix)
            report = json.loads((root / 'authored.native.source.json').read_text())
            self.assertTrue(report['analysis_available']['opn'])
            return result['opn']

    def test_key_gate_and_pitch_evidence(self):
        analysis = self.analyze(authored(NOTE, {0x44: OPN_CLOCK}))
        self.assertEqual(key_counts(analysis),
                         {'key_writes': 2, 'channel_attack_events': 1,
                          'operator_keyons': 4, 'operator_keyoffs': 4})
        sounding = [s for s in analysis.segments
                    if s.state.key_mask and s.duration_samples > 0]
        self.assertEqual(len(sounding), 1)
        self.assertEqual((sounding[0].ch, sounding[0].state.fnum,
                          sounding[0].state.block,
                          sounding[0].state.fm_prescale_divisor),
                         (0, 580, 5, 6))

    def test_ssg_writes_are_retained_as_ssg_domain(self):
        commands = write(0x00, 0x9c) + write(0x07, 0x3e) + write(0x08, 0x0c) + NOTE
        analysis = self.analyze(authored(commands, {0x44: OPN_CLOCK}))
        ssg = [w for w in analysis.unmodeled_writes if w.domain == 'ssg']
        self.assertEqual(len(ssg), 3)
        self.assertIn('YM2203: ssg writes retained without source-domain '
                      'Segment interpretation', analysis.issues)

    def test_prescaler_write_changes_divisor(self):
        commands = write(0x2e, 0x00) + NOTE
        analysis = self.analyze(authored(commands, {0x44: OPN_CLOCK}))
        sounding = [s for s in analysis.segments
                    if s.state.key_mask and s.duration_samples > 0]
        self.assertEqual(sounding[0].state.fm_prescale_divisor, 3)
        self.assertEqual(sounding[0].state.prescaler_address, 0x2e)

    def test_invalid_key_channel_is_unmodeled(self):
        analysis = self.analyze(authored(write(0x28, 0xf3) + wait(100),
                                         {0x44: OPN_CLOCK}))
        self.assertTrue(any(w.domain == 'invalid_key_channel'
                            for w in analysis.unmodeled_writes))

    def test_gates_use_native_part_letters(self):
        analysis = self.analyze(authored(NOTE, {0x44: OPN_CLOCK}))
        gates = build_opn_gates(analysis)
        audit_fm_key_edges(analysis, gates)
        self.assertEqual([(g.start, g.end) for g in gates['A']],
                         [(0, 22050)])


if __name__ == '__main__':
    unittest.main()
