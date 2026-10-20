"""Independent native-state checks for the public round-trip reporter."""
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'py'))
from mucomsx_roundtrip import compare
from test_mucomsx_source import vgm
class RoundtripTests(unittest.TestCase):
 def test_identical_control_trajectories_and_opna_exclusions(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);a=root/'A.vgm';b=root/'B.vgm'
   raw=vgm(bytes.fromhex('a0 00 fe a0 01 00 a0 07 3e a0 08 0f 61 e8 03 a0 08 0a 61 d0 07 66'))
   a.write_bytes(raw);b.write_bytes(raw)
   projection=dict(timing=dict(samples_per_tick=1,source_boundaries=[dict(source_sample=t,target_tick=t) for t in (0,1000,3000)]),routing=[dict(source_chip='psg',source_channel=0,target_chip='psg'),dict(source_chip='scc',source_channel=3,target_chip='opna_fm')])
   report=compare(a,b,projection)
   self.assertEqual(report['mismatched_intervals'],0);self.assertEqual(report['checked_intervals'],2)
   self.assertEqual(len(report['opna_projection_exclusions']),1)
   b.write_bytes(raw.replace(bytes.fromhex('a0 08 0a'),bytes.fromhex('a0 08 0f')))
   report=compare(a,b,projection)
   self.assertEqual(report['difference_counts'],{'volume':1})
if __name__=='__main__':unittest.main()
