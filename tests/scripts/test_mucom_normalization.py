"""Original-time policy, immutable evidence and shared native timing."""
from dataclasses import replace
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'py'),str(ROOT/'tests/scripts')]
from conversion_config import normalization_samples
from mucom_normalization import omit_short_fm, normalize_source, TIMER_UNIT
from opna_muc import convert_vgm
from opna_music import build_fm_gates
from test_mucom_fm_edges import authored_vgm
from vgm_reader import parse_vgm


class MucomNormalizationTests(unittest.TestCase):
    def analyze(self, pattern, *, loop_attack=False):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name);source=root/'test.vgm'
        raw=bytearray(authored_vgm(pattern))
        if loop_attack:
            import struct
            attack=raw.index(bytes((0x56,0x28,0xf0)),256)
            struct.pack_into('<I',raw,0x1c,attack-0x1c)
            struct.pack_into('<I',raw,0x20,sum(wait for _,wait in pattern))
        source.write_bytes(raw)
        metadata={}
        parse_vgm(str(source),str(root/'source'),opna_metadata=metadata,loop_count=1,dump_opna_segments=True)
        return root,source,metadata['analysis']

    def test_floor_threshold_distinguishes_352_and_353_original_samples(self):
        self.assertEqual(normalization_samples(8),352)
        for length,omitted in ((352,True),(353,False)):
            with self.subTest(length=length):
                _,_,analysis=self.analyze(((0xf0,length),(0,800)))
                derived,losses=omit_short_fm(analysis,352,None)
                self.assertEqual(bool(losses),omitted)
                self.assertEqual(bool(build_fm_gates(derived)),not omitted)

    def test_partial_repeated_and_unclosed_keys_are_retained(self):
        for pattern in (((0x80,100),(0,800)),((0xf0,100),(0xf0,100),(0,800)),
                        ((0xf0,100),(0xe0,100),(0,800)),((0xf0,100),)):
            _,_,analysis=self.analyze(pattern)
            derived,losses=omit_short_fm(analysis,352,None)
            self.assertEqual(losses,[])
            self.assertIs(derived,analysis)

    def test_loop_crossing_whole_gate_is_retained(self):
        _,_,analysis=self.analyze(((0xf0,352),(0,800)))
        derived,losses=omit_short_fm(analysis,352,100)
        self.assertEqual(losses,[]);self.assertIs(derived,analysis)

    def test_raw_state_segments_are_identical_after_omission(self):
        _,_,analysis=self.analyze(((0xf0,100),(0,800),(0xf0,900),(0,800)))
        before=repr(analysis)
        derived,losses=omit_short_fm(analysis,352,None)
        self.assertEqual(repr(analysis),before)
        self.assertEqual(len(losses),1)
        self.assertEqual([s.segment_id for s in derived.segments],
                         [s.segment_id for s in analysis.segments])
        self.assertEqual([(s.vgmticks,s.vgmticks_end) for s in derived.segments],
                         [(s.vgmticks,s.vgmticks_end) for s in analysis.segments])
        nonkeys=lambda a:[(e.source_event_id,e.register,e.data,e.vgmticks)
                          for e in a.events if e.register!=0x28]
        self.assertEqual(nonkeys(derived),nonkeys(analysis))
        self.assertEqual(len(build_fm_gates(derived)['A']),1)

    def test_held_internal_short_changes_are_not_short_notes(self):
        _,_,analysis=self.analyze(((0xf0,100),(0xe0,100),(0xf0,1000),(0,800)))
        _,losses=omit_short_fm(analysis,352,None)
        self.assertFalse(losses)
        self.assertEqual(len(build_fm_gates(analysis)['A']),1)

    def test_shared_map_obeys_exact_budget_and_protects_positive_partial_operations(self):
        _,_,analysis=self.analyze(((0xf0,1),(0xe0,1),(0xf0,1000),(0,800)))
        pcm=SimpleNamespace(segments=(),controls=(),source_end_vgmticks=analysis.source_end_vgmticks)
        derived,_,clock,timing,report=normalize_source(analysis,{},pcm,analysis.source_end_vgmticks,None,8)
        self.assertTrue(report['applied'])
        self.assertGreater(clock[1],clock[0]);self.assertGreater(clock[2],clock[1])
        period=(256-timing['timer_b'])*TIMER_UNIT
        self.assertTrue(all(abs(clock[t]*period-t)<=352 for t in clock))
        self.assertEqual(clock[0],0)
        self.assertTrue(all(clock[a]<=clock[b] for a,b in zip(sorted(clock),sorted(clock)[1:])))
        self.assertEqual({e.vgmticks for e in analysis.events}-clock.keys(),set())

    def test_unfeasible_candidate_restores_analysis_and_auxiliary_atomically(self):
        # 100 positive partial operations cannot fit the finest native Timer-B
        # within a one-sample movement budget.
        pattern=tuple((0xf0 if i%2==0 else 0xe0,1) for i in range(100))+((0,800),)
        _,_,impossible=self.analyze(pattern)
        pcm=SimpleNamespace(segments=(),controls=(),source_end_vgmticks=impossible.source_end_vgmticks)
        auxiliary={}
        derived,tracks,clock,timing,report=normalize_source(impossible,auxiliary,pcm,impossible.source_end_vgmticks,None,.03)
        self.assertFalse(report['applied']);self.assertIs(derived,impossible)
        self.assertIs(tracks,auxiliary);self.assertIsNone(clock);self.assertIsNone(timing)
        self.assertEqual(report['omitted_fm_gates'],[])

    def test_all_short_gates_produce_finite_silence_and_inspectable_loss(self):
        root,source,_=self.analyze(((0xf0,352),(0,800)))
        report=convert_vgm(source,root/'converted')
        self.assertTrue(report['normalization']['applied'])
        self.assertEqual(report['musical_note_units'],0)
        text=Path(report['muc_path']).read_bytes().decode('cp932')
        self.assertIn('A C128',text)
        self.assertEqual(report['source_musical_note_units'],1)
        self.assertTrue((root/'converted/test.opna.music.csv').exists())
        self.assertTrue((root/'converted/test.mucom.output.music.csv').exists())
        self.assertTrue((root/'converted/test.mucom.timing.csv').exists())
        self.assertEqual(json.loads((root/'converted/test.mucom.normalization.json').read_text())['correction_bound_samples'],352)

    def test_parameter_write_in_first_omitted_gate_is_retained(self):
        root,source,_=self.analyze(((0xf0,100),(0,800),(0xf0,900),(0,800)))
        raw=bytearray(source.read_bytes())
        position=raw.index(bytes((0x56,0x28,0)),256)
        raw[position:position]=bytes((0x56,0x40,37))
        import struct
        struct.pack_into('<I',raw,4,len(raw)-4);source.write_bytes(raw)
        report=convert_vgm(source,root/'converted')
        self.assertEqual(report['musical_note_units'],1)
        text=Path(report['muc_path']).read_bytes().decode('cp932')
        self.assertIn('yTL,1,37',text)
        with (root/'converted/test.mucom.control_units.csv').open(encoding='utf-8') as stream:
            rows=list(csv.DictReader(stream))
        self.assertTrue(any('yTL,1,37' in r['command'] and int(r['source_start'])==100 for r in rows))
        self.assertEqual(report['fm_projection'][0]['source_tl'][0],37)

    def test_disabled_baseline_retains_short_gate(self):
        root,source,_=self.analyze(((0xf0,352),(0,800)))
        report=convert_vgm(source,root/'baseline',normalize_lengths=False)
        self.assertFalse(report['normalization']['enabled'])
        self.assertEqual(report['musical_note_units'],1)
        self.assertEqual(report['normalization']['omitted_fm_gates'],[])

    def test_public_pcm_bytes_and_ir_are_unchanged_at_8_and_16_ms(self):
        source=ROOT/'tests/fixtures/public/opna/mucom88/pcm_samples.vgm'
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            reports=[convert_vgm(source,root/str(ms),normalization_ms=ms) for ms in (8,16)]
            banks=[Path(r['pcm_path']).read_bytes() for r in reports]
            self.assertEqual(banks[0],banks[1])
            self.assertTrue(all(r['normalization']['source_pcm_ir_unchanged'] for r in reports))
            evidence=list((root/'8').glob('*.opna_adpcm*'))
            self.assertTrue(evidence)
            for a in evidence:
                if a.is_file():self.assertEqual(a.read_bytes(),(root/'16'/a.name).read_bytes())

    def test_zero_length_canonical_pulse_has_explicit_omission(self):
        root,source,_=self.analyze(((0xf0,0),(0,800)))
        report=convert_vgm(source,root/'converted')
        self.assertEqual(report['musical_note_units'],0)
        losses=report['normalization']['omitted_fm_gates']
        self.assertEqual(len(losses),1)
        self.assertEqual(losses[0]['duration_samples'],0)

    def test_release_edit_before_later_omitted_gate_is_retained(self):
        root,source,_=self.analyze(((0xf0,900),(0,800),(0xf0,100),(0,800)))
        raw=bytearray(source.read_bytes())
        first_off=raw.index(bytes((0x56,0x28,0)),256)+3
        raw[first_off:first_off]=bytes((0x56,0x40,39))
        import struct
        struct.pack_into('<I',raw,4,len(raw)-4);source.write_bytes(raw)
        report=convert_vgm(source,root/'converted')
        self.assertEqual(report['musical_note_units'],1)
        self.assertEqual(len(report['normalization']['omitted_fm_gates']),1)
        with (root/'converted/test.mucom.control_units.csv').open(encoding='utf-8') as stream:
            rows=list(csv.DictReader(stream))
        self.assertTrue(any('yTL,1,39' in r['command'] and int(r['source_start'])==900 for r in rows))

    def test_different_pcm_source_end_is_rejected(self):
        _,_,analysis=self.analyze(((0xf0,800),(0,800)))
        pcm=SimpleNamespace(segments=(),controls=(),source_end_vgmticks=analysis.source_end_vgmticks+1)
        with self.assertRaisesRegex(ValueError,'same original source end'):
            normalize_source(analysis,{},pcm,analysis.source_end_vgmticks,None,8)

    def test_observed_reset_short_pcm_is_omitted_without_changing_bank_or_ir(self):
        from vgm_io import read_vgm_bytes
        from vgm_timing import command_times
        import struct
        fixture=ROOT/'tests/fixtures/public/opna/mucom88/pcm_samples.vgm'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pcm={}
            parse_vgm(str(fixture),str(root/'original'),opna_pcm_metadata=pcm)
            segment=next(s for s in pcm['analysis'].segments if s.termination=='reset')
            raw=bytearray(read_vgm_bytes(fixture))
            commands=list(command_times(raw,loop_count=1))
            waits=[e for e in commands if segment.start_vgmticks<=e.vgmticks<segment.end_vgmticks
                   and (e.command in (0x61,0x62,0x63) or 0x70<=e.command<=0x7f)]
            short_wait=max(1,200//len(waits))
            for event in commands:
                if segment.start_vgmticks<=event.vgmticks<segment.end_vgmticks:
                    if event.command==0x61:struct.pack_into('<H',raw,event.address+1,short_wait)
                    elif event.command in (0x62,0x63) or 0x70<=event.command<=0x7f:raw[event.address]=0x70
            struct.pack_into('<II',raw,0x1c,0,0)
            end=next(e.vgmticks for e in command_times(raw,loop_count=1) if e.command==0x66)
            struct.pack_into('<I',raw,0x18,end)
            source=root/'short_pcm.vgm';source.write_bytes(raw)
            baseline=convert_vgm(source,root/'baseline',normalize_lengths=False,song_loop=False)
            normal=convert_vgm(source,root/'normal',song_loop=False)
            self.assertTrue(normal['normalization']['omitted_pcm_gates'])
            self.assertLessEqual(normal['normalization']['omitted_pcm_gates'][0]['duration_samples'],352)
            self.assertEqual(Path(baseline['pcm_path']).read_bytes(),Path(normal['pcm_path']).read_bytes())
            evidence=list((root/'baseline').glob('*.opna_adpcm*'))
            self.assertTrue(evidence)
            for path in evidence:
                if path.is_file():self.assertEqual(path.read_bytes(),(root/'normal'/path.name).read_bytes())

    def test_invalid_normalization_values_fail_before_source_analysis(self):
        root,source,_=self.analyze(((0xf0,800),(0,800)))
        for value in (0,-1,float('nan'),float('inf'),.001):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert_vgm(source,root/'invalid',normalization_ms=value)


if __name__=='__main__':unittest.main()
