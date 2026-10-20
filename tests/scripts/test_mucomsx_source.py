"""Authored source fixtures: no game-derived music or external compiler."""
from pathlib import Path
import struct
import ast
import csv
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from mucomsx_source import load_source


def vgm(commands, *, loop=None):
    raw = bytearray(0x100)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x34, 0x100 - 0x34)
    struct.pack_into('<I', raw, 0x74, 1789773)
    struct.pack_into('<I', raw, 0x10, 3579545)
    struct.pack_into('<I', raw, 0x9c, 1789773)
    if loop is not None:
        struct.pack_into('<I', raw, 0x1c, 0x100 + loop - 0x1c)
    raw.extend(commands)
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return raw


class SourceTests(unittest.TestCase):
    def load(self, raw, **kwargs):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        path = root / 'authored.vgm'
        path.write_bytes(raw)
        return load_source(path, root / 'passes', **kwargs)

    def test_psg_decay_reuses_whole_note_with_exact_samples(self):
        commands = bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 e8 03 a0 08 0a 61 d0 07 66')
        bundle = self.load(vgm(commands), song_loop=False)
        notes = bundle.notes['psg'][0]
        sounding = [note for note in notes if not note.rest]
        self.assertEqual(len(sounding), 1)
        self.assertEqual(sounding[0].runs, [(15, 1000), (10, 2000)])
        self.assertEqual(sounding[0].length, 3000)
        self.assertIs(sounding[0].segment, bundle.segments['psg'][0][sounding[0].segment_indices[0]])
        self.assertNotEqual(sounding[0].segment.l, sounding[0].length)
        self.assertTrue(Path(bundle.paths['segments']).is_file())
        self.assertTrue(bundle.analyses['psg']['source_plans'])

    def test_five_scc_parts_remain_native(self):
        commands = bytearray()
        for ch in range(5):
            commands.extend((0xd2, 1, ch * 2, 0xfe, 0xd2, 1, ch * 2 + 1, 0))
            commands.extend((0xd2, 2, ch, 15))
        commands.extend((0xd2, 3, 0, 31, 0x61, 0xb8, 0x0b, 0x66))
        bundle = self.load(vgm(commands), song_loop=False)
        self.assertEqual(set(bundle.notes['scc']), set(range(5)))
        self.assertEqual(set(bundle.segments['scc']), set(range(5)))

    def test_loop_replay_keeps_original_metadata(self):
        commands = bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 e8 03 a0 08 0a 61 d0 07 66')
        bundle = self.load(vgm(commands, loop=15))
        self.assertEqual(bundle.first_end_sample, 3000)
        self.assertEqual(bundle.loop_sample, 3000)
        self.assertEqual(bundle.end_sample, 5000)
        self.assertEqual(bundle.metadata['loop_start_samples'], 1000)
        self.assertEqual(bundle.metadata['loop_count'], 2)

    def test_opll_native_and_sample_patch_trajectories(self):
        commands = bytes.fromhex('51 10 80 51 20 15 61 e8 03 51 00 71 61 d0 07 51 20 05 66')
        bundle = self.load(vgm(commands), song_loop=False)
        self.assertTrue(bundle.notes['opll'][0])
        notes = [n for n in bundle.notes['opll'][0] if not n.rest]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].length, 3000)
        self.assertEqual(notes[0].sample_items[0].patch_changes[0][0], 1000)
        self.assertNotEqual(notes[0].items[0].patch_changes[0][0], 1000)
        self.assertIs(notes[0].slices[0], bundle.segments['opll'][0][notes[0].segment_indices[0]])

    def test_original_trace_has_no_synthetic_boundary(self):
        bundle = self.load(vgm(bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 b8 0b 66')),
                           song_loop=False)
        trace = Path(bundle.paths['psg_trace']).read_text()
        analysis_input = Path(bundle.paths['psg_analysis_input']).read_text()
        self.assertNotIn('sourceEnd', trace)
        self.assertIn('sourceEnd', analysis_input)
        self.assertEqual(sum(n.length for n in bundle.notes['psg'][0]), 3000)
        self.assertEqual(bundle.end_sample, 3000)

    def test_opll_key_stays_on_through_observed_end(self):
        bundle = self.load(vgm(bytes.fromhex('51 10 80 51 20 15 61 b8 0b 66')),
                           song_loop=False)
        note = next(n for n in bundle.notes['opll'][0] if not n.rest)
        self.assertEqual(note.length, 3000)
        self.assertTrue(all(s.keyon for s in note.slices))
        self.assertEqual(note.sample_slices[-1].tick_end, 3000)

    def test_invalid_loop_is_rejected_before_analysis(self):
        with self.assertRaisesRegex(ValueError, 'Invalid declared'):
            self.load(vgm(bytes.fromhex('a0 00 fe 62 66'), loop=1))

    def test_opll_source_outer_and_inner_plans_keep_segment_members(self):
        from opll_inner_loops import OpllLoopPlan
        commands = bytearray.fromhex('51 10 80 51 20 15')
        for _ in range(3):
            commands.extend(bytes.fromhex('51 30 11 61 e8 03 51 30 12 61 e8 03'))
        commands.extend(bytes.fromhex('51 20 05 66'))
        bundle = self.load(vgm(commands), song_loop=False)
        detail = bundle.analyses['opll']
        plan = detail['source_plans'][0]
        self.assertIsInstance(plan, OpllLoopPlan)
        self.assertEqual(plan.members, tuple(tuple(n.segment_indices) for n in bundle.notes['opll'][0]))
        for indices, child in zip(plan.members, plan.inner):
            self.assertEqual(child.keys, tuple(detail['sample_items'][0][i].signature() for i in indices))
            self.assertTrue(all(bundle.segments['opll'][0][i].vgmticks == detail['sample_items'][0][i].tick
                                for i in indices))
        outer = Path(detail['source_plan_paths'][0]['outer'])
        candidates = Path(str(outer).removesuffix('.csv') + '.inner_repeat_candidates.csv')
        self.assertTrue(outer.is_file())
        self.assertGreater(len(candidates.read_text().splitlines()), 1)
        self.assertEqual(plan.inner_reports, [])
        inner_paths = detail['source_plan_paths'][0]['inner']
        self.assertEqual(len(inner_paths), 3)
        self.assertTrue(all(Path(p).is_file() for p in inner_paths))
        with open(inner_paths[0], newline='') as stream:
            members = list(csv.DictReader(stream))
        self.assertEqual(len(members), sum(map(len, plan.members)))
        for row in members:
            note_id, step = int(row['note_unit_id']), int(row['step'])
            self.assertEqual(int(row['segment_index']), plan.members[note_id][step])
            self.assertEqual(ast.literal_eval(row['source_key']), plan.inner[note_id].keys[step])
        with open(inner_paths[2], newline='') as stream:
            markers = list(csv.DictReader(stream))
        self.assertTrue(markers)
        self.assertTrue(all(int(row['repeats']) >= 2 for row in markers))

    def test_undeclared_reset_only_scc_retains_native_and_raw_evidence(self):
        commands = bytes.fromhex('d2 02 00 00 d2 03 00 00 a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 b8 0b 66')
        raw = vgm(commands)
        struct.pack_into('<I', raw, 0x9c, 0)
        bundle = self.load(raw, song_loop=False)
        self.assertEqual(bundle.header['scc_clock_raw'], 0)
        self.assertIn('scc', bundle.metadata['source_usage']['compatibility_initialization'])
        self.assertTrue(bundle.segments['scc'])
        self.assertFalse(bundle.notes['scc'])
        self.assertTrue(Path(bundle.paths['scc_initialization_trace']).is_file())
        self.assertIn('d2020000', Path(bundle.paths['commands']).read_text())
        self.assertTrue(bundle.notes['psg'])

    def test_undeclared_nonzero_scc_volume_cannot_be_ignored(self):
        raw = vgm(bytes.fromhex('d2 02 00 0f d2 03 00 01 61 b8 0b 66'))
        struct.pack_into('<I', raw, 0x9c, 0)
        with self.assertRaisesRegex(ValueError, 'declared SCC'):
            self.load(raw, song_loop=False)

    def test_unknown_active_chip_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported active'):
            self.load(vgm(bytes.fromhex('52 28 f0 62 66')))

    def test_truncated_command_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            self.load(vgm(bytes.fromhex('a0 00')))


if __name__ == '__main__':
    unittest.main()
