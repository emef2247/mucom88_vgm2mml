"""SSG replay tests use authored register evidence, never song fixtures."""
import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
import opna_ssg


class OpnaSsgTests(unittest.TestCase):
    def analyze(self, commands, end=1000, clock=8000001):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'source.csv'
        rows = []
        for event_id, command in enumerate(commands):
            tick, register, data, *extra = command
            overrides = extra[0] if extra else {}
            row = dict(chip_instance=0, chip_type='YM2608', vgmticks=tick,
                event_id=event_id, address=256 + event_id * 3, register=register,
                data=data, command=0x56, clock_hz=clock, clock_raw=clock,
                port=0, original_event_id=event_id, original_vgmticks=tick,
                loop_iteration=0)
            row.update(overrides)
            rows.append(row)
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        return opna_ssg.build_segments(path, end_vgmticks=end), Path(folder.name)

    def test_masks_shared_mixer_and_raw_registers(self):
        result, _ = self.analyze([(0, 0, 0xab), (0, 1, 0xf2),
            (0, 6, 0xff), (0, 7, 0xf6), (10, 8, 0xef)])
        ch0 = result.segments[0][-1]
        self.assertEqual((ch0.tone_period, ch0.noise_period, ch0.volume,
                          ch0.amplitude_register, ch0.envelope_enabled),
                         (0x2ab, 31, 15, 15, 0))
        self.assertEqual(ch0.registers[:9], (0xab, 0xf2, 0, 0, 0, 0, 255, 246, 239))
        self.assertEqual([result.segments[ch][-1].mode for ch in range(3)], [3, 0, 0])
        self.assertEqual(result.segments[1][-1].registers[6:8], (255, 246))
        self.assertEqual(ch0.pass3_row, ())
        with self.assertRaises(FrozenInstanceError):
            ch0.volume = 3

    def test_envelope_repeated_strobes_io_and_terminal_boundary(self):
        result, _ = self.analyze([(5, 11, 0x34), (5, 12, 0x12),
            (5, 8, 16), (5, 13, 0xfe), (5, 13, 0xfe), (10, 14, 0xa5)], end=17)
        for ch in range(3):
            segments = result.segments[ch]
            strobes = [s for s in segments if s.envelope_trigger]
            self.assertEqual(len(strobes), 2)
            self.assertEqual(strobes[0].vgmticks_end, 5)
            self.assertEqual((strobes[-1].envelope_period, strobes[-1].envelope_shape),
                             (0x1234, 14))
            self.assertEqual(segments[-1].registers[14], 0xa5)
            self.assertEqual((segments[0].vgmticks, segments[-1].vgmticks_end), (0, 17))
            self.assertTrue(all(a.vgmticks_end == b.vgmticks
                                for a, b in zip(segments, segments[1:])))
            self.assertEqual(segments[0].volume, 0)
        self.assertEqual(result.segments[0][-1].envelope_enabled, 1)
        self.assertNotEqual(result.segments[0][-1].scale, 'r')

    def test_expanded_loop_provenance_and_prescaler_selectors(self):
        result, _ = self.analyze([(1, 0x2e, 0), (2, 0x2f, 0),
            (3, 0x2e, 0), (4, 0x2d, 0),
            (20, 8, 12, dict(address=256, original_event_id=0,
                             original_vgmticks=1, loop_iteration=1))])
        for ch in range(3):
            self.assertEqual([s.prescale for s in result.segments[ch][:4]], [6, 3, 2, 6])
            self.assertEqual([s.effective_clock_hz for s in result.segments[ch][:4]],
                             [2000000.25, 4000000.5, 8000001.0, 2000000.25])
        last = result.segments[0][-1]
        self.assertEqual((last.event_id, last.original_event_id, last.original_vgmticks,
                          last.loop_iteration, last.address), (4, 0, 1, 1, 256))

    def test_dump_retains_named_and_raw_fields(self):
        result, folder = self.analyze([(12, 9, 5)])
        result.dump(folder, 'unit')
        with (folder / 'unit.opna_ssg.segments.csv').open(encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        observed = next(r for r in rows if r['event_id'] == '0')
        self.assertEqual(json.loads(observed['registers'])[9], 5)
        self.assertEqual(json.loads(observed['pass3_row']), [])
        report = json.loads((folder / 'unit.opna_ssg.analysis.json').read_text())
        self.assertTrue(report['registers_reset_assumed'])
        self.assertEqual(report['source_end_vgmticks'], 1000)

    def test_invalid_identity_order_clock_and_end_are_rejected(self):
        for override in (dict(chip_instance=1), dict(clock_raw=0x40000001),
                dict(clock_raw=0x80000001), dict(clock_hz=0),
                dict(chip_type='YM2203'), dict(command=0x57), dict(port=2)):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.analyze([(0, 8, 1, override)])
        with self.assertRaises(ValueError):
            self.analyze([(10, 8, 1), (9, 8, 2)])
        with self.assertRaises(ValueError):
            self.analyze([(1001, 8, 1)])
        with self.assertRaises(ValueError):
            self.analyze([])


if __name__ == '__main__':
    unittest.main()
