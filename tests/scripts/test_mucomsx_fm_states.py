"""Authored target FM state selection, source preservation and pass inspection."""
import csv
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from melody_patterns import Item
from mucom_fm_state_normalization import filter_msx_fm_note
from mucomsx_conversion import convert_vgm
from test_mucomsx_source import vgm


class MsxFmStateTests(unittest.TestCase):
    def note(self):
        slices = []
        items = []
        start = 0
        for i, (fnum, length) in enumerate(((300, 2000), (301, 500), (300, 2000))):
            slices.append(SimpleNamespace(ticks=start, tick_start=start, tick_end=start+length,
                l=length, keyon=1, fnum=fnum, block=4, inst=1, vol=3, sus=0))
            items.append(Item(i, start, start/44100, length, length, (), None, ()))
            start += length
        return SimpleNamespace(start=0, length=start, rest=False, runs=[(12, r.l) for r in slices],
            sample_slices=tuple(slices), sample_items=tuple(items), segment_indices=[0, 1, 2])

    def test_opll_threshold_and_native_evidence(self):
        source = self.note()
        one, _ = filter_msx_fm_note(source, 'opll', max_samples=44)
        state24, rows = filter_msx_fm_note(source, 'opll', max_samples=1058)
        self.assertEqual(len(one.sample_slices), 3)
        self.assertEqual(len(state24.sample_slices), 2)
        self.assertEqual(rows[1]['action'], 'omit_short_state')
        self.assertEqual(source.sample_slices[1].fnum, 301)
        self.assertEqual(source.sample_slices[0].tick_end, 2000)
        self.assertEqual(state24.sample_slices[0].tick_end, 2500)
        self.assertEqual(state24.sample_items[0].duration, 2500)
        self.assertEqual(state24.segment_indices, source.segment_indices)
        self.assertEqual(state24.length, source.length)

    def test_patch_sustain_key_zero_time_and_loop_controls_are_protected(self):
        for field, value in (('inst', 2), ('sus', 1), ('keyon', 0)):
            note = self.note()
            setattr(note.sample_slices[1], field, value)
            result, rows = filter_msx_fm_note(note, 'opll', max_samples=1058)
            self.assertEqual(len(result.sample_slices), 3)
            self.assertTrue(rows[1]['protected'])
        note = self.note()
        note.sample_items = (note.sample_items[0], replace(note.sample_items[1], patch_changes=((0, '00'),)), note.sample_items[2])
        _, rows = filter_msx_fm_note(note, 'opll', max_samples=1058)
        self.assertIn('ordered_patch_writes', rows[1]['protection_reason'])
        _, rows = filter_msx_fm_note(self.note(), 'opll', max_samples=1058, seams=(2200,))
        self.assertIn('song_loop_carried_state', rows[1]['protection_reason'])
        note = self.note()
        note.sample_slices[1].tick_end = note.sample_slices[1].tick_start
        _, rows = filter_msx_fm_note(note, 'opll', max_samples=1058)
        self.assertIn('ordered_zero_time_control', rows[1]['protection_reason'])

    def test_terminal_level_is_protected_and_gate_stays_whole(self):
        note = SimpleNamespace(start=0, length=4500, rest=False,
            runs=[(15, 2000), (10, 2000), (5, 500)], segment_indices=[4, 5, 6])
        result, rows = filter_msx_fm_note(note, 'scc', max_samples=1058)
        self.assertEqual(result.runs[-1], (5, 500))
        self.assertIn('release_tail_state', rows[-1]['protection_reason'])
        self.assertEqual(sum(length for _, length in result.runs), note.length)
        self.assertEqual(note.runs, [(15, 2000), (10, 2000), (5, 500)])

    def test_authored_opll_and_scc_exports_inspect_passes_and_bypass(self):
        opll = bytes.fromhex('51 30 10 51 10 80 51 20 15 61 d0 07 51 10 81 61 f4 01 51 10 80 61 d0 07 51 20 05 61 e8 03 66')
        scc = bytearray()
        for i in range(32):
            scc.extend((0xd2, 0, i, i * 3))
        scc.extend(bytes.fromhex('d2 01 00 fe d2 01 01 00 d2 02 00 0f d2 03 00 01 61 d0 07 d2 02 00 0a 61 f4 01 d2 02 00 05 61 d0 07 d2 03 00 00 61 e8 03 66'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for chip, commands in (('opll', opll), ('scc', scc)):
                source = root / (chip + '.vgm')
                source.write_bytes(vgm(commands))
                outputs = []
                for threshold, enabled in ((1, True), (24, True), (24, False)):
                    out = root / (chip + str(threshold) + str(enabled))
                    report = convert_vgm(source, out, song_loop=False, fm_state_ms=threshold,
                        normalize_lengths=enabled,scc_output='opna-fm' if chip=='scc' else 'native')
                    self.assertEqual(report['status'], 'generated')
                    self.assertTrue(Path(report['muc_path']).is_file())
                    self.assertEqual(report['fm_short_states']['threshold_ms'], threshold)
                    with (out / (chip + '.mucomsx.fm_states.decisions.csv')).open(newline='') as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertEqual(sum(row['action'] == 'omit_short_state' for row in rows), int(enabled and threshold == 24))
                    def read_pass(name):
                        with (out / (chip + '.mucomsx.fm_states.' + name + '.csv')).open(newline='') as stream:
                            return list(csv.DictReader(stream))
                    before, after = read_pass('input'), read_pass('output')
                    self.assertEqual(len(before)-len(after), int(enabled and threshold == 24))
                    self.assertEqual((before[0]['gate_start'], before[0]['gate_end']),
                        (after[0]['gate_start'], after[0]['gate_end']))
                    outputs.append((out / (chip + '.msx.notes.csv')).read_bytes())
                self.assertEqual(outputs[0], outputs[1])
                self.assertEqual(outputs[0], outputs[2])


if __name__ == '__main__':
    unittest.main()
