"""Native AY register intervals for direct MUCOM SSG projection, without OPM."""
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import csv
import json
from pathlib import Path

from chip_segments import PsgSegment
from psg import get_psg_mode
from vgm_io import read_vgm_header


@dataclass(frozen=True, kw_only=True)
class PsgSsgSegment(PsgSegment):
    source_event_id: int | None
    address: int | None
    register: int | None
    data: int | None
    registers: tuple
    envelope_trigger: bool
    # Fixed AY input-clock divisor, not an OPNA prescaler observation.
    prescale: int = 1
    source_kind: str = 'psg'
    original_event_id: int | None = None
    loop_iteration: int = 0
    original_vgmticks: int | None = None


@dataclass(frozen=True)
class PsgAnalysis:
    segments: dict
    source_end_vgmticks: int
    clock_hz: int
    effective_clock_hz: Fraction
    chip_type: int
    flags: int
    controls: tuple
    eligible: bool
    source_kind: str = 'psg'

    def dump(self, outdir, stem):
        out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
        with (out/(stem+'.psg_ssg.source.segments.csv')).open('w',encoding='utf-8',newline='') as f:
            columns=tuple(PsgSsgSegment.__dataclass_fields__)
            writer=csv.DictWriter(f,fieldnames=columns,lineterminator='\n');writer.writeheader()
            for channel in self.segments.values():
                for segment in channel:
                    row=asdict(segment)
                    row['registers']=json.dumps(row['registers']);row['pass3_row']=json.dumps(row['pass3_row'])
                    writer.writerow(row)
        (out/(stem+'.psg_ssg.source.json')).write_text(json.dumps(dict(
            source_chip='AY8910/YM2149',clock_hz=self.clock_hz,
            effective_clock_num=self.effective_clock_hz.numerator,
            effective_clock_den=self.effective_clock_hz.denominator,
            ay_type=self.chip_type,ay_flags=self.flags,source_end_vgmticks=self.source_end_vgmticks,
            register_reset_assumed=True,amplitude_reset_assumed=0,
            eligible_for_ssg_projection=self.eligible,controls=self.controls,
            target='MUCOM SSG directly; no OPM or fabricated OPNA source'),indent=2)+'\n',encoding='utf-8')


def build_source(raw, events, end, *, force=False, omissions=None):
    header=read_vgm_header(raw);clock_raw=header['ay_clock_raw'];clock=clock_raw&0x3fffffff
    if clock<=0:raise ValueError('PSG input requires a positive AY clock')
    chip_type,flags=header['ay_type'],header['ay_flags']
    reasons=[]
    if clock_raw&0xc0000000:reasons.append('unsupported_psg_clock_flags_or_dual_chip')
    if chip_type not in (0,1,2,0x10):reasons.append('unsupported_psg_chip_type')
    if flags&0x60:reasons.append('reserved_psg_flags')
    if any(e.command==0xa0 and raw[e.address+1]&0x80 for e in events):
        reasons.append('unsupported_second_psg_instance')
    if reasons and not force:raise ValueError('; '.join(reasons))
    if reasons and omissions is not None:
        omissions.append(dict(part='SSG',source_chip='PSG',action='omit_ssg_part',
            reason=';'.join(reasons),source_start_vgmticks=0,source_end_vgmticks=end))
    effective=Fraction(clock,2 if chip_type==0x10 and flags&0x10 else 1)
    registers=[0]*16;result={ch:[] for ch in range(3)};controls=[]
    def snapshot(ch,tick,event=None,register=None,data=None):
        amplitude=registers[8+ch]&31;mode=get_psg_mode(ch,registers[7])
        return PsgSsgSegment(ev_type='initial' if event is None else 'psg_write',
            time=tick/44100,ch=ch,ticks=tick*60//44100,l=0,
            tone_period=registers[ch*2]|((registers[ch*2+1]&15)<<8),volume=amplitude&15,
            octave=0,scale='' if mode and amplitude else 'r',volume_delta=0,pass3_row=(),
            vgmticks=tick,vgmticks_end=tick,mode=mode,noise_period=registers[6]&31,
            envelope_enabled=(amplitude>>4)&1,envelope_period=registers[11]|registers[12]<<8,
            envelope_shape=registers[13]&15,mixer=registers[7],amplitude_register=amplitude,
            source_event_id=event.source_event_id if event is not None else None,
            address=event.address if event is not None else None,register=register,data=data,
            registers=tuple(registers),envelope_trigger=register==13,
            original_event_id=getattr(event,'original_event_id',event.source_event_id) if event is not None else None,
            loop_iteration=event.loop_iteration if event is not None else 0,
            original_vgmticks=event.original_vgmticks if event is not None else None)
    current={ch:snapshot(ch,0) for ch in range(3)}
    for event in events:
        if event.command!=0xa0:continue
        register,data=raw[event.address+1:event.address+3]
        controls.append(dict(source_event_id=event.source_event_id,vgmticks=event.vgmticks,
            address=event.address,chip_instance=register>>7,register=register&127,data=data,
            original_event_id=getattr(event,'original_event_id',event.source_event_id),
            loop_iteration=event.loop_iteration,original_vgmticks=event.original_vgmticks))
        if register&0x80:continue
        if register>15:
            if not force:raise ValueError(f'Unsupported AY register 0x{register:02X}')
            if omissions is not None:omissions.append(dict(part='SSG',source_chip='PSG',port=0,
                register=register,action='omit_register_write',reason='unsupported_ay_register',
                source_event_ids=[event.source_event_id],source_start_vgmticks=event.vgmticks,
                source_end_vgmticks=event.vgmticks))
            continue
        registers[register]=data
        channels=(register//2,) if register<6 else ((register-8,) if 8<=register<=10 else range(3))
        for ch in channels:
            prior=current[ch];result[ch].append(replace(prior,vgmticks_end=event.vgmticks,
                l=event.vgmticks*60//44100-prior.ticks))
            current[ch]=replace(snapshot(ch,event.vgmticks,event,register,data),
                                volume_delta=(registers[8+ch]&15)-prior.volume)
    for ch in range(3):
        prior=current[ch];result[ch].append(replace(prior,vgmticks_end=end,l=end*60//44100-prior.ticks))
    return PsgAnalysis({ch:tuple(v) for ch,v in result.items()},end,clock,effective,
                       chip_type,flags,tuple(controls),not reasons)


def musical_notes(analysis):
    """Reuse MSX note/volume-run interpretation on an explicit source-sample view.

    Only this temporary view changes units/event labels. Native Segments remain
    intact; no MGSDRV envelope bank, duration grid or notation is imported.
    Zero-duration pitch/shape rows retain pending boundaries in extract_notes.
    """
    from mml_envelopes import extract_notes

    def event_type(segment):
        register = segment.register
        if register is not None and register < 6:
            return 'fCA' if register % 2 == 0 else 'fCB'
        if register == 13:
            return 'evS'
        return segment.ev_type

    view = {ch: tuple(replace(s, ticks=s.vgmticks, l=s.vgmticks_end-s.vgmticks,
                             ev_type=event_type(s)) for s in rows)
            for ch, rows in analysis.segments.items()}
    return extract_notes(view, 'psg')


def dump_musical_notes(analysis, notes, outdir, stem):
    """Expose sounding units and exact volume runs before target projection."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    fields = ('channel', 'note_index', 'source_start', 'source_end', 'rest',
              'tone_period', 'mode', 'hardware_envelope', 'source_segment_ids',
              'source_event_ids', 'volume_runs')
    with (out/(stem+'.psg_ssg.music.csv')).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for ch, rows in notes.items():
            for index, note in enumerate(rows):
                members = [analysis.segments[ch][i] for i in note.segment_indices]
                writer.writerow(dict(channel=ch, note_index=index,
                    source_start=note.start, source_end=note.start+note.length, rest=note.rest,
                    tone_period=note.segment.tone_period, mode=note.segment.mode,
                    hardware_envelope=note.segment.envelope_enabled,
                    source_segment_ids=json.dumps([f'psg:{ch}:{i}' for i in note.segment_indices]),
                    source_event_ids=json.dumps(list(dict.fromkeys(s.source_event_id for s in members
                        if s.source_event_id is not None))), volume_runs=json.dumps(note.runs)))
