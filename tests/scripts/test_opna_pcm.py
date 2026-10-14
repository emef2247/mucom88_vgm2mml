"""OPNA target adapter contracts; external helper/runtime checks are separate."""
from dataclasses import replace
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))

from opna_adpcm import analyze, project_msm6258
from opna_pcm import adapt
from pcm_assessment import ProjectionError
from pcm_mdx import PCM_PAN, project
from test_opna_adpcm import source
from test_opna_opm import vgm


class OpnaPcmTests(unittest.TestCase):
    def bridge(self, **parameters):
        raw, _ = source(**parameters)
        native = analyze(raw)
        target = project_msm6258(native)
        return raw, native, target, adapt(native, target, source_raw=raw)

    def test_native_bytes_and_observed_supply_remain_separate(self):
        raw, native, target, bridge = self.bridge()
        self.assertEqual(bridge.source_raw, bytes(raw))
        self.assertEqual(bridge.samples[0].encoded_bytes, target.samples[0].encoded_bytes)
        self.assertNotEqual(bridge.samples[0].encoded_bytes, native.samples[0].encoded_bytes)
        self.assertFalse(bridge.controls or bridge.transfers or bridge.raw_commands)
        self.assertEqual(bridge.playbacks[0].supplied_bytes, 0)
        self.assertFalse(bridge.playbacks[0].reset_observed)
        self.assertEqual(bridge.target_mapping[0]['encoded_target_bytes'], len(bridge.samples[0].encoded_bytes))
        with self.assertRaisesRegex(ValueError, 'source bytes differ'):
            adapt(native, target, source_raw=bytes(raw) + b'\x00')

    def test_identical_payloads_share_slot_but_playbacks_keep_identity(self):
        tail = bytes.fromhex('61 64 00 57 00 01 57 00 a0 61 64 00 57 00 01')
        _, native, target, bridge = self.bridge(tail=tail)
        self.assertEqual(len(native.segments), 2)
        self.assertEqual(len(target.samples), 2)
        self.assertEqual(len(bridge.samples), 1)
        self.assertEqual([p.sample_id for p in bridge.playbacks], [0, 0])
        self.assertEqual([p.start_vgmticks for p in bridge.playbacks], [0, 100])
        self.assertNotEqual(bridge.target_mapping[0]['source_event_ids'],
                            bridge.target_mapping[1]['source_event_ids'])

    def test_pan_enable_bits_map_once_to_existing_mdx_pan(self):
        # OPNA: silent/right/left/both -> MDX: silent/right/left/both.
        for pan, mdx in enumerate((0, 2, 1, 3)):
            with self.subTest(pan=pan):
                _, _, _, bridge = self.bridge(memory_mode=(pan << 6) | 1)
                self.assertEqual(PCM_PAN[bridge.playbacks[0].pan], mdx)

    def test_strict_blocks_transcoding_best_effort_retains_target_bytes(self):
        _, _, _, bridge = self.bridge()
        with self.assertRaisesRegex(ProjectionError, 'Strict PCM projection blocks known loss'):
            project(bridge, stem='strict', tick_mapper=lambda t: t)
        plan = project(bridge, stem='best', policy='best-effort', tick_mapper=lambda t: t)
        self.assertEqual(len(plan.bindings), 1)
        self.assertEqual(plan.units[-1].end_tick, bridge.source_end_vgmticks)
        self.assertIn('transcoded MSM6258', plan.summary()['pcm_byte_preservation_scope'])
        self.assertIn('no observed MSM6258 byte supply', plan.summary()['pcm_consumption_scope'])
        self.assertTrue(any(i['code'] == 'msm6258_requantization' for i in plan.assessment.items))

    def test_ineligible_continuation_cannot_be_omitted_even_in_best_effort(self):
        _, _, _, bridge = self.bridge(tail=bytes.fromhex('61 0a 00 57 0b 80'))
        self.assertFalse(bridge.samples)
        for policy in ('strict', 'best-effort'):
            with self.subTest(policy=policy), self.assertRaises(ProjectionError):
                project(bridge, stem='blocked', policy=policy, tick_mapper=lambda t: t,
                        omit_playback_ids=tuple(p.playback_id for p in bridge.playbacks))

    def test_odd_padding_and_source_loop_continuation_are_diagnosed(self):
        _, _, target, bridge = self.bridge(tail=bytes.fromhex('61 01 00 57 00 01'))
        self.assertEqual(target.samples[0].padding_nibbles, 1)
        self.assertTrue(any(i['code'] == 'pcm_padding_nibble' for i in bridge.target_evidence))
        _, _, _, held = self.bridge()
        held = replace(held, source_loop_vgmticks=50)
        with self.assertRaisesRegex(ProjectionError, 'continues across the song-loop boundary'):
            project(held, stem='loop', policy='best-effort', song_loop_start=50,
                    tick_mapper=lambda t: t)

    def test_replayed_pcm_retains_original_command_provenance(self):
        raw, _ = source(tail=bytes.fromhex('61 64 00 57 00 01'))
        # The data block occupies 47 bytes; repeat setup/playback after it.
        raw = vgm(raw[0x100:-1], loop_offset=47)
        native = analyze(raw, loop_count=2)
        target = project_msm6258(native)
        bridge = adapt(native, target, source_raw=raw, loop_start=200)
        self.assertEqual(bridge.loop_count, 2)
        self.assertEqual(bridge.source_end_vgmticks, 400)
        self.assertEqual(len(bridge.samples), 1)
        self.assertEqual([p.start_vgmticks for p in bridge.playbacks], [0, 200])
        self.assertTrue(any(e.loop_iteration == 1 for e in bridge.command_provenance))
        self.assertEqual(bridge.target_mapping[0]['original_source_event_ids'],
                         bridge.target_mapping[1]['original_source_event_ids'])

    def test_facade_outputs_to_root_and_separates_projected_source_passes(self):
        from opna_opm import convert
        raw, _ = source(tail=bytes.fromhex('61 64 00 57 00 01'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            src = root / 'case.vgm'
            src.write_bytes(raw)
            out = root / 'output'
            result = out / 'case.mdx.mml'
            with patch('opm_conversion.convert', return_value=(result, None, None)) as downstream:
                mml, settings = convert(src, out, dump_passes=True,
                    song_loop=False, pcm_policy='best-effort')
            args, options = downstream.call_args
            self.assertEqual(args[1], out)
            self.assertEqual(args[0].parent, out / 'projected_opm')
            self.assertEqual(options['source_passes_outdir'], out / 'projected_opm')
            self.assertTrue(options['replayed_song_loop'])
            self.assertEqual(options['pcm_analysis_override'].source_raw, bytes(raw))
            self.assertEqual(mml, result)
            self.assertEqual(settings['pcm_ir']['pdx_distinct_samples'], 1)
            report = json.loads((out / 'case.opna_pcm_bridge.json').read_text())
            self.assertEqual(report['target_sample_count'], 1)


if __name__ == '__main__':
    unittest.main()
