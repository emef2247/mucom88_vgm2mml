"""Integer native clock arithmetic agrees with the former rational operations."""
from fractions import Fraction
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'py'), str(ROOT/'tests/scripts')]
import mucom_normalization as normalization
from test_mucom_fm_edges import authored_vgm
from vgm_reader import parse_vgm


def fraction_domains(times, bound, period):
    lower = [max(0, -(-(t-bound)*period.denominator//period.numerator)) for t in times]
    upper = [(t+bound)*period.denominator//period.numerator for t in times]
    lower[0] = upper[0] = 0
    return lower, upper, [round(Fraction(t, 1)/period) for t in times]


class NativeClockArithmeticTests(unittest.TestCase):
    def test_signed_ratios_and_even_odd_halfway_cases(self):
        for denominator in range(1, 80):
            for numerator in range(-240, 241):
                self.assertEqual(normalization._round_ratio(numerator, denominator),
                                 round(Fraction(numerator, denominator)))
        for integer in (0, 1, 2, 3, 2**80, 2**80+1):
            self.assertEqual(normalization._round_ratio(2*integer+1, 2),
                             round(Fraction(2*integer+1, 2)))

    def test_every_timer_domains_and_error_values_match_fraction(self):
        rng = random.Random(2608)
        times = sorted({0, 1, 351, 352, 353, 65535, 10**15} |
                       {rng.randrange(1, 10**9) for _ in range(40)})
        for timer in range(256):
            period = (256-timer)*normalization.TIMER_UNIT
            for bound in (1, 352, 705):
                with self.subTest(timer=timer, bound=bound):
                    actual = normalization._native_tick_domains(times, bound, period)
                    self.assertEqual(actual, fraction_domains(times, bound, period))
                    for t, tick in zip(times, actual[2]):
                        error = tick*period.numerator-t*period.denominator
                        self.assertEqual(error/period.denominator, float(tick*period-t))
                        self.assertEqual(abs(error) > bound*period.denominator,
                                         abs(tick*period-t) > bound)

    def test_allocator_selected_and_rejected_candidates_match_fraction_reference(self):
        patterns = (
            (((0xf0, 1), (0xe0, 1), (0xf0, 1000), (0, 800)), 8),
            (((0xf0, 352), (0, 800), (0xf0, 353), (0, 800)), 8),
            (tuple((0xf0 if i%2 == 0 else 0xe0, 1) for i in range(100))+((0, 800),), .03),
        )
        applied = []
        for pattern, milliseconds in patterns:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root/'clock.vgm'
                source.write_bytes(authored_vgm(pattern))
                metadata = {}
                parse_vgm(str(source), str(root/'source'), opna_metadata=metadata,
                          loop_count=1, dump_opna_segments=True)
                analysis = metadata['analysis']
                pcm = SimpleNamespace(segments=(), controls=(),
                                      source_end_vgmticks=analysis.source_end_vgmticks)
                args = analysis, {}, pcm, analysis.source_end_vgmticks, None, milliseconds
                actual = normalization.normalize_source(*args)
                with patch.object(normalization, '_native_tick_domains', fraction_domains):
                    reference = normalization.normalize_source(*args)
                self.assertEqual(actual, reference)
                applied.append(actual[-1]['applied'])
        self.assertEqual(applied, [True, True, False])


if __name__ == '__main__':
    unittest.main()
