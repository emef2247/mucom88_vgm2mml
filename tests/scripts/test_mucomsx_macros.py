"""Authored tests of target macro boundaries and exact rendered-unit expansion."""
from pathlib import Path
import sys
import unittest
import re
import random
from collections import defaultdict
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
import mucomsx_macros as macros
from mucomsx_macros import compact_tracks, MAX_DEFINITION_BYTES, MAX_BODY_CHARACTERS, _structured


def unpruned_candidates(locations, max_width):
    """Original exhaustive enumerator retained only as an equivalence oracle."""
    result = defaultdict(list)
    for loc, (nodes, _) in enumerate(locations):
        words = [macros._node_text(node) for node in nodes]
        blocked = ['*' in word or re.search(r'(?<![A-Za-z])L(?![A-Za-z])', word)
                   for word in words]
        for start in range(len(words)):
            if words[start] == '&' or blocked[start]:
                continue
            size = 0
            for width in range(1, min(max_width, len(words) - start) + 1):
                stop = start + width
                size += len(words[stop - 1]) + (width > 1)
                if size > MAX_BODY_CHARACTERS or blocked[stop - 1]:
                    break
                if words[stop - 1] == '&' or (stop < len(words) and words[stop] == '&'):
                    continue
                result[tuple(words[start:stop])].append((loc, start))
    return result


class MucomsxMacrosTests(unittest.TestCase):
    def test_reuses_cross_track_units_and_keeps_finite_repeat_atomic(self):
        unit = '@1 o4 l8 [c d e f]3 r4'
        source = {'A': [unit, 'r1', unit], 'B': [unit, 'r2', unit]}
        definitions, tracks, report = compact_tracks(source)
        self.assertEqual(definitions, [f'# *1{{{unit}}}'])
        self.assertEqual(tracks['A'], ['*1', 'r1', '*1'])
        self.assertEqual(source['A'], [unit, 'r1', unit])
        self.assertEqual(report['selected_call_depth'], 1)
        self.assertTrue(all(row['byte_identity'] and row['unit_identity']
                            for row in report['expansion_proof'].values()))
        self.assertGreater(report['saving_bytes'], 0)

    def test_song_loop_marker_stays_outside_macros(self):
        short = 'o4 c8'
        source = {'A': [short, 'L', short], 'B': [short, 'L', short]}
        definitions, tracks, report = compact_tracks(source)
        self.assertEqual(definitions, [])
        self.assertEqual(tracks, source)
        long = '@1 o4 l8 c d e f g a b >c'
        definitions, tracks, report = compact_tracks({'A': [long, 'L', long]})
        self.assertEqual(tracks['A'], ['*1', 'L', '*1'])
        self.assertNotIn('L', definitions[0])

    def test_search_body_bound_is_separate_from_physical_limit(self):
        for body_size in (MAX_BODY_CHARACTERS, MAX_BODY_CHARACTERS + 1):
            with self.subTest(body_size=body_size):
                unit = 'D' + '1' * (body_size - 1)
                definitions, tracks, report = compact_tracks({'A': [unit] * 3})
                if body_size == MAX_BODY_CHARACTERS:
                    self.assertTrue(definitions)
                    self.assertLess(len(definitions[0].encode('shift_jis')),
                                    MAX_DEFINITION_BYTES)
                else:
                    self.assertEqual(definitions, [])
                    self.assertEqual(tracks['A'], [unit] * 3)

    def test_definition_cost_includes_multibyte_encoding(self):
        unit = 'あ' * 510
        definitions, tracks, report = compact_tracks({'A': [unit] * 3})
        self.assertEqual(definitions, [])
        self.assertEqual(report['saving_bytes'], 0)

    def test_no_calls_in_definitions_and_nonoverlapping_occurrences(self):
        a, b = '@1 o4 l8 c d e f', '@2 o5 l8 g a b c'
        definitions, tracks, report = compact_tracks({'A': [a, b] * 12})
        self.assertTrue(definitions)
        self.assertTrue(all('*' not in row['body'] for row in report['macros']))
        self.assertTrue(report['expansion_proof']['A']['token_identity'])

    def test_no_saving_and_empty_inputs(self):
        for source in ({}, {'A': []}, {'A': ['c8', 'd8']}):
            definitions, tracks, report = compact_tracks(source)
            self.assertEqual(definitions, [])
            self.assertEqual(tracks, source)
            self.assertEqual(report['saving_bytes'], 0)

    def test_rejects_existing_calls_and_physical_lines(self):
        for unit in ('*1', 'c8\nd8', '{c8}', ';comment'):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                compact_tracks({'A': [unit]})


    def test_control_prefixes_inside_varying_units(self):
        prefix = 'E255,0,0,0,0,255 P1 D-30 o4'
        source = {'A': [prefix + ' ' + note + '%12' for note in ('c', 'd', 'e', 'f', 'g')]}
        definitions, tracks, report = compact_tracks(source)
        self.assertTrue(definitions)
        self.assertIn(prefix, definitions[0])
        self.assertEqual(report['algorithm'], 'msx_exact_sequence_greedy_token_tree')
        self.assertTrue(report['expansion_proof']['A']['token_identity'])
        self.assertTrue(report['expansion_proof']['A']['loop_tree_identity'])
        self.assertGreater(report['saving_bytes'], 0)

    def test_nested_finite_loops_searches_inner_prefix(self):
        prefix = 'yTL,1,33 yTL,3,44 yTL,2,55 yTL,4,66'
        source = {'A': ['[' + ' '.join(prefix + ' ' + note + '%12'
                    for note in ('c', 'd', 'e', 'f')) + ']3'],
                  'B': ['[[' + prefix + ' g%12]2 ' + prefix + ' a%12]4']}
        definitions, tracks, report = compact_tracks(source)
        self.assertTrue(definitions)
        self.assertTrue(any(prefix in line for line in definitions))
        self.assertIn(']3', tracks['A'][0])
        self.assertIn(']2', tracks['B'][0])
        self.assertIn(']4', tracks['B'][0])
        self.assertTrue(report['expansion_proof']['A']['loop_tree_identity'])
        self.assertTrue(all('*' not in row['body'] for row in report['macros']))
        self.assertTrue(all(row['definition_bytes'] <= MAX_DEFINITION_BYTES
                            for row in report['macros']))

    def test_ties_multiarguments_and_L_stay_structural(self):
        prefix = 'yTL,1,33 yTL,3,44 E255,0,0,0,0,255'
        source = {'A': [prefix + ' c%12 & c%12 L ' + prefix + ' d%12 & d%12'] * 3}
        definitions, tracks, report = compact_tracks(source)
        self.assertTrue(report['expansion_proof']['A']['token_identity'])
        for row in report['macros']:
            self.assertIsNone(re.search(r'(?<![A-Za-z])L(?![A-Za-z])', row['body']))
            self.assertFalse(row['body'].startswith('&'))
            self.assertFalse(row['body'].endswith('&'))
        self.assertEqual(len(re.findall(r'(?<![A-Za-z])L(?![A-Za-z])', ' '.join(tracks['A']))), 3)

    def test_search_crosses_original_unit_boundaries(self):
        prefix = 'E255,0,0,0,0,255 P1 D-30 o4'
        # Different boundaries used for the same target command stream.
        source = {'A': [prefix + ' c%12', 'v7 d%12', prefix + ' e%12'],
                  'B': ['E255,0,0,0,0,255', 'P1 D-30 o4 f%12']}
        definitions, tracks, report = compact_tracks(source)
        self.assertTrue(any(prefix in row for row in definitions))
        self.assertTrue(all(row['loop_tree_identity'] for row in report['expansion_proof'].values()))
        self.assertEqual([row['strategy'] for row in report['alternatives']],
                         ['baseline', 'width24-first0', 'width64-first0',
                          'width64-first1', 'width64-first2'])
        self.assertEqual(report['selected_bytes'],
                         min(row['selected_bytes'] for row in report['alternatives']))

    def test_tie_across_units_is_not_a_candidate_boundary(self):
        prefix = 'E255,0,0,0,0,255 P1 D-30 o4'
        source = {'A': [prefix + ' c%12', '& c%12', 'r4'] * 5}
        definitions, tracks, report = compact_tracks(source)
        for row in report['macros']:
            self.assertFalse(row['body'].startswith('&'))
            self.assertFalse(row['body'].endswith('&'))
            self.assertNotEqual(row['body'], prefix + ' c%12')
        self.assertTrue(report['expansion_proof']['A']['loop_tree_identity'])

    def test_limits_determinism_and_merged_tree_ancestry(self):
        prefix = 'yTL,1,33 yTL,3,44 yTL,2,55 yTL,4,66'
        source = {'A': ['[' + prefix + ' c%12 ' + prefix + ' d%12]2'] * 5,
                  'B': [prefix + ' e%12 ' + prefix + ' f%12'] * 3}
        first = compact_tracks(source, limit=100)
        self.assertEqual(first, compact_tracks(source, limit=100))
        self.assertLessEqual(len(first[0]), 32)
        for row in first[2]['macros']:
            self.assertLessEqual(len(row['body']), MAX_BODY_CHARACTERS)
            self.assertNotIn('*', row['body'])
        self.assertTrue(all(row['loop_tree_identity']
                            for row in first[2]['expansion_proof'].values()))

    def test_alternative_first_candidate_can_improve_allocation(self):
        source = {'A': ['o4 v12 o4 v12 o4 v12 o4 v12 d%12 f%12 v12 '
            'c%12 v12 v12 f%12 c%12 D0 d%12 v12 D0 v12 o4 D0 '
            'o4 v12 d%12 f%12 v12 o4 v12 c%12 v12 v12 f%12 c%12 D0 '
            'd%12 v12 D0 v12 o4 D0 d%12 v12 D0 v12 o4 D0 '
            'c%12 v12 v12 f%12 c%12 D0 o4 v12 v12 o4 D0 d%12 v12 D0 v12 o4 D0']}
        greedy, alternate = [_structured(source, limit=2, max_width=24,
            first_rank=rank, encoding='shift_jis') for rank in (0, 1)]
        self.assertLess(alternate[2]['selected_bytes'], greedy[2]['selected_bytes'])
        self.assertEqual(alternate[2]['first_candidate_rank'], 1)
        self.assertTrue(alternate[2]['expansion_proof']['A']['token_identity'])

    def test_repeated_prefix_pruning_matches_exhaustive_random_trees(self):
        rng = random.Random(20261020)
        tokens = ['c%12', 'd%6', 'e%7', 'v12', 'v5', 'D0', 'o4', '&', 'L']
        def body(depth=0):
            return ' '.join(('[' + body(depth + 1) + ']2')
                            if depth < 2 and rng.randrange(5) == 0
                            else rng.choice(tokens)
                            for _ in range(rng.randrange(3, 12)))
        for case in range(24):
            repeated = body()
            source = {'A': [body(), repeated, repeated], 'B': [repeated, body()]}
            width = rng.choice((3, 5, 12))
            roots = {part: macros._parse_muc(' '.join(units)) for part, units in source.items()}
            locations = list(macros._locations(roots))
            expected = {seq: positions for seq, positions in unpruned_candidates(locations, width).items()
                        if len(positions) >= 2}
            self.assertEqual(macros._candidate_sequences(locations, width), expected)
            for rank in (0, 1, 2):
                with self.subTest(case=case, rank=rank):
                    actual = _structured(source, limit=4, max_width=width,
                                         first_rank=rank, encoding='shift_jis')
                    with patch.object(macros, '_candidate_sequences', unpruned_candidates):
                        reference = _structured(source, limit=4, max_width=width,
                                                first_rank=rank, encoding='shift_jis')
                    self.assertEqual(actual, reference)

    def test_unknown_dialect_conservatively_retains_atomic_path(self):
        source = {'A': ['unknown: keep-this-intact', 'c%12']}
        definitions, tracks, report = compact_tracks(source)
        self.assertEqual(tracks, source)
        self.assertIn('Unsupported', report['structured_search_status'])


if __name__ == '__main__':
    unittest.main()
