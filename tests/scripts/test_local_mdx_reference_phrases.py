"""Authored checks of the private-reference prefix selection, no private music."""
from fractions import Fraction
from pathlib import Path
import unittest

from generate_local_mdx_reference_phrases import (
    ROOT, cut_track, duration, expand, private_destination, score_parts, tokens)
from generate_mdx_end_tail_trials import add_tail, verify_tail
from mdx_reference_expectations import read_mdx
from test_mdx_reference_expectations import score


class PrivatePhraseSelectionTests(unittest.TestCase):
    def test_nested_finite_repeats_keep_controls_and_line_provenance(self):
        body, rows = cut_track([(7, '@1 MP2,5,12 MD48 @q1 D4 r%12 L [v12 [c16 r16]2 (]2')], 108)
        self.assertEqual([r['text'] for r in rows].count('c16'), 4)
        self.assertEqual([r['text'] for r in rows].count('('), 1)
        self.assertTrue(all(r['source_line'] == 7 for r in rows))
        self.assertEqual(len(next(r for r in rows if r['text']=='c16')['repeat_iterations']), 2)
        self.assertTrue(body.startswith('@1 MP2,5,12 MD48 @q1 D4 r%12'))
        self.assertNotIn('L', body)
        self.assertEqual(sum(r['duration_ticks'] for r in rows), 108)

    def test_never_shorten_notes_or_supply_unknown_syntax(self):
        with self.assertRaisesRegex(ValueError, 'crosses'):
            cut_track([(1, '@1 c4')], 47)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            tokens([(1, 'c4 XYZ')])
        with self.assertRaisesRegex(ValueError, 'Unterminated'):
            expand(tokens([(1, '[c4')]))
        with self.assertRaisesRegex(ValueError, 'Explicit'):
            expand(tokens([(1, '[c4]')]))
        self.assertEqual(duration('c+2^8'), 120)
        self.assertEqual(duration('n1,16.'), 18)
        self.assertEqual(duration('r%13'), Fraction(13))

    def test_private_destination_and_score_structure_are_explicit(self):
        self.assertEqual(private_destination(ROOT/'outputs/private-trial'), (ROOT/'outputs/private-trial').resolve())
        with self.assertRaisesRegex(ValueError, 'Private assets'):
            private_destination(ROOT/'tests/fixtures/public/private-trial')
        prefix, tracks = score_parts('#title "authored"\nA @1 c4\nP F4 n1,4\n\x1a')
        self.assertEqual(prefix, ['#title "authored"'])
        self.assertEqual(tracks['P'], [(3, 'F4 n1,4')])
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            score_parts('A c4\n#unknown')

    def test_tail_preserves_pcm_and_rejects_gate_change(self):
        original = read_mdx(score(a=b'\xfd\x01\xad\x0b\xf1\x00',
                                  p=b'\x81\x0b\xf1\x00'))
        candidate = read_mdx(score(a=b'\xfd\x01\xad\x0b\x17\xf1\x00',
                                   p=b'\x81\x0b\xf1\x00'))
        checks = verify_tail(original, candidate, 'A', 24)
        pcm = next(c for c in checks if c['track']=='P')
        self.assertEqual(pcm['added_rest_ticks'], 0)
        self.assertEqual(pcm['original_end_tick'], pcm['new_end_tick'])
        altered = read_mdx(score(a=b'\xfd\x01\xf8\x04\xad\x0b\x17\xf1\x00',
                                 p=b'\x81\x0b\xf1\x00'))
        with self.assertRaisesRegex(ValueError, 'commands changed'):
            verify_tail(original, altered, 'A', 24)

    def test_tail_appends_only_selected_track_and_changes_title(self):
        result = add_tail('#title "authored"\nA c16\nP n1,16\n', 'trial', 'A', 24)
        self.assertEqual(result, '#title "trial"\nA c16\nP n1,16\nA r%24\n')
        self.assertTrue(add_tail('#title "authored"\nP n1,16\n', 'trial', 'P', 384)
                        .endswith('P r%128 r%128 r%128\n'))
        with self.assertRaises(ValueError):
            add_tail('#title "authored"\n', 'trial', 'B', 24)


if __name__ == '__main__':
    unittest.main()
