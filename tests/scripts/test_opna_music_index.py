"""Evidence indexing must preserve the original complete gate representation."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_music import Control, Gate, build_fm_gates, semantic_state
from vgm_reader import parse_vgm


def reference_gates(analysis):
    result = {}
    for channel in range(6):
        segments = [s for s in analysis.segments if s.ch == channel
                    and s.chip_instance == 0 and s.state.key_mask and s.duration_samples > 0]
        groups = []
        for segment in segments:
            if (not groups or groups[-1][-1].continuity_id != segment.continuity_id
                    or groups[-1][-1].vgmticks_end != segment.vgmticks):
                groups.append([])
            groups[-1].append(segment)
        gates = []
        for group in groups:
            slices = []
            for s in group:
                if slices and semantic_state(slices[-1].state) == semantic_state(s.state):
                    previous = slices.pop()
                    slices.append(Control(previous.start, s.vgmticks_end, previous.state,
                                          previous.segment_ids + (s.segment_id,),
                                          previous.event_ids + (s.source_event_id,)))
                else:
                    slices.append(Control(s.vgmticks, s.vgmticks_end, s.state,
                                          (s.segment_id,), (s.source_event_id,)))
            start, end = group[0].vgmticks, group[-1].vgmticks_end
            evidence = [s for s in analysis.segments if s.ch == channel
                        and s.continuity_id == group[0].continuity_id
                        and start <= s.vgmticks <= end]
            events = [e for e in analysis.events if e.ch == channel
                      and e.continuity_id == group[0].continuity_id
                      and start <= e.vgmticks <= end]
            gates.append(Gate('ABCHIJ'[channel], channel, group[0].continuity_id,
                              start, end, tuple(slices),
                              tuple(s.segment_id for s in evidence),
                              tuple(e.source_event_id for e in events)))
        if gates:
            result['ABCHIJ'[channel]] = tuple(gates)
    return result


def state(fnum=618, key=15):
    return SimpleNamespace(key_mask=key, fnum=fnum, block=4, algorithm=2,
        feedback=0, left_enabled=1, right_enabled=1, ams=0, pms=0,
        operators=(), lfo_enabled=0, lfo_rate=0, fm_prescale_divisor=6,
        channel3_special_enabled=0, csm_enabled=0)


def segment(identifier, start, end, continuity=1, channel=0, instance=0, snapshot=None):
    return SimpleNamespace(segment_id=identifier, source_event_id=identifier,
        vgmticks=start, vgmticks_end=end, duration_samples=end-start,
        continuity_id=continuity, ch=channel, chip_instance=instance,
        state=snapshot or state())


class CountedRecords:
    def __init__(self, records):
        self.records = records
        self.visits = 0

    def __iter__(self):
        for record in self.records:
            self.visits += 1
            yield record


class EvidenceIndexTests(unittest.TestCase):
    def test_zero_time_inclusive_boundaries_retrigger_and_source_order(self):
        shared = state()
        records = [segment(1, 10, 20, snapshot=shared), segment(2, 20, 20),
                   segment(3, 20, 30, snapshot=shared), segment(4, 30, 30),
                   segment(5, 30, 40, continuity=2),
                   segment(6, 30, 30, continuity=2),
                   segment(7, 15, 15, instance=1), segment(8, 12, 12, channel=1),
                   segment(9, 11, 11), segment(10, 9, 9), segment(11, 31, 31)]
        events = [records[i] for i in (3, 0, 8, 6, 4, 5, 10, 9)]
        analysis = SimpleNamespace(segments=records, events=events)
        self.assertEqual(build_fm_gates(analysis), reference_gates(analysis))
        gate = build_fm_gates(analysis)['A'][0]
        self.assertEqual(gate.source_event_ids, (4, 1, 9, 7))
        self.assertEqual(gate.source_segment_ids, (1, 2, 3, 4, 7, 9))
        self.assertEqual(len(gate.controls), 1)

    def test_evidence_collections_are_not_rescanned_per_gate(self):
        records = [segment(i, i*20, i*20+10, continuity=i) for i in range(500)]
        segments, events = CountedRecords(records), CountedRecords(records)
        analysis = SimpleNamespace(segments=segments, events=events)
        result = build_fm_gates(analysis)
        self.assertEqual(len(result['A']), 500)
        self.assertEqual(segments.visits, len(records)*2)
        self.assertEqual(events.visits, len(records))

    def test_public_source_gate_representation_is_unchanged(self):
        paths = [ROOT / 'tests/fixtures/public/opna/from_fm' / name / (name+'.vgm')
                 for name in ('retrigger', 'legato_patch_mix', 'patch_change_midnote')]
        for source in paths:
            with self.subTest(source=source.name), tempfile.TemporaryDirectory() as scratch:
                metadata = {}
                parse_vgm(str(source), scratch, opna_metadata=metadata,
                          dump_opna_segments=True, loop_count=1)
                analysis = metadata['analysis']
                self.assertEqual(build_fm_gates(analysis), reference_gates(analysis))


if __name__ == '__main__':
    unittest.main()
