"""Observed SSG register loop replay, separate from target notation.

Compare first and repeated traversals on an already accepted timing map. Shared
noise/envelope register evidence is retained even when inactive on this channel.
This proves effective positive-interval register states, not envelope phase.
"""
from psg import get_psg_mode


def semantic_state(segment):
    mode=segment.mode
    if segment.scale=='r':return ('silent',segment.prescale)
    envelope=bool(segment.envelope_enabled)
    return ('sounding',segment.prescale,mode,
        segment.tone_period if mode&1 else None,
        segment.noise_period if mode&2 else None,
        envelope,segment.envelope_period if envelope else None,
        segment.envelope_shape if envelope else None,
        None if envelope else segment.amplitude_register&15)


def _register_state(registers,channel,prescale):
    mode=get_psg_mode(channel,registers[7]);amplitude=registers[8+channel]&31
    if not mode or not amplitude:return ('silent',prescale)
    envelope=bool(amplitude&16)
    return ('sounding',prescale,mode,
        registers[channel*2]|((registers[channel*2+1]&15)<<8) if mode&1 else None,
        registers[6]&31 if mode&2 else None,envelope,
        registers[11]|(registers[12]<<8) if envelope else None,
        registers[13]&15 if envelope else None,None if envelope else amplitude&15)


def prove_loop_states(head,tail,channel,writes,clock,loop_start,end):
    """Replay observed loop writes from both seeds without inventing resets."""
    if head.prescale!=tail.prescale:return False,[],[],'SSG prescale differs across loop'
    first=list(head.registers);repeated=list(tail.registers)
    groups={clock[loop_start]:[],clock[end]:[]}
    for tick,register,data,event_id in writes:
        if loop_start<=tick<=end:
            if tick not in clock:return False,[],[],'SSG loop write lacks a recorded target clock'
            groups.setdefault(clock[tick],[]).append((register,data,event_id))
    boundaries=sorted(groups);checks=[];head_ids=[]
    for index,tick in enumerate(boundaries):
        for register,data,event_id in groups[tick]:
            first[register]=repeated[register]=data
            if tick==clock[loop_start]:head_ids.append(event_id)
        if index+1==len(boundaries):continue
        next_tick=boundaries[index+1]
        source=_register_state(first,channel,head.prescale)
        replay=_register_state(repeated,channel,tail.prescale)
        row=dict(target_start_clock=tick,target_end_clock=next_tick,
            source_state=source,repeated_state=replay,equal=source==replay,
            source_event_ids=tuple(event_id for _,_,event_id in groups[tick]))
        checks.append(row)
        if source!=replay:
            return False,checks,head_ids,'SSG inherited loop state differs during positive target time; observed head restoration is delayed or incomplete'
    # Deterministic assignments must also return the repeated seed. This catches
    # an incorrectly chosen EOF snapshot, including zero-time terminal writes.
    if _register_state(repeated,channel,tail.prescale)!=_register_state(tail.registers,channel,tail.prescale):
        return False,checks,head_ids,'SSG repeated EOF state is not stationary'
    return True,checks,head_ids,'Observed loop writes reproduce all positive effective register intervals'
