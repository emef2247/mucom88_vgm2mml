"""Native MUCOM88 FM projection of an immutable OPNA control state.

Musical gates and durations belong to the caller.  Named operator controls
retain parameters that native voice loading and its volume table overwrite.
"""
from dataclasses import dataclass

TARGET_CLOCK_HZ = 7_987_200
FNUM = (618, 655, 694, 735, 779, 825, 874, 926, 981, 1040, 1102, 1167)
NOTES = ('c', 'c+', 'd', 'd+', 'e', 'f', 'f+', 'g', 'g+', 'a', 'a+', 'b')
OPERATOR_NUMBERS = (1, 3, 2, 4)  # Source register-bank order.


@dataclass(frozen=True)
class FmProjection:
    commands: tuple[str, ...]
    note: str
    voice_id: int | None
    target_fnum: int
    target_block: int
    source_tl: tuple[int, ...]
    target_tl: tuple[int, ...]
    frequency_error_hz: float
    explicit_controls: tuple[str, ...]


def fm_voice(state):
    """Encode a native 32-byte DAT record, including operator AM flags."""
    if state.algorithm is None or state.feedback is None or len(state.operators) != 4:
        raise ValueError('Incomplete FM voice')
    result = bytearray(32)
    for index, op in enumerate(state.operators):
        values = (op.dt, op.mul, op.tl, op.ks, op.ar, op.d1r, op.d2r, op.sl, op.rr)
        if any(v is None for v in values):
            raise ValueError('Incomplete FM operator parameters')
        fields = (op.dt << 4 | op.mul, op.tl, op.ks << 6 | op.ar,
                  (op.am_enabled or 0) << 7 | op.d1r, op.d2r, op.sl << 4 | op.rr)
        for group, value in enumerate(fields):
            result[1 + group * 4 + index] = value
    result[25] = state.feedback << 3 | state.algorithm
    return bytes(result)


def _operator_controls(state):
    records = []
    for op in state.operators:
        records.append((op.dt << 4 | op.mul, op.tl, op.ks << 6 | op.ar,
                        (op.am_enabled or 0) << 7 | op.d1r, op.d2r,
                        op.sl << 4 | op.rr, op.ssg_eg_raw or 0))
    return records



def am_restore_commands(state):
    """Restore observed operator AM+D1R after native H's AM side effect."""
    if any(op.d1r is None for op in state.operators):
        raise ValueError('Cannot restore AM after H: incomplete FM D1R evidence')
    return tuple(f'yDR,{number},{(op.am_enabled or 0) << 7 | op.d1r}'
                 for number, op in zip(OPERATOR_NUMBERS, state.operators))

def project_state(state, source_clock_hz, voices, *, initial, previous_state=None,
                  target_channel=0):
    """Project controls without changing source state or emitting a duration.

    ``voices`` maps native DAT bytes to one-based bank IDs.  ``initial`` denotes
    a new gate; held changes must pass the previous sounding state.  Numeric
    register controls are limited to algorithm and shared LFO controls, whose
    native MML has no equivalent disable/algorithm-only command.
    """
    if not 0 <= target_channel < 6:
        raise ValueError('Native FM target channel must be 0..5')
    if not 1 <= state.key_mask <= 15:
        raise ValueError('FM sounding state requires a nonzero operator key mask')
    if state.channel3_special_enabled or state.csm_enabled:
        raise ValueError('FM CH3 special/CSM frequencies require separate projection')
    if state.fnum is None or state.block is None or source_clock_hz <= 0:
        raise ValueError('Incomplete FM pitch or invalid source clock')
    if not initial and previous_state is None:
        raise ValueError('Held FM projection requires previous_state')
    record = fm_voice(state)
    voice_id = None
    if initial:
        if record not in voices:
            if len(voices) >= 255:
                raise ValueError('Native FM voice bank exceeds 255 records')
            voices[record] = len(voices) + 1
        voice_id = voices[record]
    # FNUM is linear in chip clock and inverse in the FM prescale divisor.
    source_units = state.fnum * (2 ** state.block) * source_clock_hz * 6 / state.fm_prescale_divisor
    scaled = source_units / TARGET_CLOCK_HZ
    block = state.block
    fnum = round(scaled / (2 ** block))
    while fnum > 2047 and block < 7:
        block += 1
        fnum = round(scaled / (2 ** block))
    if fnum > 2047 or not 0 <= block <= 7:
        raise ValueError('Source FM frequency exceeds native YM2608 pitch range')
    # Native D clears beforeCode to0; tied o1c also encodes0 and skips
    # frequency initialization/update. Another note plus exact detune avoids it.
    note_index = min(range(1 if block==0 else 0,12), key=lambda i: abs(FNUM[i] - fnum))
    detune = fnum - FNUM[note_index]
    commands, explicit = [], []
    local = target_channel % 3
    if initial:
        commands.extend((f'@{voice_id}', 'v15'))
    pan = ((state.left_enabled or 0) << 1) | (state.right_enabled or 0)
    if not pan:
        raise ValueError('FM pan 0: installed native compiler rejects both p0 and register B4; mute-bit projection is not supported by this compiler profile')
    if initial or (state.left_enabled, state.right_enabled) != (previous_state.left_enabled, previous_state.right_enabled):
        commands.append(f'p{pan}')
    modulation = (state.lfo_enabled or 0, state.lfo_rate or 0, state.pms or 0, state.ams or 0)
    previous_modulation = None if initial else (previous_state.lfo_enabled or 0, previous_state.lfo_rate or 0,
                                               previous_state.pms or 0, previous_state.ams or 0)
    modulation_changed = modulation != previous_modulation
    if modulation_changed:
        enabled, rate, pms, ams = modulation
        if enabled:
            commands.append(f'H{rate},{pms},{ams}')
        else:
            # H always enables the global LFO.  Disable is a parameter control.
            commands.extend((f'H{rate},{pms},{ams}', 'y$22,0'))
            explicit.extend(commands[-2:])
    controls = _operator_controls(state)
    previous_controls = None if initial else _operator_controls(previous_state)
    for bank, fields in enumerate(controls):
        for index, (name, value) in enumerate(zip(('DM', 'TL', 'KA', 'DR', 'SR', 'SL', 'SE'), fields)):
            # A new voice already installed DM/KA/DR/SR/SL. Its v15 overwrote
            # carrier TL, and voice records cannot carry the SSG-EG register.
            needed = (name in ('TL', 'SE')) if initial else value != previous_controls[bank][index]
            # The bundled native H handler enables AM on operators. Restore
            # their observed AM flags after every H, including held changes.
            needed = needed or name == 'DR' and modulation_changed
            if needed:
                control = f'y{name},{OPERATOR_NUMBERS[bank]},{value}'
                commands.append(control)
                explicit.append(control)
    if not initial and (state.algorithm, state.feedback) != (previous_state.algorithm, previous_state.feedback):
        command = f'y${0xb0 + local:02X},{state.feedback << 3 | state.algorithm}'
        commands.append(command)
        explicit.append(command)
    if initial or state.key_mask != previous_state.key_mask:
        command = key_delay_command(state.key_mask)
        commands.append(command)
        explicit.append(command)
        if not initial:
            key_channel = local | (4 if target_channel >= 3 else 0)
            command = f'y$28,{state.key_mask << 4 | key_channel}'
            commands.append(command)
            explicit.append(command)
    if initial or (state.fnum, state.block, state.fm_prescale_divisor) != (previous_state.fnum, previous_state.block, previous_state.fm_prescale_divisor):
        commands.extend((f'o{block + 1}', f'D{detune}'))
    source_frequency = source_units / (144 * 2 ** 20)
    target_frequency = fnum * 2 ** block * TARGET_CLOCK_HZ / (144 * 2 ** 20)
    tl = tuple(op.tl for op in state.operators)
    return FmProjection(tuple(commands), NOTES[note_index], voice_id, fnum, block,
                        tl, tl, target_frequency - source_frequency, tuple(explicit))


def key_delay_command(key_mask):
    """Suppress inactive native note operators for each <=111-clock piece.

    KD uses key-bit order (operators 1, 2, 3, 4), unlike DAT bank order.
    Delay 128 exceeds every emitted piece; refresh before each tied note.
    """
    if not 1 <= key_mask <= 15:
        raise ValueError("FM key-delay mask must be 1..15")
    return "KD" + ",".join("0" if key_mask & (1 << bit) else "128"
                           for bit in range(4))


def resolve_gate_pitch(gate, clock):
    """Resolve only zero-target-time pitch-less prefixes from observed evidence.

    Return output-only states and audit records; source controls and keys remain
    intact. Positive unknown time, another gate, and unobserved reset values
    cannot supply an initial note pitch.
    """
    from dataclasses import replace
    states=tuple(c.state for c in gate.controls)
    unknown=[i for i,state in enumerate(states) if state.fnum is None or state.block is None]
    if not unknown:return states,[]
    if clock is None:
        raise ValueError(f'FM {gate.part}: initial pitch is unobserved; recovery requires an accepted normalization clock')
    donor=next((i for i,state in enumerate(states) if state.fnum is not None and state.block is not None),None)
    if donor is None or any(i>=donor for i in unknown):
        raise ValueError(f'FM {gate.part}: no observed complete pitch within the initial prefix of the same gate')
    tick=clock[gate.start]
    observed=gate.controls[donor]
    if clock[observed.start]!=tick or any(clock[gate.controls[i].start]!=tick or
                                         clock[gate.controls[i].end]!=tick for i in unknown):
        raise ValueError(f'FM {gate.part}: unobserved initial pitch occupies positive target time; sample {gate.start}')
    target=list(states);records=[]
    for i in unknown:
        control=gate.controls[i]
        target[i]=replace(states[i],fnum=observed.state.fnum,block=observed.state.block)
        records.append(dict(part=gate.part,unit_start=gate.start,source_start=control.start,
            source_end=control.end,target_clock=tick,source_event_ids=control.event_ids,
            source_segment_ids=control.segment_ids,donor_source_start=observed.start,
            donor_event_ids=observed.event_ids,donor_segment_ids=observed.segment_ids,
            observed_fnum=observed.state.fnum,observed_block=observed.state.block,
            reason='Initial pitch-less control has zero accepted target time; use observed same-gate same-clock pitch for note spelling'))
    return tuple(target),records
