"""Public converted patterns checked against actual OPNA source writes."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
import opna
from vgm_io import read_vgm_bytes
from vgm_reader import parse_vgm
from vgm_timing import command_times
from test_opm_reader import read_rows


def fixture_root(family):
    path = ROOT / 'tests/fixtures/public' / family
    return path if path.is_dir() and any(path.rglob('*.vgm')) else None


class OpnaFixtureTests(unittest.TestCase):
    def check_family(self, family, module, commands, channels):
        root = fixture_root(family)
        if root is None:
            self.skipTest(f'Public {family.upper()} fixtures are not installed')
        paths = sorted(root.rglob('*.vgm'))
        with tempfile.TemporaryDirectory() as temp:
            for index, source in enumerate(paths):
                with self.subTest(family=family, case=source.relative_to(root)):
                    raw = read_vgm_bytes(source)
                    timed = list(command_times(raw))
                    expected = [(i, e.address, e.command, e.vgmticks,
                                 int(e.command in (0xa5, 0xa6, 0xa7)),
                                 e.command & 1 if family == 'opna' else 0,
                                 raw[e.address + 1], raw[e.address + 2])
                                for i, e in enumerate(timed) if e.command in commands]
                    facts = {}
                    parse_vgm(str(source), str(Path(temp) / str(index)),
                              **{family + '_metadata': facts, 'dump_' + family + '_segments': True})
                    fields = ('event_id', 'address', 'command', 'vgmticks', 'chip_instance',
                              'port', 'register', 'data')
                    self.assertEqual([tuple(int(row[f]) for f in fields)
                                      for row in read_rows(facts['csv_path'])], expected)
                    result = facts['analysis']
                    self.assertEqual(result.source_end_vgmticks, timed[-1].vgmticks)
                    represented = {e.source_event_id for e in result.events}
                    represented.update(e.source_event_id for e in result.unmodeled_writes)
                    self.assertEqual(represented, {r[0] for r in expected})
                    key_writes = attacks = on = off = 0
                    masks = {}
                    for event_id, address, command, tick, instance, port, register, data in expected:
                        if port or register != 0x28 or data & 3 == 3:
                            continue
                        ch = (data & 3) + (3 if family == 'opna' and data & 4 else 0)
                        previous = masks.get((instance, ch), 0)
                        mask = data >> 4
                        added, removed = mask & ~previous, previous & ~mask
                        key_writes += 1
                        attacks += bool(added)
                        on += added.bit_count()
                        off += removed.bit_count()
                        masks[instance, ch] = mask
                    self.assertEqual(module.key_counts(result), dict(
                        key_writes=key_writes, channel_attack_events=attacks,
                        operator_keyons=on, operator_keyoffs=off))
                    self.assertEqual(sum(s.rising_mask.bit_count() for s in result.segments), on)
                    self.assertEqual(sum(s.falling_mask.bit_count() for s in result.segments), off)
                    for instance in {row[4] for row in expected}:
                        for ch in range(channels):
                            segments = [s for s in result.segments if (s.chip_instance, s.ch) == (instance, ch)]
                            self.assertEqual(segments[0].vgmticks, 0)
                            self.assertEqual(segments[-1].vgmticks_end, result.source_end_vgmticks)
                            self.assertEqual(sum(s.duration_samples for s in segments), result.source_end_vgmticks)
                            self.assertTrue(all(a.vgmticks_end == b.vgmticks for a, b in zip(segments, segments[1:])))
                    self.assertEqual(len(read_rows(facts['segments_csv_path'])), len(result.segments))

    def test_opna_source_evidence_and_segments(self):
        self.check_family('opna', opna, (0x56, 0x57, 0xa6, 0xa7), 6)


if __name__ == '__main__':
    unittest.main()
