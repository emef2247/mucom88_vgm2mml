"""Independent PCM note boundaries share the FM song-loop clock."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'tests/scripts')]
from generate_pcm_fixtures import playback, vgm
from msm6258 import analyze
from pcm_mdx import project
from pcm_assessment import ProjectionError


class PcmSongLoopTests(unittest.TestCase):
    def source(self):
        body, _ = playback(bytes(range(32)))
        source = analyze(vgm(body))
        first = replace(source.playbacks[0], start_vgmticks=100, end_vgmticks=200,
                        termination='stop_control')
        second = replace(first, playback_id=1, start_vgmticks=600, end_vgmticks=700)
        return replace(source, playbacks=(first, second), source_end_vgmticks=1000,
                       source_loop_vgmticks=500)

    def test_shared_marker_splits_rest_without_restarting_a_note(self):
        source = self.source()
        original = source
        plan = project(source, stem='loop', song_loop_start=500,
                       tick_mapper=lambda value: value, policy='best-effort')
        self.assertEqual(source, original)
        markers = [u for u in plan.units if u.kind == 'song_loop']
        self.assertEqual([(u.start_tick, u.end_tick, u.command) for u in markers], [(500, 500, 'L')])
        self.assertEqual([u.source_pcm_playback_ids for u in plan.units if u.kind == 'pcm_note'], [(0,), (1,)])
        self.assertFalse(any(item['code'] == 'song_loop_not_emitted' for item in plan.assessment.items))
        self.assertEqual([c['start_tick'] for c in plan.typed_commands if c['kind'] == 'song_loop'], [500])
        self.assertFalse(any(u.start_tick < 500 < u.end_tick for u in plan.units))

    def test_continuation_is_explicitly_rejected_in_both_policies(self):
        source = self.source()
        for note in (replace(source.playbacks[0], start_vgmticks=400, end_vgmticks=600),
                     replace(source.playbacks[0], end_vgmticks=1000, termination='source_end')):
            for policy in ('strict', 'best-effort'):
                with self.subTest(policy=policy, termination=note.termination):
                    with self.assertRaises(ProjectionError) as error:
                        project(replace(source, playbacks=(note,)), stem='loop',
                                song_loop_start=500, policy=policy)
                    self.assertIn('song_loop_pcm_continuation',
                                  [item['code'] for item in error.exception.assessment.items])


if __name__ == '__main__':
    unittest.main()
