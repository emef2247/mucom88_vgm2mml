import csv
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from vgm_loop import read_loop_metadata
from vgm_reader import parse_vgm
from vgm_timing import command_times
from opm import build_segments
from msm6258 import analyze


def vgm(commands, loop=None, loop_samples=0):
    header = bytearray(0x100)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 4, len(header) + len(commands) - 4)
    struct.pack_into('<I', header, 8, 0x161)
    struct.pack_into('<I', header, 0x34, 0xcc)
    struct.pack_into('<I', header, 0x1c, 0 if loop is None else 0x100 + loop - 0x1c)
    struct.pack_into('<I', header, 0x20, loop_samples)
    return bytes(header) + commands


class VgmLoopTests(unittest.TestCase):
    def test_reader_replay_uses_original_addresses_and_carries_wait_override(self):
        # The second traversal inherits the override established at body end.
        commands = bytes([0x70, 0x62, 0x64, 0x62, 10, 0, 0x66])
        events = list(command_times(vgm(commands, 1, 735), loop_count=3))
        self.assertEqual([e.wait_samples for e in events if e.command == 0x62], [735, 10, 10])
        self.assertEqual([e.vgmticks for e in events if e.command == 0x62], [1, 736, 746])
        self.assertEqual(events[-1].vgmticks, 756)
        self.assertEqual(sum(e.command == 0x66 for e in events), 1)
        repeated = [e for e in events if e.command == 0x62]
        self.assertEqual([e.source_event_id for e in repeated], [1, 1, 1])
        self.assertEqual([e.original_vgmticks for e in repeated], [1, 1, 1])
        self.assertEqual([e.loop_iteration for e in repeated], [0, 1, 2])

    def test_reader_replay_retains_held_key_state_and_occurrence_provenance(self):
        commands = bytes([0x54, 8, 0x78, 0x62, 0x54, 0x20, 0xc7, 0x62, 0x66])
        raw = bytearray(vgm(commands, 3, 1470))
        struct.pack_into('<I', raw, 0x30, 4000000)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'held.vgm'
            path.write_bytes(raw)
            metadata, opm = {}, {}
            parse_vgm(str(path), folder, loop_count=2, loop_metadata=metadata,
                      opm_metadata=opm, include_vgmticks=True, dump_loop=True)
            analysis = build_segments(opm['csv_path'], end_vgmticks=opm['source_end_vgmticks'])
            self.assertEqual(opm['source_end_vgmticks'], 2940)
            keys = [e for e in analysis.events if e.register == 8]
            self.assertEqual(len(keys), 1)
            self.assertEqual(keys[0].rising_mask, 15)
            replay = [e for e in analysis.events if e.loop_iteration == 1]
            self.assertEqual(len(replay), 1)
            self.assertEqual(replay[0].state.key_mask, 15)
            self.assertEqual(replay[0].original_event_id, 2)
            self.assertEqual(replay[0].original_vgmticks, 735)
            self.assertEqual(metadata['replay_start_event_id'], 4)
            self.assertEqual(metadata['replay_start_samples'], 1470)
            self.assertEqual(metadata['replay_loop_samples'], 1470)
            self.assertEqual(metadata['decoded_end_samples'], 1470)
            with (Path(folder) / 'held.vgm.timing.csv').open() as stream:
                timing = list(csv.DictReader(stream))
            self.assertEqual(sum(row['address'] == '259' for row in timing), 2)

    def test_pcm_replay_does_not_stop_or_reset_at_interim_end(self):
        commands = bytes([0xb7, 0, 2, 0xb7, 1, 0x12, 0x62, 0x66])
        raw = bytearray(vgm(commands, 3, 735))
        struct.pack_into('<I', raw, 0x90, 4000000)
        raw[0x94] = 4
        result = analyze(raw, loop_count=2)
        self.assertEqual(result.source_end_vgmticks, 1470)
        self.assertEqual(len(result.playbacks), 1)
        self.assertEqual(result.samples[0].encoded_bytes, b'\x12\x12')
        self.assertEqual(result.source_loop_vgmticks, 0)
        self.assertEqual(len([e for e in result.controls if e.register == 0]), 1)
        self.assertEqual([e.loop_iteration for e in result.command_provenance if e.command == 0xb7], [0, 0, 1])

    def test_replay_rejects_bad_count_and_invalid_or_zero_time_loop(self):
        for count in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                list(command_times(vgm(b'\x66'), loop_count=count))
        for raw in (vgm(b'\x66', 0), vgm(bytes([0x54, 8, 0, 0x62, 0x66]), 1)):
            with self.assertRaises(ValueError):
                list(command_times(raw, loop_count=2))
        self.assertEqual(list(command_times(vgm(b'\x62\x66'), loop_count=3))[-1].vgmticks, 735)

    def test_no_loop(self):
        self.assertEqual(read_loop_metadata(vgm(b'\x66'))['status'], 'no_loop')

    def test_loop_at_data_start(self):
        result = read_loop_metadata(vgm(b'\x62\x66', 0, 735))
        self.assertEqual(result['loop_address'], 0x100)
        self.assertEqual(result['loop_start_samples'], 0)
        self.assertEqual(result['decoded_loop_samples'], 735)

    def test_loop_on_wait_and_shared_clock(self):
        result = read_loop_metadata(vgm(bytes([0x77, 0x7a, 0x62, 0x61, 100, 0, 0x66]), 3, 100))
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['loop_start_samples'], 754)
        self.assertAlmostEqual(result['loop_start_trace_seconds'], 754 / 44100)
        self.assertEqual(result['decoded_loop_samples'], 100)

    def test_offset_inside_write_is_not_a_boundary(self):
        result = read_loop_metadata(vgm(bytes([0x50, 0x62, 0x66]), 1))
        self.assertEqual(result['status'], 'inside_command')

    def test_data_block_payload_is_skipped(self):
        commands = bytes([0x67, 0x66, 0]) + struct.pack('<I', 2) + bytes([0x62, 0x66]) + b'\x66'
        self.assertEqual(read_loop_metadata(vgm(commands, 7))['status'], 'inside_command')
        self.assertEqual(read_loop_metadata(vgm(commands, 9))['loop_start_samples'], 0)

    def test_invalid_and_unsupported_stream(self):
        self.assertEqual(read_loop_metadata(vgm(b'\x66', 20))['status'], 'outside_data')
        self.assertEqual(read_loop_metadata(vgm(b'\x01\x66', 1))['status'], 'unsupported_command')
        self.assertEqual(read_loop_metadata(vgm(b'\x61', 0))['status'], 'truncated_command')

    def test_loop_observation_preserves_trace_and_key_writes(self):
        commands = bytes([0x51, 0x20, 0x10, 0x62, 0x51, 0x20, 0x10, 0x62, 0x66])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'loop.vgm'
            path.write_bytes(vgm(commands, 3, 1470))
            paths = parse_vgm(str(path), folder)
            original = [Path(p).read_bytes() for p in paths]
            self.assertFalse((Path(folder) / 'loop.vgm.loop.csv').exists())
            metadata = {}
            paths = parse_vgm(str(path), folder, loop_metadata=metadata, dump_loop=True)
            self.assertEqual(original, [Path(p).read_bytes() for p in paths])
            with (Path(folder) / 'loop.vgm.loop.csv').open() as fh:
                row = next(csv.DictReader(fh))
            self.assertEqual(row['status'], 'valid')
            self.assertEqual(row['loop_start_samples'], '0')
            self.assertEqual(metadata['decoded_loop_samples'], 1470)


if __name__ == '__main__':
    unittest.main()
