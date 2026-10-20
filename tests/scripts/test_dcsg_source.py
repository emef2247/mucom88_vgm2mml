"""Authored DCSG source register, variant, stereo and shared-clock checks."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from dcsg import analyze, dump_analysis


def vgm(commands, *, dual=False, clock=4000000, flags=5, loop=None):
    raw = bytearray(0x100)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 12, clock | (0x40000000 if dual else 0))
    struct.pack_into('<HBB', raw, 0x28, 3, 15, flags)
    struct.pack_into('<I', raw, 0x34, 0x100 - 0x34)
    if loop is not None:
        struct.pack_into('<I', raw, 0x1c, 0x100 + loop - 0x1c)
    raw.extend(commands + b'\x66')
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return bytes(raw)


def write(value, instance=0):
    return bytes((0x30 if instance else 0x50, value))


def tone(ch, period, instance=0):
    return write(0x80 | (ch << 5) | (period & 15), instance) + write(period >> 4, instance)


class DcsgSourceTests(unittest.TestCase):
    def test_independent_latches_exact_waits_and_volume_data(self):
        commands = tone(0, 250) + write(0x91) + tone(1, 500, 1) + write(0xb2, 1)
        commands += write(4) + b'\x61\x10\x00' + write(0x9f)
        result = analyze(vgm(commands, dual=True))
        first = [s for s in result.segments if s.chip_instance == 0 and s.ch == 0][0]
        second = [s for s in result.segments if s.chip_instance == 1 and s.ch == 1][0]
        self.assertEqual((first.start_sample, first.end_sample, first.period, first.attenuation),
                         (0, 16, 250, 4))
        self.assertEqual((second.period, second.attenuation), (500, 2))
        self.assertAlmostEqual(first.frequency_hz, 500)
        self.assertEqual(result.writes[-2].register, 1)
        self.assertEqual(result.end_sample, 16)
        self.assertFalse(result.issues)

    def test_noise_tone2_dependency_and_repeated_noise_reset(self):
        commands = tone(2, 100) + write(0xe7) + write(0xf0) + b'\x70'
        commands += tone(2, 200) + b'\x70' + write(0xe7) + b'\x70'
        result = analyze(vgm(commands))
        noise = [s for s in result.segments if s.ch == 3 and s.audible]
        self.assertEqual([s.tone2_period for s in noise], [100, 200, 200])
        self.assertEqual([s.noise_reset_count for s in noise], [1, 1, 2])
        self.assertEqual([s.noise_shift_hz for s in noise], [1250, 625, 625])
        self.assertTrue(all(s.noise_tone2_coupled for s in noise))
        self.assertEqual(sum(w.noise_reset for w in result.writes), 2)

    def test_stereo_masks_preserved_even_mono_ignored_output(self):
        commands = tone(0, 100) + write(0x90) + b'\x4f\x10\x70'
        commands += b'\x3f\x01\x70'
        stereo = analyze(vgm(commands, dual=True, flags=1))
        tone0 = [s for s in stereo.segments if s.chip_instance == 0 and s.ch == 0][0]
        self.assertTrue(tone0.stereo_left)
        self.assertFalse(tone0.stereo_right)
        self.assertEqual(stereo.writes[-1].kind, 'stereo')
        mono = analyze(vgm(commands, dual=True, flags=5))
        tone0 = [s for s in mono.segments if s.chip_instance == 0 and s.ch == 0][0]
        self.assertTrue(tone0.stereo_left and tone0.stereo_right)
        self.assertEqual(len(mono.issues), 2)

    def test_zero_period_variants_and_unknown_initial_evidence(self):
        commands = tone(0, 0) + write(0x90) + b'\x70'
        ti = analyze(vgm(commands, flags=5))
        sega = analyze(vgm(commands, flags=4))
        a = [s for s in ti.segments if s.ch == 0][0]
        b = [s for s in sega.segments if s.ch == 0][0]
        self.assertAlmostEqual(a.frequency_hz, 4000000 / 32 / 1024)
        self.assertIsNone(b.frequency_hz)
        unknown = analyze(vgm(write(0x12) + b'\x70'))
        self.assertEqual(unknown.issues[0]['code'], 'data_before_known_latch')
        self.assertTrue(all(s.attenuation is None for s in unknown.segments))
        self.assertTrue(all(not s.audible for s in unknown.segments))

    def test_loop_carries_latch_and_registers_with_original_ids(self):
        commands = tone(0, 300) + write(0x90) + write(1) + b'\x61\x64\x00'
        result = analyze(vgm(commands, loop=6), loop_count=2)
        self.assertEqual(result.end_sample, 200)
        self.assertEqual(result.writes[-1].register, 1)
        self.assertEqual(result.writes[-1].loop_iteration, 1)
        self.assertEqual(result.writes[-1].original_vgmticks, 0)
        self.assertEqual(result.writes[-1].vgmticks, 100)
        self.assertEqual(result.writes[-1].source_event_id, result.writes[-2].source_event_id)

    def test_undeclared_second_and_csv_are_inspectable(self):
        result = analyze(vgm(tone(1, 200, 1) + write(0xb3, 1) + b'\x70'))
        self.assertTrue(any(i['code'] == 'undeclared_dcsg_instance' for i in result.issues))
        self.assertTrue(any(s.chip_instance == 1 for s in result.segments))
        with tempfile.TemporaryDirectory() as folder:
            paths = dump_analysis(result, folder, 'AUTHORED')
            manifest = json.loads(Path(paths['manifest']).read_text())
            self.assertEqual(manifest['writes'], 3)
            with Path(paths['segments']).open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(any(r['chip_instance'] == '1' and r['period'] == '200' for r in rows))
            self.assertFalse(manifest['source_phase_emulated'])

    def test_truncated_stream_rejected_and_unused_header_not_audible(self):
        result = analyze(vgm(b'\x70'))
        self.assertEqual(len(result.writes), 0)
        self.assertFalse(any(s.audible for s in result.segments))
        with self.assertRaises(ValueError):
            analyze(vgm(b'\x50')[:-1])


if __name__ == '__main__':
    unittest.main()
