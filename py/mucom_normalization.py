"""Explicit MUCOM output losses and one bounded, ordered native timeline.

Source State/Segments and ADPCM bytes are immutable. This module constructs an
output-only FM view; it never interprets short internal controls as whole gates.
"""
from dataclasses import asdict, replace
from fractions import Fraction
from types import SimpleNamespace

from conversion_config import normalization_samples
from note_normalization import infer_timing
from opna import OpnaRegisterState
from opna_music import build_fm_gates, audit_fm_key_edges
from ordered_timing import allocate_tick_domains

TIMER_UNIT = Fraction(2304 * 44100, 7987200)


def _round_ratio(numerator, denominator):
    """Exact integer ratio rounding, including Python's ties-to-even rule."""
    quotient, remainder = divmod(numerator, denominator)
    twice = remainder * 2
    return quotient + (twice > denominator or (twice == denominator and quotient % 2 != 0))


def _native_tick_domains(times, bound, period):
    numerator, denominator = period.numerator, period.denominator
    lower = [max(0, -(-(t-bound)*denominator//numerator)) for t in times]
    upper = [(t+bound)*denominator//numerator for t in times]
    lower[0] = upper[0] = 0
    nearest = [_round_ratio(t*denominator, numerator) for t in times]
    return lower, upper, nearest


def apply_fm_omissions(analysis, source_event_ids):
    """Replay retained source writes once, preserving original row membership."""
    omitted = set(source_event_ids)
    if not omitted:
        return analysis
    chips, last_write, snapshots, records = {}, {}, {}, []
    for event in analysis.events:
        chip = chips.setdefault(event.chip_instance, OpnaRegisterState())
        identity = (event.chip_instance, event.source_event_id)
        original = event.original_event_id if event.original_event_id is not None else event.source_event_id
        drop = event.register == 0x28 and original in omitted
        if last_write.get(event.chip_instance) != identity:
            before = snapshots.get(('current', event.chip_instance))
            if before is None:
                before = tuple(chip.snapshot(ch) for ch in range(6))
            if drop:
                kind, rising, falling = 'normalized_omitted_key', 0, 0
            else:
                _, kind, rising, falling = chip.write(event.register, event.data, event.port)
            last_write[event.chip_instance] = identity
            snapshots[event.chip_instance] = (before, kind, rising, falling)
        before, kind, rising, falling = snapshots[event.chip_instance]
        state = chip.snapshot(event.ch)
        derived = replace(event, state=state, changed=state != before[event.ch],
                          ev_type=kind, rising_mask=rising, falling_mask=falling,
                          continuity_id=chip.continuity[event.ch])
        snapshots[identity, event.ch] = derived
        current = list(snapshots.get(('current', event.chip_instance), before))
        current[event.ch] = state
        snapshots['current', event.chip_instance] = tuple(current)
        if not drop:
            records.append(derived)
    segments = []
    initial = OpnaRegisterState()
    for segment in analysis.segments:
        derived = snapshots.get(((segment.chip_instance, segment.source_event_id), segment.ch))
        if derived is None:
            if segment.source_event_id is not None:
                raise ValueError('Output FM view lacks a source Segment event')
            segments.append(replace(segment, state=initial.snapshot(segment.ch)))
        else:
            segments.append(replace(segment, state=derived.state, changed=derived.changed,
                ev_type=derived.ev_type, rising_mask=derived.rising_mask,
                falling_mask=derived.falling_mask, continuity_id=derived.continuity_id))
    return replace(analysis, events=tuple(records), segments=tuple(segments))


def omit_short_fm(analysis, bound_samples, loop_start):
    """Only isolated, full-mask, observed on/off pairs qualify before rounding."""
    channels = {}
    for event in analysis.events:
        if event.chip_instance == 0 and event.register == 0x28:
            channels.setdefault(event.ch, []).append(event)
    losses = []
    for ch, keys in channels.items():
        selector = ch if ch < 3 else ch + 1
        for on, off in zip(keys, keys[1:]):
            duration = off.vgmticks - on.vgmticks
            if (on.data != (0xf0 | selector) or on.rising_mask != 15 or on.falling_mask
                    or off.data != selector or off.state.key_mask or off.falling_mask != 15
                    or off.rising_mask or not 0 <= duration <= bound_samples):
                continue
            if loop_start is not None and on.vgmticks < loop_start < off.vgmticks:
                continue
            losses.append(dict(part='ABCHIJ'[ch], channel=ch,
                on_event_id=on.original_event_id if on.original_event_id is not None else on.source_event_id,
                off_event_id=off.original_event_id if off.original_event_id is not None else off.source_event_id,
                source_start=on.vgmticks, source_end=off.vgmticks, duration_samples=duration,
                reason='closed full-mask gate at or below original-time omission bound'))
    ids = {row[key] for row in losses for key in ('on_event_id', 'off_event_id')}
    return apply_fm_omissions(analysis, ids), losses


def normalize_source(analysis, tracks, pcm_analysis, end, loop_start, milliseconds, *, seams=()):
    """Return a candidate policy/map; caller atomically falls back if infeasible.

    Auxiliary eligibility/bank packing must have succeeded before this call.
    SSG and rhythm attacks are retained; independent, observed-reset PCM gates
    may be omitted without modifying their source IR or their packed bank.
    """
    bound = normalization_samples(milliseconds)
    if analysis.source_end_vgmticks != end or pcm_analysis.source_end_vgmticks != end:
        raise ValueError('FM and PCM normalization require the same original source end')
    derived, losses = omit_short_fm(analysis, bound, loop_start)
    gates = build_fm_gates(derived)
    audit_fm_key_edges(derived, gates)
    proposed = {part: list(units) for part, units in tracks.items()}
    omitted_pcm = []
    by_playback = {}
    for segment in pcm_analysis.segments:
        by_playback.setdefault(segment.playback_id, []).append(segment)
    for unit in proposed.get('K', ()):
        members = [s for s in pcm_analysis.segments if s.segment_id in unit.source_segment_ids]
        if (len(members) == 1 and len(by_playback[members[0].playback_id]) == 1
                and members[0].independently_playable and not members[0].repeat
                and not members[0].issues and members[0].termination == 'reset'
                and 0 < unit.end-unit.start <= bound
                and not (loop_start is not None and unit.start < loop_start < unit.end)):
            omitted_pcm.append(dict(playback_id=members[0].playback_id,
                segment_id=members[0].segment_id, source_start=unit.start, source_end=unit.end,
                duration_samples=unit.end-unit.start, reason='independent observed-reset short PCM gate'))
    dropped_pcm_ids = {r['segment_id'] for r in omitted_pcm}
    if dropped_pcm_ids:
        proposed['K'] = [u for u in proposed['K'] if not set(u.source_segment_ids) & dropped_pcm_ids]
    for part, group in gates.items():
        proposed[part] = [SimpleNamespace(part=part, kind='fm', start=g.start, end=g.end,
            slices=tuple((c.start,c.end,'','') for c in g.controls)) for g in group]
    times = {0, end}
    times.update(e.vgmticks for e in analysis.events)
    times.update(e.vgmticks for e in analysis.unmodeled_writes)
    times.update(c.vgmticks for c in pcm_analysis.controls)
    protected = [dict(start_vgmticks=0, end_vgmticks=end, kind='song')]
    if loop_start is not None:
        times.add(loop_start)
        protected.append(dict(start_vgmticks=loop_start, end_vgmticks=end, kind='loop'))
    for part, units in proposed.items():
        previous = None
        for unit in units:
            times.update((unit.start, unit.end))
            for a,b,_,_ in unit.slices:
                times.update((a,b))
            if unit.kind=='pcm' and unit.slices:
                a,b,_,_=unit.slices[0]
                if a<b:protected.append(dict(start_vgmticks=a,end_vgmticks=b,kind='pcm_attack'))
            if unit.kind not in ('release_controls','rhythm_controls'):
                protected.append(dict(start_vgmticks=unit.start, end_vgmticks=unit.end, kind=unit.kind))
                if previous is not None and unit.start-previous > bound:
                    protected.append(dict(start_vgmticks=previous, end_vgmticks=unit.start, kind=part+'_rest'))
                previous = unit.end
    for segment in pcm_analysis.segments:
        times.update((segment.start_vgmticks, segment.end_vgmticks))
    keys = {}
    for event in derived.events:
        if event.register == 0x28:
            keys.setdefault(event.ch, []).append(event)
    for group in keys.values():
        for a,b in zip(group,group[1:]):
            if a.vgmticks < b.vgmticks and (a.state.key_mask or b.vgmticks-a.vgmticks > bound):
                protected.append(dict(start_vgmticks=a.vgmticks, end_vgmticks=b.vgmticks, kind='fm_key_operation'))
    # Positive intervals between trigger/reset operations remain side effects,
    # even when their associated ordinary control slices are short.
    effects = {}
    for event in analysis.unmodeled_writes:
        if event.port == 0 and event.register in (0x0d,0x10):
            identity = event.port,event.register
            previous = effects.get(identity)
            if previous is not None and previous < event.vgmticks:
                protected.append(dict(start_vgmticks=previous,end_vgmticks=event.vgmticks,kind='auxiliary_trigger'))
            effects[identity] = event.vgmticks
            times.add(event.vgmticks)
    for seam in seams:
        if seam['mode'] == 'head_keyoff':times.add(seam['head_key_sample'])
    times = sorted(times)
    observed_times = set(times)
    for interval in protected:
        if interval['start_vgmticks'] not in observed_times or interval['end_vgmticks'] not in observed_times:
            raise ValueError('Normalization protected interval lacks an observed timestamp')
    channels = {ch: [SimpleNamespace(vgmticks=e.vgmticks,key_on_edge=True)
                    for e in group if e.rising_mask] for ch,group in keys.items()}
    # Feed PCM into a free estimator channel, without imposing MGS notation.
    channels[8] = [SimpleNamespace(vgmticks=u.start,key_on_edge=True) for u in proposed.get('K',())]
    fitted = infer_timing(channels)
    nominated = round(256-fitted.samples_per_step*Fraction(3,2)/TIMER_UNIT) if fitted else None
    # Coarsest bounded native period first; no inherited MDX hardware period.
    timers = sorted((t for t in range(256) if (256-t)*TIMER_UNIT <= 2*bound or t == nominated),
                    key=lambda t: (t != nominated, t))
    rejected = []
    selected = None
    for timer in timers:
        period = (256-timer)*TIMER_UNIT
        lower, upper, nearest = _native_tick_domains(times, bound, period)
        # Loop-head full off must happen on L's clock, with both budgets met.
        impossible_seam = False
        for seam in seams:
            if seam['mode']=='head_keyoff':
                a,b=times.index(loop_start),times.index(seam['head_key_sample'])
                lo,hi=max(lower[a],lower[b]),min(upper[a],upper[b])
                if lo > hi:
                    impossible_seam = True;break
                tick=min(hi,max(lo,nearest[a]))
                lower[a]=lower[b]=tick;upper[a]=upper[b]=tick
        if impossible_seam:
            rejected.append(timer);continue
        clock = allocate_tick_domains(times,lower,upper,nearest,protected)
        if clock is None:
            rejected.append(timer);continue
        if any(s['mode']=='head_keyoff' and clock[s['head_key_sample']]!=clock[loop_start] for s in seams):
            rejected.append(timer);continue
        numerator, denominator = period.numerator, period.denominator
        error_numerator = max((abs(clock[t]*numerator-t*denominator) for t in times), default=0)
        if error_numerator > bound*denominator:
            raise AssertionError('Allocator exceeded exact original-source budget')
        selected = clock, timer, period, Fraction(error_numerator, denominator), nearest
        break
    report = dict(enabled=True,applied=selected is not None,normalization_ms=float(milliseconds),
        correction_bound_samples=bound,source_segments_unchanged=True,source_pcm_ir_unchanged=True,
        source_origin_samples=0, timing_basis='original_source_before_normalization',
        omitted_fm_gates=losses if selected else [],omitted_pcm_gates=omitted_pcm if selected else [],
        proposed_omitted_fm_gates=losses, proposed_omitted_pcm_gates=omitted_pcm,
        fitted_clock=asdict(fitted) if fitted else None,nominated_timer_b=nominated,
        protected_intervals=protected,rejected_timer_b=rejected,
        auxiliary_policy='SSG/rhythm retained; PCM eligibility and bytes validated before omission')
    if selected is None:
        report.update(reason='No feasible normalized native clock; restore unmodified baseline atomically',known_loss=False)
        return analysis, tracks, None, None, report
    clock,timer,period,error,nearest = selected
    report.update(reason='original-time whole-gate omission and bounded ordered native clock',
        clock_selection='musical_fit' if timer==nominated else 'bounded_native_timer',
        adjusted_nearest_boundaries=sum(clock[t]!=n for t,n in zip(times,nearest)),
        known_loss=bool(losses or omitted_pcm or error), max_absolute_error_samples=float(error))
    timing=dict(timer_b=timer,whole_note_clocks=128,samples_per_clock=float(period),
        boundary_error_limit_samples=bound,max_absolute_error_samples=float(error),
        candidate_count=1,source_boundaries=[dict(source_sample=t,target_clock=clock[t],
        error_samples=(clock[t]*period.numerator-t*period.denominator)/period.denominator) for t in times])
    if loop_start is not None:
        timing['loop_duration_error_samples']=((clock[end]-clock[loop_start])*period.numerator-(end-loop_start)*period.denominator)/period.denominator
    # FM proxies are for clock selection only. Projection reconstructs real units.
    return derived, {p:u for p,u in proposed.items() if p not in 'ABCHIJ'}, clock, timing, report
