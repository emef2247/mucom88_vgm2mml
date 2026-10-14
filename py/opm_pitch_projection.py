"""Nominal YM2151 base-pitch correction at the Segment -> MDX boundary.

KC has holes/aliases; KF supplies 64 steps per semitone. The clock scales
frequency, so a clock ratio is an additive logarithmic pitch offset. This is
an independent formula implementation, not an emulator ROM/table copy. See
https://github.com/vampirefrog/mdxtools/blob/master/ym2151.c for the hardware
KC/KF indexing and clock scaling. DT1, envelope, LFO and noise are not retimed.
"""
from dataclasses import replace
import math

from opm import OpmRegisterState

TARGET_CLOCK_HZ = 4000000
KC_CODES = (0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14)
MAX_PITCH_UNITS = 96 * 64 + 63  # KC 0x7f aliases the next octave's first note.


def pitch_units(kc, kf):
    code = kc & 127
    return (code - (code >> 2)) * 64 + (kf >> 2)


def corrected_pitch(kc, kf, source_clock_hz, *, policy='error'):
    """Return target KC/KF and cents error, including explicit range loss."""
    if policy not in ('error', 'clamp'):
        raise ValueError('OPM pitch policy must be clamp or error')
    if source_clock_hz <= 0:
        raise ValueError('OPM source clock must be positive')
    offset = 768 * math.log2(source_clock_hz / TARGET_CLOCK_HZ)
    units = pitch_units(kc, kf) + math.floor(offset + 0.5)
    if not 0 <= units <= MAX_PITCH_UNITS and policy == 'error':
        raise ValueError('OPM pitch is outside the 4 MHz target KC/KF range')
    units = min(MAX_PITCH_UNITS, max(0, units))
    note, fraction = divmod(units, 64)
    target_kc = (note // 12) * 16 + KC_CODES[note % 12] if note < 96 else 127
    return target_kc, fraction << 2, (units - pitch_units(kc, kf) - offset) * 100 / 64


def project_controls(events, source_clock_hz, *, policy='clamp'):
    """Expand ordered source controls into target controls and audit rows.

    Input rows are (event_id, (sample, register, data), segment_ids). Only
    alternate-clock sources use explicit target-only reset-zero assumptions.
    Under error policy, setup before a channel's first Key-On is deferred; once
    played, error policy rejects even released tails. Clamp policy records loss.
    Each target row retains the source control and an intra-event sequence.
    """
    if policy not in ('error', 'clamp') or source_clock_hz <= 0:
        raise ValueError('OPM pitch policy must be clamp/error and source clock must be positive')
    result, evidence = [], []
    source_pitch = [[0, 0] for _ in range(8)]
    known = [[False, False] for _ in range(8)]
    target_pitch = [[None, None] for _ in range(8)]
    played = [False] * 8
    for event_id, (samples, register, data), ids in events:
        controls = []
        is_pitch = 0x28 <= register <= 0x37
        is_on = register == 8 and bool(data & 120)
        ch = register & 7 if is_pitch else data & 7 if is_on else None
        if is_pitch:
            part = int(register >= 0x30)
            source_pitch[ch][part] = data
            known[ch][part] = True
        if is_pitch or is_on:
            kc, kf = source_pitch[ch]
            row = dict(source_event_id=event_id, source_segment_ids=list(ids),
                       source_vgmticks=samples, ch=ch, source_register=register,
                       source_data=data, source_clock_hz=source_clock_hz,
                       target_clock_hz=TARGET_CLOCK_HZ, source_kc=kc if known[ch][0] else None,
                       source_kf=kf if known[ch][1] else None,
                       assumed_reset_kc=not known[ch][0], assumed_reset_kf=not known[ch][1])
            try:
                a, b, error = corrected_pitch(kc, kf, source_clock_hz, policy=policy)
            except ValueError as failure:
                if played[ch] or is_on:
                    raise ValueError(f'{failure}; event {event_id}, ch{ch}, source clock {source_clock_hz} Hz') from failure
                row.update(status='deferred_before_first_keyon', target_kc=None,
                           target_kf=None, nominal_error_cents=None)
            else:
                for part, value in enumerate((a, b)):
                    if target_pitch[ch][part] != value:
                        controls.append((0x28 + part * 8 + ch, value))
                        target_pitch[ch][part] = value
                requested = pitch_units(kc, kf) + math.floor(768 * math.log2(source_clock_hz / TARGET_CLOCK_HZ) + 0.5)
                clamped = not 0 <= requested <= MAX_PITCH_UNITS
                row.update(status='clamped' if clamped else 'projected', target_kc=a, target_kf=b,
                           requested_target_pitch_units=requested,
                           nominal_error_cents=error)
            evidence.append(row)
        if not is_pitch:
            controls.append((register, data))
        if is_on:
            played[ch] = True
        for sequence, (reg, value) in enumerate(controls):
            result.append((event_id, samples, tuple(ids), reg, value,
                           register, data, sequence))
    return result, tuple(evidence)


def target_segments(projection, segments):
    """Pitch-state view after each complete target event; source IR untouched.

    Replay the actual projected pitch controls rather than guessing from source
    state independently. This also represents deferred setup and paired writes.
    Non-pitch known/unknown fields remain exactly the source fields.
    """
    segments = tuple(segments)
    if projection.source_clock_hz == TARGET_CLOCK_HZ:
        return segments
    groups = {}
    for write in projection.writes:
        groups.setdefault(write.source_event_id, []).append(write)
    # A deferred source pitch event may have no target write. Carry forward the
    # last emitted target state in source-event order, including shared fanout.
    result = {}
    current = OpmRegisterState()
    by_event = {}
    for segment in segments:
        by_event.setdefault(segment.source_event_id, []).append(segment)
    for event_id in sorted(by_event.keys() | groups.keys(), key=lambda value: -1 if value is None else value):
        for write in sorted(groups.get(event_id, ()), key=lambda w: w.target_sequence):
            current.write(write.register, write.data)
        for segment in by_event.get(event_id, ()):
            target = current.snapshot(segment.ch)
            native = segment.state
            registers = {r: v for r, v in native.channel_registers
                         if r not in (0x28 + segment.ch, 0x30 + segment.ch)}
            for register, value in ((0x28 + segment.ch, target.kc_raw),
                                    (0x30 + segment.ch, target.kf_raw)):
                if value is not None:
                    registers[register] = value
            state = replace(native, kc_raw=target.kc_raw, kf_raw=target.kf_raw,
                            octave=target.octave, note=target.note, kf=target.kf,
                            channel_registers=tuple(sorted(registers.items())))
            result[id(segment)] = replace(segment, state=state)
    return tuple(result[id(segment)] for segment in segments)


def pitch_report(projection):
    rows = projection.pitch_evidence
    errors = [abs(r['nominal_error_cents']) for r in rows if r['status'] == 'projected']
    losses = [r for r in rows if r['status'] == 'clamped']
    return dict(source_clock_hz=projection.source_clock_hz, target_clock_hz=projection.clock_hz,
                applied=projection.source_clock_hz != projection.clock_hz,
                method='nominal base pitch; log2 clock ratio; nearest 1/64 semitone',
                max_abs_nominal_error_cents=max(errors, default=0),
                policy=projection.pitch_policy, clamped_events=len(losses),
                max_abs_clamped_error_cents=max((abs(r['nominal_error_cents']) for r in losses), default=0),
                clamped_event_examples=[dict(source_event_id=r['source_event_id'], ch=r['ch'],
                                             error_cents=r['nominal_error_cents']) for r in losses[:3]],
                known_pitch_loss=bool(losses),
                assumed_reset_kc_events=sum(r['assumed_reset_kc'] for r in rows),
                assumed_reset_kf_events=sum(r['assumed_reset_kf'] for r in rows),
                deferred_setup_events=sum(r['status'] == 'deferred_before_first_keyon' for r in rows),
                scope='KC/KF base pitch only; DT1, EG/key scaling, LFO, noise and chip timers not retimed; not acoustic equivalence')
