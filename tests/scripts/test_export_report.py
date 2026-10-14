"""Meaningful output reports distinguish physical requests, edges and byte fidelity."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'py')]
from export_report import source_statistics, write_export_report


def source(path, commands):
    raw = bytearray(64)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    raw += commands + b'\x66'
    path.write_bytes(raw)
    return path


def report_text(path):
    return ' '.join(path.read_text().split())


class ExportReportTests(unittest.TestCase):
    def test_encoded_note_census_is_distinct_from_source_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.mdx').write_bytes(b'current compiled artifact')
            (folder / 'a.mdx.commands.csv').write_text(
                'track,index,kind,opcode_hex,operands_hex,ticks\n'
                'A,0,Note,a0,03,4\nP,0,Note,80,03,4\n'
                'A,1,KeyOffDisable,f7,,0\nA,2,OpmRegisterWrite,fe,0878,0\n'
                'A,3,OpmRegisterWrite,fe,0800,0\n')
            text = report_text(write_export_report(path, folder, 'a', dict(status='success')))
            self.assertIn('FM notes 1; PCM notes 1; holds 1', text)
            self.assertIn('requests 2 (On 1, Off 1)', text)
            self.assertIn('counts are not source Key-On/Off equivalence', text)

    def test_repeated_key_requests_are_not_repeated_operator_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = source(Path(tmp) / 'a.vgm', b'\x54\x08\x78' * 2 + b'\x54\x08\x00\x62')
            stats = source_statistics(path)
            self.assertEqual((stats['on'], stats['off'], stats['rising'], stats['falling']), (2, 1, 4, 4))
            self.assertEqual(stats['samples'], 735)
            self.assertEqual(stats['opm_writes'], 3)

    def test_selected_clock_is_nominal_not_runtime_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.mdx.normalization.json').write_text(json.dumps(dict(
                status='unchanged', reason='test', selected=dict(tick_microseconds=8192, tempo_byte=224))))
            text = report_text(write_export_report(path, folder, 'a', dict(status='success')))
            self.assertIn('8192 us/tick; tempo byte 224', text)
            self.assertIn('not measured driver interrupt load', text)

    def test_pdx_payload_and_frequency_checks_do_not_claim_runtime_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.pcm/samples').mkdir(parents=True)
            sample = b'\x12\x34\x56'
            (folder / 'a.pcm/samples/000.adpcm').write_bytes(sample)
            pdx = bytearray(768)
            struct.pack_into('>II', pdx, 0, 768, len(sample))
            (folder / 'a.pdx').write_bytes(pdx + sample)
            (folder / 'a.pcm_bindings.csv').write_text('sample_id,bank,slot,file\n0,0,0,samples/000.adpcm\n')
            (folder / 'a.pcm_projection.csv').write_text('mdx_frequency,rate_num,rate_den\n0,15625,4\n4,15625,1\n')
            (folder / 'a.pcm.assessment.json').write_text(json.dumps(dict(
                artifact_status='generated', policy='best-effort', assessment_status='lossy',
                validation_status='unverified', validation_run='not_run',
                known_losses=[dict(code='held_pan_latched')] * 100,
                unverified_items=[dict(code='runtime_not_run')], unexpected_mismatches=[])))
            row = dict(status='success', compiler='typed_pcm_mmlx')
            text = report_text(write_export_report(path, folder, 'a', row))
            self.assertIn('1/1 allocated samples byte-exact; 3 encoded bytes; PDX 771 bytes', text)
            self.assertIn('2/2 playback spans exact', text)
            self.assertIn('held_pan_latched x100', text)
            self.assertIn('runtime unverified (not_run)', text)
            self.assertIn('PSG has no OPM Key-On signal', text)
            (folder / 'a.pdx').write_bytes(pdx + b'wrong')
            text = report_text(write_export_report(path, folder, 'a', row))
            self.assertIn('0/1 allocated samples byte-exact', text)

    def test_blocked_export_discards_stale_projection_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.pcm.assessment.json').write_text(json.dumps(dict(
                artifact_status='blocked', block_reasons=['unsupported source'], known_losses=[])))
            (folder / 'a.pcm_projection.csv').write_text('mdx_frequency,rate_num,rate_den\n0,15625,4\n')
            (folder / 'a.pcm.timing.json').write_text(json.dumps(dict(pcm_playback_count=999,
                                                                   pcm_max_abs_timing_error_samples=999)))
            (folder / 'a.pcm_bindings.csv').write_text('not current')
            (folder / 'a.pdx').write_bytes(b'stale PDX')
            (folder / 'a.mdx').write_bytes(b'stale MDX')
            (folder / 'a.mdx.commands.csv').write_text('track,kind\nA,Note\n')
            (folder / 'a.mdx.normalization.json').write_text(json.dumps(dict(status='applied')))
            text = report_text(write_export_report(path, folder, 'a', dict(status='pcm_projection_blocked')))
            self.assertIn('Blocked: unsupported source', text)
            self.assertIn('PCM frequency mapping: unmeasured', text)
            self.assertNotIn('1/1 playback spans exact', text)
            self.assertNotIn('999', text)
            self.assertNotIn('Note normalization: applied', text)
            self.assertNotIn('FM notes', text)
            self.assertIn('no current generated PCM pair', text)

    def test_conversion_failure_ignores_stale_target_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.conversion.json').write_text(json.dumps(dict(chip_projection='stale-route')))
            (folder / 'a.pcm.assessment.json').write_text(json.dumps(dict(artifact_status='generated')))
            (folder / 'a.mdx.normalization.json').write_text(json.dumps(dict(status='applied')))
            text = report_text(write_export_report(path, folder, 'a', dict(status='conversion_failed')))
            self.assertNotIn('stale-route', text)
            self.assertNotIn('Note normalization: applied', text)
            self.assertIn('PCM assessment: not available', text)

    def test_optional_malformed_diagnostics_do_not_prevent_txt_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.mdx.normalization.json').write_text('malformed')
            (folder / 'a.pcm.assessment.json').write_text(json.dumps(dict(artifact_status='generated')))
            (folder / 'a.pcm_projection.csv').write_text('mdx_frequency,rate_num,rate_den\n0,12,0\n')
            (folder / 'a.pcm_bindings.csv').write_text('sample_id,bank,slot,file\n0,0,0,missing.adpcm\n')
            (folder / 'a.pdx').write_bytes(b'PDX')
            text = report_text(write_export_report(path, folder, 'a', dict(status='success')))
            self.assertIn('Export: success', text)
            self.assertIn('Normalization: unavailable', text)
            self.assertIn('PCM frequency mapping: unavailable', text)
            self.assertIn('PCM payload: unavailable', text)

    def test_missing_conversion_diagnostics_still_produces_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / 'invalid.vgm'
            path.write_bytes(b'invalid')
            text = report_text(write_export_report(path, folder, 'invalid', dict(status='conversion_failed', detail='bad')))
            self.assertIn('Source statistics: unavailable', text)
            self.assertIn('Export: conversion_failed', text)

    def test_plain_text_blocks_wrap_fields_and_explain_rejected_clocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = source(folder / 'a.vgm', b'\x62')
            (folder / 'a.mdx.normalization.json').write_text(json.dumps(dict(
                status='applied', reason='bounded target clock', normalization_ms=12,
                short_note_policy=dict(omitted_gate_count=2),
                selected=dict(tick_microseconds=8192, tempo_byte=224),
                rejected_clock_candidates=[dict(multiplier=65, max_abs_error_samples=360,
                    collapsed_protected_intervals=7,
                    examples=[dict(kind='opm_gate'), dict(kind='opm_gate'), dict(kind='side_effect')])])))
            text = write_export_report(path, folder, 'a', dict(status='success')).read_text()
            self.assertIn('Export summary\n\n  Input: a.vgm\n', text)
            self.assertIn('\n\nSource observations\n\n', text)
            self.assertIn('\n\nOutput normalization\n\n', text)
            self.assertNotIn('#', text)
            self.assertTrue(all(len(line) <= 100 for line in text.splitlines()))
            self.assertIn('    tempo byte 224;\n    MML @t224;', text)
            self.assertIn('Short-note omission (<=12 ms)', text)
            self.assertIn('collapsed protected intervals 7', text)
            self.assertIn('example kinds: opm_gate x2, side_effect x1', text)
            self.assertIn('sampled examples, not total kind counts', ' '.join(text.split()))


if __name__ == '__main__':
    unittest.main()
