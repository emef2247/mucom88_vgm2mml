"""Segment-derived diagnostic MUCOM projection, independent of tool execution."""
from dataclasses import replace
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
import opna_mucom as mucom
from test_opna_reader import parse, vgm, write
from test_opm_reader import read_rows


class OpnaMucomTests(unittest.TestCase):
    def analysis(self, commands, *, clock=mucom.CLOCK_HZ):
        with tempfile.TemporaryDirectory() as directory:
            _, facts = parse(directory, vgm(commands, opna=clock), dump=True)
            return facts['analysis']

    def projection(self, commands):
        analysis = self.analysis(commands)
        return mucom.project(analysis.segments, end_vgmticks=analysis.source_end_vgmticks,
                             clock_hz=mucom.CLOCK_HZ)

    def test_operator_projection_comes_from_typed_fields(self):
        original = self.projection(write(0x30, 0xf3) + b'\x70')
        self.assertEqual(original.writes[0].data, 0x73)  # Reserved bit is canonicalized.
        modified = []
        for segment in original.source_segments:
            if segment.register == 0x30:
                operators = list(segment.state.operators)
                operators[0] = replace(operators[0], mul=9)
                segment = replace(segment, data=0xaa,
                                  state=replace(segment.state, operators=tuple(operators)))
            modified.append(segment)
        changed = mucom.project(modified, end_vgmticks=1, clock_hz=mucom.CLOCK_HZ)
        self.assertEqual(changed.writes[0].data, 0x79)
        self.assertEqual(next(s.data for s in original.source_segments if s.register == 0x30), 0xf3)

    def test_all_operator_groups_and_channel_controls(self):
        controls = [(0x30,0x71),(0x40,0xff),(0x50,0xff),(0x60,0xff),
                    (0x70,0xff),(0x80,0xab),(0x90,0xff),(0xb0,0xff),(0xb4,0xff)]
        projection = self.projection(b''.join(write(r,d,port=1) for r,d in controls) + b'\x70')
        self.assertEqual([(w.port,w.register,w.data) for w in projection.writes],
                         [(1,r,d) for (r,_),d in zip(controls,[0x71,0x7f,0xdf,0x9f,0x1f,0xab,0xf,0x3f,0xf7])])

    def test_cross_port_pitch_uses_committed_state_and_explicit_pair(self):
        projection = self.projection(write(0xa4,0x22) + write(0xa0,0x33,port=1)
                                     + write(0xa6,0x1d,port=1) + write(0xa1,0x44) + b'\x70')
        self.assertEqual([(w.port,w.register,w.data) for w in projection.writes],
                         [(1,0xa4,0x22),(1,0xa0,0x33),(0,0xa5,0x1d),(0,0xa1,0x44)])
        self.assertEqual([w.source_event_id for w in projection.writes], [1,1,3,3])
        self.assertTrue(all(w.segment_ids for w in projection.writes))

    def test_special_pitch_and_shared_controls_are_deduplicated(self):
        projection = self.projection(write(0xac,0x1a) + write(0xa8,0x55)
                                     + write(0x22,0xff) + write(0x29,0x95)
                                     + write(0x24,0x12) + write(0x25,0xff)
                                     + write(0x26,0x34) + write(0x27,0x40) + b'\x70')
        self.assertEqual([(w.register,w.data) for w in projection.writes],
                         [(0xac,0x1a),(0xa8,0x55),(0x22,0xf),(0x29,0x95),
                          (0x24,0x12),(0x25,3),(0x26,0x34),(0x27,0x40)])
        self.assertEqual(len(next(w.segment_ids for w in projection.writes if w.register==0x22)),6)

    def test_prescaler_strobes_survive_target_cache(self):
        projection = self.projection(write(0x2d,0xff) + write(0x2e,0xff)
                                     + write(0x2d,0xff) + write(0x2e,0xff) + b'\x70')
        self.assertEqual([(w.register,w.data) for w in projection.writes],
                         [(0x2d,0),(0x2e,0),(0x2d,1),(0x2e,1)])
        self.assertTrue(any('Prescaler' in item for item in projection.diagnostics))

    def test_zero_sample_keys_end_and_csv(self):
        projection = self.projection(write(0x28,0xf4) + write(0x28,4) + b'\x61\x00\x02'
                                     + write(0x28,0x14))
        self.assertEqual([(w.source_vgmticks,w.data) for w in projection.writes],[(0,0xf4),(0,4),(512,0x14)])
        rendered = mucom.render(projection)
        self.assertIn('A C192 T55125 y40,$F4 y40,$04 r%255 r%255 r%2 y40,$14',rendered)
        self.assertEqual(sum(map(int,re.findall(r'r%(\d+)',rendered))),512)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'projection.csv'
            mucom.dump_projection(projection,csv_path=target)
            rows=read_rows(target)
            self.assertEqual(len(rows),3)
            self.assertEqual(rows[2]['source_vgmticks'],'512')
            self.assertTrue(rows[2]['segment_ids'].startswith('['))

    def test_unknown_pitch_is_labeled_and_non_fm_is_excluded(self):
        projection=self.projection(write(0xa0,0x33)+write(0,0x11)+write(0,0x80,port=1)+b'\x70')
        self.assertEqual([(w.register,w.data) for w in projection.writes],[(0xa0,0x33)])
        self.assertTrue(any('Partial pitch' in item for item in projection.diagnostics))
        self.assertTrue(any('SSG/rhythm/ADPCM' in item for item in projection.diagnostics))

    def test_scope_validation_rejects_clock_dual_csm_and_invalid_end(self):
        projection=self.projection(write(0x28,0xf0)+b'\x70')
        for clock in (4000000,True):
            with self.assertRaises(ValueError):
                mucom.project(projection.source_segments,end_vgmticks=1,clock_hz=clock)
        with self.assertRaises(ValueError):
            mucom.project(projection.source_segments,end_vgmticks=True,clock_hz=mucom.CLOCK_HZ)
        with self.assertRaises(ValueError):
            mucom.project([replace(s,chip_instance=1) for s in projection.source_segments],
                          end_vgmticks=1,clock_hz=mucom.CLOCK_HZ)
        with self.assertRaises(ValueError):
            self.projection(write(0x27,0x80)+b'\x70')


if __name__ == '__main__':
    unittest.main()
