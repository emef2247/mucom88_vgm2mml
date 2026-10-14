"""Inherited explicit MDX/MGS frontend routing and option boundaries."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from test_opm_reader import vgm

ROOT = Path(__file__).resolve().parents[2]


class CliRoutes(unittest.TestCase):
    def run_cli(self, *args):
        # These inherited tests exercise MDX compatibility, not default MUC.
        if '--target' not in args:
            args=(*args,'--target','mdx')
        return subprocess.run([sys.executable, str(ROOT / 'vgm2mml.py'), *map(str, args)],
                              capture_output=True, text=True, timeout=30)

    def test_native_pitch_policy_clamps_with_loss_report_or_rejects_explicit_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'low.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 28 00 54 08 78 62 54 08 00 62'), 3579580))
            run = self.run_cli(source, '--outdir', root / 'clamp', '--opm-pitch-policy', 'clamp')
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((root / 'clamp/low.mdx.normalization.json').read_text())
            pitch = report['selected']['pitch_projection']
            self.assertEqual(pitch['policy'], 'clamp')
            self.assertTrue(pitch['known_pitch_loss'])
            run = self.run_cli(source, '--outdir', root / 'error', '--opm-pitch-policy', 'error')
            self.assertEqual(run.returncode, 2)
            self.assertIn('outside the 4 MHz target KC/KF range', run.stderr)

    def test_explicit_mdx_plain_and_dump_passes_retain_same_music(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'test.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 28 40 54 08 78 62 54 08 00 62')))
            for name, options in [('mdx', ['--target', 'mdx']), ('dump', ['--target', 'mdx', '--dump-passes'])]:
                run = self.run_cli(source, '--outdir', root / name, *options)
                self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual((root / 'mdx/test.mdx.mml').read_bytes(),
                             (root / 'dump/test.mdx.mml').read_bytes())

    def test_target_specific_options_are_rejected(self):
        for options, message in [
            (['--target', 'mgs', '--notation', 'registers'], 'require --target mdx'),
            (['--track-layout', 'conductor'], 'require --notation registers'),
        ]:
            with self.subTest(options=options):
                run = self.run_cli('unused.vgm', *options)
                self.assertEqual(run.returncode, 2)
                self.assertIn(message, run.stderr)

    def test_waits_and_unused_declarations_do_not_select_a_chip_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'empty.vgm'
            source.write_bytes(vgm(bytes.fromhex('62'), clock=0))
            run = self.run_cli(source, '--outdir', root / 'output')
            self.assertEqual(run.returncode, 2)
            self.assertIn('no supported chip commands', run.stderr)
            self.assertFalse((root / 'output/empty.mdx.mml').exists())

    def test_psg_explicit_mdx_and_deprecated_alias_use_the_same_route(self):
        fixture = ROOT / 'tests/fixtures/public/psg/volume_sweep/volume_sweep.vgm'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, flags in [('mdx', ['--target', 'mdx']), ('alias', ['--target', 'opm'])]:
                run = self.run_cli(fixture, '--outdir', root / name, *flags)
                self.assertEqual(run.returncode, 0, run.stderr)
                if name=='alias':
                    self.assertIn('deprecated', run.stderr)
                report = json.loads((root / name / 'volume_sweep.conversion.json').read_text())
                self.assertEqual(report['target_format'], 'mdx')
                self.assertEqual(report['chip_projection'], 'psg-scc-to-opm')
                self.assertTrue(report['normalization_enabled'])
                self.assertEqual(report['projection_settings']['psg_gain'], 1)
            self.assertEqual((root / 'mdx/volume_sweep.mdx.mml').read_bytes(),
                             (root / 'alias/volume_sweep.mdx.mml').read_bytes())

    def test_unused_chip_declarations_do_not_change_the_native_route(self):
        body = bytes.fromhex('54 28 40 54 08 78 62 54 08 00 62')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, extra in [('plain', False), ('unused', True)]:
                source = root / (name + '.vgm')
                raw = bytearray(vgm(body))
                if extra:
                    header = bytearray(0x100)
                    header[:0xa0] = raw[:0xa0]
                    struct.pack_into('<I', header, 0x34, 0xcc)
                    for offset in (0x10, 0x74, 0x90, 0x9c):
                        struct.pack_into('<I', header, offset, 0xc0000001)
                    header[0x78] = 255
                    header[0x94] = 255
                    raw = header + raw[0xa0:]
                    struct.pack_into('<I', raw, 4, len(raw) - 4)
                source.write_bytes(raw)
                run = self.run_cli(source, '--outdir', root / name)
                self.assertEqual(run.returncode, 0, run.stderr)
                report = json.loads((root / name / (name + '.conversion.json')).read_text())
                self.assertEqual(report['source']['used_chips'], ['opm'])
                self.assertEqual(report['chip_projection'], 'native-opm-pcm')
                if extra:
                    self.assertEqual(report['source']['unused_declarations'], ['opll', 'pcm', 'psg', 'scc'])
            # Only the filename-derived title differs.
            self.assertEqual((root / 'plain/plain.mdx.mml').read_text().replace('plain', 'unused'),
                             (root / 'unused/unused.mdx.mml').read_text())

    def test_explicitly_muted_psg_initialization_is_retained_but_not_projected(self):
        from conversion_config import inspect_source, select_mdx_route
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'muted.vgm'
            source.write_bytes(vgm(bytes.fromhex(
                'a0 08 00 a0 09 00 a0 0a 00 a0 07 b8 '
                '54 08 78 62 a0 06 00 a0 0b ff a0 0c 00')))
            usage = inspect_source(source)
            self.assertEqual(usage['used_chips'], ['opm', 'psg'])
            self.assertEqual(usage['compatibility_initialization']['psg']['command_count'], 7)
            self.assertEqual(select_mdx_route(usage), 'native-opm-pcm')
            for tail in ('a0 08 01 62 a0 08 00', 'a0 08 10', 'a0 0e 00'):
                source.write_bytes(vgm(bytes.fromhex(
                    'a0 08 00 a0 09 00 a0 0a 00 54 08 78 ' + tail)))
                usage = inspect_source(source)
                self.assertNotIn('psg', usage['compatibility_initialization'])
                with self.assertRaises(ValueError):
                    select_mdx_route(usage)
            # Registers outside ordinary AY tone/noise/envelope initialization
            # must not gain the muted-source exemption.
            source.write_bytes(vgm(bytes.fromhex(
                'a0 08 00 62 a0 89 00 a0 0a 00 54 08 78')))
            self.assertNotIn('psg', inspect_source(source)['compatibility_initialization'])

    def test_from_fm_routes_retain_silent_compatibility_commands(self):
        from conversion_config import inspect_source, select_mdx_route
        fixtures = sorted((ROOT / 'tests/fixtures/public/opm/from_fm').glob('*/*.vgm'))
        self.assertEqual(len(fixtures), 16)
        for source in fixtures:
            with self.subTest(source=source.stem):
                usage = inspect_source(source)
                self.assertIn('psg', usage['compatibility_initialization'])
                self.assertIn('scc', usage['compatibility_initialization'])
                self.assertEqual(select_mdx_route(usage), 'native-opm-pcm')

    def test_muted_source_only_psg_scc_keep_their_projection_route(self):
        from conversion_config import inspect_source, select_mdx_route
        zero_wave = b''.join(bytes((0xd2, 0, i, 0)) for i in range(32))
        for body, clock_offset, chip in [
            (bytes.fromhex('a0 08 00 a0 09 00 a0 0a 00 62'), 0x74, 'psg'),
            (zero_wave + bytes.fromhex('d2 02 00 0f d2 03 00 01 62'), 0x9c, 'scc'),
        ]:
            with self.subTest(chip=chip), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / 'silent.vgm'
                raw = vgm(body, clock=0)
                struct.pack_into('<I', raw, clock_offset, 1789773)
                source.write_bytes(raw)
                usage = inspect_source(source)
                self.assertNotIn(chip, usage['compatibility_initialization'])
                self.assertEqual(select_mdx_route(usage), 'psg-scc-to-opm')

    def test_scc_zero_waveform_exemption_requires_positive_interval_silence(self):
        from conversion_config import inspect_source, select_mdx_route
        zero_wave = b''.join(bytes((0xd2, 0, i, 0)) for i in range(32))
        setup = zero_wave + bytes.fromhex('d2 02 00 0f d2 03 00 01 54 08 78')
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'mixed.vgm'
            for tail, silent in [
                ('62', True),
                ('d2 00 00 ff 62', False),
                ('d2 00 00 ff d2 00 00 00 62', True),
                ('62 d2 00 00 ff', True),
            ]:
                with self.subTest(tail=tail):
                    source.write_bytes(vgm(setup + bytes.fromhex(tail)))
                    usage = inspect_source(source)
                    self.assertEqual('scc' in usage['compatibility_initialization'], silent)
                    if silent:
                        self.assertEqual(select_mdx_route(usage), 'native-opm-pcm')
                    else:
                        with self.assertRaises(ValueError):
                            select_mdx_route(usage)
            # Unknown waveform RAM and constant nonzero DC are not silence.
            for wave in (b'', b''.join(bytes((0xd2, 0, i, 1)) for i in range(32))):
                source.write_bytes(vgm(wave + bytes.fromhex('d2 02 00 0f d2 03 00 01 54 08 78 62')))
                self.assertNotIn('scc', inspect_source(source)['compatibility_initialization'])
            raw = vgm(setup + bytes.fromhex('62 d2 00 00 ff'))
            struct.pack_into('<I', raw, 0x1c, 0xa0 - 0x1c)
            source.write_bytes(raw)
            self.assertNotIn('scc', inspect_source(source)['compatibility_initialization'])
            # Disabled final state can still carry nonzero waveform RAM into
            # a second loop iteration which enables the channel again.
            loop_body = bytes.fromhex(
                'd2 02 00 0f d2 03 00 01 54 08 78 62 '
                'd2 03 00 00 d2 00 00 ff')
            raw = vgm(zero_wave + loop_body)
            struct.pack_into('<I', raw, 0x9c, 1789773)
            struct.pack_into('<I', raw, 0x1c, 0xa0 + len(zero_wave) - 0x1c)
            source.write_bytes(raw)
            usage = inspect_source(source)
            self.assertNotIn('scc', usage['compatibility_initialization'])
            with self.assertRaisesRegex(ValueError, 'opm, scc'):
                select_mdx_route(usage)

    def test_supported_plus_unsupported_commands_never_publish_partial_success(self):
        for tail in (bytes.fromhex('50 90'), bytes.fromhex('a4 08 00')):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / 'mixed.vgm'
                source.write_bytes(vgm(bytes.fromhex('54 08 78') + tail + b'\x62'))
                run = self.run_cli(source, '--outdir', root / 'out')
                self.assertEqual(run.returncode, 2)
                self.assertIn('unsupported_source', run.stderr)
                self.assertIn(' at 0x', run.stderr)
                self.assertFalse((root / 'out/mixed.mdx.mml').exists())
                report = json.loads((root / 'out/mixed.conversion.json').read_text())
                self.assertEqual(report['status'], 'blocked')

    def test_unsupported_rerun_invalidates_prior_success_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'rerun.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 08 78 62 54 08 00')))
            out = root / 'out'
            self.assertEqual(self.run_cli(source, '--outdir', out).returncode, 0)
            # Include optional prior compiled artifacts and a typed PCM plan.
            for suffix in ('.mdx', '.pdx'):
                (out / ('rerun' + suffix)).write_bytes(b'previous generated artifact')
            from test_pcm_mdx import record_previous_binaries
            record_previous_binaries(out, 'rerun')
            (out / 'rerun.pcm').mkdir()
            (out / 'rerun.pcm/target.tsv').write_text('previous target plan')
            current = vgm(bytes.fromhex('54 08 78 50 90 62'))
            source.write_bytes(current)
            run = self.run_cli(source, '--outdir', out)
            self.assertEqual(run.returncode, 2)
            for suffix in ('.mdx.mml', '.mdx', '.pdx', '.mdx.normalization.json'):
                self.assertFalse((out / ('rerun' + suffix)).exists())
            self.assertFalse((out / 'rerun.pcm/target.tsv').exists())
            self.assertEqual(source.read_bytes(), current)
            report = json.loads((out / 'rerun.conversion.json').read_text())
            self.assertEqual(report['status'], 'blocked')

    def test_default_output_directory_preserves_input_vgm(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'same.vgm'
            raw = vgm(bytes.fromhex('54 08 78 62 54 08 00'))
            source.write_bytes(raw)
            run = self.run_cli(source)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(source.read_bytes(), raw)

    def test_native_conversion_and_preflight_failure_preserve_reference_mdx_pdx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 08 78 62 54 08 00')))
            references = {name: root / ('source.' + name) for name in ('mdx', 'pdx')}
            for path in references.values():
                path.write_bytes(b'original reference material')
            for body, expected in [(bytes.fromhex('54 08 78 62 54 08 00'), 0),
                                   (bytes.fromhex('54 08 78 50 90 62'), 2)]:
                source.write_bytes(vgm(body))
                run = self.run_cli(source)
                self.assertEqual(run.returncode, expected, run.stderr)
                for path in references.values():
                    self.assertEqual(path.read_bytes(), b'original reference material')
                report = json.loads((root / 'source.conversion.json').read_text())
                self.assertEqual(report['retained_binary_artifacts'], ['source.mdx', 'source.pdx'])

    def test_modified_previous_binary_is_preserved_on_failed_rerun(self):
        from test_pcm_mdx import record_previous_binaries
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 08 78 50 90 62')))
            for name in ('mdx', 'pdx'):
                (root / ('source.' + name)).write_bytes(b'previous generated artifact')
            record_previous_binaries(root, 'source')
            (root / 'source.pdx').write_bytes(b'replaced reference material')
            run = self.run_cli(source)
            self.assertEqual(run.returncode, 2)
            self.assertFalse((root / 'source.mdx').exists())
            self.assertEqual((root / 'source.pdx').read_bytes(), b'replaced reference material')
            report = json.loads((root / 'source.conversion.json').read_text())
            self.assertEqual(report['retained_binary_artifacts'], ['source.pdx'])

    def test_pcm_configuration_on_psg_is_reported_as_not_applicable(self):
        fixture = ROOT / 'tests/fixtures/public/psg/volume_sweep/volume_sweep.vgm'
        with tempfile.TemporaryDirectory() as temporary:
            run = self.run_cli(fixture, '--outdir', temporary, '--pcm-policy', 'best-effort', '--pcm-generator', 'unused')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('not applicable', run.stdout)
            report = json.loads((Path(temporary) / 'volume_sweep.conversion.json').read_text())
            self.assertFalse(report['pcm_options']['applicable'])


    def test_undeclared_used_opm_commands_are_explicitly_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'absent.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 08 78 62'), clock=0))
            run = self.run_cli(source, '--outdir', root / 'out')
            self.assertEqual(run.returncode, 2)
            self.assertIn('no clock declaration', run.stderr)

    def test_active_opll_mixed_with_opm_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'mixed.vgm'
            raw = bytearray(vgm(bytes.fromhex('54 08 78 51 20 10 62')))
            struct.pack_into('<I', raw, 0x10, 3579545)
            source.write_bytes(raw)
            run = self.run_cli(source, '--outdir', root / 'out')
            self.assertEqual(run.returncode, 2)
            self.assertIn('Unsupported MDX source combination', run.stderr)
            self.assertFalse((root / 'out/mixed.mdx.mml').exists())

    def test_unused_native_declaration_does_not_change_psg_projection(self):
        fixture = ROOT / 'tests/fixtures/public/psg/volume_sweep/volume_sweep.vgm'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'psg.vgm'
            raw = bytearray(fixture.read_bytes())
            struct.pack_into('<I', raw, 0x30, 4000000)
            struct.pack_into('<I', raw, 0x90, 8000000)
            source.write_bytes(raw)
            run = self.run_cli(source, '--outdir', root / 'out')
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((root / 'out/psg.conversion.json').read_text())
            self.assertEqual(report['chip_projection'], 'psg-scc-to-opm')
            self.assertIn('opm', report['source']['unused_declarations'])
            self.assertIn('pcm', report['source']['unused_declarations'])

    def test_compressed_data_bank_and_incomplete_stream_are_diagnosed(self):
        from generate_pcm_fixtures import vgm as pcm_vgm
        block = b'\x67\x66\x44' + struct.pack('<I', 2) + b'\x12\x34'
        stream = bytes.fromhex('90 00 17 00 01 95 00 00 00 00 62')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'bank.vgm'
            source.write_bytes(pcm_vgm(block + stream))
            run = self.run_cli(source, '--outdir', root / 'out', '--dump-passes')
            self.assertEqual(run.returncode, 2)
            self.assertIn('unsupported_pcm_data_bank', run.stderr)
            self.assertIn('stream_configuration_incomplete', run.stderr)
            assessment = json.loads((root / 'out/bank.pcm.assessment.json').read_text())
            self.assertEqual(assessment['artifact_status'], 'blocked')
            self.assertFalse((root / 'out/bank.mdx.mml').exists())

    def test_normalization_rejection_keeps_structured_baseline_output(self):
        from test_opm_note_normalization import jittered_source
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'jitter.vgm'
            jittered_source(source, tiny_control=True)
            for name, flags in [('auto', []), ('off', ['--no-normalize-lengths'])]:
                run = self.run_cli(source, '--outdir', root / name, '--dump-passes', *flags)
                self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((root / 'auto/jitter.mdx.normalization.json').read_text())
            self.assertTrue(report['enabled'])
            self.assertFalse(report['adopted'])
            self.assertIn('collapse', report['reason'])
            self.assertEqual(report['notation'], 'structured')
            self.assertEqual(report['before'], report['selected'])
            self.assertEqual((root / 'auto/jitter.mdx.mml').read_bytes(),
                             (root / 'off/jitter.mdx.mml').read_bytes())
            self.assertTrue((root / 'auto/jitter.mdx.structure.units.csv').exists())

    def test_additive_alias_keeps_model_defaults_and_rejects_conflict(self):
        fixture = ROOT / 'tests/fixtures/public/psg/volume_sweep/volume_sweep.vgm'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.run_cli(fixture, '--outdir', root, '--target', 'opm-additive')
            self.assertEqual(run.returncode, 0, run.stderr)
            settings = json.loads((root / 'volume_sweep.conversion.json').read_text())['projection_settings']
            self.assertEqual((settings['psg_model'], settings['psg_gain'], settings['pitch_policy']),
                             ('additive', .125, 'error'))
            run = self.run_cli(fixture, '--outdir', root, '--target', 'opm-additive', '--psg-model', 'fm')
            self.assertEqual(run.returncode, 2)

    def test_help_separates_format_projection_and_compatibility(self):
        run = self.run_cli('--help')
        self.assertEqual(run.returncode, 0)
        for group in ('MDX target timing', 'Advanced MDX', 'Target fidelity', 'Diagnostic', 'MGSDRV compatibility'):
            self.assertIn(group, run.stdout)
        self.assertNotIn('--articulation', run.stdout)
        self.assertNotIn('--enhance-macros', run.stdout)

    def test_native_title_language_and_explicit_override(self):
        import struct
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'title.vgm'
            raw = bytearray(vgm(bytes.fromhex('54 28 40 54 08 78 62 54 08 00 62')))
            payload = ('English' + chr(0) + 'Japanese' + chr(0) * 10).encode('utf-16-le')
            struct.pack_into('<I', raw, 0x14, len(raw) - 0x14)
            raw += b'Gd3 ' + struct.pack('<II', 0x100, len(payload)) + payload
            struct.pack_into('<I', raw, 4, len(raw) - 4)
            source.write_bytes(raw)
            for name, options, expected in [
                ('ja', [], 'Japanese'),
                ('en', ['--gd3-language', 'en'], 'English'),
                ('override', ['--title', 'Override'], 'Override'),
            ]:
                run = self.run_cli(source, '--outdir', root / name, *options)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertIn('#title "' + expected + '"',
                              (root / name / 'title.mdx.mml').read_text(encoding='utf-8'))

    def test_native_rejects_explicit_scc_gain_including_compatibility_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'gain.vgm'
            source.write_bytes(vgm(bytes.fromhex('54 28 40 54 08 78 62 54 08 00 62')))
            for gain in ('0.125', '1'):
                run = self.run_cli(source, '--outdir', root / 'output', '--scc-gain', gain)
                self.assertEqual(run.returncode, 2)
                self.assertIn('do not apply to native MDX', run.stderr)
