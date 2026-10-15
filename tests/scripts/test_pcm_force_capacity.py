"""Authored PCM capacity cases; no private fixtures or external compiler."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from generate_pcm_fixtures import playback, vgm, wait
from msm6258 import analyze
from pcm_assessment import ProjectionError
from pcm_force_capacity import select_capacity
from pcm_mdx import project
from opm_conversion import convert


class ForceCapacityTests(unittest.TestCase):
    def source(self, lengths):
        body, _ = playback(bytes(range(32)))
        base = analyze(vgm(body))
        samples = tuple(replace(base.samples[0], sample_id=i, encoded_bytes=bytes([i % 256]) * length)
                        for i, length in enumerate(lengths))
        plays = tuple(replace(base.playbacks[0], playback_id=i, sample_id=i,
                              start_vgmticks=i * 10000, end_vgmticks=(i + 1) * 10000,
                              control_event_ids=()) for i in range(len(samples)))
        return replace(base, samples=samples, playbacks=plays,
                       source_end_vgmticks=(len(samples) + 1) * 10000, controls=())

    def test_97th_sample_is_omitted_but_bound_sample_can_play_again(self):
        source = self.source([32] * 97)
        repeat = replace(source.playbacks[0], playback_id=97, start_vgmticks=970000,
                         end_vgmticks=980000)
        source = replace(source, playbacks=(*source.playbacks, repeat), source_end_vgmticks=990000)
        view = select_capacity(source)
        self.assertEqual(len(source.samples), 97)
        self.assertEqual(len(view.analysis.samples), 96)
        self.assertEqual(view.omitted_playback_ids, {96})
        self.assertEqual(view.analysis.playbacks[-1], repeat)
        self.assertEqual(view.rows[96]['cap_kind'], 'slot_capacity')
        self.assertEqual((view.rows[96]['actual'], view.rows[96]['limit']), (97, 96))
        plan = project(source, stem='slots', force=True)
        self.assertEqual(len(plan.bindings), 96)
        self.assertEqual(plan.units[-1].source_end_vgmticks, source.source_end_vgmticks)
        self.assertEqual(plan.typed_commands[-1]['kind'], 'end')
        self.assertTrue(any(u.kind == 'pcm_rest' and u.source_start_vgmticks == 960000
                            and u.source_end_vgmticks == 970000 for u in plan.units))

    def test_oversize_does_not_consume_a_slot_and_bytes_remain_whole(self):
        source = self.source([65536, 65535, 32])
        original = source
        view = select_capacity(source)
        self.assertEqual([s.sample_id for s in view.analysis.samples], [1, 2])
        self.assertEqual(source, original)
        self.assertEqual(len(source.samples[0].encoded_bytes), 65536)
        self.assertEqual(view.rows[0]['cap_kind'], 'verified_sample_byte_scope')
        self.assertEqual(view.analysis.source_end_vgmticks, source.source_end_vgmticks)
        self.assertEqual([b.sample_id for b in project(source, stem='large', force=True).bindings], [1, 2])
        with self.assertRaises(ProjectionError):
            project(source, stem='strict')

    def test_all_omitted_cross_loop_retains_loop_rest_end_and_reports(self):
        source = self.source([65536])
        source = replace(source, source_loop_vgmticks=5000)
        plan = project(source, stem='empty', force=True, song_loop_start=5000)
        self.assertFalse(plan.bindings)
        self.assertEqual([u.kind for u in plan.units], ['pcm_rest', 'song_loop', 'pcm_rest'])
        self.assertEqual(plan.units[-1].source_end_vgmticks, source.source_end_vgmticks)
        self.assertTrue(plan.summary()['pcm_force_capacity']['all_pcm_omitted'])
        with tempfile.TemporaryDirectory() as temp:
            plan.dump(temp, 'empty')
            report = json.loads((Path(temp) / 'empty.pcm.force_capacity.json').read_text())
            self.assertEqual(report['summary']['omitted_playback_ids'], [0])
            self.assertEqual(report['rows'][0]['sample_id'], 0)
            self.assertEqual(report['rows'][0]['source_end_vgmticks'], 10000)
            self.assertTrue((Path(temp) / 'empty.pcm.force_capacity.csv').is_file())

    def test_force_does_not_hide_missing_bytes_codec_rate_or_source_order(self):
        source = self.source([65536])
        cases = [replace(source, samples=(replace(source.samples[0], encoded_bytes=b''),)),
                 replace(source, samples=(replace(source.samples[0], codec='unknown'),)),
                 replace(source, playbacks=(replace(source.playbacks[0], sample_id=99),)),
                 replace(source, playbacks=(replace(source.playbacks[0], rate_num=1),)),
                 replace(source, playbacks=(replace(source.playbacks[0], independently_playable=False),))]
        overlap = self.source([32, 65536])
        cases.append(replace(overlap, playbacks=(overlap.playbacks[0],
                             replace(overlap.playbacks[1], start_vgmticks=5000))))
        for candidate in cases:
            with self.subTest(candidate=candidate.playbacks[0].sample_id):
                with self.assertRaises(ProjectionError):
                    project(candidate, stem='invalid', force=True)

    def test_force_keeps_unrelated_strict_loss_blocking(self):
        source = replace(self.source([65536]), source_loop_vgmticks=5000)
        with self.assertRaises(ProjectionError) as caught:
            project(source, stem='loop', force=True)
        self.assertIn('song_loop_not_emitted', str(caught.exception))
        plan = project(source, stem='loop', force=True, policy='best-effort')
        self.assertEqual(plan.assessment.policy, 'best-effort')

    def test_binding_order_uses_first_playback_not_sample_table(self):
        source = self.source([32, 32, 32])
        source = replace(source, samples=tuple(reversed(source.samples)))
        self.assertEqual([s.sample_id for s in select_capacity(source).analysis.samples], [0, 1, 2])

    def test_normalization_omitted_samples_do_not_exhaust_capacity(self):
        source = self.source([32] * 97)
        source = replace(source, playbacks=tuple(
            replace(p, end_vgmticks=p.start_vgmticks + 352) if p.playback_id < 96 else p
            for p in source.playbacks))
        plan = project(source, stem='normalized', force=True, omit_playback_ids=range(96))
        self.assertEqual([b.sample_id for b in plan.bindings], [96])
        self.assertEqual([r['playback_id'] for r in plan.rows], [96])
        self.assertEqual([p['playback_id'] for p in plan.omitted_playbacks], list(range(96)))
        self.assertFalse(plan.force_capacity.omitted_playback_ids)
        self.assertEqual(plan.force_capacity.summary()['normalization_omitted_playback_ids'], list(range(96)))
        self.assertEqual(plan.force_capacity.rows[0]['action'], 'omit_normalization')
        self.assertEqual(plan.force_capacity.rows[-1]['action'], 'retain')
        self.assertEqual(len(source.samples), 97)
        self.assertEqual(plan.units[-1].source_end_vgmticks, source.source_end_vgmticks)

    def test_all_omitted_conversion_generates_fm_mml_without_stale_pdx_success(self):
        source = self.source([65536])
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / 'empty.vgm'
            path.write_bytes(vgm(wait(source.source_end_vgmticks)))
            output = folder / 'out'
            output.mkdir()
            stale = output / 'empty.pdx'
            stale.write_bytes(b'unowned reference')
            mml, _, _ = convert(path, output, pcm_analysis_override=source, force=True,
                                normalize_lengths=False, song_loop=False, dump_passes=True)
            self.assertTrue(mml.is_file())
            self.assertNotIn('#pcmfile', mml.read_text())
            self.assertEqual(stale.read_bytes(), b'unowned reference')
            report = json.loads((output / 'empty.pcm.assessment.json').read_text())
            self.assertEqual(report['artifacts']['pdx']['status'], 'preserved_existing')
            self.assertFalse(report['artifacts']['pdx']['required'])
            self.assertFalse((output / 'empty.mdx').exists())
            timing = json.loads((output / 'empty.pcm.timing.json').read_text())
            self.assertTrue(timing['pcm_force_capacity']['all_pcm_omitted'])


if __name__ == '__main__':
    unittest.main()
