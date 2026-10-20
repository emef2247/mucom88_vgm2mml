"""Authored DCSG target-view allocation through inherited MSX interpretation."""
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'tests/scripts')]
from dcsg import analyze
from dcsg_music import load_dcsg_bundle, project_views, noise_slot, sn_amplitude, ay_level
from test_dcsg_source import vgm, tone, write


class DcsgMusicTests(unittest.TestCase):
    def test_volume_curve_and_noise_uses_free_tone_channel(self):
        self.assertEqual(ay_level(sn_amplitude(0)), 15)
        self.assertEqual(ay_level(sn_amplitude(3)), 13)
        self.assertEqual(ay_level(sn_amplitude(15)), 0)
        slot, level, error = noise_slot([1, 0, .5], .5, previous=0)
        self.assertEqual(slot, 1)
        commands = tone(0, 100) + write(0x90) + write(0xe4) + write(0xf3) + b'\x62'
        native = analyze(vgm(commands))
        views, audit, assessments = project_views(native)
        self.assertEqual(views[0][0].mode, 1)
        self.assertEqual(views[1][0].mode, 2)
        self.assertEqual(audit[0]['noise_target_channel'], 1)
        self.assertTrue(any('lfsr' in a['reason'] for a in assessments))
        self.assertEqual(len([s for s in native.segments if s.ch == 3]), 1)

    def test_four_voices_retained_noise_shared_without_replacing_tone(self):
        commands = b''.join(tone(ch, 100 + ch * 50) + write(0x90 | ch << 5 | ch * 3)
                            for ch in range(3))
        commands += write(0xe7) + write(0xf6) + b'\x62'
        native = analyze(vgm(commands))
        views, audit, assessments = project_views(native)
        self.assertEqual({s.ch for s in native.segments if s.audible}, {0, 1, 2, 3})
        self.assertTrue(all(rows[0].mode & 1 for rows in views.values()))
        self.assertEqual(sum(bool(rows[0].mode & 2) for rows in views.values()), 1)
        self.assertTrue(any('fourth_voice' in a['reason'] for a in assessments))
        self.assertEqual(audit[0]['noise_target_channel'], 2)

    def test_dual_native_source_and_musical_views_keep_independent_engines(self):
        commands = tone(0, 200) + write(0x90) + tone(0, 202, 1) + write(0x90, 1)
        commands += b'\x62' + write(0x93) + write(0x93, 1) + b'\x62'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'DUAL.vgm'
            path.write_bytes(vgm(commands, dual=True))
            bundle = load_dcsg_bundle(path, Path(folder) / 'out')
            self.assertEqual(set(bundle.notes['psg']), {0, 3})
            a, b = bundle.notes['psg'][0][0], bundle.notes['psg'][3][0]
            self.assertEqual((a.segment.target_period, b.segment.target_period), (200, 202))
            self.assertEqual(a.runs, [(15, 735), (13, 735)])
            self.assertEqual(len(bundle.segments['dcsg']), 8)
            self.assertEqual(bundle.analyses['psg']['source_plans'].keys(), bundle.notes['psg'].keys())
            self.assertTrue(all(Path(p).exists() for p in bundle.paths.values()))
            self.assertEqual(bundle.metadata['dcsg_target_engines']['1'], 'OPNA_SSG')

    def test_loop_replays_source_state_and_uses_first_end_as_repeat_entry(self):
        commands = tone(0, 200) + write(0x90) + b'\x62' + write(0x92) + b'\x62'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'LOOP.vgm'
            path.write_bytes(vgm(commands, loop=7))
            bundle = load_dcsg_bundle(path, Path(folder) / 'out')
            self.assertEqual(bundle.first_end_sample, 1470)
            self.assertEqual(bundle.loop_sample, 1470)
            self.assertEqual(bundle.end_sample, 2205)
            self.assertTrue(any(w.loop_iteration for w in bundle.analyses['psg']['source_analysis'].writes))

    def test_unknown_or_dc_tone_never_fabricates_pitch_and_t6w28_rejected(self):
        result = analyze(vgm(tone(0, 0) + write(0x90) + b'\x62', flags=4))
        views, _, assessments = project_views(result)
        self.assertFalse(views[0][0].mode)
        self.assertTrue(any('not_pitched' in a['reason'] for a in assessments))
        raw = bytearray(vgm(b'\x62'))
        raw[15] |= 128
        with self.assertRaisesRegex(ValueError, 'T6W28'):
            project_views(analyze(bytes(raw)))


if __name__ == '__main__':
    unittest.main()
