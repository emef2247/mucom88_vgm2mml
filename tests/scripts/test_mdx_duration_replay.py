"""Optional independent compiler/player evidence for MDX duration notation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
sys.path.insert(0, str(ROOT / 'scripts'))
from mdx_duration import timed
from mdx_compaction import compact
from opm_roundtrip import controls
from opm_mdx_structure import compare_hybrid
from verify_opm_mdx_roundtrip import generate


def old_timed(name, duration, held=False):
    parts = []
    while duration:
        count = min(duration, 65535 if name == 'r' else 256)
        duration -= count
        parts.append(f'{name}%{count}')
        if name != 'r' and (duration or held):
            parts.append('&')
    return ' '.join(parts)


class IdentitySamples:
    """The reference is already externally replayed at the target clock."""
    def __init__(self, analysis):
        self.writes = controls(analysis)
        self.source_end_vgmticks = analysis.source_end_vgmticks
        self.end_projected_vgmticks = analysis.source_end_vgmticks

    @staticmethod
    def mdx_tick(sample):
        return sample

    @staticmethod
    def projected_samples(sample):
        return sample

    @staticmethod
    def timing_report():
        return {'max_abs_timing_error_samples': 0}


class MdxDurationReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = ROOT / 'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator'
        if sys.platform == 'win32' or not os.access(cls.generator, os.X_OK):
            raise unittest.SkipTest('External Linux MDX compiler/player is unavailable; run in WSL')
        destination = os.environ.get('MDX_DURATION_REPLAY_OUTDIR')
        if destination:
            cls.output = Path(destination)
            cls.output.mkdir(parents=True, exist_ok=True)
        else:
            cls.temp = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.temp.cleanup)
            cls.output = Path(cls.temp.name)
        source = ROOT / 'tests/fixtures/public/opm/from_mdx/held_controls/reference/held_controls.mml'
        # Only the original MIT synthetic voices, not the reference score.
        cls.voices = source.read_text(encoding='utf-8').split('; Ampersands')[0]

    def check_replay(self, name, make_body):
        folder = self.output / name
        folder.mkdir(exist_ok=True)
        analyses = {}
        for spelling, formatter in [('old', old_timed), ('new', timed)]:
            body = make_body(formatter)
            if spelling == 'new':
                body, decisions = compact(body, 'A')
                (folder / 'compaction.json').write_text(json.dumps(decisions, indent=2) + '\n')
            mml = folder / (spelling + '.mml')
            mml.write_text(self.voices + '\nA t120 @0 v10 p3 o4 q8 ' + body + '\n', encoding='utf-8')
            analyses[spelling] = generate(self.generator, mml, folder, spelling)
        old, new = analyses['old'], analyses['new']
        report = compare_hybrid(IdentitySamples(old), old, new, initialization=[])
        keys = lambda analysis: [row for row in controls(analysis) if row[1] == 8]
        report['raw_key_writes_match'] = keys(old) == keys(new)
        report['raw_key_writes'] = keys(old)
        (folder / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertTrue(report['passed'], report)
        self.assertTrue(report['raw_key_writes_match'], report)

    def test_long_held_note_and_released_tail(self):
        self.check_replay('long_held', lambda fmt: fmt('c', 2048) + ' ' + fmt('r', 384))

    def test_controls_at_held_slice_boundaries(self):
        self.check_replay('controls', lambda fmt: ' '.join([
            fmt('c', 1024, held=True), 'D16 p1 @v90',
            fmt('c', 1536, held=True), 'y96,35 p2',
            fmt('c', 224), fmt('r', 384)]))

    def test_nested_finite_repeats_with_trailing_tie(self):
        self.check_replay('nested_ties', lambda fmt:
                          '[[' + fmt('c', 1536, held=True) + ']2]3 '
                          + fmt('c', 288) + ' ' + fmt('r', 192))

    def test_named_triplet_and_dotted_lengths(self):
        self.check_replay('named_lengths', lambda fmt: ' '.join(
            fmt('c', length) for length in (12, 24, 36, 32, 48, 72, 84, 128, 144, 192, 252, 288))
            + ' ' + fmt('r', 384))
