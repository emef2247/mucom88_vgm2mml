"""MUCOM blueprint score projection; original synthesis and dynamics stay native."""
from collections import defaultdict
from dataclasses import asdict
import json
import textwrap

from musical_hal.source_loop_plan import SourceLoopPlan
from musical_hal.score_normalization import score_tempo, normalize, timing_for_devices
from blueprint_midi import render as render_midi
from mml_format import wrap_track

FM = ('A','B','C','H','I','J')
SSG = ('D','E','F')
NOTES = ('c','c+','d','d+','e','f','f+','g','g+','a','a+','b')
DRUMS = {'BD':35,'SD':38,'HH':42,'CYM':49,'TOM':45,'TC':45,'RIM':37}


def _duration(note, steps):
    if steps <= 0 or steps % 3:
        raise ValueError('Blueprint duration must be a positive multiple of one 64th note')
    words=[]
    values=sorted({(192//n, str(n)) for n in (2,4,8,16,32,64)} |
                  {(192//n*3//2,str(n)+'.') for n in (2,4,8,16,32) if (192//n)%2==0},reverse=True)
    while steps:
        length,spelling=next((length,spelling) for length,spelling in values if length<=steps)
        words.append(note+spelling);steps-=length
    return (' ' if note=='r' else ' & ').join(words)


def _voice_bank():
    bank=bytearray(8192);record=bytearray(32)
    for op in range(4):
        record[1+op]=1;record[5+op]=0 if op==3 else 127
        record[9+op]=31;record[21+op]=15
    record[25]=7
    bank[32:64]=record
    return bytes(bank)


def render(performance, guide, stem, *, tempo=225, legacy_loops=False, enhance_macros=True):
    """Return owned MUC/MIDI/bank bytes and inspectable source-to-score spans."""
    tempo=score_tempo(tempo)
    timer=round(256-7987200*60/(2304*tempo*32))
    if not 0<=timer<=255:
        raise ValueError('Blueprint MUC tempo cannot be represented by Timer-B at C128: '+str(tempo))
    devices={d.device_id:d for d in performance.devices}
    score_timing=normalize(performance,tempo=tempo).report
    clocks={identity:timing_for_devices(score_timing,{identity}) for identity in devices}
    parts=defaultdict(list)
    decisions=[]
    for unit in performance.units:
        device=devices[unit.device_id]
        if (device.chip in ('msm6258','msm6295','segapcm','c140') or
                'pcm' in unit.section.lower() or unit.role in ('pcm','sample')):
            continue
        clock=clocks[unit.device_id]
        start=clock.position(unit.start_sample);stop=start+int(clock.count(unit.end_sample-unit.start_sample))*3
        if stop<=start:
            decisions.append(dict(unit_id=unit.unit_id,action='omit_sub_64th_gate',
                source_start_sample=unit.start_sample,source_end_sample=unit.end_sample))
            continue
        # Project the complete gate once. Internal controls cannot extend it or
        # create extra simultaneous voices when several fall on the same step.
        points={}
        source_points=guide.points(unit)
        for sample,pitch in source_points:
            at=start+int(clock.count(max(0,sample-unit.start_sample)))*3
            if at<stop:points[at]=(sample,pitch)
        accepted=[]
        for at,(sample,pitch) in sorted(points.items()):
            if accepted and accepted[-1][2]==pitch:continue
            accepted.append((at,sample,pitch))
        if len(accepted)<len(source_points):
            decisions.append(dict(unit_id=unit.unit_id,action='collapse_internal_pitch_controls',
                before=len(source_points),after=len(accepted)))
        first=True
        for index,(at,sample,pitch) in enumerate(accepted):
            end=accepted[index+1][0] if index+1<len(accepted) else stop
            source_end=accepted[index+1][1] if index+1<len(accepted) else unit.end_sample
            if pitch is None:
                decisions.append(dict(unit_id=unit.unit_id,action='omit_unknown_pitch',
                    source_start_sample=sample,source_end_sample=source_end))
                first=True
                continue
            original=pitch
            while pitch<24:pitch+=12
            while pitch>107:pitch-=12
            row=dict(unit_id=unit.unit_id,
                source_device=unit.device_id,source_chip=device.chip,source_instance=device.instance,
                source_section=unit.section,source_channel=unit.channel,
                source_start_sample=sample,source_end_sample=source_end,
                step_start=at,step_end=end,midi_note=pitch,source_midi_note=original,
                attack=first,percussion=False,
                approximation='octave_fold' if original!=pitch else '')
            parts[unit.device_id,unit.section,unit.channel].append(row)
            first=False
    for hit in performance.percussion:
        device=devices[hit.device_id]
        clock=clocks[hit.device_id]
        start=clock.position(hit.start_sample)
        stop=max(start+3,clock.position(hit.stop_sample)) if hit.stop_sample is not None else start+3
        family=hit.instrument_family.upper()
        parts[hit.device_id,'percussion:'+family,-1].append(dict(unit_id=hit.event_id,
            source_device=hit.device_id,source_chip=device.chip,source_instance=device.instance,
            source_section='percussion:'+family,source_channel=-1,
            source_start_sample=hit.start_sample,source_end_sample=hit.stop_sample,
            step_start=start,step_end=stop,midi_note=DRUMS.get(family,37),attack=True,percussion=True,
            approximation='MUC fixed-pitch rhythm guide; MIDI percussion',muc_midi_note=60))
    # Color overlaps instead of silently discarding a source voice.
    lanes=[]
    for identity,rows in sorted(parts.items()):
        colored=[]
        for row in sorted(rows,key=lambda r:(r['step_start'],r['step_end'],r['unit_id'])):
            lane=next((lane for lane in colored if lane[-1]['step_end']<=row['step_start']),None)
            if lane is None:lane=[];colored.append(lane)
            if (lane and not row['attack'] and lane[-1]['unit_id']==row['unit_id'] and
                    lane[-1]['step_end']==row['step_start'] and lane[-1]['midi_note']==row['midi_note']):
                lane[-1]['step_end']=row['step_end']
                lane[-1]['source_end_sample']=row['source_end_sample']
            else:lane.append(dict(row))
        lanes.extend((identity,index,rows) for index,rows in enumerate(colored))
    if not lanes and devices and all(d.chip in ('msm6258','msm6295','segapcm','c140') for d in devices.values()):
        return [],dict(status='not_applicable',reason='PCM-only source has no pitched score',excluded_pcm=len(performance.pcm_packets),decisions=decisions)
    if not lanes:
        decisions.append(dict(action='empty_score_document',
            reason='No known pitched score remains after musical interpretation; no notes invented'))
    pages=[]
    for identity,lane,rows in lanes:
        square=devices[identity[0]].chip=='psg' or identity[1]=='ssg'
        choice=None
        for page in pages:
            available=[part for part in ((*SSG,*FM) if square else (*FM,*SSG)) if part not in page]
            if available:choice=page,available[0];break
        if choice is None:
            page={};pages.append(page);choice=page,(SSG[0] if square else FM[0])
        choice[0][choice[1]]=(identity,lane,rows)
    if not pages:
        source_id=next((identity for identity,d in devices.items()
                        if d.chip not in ('msm6258','msm6295','segapcm','c140')), 'source:score')
        pages=[{'D':((source_id,'unresolved score',0),0,[])}]
    artifacts=[];reports=[]
    for number,page in enumerate(pages):
        name=stem if not number else f'{stem}.part{number+1:02d}'
        bank_name=name+'.dat'
        lines=[f'#title {name} blueprint','#driver mucomDotNET',
            '; Score only: original volume/envelopes/timbres omitted; fixed audition defaults.',
            f'; Reference tempo {tempo}; target Timer-B {timer}; original integer samples retained in sidecars.']
        if not lanes:
            lines.append('; No known pitched score could be notated; this rest-only draft is an editing placeholder.')
            lines.append('; Inspect blueprint decisions in musical_layer.json; source note durations were not stretched.')
        if any(part in FM for part in page):lines.append('#voice '+bank_name)
        for voice in performance.voices:
            for line in textwrap.wrap(json.dumps(asdict(voice),ensure_ascii=True),width=100):
                lines.append('; Source voice: '+line)
        end=max(3,max((clock.position(performance.end_sample) for clock in clocks.values()),default=0),max((r['step_end'] for _,_,rows in page.values() for r in rows),default=0))
        notes=[];structures=[];bodies={};setups={}
        for part,(identity,lane,rows) in page.items():
            lines.append(f'; Part {part}: source {identity[0]} {identity[1]} CH{identity[2]+1}, lane {lane+1}')
            commands=[];keys=[];cursor=0;previous=None
            for row in rows:
                if row['step_start']>cursor:
                    gap=row['step_start']-cursor;commands.append(_duration('r',gap));keys.append(('rest',gap))
                length=row['step_end']-row['step_start']
                pitch=row.get('muc_midi_note',row['midi_note'])
                hold=not row['attack'] and previous is not None and previous['unit_id']==row['unit_id'] and cursor==row['step_start']
                commands.append(('& ' if hold else '')+f'o{pitch//12-1} '+_duration(NOTES[pitch%12],length))
                keys.append((pitch,length,'hold' if hold else 'attack'))
                notes.append(dict(row,track=part));cursor=row['step_end'];previous=row
            if cursor<end:commands.append(_duration('r',end-cursor));keys.append(('rest',end-cursor))
            plan=SourceLoopPlan.build(keys,strategy='retained' if legacy_loops else 'structural')
            body,loops=plan.render(commands)
            structures.extend(dict(row,part=part) for row in loops)
            setups[part]='@1 v12' if part in FM else '@0 v15 P1 E255,255,255,255,0,255'
            bodies[part]=[body]
        from mucomsx_macros import compact_tracks
        definitions,compacted,macro_report=compact_tracks(bodies,enhance_macros=enhance_macros)
        lines.extend(definitions)
        for part,chunks in compacted.items():
            lines.extend(wrap_track(part,f'C128 t{timer} q0 {setups[part]} '+' '.join(chunks)))
        text='\r\n'.join(lines)+'\r\n'
        page_report=dict(tempo=tempo,end_step=end,score_notes=notes,file_index=number,
            timer_b=timer,effective_tempo=7987200*60/(2304*(256-timer)*32),loops=structures,macros=macro_report,source_sha256=performance.source_sha256,
            target_clock='C128; note values from canonical192-step score',
            fixed_voice='sine audition bank; source voice parameters are documentary only')
        midi,midi_report=render_midi(page_report,name)
        artifacts.extend([dict(relative='blueprint/'+name+'.muc',data=text.encode('cp932',errors='replace'),kind='blueprint'),
                          dict(relative='blueprint/'+name+'.midi',data=midi,kind='blueprint_midi')])
        if any(part in FM for part in page):
            artifacts.append(dict(relative='blueprint/'+bank_name,data=_voice_bank(),kind='blueprint_voice'))
        reports.append(dict(page_report,midi=midi_report))
    return artifacts,dict(file_count=len(pages),sounding_parts=len(lanes),
        score_notes=[note for report in reports for note in report['score_notes']],
        per_file_reports=reports,source_sha256=performance.source_sha256,
        source_native_unchanged=True,source_volume_and_timbres_executable=False,
        score_document='notated' if lanes else 'rest_only_placeholder',
        decisions=decisions,score_timing=score_timing)
