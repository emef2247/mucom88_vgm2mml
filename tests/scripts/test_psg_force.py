"""Shared PSG/SSG forced omissions and explicit channel-capacity reporting."""
from dataclasses import replace
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'tests/scripts')]
from chip_segments import SccAnalysis
from psg_scc_opm import project
from opm_performance import build_performance
from opna_force import collect, dump, format_register_list
from test_psg_scc_opm import psg
import test_opna_ssg_opm as ssg_helpers


def authored_psg():
    data=bytes.fromhex(
        'a0 00 c8 a0 01 00 a0 07 3e a0 08 0f 61 e8 03 '
        'a0 06 05 a0 07 36 61 e8 03 a0 07 3e 61 e8 03 '
        'a0 0b d0 a0 0c 07 a0 0d 0a a0 08 10 61 e8 03 '
        'a0 08 0f 61 e8 03 a0 08 00 61 e8 03 66')
    raw=bytearray(0x100)+data
    raw[:4]=b'Vgm '
    for offset,value in ((4,len(raw)-4),(8,0x171),(0x34,0xcc),(0x74,1789772),(0x18,6000)):
        struct.pack_into('<I',raw,offset,value)
    return raw


class PsgForceTests(unittest.TestCase):
    def test_shared_force_preserves_native_and_surviving_gates(self):
        segments=tuple(psg(vgmticks=i*1000,vgmticks_end=(i+1)*1000,mode=mode,envelope_enabled=env)
                       for i,(mode,env) in enumerate(((1,0),(3,0),(1,0),(1,1),(1,0))))
        for model in ('fm','additive'):
            with self.subTest(model=model):
                options=dict(psg_clock=1789772,scc_clock=0,end_vgmticks=5000,psg_model=model)
                with self.assertRaisesRegex(ValueError,'noise/hardware EG'):
                    project({0:segments},SccAnalysis({},()),**options)
                plan=project({0:segments},SccAnalysis({},()),force=True,**options)
                self.assertEqual([r['target_audible'] for r in plan.rows],[True,False,True,False,True])
                self.assertEqual([s.mode for s in segments],[1,3,1,1,1])
                self.assertEqual([r['reason'] for r in plan.settings['forced_omissions']],
                                 ['psg_noise','psg_hardware_envelope'])
                performance=build_performance(plan)
                edges=[(w.vgmticks,bool(w.data&0x78)) for w in performance.writes if w.inferred_key]
                self.assertEqual(edges,[(0,True),(1000,False),(2000,True),(3000,False),(4000,True),(5000,False)])

    def test_cli_force_keeps_source_and_lists_psg_omissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'AUTH.vgm';out=root/'out'
            source.write_bytes(authored_psg())
            command=[sys.executable,str(ROOT/'vgm2mml.py'),str(source),'--target','mdx','--outdir',str(out),
                     '--no-song-loop','--no-normalize-lengths']
            run=subprocess.run([*command,'--force'],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('PSG1',run.stdout)
            self.assertIn('psg_noise',run.stdout)
            self.assertIn('psg_hardware_envelope',run.stdout)
            self.assertTrue((out/'AUTH.mdx.mml').is_file())
            registry=json.loads((out/'AUTH.psg_force.skipped_registers.json').read_text())
            self.assertTrue(registry['source_ir_unchanged'])
            self.assertTrue(all(r['record_kind']=='interval' for r in registry['records']))
            self.assertTrue(any(p.name.endswith('.csv') for p in out.iterdir()))
            strict=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(strict.returncode,0)
            self.assertFalse((out/'AUTH.psg_force.skipped_registers.json').exists())

    def test_channel_shortage_names_the_omitted_ssg_part(self):
        fm,ssg=ssg_helpers.OpnaSsgOpmTests().analyze(ssg_helpers.body(range(6),mixer=0x1c))
        rows=collect(fm,ssg,[],[],dropped_ssg=(2,),force=True)
        dropped=[r for r in rows if r['action']=='omit_ssg_part']
        self.assertTrue(dropped)
        self.assertEqual({r['source_part'] for r in dropped},{'SSG3'})
        with tempfile.TemporaryDirectory() as temporary:
            report=dump(rows,temporary,'AUTH',force=True)
            text='\n'.join(format_register_list(report['skipped_registers']))
            self.assertIn('SSG3',text)
            self.assertIn('existing_opm_channel_allocation_limit',text)


if __name__=='__main__':
    unittest.main()
