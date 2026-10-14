"""Output-only batch export routing, continuation and stale-file handling."""
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
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'py')]
from export_mdx import inspect_export_commands, main, run_batch, tick_budget
from opm_mdx import mdx_tick


def vgm(commands=b'\x62', declared_samples=0):
    raw = bytearray(0x40)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x18, declared_samples)
    raw += commands + b'\x66'
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return bytes(raw)


def successful_run(command, **kwargs):
    if command[1] == str(ROOT / 'vgm2mml.py'):
        source = Path(command[2])
        output = Path(command[command.index('--outdir') + 1])
        (output / (source.stem + '.mdx.mml')).write_text('#title "export"\nA r4\n')
    elif command[1] == '--from-mdx':
        Path(command[3]).write_bytes(b'VGM')
    else:
        Path(command[2]).write_bytes(b'MDX')
        Path(command[3]).write_bytes(b'VGM')
    return subprocess.CompletedProcess(command, 0, '', '')


class ExportMdxTests(unittest.TestCase):
    def test_report_write_failure_does_not_change_successful_artifact_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            def compile_score(mml, mdx, **kwargs):
                mdx.write_bytes(b'compiled MDX')
            with patch('export_mdx.subprocess.run', side_effect=successful_run), \
                    patch('export_mdx.compile_mxc', side_effect=compile_score), \
                    patch('export_mdx.write_export_report', side_effect=OSError('report unavailable')):
                row = run_batch(source, out, generator=generator, no_vgm=True)[0]
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['report_status'], 'failed')
            self.assertIn('report unavailable', row['report_error'])
            self.assertTrue(row['mdx'])
            with patch('sys.argv', ['export_mdx.py', 'a.vgm', '--outdir', 'out']), \
                    patch('export_mdx.run_batch', return_value=[row]):
                self.assertEqual(main(), 1)

    def setUp(self):
        census = patch('export_mdx.inspect_export_commands', return_value='')
        census.start()
        self.addCleanup(census.stop)

    def test_census_inspects_final_mdx_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mdx, census = folder / 'a.mdx', folder / 'a.mdx.commands.csv'
            mdx.write_bytes(b'compiled score')
            def inspect(command, **kwargs):
                self.assertEqual(command, ['helper', '--inspect-commands', str(mdx), str(census)])
                census.write_text('track,index,kind,opcode_hex,operands_hex,ticks\n')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('export_mdx.subprocess.run', side_effect=inspect):
                self.assertEqual(inspect_export_commands('helper', mdx, census, 10), '')
            with patch('export_mdx.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'old helper')):
                self.assertIn('unavailable', inspect_export_commands('helper', mdx, census, 10))
            self.assertFalse(census.exists())
            self.assertEqual(mdx.read_bytes(), b'compiled score')

    def test_no_vgm_cli_forwards_export_choice(self):
        args = ['export_mdx.py', 'input.vgm', '--outdir', 'out', '--no-vgm']
        with patch('sys.argv', args), patch('export_mdx.run_batch', return_value=[dict(status='success')]) as call:
            self.assertEqual(main(), 0)
        self.assertTrue(call.call_args.kwargs['no_vgm'])

    def test_no_vgm_mxc_compiles_without_replay_and_removes_old_vgm(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            folder = out / 'tracks/a.vgm'
            folder.mkdir(parents=True)
            (folder / 'a.vgm').write_bytes(b'old replay')
            def compile_score(mml, mdx, **kwargs):
                mdx.write_bytes(b'native MDX')
            with patch('export_mdx.subprocess.run', side_effect=successful_run) as calls, \
                    patch('export_mdx.compile_mxc', side_effect=compile_score) as compile_call, \
                    patch('export_mdx.tick_budget', side_effect=AssertionError('Replay was requested')):
                row = run_batch(source, out, generator=generator, no_vgm=True)[0]
            compile_call.assert_called_once()
            self.assertEqual(calls.call_count, 1)
            self.assertEqual(row['status'], 'success')
            self.assertTrue(row['mml'] and row['mdx'])
            self.assertFalse(row['vgm'] or row['max_ticks'])
            self.assertFalse((folder / 'a.vgm').exists())

    def test_no_vgm_mmlx_uses_compile_only_and_requires_nonempty_mdx(self):
        for produced in (True, False):
            with self.subTest(produced=produced), tempfile.TemporaryDirectory() as tmp:
                source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
                def run(command, **kwargs):
                    if command[1] == str(ROOT / 'vgm2mml.py'):
                        return successful_run(command, **kwargs)
                    self.assertEqual(command[1], '--compile-only')
                    self.assertEqual(len(command), 4)
                    if produced:
                        Path(command[3]).write_bytes(b'MDX')
                    return subprocess.CompletedProcess(command, 0, '', '')
                with patch('export_mdx.subprocess.run', side_effect=run):
                    row = run_batch(source, out, generator=generator, compiler='mmlx', no_vgm=True)[0]
                self.assertEqual(row['status'], 'success' if produced else 'compilation_failed')
                self.assertFalse(row['vgm'])

    def test_no_vgm_pcm_keeps_typed_pair_and_assessment_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('pcm.vgm',))
            def run(command, **kwargs):
                self.assertEqual(command[1], str(ROOT / 'vgm2mml.py'))
                successful_run(command, **kwargs)
                folder = Path(command[command.index('--outdir') + 1])
                (folder / 'pcm.mdx').write_bytes(b'typed PCM MDX')
                (folder / 'pcm.pdx').write_bytes(b'PDX')
                (folder / 'pcm.pcm.assessment.json').write_text(json.dumps(dict(
                    policy='best-effort', assessment_status='lossy', validation_status='unverified',
                    validation_run='not_run', artifact_status='generated', known_losses=[{}])))
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('export_mdx.subprocess.run', side_effect=run) as calls, \
                    patch('export_mdx.compile_mxc') as compile_call:
                row = run_batch(source, out, generator=generator, no_vgm=True,
                                pcm_policy='best-effort')[0]
            self.assertEqual(calls.call_count, 1)
            compile_call.assert_not_called()
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['pcm_projection_status'], 'lossy')
            self.assertEqual(row['pcm_validation_status'], 'unverified')
            self.assertTrue(row['mml'] and row['mdx'] and row['pdx'])
            self.assertFalse(row['vgm'])
            self.assertEqual((out / row['mdx']).read_bytes(), b'typed PCM MDX')

    def test_missing_typed_pcm_mdx_never_compiles_readable_pcm_mml(self):
        for no_vgm in (False, True):
            with self.subTest(no_vgm=no_vgm), tempfile.TemporaryDirectory() as tmp:
                source, out, generator = self.prepare(Path(tmp), ('pcm.vgm',))
                def run(command, **kwargs):
                    successful_run(command, **kwargs)
                    folder = Path(command[command.index('--outdir') + 1])
                    (folder / 'pcm.pdx').write_bytes(b'PDX')
                    return subprocess.CompletedProcess(command, 0, '', '')
                with patch('export_mdx.subprocess.run', side_effect=run) as calls, \
                        patch('export_mdx.compile_mxc') as compile_call:
                    row = run_batch(source, out, generator=generator, no_vgm=no_vgm)[0]
                self.assertEqual(calls.call_count, 1)
                compile_call.assert_not_called()
                self.assertEqual(row['status'], 'generation_failed')
                self.assertIn('complete typed PCM MDX/PDX pair', row['detail'])
                self.assertFalse(row['mdx'] or row['vgm'])

    def test_normalization_choice_is_forwarded_without_resolving_the_default(self):
        for requested, flag in [(None, None), (True, '--normalize-lengths'), (False, '--no-normalize-lengths')]:
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as temporary:
                source, out, generator = self.prepare(Path(temporary), ('a.vgm',))
                with patch('export_mdx.subprocess.run', side_effect=successful_run) as run:
                    row = run_batch(source, out, generator=generator, compiler='mmlx',
                                    normalize_lengths=requested, normalization_ms=16)[0]
                self.assertEqual(row['status'], 'success')
                command = run.call_args_list[0].args[0]
                self.assertEqual(command[command.index('--normalization-ms') + 1], '16')
                if flag:
                    self.assertIn(flag, command)
                else:
                    self.assertNotIn('--normalize-lengths', command)
                    self.assertNotIn('--no-normalize-lengths', command)

    def test_default_mxc_compiles_then_replays_existing_mdx(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            def compile_score(mml, mdx, **kwargs):
                self.assertTrue(mml.is_file())
                self.assertEqual(kwargs['generator'], generator)
                mdx.write_bytes(b'native MDX')
                prepared = kwargs['prepared_output']
                prepared.parent.mkdir(parents=True)
                prepared.write_bytes(b'A r4\r\n')
            def run(command, **kwargs):
                if command[1] == str(ROOT / 'vgm2mml.py'):
                    return successful_run(command, **kwargs)
                self.assertEqual(command[1], '--from-mdx')
                self.assertEqual(Path(command[2]).read_bytes(), b'native MDX')
                Path(command[3]).write_bytes(b'VGM')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('export_mdx.subprocess.run', side_effect=run), \
                    patch('export_mdx.compile_mxc', side_effect=compile_score) as compile_call:
                row = run_batch(source, out, generator=generator)[0]
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['compiler'], 'mxc')
            self.assertEqual(row['compiler_input'], '_compiler_inputs/a.vgm.mxc.mml')
            compile_call.assert_called_once()
            with (out / 'results.csv').open(encoding='utf-8', newline='') as stream:
                self.assertEqual(list(csv.DictReader(stream))[0]['compiler'], 'mxc')

    def test_mxc_failure_clears_stale_binary_and_keeps_current_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            folder = out / 'tracks/a.vgm'
            folder.mkdir(parents=True)
            stale_input = out / '_compiler_inputs/a.vgm.mxc.mml'
            stale_input.parent.mkdir()
            stale_input.write_bytes(b'old prepared score')
            for suffix in ('.mdx', '.vgm'):
                (folder / ('a' + suffix)).write_bytes(b'stale')
            with patch('export_mdx.subprocess.run', side_effect=successful_run) as calls, \
                    patch('export_mdx.compile_mxc', side_effect=ValueError('Missing --mxc tool')):
                row = run_batch(source, out, generator=generator)[0]
            self.assertEqual(row['status'], 'compilation_failed')
            self.assertTrue(row['mml'])
            self.assertFalse(row['mdx'] or row['vgm'])
            self.assertEqual(calls.call_count, 1)
            self.assertFalse(stale_input.exists())
            self.assertFalse(row['compiler_input'])

    def test_mxc_replay_failure_preserves_validated_mdx(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            def compile_score(mml, mdx, **kwargs):
                mdx.write_bytes(b'native MDX')
            def run(command, **kwargs):
                if command[1] == str(ROOT / 'vgm2mml.py'):
                    return successful_run(command, **kwargs)
                return subprocess.CompletedProcess(command, 1, '', 'replay failed')
            with patch('export_mdx.subprocess.run', side_effect=run), \
                    patch('export_mdx.compile_mxc', side_effect=compile_score):
                row = run_batch(source, out, generator=generator)[0]
            self.assertEqual(row['status'], 'replay_failed')
            self.assertTrue(row['mdx'])
            self.assertFalse(row['vgm'])

    def test_mxc_timeout_keeps_diagnostic_and_skips_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            error = subprocess.TimeoutExpired(['run68'], 17, output=b'partial compiler output')
            with patch('export_mdx.subprocess.run', side_effect=successful_run) as calls, \
                    patch('export_mdx.compile_mxc', side_effect=error):
                row = run_batch(source, out, generator=generator, timeout=17)[0]
            self.assertEqual(row['status'], 'compilation_timeout')
            self.assertIn('partial compiler output', (out / row['error_log']).read_text())
            self.assertEqual(calls.call_count, 1)
            self.assertFalse(row['mdx'] or row['vgm'])

    def prepare(self, root, names=('a.vgm', 'b.vgm')):
        source, output, generator = root / 'input', root / 'out', root / 'generator'
        source.mkdir()
        generator.write_bytes(b'external tool')
        for name in names:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(vgm()) if path.suffix.lower() == '.vgz' else vgm())
        return source, output, generator

    def test_recursive_exports_three_files_and_forwards_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('nested space/曲.vgm', 'nested space/曲.vgz'))
            error = out / '_errors/nested space/曲.vgm.log'
            error.parent.mkdir(parents=True)
            error.write_text('old failure')
            with patch('export_mdx.subprocess.run', side_effect=successful_run) as run:
                rows = run_batch(source, out, target='opm', generator=generator, compiler="mmlx",
                                 psg_model='fm', psg_gain=.5, scc_gain=.25,
                                 opm_pitch_policy='clamp')
            self.assertEqual([r['status'] for r in rows], ['success', 'success'])
            self.assertEqual(run.call_count, 4)
            self.assertTrue(all(row['compiler_input'] == row['mml'] for row in rows))
            self.assertFalse(error.exists())
            for name in ('曲.vgm', '曲.vgz'):
                folder = out / 'tracks/nested space' / name
                self.assertEqual(sorted(p.name for p in folder.iterdir()),
                                 ['曲.mdx', '曲.mdx.mml', '曲.report.txt', '曲.vgm'])
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0][commands[0].index('--target') + 1], 'opm')
            self.assertIn('--psg-model', commands[0])
            self.assertEqual(commands[0][commands[0].index('--psg-gain') + 1], '0.5')
            self.assertNotIn('--dump-passes', commands[0])
            self.assertEqual(commands[1][-2:], ['--max-ticks', str(mdx_tick(735) + 2)])
            self.assertTrue(all('verify' not in Path(command[0]).name for command in commands))
            with (out / 'results.csv').open(encoding='utf-8', newline='') as stream:
                saved = list(csv.DictReader(stream))
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0]['vgm'], rows[0]['vgm'])

    def test_failed_conversion_continues_and_removes_only_stale_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp))
            folder = out / 'tracks/a.vgm'
            folder.mkdir(parents=True)
            for suffix in ('.mdx.mml', '.mdx', '.vgm', '.pdx'):
                (folder / ('a' + suffix)).write_bytes(b'stale')
            (folder / 'keep.txt').write_text('keep')
            def run(command, **kwargs):
                if command[1] == str(ROOT / 'vgm2mml.py') and Path(command[2]).name == 'a.vgm':
                    return subprocess.CompletedProcess(command, 2, '', 'noise unsupported')
                return successful_run(command, **kwargs)
            with patch('export_mdx.subprocess.run', side_effect=run):
                rows = run_batch(source, out, target='opm', generator=generator, compiler="mmlx")
            self.assertEqual([r['status'] for r in rows], ['conversion_failed', 'success'])
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['a.report.txt', 'keep.txt'])
            self.assertIn('noise unsupported', (out / rows[0]['error_log']).read_text())
            self.assertEqual(rows[0]['vgm'], '')
            self.assertEqual(rows[0]['pdx'], '')

    def test_pcm_export_reports_fourth_artifact_and_selected_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('pcm.vgm', 'fm.vgm'))
            def run(command, **kwargs):
                result = successful_run(command, **kwargs)
                if command[1] == str(ROOT / 'vgm2mml.py') and Path(command[2]).stem == 'pcm':
                    folder = Path(command[command.index('--outdir') + 1])
                    (folder / 'pcm.pdx').write_bytes(b'current encoded sample package')
                    (folder / 'pcm.mdx').write_bytes(b'typed PCM MDX')
                return result
            with patch('export_mdx.subprocess.run', side_effect=run) as calls:
                rows = run_batch(source, out, generator=generator, compiler="mmlx")
            self.assertEqual([row['status'] for row in rows], ['success', 'success'])
            self.assertEqual(rows[0]['pdx'], '')
            self.assertTrue(rows[1]['pdx'].endswith('pcm.pdx'))
            converters = [call.args[0] for call in calls.call_args_list
                          if call.args[0][1] == str(ROOT / 'vgm2mml.py')]
            for command in converters:
                self.assertEqual(command[command.index('--pcm-generator') + 1], str(generator))
            playback = [call.args[0] for call in calls.call_args_list if call.args[0][0] == str(generator)]
            self.assertNotIn('--pcm-mode', playback[0])
            self.assertEqual(playback[1][1], '--from-mdx')
            self.assertNotIn('--pcm-mode', playback[1])
            with (out / 'results.csv').open(encoding='utf-8', newline='') as stream:
                self.assertEqual(list(csv.DictReader(stream))[1]['pdx'], rows[1]['pdx'])

    def test_pcm_replay_limit_keeps_compiled_pair_and_concrete_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('pcm.vgm',))
            def run(command, **kwargs):
                if command[1] == str(ROOT / 'vgm2mml.py'):
                    successful_run(command, **kwargs)
                    folder = Path(command[command.index('--outdir') + 1])
                    (folder / 'pcm.pdx').write_bytes(b'PDX')
                    (folder / 'pcm.mdx').write_bytes(b'typed PCM MDX')
                    return subprocess.CompletedProcess(command, 0, '', '')
                self.assertEqual(command[1], '--from-mdx')
                return subprocess.CompletedProcess(command, 1, '',
                                                   'PCM replay unavailable: pinned helper limitation')
            with patch('export_mdx.subprocess.run', side_effect=run):
                row = run_batch(source, out, generator=generator)[0]
            self.assertEqual(row['status'], 'pcm_replay_unavailable')
            self.assertTrue(row['mml'] and row['mdx'] and row['pdx'])
            self.assertEqual(row['vgm'], '')
            self.assertIn('PCM replay unavailable:', (out / row['error_log']).read_text())

    def test_canonical_pcm_pair_is_replayed_without_recompiling_readable_pcm(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('pcm.vgm',))
            def run(command, **kwargs):
                if command[1] == str(ROOT / 'vgm2mml.py'):
                    successful_run(command, **kwargs)
                    folder = Path(command[command.index('--outdir') + 1])
                    (folder / 'pcm.pdx').write_bytes(b'PDX')
                    (folder / 'pcm.mdx').write_bytes(b'typed PCM MDX')
                    (folder / 'pcm.pcm.assessment.json').write_text(json.dumps(dict(
                        policy='best-effort', assessment_status='lossy', validation_status='unverified',
                        validation_run='not_run', artifact_status='generated', known_losses=[{}])))
                    return subprocess.CompletedProcess(command, 0, '', '')
                self.assertEqual(command[1], '--from-mdx')
                self.assertEqual(Path(command[2]).read_bytes(), b'typed PCM MDX')
                return subprocess.CompletedProcess(command, 1, '', 'PCM replay unavailable: guarded')
            with patch('export_mdx.subprocess.run', side_effect=run) as calls:
                row = run_batch(source, out, generator=generator, pcm_policy='best-effort')[0]
            self.assertEqual(calls.call_args_list[0].args[0][-2:], ['--pcm-policy', 'best-effort'])
            self.assertEqual(row['status'], 'pcm_replay_unavailable')
            self.assertEqual(row['pcm_projection_status'], 'lossy')
            self.assertEqual(row['pcm_validation_status'], 'unverified')
            self.assertEqual(row['pcm_validation_run'], 'not_run')
            self.assertEqual(row['pcm_known_losses'], 1)
            self.assertTrue(row['mml'] and row['mdx'] and row['pdx'] and row['pcm_assessment'])
            self.assertFalse(row['vgm'])

    def test_blocked_strict_result_has_loss_diagnostics_and_skips_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('pcm.vgm',))
            def run(command, **kwargs):
                folder = Path(command[command.index('--outdir') + 1])
                (folder / 'pcm.pcm.assessment.json').write_text(json.dumps(dict(
                    policy='strict', assessment_status='lossy', validation_status='unverified',
                    validation_run='not_run', artifact_status='blocked', known_losses=[{}])))
                return subprocess.CompletedProcess(command, 2, '', 'known held-pan loss')
            with patch('export_mdx.subprocess.run', side_effect=run) as calls:
                row = run_batch(source, out, generator=generator)[0]
            self.assertEqual(calls.call_count, 1)
            self.assertEqual(row['status'], 'pcm_projection_blocked')
            self.assertEqual(row['pcm_projection_status'], 'lossy')
            self.assertEqual(row['pcm_validation_status'], 'unverified')
            self.assertEqual(row['pcm_known_losses'], 1)
            self.assertTrue(row['pcm_assessment'])
            self.assertFalse(row['mdx'] or row['pdx'])

    def test_failed_generation_retains_current_mml_and_partial_mdx(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp))
            def run(command, **kwargs):
                if command[0] == str(generator) and Path(command[1]).name == 'a.mdx.mml':
                    Path(command[2]).write_bytes(b'compiled MDX')
                    return subprocess.CompletedProcess(command, 1, '', 'playback failed')
                return successful_run(command, **kwargs)
            with patch('export_mdx.subprocess.run', side_effect=run):
                rows = run_batch(source, out, generator=generator, compiler="mmlx", max_ticks=2000000)
            self.assertEqual([r['status'] for r in rows], ['generation_failed', 'success'])
            self.assertTrue(rows[0]['mml'])
            self.assertTrue(rows[0]['mdx'])
            self.assertEqual(rows[0]['vgm'], '')
            self.assertEqual(rows[1]['max_ticks'], 2000000)

    def test_timeouts_are_distinct_and_keep_partial_log(self):
        for stage in ('conversion', 'generation'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                source, out, generator = self.prepare(Path(tmp))
                def run(command, **kwargs):
                    converting = command[1] == str(ROOT / 'vgm2mml.py')
                    is_first = Path(command[2] if converting else command[1]).name.startswith('a.')
                    if is_first and converting == (stage == 'conversion'):
                        raise subprocess.TimeoutExpired(command, 17, output=b'partial stdout',
                                                        stderr=b'partial stderr')
                    return successful_run(command, **kwargs)
                with patch('export_mdx.subprocess.run', side_effect=run):
                    rows = run_batch(source, out, generator=generator, compiler="mmlx", timeout=17)
                self.assertEqual([r['status'] for r in rows], [stage + '_timeout', 'success'])
                log = (out / rows[0]['error_log']).read_text()
                self.assertIn('partial stdout', log)
                self.assertIn('partial stderr', log)

    def test_zero_exit_without_outputs_is_not_success(self):
        for stage in ('conversion', 'generation'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
                def run(command, **kwargs):
                    converting = command[1] == str(ROOT / 'vgm2mml.py')
                    if converting == (stage == 'conversion'):
                        return subprocess.CompletedProcess(command, 0, '', '')
                    return successful_run(command, **kwargs)
                with patch('export_mdx.subprocess.run', side_effect=run):
                    row = run_batch(source, out, generator=generator, compiler="mmlx")[0]
                self.assertEqual(row['status'], stage + '_failed')

    def test_setup_errors_do_not_launch_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            cases = ((source, source), (source, source / 'out'), (source, source.parent),
                     (source / 'a.vgm', source))
            with patch('export_mdx.subprocess.run') as run:
                for src, dest in cases:
                    with self.subTest(src=src, dest=dest), self.assertRaises(ValueError):
                        run_batch(src, dest, generator=generator, compiler="mmlx")
                with self.assertRaisesRegex(ValueError, 'Build'):
                    run_batch(source, out, generator=Path(tmp) / 'missing')
                for value in (0, -1, 0x100000000):
                    with self.assertRaises(ValueError):
                        run_batch(source, out, generator=generator, compiler="mmlx", max_ticks=value)
                run.assert_not_called()
            self.assertFalse(out.exists())

    def test_budget_uses_decoded_waits_not_header_or_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.vgm'
            payload = b'\x61\xff\xff\x66'
            commands = (b'\x67\x66\x00' + struct.pack('<I', len(payload)) + payload
                        + b'\x61\x88\x13\x64\x62\x03\x00\x62\x7a')
            source.write_bytes(vgm(commands, declared_samples=0xffffffff))
            self.assertEqual(tick_budget(source), mdx_tick(5000 + 3 + 11) + 2)

    def test_results_symlink_cannot_overwrite_outside_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, out, generator = self.prepare(root, ('a.vgm',))
            outside = root / 'keep.csv'
            outside.write_text('keep unchanged')
            out.mkdir()
            try:
                (out / 'results.csv').symlink_to(outside)
            except OSError as error:
                self.skipTest(f'Symlink creation unavailable: {error}')
            with patch('export_mdx.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError, 'leaves'):
                    run_batch(source, out, generator=generator, compiler="mmlx")
                run.assert_not_called()
            self.assertEqual(outside.read_text(), 'keep unchanged')
            self.assertFalse((out / 'tracks').exists())

    def test_prepared_input_symlink_cannot_overwrite_outside_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, out, generator = self.prepare(root, ('a.vgm',))
            outside = root / 'keep.mml'
            outside.write_text('keep unchanged')
            prepared = out / '_compiler_inputs/a.vgm.mxc.mml'
            prepared.parent.mkdir(parents=True)
            try:
                prepared.symlink_to(outside)
            except OSError as error:
                self.skipTest(f'Symlink creation unavailable: {error}')
            with patch('export_mdx.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError, 'leaves'):
                    run_batch(source, out, generator=generator)
                run.assert_not_called()
            self.assertEqual(outside.read_text(), 'keep unchanged')

    def test_single_file_cli_returns_nonzero_for_any_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out, generator = self.prepare(Path(tmp), ('a.vgm',))
            args = ['export_mdx.py', str(source / 'a.vgm'), '--outdir', str(out),
                    '--generator', str(generator), '--compiler', 'mmlx', '--target', 'opm']
            failure = subprocess.CompletedProcess([], 1, '', 'unsupported')
            with patch('sys.argv', args), patch('export_mdx.subprocess.run', return_value=failure):
                self.assertEqual(main(), 1)


if __name__ == '__main__':
    unittest.main()
