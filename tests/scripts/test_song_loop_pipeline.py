"""Declared source loops reuse the normal native and PSG Segment pipelines."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py')]
from opm_conversion import convert
from psg_scc_conversion import convert as convert_psg
from vgm_reader import parse_vgm
from opm import build_segments
from vgm_timing import command_times
from vgm_io import read_vgm_bytes


def declared(raw, address):
    raw = bytearray(raw)
    struct.pack_into('<I', raw, 0x1c, address - 0x1c)
    return raw


class SongLoopPipelineTests(unittest.TestCase):
    def test_native_reader_expansion_and_finite_opt_out(self):
        fixture = ROOT / 'tests/fixtures/public/opm/from_mdx/held_controls/held_controls.vgm'
        raw = read_vgm_bytes(fixture)
        events = list(command_times(raw))
        start = next(e.address for e in events if e.command == 0x54 and raw[e.address + 1] == 8 and raw[e.address + 2] & 0x78)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'held.vgm'
            source.write_bytes(declared(raw, start))
            mml, analysis, projection = convert(source, folder / 'loop', dump_passes=True)
            report = json.loads(mml.with_name('held.mdx.normalization.json').read_text())
            self.assertTrue(report['song_loop']['emitted'])
            self.assertGreater(analysis.source_end_vgmticks, events[-1].vgmticks)
            self.assertIn(' L', mml.read_text())
            self.assertTrue(any(s.loop_iteration == 1 for s in analysis.segments))
            finite, original, unused = convert(source, folder / 'finite', song_loop=False)
            self.assertEqual(original.source_end_vgmticks, events[-1].vgmticks)
            self.assertNotIn(' L', finite.read_text())
            # A third source traversal gives the same steady native state at
            # matching writes; replay cannot rely on reinitializing registers.
            metadata = {}
            parse_vgm(str(source), str(folder / 'third'), opm_metadata=metadata, loop_count=3)
            third = build_segments(metadata['csv_path'], end_vgmticks=metadata['source_end_vgmticks'])
            states = {(e.loop_iteration, e.original_event_id, e.ch): e.state for e in third.events}
            for (iteration, event_id, ch), state in states.items():
                if iteration == 2:
                    self.assertEqual(state, states[1, event_id, ch])

    def test_psg_replay_keeps_source_and_projected_loop_boundaries(self):
        fixture = ROOT / 'tests/fixtures/public/psg/retrigger/retrigger.vgm'
        raw = read_vgm_bytes(fixture)
        events = list(command_times(raw))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'psg.vgm'
            source.write_bytes(declared(raw, events[0].address))
            mml, plan = convert_psg(source, folder / 'loop', dump_passes=True)
            self.assertIn(' L', mml.read_text())
            self.assertGreater(plan.source_end, events[-1].vgmticks)
            report = json.loads(mml.with_name('psg.mdx.normalization.json').read_text())
            self.assertTrue(report['song_loop']['emitted'])
            self.assertGreater(report['song_loop']['playback_start_samples'], 0)
            # Only the bounded source replay occurs: derived OPM must not replay
            # the already-expanded PSG trajectory a second time.
            self.assertLessEqual(abs(plan.source_end - plan.structured_context.analysis.source_end_vgmticks), 6)


if __name__ == '__main__':
    unittest.main()
