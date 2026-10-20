"""Public MSX source analysis inventory; no target compilation or private data."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from mucomsx_source import load_source
from vgm_timing import command_times


class PublicSourceTests(unittest.TestCase):
    def test_all_public_msx_source_directories(self):
        public = ROOT / 'tests/fixtures/public'
        checked = 0
        for group in ('psg', 'opll', 'psg_opll', 'psg_scc', 'psg_scc_opll', 'scc'):
            files = sorted(p for p in (public / group).rglob('*')
                           if p.suffix.lower() in ('.vgm', '.vgz'))
            self.assertTrue(files, group)
            for source in files:
                with self.subTest(source=str(source.relative_to(public))), tempfile.TemporaryDirectory() as tmp:
                    with contextlib.redirect_stdout(io.StringIO()):
                        bundle = load_source(source, tmp, song_loop=False)
                    self.assertEqual(bundle.end_sample, tuple(command_times(bundle.raw))[-1].vgmticks)
                    self.assertEqual(bundle.first_end_sample, bundle.end_sample)
                    usage = bundle.metadata['source_usage']
                    self.assertEqual(set(bundle.segments), set(usage['used_chips']))
                    self.assertTrue(Path(bundle.paths['commands']).is_file())
                    self.assertTrue(Path(bundle.paths['segments']).is_file())
                    for chip, channels in bundle.segments.items():
                        for ch, rows in channels.items():
                            for row in rows:
                                self.assertIsNotNone(row.vgmticks)
                                self.assertGreaterEqual(row.vgmticks_end, row.vgmticks)
                    if not bundle.header['scc_clock_raw'] and 'scc' in usage['used_chips']:
                        self.assertIn('scc', usage['compatibility_initialization'])
                        self.assertFalse(bundle.notes['scc'])
                    checked += 1
        self.assertGreaterEqual(checked, 60)


if __name__ == '__main__':
    unittest.main()
