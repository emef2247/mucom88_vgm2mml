"""Authored mixed-source evidence and explicit OPM-only export checks."""
import csv
import gzip
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from conversion_config import inspect_source, select_mdx_route
from vgm_reader import parse_vgm
from export_mdx import run_batch


def vgm(commands, *, sega=True, loop=None):
    raw = bytearray(0x100)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x30, 4000000)
    struct.pack_into('<I', raw, 0x34, len(raw) - 0x34)
    if sega:
        struct.pack_into('<II', raw, 0x38, 4000000, 0x70000c)
    if loop is not None:
        struct.pack_into('<I', raw, 0x1c, len(raw) + loop - 0x1c)
    raw += commands + b'\x66'
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return bytes(raw)


def write(offset, value):
    return b'\xc0' + struct.pack('<HB', offset, value)


def block(payload, *, instance=0, start=0, size=65536):
    body = struct.pack('<II', size, start) + payload
    return b'\x67\x66\x80' + struct.pack('<I', len(body) | instance << 31) + body


def fm():
    commands = b'\x54\x20\xc7'
    for op in range(4):
        for reg, value in ((0x40, 1), (0x60, 32), (0x80, 31),
                           (0xa0, 0), (0xc0, 0), (0xe0, 15)):
            commands += bytes((0x54, reg + op * 8, value))
    return commands + b'\x54\x28\x40\x54\x30\x00\x54\x08\x78\x62\x54\x08\x00\x62'


def rows(path):
    with open(path, newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


class SegaPcmSourceTests(unittest.TestCase):
    def parse(self, folder, data, **options):
        source = folder / 'authored.vgm'
        source.write_bytes(data)
        sega, opm = {}, {}
        paths = parse_vgm(str(source), str(folder / 'out'), segapcm_metadata=sega,
                          opm_metadata=opm, dump_opm_segments=True,
                          include_vgmticks=True, **options)
        self.assertEqual(len(paths), 8)
        json.dumps(sega)
        return sega, opm

    def test_shared_order_rom_history_unknowns_and_eof(self):
        payload = b'\x66\xc0\x62\x67\x00'
        commands = (fm() + block(payload) + write(0x84, 23) + write(0x86, 0)
                    + write(0x86, 0) + b'\x62' + block(b'\x80', start=1) + b'\x62')
        with tempfile.TemporaryDirectory() as tmp:
            sega, opm = self.parse(Path(tmp), vgm(commands))
            self.assertEqual(sega['source_end_vgmticks'], 2940)
            self.assertEqual(opm['source_end_vgmticks'], 2940)
            self.assertEqual(Path(sega['payload_paths'][0]).read_bytes(), payload)
            states = rows(sega['state_csv_path'])
            self.assertEqual([s['event_kind'] for s in states],
                             ['programmed_address_low_write', 'enable_request', 'enable_request', 'rom_update'])
            self.assertEqual(states[-1]['rom_generation'], '2')
            self.assertEqual(states[-1]['programmed_address'], '')
            segments = rows(sega['segments_csv_path'])
            self.assertEqual(segments[-1]['end_reason'], 'source_end')
            self.assertEqual(segments[-1]['enabled_configuration'], 'True')
            self.assertTrue(all(s['runtime_address'] == '' for s in segments))
            self.assertEqual(rows(opm['csv_path'])[-1]['vgmticks'], '735')

    def test_gzip_instances_loop_provenance_and_malformed_header(self):
        commands = write(0x8086, 1) + b'\x62' + write(0x8086, 0) + block(b'\x81', instance=1) + b'\x62'
        with tempfile.TemporaryDirectory() as tmp:
            data = vgm(commands, loop=5)
            sega, _ = self.parse(Path(tmp), gzip.compress(data), loop_count=2)
            self.assertTrue(any(r['loop_iteration'] == '1' for r in rows(sega['csv_path'])))
            self.assertTrue(all(r['chip_instance'] == '1' for r in rows(sega['csv_path'])))
            self.assertIn('Second SegaPCM instance used without a dual-chip declaration', sega['issues'])
        malformed = b'\x67\x66\x80\x02\x00\x00\x00ab'
        with tempfile.TemporaryDirectory() as tmp:
            sega, _ = self.parse(Path(tmp), vgm(malformed))
            self.assertEqual(Path(sega['payload_paths'][0]).read_bytes(), b'ab')
            self.assertTrue(sega['issues'])

    def test_inventory_default_and_full_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'a.vgm'
            source.write_bytes(vgm(fm()))
            self.assertEqual(select_mdx_route(inspect_source(source)), 'native-opm-pcm')
            source.write_bytes(vgm(fm() + write(0x86, 0)))
            self.assertEqual(select_mdx_route(inspect_source(source)), 'opm-segapcm-source')
            source.write_bytes(vgm(fm() + write(0x86, 0) + b'\x52\x00\x00'))
            with self.assertRaisesRegex(ValueError, 'unsupported_source'):
                select_mdx_route(inspect_source(source))
            source.write_bytes(vgm(b'\xc0\x00'))
            with self.assertRaises(ValueError):
                inspect_source(source)

    def test_cli_fm_equivalence_and_failed_target_retains_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain, mixed = root / 'plain.vgm', root / 'mixed.vgm'
            plain.write_bytes(vgm(fm(), sega=False, loop=0))
            mixed.write_bytes(vgm(block(b'\x80\x81') + write(0x86, 0) + fm(), loop=0))
            def run(source, out, *options):
                result = subprocess.run([sys.executable, str(ROOT / 'vgm2mml.py'), str(source), '--target', 'mdx',
                                         '--outdir', str(out), '--title', 'authored', *options],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([sys.executable, str(ROOT / 'vgm2mml.py'), str(mixed), '--target', 'mdx',
                                     '--outdir', str(root / 'source')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads((root / 'source/mixed.conversion.json').read_text())
            self.assertEqual(report['status'], 'blocked')
            self.assertFalse(list((root / 'source').glob('*.mml')))
            self.assertTrue((root / 'source/mixed.segapcm.segments.csv').is_file())
            run(plain, root / 'plain')
            run(mixed, root / 'mixed', '--fm-only')
            a = (root / 'plain/plain.mdx.mml').read_text()
            b = (root / 'mixed/mixed.mdx.mml').read_text()
            self.assertEqual(a, b)
            self.assertIn(' L', ' ' + b.replace('\n', ' '))
            report = json.loads((root / 'mixed/mixed.conversion.json').read_text())
            self.assertEqual(report['excluded_sources'][0]['chip'], 'segapcm')
            self.assertTrue((root / 'mixed/mixed.segapcm.segments.csv').is_file())
            self.assertTrue((root / 'mixed/mixed.opm.segments.csv').is_file())
            self.assertFalse(list((root / 'mixed').glob('*.pdx')))

    def test_export_option_and_source_only_does_not_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, generator = root / 'authored.vgm', root / 'helper'
            source.write_bytes(vgm(fm() + write(0x86, 0)))
            generator.touch()
            def convert(command, **kwargs):
                self.assertIn('--fm-only', command)
                return subprocess.CompletedProcess(command, 1, '', 'test stop before compiler')
            with patch('export_mdx.subprocess.run', side_effect=convert):
                result = run_batch(source, root / 'fm', generator=generator, fm_only=True)[0]
            self.assertEqual(result['status'], 'conversion_failed')
            def analyzed(command, **kwargs):
                out = Path(command[command.index('--outdir') + 1])
                (out / 'authored.conversion.json').write_text('{"status":"source_analyzed"}')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('export_mdx.subprocess.run', side_effect=analyzed), \
                    patch('export_mdx.compile_mxc') as compiler:
                result = run_batch(source, root / 'source', generator=generator)[0]
            compiler.assert_not_called()
            self.assertEqual(result['status'], 'target_projection_failed')
            self.assertIn('--fm-only', result['detail'])


if __name__ == '__main__':
    unittest.main()
