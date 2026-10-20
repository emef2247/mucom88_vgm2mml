"""MSX source musical units and structures lowered to the MUCOMSX dialect."""
from fractions import Fraction
import csv
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from mucomsx_source import load_source
from pre_envelope_loops import prepare
from opll_inner_loops import OpllLoopPlan
from opll_note_units import NoteUnit
from mml_envelopes import candidate_curves, settings
from source_loop_plan import SourceLoopPlan, expanded_tokens
from mucom_structure import limit_repeat_depth
from mucomsx_macros import compact_tracks
from mucomsx_psg_render import compact_psg_commands
from ordered_timing import allocate_tick_domains
from mucom_aux import SSG_PERIOD
from mml_format import wrap_track

CLOCK = 8_000_000
SSG_CLOCK = CLOCK // 4
PARTS = {'psg': ('MSXD', 'MSXE', 'MSXF'),
         'scc': (), 'opll': ('MSXA', 'MSXB', 'MSXC', 'MSXH', 'MSXI', 'MSXJ')}
NOTES = ('c','c+','d','d+','e','f','f+','g','g+','a','a+','b')
FLAT_ENVELOPE = 'E255,0,0,0,0,255'



class ConversionError(ValueError):
    pass


def dump_rows(path, rows, fields=()):
    fields = tuple(fields or (rows[0] if rows else ('status',)))
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)


def timed(note, ticks, continuation=False):
    if ticks <= 0:
        return ''
    output = []
    while ticks:
        chunk = min(ticks, 111)
        if note != 'r' and (continuation or output):
            output.append('&')
        output.append(f'{note}%{chunk}')
        ticks -= chunk
    return ' '.join(output)


def tone(period):
    candidates = [(o, n, p >> (o-1)) for o in range(1,9)
                  for n,p in enumerate(SSG_PERIOD) if (o,n) != (1,0)]
    o,n,base = min(candidates, key=lambda row: abs(row[2]-period))
    detune = (base-period) << (o-1)
    if not 1 <= period <= 4095 or not -32768 <= detune <= 32767:
        raise ConversionError('Projected SSG/SCC pitch is outside MUCOMSX range')
    return f'D{detune} o{o}', NOTES[n], dict(projected_opna_period=period, detune=detune, octave=o)


def choose_timing(notes, end, loop, bound_ms):
    if not math.isfinite(bound_ms) or not 0 < bound_ms <= 100:
        raise ConversionError('MUCOMSX timing bound must be in (0,100] milliseconds')
    times = {0,end}; protected = ([dict(start_vgmticks=0,end_vgmticks=end)] if end>0 else [])
    if loop is not None:
        times.add(loop); protected.append(dict(start_vgmticks=loop,end_vgmticks=end))
    for rows in notes.values():
        for note in rows:
            start,stop = note.start,note.start+note.length
            times.update((start,stop))
            if not note.rest and stop>start:
                protected.append(dict(start_vgmticks=start,end_vgmticks=stop))
            cursor=start
            for _,length in note.runs:
                times.add(cursor);cursor+=length;times.add(cursor)
            for row in getattr(note,'sample_slices',()):
                times.update((row.tick_start,row.tick_end))
    times=sorted(times);requested_bound=Fraction(str(bound_ms))*44100/1000
    # Native software-volume output can follow its command by one driver tick.
    # Reserve half of the requested budget for this observed target latency.
    bound=requested_bound/2
    selected=None
    # Slowest feasible interrupt, preserving complete sounding gates. Short
    # control intervals may share a tick but ordered command evidence remains.
    for timer in range(256):
        period=Fraction((256-timer)*2304*44100,CLOCK)
        if period>requested_bound/2:continue
        lower=[max(0,math.ceil((t-bound)/period)) for t in times]
        upper=[math.floor((t+bound)/period) for t in times]
        nearest=[round(Fraction(t)/period) for t in times]
        lower[0]=upper[0]=nearest[0]=0
        ticks=allocate_tick_domains(times,lower,upper,nearest,protected)
        if ticks is None:
            continue
        selected=(timer,period,ticks);break
    if selected is None:
        raise ConversionError('MUCOMSX Timer-B cannot preserve gates within timing bound')
    timer,period,ticks=selected
    return ticks,dict(clock_hz=CLOCK,timer_b=timer,whole_note_clocks=128,
        timer_unit='2304 master clocks; verified against authored native export',
        samples_per_tick=float(period),max_error_samples=float(max(abs(ticks[t]*period-t) for t in times)),
        boundary_bound_ms=bound_ms,quantization_budget_ms=bound_ms/2,
        software_volume_latency_budget_ms=float(period*1000/44100),source_boundaries=[dict(source_sample=t,target_tick=ticks[t],
        error_samples=float(ticks[t]*period-t)) for t in times])


def clipped(note, start, stop):
    result=SimpleNamespace(**vars(note))
    result.start=start;result.length=stop-start
    if note.rest:
        result.runs=[]
    else:
        cursor=note.start;result.runs=[]
        for volume,length in note.runs:
            a,b=max(start,cursor),min(stop,cursor+length)
            if a<b:result.runs.append((volume,b-a))
            cursor+=length
    pairs=[(row,item) for row,item in zip(getattr(note,'sample_slices',()),
        getattr(note,'sample_items',())) if row.tick_start<stop and row.tick_end>start]
    result.sample_slices=tuple(row for row,_ in pairs)
    result.sample_items=tuple(item for _,item in pairs)
    result.continuation=start>note.start and not note.rest
    return result


def note_state(chip,note,at_end=False):
    if note is None or note.rest:return ('rest',)
    s=note.segment
    if chip=='opll':
        rows=note.sample_slices
        row=rows[-1] if at_end else rows[0]
        item=note.sample_items[-1] if at_end else note.sample_items[0]
        return (row.fnum,row.block,row.inst,row.vol,row.sus,item.patch)
    return settings(s,chip)+(note.runs[-1][0] if at_end else note.runs[0][0],)


def convert_vgm(input_path,outdir,*,diagnostics_outdir=None,song_loop=True,loops=True,
                macros=True,force=False,normalization_ms=8.0,normalize_lengths=True,
                timing_bound_ms=8.0,dump_passes=True,opll_rhythm='native',psg_enhance_cents=None):
    out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
    passes=Path(diagnostics_outdir or out);passes.mkdir(parents=True,exist_ok=True)
    stem=Path(input_path).stem;report_path=passes/(stem+'.mucomsx.json')
    report=dict(status='analyzing',target='MUCOMSX v1.0-stable',source=str(input_path),
        tracks={},assessments=[],omissions=[],source_evidence_unchanged=True)
    if opll_rhythm not in ('native','layered'):raise ConversionError('Unknown OPLL rhythm routing')
    stage='source_analysis'
    def assessment(chip,ch,note,action,reason):
        row=dict(chip=chip,channel=ch,source_start=note.start,
            source_end=note.start+note.length,segment_indices=json.dumps(note.segment_indices),
            action=action,reason=reason)
        report['assessments'].append(row)
        if action.startswith('omit'):report['omissions'].append(row)
    try:
        from vgm_io import read_vgm_bytes
        from vgm_timing import command_times
        raw=read_vgm_bytes(input_path)
        is_dcsg=any(e.command in (0x50,0x30) for e in command_times(raw))
        if is_dcsg:
            from dcsg_music import load_dcsg_bundle
            bundle=load_dcsg_bundle(input_path,passes,stem,song_loop=song_loop)
        else:bundle=load_source(input_path,passes,stem,song_loop=song_loop)
        report['source']=dict(paths=bundle.paths,loop=bundle.metadata,
            first_end_sample=bundle.first_end_sample,end_sample=bundle.end_sample)
        # Source analysis and envelope candidates precede every target limit.
        curves=[]
        for chip in ('psg','scc'):
            notes=bundle.notes.get(chip,{})
            for curve,count in candidate_curves(notes).items():
                structural=candidate_curves(bundle.analyses.get(chip,{}).get('representatives',{})).get(curve,0)
                curves.append(dict(chip=chip,source_samples=sum(n for _,n in curve),
                    occurrences=count,structural_occurrences=structural,volume_runs=json.dumps(curve),
                    target_lowering='tied volume controls; no MGSDRV table limits'))
            for ch,plan in bundle.analyses.get(chip,{}).get('source_plans',{}).items():
                plan.dump(passes/f'{stem}.{chip}{ch}.source_structure.csv',
                    [n.segment_indices for n in notes[ch]])
        dump_rows(passes/(stem+'.msx.envelope_candidates.csv'),curves,
            ('chip','source_samples','occurrences','structural_occurrences','volume_runs','target_lowering'))
        stage='target_eligibility'
        tracks={};origins={};end=bundle.end_sample;loop=bundle.loop_sample
        enhance_requested=psg_enhance_cents is not None and bool(bundle.notes.get('psg'))
        fm_parts=set();fm_available=iter(('A','B','C','H','I','J'));routing=[]
        report['scc_projection_policy']='all_opna_fm_additive4'
        for chip,channels in bundle.notes.items():
            for ch,rows in sorted(channels.items()):
                if chip=='opll' and ch>=9:continue
                if not any(not n.rest for n in rows):continue
                if chip=='scc' or ch>=len(PARTS[chip]):
                    if chip=='opll':
                        for note in rows:
                            if not note.rest:assessment(chip,ch,note,'omit_part','MUCOMSX native OPLL six-melody interface; OPNA substitution disabled')
                        if not force:
                            raise ConversionError(f'OPLL source ch{ch+1} exceeds the MUCOMSX native six-melody interface; no automatic OPNA substitution (--force permits explicit omission)')
                        continue
                    if chip=='scc':
                        part=next(fm_available,None)
                        if part is None:
                            for note in rows:
                                if not note.rest:assessment(chip,ch,note,'omit_part','OPNA FM channel capacity')
                            if not force:raise ConversionError('OPNA FM channel capacity exceeded; --force permits diagnosed omission')
                            continue
                        fm_parts.add(part)
                    elif chip=='psg' and is_dcsg and ch<6:part=('D','E','F')[ch-3]
                    else:
                        raise ConversionError('Native PSG channel capacity exceeded')
                else:part=PARTS[chip][ch]
                tracks[part]=list(rows);origins[part]=(chip,ch)
                routing.append(dict(source_chip=chip,source_channel=ch,target_part=part,
                    target_chip='opna_fm' if part in fm_parts else 'opna_ssg' if is_dcsg and chip=='psg' and ch>=3 else chip))
        if psg_enhance_cents is not None:
            if is_dcsg:raise ConversionError('Dual DCSG already uses both PSG and SSG engines; extra PSG enhancement is unavailable')
            if not math.isfinite(psg_enhance_cents) or not -100<=psg_enhance_cents<=100:
                raise ConversionError('PSG enhancement detune must be in -100..100 cents')
            for native,shadow in zip(('MSXD','MSXE','MSXF'),('D','E','F')):
                if native not in tracks:continue
                copies=[]
                for note in tracks[native]:
                    view=SimpleNamespace(**vars(note));state=SimpleNamespace(**vars(note.segment))
                    clock=bundle.header['ay_clock_raw']&0x3fffffff
                    if bundle.header.get('ay_type')==0x10 and bundle.header.get('ay_flags',0)&0x10:clock/=2
                    state.target_period=max(1,min(4095,round(state.tone_period*SSG_CLOCK/clock*2**(-psg_enhance_cents/1200))))
                    state.target_noise_period=max(0,min(31,round(state.noise_period*SSG_CLOCK/clock)))
                    view.segment=state;copies.append(view)
                tracks[shadow]=copies;origins[shadow]=origins[native]
                routing.append(dict(source_chip='psg',source_channel=origins[native][1],target_part=shadow,
                    target_chip='opna_ssg',enhancement_cents=psg_enhance_cents))
            report['psg_enhancement']=dict(cents=psg_enhance_cents,policy='explicit detuned SSG layer; quantized periods; shared-noise limitations retained')
        report['routing']=routing
        if is_dcsg:report['dcsg_projection']=bundle.metadata['dcsg_projection_assessments']
        rhythm_segments={ch:rows for ch,rows in bundle.segments.get('opll',{}).items() if ch>=9}
        rhythm_attacks=sorted({r.vgmticks for rows in rhythm_segments.values() for r in rows
                               if r.keyon and r.is_ryt and r.ev_type=='rhythm_expand'})
        if rhythm_attacks:
            # These source Segments are attack-only evidence, so release and
            # subsequent percussion pitch/volume are explicitly approximate.
            times=sorted({0,end,*rhythm_attacks})
            rows=[]
            for a,b in zip(times,times[1:]):
                refs=[(ch,i) for ch,states in rhythm_segments.items() for i,r in enumerate(states)
                      if r.vgmticks==a and r.keyon and r.is_ryt and r.ev_type=='rhythm_expand']
                rows.append(SimpleNamespace(start=a,length=b-a,rest=not refs,runs=[],
                    segment_indices=refs,segment=SimpleNamespace(),rhythm_refs=refs))
            tracks['MSXG']=rows;origins['MSXG']=('rhythm',0)
            if opll_rhythm=='layered':tracks['G']=list(rows);origins['G']=('rhythm',0)
        if not tracks:raise ConversionError('No supported sounding MSX source parts')
        if loop is not None:
            incompatible=[]
            for part,rows in tracks.items():
                head=next((n for n in rows if n.start<loop<n.start+n.length),None)
                if head is not None and not head.rest and origins[part][0]!='rhythm':
                    tail=next((n for n in reversed(rows) if n.start<end<=n.start+n.length),None)
                    if note_state(origins[part][0],clipped(head,loop,min(end,head.start+head.length)))!=note_state(origins[part][0],tail,True):incompatible.append(part)
            if incompatible:
                if not force:raise ConversionError('Held song-loop state is not repeatable: '+','.join(incompatible))
                report['omissions'].append(dict(chip='song',channel='',source_start=loop,source_end=end,
                    segment_indices='[]',action='omit_song_loop',reason='incompatible carried target state: '+','.join(incompatible)))
                end=bundle.first_end_sample;loop=None
        stage='musical_projection'
        # A new target view clips/splits units; native Segments and interpreted
        # source notes remain unchanged in their dedicated dumps.
        projected={}
        for part,rows in tracks.items():
            pieces=[];cursor=0
            for note in rows:
                if note.start>=end:continue
                a,b=note.start,min(end,note.start+note.length)
                if b<=a:continue
                if a>cursor:
                    rest=SimpleNamespace(start=cursor,length=a-cursor,rest=True,runs=[],segment_indices=[],segment=note.segment)
                    cuts=[cursor]+([loop] if loop is not None and cursor<loop<a else [])+[a]
                    pieces.extend(clipped(rest,x,y) for x,y in zip(cuts,cuts[1:]))
                view=note
                if (normalize_lengths and origins[part][0]!='rhythm' and not note.rest
                        and note.start+note.length<bundle.end_sample
                        and note.length <= math.floor(normalization_ms*44.1)):
                    view=SimpleNamespace(**vars(note));view.rest=True;view.runs=[]
                    assessment(origins[part][0],origins[part][1],note,'omit_short_gate',
                        'whole original-time sounding gate within normalization threshold')
                cuts=[a]+([loop] if loop is not None and a<loop<b else [])+[b]
                pieces.extend(clipped(view,x,y) for x,y in zip(cuts,cuts[1:]))
                cursor=b
            if cursor<end:
                rest=SimpleNamespace(start=cursor,length=end-cursor,rest=True,runs=[],segment_indices=[],segment=rows[-1].segment)
                cuts=[cursor]+([loop] if loop is not None and cursor<loop<end else [])+[end]
                pieces.extend(clipped(rest,x,y) for x,y in zip(cuts,cuts[1:]))
            projected[part]=pieces
        stage='target_timing'
        ticks,timing=choose_timing(projected,end,loop,normalization_ms if normalize_lengths else timing_bound_ms)
        report['timing']=timing
        rhythm_by_tick={}
        if rhythm_attacks:
            from mucomsx_opll import project_rhythm_segments
            rhythm_units,rhythm_report=project_rhythm_segments(rhythm_segments,ticks,ticks[end],source_end_sample=end)
            rhythm_by_tick={u.start:u for u in rhythm_units}
            report['rhythm_projection']=rhythm_report
            (passes/(stem+'.mucomsx.rhythm.json')).write_text(json.dumps(rhythm_report,indent=2)+'\n',encoding='utf-8')
        voice_ids={};voices={};scc_fm_voices={};pitch_rows=[];units_rows=[];structure_reports={};rendered={};plain={};state_reports={}
        def voice_id(record):
            if record not in voice_ids:
                if len(voice_ids)>=32:raise ConversionError('MUCOMSX used-FM-voice capacity 32 exceeded')
                voice_ids[record]=len(voice_ids);voices[voice_ids[record]]=record
            return voice_ids[record]
        for part,rows in projected.items():
            chip,ch=origins[part];commands=[];keys=[];children=[];unit_offset=len(units_rows)
            for index,note in enumerate(rows):
                child_commands=None;child_keys=None
                if note.rest:
                    command=timed('r',ticks[note.start+note.length]-ticks[note.start]);key=('rest',note.length)
                elif chip=='rhythm':
                    duration=ticks[note.start+note.length]-ticks[note.start]
                    if note.continuation:
                        # A loop marker inside a one-shot interval is not a new
                        # source attack. Keep the selected rhythm mask tied.
                        command=timed('c',duration,continuation=True)
                        key=('rhythm_continuation',note.length)
                    else:
                        unit=rhythm_by_tick.get(ticks[note.start])
                        if unit is None:
                            raise ConversionError(f'No projected rhythm attack at source sample {note.start}')
                        command=' '.join(unit.commands)+' '+timed(unit.note,duration)
                        key=('rhythm',unit.commands,note.length)
                    assessment('opll',0,note,'approximate_opll_rhythm','Attack-only rhythm projection; releases, held pitch and levels are not exact')
                elif chip=='scc' and part in fm_parts:
                    from mucomsx_scc_opna import project_waveform,volume_controls,project_pitch
                    state=note.segment;wave=state.waveform_hex
                    if wave not in scc_fm_voices:scc_fm_voices[wave]=project_waveform(wave)
                    voice=scc_fm_voices[wave];vid=voice_id(voice.dat)
                    p=project_pitch(state.tone_period,bundle.header['scc_clock_raw']&0x3fffffff)
                    words=[f'@{vid} v15 '+' '.join(p.commands)];cursor=note.start;continuation=note.continuation
                    for volume,length in note.runs:
                        duration=ticks[cursor+length]-ticks[cursor]
                        if continuation and duration>0:words.append('&')
                        words.extend(volume_controls(voice,volume))
                        text=timed(p.note,duration)
                        if text:words.append(text);continuation=True
                        cursor+=length
                    command=' '.join(words);key=(settings(state,'scc'),note.length,tuple(note.runs),note.continuation)
                    assessment(chip,ch,note,'approximate_scc_opna','SCC source voice mapped to one OPNA additive FM channel; four harmonics, phase/DC and envelope losses reported')
                    pitch_rows.append(dict(part=part,source_start=note.start,source_period=state.tone_period,
                        source_clock_hz=bundle.header['scc_clock_raw']&0x3fffffff,
                        projected_opna_period=p.target_fnum,detune=p.detune,octave=p.target_block))
                elif chip=='psg':
                    s=note.segment
                    source_clock=(bundle.metadata['dcsg_source_config']['clock_hz'] if is_dcsg else
                        bundle.header['ay_clock_raw']&0x3fffffff)
                    if source_clock<=0:raise ConversionError('Sounding source requires declared clock')
                    if chip=='psg' and bundle.header.get('ay_type')==0x10 and bundle.header.get('ay_flags',0)&0x10:source_clock/=2
                    period=getattr(s,'target_period',round(s.tone_period*SSG_CLOCK/source_clock))
                    pitch,nname,pinfo=tone(period);prefix=[]
                    if not note.continuation:
                        prefix.append(FLAT_ENVELOPE)
                        prefix.append('P'+str(s.mode))
                        if s.mode&2:prefix.append('w'+str(getattr(s,'target_noise_period',max(0,min(31,round(s.noise_period*SSG_CLOCK/source_clock))))))
                        if s.envelope_enabled:
                            shape=(s.envelope_shape-8)&15
                            prefix.extend(('m'+str(round(s.envelope_period*SSG_CLOCK/source_clock)),'S'+str(shape)))
                            assessment(chip,ch,note,'approximate_hw_envelope','MUC note hardware-envelope trigger semantics; shared source evidence retained')
                    prefix.append(pitch)
                    words=(['&'] if note.continuation else [])+[' '.join(prefix)];cursor=note.start;continuation=False
                    for volume,length in note.runs:
                        a,b=cursor,cursor+length;duration=ticks[b]-ticks[a]
                        if continuation and duration>0:words.append('&')
                        if not s.envelope_enabled:words.append('v'+str(volume))
                        text=timed(nname,duration)
                        if text:words.append(text);continuation=True
                        cursor=b
                    command=' '.join(words);key=(settings(s,chip),note.length,tuple(note.runs),note.continuation)
                    pitch_rows.append(dict(part=part,source_start=note.start,source_period=s.tone_period,
                        source_clock_hz=source_clock,**pinfo))
                else:
                    from mucomsx_opll import preset_patch,patch_voice,patch_projection_report,project_pitch
                    words=[];continuation=note.continuation;signature=[];child_keys=[];child_commands=[]
                    for state,item in zip(note.sample_slices,note.sample_items):
                        a,b=max(note.start,state.tick_start),min(note.start+note.length,state.tick_end)
                        if b<=a:continue
                        patch=preset_patch(state.inst) if state.inst else bytes.fromhex(item.patch)
                        if item.patch_changes:
                            assessment(chip,ch,note,'approximate_patch_trajectory','Native MSX-MUSIC ROM-preset projection cannot reproduce held arbitrary patch writes')
                        record=patch_voice(patch);vid=voice_id(record)
                        fnum,block=state.fnum,state.block
                        volume=15-state.vol
                        if not fnum:
                            fallback=next((r for r in note.sample_slices if r.fnum),None)
                            fnum,block=(fallback.fnum,fallback.block) if fallback is not None else (256,3)
                            volume=0
                            assessment(chip,ch,note,'approximate_zero_frequency','Keyed FNUM=0 phase/DC state retained in source; timed silent target gate')
                        p=project_pitch(fnum,block,bundle.header['opll_clock_raw']&0x3fffffff,target_clock_hz=CLOCK)
                        prefix=f'@{vid} v{volume} '+ ' '.join(p.commands)
                        duration=ticks[b]-ticks[a]
                        if continuation and duration>0:prefix='& '+prefix
                        duration_text=timed(p.note,duration)
                        child_commands.append(prefix+' '+duration_text)
                        if duration_text:continuation=True
                        child_keys.append((item.signature(),b-a))
                        signature.append((b-a,state.fnum,state.block,state.inst,state.vol,state.sus,patch.hex()))
                        pitch_rows.append(dict(part=part,source_start=a,source_period=state.fnum,
                            source_clock_hz=bundle.header['opll_clock_raw']&0x3fffffff,
                            projected_opna_period=p.target_fnum,detune=p.detune,octave=p.target_block))
                    command=' '.join(child_commands)
                    key=('opll',tuple(signature),note.continuation)
                    assessment(chip,ch,note,'approximate_opll_voice','MUCOMSX OPNA-DAT to OPLL ROM-preset selection; sustain/custom-wave/AM/VIB/EG fidelity not guaranteed')
                commands.append(command);keys.append(key)
                children.append((child_commands or [command],child_keys or [key]))
                units_rows.append(dict(part=part,note_index=index,source_start=note.start,source_end=note.start+note.length,
                    rest=note.rest,continuation=note.continuation,source_segment_indices=json.dumps(note.segment_indices),
                    volume_runs=json.dumps(note.runs),command=command))
            # Preserve the same MSX source-first structural planner; L divides
            # the plans, never a repeated source unit or macro definition.
            split=next((i for i,n in enumerate(rows) if loop is not None and n.start==loop),len(rows))
            if chip=='psg':
                commands,state_report=compact_psg_commands(commands,
                    hardware=[not n.rest and n.segment.envelope_enabled for n in rows],
                    loop_index=split if loop is not None else None)
                state_reports[part]=state_report
                for record,command in zip(units_rows[unit_offset:],commands):
                    record['rendered_command']=command
            else:
                for record in units_rows[unit_offset:]:
                    record['rendered_command']=record['command']
            chunks=[];plain_chunks=[];details=[]
            for label,a,b in [('intro',0,split),('loop',split,len(rows))]:
                if a==b:continue
                if chip in ('psg','scc'):
                    plan=prepare({0:rows[a:b]},chip,strategy='structural')[0][0]
                    text,folds=plan.render(commands[a:b]) if loops else (' '.join(commands[a:b]),[])
                elif chip=='opll':
                    members=[];items=[];body=[];units=[]
                    for local_index,source_index in enumerate(range(a,b)):
                        child_commands,child_keys=children[source_index]
                        indices=list(range(len(items),len(items)+len(child_keys)))
                        members.append(SimpleNamespace(segment_indices=indices))
                        items.extend(SimpleNamespace(signature=lambda key=key:key) for key in child_keys)
                        body.extend(child_commands)
                        units.append(NoteUnit(local_index,local_index+1,
                            'rest' if rows[source_index].rest else 'note',keys[source_index],rows[source_index].length))
                    plan=OpllLoopPlan.build(members,units,items,strategy='structural')
                    text,folds=plan.render(body,list(range(len(body)+1))) if loops else (' '.join(commands[a:b]),[])
                else:
                    plan=SourceLoopPlan.build(keys[a:b],strategy='structural')
                    text,folds=plan.render(commands[a:b]) if loops else (' '.join(commands[a:b]),[])
                text,depths=limit_repeat_depth(text)
                if expanded_tokens(text)!=expanded_tokens(' '.join(commands[a:b])):raise AssertionError('MSX structural projection changed commands')
                plan.dump(passes/f'{stem}.{part}.{label}.structure.csv',[n.segment_indices for n in rows[a:b]],folds)
                if label=='loop':chunks.append('L');plain_chunks.append('L')
                # Keep individual self-contained leaves available for macros when
                # finite repeat selection did not compact the whole region.
                chunks.extend([text] if '[' in text else commands[a:b]);plain_chunks.extend(commands[a:b])
                details.append(dict(region=label,source_units=b-a,folds=folds,depth_expansions=depths))
            rendered[part]=chunks;plain[part]=plain_chunks;structure_reports[part]=details
            report['tracks'][part]=dict(chip=chip,source_channel=ch,musical_units=len(rows),
                plain_characters=len(' '.join(plain_chunks)),structured_characters=len(' '.join(chunks)),
                emitted_repeats=' '.join(chunks).count('['))
        (passes/(stem+'.mucomsx.psg_render.json')).write_text(json.dumps(state_reports,indent=2)+'\n',encoding='utf-8')
        report['psg_state_spelling']={part:{key:value for key,value in detail.items() if key!='units'}
            for part,detail in state_reports.items()}
        report['scc_opna_voices']=[voice.report for voice in scc_fm_voices.values()]
        stage='macro_projection'
        definitions,compacted,macro_report=compact_tracks(rendered,limit=32 if macros else 0)
        report['macros']=macro_report
        header=[f'#title {stem}','#driver mucomDotNET']
        if voices:
            bank=bytearray(8192)
            for index,record in voices.items():bank[index*32:(index+1)*32]=record
            bank_path=out/(stem+'.dat');header.append('#voice '+bank_path.name)
            report['voice_bank']=str(bank_path)
        prefix='C128 t'+str(timing['timer_b'])+' q0 '
        lines=header+definitions
        for part,chunks in compacted.items():lines.extend(wrap_track(part,prefix+' '.join(chunks)))
        text='\r\n'.join(lines)+'\r\n';encoded=text.encode('cp932')
        # Retain rejected target candidates and the exact projection evidence
        # before enforcing the compiler's input limit.
        dump_rows(passes/(stem+'.mucomsx.units.csv'),units_rows)
        dump_rows(passes/(stem+'.mucomsx.pitch.csv'),pitch_rows)
        (passes/(stem+'.mucomsx.structure.json')).write_text(json.dumps(structure_reports,indent=2)+'\n',encoding='utf-8')
        (passes/(stem+'.mucomsx.macros.json')).write_text(json.dumps(macro_report,indent=2)+'\n',encoding='utf-8')
        if len(encoded)>65536:
            candidate=passes/(stem+'.mucomsx.rejected_muc.txt');candidate.write_bytes(encoded)
            report['rejected_candidate']=str(candidate)
            raise ConversionError(f'MUCOMSX MUC input capacity exceeded: {len(encoded)}/65536 bytes')
        if any(len(line.encode('cp932'))>1023 for line in lines):raise ConversionError('MUCOMSX physical line exceeds 1023 bytes')
        path=out/(stem+'.muc')
        if voices:bank_path.write_bytes(bank)
        path.write_bytes(encoded)
        dump_rows(passes/(stem+'.mucomsx.units.csv'),units_rows)
        dump_rows(passes/(stem+'.mucomsx.pitch.csv'),pitch_rows)
        dump_rows(passes/(stem+'.mucomsx.assessments.csv'),report['assessments'],('chip','channel','source_start','source_end','segment_indices','action','reason'))
        (passes/(stem+'.mucomsx.structure.json')).write_text(json.dumps(structure_reports,indent=2)+'\n',encoding='utf-8')
        (passes/(stem+'.mucomsx.macros.json')).write_text(json.dumps(macro_report,indent=2)+'\n',encoding='utf-8')
        report.update(status='generated',muc_path=str(path),muc_bytes=len(encoded),waveform_count=0,
            native_scc_target_channels=0,
            musical_note_units=sum(not n.rest for rows in projected.values() for n in rows),
            source_end_sample=bundle.end_sample,target_end_source_sample=end,target_loop_source_sample=loop,
            software_envelope='MSX note volume runs lowered to tied v updates; no arbitrary MGSDRV @e table claim',
            native_scc_channels_retained=len(bundle.segments.get('scc',{})),
            compiler_status='not_run',validation_scope='Source interpretation, structure expansion and MUC generation; no listening equivalence claim')
        return report
    except Exception as error:
        report.update(status='conversion_failed',failed_stage=stage,detail=str(error))
        raise ConversionError(str(error)) from error
    finally:
        report_path.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
