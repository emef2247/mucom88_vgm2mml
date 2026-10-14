"""Independent native SSG register replay for authored source control intervals."""
from bisect import bisect_right
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts')]
from opna_muc import convert_vgm
from mucom_compiler import compile_muc
from verify_mucom_native import DEFAULT_TOOL
from vgm_io import read_vgm_bytes
from vgm_timing import command_times


def authored_ssg(intervals):
    """Rows: period, manual/envelope amplitude, mixer, duration, optional writes."""
    header=bytearray(256);header[:4]=b'Vgm '
    struct.pack_into('<I',header,8,0x171)
    struct.pack_into('<I',header,0x34,256-0x34)
    struct.pack_into('<I',header,0x48,7987200)
    body=bytearray();total=0;last={}
    for period,amplitude,mixer,duration,extra in intervals:
        for register,data in ((0,period&255),(1,period>>8),(7,mixer),(8,amplitude),*extra):
            if last.get(register)!=data or register==13:
                body.extend((0x56,register,data));last[register]=data
        body.extend(b'\x61'+struct.pack('<H',duration));total+=duration
    body.append(0x66)
    result=header+body
    struct.pack_into('<I',result,4,len(result)-4)
    struct.pack_into('<I',result,0x18,total)
    return bytes(result)


def ssg_snapshots(path):
    """Replay actual register writes without using target pitch projection."""
    raw=read_vgm_bytes(path);registers=[0]*16;rows=[(0,tuple(registers))]
    for event in command_times(raw):
        if event.command==0x56 and raw[event.address+1]<16:
            registers[raw[event.address+1]]=raw[event.address+2]
            rows.append((event.vgmticks,tuple(registers)))
    return rows


@unittest.skipUnless(sys.platform=='win32' and DEFAULT_TOOL.is_file(),
                     'native MUCOM compiler/replay unavailable')
class MucomSSGProjectionTests(unittest.TestCase):
    def assert_native_intervals(self,intervals):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source.vgm'
            source.write_bytes(authored_ssg(intervals))
            report=convert_vgm(source,out/'converted',song_loop=False)
            muc=Path(report['muc_path']);mub=muc.with_suffix('.mub')
            compile_muc(muc,mub)
            replay=subprocess.run([str(DEFAULT_TOOL),str(mub)],cwd=muc.parent,
                capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(replay.returncode,0,replay.stdout+replay.stderr)
            generated=mub.with_suffix('.vgm');self.assertTrue(generated.is_file())
            native=ssg_snapshots(generated);times=[row[0] for row in native]
            allocation={row['source_sample']:row['target_clock']
                        for row in report['timing']['source_boundaries']}
            spc=report['timing']['samples_per_clock'];start=0
            source_rows=ssg_snapshots(source);source_times=[row[0] for row in source_rows]
            for index,(_,_,_,duration,_) in enumerate(intervals):
                end=start+duration
                expected=source_rows[bisect_right(source_times,(start+end)/2)-1][1]
                # Collapse zero-time bus writes, but inspect every resulting
                # positive native interval. Ignore only the established two-
                # sample native boundary jitter, never a complete driver tick.
                lower=(allocation[start]+1)*spc+2
                upper=(allocation[end]+1)*spc-2
                boundaries=sorted({lower,upper,*[tick for tick in times if lower<tick<upper]})
                self.assertGreater(upper,lower)
                for left,right in zip(boundaries,boundaries[1:]):
                    actual=native[bisect_right(times,(left+right)/2)-1][1]
                    with self.subTest(interval=index,native_span=(left,right),
                                      source_period=expected[0]+(expected[1]&15)*256):
                        if expected[8]&16:
                            # Hardware phase is a separate unsupported comparison.
                            self.assertTrue(actual[8]&16)
                        elif expected[8]&15 and not expected[7]&1:
                            self.assertEqual(actual[0]+(actual[1]&15)*256,
                                             expected[0]+(expected[1]&15)*256)
                            self.assertEqual(actual[8]&31,expected[8]&31)
                            self.assertEqual(actual[7]&9,expected[7]&9)
                        else:
                            self.assertFalse(actual[8]&31 and not actual[7]&1)
                start=end
            return muc.read_bytes().decode('cp932'),report

    def test_held_pitch_sweep_native_period_and_live_volume(self):
        # Off-table periods require both signs of D at several octaves.
        periods=(3816,3900,3600,1950,1880,960,930,470,480,120,122,15,16,0,4095)
        intervals=[(period,(15,11,7,13)[index%4],0x3e,8820,())
                   for index,period in enumerate(periods)]
        score,_=self.assert_native_intervals(intervals)
        self.assertEqual(score.count('E255,255,255,255,0,255'),1)

    def test_first_octave_held_detune_updates_without_retrigger(self):
        self.assert_native_intervals([
            (3700,15,0x3e,8820,()),(3750,11,0x3e,8820,()),
            (3710,9,0x3e,8820,())])

    def test_silence_and_reattack_reinitialize_flat_envelope(self):
        score,_=self.assert_native_intervals([
            (1800,15,0x3e,8820,()),(1700,8,0x3e,8820,()),
            (1700,0,0x3e,8820,()),(1300,12,0x3e,8820,()),
            (1250,6,0x3e,8820,())])
        self.assertEqual(score.count('E255,255,255,255,0,255'),2)

    def test_hardware_to_software_transition_keeps_manual_volume(self):
        score,report=self.assert_native_intervals([
            (1800,16,0x3e,8820,((11,32),(12,0),(13,10))),
            (1750,9,0x3e,8820,()),(1680,12,0x3e,8820,()),
            (1580,4,0x3e,8820,())])
        self.assertEqual(score.count('E255,255,255,255,0,255'),1)
        self.assertIn('v9',score)


if __name__=='__main__':unittest.main()
