"""Authored source-only HAL checks; no compiler, audio or private fixtures."""
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'py'))
from musical_hal import build_native_performance, build_score_guide, write_native_vgm
from musical_hal.native_imports.vgm_timing import command_times
from musical_hal.native_vgm import scheduled_native_writes


def authored(commands, clocks):
    raw = bytearray(256)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x34, 256-0x34)
    for offset, clock in clocks.items():
        struct.pack_into('<I', raw, offset, clock)
    raw.extend(commands)
    raw.append(0x66)
    struct.pack_into('<I', raw, 4, len(raw)-4)
    return bytes(raw)


class MusicalHalSourceTests(unittest.TestCase):
    def roundtrip(self, raw, expected):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            performance = build_native_performance(raw, root, 'authored')
            self.assertEqual({d.chip for d in performance.devices}, set(expected))
            self.assertEqual(performance.source_sha256, hashlib.sha256(raw).hexdigest())
            self.assertEqual(performance.end_sample, 17)
            self.assertTrue(all(type(c.sample) is int for c in performance.controls))
            guide = build_score_guide(performance, repeats=True)
            guide.dump(root, 'authored')
            target = root/'reference.vgm'
            mapping = root/'mapping.csv'
            write_native_vgm(performance, target, mapping_path=mapping)
            rebuilt = target.read_bytes()
            def operations(data):
                events = tuple(command_times(data))
                return [(e.vgmticks, data[e.address:following.address])
                        for e, following in zip(events, events[1:])
                        if e.command != 0x61]
            source_commands = operations(raw)
            replay_commands = operations(rebuilt)
            self.assertEqual(replay_commands, source_commands)
            scheduled = scheduled_native_writes(performance)
            self.assertEqual([(r['sample'], r['command']) for r in scheduled], source_commands)
            with mapping.open(newline='', encoding='utf-8') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), len(source_commands))
            source_report = json.loads((root/'authored.native.source.json').read_text())
            self.assertEqual(source_report['source_sha256'], performance.source_sha256)
            self.assertTrue((root/'authored.score_guide.events.csv').is_file())
            return performance

    def test_native_domains_order_and_integer_time(self):
        commands = bytes.fromhex(
            '54 20 c7 54 28 4a 54 08 78 '
            '56 a0 69 56 a4 22 56 28 f0 '
            '50 80 50 10 50 90 '
            'b7 00 02 b8 00 08 c0 86 00 01 d4 00 05 00 '
            'b9 00 00 b9 02 40 b9 03 01 b9 04 9f '
            '61 11 00 54 08 00 56 28 00 50 9f b9 04 00')
        self.roundtrip(authored(commands, {0x30:4000000, 0x48:7987200, 0x0c:3579545,
            0x90:4000000, 0x98:1000000, 0x38:4000000, 0xa8:8000000, 0xa4:3579545}),
            ('opm','opna','dcsg','msm6258','msm6295','segapcm','c140','huc6280'))

    def test_psg_and_opll_use_local_source_adapters(self):
        commands = bytes.fromhex('a0 00 40 a0 01 01 a0 07 3e a0 08 0f '
            '51 30 10 51 10 80 51 20 15 d2 00 00 7f d2 01 00 40 d2 01 01 01 d2 02 00 0f d2 03 00 01 61 11 00 a0 08 00 51 20 05 d2 03 00 00')
        performance = self.roundtrip(authored(commands, {0x74:1789772,0x10:3579545,0x9c:1789772}), ('psg','opll','scc'))
        self.assertTrue(any(unit.device_id == 'opll:0' for unit in performance.units))

    def test_pcm_data_and_stream_dependencies(self):
        commands = bytes.fromhex('67 66 04 02 00 00 00 12 34 '
            '90 00 17 00 01 91 00 04 01 00 92 00 40 1f 00 00 '
            '93 00 00 00 00 00 01 02 00 00 00 61 11 00 94 00')
        performance = self.roundtrip(authored(commands, {0x90:4000000}), ('msm6258',))
        self.assertTrue(any(p.dependencies for p in performance.pcm_packets))

    def test_normalization_preserves_original_evidence_and_dumps(self):
        from musical_hal.score_normalization import normalize
        raw = authored(bytes.fromhex('54 20 c7 54 28 4a 54 08 78 61 11 00 54 08 00'),
                       {0x30:4000000})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = build_native_performance(raw, root, 'original')
            before = scheduled_native_writes(original)
            projected = normalize(original, tempo=225)
            projected.dump(root, 'projected')
            self.assertEqual(scheduled_native_writes(original), before)
            self.assertIsNot(projected.performance, original)
            self.assertEqual(projected.performance.source_sha256, original.source_sha256)
            self.assertTrue((root/'projected.score_normalization.controls.csv').is_file())
            self.assertTrue((root/'projected.score_normalization.json').is_file())
            write_native_vgm(projected.performance, root/'projected.vgm')

    def test_provenance_hashes_match_package(self):
        root = Path(__file__).resolve().parents[2]
        provenance = json.loads((root/'docs/musical_hal_provenance.json').read_text())
        self.assertTrue(provenance['source_commit'].startswith('b78d675'))
        for row in provenance['files']:
            with self.subTest(path=row['destination_path']):
                self.assertEqual(hashlib.sha256((root/row['destination_path']).read_bytes()).hexdigest(),
                                 row['adapted_sha256'])


if __name__ == '__main__':
    unittest.main()
