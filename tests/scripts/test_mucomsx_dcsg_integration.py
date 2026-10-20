"""Canonical and owned batch MUCOMSX integration using authored public sources."""
from pathlib import Path
import csv
import json
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'scripts')]
from mucomsx_conversion import convert_vgm,ConversionError
from export_mucom import run_batch
FIXTURES=ROOT/'tests/fixtures/public/mucomsx/source'


class MucomsxDcsgIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)

    def test_dual_source_retained_and_all_six_target_parts_use_projected_periods(self):
        report=convert_vgm(FIXTURES/'DCSGDUAL.vgm',self.out,song_loop=False)
        expected=json.loads((FIXTURES/'DCSGDUAL.expected.json').read_text())
        self.assertEqual(set(report['tracks']),set(expected['target_parts']))
        source=report['source']['paths']
        with Path(source['dcsg_segments']).open() as f:segments=list(csv.DictReader(f))
        self.assertEqual({(r['chip_instance'],r['ch']) for r in segments},
                         {(str(i),str(ch)) for i in range(2) for ch in range(4)})
        self.assertTrue(any('fourth_voice' in r['reason'] for r in report['dcsg_projection']))
        with (self.out/'DCSGDUAL.mucomsx.pitch.csv').open() as f:pitches=list(csv.DictReader(f))
        self.assertTrue(pitches)
        self.assertEqual({int(row['projected_opna_period']) for row in pitches},
                         set(expected['first_projected_virtual_2mhz_periods']))
        with Path(source['dcsg_projection_views']).open() as f:views=list(csv.DictReader(f))
        self.assertEqual([int(next(r for r in views if r['ch']==str(ch))['target_period'])
                          for ch in range(6)],expected['first_projected_virtual_2mhz_periods'])
        text=Path(report['muc_path']).read_text(encoding='cp932')
        self.assertTrue(all(any(line.startswith(part+' ') for line in text.splitlines())
                            for part in expected['target_parts']))

    def test_native_psg_optional_ssg_layer_and_scc_fm_fallback_are_explicit(self):
        report=convert_vgm(FIXTURES/'PSGSRC.vgm',self.out/'enhanced',song_loop=False,
                           psg_enhance_cents=11)
        self.assertIn('MSXD',report['tracks']);self.assertIn('D',report['tracks'])
        self.assertEqual(report['psg_enhancement']['cents'],11)
        with (self.out/'enhanced/PSGSRC.mucomsx.pitch.csv').open() as f:
            pitch=list(csv.DictReader(f))
        original=next(r for r in pitch if r['part']=='MSXD')
        layer=next(r for r in pitch if r['part']=='D')
        self.assertEqual(int(layer['projected_opna_period']),
                         round(int(original['projected_opna_period'])*2**(-11/1200)))
        self.assertTrue(any(r.get('enhancement_cents')==11 for r in report['routing']))
        from test_mucomsx_source import vgm
        raw_scc=(FIXTURES/'SCCSRC.vgm').read_bytes()[0x100:-1]
        raw_psg=(FIXTURES/'PSGSRC.vgm').read_bytes()[0x100:]
        source=self.out/'MIXED.vgm';source.write_bytes(vgm(raw_scc+raw_psg))
        mixed=convert_vgm(source,self.out/'mixed',song_loop=False,psg_enhance_cents=11)
        self.assertEqual(mixed['scc_projection_policy'],'all_opna_fm_additive4')
        self.assertTrue(any(r['source_chip']=='scc' and r['target_chip']=='opna_fm' for r in mixed['routing']))
        text=Path(mixed['muc_path']).read_text(encoding='cp932')
        self.assertNotIn('#SSGExtend On',text)
        self.assertIn('MSXD',mixed['tracks']);self.assertIn('D',mixed['tracks'])

    def test_batch_options_forwarded_only_to_mucomsx_and_muc_ownership_preserved(self):
        rows=run_batch(FIXTURES/'PSGSRC.vgm',self.out/'batch',target='mucomsx',
                       muc_only=True,song_loop=False,opll_rhythm='layered',psg_enhance_cents=11)
        self.assertEqual(rows[0]['status'],'muc_only')
        manifest=json.loads((self.out/'batch/export_manifest.json').read_text())
        self.assertIn('PSGSRC.muc',manifest['PSGSRC']['files'])
        runs=list((self.out/'batch/_diagnostics/PSGSRC').glob('run_*'))
        report=json.loads((runs[0]/'PSGSRC.mucomsx.json').read_text())
        self.assertEqual(report['psg_enhancement']['cents'],11)
        for options in ({'opll_rhythm':'layered'},{'psg_enhance_cents':11}):
            with self.assertRaisesRegex(ValueError,'require --target mucomsx'):
                run_batch(FIXTURES/'PSGSRC.vgm',self.out/'invalid',muc_only=True,**options)
        with self.assertRaisesRegex(ValueError,'requires a local --runtime-profile'):
            run_batch(FIXTURES/'PSGSRC.vgm',self.out/'invalid',target='mucomsx',
                      runtime_profile=self.out/'missing.json')

    def test_layered_opll_rhythm_is_forwarded_by_batch(self):
        from test_mucomsx_source import vgm
        source=self.out/'RHYTHM.vgm'
        source.write_bytes(vgm(bytes.fromhex('51 36 00 51 0e 30 61 b8 0b 51 0e 20 61 b8 0b 66')))
        rows=run_batch(source,self.out/'rhythm',target='mucomsx',muc_only=True,
                       song_loop=False,opll_rhythm='layered')
        self.assertEqual(rows[0]['status'],'muc_only')
        text=(self.out/'rhythm/tracks/RHYTHM/RHYTHM.muc').read_text(encoding='cp932')
        self.assertTrue(any(line.startswith('MSXG ') for line in text.splitlines()))
        self.assertTrue(any(line.startswith('G ') for line in text.splitlines()))

    def test_cli_mucom_rejects_explicit_mucomsx_only_options(self):
        command=[sys.executable,str(ROOT/'scripts/export_mucom.py'),str(FIXTURES/'PSGSRC.vgm'),
                 '--outdir',str(self.out/'invalid'),'--opll-rhythm','native','--muc-only']
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('require --target mucomsx',result.stderr)


if __name__=='__main__':unittest.main()
