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


if __name__ == '__main__':
    unittest.main()
