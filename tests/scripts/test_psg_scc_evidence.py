"""Projected evidence annotation preserves large provenance fields."""
import csv
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT)]
from psg_scc_conversion import _mark_projected_evidence, _read_generated_csv


class ProjectedEvidenceTests(unittest.TestCase):
    def write_csv(self, path, fields, rows):
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)

    def test_annotation_preserves_large_quoted_fields_and_source_evidence(self):
        trajectory = ('0,1;"quoted"\n' * 12000) + '終端'
        source_ids = ','.join(str(i) for i in range(30000))
        self.assertGreater(len(trajectory), 131072)
        self.assertGreater(len(source_ids), 131072)
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(131072)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'source.vgm'
                source.write_bytes(b'synthetic source')
                source_csvs = [root / ('source.' + chip + '.segments.csv')
                               for chip in ('psg', 'scc')]
                for source_csv in source_csvs:
                    source_csv.write_bytes(b'vgmticks,source_event_id\n0,1\n')
                source_before = [path.read_bytes() for path in source_csvs]
                folder = root / 'projected_opm'
                folder.mkdir()
                target = folder / 'source.vgm'
                target.write_bytes(b'synthetic target')
                mapping = folder / 'source.source_map.csv'
                self.write_csv(mapping, ['target_vgmticks', 'vgmticks', 'source_event_ids'],
                               [dict(target_vgmticks=0, vgmticks=0, source_event_ids=source_ids)])
                units = folder / 'source.mdx.music.units.csv'
                fields = ['trajectory', 'source_event_ids', 'state_origin']
                row = dict(trajectory=trajectory, source_event_ids=source_ids, state_origin='old')
                self.write_csv(units, fields, [row])
                performance = SimpleNamespace(source_end=0, settings={})
                projection = SimpleNamespace(mdx_tick=lambda value: value,
                                             projected_samples=lambda value: value,
                                             end_projected_vgmticks=0, source_end_vgmticks=0)

                _mark_projected_evidence(folder, source, target, performance, projection)

                self.assertEqual(csv.field_size_limit(), 131072)
                unit_fields, unit_rows = _read_generated_csv(units)
                self.assertEqual(unit_fields, fields)
                self.assertEqual(unit_rows, [dict(row, state_origin='projected_opm')])
                _, mapping_rows = _read_generated_csv(mapping)
                self.assertEqual(mapping_rows[0]['source_event_ids'], source_ids)
                self.assertEqual(mapping_rows[0]['final_mdx_tick'], '0')
                self.assertEqual(mapping_rows[0]['state_origin'], 'projected_opm')
                self.assertEqual([path.read_bytes() for path in source_csvs], source_before)
                self.assertEqual(csv.field_size_limit(), 131072)
        finally:
            csv.field_size_limit(previous_limit)

    def test_limit_restored_after_row_iteration_error(self):
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(1024)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'generated.csv'
                self.write_csv(path, ['trajectory'], [dict(trajectory='x' * 150000)])
                original = path.read_bytes()
                real_reader = csv.DictReader

                class FailingReader:
                    def __init__(self, stream):
                        self.reader = real_reader(stream)
                        self.fieldnames = self.reader.fieldnames

                    def __iter__(self):
                        self_outer.assertGreater(csv.field_size_limit(), 131072)
                        yield next(self.reader)
                        raise csv.Error('synthetic row iteration failure')

                self_outer = self
                with patch('psg_scc_conversion.csv.DictReader', FailingReader):
                    with self.assertRaisesRegex(csv.Error, 'row iteration failure'):
                        _read_generated_csv(path)
                self.assertEqual(csv.field_size_limit(), 1024)
                self.assertEqual(path.read_bytes(), original)
        finally:
            csv.field_size_limit(previous_limit)


if __name__ == '__main__':
    unittest.main()
