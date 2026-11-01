"""Retained source usage and compatibility-initialization inspection."""
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
