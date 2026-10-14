"""Independent Segment boundaries and key events in diagnostic comparisons."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'py'), str(Path(__file__).resolve().parent)]
from compare_opm_vgm import run_comparison
from test_opm_reader import vgm


def write(register, data):
    return bytes((0x54, register, data))


def setup(channel):
    commands = write(0x20 + channel, 0xc4) + write(0x28 + channel, 0x4e)
    commands += write(0x30 + channel, 0) + write(0x38 + channel, 0)
    for bank in range(4):
        for base, value in ((0x40, 1), (0x60, 0), (0x80, 31), (0xa0, 0), (0xc0, 0), (0xe0, 0)):
            commands += write(base + bank * 8 + channel, value)
    return commands + write(8, 0x78 | channel)


class OpmComparisonTests(unittest.TestCase):
    def compare(self, root, left, right):
        a, b = root / 'left.vgm', root / 'right.vgm'
        a.write_bytes(vgm(left))
        b.write_bytes(vgm(right))
        return run_comparison(a, b, root / 'report', ((5, 0),))

    def test_mapped_channels_equal_despite_different_wait_encoding(self):
        left = setup(5) + b'\x61\x11\x00' + write(8, 5) + b'\x7f'
        right = setup(0) + b'\x7f\x70' + write(8, 0) + b'\x7f'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.compare(root, left, right)
            channel = report['channels'][0]
            self.assertEqual(channel['differing_samples_by_field'], {})
            self.assertEqual(channel['unknown_samples_by_field'], {})
            self.assertTrue(channel['key_commands_identical'])
            self.assertEqual(report['end_difference_samples'], 0)
            self.assertNotEqual(report['left_commands']['command_counts'], report['right_commands']['command_counts'])
            self.assertFalse(report['waveform_equivalence_checked'])
            self.assertEqual((root / 'left.vgm').read_bytes(), vgm(left))
            self.assertEqual((root / 'right.vgm').read_bytes(), vgm(right))
            self.assertTrue((root / 'report/left/segments.csv').is_file())
            self.assertTrue((root / 'report/right/state.csv').is_file())
            self.assertEqual(json.loads((root / 'report/report.json').read_text())['channel_mapping'], [[5, 0]])

    def test_zero_duration_retrigger_remains_visible(self):
        left = setup(5) + b'\x7f' + write(8, 5) + write(8, 0x7d) + b'\x7f'
        right = setup(0) + b'\x7f\x7f'
        with tempfile.TemporaryDirectory() as temporary:
            channel = self.compare(Path(temporary), left, right)['channels'][0]
            self.assertEqual(channel['differing_samples_by_field'], {})
            self.assertFalse(channel['key_commands_identical'])
            self.assertEqual(channel['left_rising_edges'], 2)
            self.assertEqual(channel['right_rising_edges'], 1)

    def test_known_operator_fields_survive_unknown_unused_operator(self):
        commands = setup(0)
        omitted = {0x48, 0x68, 0x88, 0xa8, 0xc8, 0xe8}
        right = b''.join(commands[i:i + 3] for i in range(0, len(commands), 3)
                         if commands[i + 1] not in omitted)
        right += write(0x70, 3) + b'\x7f'
        with tempfile.TemporaryDirectory() as temporary:
            channel = self.compare(Path(temporary), setup(5) + b'\x7f', right)['channels'][0]
            self.assertEqual(channel['unknown_samples_by_field']['m2.tl'], 16)
            self.assertEqual(channel['differing_samples_by_field']['c1.tl'], 16)

    def test_pitch_delta_identifies_one_kf_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = self.compare(Path(temporary), setup(5) + b'\x7f',
                                  setup(0) + write(0x30, 4) + b'\x7f')
            self.assertEqual(report['channels'][0]['nominal_pitch_delta_cents_samples'], {'1.562500': 16})

    def test_channel_modulation_sensitivity_is_compared(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = self.compare(Path(temporary), setup(5) + b'\x7f',
                                  setup(0) + write(0x38, 0x32) + b'\x7f')
            differences = report['channels'][0]['differing_samples_by_field']
            self.assertEqual(differences['pms'], 16)
            self.assertEqual(differences['ams'], 16)

    def test_released_states_and_unequal_ends_are_not_dropped(self):
        left = setup(5) + b'\x7f' + write(8, 5) + write(0x2d, 0x4a) + b'\x7f'
        right = setup(0) + b'\x7f' + write(8, 0) + b'\x7f\x76'
        with tempfile.TemporaryDirectory() as temporary:
            report = self.compare(Path(temporary), left, right)
            self.assertEqual(report['channels'][0]['differing_samples_by_field']['pitch'], 16)
            self.assertEqual(report['end_difference_samples'], 7)
            self.assertEqual(report['compared_through_vgmticks'], 32)

    def test_rejects_output_covering_input_or_ambiguous_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'left.vgm'
            source.write_bytes(vgm(setup(5) + b'\x7f'))
            with self.assertRaisesRegex(ValueError, 'contain either input'):
                run_comparison(source, source, root, ((5, 0),))
            with self.assertRaisesRegex(ValueError, 'one-to-one'):
                run_comparison(source, source, root / 'out', ((5, 0), (6, 0)))


if __name__ == '__main__':
    unittest.main()
