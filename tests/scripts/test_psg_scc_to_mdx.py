"""Diagnostic batch reporting accepts the structured comparison schema."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import psg_scc_to_mdx


class DiagnosticBatchTests(unittest.TestCase):
    def test_structured_report_without_legacy_expected_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, helper, output = root / 'input.vgm', root / 'helper', root / 'output'
            source.write_bytes(b'input')
            helper.write_bytes(b'helper')
            report = dict(passed=True, comparison='projected_musical_opm', source_controls=10,
                          returned_controls=30, key_commands_match=True, known_state_mismatches=0)
            args = ['psg_scc_to_mdx.py', str(source), '--outdir', str(output), '--generator', str(helper)]
            with patch.object(sys, 'argv', args), patch.object(psg_scc_to_mdx, 'convert', return_value=(output / 'input.mdx.mml', object())), \
                    patch.object(psg_scc_to_mdx, 'compile_and_verify', return_value=report), redirect_stdout(io.StringIO()):
                psg_scc_to_mdx.main()
            result = json.loads((output / 'results.json').read_text())[0]
            self.assertEqual(result['status'], 'verified')
            self.assertIn('state/Key', result['detail'])
            self.assertIn('not acoustic equivalence', result['detail'])


if __name__ == '__main__':
    unittest.main()
