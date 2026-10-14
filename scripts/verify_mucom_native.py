"""Offline native MUB replay: compare FM gates and positive control intervals.

This is a target-control check, separate from compilation, listening and the
auxiliary-engine/PCM decoder waveform equivalence checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'py'))
from vgm_io import read_vgm_bytes
from vgm_loop import read_loop_metadata
from vgm_reader import parse_vgm
from opna_music import build_fm_gates,audit_fm_key_edges

DEFAULT_TOOL=ROOT/'.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe'
FIELDS=('dt','mul','tl','ks','ar','am_enabled','d1r','d2r','sl','rr','ssg_eg_raw')


def _tool_argument(path):
    if sys.platform=='win32':return str(path.resolve())
    return subprocess.check_output(['wslpath','-w',str(path.resolve())],text=True).strip()


def _signature(state,clock,target_clock):
    # Normalize to target pitch resolution independently of MUC commands.
    block=state.block
    fnum=round(state.fnum*clock*6/(target_clock*state.fm_prescale_divisor))
    while fnum>2047 and block<7:
        block+=1
        fnum=round(state.fnum*clock*6/(target_clock*state.fm_prescale_divisor*2**(block-state.block)))
    parameters=tuple(getattr(state,f) or 0 for f in
        ('key_mask','algorithm','feedback','left_enabled','right_enabled','ams','pms','lfo_enabled','lfo_rate'))
    operators=tuple(tuple(getattr(op,f) or 0 for f in FIELDS) for op in state.operators)
    return fnum*2**block,parameters,operators


def _timeline(controls,transform,clock,target_clock):
    result=[]
    for control in controls:
        start,end=transform(control.start),transform(control.end)
        if end-start<1:continue
        key=_signature(control.state,clock,target_clock)
        if result and result[-1][2]==key and abs(result[-1][1]-start)<2:
            previous=result.pop();result.append((previous[0],end,key))
        else:result.append((start,end,key))
    return result


def _compare_timelines(source,native,part,gate):
    errors=[];checks=[]
    if len(source)!=len(native):
        errors.append(f'{part}:{gate} control interval count {len(source)} -> {len(native)}')
    for control_index,(before,after) in enumerate(zip(source,native)):
        for boundary in (0,1):
            delta=after[boundary]-before[boundary]
            checks.append(dict(part=part,gate=gate,control=control_index,
                property=('control_start','control_end')[boundary],error_samples=delta))
            if abs(delta)>2:
                errors.append(f'{part}:{gate} control {control_index} boundary error {delta}')
    return checks,errors



def _projection_transform(timing,loop_start=None,*,startup_clocks=1):
    """Use the converter's recorded allocation, including repeated source loops."""
    boundaries=timing['source_boundaries']
    if not boundaries:
        raise ValueError('Projection has no recorded source boundaries')
    clock={}
    for row in boundaries:
        sample,tick=row['source_sample'],row['target_clock']
        if sample in clock and clock[sample]!=tick:
            raise ValueError(f'Conflicting recorded target clocks for source sample {sample}')
        clock[sample]=tick
    end=max(clock)
    spc=timing['samples_per_clock']
    if loop_start is not None:
        if loop_start not in clock:
            raise ValueError(f'Missing recorded target clock for source sample {loop_start}')
        if loop_start>=end or clock[loop_start]>=clock[end]:
            raise ValueError('Projection has no positive recorded loop duration')
    def projected(sample):
        iteration=0;original=sample
        if loop_start is not None and sample>end:
            duration=end-loop_start
            iteration=(sample-loop_start)//duration
            original=loop_start+(sample-loop_start)%duration
        if original not in clock:
            raise ValueError(f'Missing recorded target clock for source sample {original} (requested {sample})')
        tick=clock[original]
        if iteration:
            tick+=iteration*(clock[end]-clock[loop_start])
        return (tick+startup_clocks)*spc
    return projected


def _normalized_analysis(analysis,report):
    normalization=report.get('normalization',{})
    omissions=normalization.get('omitted_fm_gates',()) if normalization.get('applied') else ()
    event_ids={row[field] for row in omissions for field in ('on_event_id','off_event_id')}
    if not event_ids:
        return analysis,[]
    from mucom_normalization import apply_fm_omissions
    return apply_fm_omissions(analysis,event_ids),list(omissions)


def verify(source,mub,outdir,*,projection,tool=DEFAULT_TOOL):
    source,mub,outdir,tool=map(Path,(source,mub,outdir,tool))
    outdir.mkdir(parents=True,exist_ok=True)
    target_report=json.loads(Path(projection).read_text(encoding='utf-8'))
    if target_report.get('status')!='converted':
        raise ValueError('Projection must describe a converted MUC')
    if target_report.get('source_sha256')!=hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError('Projection source hash does not match the supplied VGM')
    from mucom_compiler import PCM_REPLAY
    if tool==DEFAULT_TOOL and target_report.get('pcm_projection',{}).get('compiler_requirements'):
        tool=PCM_REPLAY
    # Pinned NET6 native replay begins at clock0; the retained legacy tool
    # begins at clock1. This is a measured tool profile, not fitted to a song.
    startup_clocks=0 if tool.resolve()==PCM_REPLAY.resolve() else 1
    timing=target_report['timing'];spc=timing['samples_per_clock']
    local=outdir/'native.mub';generated=outdir/'native.vgm'
    shutil.copyfile(mub,local);generated.unlink(missing_ok=True)
    flags={'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if sys.platform=='win32' else {}
    result=subprocess.run([str(tool.resolve()),_tool_argument(local)],cwd=outdir,
                          capture_output=True,timeout=60,**flags)
    (outdir/'replay.log').write_bytes(result.stdout+result.stderr)
    if result.returncode or not generated.is_file():raise RuntimeError('Native MUB-to-VGM failed; see replay.log')
    raw=read_vgm_bytes(source);loop=read_loop_metadata(raw)
    song_loop=target_report.get('song_loop',{}).get('enabled',True)
    iterations=2 if song_loop and loop['status']=='valid' else 1
    old,new={},{}
    parse_vgm(str(source),str(outdir/'source'),opna_metadata=old,dump_opna_segments=True,loop_count=iterations)
    parse_vgm(str(generated),str(outdir/'native'),opna_metadata=new,dump_opna_segments=True)
    source_analysis,omissions=_normalized_analysis(old['analysis'],target_report)
    expected=build_fm_gates(source_analysis);actual=build_fm_gates(new['analysis'])
    start=loop.get('loop_start_samples') if song_loop else None
    if start is None and song_loop and loop['status']=='valid':start=loop['loop_start_vgmticks']
    projected=_projection_transform(timing,start,startup_clocks=startup_clocks)
    source_end=old['analysis'].source_end_vgmticks
    target_end=projected(source_end)
    errors=[];checks=[]
    from types import SimpleNamespace
    cropped={p:tuple(g for g in group if g.start<target_end-2) for p,group in actual.items()}
    try:
        audit_fm_key_edges(source_analysis,expected)
        audit_fm_key_edges(SimpleNamespace(events=tuple(e for e in new['analysis'].events
                              if e.vgmticks<target_end-2)),cropped)
    except ValueError as error:errors.append('Ordered FM edge audit: '+str(error))
    for channel in range(6):
        before=[(projected(e.vgmticks),e.state.key_mask) for e in source_analysis.events
                if e.ch==channel and e.register==0x28 and (e.rising_mask or e.falling_mask)
                and projected(e.vgmticks)<target_end-2]
        after=[(e.vgmticks,e.state.key_mask) for e in new['analysis'].events
                if e.ch==channel and e.register==0x28 and (e.rising_mask or e.falling_mask)
                and e.vgmticks<target_end-2]
        if len(before)!=len(after):errors.append(f'ch{channel}: operator key edge count {len(before)} -> {len(after)}')
        for index,(source_edge,native_edge) in enumerate(zip(before,after)):
            if source_edge[1]!=native_edge[1] or abs(source_edge[0]-native_edge[0])>2:
                errors.append(f'ch{channel}: operator key edge {index} differs after timing projection')
    for part in sorted(set(expected)|set(actual)):
        targets=[g for g in actual.get(part,()) if g.start<target_end-2]
        sources=expected.get(part,())
        if len(targets)!=len(sources):errors.append(f'{part}: gate count {len(sources)} -> {len(targets)}')
        for index,(gate,other) in enumerate(zip(sources,targets)):
            for field in ('start','end'):
                delta=getattr(other,field)-projected(getattr(gate,field))
                checks.append(dict(part=part,gate=index,property=field,error_samples=delta))
                if abs(delta)>2:errors.append(f'{part}:{index} {field}: projected timing error {delta}')
            source_clock=old['analysis'].events[0].clock_hz
            native_clock=new['analysis'].events[0].clock_hz
            source_timeline=_timeline(gate.controls,projected,source_clock,native_clock)
            native_timeline=_timeline(other.controls,lambda t:t,native_clock,native_clock)
            control_checks,control_errors=_compare_timelines(source_timeline,native_timeline,part,index)
            checks.extend(control_checks);errors.extend(control_errors)
            for control in gate.controls:
                first,last=projected(control.start),projected(control.end)
                if last-first<1:continue # Explicitly collapsed in the projection report.
                point=(first+last)/2
                candidate=next((c for c in other.controls if c.start-2<=point<c.end+2),None)
                if candidate is None:
                    errors.append(f'{part}:{index} missing control interval {control.start}');continue
                s,n=control.state,candidate.state
                observed=s.fnum*2**s.block*old['analysis'].events[0].clock_hz*6/s.fm_prescale_divisor
                replay=n.fnum*2**n.block*new['analysis'].events[0].clock_hz*6/n.fm_prescale_divisor
                pitch_error=replay-observed
                allowance=new['analysis'].events[0].clock_hz*2**n.block/2+1
                if abs(pitch_error)>allowance:errors.append(f'{part}:{index} pitch differs at {control.start}')
                for field in ('key_mask','algorithm','feedback','left_enabled','right_enabled','ams','pms','lfo_enabled','lfo_rate'):
                    if (getattr(s,field) or 0)!=(getattr(n,field) or 0):
                        errors.append(f'{part}:{index} {field} differs at {control.start}')
                for slot,(a,b) in enumerate(zip(s.operators,n.operators)):
                    for field in FIELDS:
                        if (getattr(a,field) or 0)!=(getattr(b,field) or 0):
                            errors.append(f'{part}:{index} op{slot} {field} differs at {control.start}')
    had_source_fm=any(e.register==0x28 and e.rising_mask for e in old['analysis'].events)
    status=('passed' if not errors else 'failed') if had_source_fm or any(actual.values()) else 'not_applicable'
    report=dict(status=status,scope='FM gates and positive control timelines after recorded timing and authorized output omissions',
                source=str(source),mub=str(mub),native_vgm=str(generated),source_iterations=iterations,
                assumed_native_startup_samples=startup_clocks*spc,native_timing_tolerance_samples=2,
                expected_gates={p:len(g) for p,g in expected.items()},checks=checks,errors=errors,
                authorized_omitted_fm_gates=omissions,
                tool_sha256=hashlib.sha256(tool.read_bytes()).hexdigest(),
                limits=['Authorized complete short FM gates are checked as intentional omissions, not source-exact replay',
                        'No waveform/listening claim','SSG/rhythm/PCM equivalence is outside this check',
                        'Control intervals explicitly collapsed by MUC timing are not positive replay intervals',
                        'Released-envelope control units are emitted but not covered by this gate check'])
    (outdir/'comparison.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('mub');parser.add_argument('--projection',required=True)
    parser.add_argument('--outdir',required=True);parser.add_argument('--tool',default=DEFAULT_TOOL)
    args=parser.parse_args(argv)
    result=verify(args.source,args.mub,args.outdir,projection=args.projection,tool=args.tool)
    print(result['status']);return int(result['status']!='passed')


if __name__=='__main__':raise SystemExit(main())
