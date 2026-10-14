"""Exact duration and setter behavior without source-song assumptions."""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from mucom_compaction import compact
from source_loop_plan import expanded_tokens


def observations(text):
    """Independent effective-state/timing interpreter for the tested subset."""
    octave, detune, clock = 6, 0, 128
    result = []
    for word in expanded_tokens(text):
        match = re.fullmatch(r'(o|D|C)(-?\d+)', word)
        if match:
            value = int(match[2])
            if match[1] == 'o': octave = value
            elif match[1] == 'D': detune = value
            else: clock = value
            continue
        match = re.fullmatch(r'([a-gr][+-]?)(?:%(\d+)|(\d+)(\.)?)', word)
        if match:
            ticks = int(match[2]) if match[2] else clock // int(match[3])
            if match[4]: ticks += ticks // 2
            result.append(('duration', match[1], ticks, octave, detune))
        else:
            # Keep every side effect and its ordering, including zero-time keys.
            result.append(('control', word))
    return result


class MucomCompactionTests(unittest.TestCase):
    def equivalent(self, text, part='A', **kwargs):
        out, rows = compact(text, part, **kwargs)
        self.assertEqual(observations(text), observations(out))
        self.assertTrue(all(set(row) == {'track', 'token_index', 'action', 'before',
                                        'after', 'reason', 'bytes_saved'} for row in rows))
        return out, rows

    def test_known_octave_detune_are_reused(self):
        out, rows = self.equivalent('o4 D-7 c%16 o4 D-7 d%24')
        self.assertEqual(out, 'o4 D-7 c8 d8.')
        self.assertEqual(len([r for r in rows if r['action'] == 'omit_setter']), 2)

    def test_nested_loop_entries_and_back_edges_keep_absolute_settings(self):
        source = 'o4 D0 [o4 D0 c%16 [o5 D1 d%16 o6 D2 e%16]2 o6 D2 f%16]3 o6 D2 g%16'
        out, _ = self.equivalent(source)
        self.assertIn('[ o4 D0', out)
        self.assertIn('[ o5 D1', out)
        self.assertIn(']2 o6 D2', out)
        self.assertIn(']3 o6 D2', out)

    def test_song_loop_entry_remains_explicit(self):
        out, _ = self.equivalent('o4 D1 c%16 L o4 D1 d%16 o4 D1 e%16')
        self.assertIn('L o4 D1 d8 e8', out)

    def test_side_effects_invalidate_and_are_never_removed(self):
        for action in ('@1', 'H0,0,0,0', 'yTL,1,10', 'y$28,0', 'KD0,0,0,0',
                       'v10', 'p3', 'q0', 'unknown', 'D1+', '>'):
            source = f'o4 D0 c%16 {action} {action} o4 D0 d%16'
            out, _ = self.equivalent(source)
            self.assertIn(f'{action} {action} o4 D0', out)

    def test_zero_time_key_edges_and_ties_keep_order(self):
        source = 'o4 D0 y$28,0 y$28,240 KD128,0,0,0 c%16 & yTL,1,20 o4 D0 c%24 & c%32'
        self.equivalent(source)

    def test_every_exact_normalized_length_retains_ticks(self):
        source = ' '.join(f'c%{ticks} r%{ticks}' for ticks in range(1, 112))
        out, _ = self.equivalent(source)
        self.assertIn('c%7', out)
        self.assertIn('c2.', out)
        self.assertIn('r128', out)

    def test_out_of_range_and_nonintegral_dotted_values_remain_explicit(self):
        out, _ = self.equivalent('c%0 c%3 c%7 c%112 c%128 c%256')
        self.assertEqual(out, 'c%0 c64. c%7 c%112 c%128 c%256')

    def test_clock_changes_suspend_c128_spelling(self):
        out, _ = self.equivalent('C96 c%16 C128 c%16')
        self.assertEqual(out, 'C96 c%16 C128 c8')

    def test_durations_can_be_disabled_independently(self):
        out, _ = self.equivalent('o4 D0 c%16 o4 D0 d%24', durations=False)
        self.assertEqual(out, 'o4 D0 c%16 d%24')

    def test_pcm_octave_is_never_omitted_or_made_relative(self):
        out, _ = self.equivalent('o4 c%16 o4 d%16 o5 e%16', part='K')
        self.assertEqual(out, 'o4 c8 o4 d8 o5 e8')

    def test_rest_loops_preserve_existing_chunks_not_merged_lengths(self):
        source = ' '.join(['r%111'] * 12 + ['r%10'])
        out, rows = self.equivalent(source)
        self.assertEqual(out, '[ r%111 ]12 r%10')
        self.assertTrue(any(row['action'] == 'duration_loop' for row in rows))

    def test_tied_chunks_keep_trailing_tie_and_key_delay_in_loop(self):
        source = 'o4 ' + ' '.join(['KD128,0,0,0 c%111 &'] * 8) + ' c%10'
        out, _ = self.equivalent(source)
        self.assertEqual(out, 'o4 [ KD128,0,0,0 c%111 & ]8 c%10')

    def test_ordinary_retrigger_notes_are_not_folded_as_duration_chunks(self):
        out, rows = self.equivalent(' '.join(['c%16'] * 8))
        self.assertNotIn('[', out)
        self.assertFalse(any(row['action'] == 'duration_loop' for row in rows))

    def test_loop_depth_and_native_repeat_count_limits(self):
        source = ' '.join(['['] * 16 + ['r%111'] * 5 + [']2'] * 16)
        out, rows = compact(source, 'A')
        self.assertFalse(any(row['action'] == 'duration_loop' for row in rows))
        source = ' '.join(['r%111'] * 510)
        out, _ = self.equivalent(source)
        self.assertEqual(out, '[ r%111 ]255 [ r%111 ]255')

    def test_duration_loops_disabled_with_duration_policy(self):
        source = ' '.join(['r%111'] * 8)
        out, rows = self.equivalent(source, durations=False)
        self.assertEqual(out, source)
        self.assertEqual(rows, [])

    def test_repeat_compaction_is_idempotent(self):
        out, _ = self.equivalent('o4 D1 [o4 D1 c%16 o4 D1 d%24]2 o4 D1 e%32')
        self.assertEqual(compact(out, 'A')[0], out)


if __name__ == '__main__':
    unittest.main()
