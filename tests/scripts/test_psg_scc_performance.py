"""Shared PSG/SCC performance engine checks (synced from msx_vgm2mml)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from chip_segments import PsgSegment
from psg_scc_performance import analyze, audible


def psg(tick=0, length=2, **kwargs):
    values = dict(ev_type='v', time=0.0, ch=0, ticks=tick, l=length,
                  tone_period=100, volume=12, octave=4, scale='c', volume_delta=0,
                  pass3_row=('authored',), mode=1, noise_period=3,
                  envelope_enabled=0, envelope_period=10, envelope_shape=9,
                  mixer=0, amplitude_register=12, vgmticks=tick * 735,
                  vgmticks_end=(tick + length) * 735)
    values.update(kwargs)
    return PsgSegment(**values)


class PsgSccPerformanceTests(unittest.TestCase):
    def test_noise_only_psg_channel_forms_gates(self):
        # A dedicated noise channel has no pitch: its sounding segments
        # carry scale 'r' with mode 2 and must still count as audible,
        # or the whole noise part silently produces no gates or units.
        rows = [psg(0, ch=2, scale='r', tone_period=0, mode=2, volume=14),
                psg(2, ch=2, scale='r', tone_period=0, mode=2, volume=0),
                psg(4, ch=2, scale='r', tone_period=0, mode=2, volume=14)]
        self.assertTrue(audible(rows[0], 'psg'))
        self.assertFalse(audible(rows[1], 'psg'))
        performance = analyze({2: rows}, 'psg')
        self.assertEqual(len(performance.gates), 2)
        notes = [n for _, ns in performance.channels for n in ns]
        self.assertTrue(any(not n.rest for n in notes))


if __name__ == '__main__':
    unittest.main()
