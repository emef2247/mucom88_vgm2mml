"""OPNA source evidence -> whole musical units -> native MUCOM88 MUC."""
from dataclasses import dataclass
import csv
import json
import hashlib
import re
import time
from bisect import bisect_right
from pathlib import Path

from conversion_config import inspect_source
from vgm_io import read_vgm_bytes
from vgm_reader import parse_vgm
from vgm_timing import command_times
from opna_ssg import build_segments as build_ssg
from opna_music import build_fm_gates, dump_gates
from mucom_structure import structure_track, repeat_depth
from mucom_compaction import compact
from mml_format import wrap_track
from mucom_aux import project_ssg, project_rhythm, project_pcm
from mucom_fm import project_state, resolve_gate_pitch

TARGET_CLOCK_HZ=7987200
TIMER_UNIT_SAMPLES=2304*44100/TARGET_CLOCK_HZ


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    part: str
    start: int
    end: int
    kind: str
    slices: tuple
    source_key: tuple
    source_segment_ids: tuple=()
    source_event_ids: tuple=()
    control_keys: tuple=()
    control_membership: tuple=()

    @property
    def key(self):
        return self.kind,self.end-self.start,self.source_key


def choose_clock(units,end,loop_start,bound_ms,*,seams=(),extra_times=()):
    bound=bound_ms*44100/1000
    if not 0<bound_ms<=100:
        raise ConversionError('MUCOM timing bound must be greater than zero and at most 100 ms')
    times={0,end,*extra_times}
    if loop_start is not None:times.add(loop_start)
    spans=[]
    for track in units.values():
        for unit in track:
            if unit.kind not in ('release_controls','rhythm_controls'):spans.append((unit.start,unit.end))
            if unit.kind=='pcm' and unit.slices:spans.append(unit.slices[0][:2])
            for start,stop,_,_ in unit.slices:
                times.update((start,stop))
    times.update(seam["head_key_sample"] for seam in seams if seam["mode"]=="head_keyoff")
    times=sorted(times)
    candidates=[]
    for timer in range(256):
        spc=(256-timer)*TIMER_UNIT_SAMPLES
        ticks={sample:round(sample/spc) for sample in times}
        error=max((abs(ticks[t]*spc-t) for t in times),default=0)
        if error>bound or any(a<b and ticks[a]>=ticks[b] for a,b in spans):continue
        if loop_start is not None and ticks[end]<=ticks[loop_start]:continue
        if any(seam["mode"]=="head_keyoff" and ticks[seam["head_key_sample"]]!=ticks[loop_start] for seam in seams):continue
        conventional=sum(ticks[u.end]-ticks[u.start] in (8,12,16,24,32,48,64,96,128)
                         for track in units.values() for u in track)
        candidates.append((conventional,-abs(timer-202),timer,spc,ticks,error))
    if not candidates:
        raise ConversionError('Native Timer-B cannot retain positive gates within the declared timing bound')
    _,_,timer,spc,ticks,error=max(candidates,key=lambda row:row[:3])
    report=dict(timer_b=timer,whole_note_clocks=128,samples_per_clock=spc,
                boundary_error_limit_samples=bound,max_absolute_error_samples=error,
                candidate_count=len(candidates),source_boundaries=[dict(source_sample=t,
                target_clock=ticks[t],error_samples=ticks[t]*spc-t) for t in times])
    if loop_start is not None:
        report['loop_duration_error_samples']=(ticks[end]-ticks[loop_start])*spc-(end-loop_start)
    return ticks,report


def timed(note,length,*,key_delay=None):
    if length<=0:raise ConversionError('A positive musical interval collapsed in target timing')
    parts=[]
    while length:
        chunk=min(111,length)
        if parts and note!='r':parts.append('&')
        if key_delay and note!='r':parts.append(key_delay)
        parts.append(f'{note}%{chunk}')
        length-=chunk
    return ' '.join(parts)


def merge_items(part,items,source_members=(),*,raw=None,pcm_samples=()):
    """Construct auxiliary musical units from native evidence, not target text."""
    units=[];groups=[]
    for start,end,prefix,identity in items:
        if end<start or (end==start and part!='G'):continue
        if (groups and identity is not None and groups[-1][-1][3]==identity
                and groups[-1][-1][1]==start):groups[-1].append((start,end,prefix,identity))
        else:groups.append([(start,end,prefix,identity)])
    rhythm=[];registers=[0]*32;latest=[None]*32
    if part=='G':
        if raw is None:raise ConversionError('Rhythm musical units require raw source evidence')
        for event in source_members:
            if event.command!=0x56:continue
            register,data=raw[event.address+1:event.address+3]
            if not 0<=register<=0x1f:continue
            rhythm.append((event,register,data,tuple(registers),tuple(latest)))
            registers[register]=data
            latest[register]=event.source_event_id
    samples={sample.sample_id:sample for sample in pcm_samples}
    for group in groups:
        slices=[]
        for start,end,prefix,_ in group:
            command,note=prefix.rsplit(' ',1)
            if slices and slices[-1][2:]==(command,note):
                previous=slices.pop();slices.append((previous[0],end,command,note))
            else:slices.append((start,end,command,note))
        start,end=group[0][0],group[-1][1]
        if part in 'DEF':
            members=tuple((index,s) for index,s in enumerate(source_members)
                          if s.vgmticks<end and s.vgmticks_end>start)
            source_key=tuple((s.vgmticks-start,s.vgmticks_end-s.vgmticks,
                              tuple(s.registers),s.prescale,s.envelope_trigger)
                             for _,s in members)
            segment_ids=tuple(f'ssg:{s.ch}:{index}' for index,s in members)
            event_ids=tuple(dict.fromkeys(s.source_event_id for _,s in members
                                         if s.source_event_id is not None))
        elif part=='K':
            epoch_id=group[0][3][1] if group[0][3] and group[0][3][0]=='pcm' else None
            if epoch_id is None:
                members=tuple(s for s in source_members if s.start_vgmticks<end and
                              (s.end_vgmticks>start or s.start_vgmticks==s.end_vgmticks==start))
            else:
                members=tuple(s for s in source_members if s.playback_id==epoch_id
                              and ((s.start_vgmticks<end and s.end_vgmticks>start)
                                   or s.start_vgmticks==s.end_vgmticks and start<=s.start_vgmticks<=end))
            source_key=tuple((s.start_vgmticks-start,s.end_vgmticks-s.start_vgmticks,
                              samples[s.sample_id].sha256,s.rate_num,s.rate_den,
                              s.level,s.pan,s.repeat,s.memory_mode,tuple(s.registers),
                              s.continuation_index,s.termination) for s in members)
            segment_ids=tuple(s.segment_id for s in members)
            event_ids=tuple(dict.fromkeys(event_id for s in members
                                         for event_id in s.source_event_ids))
        elif part=='G':
            rows=[row for row in rhythm if start<=row[0].vgmticks<end
                  or (start==end==row[0].vgmticks)]
            if not rows:raise ConversionError('Rhythm action unit lacks native source controls')
            control_indices=(0xb,0xc,0xd,0x11,0x18,0x19,0x1a,0x1b,0x1c,0x1d)
            source_key=(tuple(rows[0][3][i] for i in control_indices),
                        tuple((row[0].vgmticks-start,row[1],row[2]) for row in rows))
            segment_ids=tuple(f'rhythm:{row[0].source_event_id}' for row in rows
                              if row[1]==0x10)
            event_ids=tuple(dict.fromkeys(
                [rows[0][4][i] for i in control_indices if rows[0][4][i] is not None]
                +[row[0].source_event_id for row in rows]))
        else:
            raise ConversionError(f'Unknown auxiliary musical part {part}')
        if not source_key:raise ConversionError(f'{part}: musical unit lacks native source membership')
        slice_keys=[];slice_membership=[]
        for index,(slice_start,slice_end,_,_) in enumerate(slices):
            if part in 'DEF':
                evidence=tuple((member.vgmticks-slice_start,member.vgmticks_end-member.vgmticks,
                                tuple(member.registers),member.prescale,member.envelope_trigger)
                               for _,member in members if member.vgmticks<slice_end and member.vgmticks_end>slice_start)
            elif part=='K':
                evidence=tuple((member.start_vgmticks-slice_start,member.end_vgmticks-member.start_vgmticks,
                    samples[member.sample_id].sha256,member.rate_num,member.rate_den,member.level,member.pan,
                    member.repeat,member.memory_mode,tuple(member.registers),member.continuation_index,member.termination)
                    for member in members if member.start_vgmticks<slice_end and member.end_vgmticks>slice_start)
            else:
                selected=[row for row in rows if slice_start<=row[0].vgmticks<slice_end
                          or slice_start==slice_end==row[0].vgmticks]
                evidence=(tuple(selected[0][3][i] for i in control_indices),
                          tuple((row[0].vgmticks-slice_start,row[1],row[2]) for row in selected)) if selected else ()
            action=('unit_entry' if index==0 else 'unit_control') if part in 'DEFK' else 'ordered_actions'
            slice_keys.append((slice_end-slice_start,(action,evidence)))
            if part in 'DEF':
                selected_members=[(sid,member) for sid,member in members
                                  if member.vgmticks<slice_end and member.vgmticks_end>slice_start]
                ids=tuple(f'ssg:{member.ch}:{sid}' for sid,member in selected_members)
                events=tuple(dict.fromkeys(member.source_event_id for _,member in selected_members
                                         if member.source_event_id is not None))
            elif part=='K':
                selected_members=[member for member in members
                    if member.start_vgmticks<slice_end and member.end_vgmticks>slice_start]
                ids=tuple(member.segment_id for member in selected_members)
                events=tuple(dict.fromkeys(eid for member in selected_members for eid in member.source_event_ids))
            else:
                ids=tuple(f'rhythm:{row[0].source_event_id}' for row in selected if row[1]==0x10)
                events=tuple(dict.fromkeys([selected[0][4][i] for i in control_indices
                    if selected[0][4][i] is not None]+[row[0].source_event_id for row in selected])) if selected else ()
            slice_membership.append((ids,events))
        units.append(Unit(part,start,end,'ssg' if part in 'DEF' else 'rhythm_controls' if part=='G' else 'pcm',
                          tuple(slices),source_key,segment_ids,event_ids,tuple(slice_keys),tuple(slice_membership)))
    return units


def analyze_fm_seams(analysis,gates,loop_start,end):
    """Resolve observed EOF-held gates against ordered loop-head key writes."""
    from opna_music import semantic_state
    seams=[]
    for part,channel_gates in gates.items():
        channel=channel_gates[0].channel
        last=next(event for event in reversed(analysis.events) if event.ch==channel)
        if not last.state.key_mask:continue
        if loop_start is None:continue
        head=next((event for event in analysis.events
                   if event.ch==channel and event.register==0x28
                   and event.vgmticks>=loop_start),None)
        crossing=next((gate for gate in channel_gates if gate.start<loop_start<gate.end),None)
        state_at_loop=next((control.state for control in crossing.controls
                           if control.start<=loop_start<control.end),None) if crossing else None
        record=dict(part=part,source_eof=end,source_loop_start=loop_start,
                    eof_event_id=last.source_event_id,eof_key_mask=last.state.key_mask,
                    head_key_event_id=head.source_event_id if head else None,
                    head_key_sample=head.vgmticks if head else None,
                    head_key_mask=head.state.key_mask if head else None,
                    source_segment_ids=list(channel_gates[-1].source_segment_ids))
        if (state_at_loop is not None and semantic_state(state_at_loop)==semantic_state(last.state)
                and (not state_at_loop.channel3_special_enabled or
                     state_at_loop.special_frequencies==last.state.special_frequencies)):
            record['mode']='stable_carry'
            record['crossing_source_segment_ids']=list(crossing.source_segment_ids)
        elif head is not None and not head.state.key_mask:
            record['mode']='head_keyoff'
        else:
            raise ConversionError(f'{part}: EOF-held FM loop seam has no ordered full head key-off or stable carry')
        seams.append(record)
    return seams


def analyze_ssg_seams(analysis,tracks,loop_start,end,*,clock=None,events=(),raw=None,audit=None):
    if loop_start is None:return []
    from opna_ssg_loop import semantic_state,prove_loop_states
    seams=[];writes=[]
    if raw is not None:
        writes=[(event.vgmticks,raw[event.address+1],raw[event.address+2],event.source_event_id)
            for event in events if event.command==0x56 and raw[event.address+1]<=15]
    for channel,segments in analysis.segments.items():
        part='DEF'[channel]
        if part not in tracks:continue
        crossing=next((u for u in tracks[part] if u.start<loop_start<u.end),None)
        if crossing is None:continue
        head=next((s for s in reversed(segments) if s.vgmticks<=loop_start<s.vgmticks_end),None)
        tail=next((s for s in reversed(segments) if s.vgmticks<=end and s.vgmticks_end==end),None)
        if head is None or tail is None:continue
        stable=tail.scale!='r' and semantic_state(head)==semantic_state(tail)
        checks=[];head_ids=[];reason='Semantic source state is stable across loop'
        if clock is not None and raw is not None:
            valid,checks,head_ids,reason=prove_loop_states(head,tail,channel,writes,clock,loop_start,end)
            if not valid:
                if audit is not None:audit.append(dict(part=part,accepted=False,reason=reason,checks=checks))
                continue
            if not stable and (head.envelope_enabled or tail.envelope_enabled):
                if audit is not None:audit.append(dict(part=part,accepted=False,
                    reason='Active hardware-envelope loop restoration requires phase-aware projection',checks=checks))
                continue
        elif not stable:continue
        mode='stable_carry' if stable else 'observed_head_restore'
        seam=dict(part=part,mode=mode,source_loop_start=loop_start,source_eof=end,
             head_source_event_id=head.source_event_id,eof_source_event_id=tail.source_event_id,
             crossing_source_segment_ids=list(crossing.source_segment_ids),
             tail_sounding=tail.scale!='r',head_restore_event_ids=head_ids,
             register_loop_checks=checks,reason=reason,
             hardware_envelope_phase='Not certified; observed global shape triggers remain separate')
        seams.append(seam)
        if audit is not None:audit.append(dict(part=part,accepted=True,reason=reason,checks=checks))
    return seams


def repeat_tree_depth(nodes):
    return max((1+repeat_tree_depth(node.children) if node.children else 0
                for node in nodes),default=0)


def control_keys(unit):
    if unit.control_keys:
        if len(unit.control_keys)!=len(unit.slices):
            raise ConversionError('Control keys no longer match native slices')
        return unit.control_keys
    # Unknown auxiliary membership remains conservative. It cannot nominate
    # repeats solely because its target spelling happens to match.
    return tuple((stop-start,('source_slice',unit.source_key,index))
                 for index,(start,stop,_,_) in enumerate(unit.slices))


def control_membership(unit):
    if unit.control_membership:
        if len(unit.control_membership)!=len(unit.slices):
            raise ConversionError('Control membership no longer matches native slices')
        return unit.control_membership
    return tuple((unit.source_segment_ids,unit.source_event_ids) for _ in unit.slices)


def split_unit_at_loop(unit,loop_start,*,restore_ssg=False):
    before=[];after=[];before_keys=[];after_keys=[];before_members=[];after_members=[];key_delay=None
    for row,key,membership in zip(unit.slices,control_keys(unit),control_membership(unit)):
        start,stop,prefix,note=row
        match=re.search(r'\bKD[0-9]+,[0-9]+,[0-9]+,[0-9]+',prefix)
        if match:key_delay=match.group(0)
        if stop<=loop_start:
            before.append(row);before_keys.append(key);before_members.append(membership)
        elif start>=loop_start:
            if not after and key_delay and not match:
                prefix=key_delay+' '+prefix
            after.append((start,stop,prefix,note));after_keys.append(key);after_members.append(membership)
        else:
            before.append((start,loop_start,prefix,note))
            before_keys.append((loop_start-start,key[1]));before_members.append(membership)
            restored_prefix=key_delay or ''
            if restore_ssg and unit.kind=='ssg':
                restored_prefix=re.sub(r'\bE[0-9]+(?:,[0-9]+){5}\s*','',prefix)
            after.append((loop_start,stop,restored_prefix,note))
            continuation=key[1]
            if unit.kind=='fm' and continuation[0]=='attack':
                continuation=('held_control',continuation[1])
            if unit.kind=='ssg' and continuation[0]=='unit_entry':
                continuation=('unit_control',continuation[1])
            after_keys.append((stop-loop_start,continuation));after_members.append(membership)
    return (Unit(unit.part,unit.start,loop_start,unit.kind,tuple(before),unit.source_key,
                 unit.source_segment_ids,unit.source_event_ids,tuple(before_keys),tuple(before_members)),
            Unit(unit.part,loop_start,unit.end,unit.kind,tuple(after),unit.source_key,
                 unit.source_segment_ids,unit.source_event_ids,tuple(after_keys),tuple(after_members)))


def group_release_controls(units):
    """Retain timed, ordered released actions as one inspectable trajectory."""
    result=[];run=[]
    def flush():
        if any(unit.kind=='release_controls' for unit in run):
            slices=tuple(row for unit in run for row in unit.slices)
            keys=tuple((stop-start,(unit.kind,unit.source_key))
                       for unit in run for start,stop,_,_ in unit.slices)
            result.append(Unit(run[0].part,run[0].start,run[-1].end,'release_controls',
                slices,tuple(unit.key for unit in run),
                tuple(sid for unit in run for sid in unit.source_segment_ids),
                tuple(eid for unit in run for eid in unit.source_event_ids),keys,
                tuple(member for unit in run for member in control_membership(unit))))
        else:result.extend(run)
        run.clear()
    for unit in units:
        if unit.kind in ('release_controls','rest'):run.append(unit)
        else:flush();result.append(unit)
    flush();return result


def render_track(units,end,loop_start,clock,*,loops=True,seam=None,audit=None,normalize_lengths=False):
    participating=loop_start is not None and any(u.end>loop_start for u in units)
    full=[];cursor=0;marked=False;tie_before_marker=set()
    def rest(stop):
        nonlocal cursor
        if stop>cursor:
            full.append(Unit(units[0].part,cursor,stop,'rest',((cursor,stop,'','r'),),('rest',)))
            cursor=stop
    def marker():
        nonlocal marked
        rest(loop_start)
        full.append(Unit(units[0].part,loop_start,loop_start,'song_loop',(),('L',)))
        marked=True
    for unit in units:
        if unit.start<cursor:raise ConversionError(f'{unit.part}: overlapping musical units at {unit.start}')
        if participating and not marked and unit.start>=loop_start:marker()
        if participating and not marked and unit.start<loop_start<unit.end:
            if clock[unit.end]==clock[loop_start]:
                rest(unit.start);full.append(unit);cursor=loop_start;marker();continue
            if clock[unit.start]==clock[loop_start]:
                marker()
            elif unit.kind=='rhythm_controls' or (unit.kind in ('fm','ssg') and seam and seam['mode'] in ('stable_carry','observed_head_restore')):
                before,after=split_unit_at_loop(unit,loop_start,restore_ssg=seam is not None and seam['mode']=='observed_head_restore')
                rest(unit.start);full.append(before);cursor=loop_start
                if unit.kind in ('fm','ssg'):tie_before_marker.add(id(before))
                marker();full.append(after);cursor=unit.end;continue
            else:
                raise ConversionError(f'{unit.part}: held gate across song-loop boundary at {loop_start}')
        rest(unit.start);full.append(unit);cursor=unit.end
    if participating and not marked:marker()
    if participating or loop_start is None:rest(end)
    full=group_release_controls(full)
    commands=[];slice_commands=[];slice_keys=[]
    for unit in full:
        if unit.kind=='song_loop':
            commands.append('L');slice_commands.append(('L',));slice_keys.append((unit.key,));continue
        pieces=[];key_delay=None;positive=False
        for start,stop,prefix,note in unit.slices:
            length=clock[stop]-clock[start];words=[]
            if positive and length>0 and unit.kind in ('fm','ssg','pcm'):
                words.append('&')
            match=re.search(r'\bKD[0-9]+,[0-9]+,[0-9]+,[0-9]+',prefix)
            if match:key_delay=match.group(0)
            if prefix:words.append(prefix)
            if length>0:words.append(timed(note,length,key_delay=key_delay));positive=True
            pieces.append(' '.join(words))
        if id(unit) in tie_before_marker or (seam and seam['mode'] in ('stable_carry','observed_head_restore') and seam.get('tail_sounding',True) and unit.kind in ('fm','ssg') and unit.end==end):
            pieces[-1]+=' &'
        commands.append(' '.join(pieces))
        slice_commands.append(tuple(pieces));slice_keys.append(control_keys(unit))
    slice_durations=[tuple(clock[stop]-clock[start] for start,stop,_,_ in unit.slices)
                     if unit.kind!='song_loop' else (0,) for unit in full]
    plan,folds,text,detail=structure_track(full,commands,slice_commands,slice_keys,slice_durations,loops=loops,normalize_lengths=normalize_lengths)
    compacted,decisions=compact(text,units[0].part,durations=loops)
    native_depth=repeat_depth(compacted)
    if native_depth>16:raise ConversionError('Native repeat nesting exceeds 16 after target compaction')
    if audit is not None:
        audit.update(detail,uncompacted=text,compaction=decisions,
                     slice_commands=slice_commands,slice_keys=slice_keys)
    return full,plan,folds,compacted,participating,native_depth


def convert_vgm(input_path,outdir,*,dump_passes=True,diagnostics_outdir=None,
                timing_bound_ms=8.0,loops=True,song_loop=True,normalize_lengths=True,normalization_ms=8.0,
                pcm_only=False,pcm_policy="strict"):
    source,output=Path(input_path),Path(outdir)
    output.mkdir(parents=True,exist_ok=True)
    passes=Path(diagnostics_outdir) if diagnostics_outdir else output
    passes.mkdir(parents=True,exist_ok=True)
    stem=source.stem;manifest=output/(stem+'.mucom.json')
    report=dict(schema_version=2,target='mucom88',status='analyzing',source=str(source),
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                native_target_clock_hz=TARGET_CLOCK_HZ,source_passes=str(passes),diagnostics=[],
                tracks={},fm_projection=[],fm_initial_pitch_resolution=[],pcm_bindings=[],stage_timings={},limits=[
                'Source State/Segments are intact; MUC timing is target-only.',
                'Native startup latency, envelope/acoustic identity and listening are separate checks.'])
    def save():manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    stage_started=None
    def finish_stage():
        nonlocal stage_started
        if stage_started is not None:
            elapsed=time.perf_counter()-stage_started
            report['stage_timings'][report['stage']]=report['stage_timings'].get(report['stage'],0)+elapsed
            stage_started=None
            report['current_stage_completed']=True
    def progress(stage):
        nonlocal stage_started
        finish_stage()
        report['stage']=stage
        stage_started=time.perf_counter()
        report['current_stage_started_at_unix']=time.time()
        report['current_stage_completed']=False
        save()
        print('MUC stage: '+stage,flush=True)
    try:
        from conversion_config import normalization_samples
        normalization_samples(normalization_ms)
        progress('source_preflight')
        usage=inspect_source(source);report['source_usage']=usage
        if usage['unsupported_commands']:
            issue=usage['unsupported_commands'][0]
            raise ConversionError(f"Unsupported source {issue['command']} at {issue['address']:#x}: {issue['reason']}")
        raw=read_vgm_bytes(source);events=list(command_times(raw,loop_count=1))
        if pcm_policy not in ('strict','best-effort'):
            raise ConversionError('PCM policy must be strict or best-effort')
        used=set(usage['used_chips'])-set(usage['compatibility_initialization'])
        external_pcm=used & {'pcm','segapcm'}
        interop_kind=None
        if external_pcm:
            if len(external_pcm)!=1:
                raise ConversionError('Simultaneous MSM6258 and SegaPCM projection is unsupported')
            if pcm_policy!='best-effort':
                raise ConversionError('MSM6258/SegaPCM transcoding requires --pcm-policy best-effort')
            excluded=used-{'opna',*external_pcm}
            if excluded and not pcm_only:
                raise ConversionError('Unsupported FM/other source projection: '+', '.join(sorted(excluded))+
                                      '; use --pcm-only to explicitly extract PCM')
            report['excluded_sources']=sorted(used-external_pcm) if pcm_only else []
            report['pcm_policy']=pcm_policy
            interop_kind='msm6258' if 'pcm' in external_pcm else 'segapcm'
        elif pcm_only:
            raise ConversionError('--pcm-only requires an MSM6258 or SegaPCM source')
        interop=None
        allowed={0x56,0x57,0x61,0x62,0x63,0x64,0x66,0x67}
        for command,chip in ((0xa0,'psg'),(0xd2,'scc'),(0x51,'opll')):
            if chip in usage['compatibility_initialization']:allowed.add(command)
        if interop_kind:
            allowed.update((0xb7,0xc0,0x90,0x91,0x92,0x93,0x94,0x95))
            if pcm_only: allowed.update((0x54,0xa4,0xa0,0xd2,0x51))
        unsupported=next((e for e in events if e.command not in allowed and not 0x70<=e.command<=0x7f),None)
        if unsupported:raise ConversionError(f'Unsupported active command {unsupported.command:#x} at {unsupported.address:#x}')
        metadata,pcm_metadata,loop={},{},{}
        progress('native_state_segments_pcm_ir')
        parse_vgm(str(source),str(passes),opna_metadata=metadata,opna_pcm_metadata=pcm_metadata,
                  loop_metadata=loop,dump_loop=True,dump_opna_segments=True,
                  dump_opm_segments=bool(interop_kind),loop_count=1)
        analysis=metadata.get('analysis')
        if analysis is None and interop_kind:
            from opna import OpnaAnalysis
            analysis=OpnaAnalysis((),(),metadata['source_end_vgmticks'],False,(),())
        if analysis is None:raise ConversionError('No native YM2608 source evidence')
        if analysis.csm_observed and not pcm_only:raise ConversionError('FM: CSM timer-generated gates are not interpreted')
        if loop['status'] not in ('valid','no_loop'):raise ConversionError('Invalid declared VGM loop')
        end=analysis.source_end_vgmticks
        source_loop_start=loop.get('loop_start_samples')
        loop_start=source_loop_start if song_loop else None
        report['song_loop']=dict(enabled=song_loop,source_start_samples=source_loop_start,
                                 target_start_samples=loop_start)
        native_pcm=pcm_metadata['analysis']
        if interop_kind:
            if native_pcm.segments and not pcm_only:
                raise ConversionError('Native ADPCM-B and external PCM cannot share MUCOM part K')
            progress('external_pcm_projection')
            from pcm_mucom import project_input
            interop=project_input(raw,kind=interop_kind,outdir=passes,stem=stem,loop_start=loop_start)
            report['pcm_interop']=interop.report
            if pcm_only:
                from opna import OpnaAnalysis
                analysis=OpnaAnalysis((),(),end,False,(),())
            pcm_metadata['analysis']=interop.normalization_view
        source_clock=analysis.events[0].clock_hz if analysis.events else (TARGET_CLOCK_HZ if interop_kind else pcm_metadata['analysis'].clock_hz)
        report['source_clock_hz']=source_clock if metadata['clock_hz'] else None
        if source_clock!=TARGET_CLOCK_HZ:
            report['diagnostics'].append('Alternate source clock: pitch/rate projected; envelope, detune and hardware-LFO physical rates are not corrected')
        progress('musical_gate_construction')
        gates=build_fm_gates(analysis);dump_gates(gates,passes/(stem+'.opna.music.csv'))
        from opna_music import audit_fm_key_edges
        original_analysis=analysis;original_gates=gates
        seams=analyze_fm_seams(analysis,gates,loop_start,end)
        report['fm_loop_seams']=seams
        if loop_start is None:
            for part,channel_gates in gates.items():
                last=next(event for event in reversed(analysis.events) if event.ch==channel_gates[0].channel)
                if last.state.key_mask:
                    report['diagnostics'].append(f'{part}: source EOF truncates a held FM gate; target ends it at projected EOF')
        progress('auxiliary_eligibility_and_bank_binding')
        tracks={}
        if metadata['csv_path'] and not pcm_only:
            ssg=build_ssg(metadata['csv_path'],end_vgmticks=end)
        else:
            from opna_ssg import SSGAnalysis
            ssg=SSGAnalysis({0:(),1:(),2:()},end,0,0)
        ssg.dump(passes,stem)
        ssg_controls=[]
        for part,items in ({} if pcm_only else project_ssg(ssg,events,raw,source_clock,global_controls=ssg_controls)).items():
            tracks[part]=merge_items(part,items,ssg.segments['DEF'.index(part)])
        if ssg_controls:
            report['diagnostics'].append('Legacy hardware-envelope note attacks add same-clock shape resets; global G controls restore source shapes0..15 after DEF. Hardware-envelope waveform/phase identity is not certified.')
        rhythm=[] if pcm_only else project_rhythm(events,raw,end,extra_controls=ssg_controls)
        if rhythm:tracks['G']=merge_items('G',rhythm,events,raw=raw)
        report['pcm_projection']={}
        if interop is not None:
            pcm_items,bank,bindings=interop.items,interop.bank,interop.bindings
            report['pcm_projection']=interop.report
            tracks['K']=interop.build_units()
        else:
            try:
                pcm_items,bank,bindings=project_pcm(pcm_metadata['analysis'],source_clock,raw=raw,
                    diagnostics=report['diagnostics'],projection=report['pcm_projection'])
            finally:
                if report['pcm_projection'].get('source_epochs'):
                    from opna_adpcm_playback import dump_description
                    dump_description(report['pcm_projection']['source_epochs'],passes,stem)
            if pcm_items:tracks['K']=merge_items('K',pcm_items,pcm_metadata['analysis'].segments,
                pcm_samples=pcm_metadata['analysis'].samples)
        report['pcm_bindings']=bindings
        clock=timing=None
        report['normalization']=dict(enabled=False,applied=False,normalization_ms=float(normalization_ms),
            omitted_fm_gates=[],omitted_pcm_gates=[],known_loss=False,
            reason='Explicit baseline without length normalization')
        if normalize_lengths:
            progress('output_normalization')
            from mucom_normalization import normalize_source
            analysis,tracks,clock,timing,report['normalization']=normalize_source(
                original_analysis,tracks,pcm_metadata['analysis'],end,loop_start,normalization_ms,seams=seams)
            gates=build_fm_gates(analysis)
        audit_fm_key_edges(analysis,gates)
        from opna import dump_analysis
        dump_analysis(analysis,state_csv=passes/(stem+'.mucom.output.state.csv'),
                      segments_csv=passes/(stem+'.mucom.output.segments.csv'))
        dump_gates(gates,passes/(stem+'.mucom.output.music.csv'))
        (passes/(stem+'.mucom.normalization.json')).write_text(
            json.dumps(report['normalization'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        # Preserve the accepted map before FM consumes it, including failures
        # in initial-pitch resolution. Source and output views remain distinct.
        if timing is not None:
            report['timing']=timing
            with (passes/(stem+'.mucom.timing.csv')).open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=('source_sample','target_clock','error_samples'))
                writer.writeheader();writer.writerows(timing['source_boundaries'])
        progress('native_target_projection')
        voices={}
        for part,channel_gates in gates.items():
            tracks[part]=[]
            for gate in channel_gates:
                target_states,resolutions=resolve_gate_pitch(gate,clock)
                report['fm_initial_pitch_resolution'].extend(resolutions)
                slices=[];previous=None;initial_zero_key=False
                for index,(control,target_state) in enumerate(zip(gate.controls,target_states)):
                    state=project_state(target_state,source_clock,voices,
                                        initial=index==0,previous_state=previous,target_channel=gate.channel)
                    command=' '.join(state.commands)
                    if index==0 and (control.start==control.end or
                            clock is not None and clock[control.start]==clock[control.end]):
                        key_channel=gate.channel if gate.channel<3 else gate.channel+1
                        # Explicit key order precedes a tied first note. Seed
                        # its observed/projected pitch before the raw key-on.
                        local=gate.channel%3
                        command+=f' y${0xa4+local:02X},{state.target_block<<3 | state.target_fnum>>8}'
                        command+=f' y${0xa0+local:02X},{state.target_fnum&255}'
                        command+=f' y$28,{control.state.key_mask<<4 | key_channel}'
                        initial_zero_key=True
                    if initial_zero_key and control.end>control.start:
                        command+=' &'
                        initial_zero_key=False
                    slices.append((control.start,control.end,command,state.note))
                    record=dict(part=part,unit_start=gate.start,source_start=control.start,
                                source_end=control.end,source_segment_ids=control.segment_ids,
                                source_event_ids=control.event_ids)
                    from dataclasses import asdict
                    record.update(asdict(state));record['ordered_commands']=command
                    report['fm_projection'].append(record)
                    previous=target_state
                from opna_music import semantic_state
                keys=tuple((control.end-control.start,
                            ('attack' if index==0 else 'held_control',semantic_state(control.state)))
                           for index,control in enumerate(gate.controls))
                tracks[part].append(Unit(part,gate.start,gate.end,'fm',tuple(slices),gate.key,
                                        gate.source_segment_ids,gate.source_event_ids,keys,
                    tuple((control.segment_ids,control.event_ids) for control in gate.controls)))
        # Released envelopes can remain audible. Keep parameter edits after
        # key-off in their own source-linked control units, before the next gate.
        names={0x30:'DM',0x40:'TL',0x50:'KA',0x60:'DR',0x70:'SR',0x80:'SL',0x90:'SE'}
        slots=(1,3,2,4)
        parts=set(original_gates)|set(gates)|{row['part'] for row in report['normalization']['omitted_fm_gates']}
        for part in sorted(parts):
            channel='ABCHIJ'.index(part)
            channel_gates=gates.get(part,())
            tracks.setdefault(part,[])
            gate_starts=[g.start for g in channel_gates]
            omitted=[row for row in report['normalization']['omitted_fm_gates'] if row['part']==part]
            first_gate_end=original_gates[part][0].end if part in original_gates else end
            first_omitted_id=min((row['on_event_id'] for row in omitted),default=None)
            for event in analysis.events:
                if event.ch!=channel or event.state.key_mask or event.register is None:continue
                if first_omitted_id is None:
                    if event.vgmticks<first_gate_end:continue
                elif event.vgmticks<first_gate_end and event.source_event_id<first_omitted_id:continue
                position=bisect_right(gate_starts,event.vgmticks)-1
                if position>=0 and event.vgmticks<channel_gates[position].end:continue
                reg,data=event.register,event.data;prefix=''
                if reg&0xf0 in names and reg&3!=3:
                    prefix=f'y{names[reg&0xf0]},{slots[(reg>>2)&3]},{data}'
                elif 0xb0<=reg<=0xb2:prefix=f'y${reg:02X},{data}'
                elif reg in (0xa0+channel%3,0xa4+channel%3):
                    state=event.state
                    if state.fnum is not None and state.block is not None:
                        scaled=round(state.fnum*source_clock*6/(TARGET_CLOCK_HZ*state.fm_prescale_divisor))
                        if not 0<=scaled<=2047:
                            raise ConversionError(f'{part}: released pitch exceeds native FNUM range at {event.vgmticks}')
                        prefix=f'y${0xa4+channel%3:02X},{state.block<<3 | scaled>>8} y${0xa0+channel%3:02X},{scaled&255}'
                elif reg==0x22:prefix=f'y$22,{data}'
                elif 0xb4<=reg<=0xb6:
                    state=event.state
                    if not (state.left_enabled or state.right_enabled):
                        raise ConversionError(f'FM {part}: native compiler cannot encode pan 0 during release; sample {event.vgmticks}, event {event.source_event_id}')
                    prefix=f'H{state.lfo_rate or 0},{state.pms or 0},{state.ams or 0} p{2*(state.left_enabled or 0)+(state.right_enabled or 0)}'
                    if not state.lfo_enabled:prefix+=' y$22,0'
                    # H enables AM on operators even while their gates are off.
                    # Released envelopes retain their source AM/D1R controls.
                    from mucom_fm import am_restore_commands
                    prefix+=' '+' '.join(am_restore_commands(state))
                if prefix:
                    tracks[part].append(Unit(part,event.vgmticks,event.vgmticks,'release_controls',
                       ((event.vgmticks,event.vgmticks,prefix,'r'),),
                       ((reg,data),),(),(event.source_event_id,)))
            tracks[part].sort(key=lambda u:(u.start,u.kind!='release_controls'))
        # A deliberate whole-gate omission still produces a finite silent part.
        for part in tuple(tracks):
            if not tracks[part]:
                tracks[part]=[Unit(part,0,end,'rest',((0,end,'','r'),),('rest',))]
        if not tracks:raise ConversionError('No sounding YM2608 musical units')
        progress('target_timing')
        if clock is None:
            clock,timing=choose_clock(tracks,end,loop_start,timing_bound_ms,seams=seams,
                extra_times=(event.vgmticks for event in events))
        report['timing']=timing
        report['ssg_loop_analysis']=[]
        ssg_seams=analyze_ssg_seams(ssg,tracks,loop_start,end,clock=clock,events=events,raw=raw,
            audit=report['ssg_loop_analysis'])
        report['ssg_loop_seams']=ssg_seams
        with (passes/(stem+'.mucom.ssg_loop_checks.csv')).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=('part','accepted','target_start_clock','target_end_clock',
                'source_state','repeated_state','equal','source_event_ids','reason'))
            writer.writeheader()
            for analysis_row in report['ssg_loop_analysis']:
                for row in analysis_row['checks']:
                    writer.writerow(dict(row,part=analysis_row['part'],accepted=analysis_row['accepted'],reason=analysis_row['reason']))
        with (passes/(stem+'.mucom.initial_pitch.csv')).open('w',encoding='utf-8',newline='') as stream:
            records=report['fm_initial_pitch_resolution']
            writer=csv.DictWriter(stream,fieldnames=list(records[0]) if records else
                ('part','unit_start','source_start','source_end','target_clock','donor_source_start','reason'))
            writer.writeheader();writer.writerows(records)
        with (passes/(stem+'.mucom.timing.csv')).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=('source_sample','target_clock','error_samples'))
            writer.writeheader();writer.writerows(timing['source_boundaries'])
        report['collapsed_control_intervals']=[dict(part=u.part,source_start=a,source_end=b,
                 target_clock=clock[a],reason='Internal control interval moved to one target clock; gate retained')
                 for track in tracks.values() for u in track for a,b,_,_ in u.slices
                 if a<b and clock[a]==clock[b]]
        for seam in seams:
            seam['target_loop_clock']=clock[loop_start]
            if seam['mode']=='head_keyoff':seam['target_head_key_clock']=clock[seam['head_key_sample']]
        with (passes/(stem+'.mucom.fm_seams.csv')).open('w',encoding='utf-8',newline='') as stream:
            fields=('part','mode','source_loop_start','source_eof','eof_event_id','eof_key_mask',
                    'head_key_event_id','head_key_sample','head_key_mask','target_loop_clock',
                    'target_head_key_clock','source_segment_ids','crossing_source_segment_ids')
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(seams)
        if report['pcm_projection']:
            from mucom_aux import pcm_timing_audit
            pcm_timing_audit(report['pcm_projection'],clock,report['timing'],
                             omitted=report['normalization'].get('omitted_pcm_gates',()))
            if interop is not None:
                from fractions import Fraction
                period=Fraction((256-timing['timer_b'])*2304*44100,TARGET_CLOCK_HZ)
                audit=[]
                for row in interop.report['mapping']:
                    start,stop=row['start_vgmticks'],row['end_vgmticks']
                    gate=(clock[stop]-clock[start])*period
                    rate=Fraction(row['target_rate_num'],row['target_rate_den'])
                    valid=Fraction(row['valid_samples']*44100,1)/rate
                    padded=Fraction((row['valid_samples']+row['padding_samples'])*44100,1)/rate
                    audit.append(dict(segment_id=row['segment_id'],target_start_clock=clock[start],
                        target_end_clock=clock[stop],gate_duration_samples=float(gate),
                        valid_audio_duration_samples=float(valid),padded_audio_duration_samples=float(padded),
                        gate_minus_valid_audio_samples=float(gate-valid),
                        possible_gate_truncation=gate<valid,
                        scope='nominal scheduled gate versus encoded duration; runtime consumption unverified'))
                report['pcm_projection']['target_timing_audit']=audit
            (passes/(stem+'.mucom.pcm_projection.json')).write_text(
                json.dumps(report['pcm_projection'],indent=2)+'\n',encoding='utf-8')
        lines=[f'#title {stem}','#driver mucomDotNET','; Source-derived musical units; inspect source and target pass CSVs.']
        voice_path=output/(stem+'.dat') if voices else None
        pcm_path=output/(stem+'_pcm.bin') if bindings else None
        if voice_path:lines.append('#voice '+voice_path.name)
        if pcm_path:lines.append('#pcm '+pcm_path.name)
        progress('musical_structure_and_rendering')
        rows=[];inner_rows=[];control_rows=[];candidate_rows=[];compaction=[]
        plain_lines=lines[:];uncompacted_lines=lines[:];before_limit_lines=lines[:]
        for part,units in sorted(tracks.items(),key=lambda row:'ABCDEFGHIJK'.index(row[0])):
            audit={}
            full,plan,folds,text,owner,depth=render_track(units,end,loop_start,clock,loops=loops,
                seam=next((s for s in seams+ssg_seams if s["part"]==part),None),audit=audit,
                normalize_lengths=report['normalization']['applied'])
            plan.dump(passes/(stem+f'.mucom.{part}.loops.csv'),[u.source_segment_ids for u in full],
                      audit['outer_projections']['native'])
            audit['feasible_outer'].dump(passes/(stem+f'.mucom.{part}.timing_loops.csv'),
                                        [u.source_segment_ids for u in full],audit['outer_projections']['target_timing'])
            if report['normalization']['applied']:
                audit['normalized_outer'].dump(passes/(stem+f'.mucom.{part}.normalized_loops.csv'),
                    [u.source_segment_ids for u in full],audit['outer_projections']['normalized'])
            with (passes/(stem+f'.mucom.{part}.selected_loops.csv')).open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(folds[0]) if folds else
                    ('occurrence_id','unit_start','unit_end','unit_width','repeats','status','planning_basis','target_selected'))
                writer.writeheader();writer.writerows(folds)
            for index,(unit,child) in enumerate(zip(full,audit['children'])):
                representatives=set(child.representatives())
                for slice_index,(slice_row,key,command) in enumerate(zip(
                        unit.slices,audit['slice_keys'][index],audit['slice_commands'][index])):
                    start,stop,_,_=slice_row
                    segment_ids,event_ids=control_membership(unit)[slice_index]
                    control_rows.append(dict(unit_id=f'{part}:{index}',part=part,slice_index=slice_index,
                        source_start=start,source_end=stop,target_start_clock=clock[start],
                        target_end_clock=clock[stop],source_key=repr(key),command=command,
                        structural_representative=int(slice_index in representatives),
                        source_segment_ids=json.dumps(segment_ids),
                        source_event_ids=json.dumps(event_ids)))
                for basis,child_plan in [('native',child),('target_timing',audit['feasible_children'][index])] + (
                        [('normalized',audit['normalized_children'][index])] if report['normalization']['applied'] else []):
                    if child_plan.structure is not None:
                        for candidates in child_plan.structure.catalog:
                            for candidate in candidates:
                                candidate_rows.append(dict(unit_id=f'{part}:{index}',part=part,
                                    planning_basis=basis,slice_start=candidate.start,slice_width=candidate.width,
                                    maximum_repeats=candidate.max_repeats))
            for row in audit['inner_rows']:
                unit=full[row['unit_index']]
                inner_rows.append(dict(row,part=part,unit_id=f'{part}:{row["unit_index"]}',
                    source_start=unit.start,source_end=unit.end,
                    source_segment_ids=json.dumps(unit.source_segment_ids),
                    source_event_ids=json.dumps(unit.source_event_ids)))
            compaction.extend(audit['compaction'])
            header=f'C128 t{timing["timer_b"]} q0 '
            lines.append(f'; Part {part}: musical units and finite phrase structure')
            lines.extend(wrap_track(part,header+text))
            plain_lines.extend(wrap_track(part,header+audit['plain']))
            uncompacted_lines.extend(wrap_track(part,header+audit['uncompacted']))
            before_limit_lines.extend(wrap_track(part,header+audit['selected_before_depth_limit']))
            report['tracks'][part]=dict(musical_units=len(units),loop=owner,
                source_structure_depth=repeat_tree_depth(plan.tree),target_repeat_depth=depth,
                folds=folds,structure_selection=audit['selected'],
                selected_inner_loops_before_depth_limit=sum(r['status']=='applied' and r['target_selected'] for r in audit['inner_rows']),
                emitted_repeat_commands=text.count('['),
                depth_expansions=audit['depth_expansions'],
                plain_muc_chars=len(audit['plain']),structured_muc_chars=len(text))
            for index,u in enumerate(full):
                rows.append((f'{part}:{index}',part,u.kind,u.start,u.end,clock[u.start],clock[u.end],
                             json.dumps(u.source_segment_ids),json.dumps(u.source_event_ids),
                             json.dumps(u.slices),repr(u.source_key)))
        with (passes/(stem+'.mucom.units.csv')).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(('unit_id','part','kind','source_start','source_end',
                    'target_start_clock','target_end_clock','source_segment_ids','source_event_ids','slices','source_key'))
            writer.writerows(rows)
        for suffix,stage_lines in (('plain',plain_lines),('uncompacted',uncompacted_lines),('before_depth_limit',before_limit_lines)):
            (passes/(stem+f'.mucom.{suffix}.muc')).write_bytes(('\r\n'.join(stage_lines)+'\r\n').encode('cp932'))
        for suffix,records,fields in (
            ('inner_loops',inner_rows,('part','unit_id','status')),
            ('control_units',control_rows,('unit_id','part','slice_index','source_start','source_end','target_start_clock','target_end_clock','source_key','command','structural_representative','source_segment_ids','source_event_ids')),
            ('inner_repeat_candidates',candidate_rows,('unit_id','part','planning_basis','slice_start','slice_width','maximum_repeats')),
            ('compaction',compaction,('track','token_index','action','before','after','reason','bytes_saved'))):
            with (passes/(stem+f'.mucom.{suffix}.csv')).open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(records[0]) if records else fields)
                writer.writeheader();writer.writerows(records)
        report['structure']=dict(notation='structured',
            selected_outer_loops_before_depth_limit=sum(row['status']=='applied' for track in report['tracks'].values() for row in track['folds']),
            selected_inner_loops_before_depth_limit=sum(row['status']=='applied' and row['target_selected'] for row in inner_rows),
            emitted_repeat_commands=sum(track['emitted_repeat_commands'] for track in report['tracks'].values()),
            compaction_decisions=len(compaction),
            plain_muc_chars=sum(track['plain_muc_chars'] for track in report['tracks'].values()),
            structured_muc_chars=sum(track['structured_muc_chars'] for track in report['tracks'].values()),
            maximum_repeat_depth=max((track['target_repeat_depth'] for track in report['tracks'].values()),default=0))
        muc_path=output/(stem+'.muc')
        muc_path.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932',errors='strict'))
        if voice_path:voice_path.write_bytes(bytes(32)+b''.join(voices))
        if pcm_path:pcm_path.write_bytes(bank)
        report.update(status='converted',muc_path=str(muc_path),voice_path=str(voice_path) if voice_path else None,
                      pcm_path=str(pcm_path) if pcm_path else None,manifest_path=str(manifest),
                      musical_note_units=sum(len(v) for v in gates.values()),
                      source_musical_note_units=sum(len(v) for v in original_gates.values()),
                      muc_sha256=hashlib.sha256(muc_path.read_bytes()).hexdigest())
        finish_stage();save();return report
    except (ValueError,KeyError,OSError) as error:
        finish_stage()
        report['diagnostics'].append(str(error))
        report.update(status='rejected');save()
        raise ConversionError(str(error)) from error
