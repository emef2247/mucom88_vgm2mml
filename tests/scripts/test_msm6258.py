"""Original synthetic ADPCM source evidence; no game-derived sample assets."""
import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from msm6258 import analyze


def source(commands, *, clock=8000000, flags=6, loop_command_offset=None):
    header = bytearray(0x100)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 8, 0x171)
    struct.pack_into('<I', header, 0x34, 0x100-0x34)
    struct.pack_into('<I', header, 0x90, clock)
    header[0x94] = flags
    if loop_command_offset is not None:
        struct.pack_into('<I', header, 0x1c, 0x100 + loop_command_offset - 0x1c)
    result = header + commands + b'\x66'
    struct.pack_into('<I', result, 4, len(result)-4)
    return bytes(result)


def write(reg, value):
    return bytes((0xb7, reg, value))


def wait(ticks):
    return b'\x61' + struct.pack('<H', ticks)


def run(data=(0x12, 0x34), pan=0):
    # One byte every 5.6448 VGM samples at 8MHz/512: exact source lattice
    # rounded down to 0,5. Stop at 11 before another byte is due.
    return (write(0,1) + write(2,pan) + write(0,2) + write(1,data[0]) +
            wait(5) + write(1,data[1]) + wait(6) + write(0,1))


class Msm6258Tests(unittest.TestCase):
    def test_reset_play_low_first_bytes_and_exact_source_ids(self):
        analysis = analyze(source(run()))
        self.assertEqual(analysis.source_end_vgmticks, 11)
        self.assertEqual(analysis.samples[0].encoded_bytes, b'\x12\x34')
        playback = analysis.playbacks[0]
        self.assertEqual((playback.start_vgmticks, playback.end_vgmticks), (0,11))
        self.assertTrue(playback.reset_observed)
        self.assertTrue(playback.independently_playable)
        self.assertEqual((playback.rate_num,playback.rate_den), (15625,1))
        self.assertIsNone(playback.consumed_nibbles)
        self.assertEqual(playback.nominal_nibbles, 3)
        data = [row for row in analysis.transfers if row.register == 1]
        self.assertEqual([(r.source_event_id,r.vgmticks) for r in data], [(3,0),(5,5)])
        with self.assertRaises(FrozenInstanceError):
            playback.sample_id = 8

    def test_initial_reset_assumption_is_distinct_from_observed_reset(self):
        raw = source(write(0,2)+write(1,0x12)+wait(5)+write(0,1))
        playback = analyze(raw).playbacks[0]
        self.assertFalse(playback.reset_observed)
        self.assertTrue(playback.decoder_reset_known)
        self.assertEqual(playback.reset_origin, 'vgm_initialization')
        self.assertTrue(playback.independently_playable)
        initial_control=analyze(raw).controls[0]
        self.assertFalse(initial_control.reset_observed)
        self.assertTrue(initial_control.decoder_reset_known)
        unknown = analyze(raw, initialization='explicit').playbacks[0]
        self.assertFalse(unknown.independently_playable)
        self.assertIn('decoder_start_unknown_or_continuation', unknown.issues)

    def test_exact_byte_dedup_does_not_include_pan_or_reset_state(self):
        analysis = analyze(source(run(pan=1)+run(pan=2)))
        self.assertEqual(len(analysis.samples),1)
        self.assertEqual([p.sample_id for p in analysis.playbacks], [0,0])
        self.assertEqual([p.pan for p in analysis.playbacks], [1,2])
        self.assertEqual([p.start_vgmticks for p in analysis.playbacks], [0,11])

    def test_hash_collision_still_checks_bytes_and_stable_first_seen_ids(self):
        class Collision:
            def hexdigest(self):
                return '0'*64
        with patch('msm6258.hashlib.sha256',return_value=Collision()):
            a=analyze(source(run((0x12,0x34))+run((0x56,0x78))+run((0x12,0x34))))
        self.assertEqual([p.sample_id for p in a.playbacks],[0,1,0])
        self.assertEqual([s.encoded_bytes for s in a.samples],[b'\x12\x34',b'\x56\x78'])

    def test_unknown_data_error_is_aggregated_but_all_bytes_retained(self):
        a=analyze(source(write(1,0x12)*100),initialization='explicit')
        self.assertEqual(len(a.transfers),100)
        self.assertEqual(len(a.issues),1)
        self.assertIn('100 writes',a.issues[0].detail)

    def test_pan_and_repeated_play_preserve_one_continuous_span(self):
        raw = source(write(0,2)+write(1,0x12)+wait(5)+write(2,1)+
                     write(0,2)+write(1,0x34)+wait(6)+write(0,1))
        analysis = analyze(raw)
        self.assertEqual(len(analysis.playbacks),1)
        self.assertEqual(analysis.samples[0].encoded_bytes,b'\x12\x34')
        self.assertFalse([c for c in analysis.controls if c.register==0][1].reset_observed)
        self.assertTrue(analysis.playbacks[0].independently_playable)
        self.assertEqual(analysis.playbacks[0].control_event_ids,(0,3,4))

    def test_same_timestamp_stop_play_order_is_retained(self):
        raw = source(write(0,2)+write(1,0x12)+write(0,1)+write(0,2)+
                     write(1,0x34)+wait(5)+write(0,1))
        analysis = analyze(raw)
        self.assertEqual([(p.start_vgmticks,p.end_vgmticks) for p in analysis.playbacks],[(0,0),(0,5)])
        self.assertTrue(analysis.playbacks[1].reset_observed)
        self.assertEqual([s.encoded_bytes for s in analysis.samples],[b'\x12',b'\x34'])

    def test_clock_latch_and_divider_before_data_update_rate(self):
        clock = 4000000
        commands = write(0,2)
        for reg, byte in zip(range(8,12),clock.to_bytes(4,'little')):
            commands += write(reg,byte)
        commands += write(12,1)+write(1,0x12)+wait(16)+write(0,1)
        p=analyze(source(commands)).playbacks[0]
        self.assertEqual((p.clock_hz,p.divider,p.rate_num,p.rate_den),(4000000,768,15625,3))
        self.assertTrue(p.independently_playable)

    def test_mid_data_rate_change_is_preserved_and_diagnosed(self):
        a=analyze(source(write(0,2)+write(1,0x12)+wait(5)+write(12,0)+
                         write(1,0x34)+wait(10)+write(0,1)))
        self.assertEqual(len(a.playbacks),1)
        self.assertIn('rate_change_during_playback',a.playbacks[0].issues)
        self.assertFalse(a.playbacks[0].independently_playable)
        self.assertEqual([c.divider for c in a.controls if c.register==12],[1024])

    def test_irregular_delivery_and_unfed_tail_cannot_pass_as_sample(self):
        for gap in (0,2,20):
            a=analyze(source(write(0,2)+write(1,0x12)+wait(gap)+write(1,0x34)+wait(5)+write(0,1)))
            self.assertFalse(a.playbacks[0].independently_playable)
            self.assertIn('irregular_byte_supply',a.playbacks[0].issues)
        a=analyze(source(write(0,2)+write(1,0x12)+wait(100)+write(0,1)))
        self.assertFalse(a.playbacks[0].delivery_cadence_compatible)

    def test_unsupported_register_mode_and_instance_remain_inspectable(self):
        a=analyze(source(run()+write(0x81,0x56)+write(3,7),flags=2))
        self.assertIn('unsupported_adpcm3',a.playbacks[0].issues)
        self.assertEqual(a.transfers[len(a.transfers)-2].chip_instance,1)
        self.assertEqual({i.code for i in a.issues},{'unsupported_chip_instance','unsupported_register'})

    def test_blocks_and_stream_commands_retained_without_fake_playback(self):
        block=b'\x67\x66\x04'+struct.pack('<I',2)+b'\x12\x34'
        setup=b'\x90\x00\x17\x00\x01'
        start=b'\x95\x00\x00\x00\x00'
        a=analyze(source(block+setup+start+wait(10)))
        self.assertFalse(a.playbacks)
        self.assertEqual([c.command for c in a.raw_commands],[0x67,0x90,0x95])
        self.assertIn('stream_configuration_incomplete', [i.code for i in a.issues])
        self.assertIn(b'\x12\x34',a.source_raw)
        self.assertEqual(a.blocks[0].payload,b'\x12\x34')

    def test_source_loop_inside_decoder_state_is_an_observation(self):
        a=analyze(source(run(),loop_command_offset=12))
        self.assertEqual(a.source_loop_vgmticks,0)
        self.assertIn('decoder_continuation_at_song_loop',[i.code for i in a.observations])
        self.assertNotIn('decoder_continuation_at_song_loop', [i.code for i in a.issues])
        self.assertTrue(a.playbacks[0].reset_observed)
        self.assertEqual([c.data for c in a.controls if c.register == 0], [1, 2, 1])

    def test_dump_retains_source_rows_and_exact_binary_asset(self):
        a=analyze(source(run()))
        with tempfile.TemporaryDirectory() as temp:
            a.dump(temp,'short')
            folder=Path(temp)
            self.assertEqual((folder/'short.pcm_samples/sample_0000.adpcm').read_bytes(),b'\x12\x34')
            with (folder/'short.pcm_raw.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),len(a.transfers))
            self.assertEqual(rows[3]['address'],'265')
            self.assertEqual(json.loads((folder/'short.pcm_source.json').read_text())['consumption_origin'],'unknown_not_emulated')


if __name__=='__main__':
    unittest.main()
