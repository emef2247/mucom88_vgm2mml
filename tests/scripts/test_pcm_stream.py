"""Authored stream supply schedules remain separate from actual chip controls."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from generate_pcm_fixtures import (DESTINATION, data_block, stream_cases,
    stream_fast_start, stream_setup, stream_start, vgm, wait, write)
from msm6258 import analyze


class PcmStreamTests(unittest.TestCase):
    def test_public_authored_schedules_and_raw_banks_are_reproducible(self):
        for name, raw, expected in stream_cases():
            with self.subTest(name=name):
                self.assertEqual((DESTINATION / (name + '.vgm')).read_bytes(), raw)
                self.assertEqual(json.loads((DESTINATION / (name + '.expected.json')).read_text()), expected)
                analysis = analyze(raw)
                supplies = list(analysis.stream_supplies)
                self.assertEqual([s.vgmticks for s in supplies], expected['transfer_ticks'])
                self.assertEqual([s.data for s in supplies], expected['transfer_bytes'])
                self.assertEqual(analysis.blocks[0].payload, b'\x12\x34\x56\x78')
                self.assertTrue(all(s.bank_id == 4 and s.block_id == 0 for s in supplies))
                self.assertTrue(all(t.register != 1 for t in analysis.transfers))
                self.assertEqual(analysis.source_end_vgmticks, expected['source_end_vgmticks'])
                if expected['sample_sha256'] is None:
                    self.assertFalse(analysis.playbacks)
                else:
                    self.assertEqual(analysis.samples[0].sha256, expected['sample_sha256'])

    def test_finite_stream_is_independent_when_chip_stop_is_observed(self):
        for name, raw, _ in stream_cases():
            if name in ('stream95_finite', 'stream93_count', 'stream93_to_end'):
                with self.subTest(name=name):
                    a = analyze(raw)
                    self.assertTrue(a.playbacks[0].independently_playable)
                    self.assertFalse(a.issues)
                    self.assertEqual([c.data for c in a.controls if c.register == 0], [1, 2, 1])

    def test_natural_end_and_stream_stop_do_not_write_chip_stop(self):
        for ending in (b'', b'\x94\x00', b'\x94\xff'):
            with self.subTest(ending=ending):
                commands = (data_block(b'\x12\x34') + stream_setup() +
                    write(0, 2) + stream_fast_start() + wait(7) + ending + wait(100))
                a = analyze(vgm(commands))
                self.assertEqual([c.data for c in a.controls if c.register == 0], [2])
                self.assertEqual(len(a.playbacks), 1)
                self.assertEqual(a.playbacks[0].end_vgmticks, 107)
                self.assertFalse(a.playbacks[0].independently_playable)
                self.assertIn('irregular_byte_supply', a.playbacks[0].issues)

    def test_same_tick_stream_stop_cancels_supply_before_restart(self):
        commands = (data_block(b'\x12') + data_block(b'\x34') + stream_setup() +
            write(0, 2) + stream_fast_start(0) + b'\x94\x00' +
            stream_fast_start(1) + wait(5) + write(0, 1))
        a = analyze(vgm(commands))
        self.assertEqual([(s.vgmticks, s.data, s.block_id) for s in a.stream_supplies], [(0, 0x34, 1)])
        self.assertEqual(a.samples[0].encoded_bytes, b'\x34')

    def test_zero_frequency_pauses_supply_and_preserves_stream_position(self):
        commands = (data_block(b'\x12\x34\x56\x78') + stream_setup(frequency=11025) +
            write(0, 2) + stream_fast_start() + wait(4) + b'\x92\x00' +
            struct.pack('<I', 0) + wait(8) + b'\x92\x00' +
            struct.pack('<I', 11025) + wait(13) + write(0, 1))
        a = analyze(vgm(commands))
        self.assertEqual([(s.vgmticks, s.data) for s in a.stream_supplies],
                         [(0, 0x12), (12, 0x34), (16, 0x56), (20, 0x78)])
        self.assertNotIn('unsupported_stream_control', [i.code for i in a.issues])

    def test_zero_frequency_start_remains_explicitly_unsupported(self):
        for resume in (b'', b'\x92\x00' + struct.pack('<I', 44100)):
            with self.subTest(resume=resume):
                commands = (data_block(b'\x12\x34\x56') + stream_setup(frequency=0) +
                            write(0, 2) + stream_fast_start() + resume + wait(9))
                analysis = analyze(vgm(commands))
                self.assertFalse(analysis.stream_supplies)
                self.assertIn('unsupported_stream_control', [i.code for i in analysis.issues])

    def test_active_data_setup_change_is_diagnosed_before_new_supplies(self):
        commands = (data_block(bytes(range(8))) + stream_setup() + write(0, 2) +
                    stream_fast_start() + wait(7) + bytes.fromhex('91 00 04 02 01') + wait(20))
        analysis = analyze(vgm(commands))
        self.assertEqual([s.data for s in analysis.stream_supplies], [0, 1])
        self.assertIn('unsupported_stream_control', [i.code for i in analysis.issues])

    def test_repeated_setup_retains_data_and_frequency_configuration(self):
        commands = (data_block(b'\x12\x34') + stream_setup() + write(0, 2) +
                    bytes.fromhex('90 00 17 00 01') + stream_fast_start() + wait(10) + write(0, 1))
        analysis = analyze(vgm(commands))
        self.assertFalse(analysis.issues)
        self.assertEqual([s.data for s in analysis.stream_supplies], [0x12, 0x34])

    def test_reused_start_offset_keeps_absolute_position_after_base_change(self):
        commands = (data_block(bytes(range(6))) + stream_setup(base=1) + write(0, 2) +
                    stream_start(offset=1, length=1) + wait(6) +
                    bytes.fromhex('91 00 04 01 00') + stream_start(offset=0xffffffff, length=2) +
                    wait(12) + write(0, 1))
        analysis = analyze(vgm(commands))
        self.assertFalse(analysis.issues)
        self.assertEqual([s.bank_offset for s in analysis.stream_supplies], [2, 2, 3])

    def test_bank_offsets_cross_block_boundaries_and_step_selects_source_bytes(self):
        bank = data_block(b'\x10\x11\x12') + data_block(b'\x20\x21\x22')
        for step, base, expected, offsets in (
                (0, 0, [0x12, 0x20, 0x21, 0x22], [2, 3, 4, 5]),
                (2, 1, [0x11, 0x20, 0x22], [1, 3, 5])):
            with self.subTest(step=step):
                commands = bank + stream_setup(step=step, base=base) + write(0, 2)
                commands += stream_start(offset=2 if step == 0 else 0, mode=3, length=0)
                a = analyze(vgm(commands + wait(25) + write(0, 1)))
                self.assertEqual([s.data for s in a.stream_supplies], expected)
                self.assertEqual([s.bank_offset for s in a.stream_supplies], offsets)
                self.assertEqual([b.bank_offset for b in a.blocks], [0, 3])

    def test_invalid_bank_block_and_bounds_do_not_fabricate_supplies(self):
        for setup, start in (
                (stream_setup(bank=3), stream_fast_start()),
                (stream_setup(), stream_fast_start(block=8)),
                (stream_setup(), stream_start(offset=8)),
                (stream_setup(), stream_start(length=8))):
            with self.subTest(setup=setup, start=start):
                a = analyze(vgm(data_block(b'\x12\x34') + setup + start + wait(25)))
                self.assertFalse(a.stream_supplies)
                self.assertIn('invalid_stream_reference', [i.code for i in a.issues])
                self.assertEqual(a.blocks[0].payload, b'\x12\x34')

    def test_incomplete_configuration_and_unsupported_modes_remain_diagnosed(self):
        for commands, code in (
                (b'\x90\x00\x17\x00\x01' + stream_fast_start(), 'stream_configuration_incomplete'),
                (stream_setup(register=0) + stream_fast_start(), 'unsupported_stream_control'),
                (stream_setup(frequency=44101) + stream_fast_start(), 'unsupported_stream_control'),
                (stream_setup() + stream_fast_start(flags=1), 'unsupported_stream_control'),
                (stream_setup() + stream_fast_start(flags=0x10), 'unsupported_stream_control'),
                (stream_setup() + stream_start(mode=0x81), 'unsupported_stream_control'),
                (stream_setup() + stream_start(mode=0x11), 'unsupported_stream_control'),
                (stream_setup() + stream_start(mode=2), 'unsupported_stream_control')):
            with self.subTest(code=code, commands=commands):
                a = analyze(vgm(data_block(b'\x12\x34') + commands + wait(25)))
                self.assertFalse(a.stream_supplies)
                self.assertIn(code, [i.code for i in a.issues])

    def test_compressed_bank_payload_is_retained_without_guessing_decoded_bytes(self):
        payload = b'\x12\x34\x56\x78'
        a = analyze(vgm(data_block(payload, bank=0x44) + stream_setup() +
                        stream_fast_start() + wait(25)))
        self.assertFalse(a.stream_supplies)
        self.assertEqual(a.blocks[0].payload, payload)
        self.assertIn('unsupported_pcm_data_bank', [i.code for i in a.issues])

    def test_stream_pass_dump_preserves_command_and_derived_supply_origin(self):
        name, raw, expected = next(stream_cases())
        a = analyze(raw)
        with tempfile.TemporaryDirectory() as folder:
            a.dump(folder, name)
            with (Path(folder) / (name + '.pcm_stream_supplies.csv')).open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([int(row['vgmticks']) for row in rows], expected['transfer_ticks'])
            self.assertTrue(all(int(row['address']) >= 0x100 for row in rows))
            self.assertTrue(all(row['source_event_id'] == rows[0]['source_event_id'] for row in rows))


if __name__ == '__main__':
    unittest.main()
