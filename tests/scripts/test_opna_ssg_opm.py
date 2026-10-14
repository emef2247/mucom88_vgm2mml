"""FM-first allocation, unchanged PSG synthesis and exact mixed source timing."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_opm import convert, project as project_fm
from opna_ssg import build_segments
from opna_ssg_opm import allocate, merge, project
from psg_opm_fm import FmVoice, tone_tl
from psg_scc_opm import opm_pitch
from vgm_reader import parse_vgm
from test_opna_opm import vgm
from pcm_mdx import default_generator


def body(fm_channels=(), *, mixer=0x38):
    data = bytearray.fromhex('56 29 80')
    for ch in fm_channels:
        port, local = divmod(ch, 3)
        data.extend((0x56 + port, 0xb4 + local, 0xc0))
        data.extend((0x56, 0x28, 0xf0 | local | (4 if port else 0)))
    data.extend((0x56, 7, mixer))
    for ch in range(3):
        data.extend((0x56, ch * 2, 250, 0x56, ch * 2 + 1, 0,
                     0x56, 8 + ch, 15))
    return bytes(data) + bytes.fromhex('61 64 00')


class OpnaSsgOpmTests(unittest.TestCase):
    def analyze(self, data):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.vgm'
            path.write_bytes(vgm(data))
            metadata = {}
            parse_vgm(str(path), str(Path(temporary) / 'passes'),
                opna_metadata=metadata, dump_opna_segments=True)
            return metadata['analysis'], build_segments(metadata['csv_path'],
                end_vgmticks=metadata['source_end_vgmticks'])

    def test_five_fm_and_three_ssg_fit_without_reassignment(self):
        fm, ssg = self.analyze(body((0, 1, 2, 3, 4)))
        fm_map, ssg_map, rows = allocate(fm, ssg)
        self.assertEqual(fm_map, {0: 0, 1: 1, 2: 2, 3: 4, 4: 5})
        self.assertEqual(ssg_map, {0: 3, 1: 6, 2: 7})
        self.assertEqual(sum(r['status'] == 'assigned' for r in rows), 8)
        self.assertFalse(set(fm_map.values()) & set(ssg_map.values()))

    def test_six_fm_drop_third_ssg_before_unsupported_mode_validation(self):
        # SSG C uses noise. It is preserved as source IR and dropped for
        # capacity; it cannot reject the two retained tone parts.
        fm, ssg = self.analyze(body(range(6), mixer=0x1c))
        fm_map, ssg_map, rows = allocate(fm, ssg)
        self.assertEqual(len(fm_map), 6)
        self.assertEqual(ssg_map, {0: 3, 1: 7})
        self.assertEqual([r['source_ch'] for r in rows if r['status'] == 'dropped'], [2])
        projected, _, _ = project(ssg, ssg_map)
        self.assertEqual({r['target_ch'] for r in projected}, {3, 7})
        with self.assertRaisesRegex(ValueError, 'noise/hardware EG'):
            project(ssg, {2: 7})

    def test_unused_fm_setup_cannot_overwrite_ssg_channel(self):
        fm, ssg = self.analyze(bytes.fromhex('56 31 71 56 28 01 56 29 80') + body((0,)))
        fm_map, ssg_map, _ = allocate(fm, ssg)
        self.assertEqual(ssg_map, {0: 1, 1: 2, 2: 3})
        fm_rows, _ = project_fm(fm, clock_hz=7987200, channel_map=fm_map)
        self.assertTrue(all(r['target_ch'] in (None, 0) for r in fm_rows))
        self.assertFalse(any(r['target_register'] == 0x41 for r in fm_rows))

    def test_ssg_reuses_psg_fm_voice_level_and_prescaler_pitch_without_key_restart(self):
        _, ssg = self.analyze(body(()) + bytes.fromhex('56 2e 00 61 64 00'))
        rows, baseline, performance = project(ssg, {0: 5})
        voice = FmVoice()
        self.assertIn((0x25, 0xc0 | voice.feedback << 3 | voice.algorithm),
                      [(r['target_register'], r['target_data']) for r in rows])
        self.assertIn((0x75, tone_tl(15, 1)),
                      [(r['target_register'], r['target_data']) for r in rows])
        for divisor in (4, 2):
            kc, kf, _ = opm_pitch(7987200 / divisor / (16 * 250))
            self.assertIn((0x2d, kc), [(r['target_register'], r['target_data']) for r in rows])
            self.assertIn((0x35, kf), [(r['target_register'], r['target_data']) for r in rows])
        self.assertEqual(sum(w.inferred_key and bool(w.data & 0x78) for w in performance.writes), 1)
        self.assertEqual(baseline.settings['psg_model'], 'fm')

    def test_exact_sample_and_event_order_survive_quantized_tick_collisions(self):
        rows = [dict(write_id=i, source_vgmticks=t, source_event_id=e,
                     synthetic_phase=None) for i, t, e in ((0, 2, 4), (1, 1, 3), (2, 2, 2))]
        merged = merge(rows[:1], rows[1:])
        self.assertEqual([(r['source_vgmticks'], r['source_event_id']) for r in merged],
                         [(1, 3), (2, 2), (2, 4)])

    def test_loop_keeps_ssg_terminal_gate_and_source_provenance(self):
        _, ssg = self.analyze(body(()))
        finite, _, _ = project(ssg, {0: 3})
        looped, _, _ = project(ssg, {0: 3}, loop_start=50)
        self.assertTrue(any(r['synthetic_phase'] == 'terminal' for r in finite))
        self.assertFalse(any(r['synthetic_phase'] == 'terminal' for r in looped))
        source_rows = [r for r in looped if r['source_event_id'] is not None]
        self.assertTrue(source_rows)
        self.assertTrue(all(r['source_chip'] == 'opna_ssg' for r in source_rows))

    def test_combined_source_reaches_existing_mdx_renderer_and_retains_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'mixed.vgm'
            source.write_bytes(vgm(body((0,))))
            mml, settings = convert(source, root / 'out', dump_passes=True,
                                    song_loop=False, normalize_lengths=False)
            self.assertTrue(mml.is_file())
            self.assertEqual(settings['channel_allocation']['assigned'], 4)
            self.assertEqual(settings['ssg_projection']['inferred_attacks'], 3)
            self.assertTrue((root / 'out/mixed.opna_ssg.segments.csv').is_file())
            self.assertTrue((root / 'out/projected_opm/mixed.source_map.csv').is_file())
            self.assertIn('D', mml.read_text(encoding='utf-8'))

    def test_ssg_only_uses_first_three_channels_without_fm_reservations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'ssg.vgm'
            source.write_bytes(vgm(body(())))
            mml, settings = convert(source, root / 'out', dump_passes=True,
                                    song_loop=False, normalize_lengths=False)
            assigned = [r for r in settings['channel_allocation']['parts'] if r['status'] == 'assigned']
            self.assertEqual([(r['source_domain'], r['target_ch']) for r in assigned],
                             [('ssg', 0), ('ssg', 1), ('ssg', 2)])
            self.assertTrue(mml.is_file())

    @unittest.skipUnless(default_generator().is_file(), 'Build external MDX helper for integration check')
    def test_fm_ssg_pcm_share_existing_binary_generation(self):
        from test_opna_adpcm import source as pcm_source
        raw, _ = pcm_source()
        mixed = vgm(raw[0x100:-1] + body((0,)) + bytes.fromhex('61 3a 11'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'MIXED.vgm'
            source.write_bytes(mixed)
            out = root / 'out'
            _, settings = convert(source, out, dump_passes=True, song_loop=False,
                pcm_policy='best-effort', normalize_lengths=False)
            self.assertEqual(settings['channel_allocation']['assigned'], 4)
            self.assertEqual(settings['pcm_ir']['pdx_distinct_samples'], 1)
            self.assertGreater((out / 'MIXED.mdx').stat().st_size, 0)
            self.assertGreater((out / 'MIXED.pdx').stat().st_size, 0)
            target = json.loads((out / 'MIXED.msm6258_ir.analysis.json').read_text())
            encoded = (out / target['samples'][0]['file']).read_bytes()
            pdx = (out / 'MIXED.pdx').read_bytes()
            start = int.from_bytes(pdx[:4], 'big')
            length = int.from_bytes(pdx[4:8], 'big')
            self.assertEqual(pdx[start:start + length], encoded)


if __name__ == '__main__':
    unittest.main()
