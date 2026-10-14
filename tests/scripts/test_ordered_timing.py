"""Target-neutral ordered allocation and inherited MDX domain compatibility."""
from itertools import product
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from ordered_timing import allocate_tick_domains
from opm_output_normalization import bounded_ticks
from opm_mdx import mdx_tick, projected_samples


def interval(start, end):
    return dict(start_vgmticks=start, end_vgmticks=end)


class OrderedTimingTests(unittest.TestCase):
    def test_nonadjacent_protection_allows_internal_controls_to_coalesce(self):
        result = allocate_tick_domains([100, 101, 102, 110], [5, 5, 5, 5],
            [5, 6, 6, 6], [5, 5, 5, 5], [interval(100, 110)])
        self.assertEqual(result, {100: 5, 101: 5, 102: 5, 110: 6})

    def test_backwards_tightening_leaves_room_for_positive_chain(self):
        result = allocate_tick_domains([0, 10, 20], [0, 0, 0], [2, 2, 2],
            [2, 2, 2], [interval(0, 10), interval(10, 20)])
        self.assertEqual(result, {0: 0, 10: 1, 20: 2})

    def test_infeasible_positive_chain_returns_none(self):
        self.assertIsNone(allocate_tick_domains([0, 10, 20], [0, 0, 0],
            [1, 1, 1], [0, 0, 0], [interval(0, 10), interval(10, 20)]))

    def test_order_preserved_with_conflicting_nearest_and_fixed_anchors(self):
        result = allocate_tick_domains([12, 13, 14, 15], [7, 7, 7, 9],
            [7, 9, 9, 9], [7, 9, 7, 9], [])
        self.assertEqual(result, {12: 7, 13: 9, 14: 9, 15: 9})

    def test_inputs_not_mutated(self):
        times, lower, upper, nearest = [0, 10], [0, 0], [0, 1], [0, 0]
        protected = [interval(0, 10)]
        before = (times[:], lower[:], upper[:], nearest[:], [dict(protected[0])])
        allocate_tick_domains(times, lower, upper, nearest, protected)
        self.assertEqual((times, lower, upper, nearest, protected), before)

    def test_empty_and_zero_length_protection(self):
        self.assertEqual(allocate_tick_domains([], [], [], [], []), {})
        self.assertEqual(allocate_tick_domains([7], [3], [3], [0],
            [interval(7, 7)]), {7: 3})

    def test_invalid_arrays(self):
        for args in (([0], [], [0], [0]), ([1, 0], [0, 0], [1, 1], [0, 0]),
                     ([0, 0], [0, 0], [1, 1], [0, 0]),
                     ([-1], [0], [0], [0]), ([0.0], [0], [0], [0]),
                     ([0], [False], [0], [0]), ([0], [0], [0], [0.0])):
            with self.subTest(args=args), self.assertRaises(ValueError):
                allocate_tick_domains(*args, [])

    def test_invalid_protected_intervals(self):
        for protected in ([interval(0, 9)], [interval(10, 0)],
                          [interval(0.0, 10)], [{}], [None]):
            with self.subTest(protected=protected), self.assertRaises(ValueError):
                allocate_tick_domains([0, 10], [0, 0], [0, 1], [0, 1], protected)

    def test_empty_domain_is_infeasible(self):
        self.assertIsNone(allocate_tick_domains([0], [2], [1], [1], []))

    def test_feasibility_matches_exhaustive_search(self):
        times = [0, 3, 5, 8]
        edges = [interval(0, 5), interval(3, 8)]
        for upper in product(range(3), repeat=4):
            feasible = any(all(a <= b for a, b in zip(ticks, ticks[1:]))
                and ticks[0] < ticks[2] and ticks[1] < ticks[3]
                for ticks in product(*(range(u + 1) for u in upper)))
            result = allocate_tick_domains(times, [0] * 4, upper, [1] * 4, edges)
            self.assertEqual(result is not None, feasible, upper)

    def test_mdx_exact_original_sample_domains_include_floor_boundary(self):
        # Tick 1 projects to floor(7056/625)=11 source samples, not 11.2896.
        self.assertEqual(bounded_ticks([0, 11], [interval(0, 11)], 1, 0),
                         {0: 0, 11: 1})
        self.assertIsNone(bounded_ticks([0, 12], [interval(0, 12)], 1, 0))
        self.assertEqual(bounded_ticks([0, 12], [interval(0, 12)], 1, 1),
                         {0: 0, 12: 1})

    def test_mdx_wrapper_matches_original_algorithm(self):
        def original(times, protected, multiplier, bound):
            numerator, denominator = 7056 * multiplier, 625
            ceil_div = lambda a: -(-a // numerator)
            lower = [max(0, ceil_div((t-bound)*denominator)) for t in times]
            upper = [ceil_div((t+bound+1)*denominator)-1 for t in times]
            lower[0] = upper[0] = 0
            index = {t: i for i, t in enumerate(times)}
            outgoing = [[(i+1, 0)] if i+1 < len(times) else [] for i in range(len(times))]
            incoming = [[] for _ in times]
            for row in protected:
                a, b = row['start_vgmticks'], row['end_vgmticks']
                if b > a:
                    outgoing[index[a]].append((index[b], 1))
            for i in range(len(times)-1, -1, -1):
                for j, delta in outgoing[i]:
                    upper[i] = min(upper[i], upper[j]-delta)
                    incoming[j].append((i, delta))
                if lower[i] > upper[i]:
                    return None
            chosen = []
            for i, t in enumerate(times):
                earliest = max([lower[i]] + [chosen[j]+delta for j, delta in incoming[i]])
                if earliest > upper[i]:
                    return None
                chosen.append(min(upper[i], max(earliest, mdx_tick(t, multiplier))))
            return dict(zip(times, chosen))
        for times in ([0, 1, 3], [0, 10, 11, 20], [0, 1000, 1353, 2000]):
            for multiplier, bound in product((1, 7, 65), (0, 3, 8, 352)):
                protected = [interval(times[0], times[-1]), interval(times[1], times[-1])]
                actual = bounded_ticks(times, protected, multiplier, bound)
                self.assertEqual(actual, original(times, protected, multiplier, bound))
                if actual:
                    self.assertTrue(all(abs(projected_samples(actual[t], multiplier)-t) <= bound
                                        for t in times))


if __name__ == '__main__':
    unittest.main()
