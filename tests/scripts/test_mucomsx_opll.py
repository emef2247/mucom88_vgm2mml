"""Authored OPLL parameter mappings and independent physical-frequency checks."""
from pathlib import Path
import math
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from mucomsx_opll import (patch_voice, patch_projection_report, preset_patch,
                          project_pitch, PRESET_PATCHES, project_rhythm_segments)
from mucom_fm import FNUM, NOTES


class MucomsxOpllTests(unittest.TestCase):
    def test_presets_are_source_reconstruction_not_factory_dat(self):
        self.assertEqual(len(PRESET_PATCHES), 15)
        self.assertEqual(preset_patch(1), bytes.fromhex('71 61 1e 17 ef 7f 00 17'))
        self.assertEqual(preset_patch(15), bytes.fromhex('41 41 89 03 f1 e5 11 13'))
        self.assertTrue(all(len(patch_voice(preset_patch(i))) == 32 for i in range(1, 16)))
        for inst in (0, -1, 16, '1'):
            with self.assertRaises(ValueError):
                preset_patch(inst)

    def test_observed_custom_patch_operator_fields(self):
        patch = bytes.fromhex('b2 43 9a db f4 a7 65 89')
        voice = patch_voice(patch)
        self.assertEqual(voice[1:5], bytes((2, 2, 3, 3)))
        self.assertEqual(voice[5:9], bytes((26, 26, 0, 0)))
        self.assertEqual(voice[9:13], bytes((223, 223, 21, 21)))
        self.assertEqual(voice[13:17], bytes((137, 137, 15, 15)))
        self.assertEqual(voice[17:21], bytes((0, 0, 19, 19)))
        self.assertEqual(voice[21:25], bytes((0x65, 0x65, 0x89, 0x89)))
        self.assertEqual(voice[25], 3 * 8 + 4)
        self.assertEqual(voice[0], 0)
        self.assertEqual(voice[26:], bytes(6))

    def test_losses_are_explicit(self):
        report = patch_projection_report(bytes.fromhex('ff ff ff ff ff ff ff ff'))
        self.assertFalse(report['exact'])
        self.assertEqual(report['source_am'], [True, True])
        self.assertEqual(report['source_vibrato'], [True, True])
        self.assertEqual(report['source_sustained_eg'], [True, True])
        self.assertEqual(report['source_ksr'], [True, True])
        self.assertEqual(report['source_ksl'], [3, 3])
        self.assertEqual(report['source_waveform'], [1, 1])
        self.assertTrue(report['limitations'])
        with self.assertRaises(ValueError):
            patch_voice(bytes(7))

    def test_physical_frequency_matches_source_formula(self):
        clock = 3_579_545
        for fnum, block in ((1, 0), (172, 4), (256, 3), (511, 7)):
            with self.subTest(fnum=fnum, block=block):
                projection = project_pitch(fnum, block, clock)
                expected = clock / 72 * fnum * 2 ** block / 2 ** 19
                self.assertAlmostEqual(projection.source_frequency_hz, expected)
                index = NOTES.index(projection.note)
                encoded = FNUM[index] + projection.detune
                actual = 8_000_000 / 144 * encoded * 2 ** projection.target_block / 2 ** 21
                self.assertAlmostEqual(projection.target_frequency_hz, actual)
                self.assertAlmostEqual(projection.frequency_error_hz, actual - expected)
                self.assertLessEqual(abs(actual - expected), 8_000_000 * 2 ** projection.target_block / (144 * 2 ** 21) / 2)
                self.assertEqual(projection.commands[0], f'o{projection.target_block + 1}')
                self.assertNotEqual((projection.commands[0], projection.note), ('o1', 'c'))

    def test_explicit_reference_clock_changes_fnum_not_source_frequency(self):
        standard = project_pitch(256, 4, 3_579_545)
        alternate = project_pitch(256, 4, 3_579_545, target_clock_hz=7_987_200)
        self.assertEqual(standard.source_frequency_hz, alternate.source_frequency_hz)
        self.assertNotEqual((standard.target_fnum, standard.target_block),
                            (alternate.target_fnum, alternate.target_block))
        self.assertEqual(standard.target_clock_hz, 8_000_000)

    def test_invalid_pitch_is_diagnostic(self):
        for args in ((0, 0, 3_579_545), (512, 0, 3_579_545),
                     (1, 8, 3_579_545), (1, 0, 0), (1, 0, math.inf)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                project_pitch(*args)
        with self.assertRaises(ValueError):
            project_pitch(511, 7, 100_000_000)


class MucomsxOpllRhythmTests(unittest.TestCase):
    @staticmethod
    def hit(start, volume=0, **changes):
        fields = dict(ev_type='rhythm_expand', is_ryt=1, keyon=1,
                      vgmticks=start, vol=volume, fnum=172, block=4)
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_native_masks_and_simultaneous_attack_provenance(self):
        source = {channel: [self.hit(100, channel - 9)] for channel in range(9, 14)}
        units, report = project_rhythm_segments(source, {100: 10}, 30)
        self.assertEqual((units[0].start, units[0].end, units[0].note), (0, 10, 'r'))
        attack = units[1]
        self.assertEqual((attack.start, attack.end, attack.note), (10, 30, 'c'))
        self.assertEqual(attack.commands, ('v63,31,29,25,23,27,31', '@31'))
        self.assertEqual(attack.source_refs, tuple((ch, 0) for ch in range(9, 14)))
        self.assertEqual([row['instrument'] for row in report['attacks']],
                         ['BD', 'SD', 'TOM', 'CYM', 'HH'])
        self.assertFalse(report['exact'])
        self.assertEqual(report['release_policy'], 'inferred_at_next_target_rhythm_boundary')
        self.assertTrue(all(segment.vgmticks == 100 for rows in source.values()
                            for segment in rows))

    def test_ordered_attacks_and_rest_are_not_tied(self):
        units, report = project_rhythm_segments(
            {9: [self.hit(30), self.hit(10, 15)], 10: [self.hit(20)]},
            lambda tick: tick, 40)
        self.assertEqual([(unit.start, unit.end, unit.note) for unit in units],
                         [(0, 10, 'r'), (10, 20, 'c'), (20, 30, 'c'), (30, 40, 'c')])
        self.assertEqual([unit.commands[-1] for unit in units], ['@0', '@1', '@2', '@1'])
        self.assertEqual(units[1].commands[0], 'v63,1,31,31,31,31,31')
        self.assertEqual(units[1].source_refs, ((9, 1),))

    def test_unprojectable_collisions_and_zero_interval_are_rejected(self):
        cases = (
            ({9: [self.hit(10), self.hit(10)]}, {10: 1}, 2),
            ({9: [self.hit(10)], 10: [self.hit(11)]}, {10: 1, 11: 1}, 2),
            ({9: [self.hit(10)]}, {10: 2}, 2),
            ({9: [self.hit(10, 16)]}, {10: 1}, 2),
        )
        for source, clock, end in cases:
            with self.subTest(source=source), self.assertRaises(ValueError):
                project_rhythm_segments(source, clock, end)

    def test_shortened_output_excludes_replay_attacks_without_reindexing_source(self):
        source={9:[self.hit(10),self.hit(20),self.hit(30)]}
        units, report=project_rhythm_segments(source,{10:1,20:2},3,source_end_sample=30)
        self.assertEqual([a['source_segment_index'] for a in report['attacks']],[0,1])
        self.assertEqual(report['excluded_attacks'],[dict(source_channel=9,source_segment_index=2,
            source_start_vgmticks=30,reason='outside_emitted_source_timeline')])
        self.assertEqual([h.vgmticks for h in source[9]],[10,20,30])
        self.assertEqual(units[-1].end,3)
        with self.assertRaisesRegex(ValueError,'source sample 10'):
            project_rhythm_segments(source,{},3,source_end_sample=20)

    def test_inactive_and_nonrhythm_segments_do_not_create_attacks(self):
        source = {0: [self.hit(10)], 9: [self.hit(10, keyon=0)],
                  10: [self.hit(10, is_ryt=0)], 11: [self.hit(10, ev_type='note')]}
        units, report = project_rhythm_segments(source, {}, 20)
        self.assertEqual(units, ())
        self.assertEqual(report['attacks'], [])


if __name__ == '__main__':
    unittest.main()
