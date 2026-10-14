"""Small authored native SegaPCM runtime cases; no game-derived ROM data."""
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from segapcm import SegaPcmTrace
from segapcm_playback import render


class SegaPcmPlaybackTests(unittest.TestCase):
    def trace(self, *, clock=5644800, size=65536):
        trace = SegaPcmTrace(dict(segapcm_clock_raw=clock,
                                 segapcm_interface_raw=0x70000c))
        event = SimpleNamespace(source_event_id=0, address=0, vgmticks=0,
                                loop_iteration=0, original_vgmticks=0)
        trace.rom_block(0, event, 8 + size,
                        size.to_bytes(4, 'little') + bytes(4) + bytes([129]) * size)
        return trace

    def write(self, trace, offset, value, time=0):
        index = len(trace.writes) + len(trace.rom_blocks)
        event = SimpleNamespace(source_event_id=index, address=index,
                                vgmticks=time, loop_iteration=0,
                                original_vgmticks=time)
        trace.write(index, event, offset, value)

    def voice(self, trace, *, start=0, end=0, control=2, delta=128):
        for register, value in ((2, 3), (3, 5), (4, 0), (5, 0),
                                (6, end), (7, delta),
                                (0x84, start & 255), (0x85, start >> 8),
                                (0x86, control)):
            self.write(trace, register, value)

    def test_exact_rate_live_volume_and_disable(self):
        trace = self.trace()
        self.voice(trace)
        self.write(trace, 2, 7, 2)
        self.write(trace, 0x86, 3, 3)
        native = render(trace, 5)
        self.assertEqual(native.sample_rate, Fraction(44100))
        self.assertEqual(list(native.samples), [3, 5, 3, 5, 7, 5, 0, 0, 0, 0])
        self.assertEqual(len(native.gates), 1)
        self.assertEqual((native.gates[0].start_vgmticks,
                          native.gates[0].end_vgmticks,
                          native.gates[0].termination), (0, 3, 'disable'))
        self.assertEqual(trace.states[0, 0].registers[6], 0)

    def test_natural_stop_before_fetch_and_fractional_address(self):
        trace = self.trace()
        self.voice(trace, start=255)
        native = render(trace, 5)
        self.assertEqual(list(native.samples), [3, 5, 3, 5, 0, 0, 0, 0, 0, 0])
        self.assertEqual(native.gates[0].end_vgmticks, 2)
        self.assertEqual(native.gates[0].termination, 'natural_stop')
        # Programmed source evidence is unchanged by runtime advancement.
        self.assertEqual(trace.states[0, 0].registers[6:9], (255, 0, 2))

    def test_loop_reset_and_live_rom_update(self):
        trace = self.trace()
        self.voice(trace, start=255, control=0, delta=255)
        index = len(trace.writes) + len(trace.rom_blocks)
        event = SimpleNamespace(source_event_id=index, address=index,
                                vgmticks=2, loop_iteration=0, original_vgmticks=2)
        trace.rom_block(index, event, 9,
                        (65536).to_bytes(4, 'little') + bytes(4) + bytes([130]))
        native = render(trace, 4, loop_start=1)
        self.assertEqual(list(native.samples), [3, 5, 3, 5, 6, 10, 6, 10])
        loops = [event for event in native.runtime_events if event.kind == 'sample_loop']
        self.assertEqual(loops[0].vgmticks, 2)
        self.assertEqual(loops[0].address_fixed, 0)
        self.assertEqual(native.gates[0].termination, 'source_end')

    def test_natural_stop_restart_clears_fraction(self):
        trace = self.trace()
        self.voice(trace, start=255, delta=255)
        for register, value in ((0x84, 0), (0x85, 0), (0x86, 2)):
            self.write(trace, register, value, 3)
        native = render(trace, 5)
        stop = next(e for e in native.runtime_events if e.kind == 'natural_stop')
        restart = next(e for e in native.runtime_events
                       if e.kind == 'control_write' and e.vgmticks == 3)
        self.assertEqual(stop.vgmticks, 2)
        self.assertEqual(stop.address_fixed & 255, 0)
        self.assertEqual(restart.address_fixed, 0)
        self.assertEqual(len(native.gates), 2)

    def test_rational_frame_time_and_dump(self):
        trace = self.trace(clock=4000001)
        self.voice(trace)
        native = render(trace, 4)
        self.assertEqual(native.sample_rate, Fraction(4000001, 128))
        self.assertEqual(native.frame_count, 3)
        with tempfile.TemporaryDirectory() as tmp:
            summary = native.dump(tmp, 'authored')
            self.assertEqual(Path(summary['waveform_path']).stat().st_size, 24)
            self.assertFalse(summary['source_state_modified'])

    def test_source_validation_and_resource_bounds(self):
        trace = self.trace()
        self.voice(trace)
        with self.assertRaisesRegex(ValueError, 'frame resource limit'):
            render(trace, 10, max_frames=2)
        self.write(trace, 0x100, 0)
        native = render(trace, 10)
        self.assertTrue(any('Unused RAM' in issue for issue in native.issues))
        self.assertEqual(native.missing_rom_reads, 0)
        # A sparse update has defined neutral backing but missing source evidence.
        trace.rom_blocks[0] = type(trace.rom_blocks[0])(
            **{**trace.rom_blocks[0].__dict__, 'payload': b'\x81'})
        native = render(trace, 10)
        self.assertGreater(native.missing_rom_reads, 0)


if __name__ == '__main__':
    unittest.main()
