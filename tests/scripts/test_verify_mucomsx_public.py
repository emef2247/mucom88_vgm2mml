"""Inventory and mocked stage orchestration; no emulator/private media use."""
from collections import Counter
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_mucomsx_public as harness


def usage(source):
    return dict(used_chips=['psg'], unsupported_commands=[])


def conversion(source, output, **options):
    output.mkdir(parents=True)
    muc = output / (Path(source).stem + '.muc')
    muc.write_text('MSXD c4\n')
    return dict(status='generated', muc_path=str(muc), timing={}, routing=[])


def native(muc, output, **options):
    output.mkdir(parents=True)
    target = output / 'CHECK.VGM'
    target.write_bytes(b'authored mock export')
    return dict(status='passed', files={'CHECK.VGM':dict(path=str(target))})


def comparison(source, target, projection):
    return dict(checked_intervals=3, mismatched_intervals=0, opna_projection_exclusions=[])


class PublicHarnessTests(unittest.TestCase):
    def fixtures(self, root, names=('one', 'two')):
        result = []
        for name in names:
            path = root / (name + '.vgm')
            raw = bytearray(64)
            raw[:4] = b'Vgm '
            raw.extend(b'\x62\x66')
            path.write_bytes(raw)
            result.append(harness.Fixture(path, 'psg/' + path.name, 'psg'))
        return result

    def test_inventory_covers_all_requested_groups_and_authored_sources(self):
        fixtures = harness.discover_fixtures()
        counts = Counter(f.category for f in fixtures)
        self.assertEqual({group:counts[group] for group in harness.GROUPS},
                         dict(psg=13, opll=19, psg_scc=2, psg_opll=1, psg_scc_opll=12))
        self.assertGreaterEqual(counts['authored_mucomsx'], 4)
        self.assertEqual(len({f.relative for f in fixtures}), len(fixtures))
        self.assertTrue(all(f.relative.startswith('mucomsx/source/')
                            for f in fixtures if f.category == 'authored_mucomsx'))

    def test_profile_absence_reports_native_and_roundtrip_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root)
            with patch.object(harness, 'inspect_source', side_effect=usage), \
                    patch.object(harness, 'convert_vgm', side_effect=conversion) as converter, \
                    patch.object(harness, 'run_job') as runner, patch.object(harness, 'compare') as comparator:
                rows = harness.run_public(root / 'out', fixtures=fixtures, jobs=2)
            self.assertTrue(all(row['status'] == 'muc_generated_runtime_not_run' for row in rows))
            runner.assert_not_called()
            comparator.assert_not_called()
            self.assertTrue(all(call.kwargs['song_loop'] is False and call.kwargs['normalize_lengths'] is False
                                and call.kwargs['force'] is False for call in converter.call_args_list))
            summary = json.loads((root / 'out/summary.json').read_text())
            self.assertEqual(summary['phase_counts']['native'], {'not_run':2})

    def test_native_and_comparison_results_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root)
            profile = root / 'local.json'
            profile.write_text(json.dumps(dict(boot_disk='not-accessed.dsk')))
            def compare_case(source, target, projection):
                result = comparison(source, target, projection)
                if source.stem == 'two':
                    result.update(mismatched_intervals=1, opna_projection_exclusions=[{'target_chip':'opna_fm'}])
                return result
            with patch.object(harness, 'inspect_source', side_effect=usage), \
                    patch.object(harness, 'convert_vgm', side_effect=conversion), \
                    patch.object(harness, 'run_job', side_effect=native) as runner, \
                    patch.object(harness, 'compare', side_effect=compare_case):
                rows = harness.run_public(root / 'out', fixtures=fixtures, runtime_profile=profile, timeout=17, jobs=2)
            by_name = {Path(row['source']).stem:row for row in rows}
            self.assertEqual(by_name['one']['status'], 'validated')
            self.assertEqual(by_name['two']['status'], 'roundtrip_mismatched')
            self.assertEqual(by_name['two']['native_status'], 'passed')
            self.assertEqual(by_name['two']['opna_projection_exclusions'], 1)
            self.assertTrue(all(call.kwargs['timeout'] == 17 for call in runner.call_args_list))
            with (root / 'out/results.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_conversion_failure_does_not_run_native_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root)
            profile = root / 'local.json'
            profile.write_text(json.dumps(dict(boot_disk='not-accessed.dsk')))
            def convert_case(source, output, **kwargs):
                if source.stem == 'one':
                    raise ValueError('MUC capacity exceeded')
                return conversion(source, output, **kwargs)
            with patch.object(harness, 'inspect_source', side_effect=usage), \
                    patch.object(harness, 'convert_vgm', side_effect=convert_case), \
                    patch.object(harness, 'run_job', side_effect=native) as runner, \
                    patch.object(harness, 'compare', side_effect=comparison):
                rows = harness.run_public(root / 'out', fixtures=fixtures, runtime_profile=profile)
            self.assertEqual(rows[0]['status'], 'conversion_failed')
            self.assertEqual(rows[0]['native_status'], 'not_run')
            self.assertEqual(rows[1]['status'], 'validated')
            self.assertEqual(runner.call_count, 1)

    def test_native_failure_and_projection_exclusion_are_not_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root)
            profile = root / 'local.json'
            profile.write_text(json.dumps(dict(boot_disk='not-accessed.dsk')))
            def native_case(muc, output, **kwargs):
                if muc.stem == 'one':
                    return dict(status='failed', detail='DOS compile failed')
                return native(muc, output, **kwargs)
            with patch.object(harness, 'inspect_source', side_effect=usage), \
                    patch.object(harness, 'convert_vgm', side_effect=conversion), \
                    patch.object(harness, 'run_job', side_effect=native_case), \
                    patch.object(harness, 'compare', return_value=dict(checked_intervals=0,
                        mismatched_intervals=0, opna_projection_exclusions=[{'target_chip':'opna_fm'}])):
                rows = harness.run_public(root / 'out', fixtures=fixtures, runtime_profile=profile)
            self.assertEqual(rows[0]['status'], 'native_failed')
            self.assertEqual(rows[0]['roundtrip_status'], 'not_run')
            self.assertEqual(rows[1]['status'], 'roundtrip_not_comparable')

    def test_source_and_comparator_failures_remain_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root)
            profile = root / 'local.json'
            profile.write_text(json.dumps(dict(boot_disk='not-accessed.dsk')))
            def inspect_case(source):
                if source.stem == 'one':
                    return dict(unsupported_commands=[dict(reason='Unsupported source command', command='0x50', address=256)])
                return usage(source)
            with patch.object(harness, 'inspect_source', side_effect=inspect_case), \
                    patch.object(harness, 'convert_vgm', side_effect=conversion) as converter, \
                    patch.object(harness, 'run_job', side_effect=native), \
                    patch.object(harness, 'compare', side_effect=ValueError('Comparison could not align intervals')):
                rows = harness.run_public(root / 'out', fixtures=fixtures, runtime_profile=profile)
            self.assertEqual(rows[0]['status'], 'source_failed')
            self.assertEqual(rows[0]['conversion_status'], 'not_run')
            self.assertEqual(rows[1]['status'], 'roundtrip_failed')
            self.assertEqual(rows[1]['native_status'], 'passed')
            self.assertEqual(converter.call_count, 1)

    def test_dcsg_source_dispatch_uses_native_bundle_instead_of_ay_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root, ('one',))
            raw = bytearray(fixtures[0].source.read_bytes()[:64])
            raw.extend(b'\x50\x90\x62\x66')
            fixtures[0].source.write_bytes(raw)
            bundle = SimpleNamespace(paths={}, metadata={}, header={})
            with patch.object(harness, 'inspect_source') as ordinary, \
                    patch.object(harness, 'load_dcsg_bundle', return_value=bundle) as dcsg, \
                    patch.object(harness, 'convert_vgm', side_effect=conversion):
                row = harness.run_public(root / 'out', fixtures=fixtures)[0]
            ordinary.assert_not_called()
            dcsg.assert_called_once()
            self.assertFalse(dcsg.call_args.kwargs['song_loop'])
            self.assertEqual(row['source_status'], 'passed')
            self.assertEqual(row['conversion_status'], 'passed')

    def test_converter_source_failure_is_classified_before_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root, ('one',))
            def bad_source(source, output, **kwargs):
                output.mkdir(parents=True)
                (output / (source.stem + '.mucomsx.json')).write_text(json.dumps(
                    dict(status='conversion_failed', failed_stage='source_analysis')))
                raise ValueError('Native source Segment construction failed')
            with patch.object(harness, 'inspect_source', side_effect=usage), \
                    patch.object(harness, 'convert_vgm', side_effect=bad_source):
                row = harness.run_public(root / 'out', fixtures=fixtures)[0]
            self.assertEqual(row['status'], 'source_failed')
            self.assertEqual(row['conversion_status'], 'not_run')

    def test_existing_output_and_invalid_jobs_do_not_mutate_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = self.fixtures(root)
            output = root / 'out'
            output.mkdir()
            protected = output / 'user.txt'
            protected.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'new or empty'):
                harness.run_public(output, fixtures=fixtures)
            self.assertEqual(protected.read_text(), 'keep')
            for jobs in (0, 4, True):
                with self.assertRaisesRegex(ValueError, 'Jobs'):
                    harness.run_public(root / 'fresh', fixtures=fixtures, jobs=jobs)
            self.assertFalse((root / 'fresh').exists())


if __name__ == '__main__':
    unittest.main()
