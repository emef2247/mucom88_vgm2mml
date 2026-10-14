"""Long MML durations preserve elapsed ticks and one sounding gate."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from mdx_duration import timed
from mdx_compaction import compact
from mdx_compiler import prepare_mxc


def ticks(token):
    match = re.fullmatch(r'[a-gr][+#-]?(%?)(\d+)(\.*)', token)
    raw, number, dots = match.groups()
    numerator = int(number) if raw else 192
    denominator = 1 if raw else int(number)
    return numerator * ((1 << (len(dots) + 1)) - 1) // (denominator * (1 << len(dots)))


class MdxDurationLimitTests(unittest.TestCase):
    def test_every_chunk_within_limit_and_note_continuity_preserved(self):
        for name in ('r', 'c', 'a+'):
            for duration in (1, 128, 192, 240, 256, 257, 288, 336, 384, 65536):
                with self.subTest(name=name, duration=duration):
                    tokens = timed(name, duration).split()
                    lengths = [ticks(t) for t in tokens if t != '&']
                    self.assertTrue(all(0 < n <= 256 for n in lengths))
                    self.assertEqual(sum(lengths), duration)
                    self.assertEqual(tokens.count('&'), 0 if name == 'r' else len(lengths)-1)
        self.assertTrue(timed('c', 336, held=True).endswith('&'))

    def test_compaction_keeps_safe_rest_chunks_and_total_ticks(self):
        output, decisions = compact(timed('r', 1025), 'A')
        self.assertEqual(output, '[r%128]8 r192')
        self.assertEqual(decisions[0]['action'], 'rest_chunks')
        self.assertEqual(decisions[0]['before'].split(), ['r%128'] * 8)
        output, _ = compact(timed('r', 260*128+32), 'A')
        self.assertTrue(all(int(n) <= 256 for n in re.findall(r'r%(\d+)', output)))
        self.assertIn('[r%128]255', output)

    def test_old_dotted_mml_is_also_safe_at_mxc_boundary(self):
        self.assertEqual(prepare_mxc('A r1. r1.. r%257 r%256 r2.\n'),
                         'A r%128 r%128 r%32 r%128 r%128 r%80 r%128 r%128 r%1 r%256 r2.\n')
        self.assertEqual(prepare_mxc('#title "r1."\nA r1. ; r1..\n'),
                         '#title "r1."\nA r%128 r%128 r%32 ; r1..\n')


if __name__ == '__main__':
    unittest.main()
