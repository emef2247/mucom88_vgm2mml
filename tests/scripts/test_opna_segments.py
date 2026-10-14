"""Native FM interpretation from independently authored OPNA commands."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
import opna
from test_opna_reader import parse, vgm, write
from test_opm_reader import read_rows


class OpnaSegmentTests(unittest.TestCase):
    def analyze(self, folder, commands, *, loop_offset=None, loop_count=1):
        raw = vgm(commands, opna=8000000, loop_offset=loop_offset)
        _, facts = parse(folder, raw, dump=True, loop_count=loop_count)
        return facts

    def test_partial_keys_operator_order_nochange_and_zero_sample_edges(self):
        commands = (write(0x28, 0x11) + b'\x62' + write(0x28, 0x21)
                    + write(0x28, 0x21) + write(0x28, 1) + write(0x28, 0xf1)
                    + b'\x70' + write(0x28, 1) + b'\x63')
        with tempfile.TemporaryDirectory() as tmp:
            facts = self.analyze(tmp, commands)
            events = facts['analysis'].events
            self.assertEqual([e.rising_mask for e in events], [1, 2, 0, 0, 15, 0])
            self.assertEqual([e.falling_mask for e in events], [0, 1, 0, 2, 0, 15])
            self.assertEqual([e.continuity_id for e in events], [1, 2, 2, 2, 3, 3])
            self.assertFalse(events[2].changed)
            self.assertEqual(facts['key_counts'], dict(key_writes=6, channel_attack_events=3,
                                                     operator_keyons=6, operator_keyoffs=6))
            off = [s for s in facts['analysis'].segments if s.ch == 1 and s.ev_type == 'key_off']
            self.assertEqual((off[0].vgmticks, off[0].vgmticks_end), (735, 735))
            rows = read_rows(facts['state_csv_path'])
            self.assertEqual((rows[1]['op2_key_on_edge'], rows[1]['op1_key_off_edge']), ('1', '1'))

    def test_fnum_latch_is_global_across_channel_and_port(self):
        w = lambda r, d, p=0: write(r, d, port=p)
        commands = (w(0xa0, 0x11) + w(0xa4, 0x22) + w(0xa1, 0x33)
                    + w(0xa6, 0x1d, 1) + w(0xa0, 0x44) + w(0xa2, 0x55, 1)
                    + w(0x28, 0xf0) + b'\x62')
        with tempfile.TemporaryDirectory() as tmp:
            result = self.analyze(tmp, commands)['analysis']
            first = result.events[0].state
            self.assertEqual(first.fnum_low_raw, 0x11)
            self.assertIsNone(first.fnum)
            high = [e for e in result.events if e.register == 0xa4 and e.ch == 0][0].state
            self.assertIsNone(high.fnum)  # Upper write alone never commits pitch.
            ch1 = [e for e in result.events if e.register == 0xa1][0].state
            self.assertEqual((ch1.fnum, ch1.block), (0x233, 4))
            ch0 = [e for e in result.events if e.register == 0xa0][-1].state
            ch5 = [e for e in result.events if e.register == 0xa2][-1].state
            self.assertEqual((ch0.fnum, ch0.block, ch5.fnum, ch5.block), (0x544, 3, 0x555, 3))
            self.assertEqual(opna.key_counts(result)['channel_attack_events'], 1)

    def test_operator_fields_held_voice_pan_lfo_and_enable(self):
        w = lambda r, d, p=0: write(r, d, port=p)
        commands = (w(0x29, 0x80) + w(0x28, 0xf4) + b'\x62'
                    + w(0x30, 0x71, 1) + w(0x34, 0x32, 1)
                    + w(0x38, 0x43, 1) + w(0x3c, 0x54, 1)
                    + w(0x40, 0x85, 1) + w(0x50, 0xff, 1)
                    + w(0x60, 0x9b, 1) + w(0x70, 0x13, 1)
                    + w(0x80, 0xab, 1) + w(0x90, 0x0d, 1)
                    + w(0xb0, 0x2a, 1) + w(0xb4, 0xb5, 1) + w(0x22, 0x0f) + b'\x63')
        with tempfile.TemporaryDirectory() as tmp:
            facts = self.analyze(tmp, commands)
            last = [e for e in facts['analysis'].events if e.ch == 3][-1].state
            self.assertEqual([o.mul for o in last.operators], [1, 2, 3, 4])
            operator = last.operators[0]
            self.assertEqual((operator.dt, operator.tl, operator.ks, operator.ar, operator.am_enabled,
                              operator.d1r, operator.d2r, operator.sl, operator.rr,
                              operator.ssg_eg_enabled, operator.ssg_eg_shape),
                             (7, 5, 3, 31, 1, 27, 19, 10, 11, 1, 5))
            self.assertIsNone(last.operators[1].tl)
            self.assertEqual((last.algorithm, last.feedback, last.left_enabled, last.right_enabled,
                              last.ams, last.pms, last.lfo_enabled, last.lfo_rate,
                              last.six_channel_enabled), (2, 5, 1, 0, 3, 5, 1, 7, 1))
            self.assertEqual(facts['key_counts']['channel_attack_events'], 1)

    def test_ch3_special_frequencies_prescaler_and_csm_are_source_state(self):
        commands = (write(0xac, 0x09) + write(0xa8, 0x11)
                    + write(0xad, 0x12) + write(0xa9, 0x22)
                    + write(0xae, 0x1b) + write(0xaa, 0x33)
                    + write(0xa6, 0x24) + write(0xa2, 0x44)
                    + write(0x27, 0xc0) + write(0x2f, 0)
                    + write(0x2e, 0) + write(0x2d, 0) + write(0x2e, 0) + b'\x62')
        with tempfile.TemporaryDirectory() as tmp:
            result = self.analyze(tmp, commands)['analysis']
            state = [e for e in result.events if e.ch == 2][-1].state
            self.assertEqual(state.special_frequencies,
                             (('op1', 0x222, 2), ('op2', 0x333, 3), ('op3', 0x111, 1), ('op4', 0x444, 4)))
            self.assertEqual((state.channel3_special_enabled, state.csm_enabled), (1, 1))
            prescalers = [e.state.fm_prescale_divisor for e in result.events if e.ch == 0 and e.ev_type == 'prescaler']
            self.assertEqual(prescalers, [2, 2, 6, 3])
            self.assertTrue(result.csm_observed)
            self.assertTrue(result.issues)
            self.assertEqual(opna.key_counts(result)['operator_keyons'], 0)

    def test_declared_loop_carries_held_state_and_provenance(self):
        commands = write(0x28, 0xf0) + b'\x70' + write(0xb0, 3) + b'\x71'
        with tempfile.TemporaryDirectory() as tmp:
            facts = self.analyze(tmp, commands, loop_offset=4, loop_count=2)
            result = facts['analysis']
            self.assertEqual(result.source_end_vgmticks, 5)
            self.assertEqual(opna.key_counts(result)['channel_attack_events'], 1)
            replay = [e for e in result.events if e.loop_iteration == 1]
            self.assertEqual(len(replay), 1)
            self.assertEqual((replay[0].original_event_id, replay[0].original_vgmticks,
                              replay[0].vgmticks), (2, 1, 3))
            self.assertEqual(replay[0].state.key_mask, 15)
            for ch in range(6):
                segments = [s for s in result.segments if s.ch == ch]
                self.assertEqual(segments[0].vgmticks, 0)
                self.assertEqual(segments[-1].vgmticks_end, 5)
                self.assertEqual(sum(s.duration_samples for s in segments), 5)
                self.assertTrue(all(a.vgmticks_end == b.vgmticks for a, b in zip(segments, segments[1:])))

    def test_dual_chip_isolation_and_unterminated_gate(self):
        commands = write(0x28, 0xf0) + write(0xa4, 0x22, instance=1) + write(0xa0, 0x33, instance=1) + b'\x62'
        with tempfile.TemporaryDirectory() as tmp:
            _, facts = parse(tmp, vgm(commands, opna=0x40000000 | 8000000), dump=True)
            result = facts['analysis']
            last = {(s.chip_instance, s.ch): s for s in result.segments if s.vgmticks_end == 735}
            self.assertEqual(last[0, 0].state.key_mask, 15)
            self.assertIsNone(last[0, 0].state.fnum)
            self.assertEqual(last[1, 0].state.key_mask, 0)
            self.assertEqual(last[1, 0].state.fnum, 0x233)
            self.assertEqual(opna.key_counts(result)['operator_keyoffs'], 0)


if __name__ == '__main__':
    unittest.main()
