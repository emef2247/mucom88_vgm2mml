"""Nominal ADPCM-B playback positions derived from immutable source Segments.

Observed decoder epochs remain distinct from control slices. Position estimates
use the retained rational nibble rates, not a target clock or MML duration.
This is not a cycle-accurate Delta-T emulator or a target PCM bank format.
"""
import csv
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path


@dataclass(frozen=True)
class PositionSpan:
    segment_id: int
    playback_id: int
    start_vgmticks: int
    end_vgmticks: int
    position_start: Fraction
    position_end: Fraction
    rate: Fraction
    source_event_ids: tuple
    original_source_event_ids: tuple


@dataclass(frozen=True)
class PlaybackEpoch:
    playback_id: int
    sample_id: int
    start_vgmticks: int
    end_vgmticks: int
    segments: tuple
    positions: tuple
    sample_nibbles: int
    nominal_end: Fraction | None


def build_epochs(analysis, *, resolved_codes=()):
    """Certify live pan/level/rate trajectories without making slices restartable.

    The caller may independently certify ordinary-clock extra-header metadata.
    Address, memory mode, decoder reset, repeat and memory mutation are never
    resolved by treating the broad continuation diagnostic as harmless.
    """
    allowed={'controls_during_decoder_epoch', *resolved_codes}
    blockers={issue.code for issue in analysis.issues if issue.code not in allowed
              and not issue.code.endswith('_seeded_from_vgm_rom_block_assumption')}
    if blockers:
        raise ValueError('Unresolved PCM source controls: '+', '.join(sorted(blockers)))
    groups={}
    for segment in analysis.segments:
        groups.setdefault(segment.playback_id,[]).append(segment)
    samples={sample.sample_id:sample for sample in analysis.samples}
    epochs=[]
    for identity,group in groups.items():
        sample_id=group[0].sample_id
        if sample_id not in samples or any(s.sample_id!=sample_id for s in group):
            raise ValueError('PCM decoder epoch requires one known encoded sample')
        sample=samples[sample_id]
        if sample.codec!='yamaha_adpcm_b_high_nibble_first':
            raise ValueError('PCM decoder epoch requires native ADPCM-B bytes')
        for index,segment in enumerate(group):
            remaining=set(segment.issues)-allowed
            if remaining or segment.repeat:
                raise ValueError('PCM decoder epoch has unsupported repeat or source controls: '+', '.join(sorted(remaining)))
            if len(segment.registers)!=17 or segment.rate_den<=0 or segment.rate_num<0:
                raise ValueError('PCM decoder epoch lacks a valid native control state')
            if segment.end_vgmticks<segment.start_vgmticks:
                raise ValueError('PCM decoder epoch has reversed source time')
            if index:
                previous=group[index-1]
                if segment.start_vgmticks!=previous.end_vgmticks:
                    raise ValueError('PCM decoder continuation has a source gap or overlap')
                if segment.continuation_index!=previous.continuation_index+1:
                    raise ValueError('PCM decoder continuation order is incomplete')
                for register,(before,after) in enumerate(zip(previous.registers,segment.registers)):
                    if before==after:continue
                    if register not in (1,9,10,11) or (register==1 and (before^after)&63):
                        raise ValueError(f'PCM decoder continuation changes unsupported register 0x{register:02x}')
        position=Fraction(0);end=None;spans=[];size=len(sample.encoded_bytes)*2
        for segment in group:
            rate=Fraction(segment.rate_num,segment.rate_den)
            start=position
            advance=Fraction(segment.end_vgmticks-segment.start_vgmticks,44100)*rate
            if end is None and rate>0 and position+advance>=size:
                end=Fraction(segment.start_vgmticks)+(size-position)*44100/rate
            position=min(Fraction(size),position+advance)
            spans.append(PositionSpan(segment.segment_id,identity,segment.start_vgmticks,
                segment.end_vgmticks,start,position,rate,segment.source_event_ids,
                segment.original_source_event_ids))
        epochs.append(PlaybackEpoch(identity,sample_id,group[0].start_vgmticks,
            group[-1].end_vgmticks,tuple(group),tuple(spans),size,end))
    return tuple(epochs)


def describe(epochs):
    def ratio(value):
        return None if value is None else dict(numerator=value.numerator,denominator=value.denominator)
    return [dict(playback_id=e.playback_id,sample_id=e.sample_id,
        source_start=e.start_vgmticks,source_end=e.end_vgmticks,
        source_segment_ids=[s.segment_id for s in e.segments],
        sample_nibbles=e.sample_nibbles,nominal_end=ratio(e.nominal_end),
        position_model='nominal rational nibble consumption; decoder fetch/interpolation not simulated',
        termination=e.segments[-1].termination,
        spans=[dict(segment_id=s.segment_id,source_start=s.start_vgmticks,
            source_end=s.end_vgmticks,position_start=ratio(s.position_start),
            position_end=ratio(s.position_end),rate=ratio(s.rate),
            source_event_ids=list(s.source_event_ids),
            original_source_event_ids=list(s.original_source_event_ids)) for s in e.positions]) for e in epochs]


def dump_description(description, outdir, stem):
    root=Path(outdir);root.mkdir(parents=True,exist_ok=True)
    (root/(stem+'.opna_adpcm.playback.json')).write_text(json.dumps(description,indent=2)+'\n',encoding='utf-8')
    fields=('playback_id','sample_id','segment_id','source_start','source_end',
            'position_start_num','position_start_den','position_end_num','position_end_den',
            'rate_num','rate_den','source_event_ids','original_source_event_ids')
    with (root/(stem+'.opna_adpcm.playback.csv')).open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for epoch in description:
            for span in epoch['spans']:
                row={k:span[k] for k in ('segment_id','source_start','source_end')}
                row.update(playback_id=epoch['playback_id'],sample_id=epoch['sample_id'])
                for key in ('position_start','position_end','rate'):
                    row[key+'_num']=span[key]['numerator'];row[key+'_den']=span[key]['denominator']
                for key in ('source_event_ids','original_source_event_ids'):row[key]=json.dumps(span[key])
                writer.writerow(row)
