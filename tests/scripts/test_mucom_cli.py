"""Native MUCOM88 CLI policy forwarding without source conversion."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mucom_cli_entry', ROOT / 'vgm2mml.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class MucomCliTests(unittest.TestCase):
    def test_native_normalization_default_explicit_and_off(self):
        for flags, enabled, milliseconds in (([], True, 8.0),
                (['--normalize-lengths', '--normalization-ms', '16'], True, 16.0),
                (['--no-normalize-lengths', '--normalization-ms', '4'], False, 4.0)):
            with self.subTest(flags=flags), patch('opna_muc.convert_vgm', return_value={
                    'muc_path': 'score.muc', 'musical_note_units': 1,
                    'timing': {'timer_b': 200}}) as converter, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(entry.main(['authored.vgm', '--outdir', 'outputs/test',
                    '--timing-bound-ms', '3', '--no-song-loop', *flags]), 0)
            self.assertEqual(converter.call_args.kwargs['normalize_lengths'], enabled)
            self.assertEqual(converter.call_args.kwargs['normalization_ms'], milliseconds)
            self.assertEqual(converter.call_args.kwargs['timing_bound_ms'], 3.0)
            self.assertFalse(converter.call_args.kwargs['song_loop'])

    def test_failed_optional_product_report_does_not_block_native_conversion(self):
        for flags in ([],['--score-normalize']):
            with self.subTest(flags=flags), patch('mucom_musical_products.prepare',
                    side_effect=PermissionError('locked musical_layer.json')), patch(
                    'opna_muc.convert_vgm',return_value={'muc_path':'score.muc',
                    'musical_note_units':1,'timing':{'timer_b':200}}) as converter, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if flags:
                    with self.assertRaises(SystemExit) as error:
                        entry.main(['authored.vgm',*flags])
                    self.assertEqual(error.exception.code,1)
                    converter.assert_not_called()
                else:
                    self.assertEqual(entry.main(['authored.vgm']),0)
                    converter.assert_called_once()

    def test_conflicting_normalization_flags_are_rejected(self):
        with patch('opna_muc.convert_vgm') as converter, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                entry.main(['authored.vgm', '--normalize-lengths', '--no-normalize-lengths'])
        self.assertEqual(error.exception.code, 2)
        converter.assert_not_called()

    def test_mdx_targets_are_rejected_before_inherited_dispatch(self):
        for target in ('mdx', 'opm', 'opm-additive'):
            with self.subTest(target=target), patch.object(entry, '_inherited_main') as inherited, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    entry.main(['unused.vgm', '--target', target])
                self.assertEqual(error.exception.code, 2)
                inherited.assert_not_called()

    def test_inherited_target_keeps_its_own_parser(self):
        with patch.object(entry, '_inherited_main', return_value=0) as inherited:
            self.assertEqual(entry.main(['authored.vgm', '--target', 'mgs',
                '--normalization-ms', '16', '--no-normalize-lengths']), 0)
        inherited.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
