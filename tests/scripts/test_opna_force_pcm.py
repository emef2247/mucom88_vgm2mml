"""Authored PCM force-omission checks; no private fixture dependencies."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from opna_adpcm import AdpcmIssue, analyze
from opna_force_pcm import prepare


def native_source(*, repeat=False, tail=b'', include_memory=True):
    encoded = b'\x70' + bytes(31)
    payload = struct.pack('<II', 32, 0) + encoded
    block = b'\x67\x66\x81' + struct.pack('<I', len(payload)) + payload
    writes = bytes((0x57, 1, 0xc1, 0x57, 9, 0, 0x57, 10, 0x20,
                    0x57, 11, 255, 0x57, 0, 0xb0 if repeat else 0xa0))
    body = (block if include_memory else b'') + writes + tail + b'\x61\x64\0\x66'
    raw = bytearray(0x100) + body
    raw[:4] = b'Vgm '
    for offset, value in ((4, len(raw)-4), (8, 0x171), (0x34, 0xcc), (0x48, 7987200)):
        struct.pack_into('<I', raw, offset, value)
    return analyze(raw)


class OpnaForcePcmTests(unittest.TestCase):
    def test_default_returns_exact_original_even_when_hardware_is_unsupported(self):
        native = native_source(repeat=True)
        projected, omissions = prepare(native)
        self.assertIs(projected, native)
        self.assertEqual(omissions, [])

    def test_repeat_omits_whole_part_preserving_native_and_all_control_provenance(self):
        native = native_source(repeat=True)
        before = (native.samples, native.segments, native.issues)
        projected, omissions = prepare(native, force=True)
        self.assertIsNot(projected, native)
        self.assertEqual((projected.samples, projected.segments, projected.issues), ((), (), ()))
        self.assertEqual((native.samples, native.segments, native.issues), before)
        self.assertTrue(native.samples and native.segments and native.issues)
        self.assertEqual(projected, replace(native, samples=(), segments=(), issues=()))
        self.assertIs(projected.blocks, native.blocks)
        self.assertIs(projected.controls, native.controls)
        self.assertEqual(len(omissions), len(native.controls))
        for row, control in zip(omissions, native.controls):
            self.assertEqual((row['source_event_id'], row['original_event_id'], row['loop_iteration'],
                              row['original_vgmticks'], row['address'], row['vgmticks'],
                              row['vgmticks_end'], row['chip_instance'], row['port'],
                              row['register'], row['data']),
                             (control.source_event_id, control.original_source_event_id,
                              control.loop_iteration, control.original_vgmticks, control.address,
                              control.vgmticks, control.vgmticks, control.chip_instance,
                              control.port, control.register, control.data))
            self.assertEqual(row['reason'], 'repeat_requires_continuous_decoder_timing')
            self.assertEqual(row['action'], 'omit_pcm_part')
            self.assertEqual(row['record_kind'], 'write')
            self.assertIsNone(row['source_ch'])
            self.assertIsNone(row['affected_bits'])
            self.assertEqual((row['trigger_port'], row['trigger_register'], row['trigger_data']),
                             (control.port, control.register, control.data))

    def test_unsupported_control_and_segment_only_hardware_issues_trigger_omission(self):
        native = native_source(tail=bytes.fromhex('57 06 01'))
        self.assertTrue(prepare(native, force=True)[1])
        repeat = native_source(repeat=True)
        self.assertTrue(prepare(replace(repeat, issues=()), force=True)[1])

    def test_supported_level_and_rate_controls_and_zero_delta_do_not_omit(self):
        for tail in ('57 0b 80', '57 09 01', '57 0a 00'):
            with self.subTest(tail=tail):
                native = native_source(tail=bytes.fromhex(tail))
                projected, omissions = prepare(native, force=True)
                self.assertIs(projected, native)
                self.assertEqual(omissions, [])
        native = native_source()
        zero = AdpcmIssue('zero_delta_n_or_clock', None, 0, 'authored ordinary-clock stop')
        native = replace(native, issues=(zero,))
        self.assertIs(prepare(native, force=True)[0], native)

    def test_missing_bytes_and_other_unresolved_errors_are_not_swallowed(self):
        native = native_source(repeat=True, include_memory=False)
        self.assertIn('unmapped_or_missing_rom_bytes', {issue.code for issue in native.issues})
        self.assertIs(prepare(native, force=True)[0], native)
        for code in ('source_sample_evidence_size_limit', 'unsupported_or_invalid_rom_block',
                     'unresolved_rom_block_history', 'changing_rom_size', 'unsupported_second_chip',
                     'unsupported_clock_or_chip_configuration', 'future_unknown_diagnostic'):
            with self.subTest(code=code):
                native = native_source(repeat=True)
                issue = AdpcmIssue(code, None, 0, 'authored unresolved blocker')
                native = replace(native, issues=native.issues + (issue,))
                self.assertEqual(prepare(native, force=True), (native, []))
        native = replace(native_source(repeat=True), clock_hz=0, clock_raw=0)
        self.assertEqual(prepare(native, force=True), (native, []))

    def test_harmless_codes_coexist_and_no_control_fallback_invents_no_write(self):
        native = native_source(repeat=True)
        harmless = tuple(AdpcmIssue(code, None, 0, 'authored supported evidence') for code in (
            'controls_during_decoder_epoch', 'ram1_seeded_from_vgm_rom_block_assumption',
            'ram8_seeded_from_vgm_rom_block_assumption', 'zero_delta_n_or_clock'))
        native = replace(native, issues=native.issues + harmless)
        self.assertTrue(prepare(native, force=True)[1])
        native = replace(native, controls=())
        projected, rows = prepare(native, force=True)
        self.assertEqual(projected.segments, ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['record_kind'], 'part')
        self.assertIsNone(rows[0]['register'])
        self.assertIsNone(rows[0]['port'])
        self.assertIsNone(rows[0]['source_event_id'])


if __name__ == '__main__':
    unittest.main()
