"""Independent OPM source -> explicit OPNA FM target view for musical MUC.

Native OPM evidence is immutable. This is target projection, never a native
YM2608 source trace. Operator banks retain register order m1,m2,c1,c2; decoded
Key masks already have the same logical operator-bit order on both chips.
"""
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path

from opna import OpnaAnalysis, OpnaRegisterState, OpnaStateEvent, OpnaSegment, dump_analysis
from opm_pitch_projection import pitch_units

TARGET_CLOCK = 7987200
TARGET_ORIGIN = 'YM2608-target-from-OPM'


def nominal_frequency(kc, kf, clock):
    if clock <= 0:
        raise ValueError('OPM requires a positive source clock')
    # Existing Yamaha nominal convention: KC4A/KF0 at3579545Hz is A440.
    return 440 * clock / 3579545 * 2 ** ((pitch_units(kc, kf)/64 - 56)/12)


def opna_pitch(frequency):
    if not math.isfinite(frequency) or frequency <= 0:
        raise ValueError('OPNA pitch requires a positive finite frequency')
    unit = TARGET_CLOCK / (144 * 2**20)
    choices = []
    for block in range(8):
        fnum = max(1, min(2047, round(frequency / (unit * 2**block))))
        actual = fnum * unit * 2**block
        choices.append((abs(actual-frequency), block, fnum, actual))
    _, block, fnum, actual = min(choices)
    return fnum, block, actual


@dataclass(frozen=True)
class Projection:
    analysis: OpnaAnalysis
    writes: tuple
    report: dict

    def dump(self, outdir, stem):
        out = Path(outdir);out.mkdir(parents=True,exist_ok=True)
        prefix = stem+'.opm_opna.target'
        fields = tuple(dict.fromkeys(k for row in self.writes for k in row)) or ('target_event_id',)
        with (out/(prefix+'.writes.csv')).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
            writer.writeheader();writer.writerows(self.writes)
        dump_analysis(self.analysis,state_csv=out/(prefix+'.state.csv'),
                      segments_csv=out/(prefix+'.segments.csv'))
        (out/(stem+'.opm_opna.json')).write_text(json.dumps(self.report,indent=2)+'\n',encoding='utf-8')


def _target_analysis(writes, end):
    chip=OpnaRegisterState();current={};events=[];segments=[]
    for ch in range(6):
        current[ch]=OpnaStateEvent(None,None,None,None,None,ch//3,0,TARGET_ORIGIN,
            TARGET_CLOCK,TARGET_CLOCK,ch,0,'initial',False,0,0,0,chip.snapshot(ch))
    def close(row,stop):
        segments.append(OpnaSegment(**row.__dict__,segment_id=len(segments),vgmticks_end=stop))
    for row in writes:
        channels,kind,rising,falling=chip.write(row['target_register'],row['target_data'],row['target_port'])
        for ch in channels:
            previous=current[ch];state=chip.snapshot(ch)
            event=OpnaStateEvent(row['target_event_id'],row['source_address'],
                0x56+row['target_port'],row['target_register'],row['target_data'],
                row['target_port'],0,TARGET_ORIGIN,TARGET_CLOCK,TARGET_CLOCK,ch,
                row['source_vgmticks'],kind,state!=previous.state,rising,falling,
                chip.continuity[ch],state,original_event_id=row['original_event_id'],
                loop_iteration=row['loop_iteration'],original_vgmticks=row['original_vgmticks'])
            events.append(event)
            if event.changed:
                close(previous,event.vgmticks);current[ch]=event
    for ch in range(6):close(current[ch],end)
    return OpnaAnalysis(tuple(events),tuple(segments),end,False,(),())


def project(native, *, channel_policy='error', force=False, omissions=None):
    """Project basic4OP FM and diagnose every explicit unsupported effect.

    Fixed whole-song channel allocation; no stealing. Force neutralizes LFO,
    omits DT2, and mutes the unsupported noise operator. Real pan0 uses a
    diagnosed centered/all-operator-TL mute approximation, retaining Key state.
    """
    if channel_policy not in ('error','omit'):
        raise ValueError('OPM channel policy must be error or omit')
    omissions = [] if omissions is None else omissions
    start_omissions=len(omissions)
    active=sorted({e.ch for e in native.events if not e.chip_instance and e.state.key_mask})
    if len(active)>6 and channel_policy=='error':
        raise ValueError('OPM uses more than6 FM channels; select --opm-channel-policy omit to allow explicit omission')
    channel_map={ch:index for index,ch in enumerate(active[:6])}
    groups={}
    for event in native.events:groups.setdefault(event.source_event_id,[]).append(event)
    writes=[];cache={};sequences={};pitch_assumptions=[];ignored_timers=[];seen_unsupported=set()
    def omission(s,reason,action,*,bits=None,ch=None,**details):
        key=(s.source_event_id,reason,ch)
        if key in seen_unsupported:return
        seen_unsupported.add(key)
        omissions.append(dict(source_chip='OPM',part='FM' if ch is None else 'ABCDEFGH'[ch],
            register=s.register,bits=bits,action=action,reason=reason,
            source_event_ids=[s.source_event_id],source_start_vgmticks=s.vgmticks,
            source_end_vgmticks=s.vgmticks,source_channel=s.ch,chip_instance=s.chip_instance,**details))
    for ch in active[6:]:
        affected=[e for e in native.events if not e.chip_instance and e.ch==ch]
        omissions.append(dict(source_chip='OPM',part='ABCDEFGH'[ch],
            action='omit_fm_channel',reason='opm8_to_opna6_channel_capacity',
            source_channel=ch,source_event_ids=list(dict.fromkeys(e.source_event_id for e in affected)),
            source_start_vgmticks=min(e.vgmticks for e in affected),
            source_end_vgmticks=native.source_end_vgmticks,actual=len(active),limit=6,unit='FM channels'))
    def emit(s,port,reg,data,reason,**audit):
        # Never suppress Key requests; paired pitch commits must stay ordered.
        if reg!=0x28 and not 0xa0<=reg<=0xa6 and cache.get((port,reg))==data:return
        cache[port,reg]=data
        source_id=s.source_event_id if s else None
        sequence=sequences.get(source_id,0);sequences[source_id]=sequence+1
        writes.append(dict(target_event_id=len(writes),target_sequence=sequence,
            target_origin=TARGET_ORIGIN,source_event_id=s.source_event_id if s else None,
            original_event_id=s.original_event_id if s else None,
            original_vgmticks=s.original_vgmticks if s else 0,
            loop_iteration=s.loop_iteration if s else 0,source_address=s.address if s else None,
            source_vgmticks=s.vgmticks if s else 0,source_channel=s.ch if s else None,
            source_register=s.register if s else None,source_data=s.data if s else None,
            target_channel=channel_map.get(s.ch) if s else None,
            target_port=port,target_register=reg,target_data=data,reason=reason,**audit))
    # Initialization is replayable evidence: normalization rebuilds from events.
    emit(None,0,0x29,0x80,'target six-channel output enabled')
    emit(None,0,0x22,0,'target LFO neutral; no OPM LFO approximation')
    emit(None,0,0x27,0,'target ordinary FM; no CSM or special frequencies')
    for target in channel_map.values():
        port,local=divmod(target,3)
        for bank in range(4):emit(None,port,0x90+4*bank+local,0,'target has no source SSG-EG')
    def tl(s,bank):
        if force and s.state.left_enabled==s.state.right_enabled==0:return 127
        if force and s.ch==7 and s.state.noise_enabled and bank==3:return 127
        return s.state.operators[bank].tl
    def levels(s):
        port,local=divmod(channel_map[s.ch],3)
        for bank in range(4):
            value=tl(s,bank)
            if value is not None:emit(s,port,0x40+bank*4+local,value,'projected operator TL/output gate')
    def pan(s):
        left,right=s.state.left_enabled,s.state.right_enabled
        if left is None or right is None:return None
        if not left and not right:
            if not force:raise ValueError(f'OPM pan0 requires --force TL-mute approximation; source event{s.source_event_id}')
            omission(s,'native_muc_pan0_not_encodable','center_and_mute_operator_levels',ch=s.ch)
            return 0xc0
        return left<<7 | right<<6
    def pitch(s):
        kc,kf=s.state.kc_raw,s.state.kf_raw
        if kc is None or kf is None:
            pitch_assumptions.append(dict(source_event_id=s.source_event_id,source_channel=s.ch,
                assumed_reset_kc=kc is None,assumed_reset_kf=kf is None))
        frequency=nominal_frequency(kc or 0,kf or 0,s.clock_hz)
        fnum,block,actual=opna_pitch(frequency)
        unit=TARGET_CLOCK/(144*2**20)
        clamped=not unit<=frequency<=2047*unit*2**7
        if clamped:
            if not force:raise ValueError('OPM nominal pitch outside OPNA FNUM/BLOCK range; use --force to clamp')
            omission(s,'opm_pitch_outside_opna_range','clamp_pitch_to_opna_range',ch=s.ch,
                source_frequency_hz=frequency,target_frequency_hz=actual)
        port,local=divmod(channel_map[s.ch],3)
        audit=dict(source_frequency_hz=frequency,target_frequency_hz=actual,
                   error_cents=1200*math.log2(actual/frequency),pitch_clamped=clamped,source_clock_hz=s.clock_hz)
        # Shared high latch is overwritten before every low-byte commit.
        emit(s,port,0xa4+local,block<<3 | fnum>>8,'KC/KF to committed FNUM/BLOCK high',**audit)
        emit(s,port,0xa0+local,fnum&255,'KC/KF to committed FNUM/BLOCK low',**audit)
    for eid,group in sorted(groups.items()):
        s=group[0];reg,data=s.register,s.data
        if s.chip_instance or s.chip_type!='YM2151':
            if not force:raise ValueError('OPM projection supports one YM2151 instance')
            omission(s,'unsupported_opm_instance_or_variant','omit_opm_write');continue
        if s.clock_hz<=0:raise ValueError('OPM requires a positive source clock')
        if reg==0x14 and data&0x80:
            if not force:raise ValueError('OPM CSM timer-generated Keys are unsupported; use --force')
            omission(s,'opm_csm_not_projected','omit_effect',bits=0x80)
        if reg in (0x10,0x11,0x12,0x14):
            ignored_timers.append(s.source_event_id)
        for member in group:
            if member.ch not in channel_map:continue
            state=member.state
            lfo=(not state.lfo_reset and ((state.pms and state.pmd) or
                 (state.ams and state.amd and any(op.am_enabled for op in state.operators))))
            if lfo:
                if not force:raise ValueError('Active OPM hardware LFO is unsupported; use --force')
                omission(member,'opm_hardware_lfo_not_projected','neutralize_lfo',ch=member.ch)
        if reg<0x20 and reg not in (8,15):
            if reg not in (1,0x10,0x11,0x12,0x14,0x18,0x19,0x1b) or reg==1 and data&~2:
                if not force:raise ValueError(f'Unsupported OPM shared register0x{reg:02x}')
                omission(s,'unsupported_opm_shared_register','omit_write')
            continue
        if s.ch not in channel_map:continue
        target=channel_map[s.ch];port,local=divmod(target,3)
        if reg==15:
            if data&0x80:
                if not force:raise ValueError('OPM noise waveform is unsupported; use --force')
                omission(s,'opm_noise_not_projected','mute_noise_operator',bits=0x80,ch=s.ch)
            levels(s)
        elif reg==8:
            if data&0x78:
                pitch(s)
                if pan(s) is None:raise ValueError('OPM Key-On has unobserved pan state')
            emit(s,0,0x28,((data>>3)&15)<<4 | (local | (4 if target>=3 else 0)),
                 'logical operator Key mask; no bank-order swap')
        elif 0x20<=reg<=0x27:
            emit(s,port,0xb0+local,data&63,'algorithm and feedback')
            value=pan(s)
            if value is not None:emit(s,port,0xb4+local,value,'left/right pan; LFO sensitivity neutral')
            levels(s)
        elif 0x28<=reg<=0x37:pitch(s)
        elif 0x38<=reg<=0x3f:
            value=pan(s)
            if value is not None:emit(s,port,0xb4+local,value,'LFO sensitivity neutral')
        elif reg>=0x40:
            bank=(reg>>3)&3;group_base=reg&0xe0
            target_base={0x40:0x30,0x60:0x40,0x80:0x50,0xa0:0x60,0xc0:0x70,0xe0:0x80}[group_base]
            if group_base==0xc0 and data&0xc0:
                if not force:raise ValueError('Nonzero OPM DT2 is unsupported; use --force')
                omission(s,'opm_dt2_not_projected','omit_dt2_bits',bits=0xc0,ch=s.ch)
            value=(data&31) if group_base==0xc0 else tl(s,bank) if group_base==0x60 else data
            emit(s,port,target_base+bank*4+local,value,'operator register-bank projection')
    analysis=_target_analysis(writes,native.source_end_vgmticks)
    report=dict(source_origin='native OPM State/Segments',target_origin=TARGET_ORIGIN,
        source_ir_unchanged=True,independent_projection=True,vgm_conv_dependency=False,
        target_clock_hz=TARGET_CLOCK,source_clock_hz=next((e.clock_hz for e in native.events),None),
        channel_policy=channel_policy,channel_map=channel_map,
        active_source_channels=active,omitted_source_channels=active[6:],
        target_write_count=len(writes),target_only_pitch_reset_assumptions=pitch_assumptions,
        source_timer_writes_not_scheduled=ignored_timers,
        omissions=omissions[start_omissions:],
        scope='nominal basic4OP FM; DT1/EG physical rates and acoustic identity not certified')
    return Projection(analysis,tuple(writes),report)
