"""Target song markers retain ordered source controls and held gates."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py')]
from mdx_compaction import compact
from opm_mdx import project_segments, render
from opm_mdx_music import build_music, render_music_tracks, MusicUnit
from source_loop_plan import expanded_tokens
from test_opm_mdx import analyze, wait, write
from test_opm_reader import vgm


class SongLoopRenderingTests(unittest.TestCase):
    def build(self, commands, boundary, event_id=None):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / 'song.vgm'
            path.write_bytes(vgm(commands))
            analysis = analyze(path, folder / 'trace')
            projection = project_segments(analysis.segments,
                                          end_vgmticks=analysis.source_end_vgmticks)
            music = build_music(projection, analysis.segments,
                                song_loop_start=boundary,
                                song_loop_event_id=event_id)
            return projection, music

    def test_held_gate_crossing_marker_retains_raw_edges_without_new_attack(self):
        projection, music = self.build(write(8, 120) + wait(1000) +
                                        write(0x28, 48) + wait(1000) + write(8, 0), 1000)
        units = music.units['A']
        self.assertEqual([u.kind for u in units].count('song_loop'), 1)
        self.assertEqual([u.command for u in units if u.kind == 'song_loop'], ['L'])
        self.assertTrue(any(u.fallback_reason == 'gate_crosses_song_loop' for u in units))
        tokens = expanded_tokens(' '.join(u.command for u in units))
        self.assertEqual([t for t in tokens if t.startswith('y8,')], ['y8,120', 'y8,0'])
        self.assertFalse(any(u.start_tick < projection.mdx_tick(1000) < u.end_tick for u in units))

    def test_same_sample_command_order_places_pre_boundary_write_before_marker(self):
        commands = write(0x28, 40) + wait(1000) + write(0x28, 41) + write(0x28, 42) + wait(1000)
        projection, unused = self.build(commands, 1000)
        boundary_id = projection.writes[-1].source_event_id
        projection, music = self.build(commands, 1000, boundary_id)
        tokens = expanded_tokens(' '.join(u.command for u in music.units['A']))
        self.assertLess(tokens.index('y40,41'), tokens.index('L'))
        self.assertLess(tokens.index('L'), tokens.index('y40,42'))
        raw = render(projection, song_loop_start=1000, song_loop_event_id=boundary_id)
        self.assertLess(raw.index('A y40,41'), raw.index('A L'))
        self.assertLess(raw.index('A L'), raw.index('A y40,42'))

    def test_track_with_only_intro_controls_has_shared_loop_rest(self):
        projection, music = self.build(write(0x28, 40) + write(0x29, 40) + wait(2000), 1000)
        for units in music.units.values():
            marker = next(u for u in units if u.kind == 'song_loop')
            self.assertEqual(marker.start_tick, projection.mdx_tick(1000))
            self.assertEqual(units[-1].end_tick, projection.end_mdx_tick)

    def test_compaction_retains_absolute_entry_settings(self):
        result, changes = compact('o3 @v100 r%10 L o3 @v100 r%10 o4 r%10', 'A')
        self.assertIn('L o3 @v100', result)
        self.assertIn('> r%10', result)

    def test_finite_compression_keeps_song_marker_outside_repeat_bodies(self):
        def unit(kind, command, tick):
            return MusicUnit('A', tick, tick + (kind != 'song_loop'), tick, tick,
                             kind, command, (), (), (), unlooped_command=command)
        units = tuple(unit('rest', 'r%1', i) for i in range(4)) + (unit('song_loop', 'L', 4),) + tuple(
            unit('rest', 'r%1', i) for i in range(4, 8))
        music = render_music_tracks({'A': units}, (), title='song loop')
        tokens = music.text.split()
        depth = 0
        for token in tokens:
            depth += token.count('[')
            if token == 'L': self.assertEqual(depth, 0)
            depth -= token.count(']')
        self.assertEqual(tokens.count('L'), 1)

    def test_nonpositive_loop_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'positive target duration'):
            self.build(write(0x28, 40) + wait(1000), 1000)


if __name__ == '__main__':
    unittest.main()
