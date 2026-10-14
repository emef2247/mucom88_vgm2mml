"""Optional external replay checks for relative MDX setter compaction."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
sys.path.insert(0, str(ROOT / 'scripts'))
from mdx_compaction import compact
from opm_mdx_structure import compare_hybrid
from opm_roundtrip import controls
from verify_opm_mdx_roundtrip import generate
from test_mdx_duration_replay import IdentitySamples


class MdxRelativeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = ROOT / 'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator'
        if sys.platform == 'win32' or not os.access(cls.generator, os.X_OK):
            raise unittest.SkipTest('External Linux MDX compiler/player is unavailable; run in WSL')
        destination = os.environ.get('MDX_RELATIVE_REPLAY_OUTDIR')
        if destination:
            cls.output = Path(destination)
            cls.output.mkdir(parents=True, exist_ok=True)
        else:
            cls.temp = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.temp.cleanup)
            cls.output = Path(cls.temp.name)
        source = ROOT / 'tests/fixtures/public/opm/from_mdx/held_controls/reference/held_controls.mml'
        cls.voices = source.read_text(encoding='utf-8').split('; Ampersands')[0]

    def check_replay(self, name, body, *, octave=False, volume=False):
        folder = self.output / name
        folder.mkdir(exist_ok=True)
        changed, decisions = compact(body, 'A', durations=False)
        (folder / 'compaction.json').write_text(json.dumps(decisions, indent=2) + '\n')
        if octave:
            self.assertTrue('<' in changed or '>' in changed, changed)
        if volume:
            self.assertTrue('(' in changed or ')' in changed, changed)
        analyses = {}
        for spelling, commands in [('absolute', body), ('relative', changed)]:
            mml = folder / (spelling + '.mml')
            mml.write_text(self.voices + '\nA t120 @0 p3 q8 ' + commands + '\n', encoding='utf-8')
            analyses[spelling] = generate(self.generator, mml, folder, spelling)
        source, actual = analyses['absolute'], analyses['relative']
        report = compare_hybrid(IdentitySamples(source), source, actual, initialization=[])
        keys = lambda analysis: [row for row in controls(analysis) if row[1] == 8]
        report['raw_key_writes_match'] = keys(source) == keys(actual)
        report['raw_key_writes'] = keys(source)
        (folder / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertTrue(report['passed'], report)
        self.assertTrue(report['raw_key_writes_match'], report)

    def test_initial_single_double_and_large_octave_steps(self):
        self.check_replay('octaves',
                          'v10 o4 c8 o5 c8 o3 c8 o7 c8 o6 c8 o4 c8 r4', octave=True)

    def test_coarse_volume_steps_and_limits(self):
        self.check_replay('coarse_volume',
                          'o4 v0 c8 v1 c8 v3 c8 v15 c8 v14 c8 v12 c8 v0 c8 r4', volume=True)

    def test_fine_volume_steps_and_limits(self):
        self.check_replay('fine_volume',
                          'o4 @v0 c8 @v1 c8 @v3 c8 @v127 c8 @v126 c8 @v124 c8 @v0 c8 r4',
                          volume=True)

    def test_nested_repeats_with_different_terminal_setters(self):
        self.check_replay('nested_entries',
                          'o4 @v90 c8 [o3 @v80 c8 [o4 @v81 c8 o5 @v83 c8]2 '
                          'o6 @v85 c8]3 o7 @v86 c8 r4', octave=True, volume=True)

    def test_mixed_coarse_and_fine_setters(self):
        self.check_replay('mixed_volume',
                          'o4 v10 c8 v11 c8 @v100 c8 @v101 c8 v12 c8 v10 c8 '
                          '@v102 c8 @v100 c8 r4', volume=True)

    def test_raw_voice_changes_and_held_setters(self):
        self.check_replay('held_raw_voice',
                          'o4 @v100 c8 & @v101 c8 & o5 @v103 c8 & '
                          'y96,35 y32,199 @0 @v104 o4 c8 & @v105 c8 & '
                          '@1 @v106 c8 & @v107 c8 r4', octave=True, volume=True)
