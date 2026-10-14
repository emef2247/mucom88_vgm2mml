"""Inspectable, intentionally lossy MDX output policy; source evidence is immutable."""
from dataclasses import asdict, replace
from types import SimpleNamespace

from note_normalization import infer_timing
from opm_mdx import mdx_tick, projected_samples
from opm_target_pruning import analyze_gates, omit_short_gates
from conversion_config import normalization_samples


SHORT_NOTE_SAMPLES = 352  # floor(44100 * 0.008); 353 samples exceeds 8 ms.
FRAME_MULTIPLIER = 65  # 16.640 ms; a preferred fallback, not a minimum period.


def bounded_ticks(times, protected, multiplier, bound):
    """Allocate exact MDX sample domains through the shared ordered solver."""
    from ordered_timing import allocate_tick_domains

    numerator, denominator = 7056 * multiplier, 625
    ceil_div = lambda a: -(-a // numerator)
    lower = [max(0, ceil_div((t-bound)*denominator)) for t in times]
    upper = [ceil_div((t+bound+1)*denominator)-1 for t in times]
    if times:
        lower[0] = upper[0] = 0
    nearest = [mdx_tick(t, multiplier) for t in times]
    return allocate_tick_domains(times, lower, upper, nearest, protected)


def normalize_output(segments, original, *, source_events=None, pcm_analysis=None,
                     loop_metadata=None, source_event_times=None, normalization_ms=8):
    bound = normalization_samples(normalization_ms)
    preferred = max(1, (min(255 * SHORT_NOTE_SAMPLES, FRAME_MULTIPLIER * bound) + SHORT_NOTE_SAMPLES // 2) // SHORT_NOTE_SAMPLES)
    events = tuple(segments if source_events is None else source_events)
    loop = loop_metadata or {}
    policy_events = tuple(replace(e, vgmticks=source_event_times.get(e.source_event_id, e.vgmticks))
                          for e in events) if source_event_times is not None else events
    pruned, pruning = omit_short_gates(policy_events, original, bound)
    pruning['timing_basis'] = 'original_source_before_normalization'
    if loop.get('status') == 'valid':
        starts = (loop['loop_start_samples'], loop['decoded_end_samples'])
        crossing = [g for g in pruning['omitted_gates'] if any(
            g['source_on_vgmticks'] < t < g['source_off_vgmticks'] for t in starts)]
        if crossing:
            allowed = {i for g in pruning['omitted_gates'] if g not in crossing
                       for i in (g['source_on_event_id'], g['source_off_event_id'])}
            pruned = replace(original, writes=tuple(w for w in original.writes
                                                   if w.register != 8 or w.source_event_id not in allowed))
            omitted = [g for g in pruning['omitted_gates'] if g not in crossing]
            pruning.update(omitted_gates=omitted, omitted_gate_count=len(omitted),
                           omitted_key_write_count=len(allowed),
                           omitted_duration_samples=sum(g['duration_samples'] for g in omitted),
                           loop_crossing_gates_retained=len(crossing), known_loss=bool(omitted))
    facts = analyze_gates(events, pruned)
    protected = [dict(start_vgmticks=0, end_vgmticks=original.source_end_vgmticks, kind='song')]
    protected += [dict(r, kind='opm_gate') for r in facts['survivor_gate_intervals']]
    rest_intervals = [dict(r, kind='opm_rest') for r in facts['survivor_key_rest_intervals']]
    protected += [r for r in rest_intervals if r['duration_samples'] > bound]
    # A partial slot change/retrigger is an operation, not a short complete note.
    by_ch = {}
    surviving_ids = {w.source_event_id for w in pruned.writes if w.register == 8}
    for e in events:
        if e.register == 8 and e.source_event_id in surviving_ids:
            by_ch.setdefault(e.ch, []).append(e)
    for ch, keys in by_ch.items():
        keys.sort(key=lambda e: (e.vgmticks, e.source_event_id))
        for a, b in zip(keys, keys[1:]):
            if (a.vgmticks < b.vgmticks and
                    (a.state.key_mask or b.vgmticks-a.vgmticks > bound)):
                protected.append(dict(ch=ch, start_vgmticks=a.vgmticks,
                                      end_vgmticks=b.vgmticks, kind='opm_key_operation'))
    effects = {}
    for w in pruned.writes:
        if w.register in (1, 0x14):
            previous = effects.get(w.register)
            if previous is not None and previous.source_vgmticks < w.source_vgmticks:
                protected.append(dict(start_vgmticks=previous.source_vgmticks,
                                      end_vgmticks=w.source_vgmticks, kind='side_effect'))
            effects[w.register] = w
    omitted_pcm = []
    if pcm_analysis is not None:
        if pcm_analysis.source_end_vgmticks != original.source_end_vgmticks:
            raise ValueError('OPM and PCM normalization require the same source end')
        omitted_pcm = [p.playback_id for p in pcm_analysis.playbacks
                       if getattr(pcm_analysis, 'normalization_omission_safe', True)
                       and 0 < p.end_vgmticks - p.start_vgmticks <= bound]
        previous = None
        for p in pcm_analysis.playbacks:
            if p.playback_id in omitted_pcm:
                continue
            protected.append(dict(start_vgmticks=p.start_vgmticks, end_vgmticks=p.end_vgmticks,
                                  kind='pcm_gate', playback_id=p.playback_id))
            if previous is not None and previous.end_vgmticks < p.start_vgmticks:
                rest = dict(start_vgmticks=previous.end_vgmticks,
                            end_vgmticks=p.start_vgmticks, kind='pcm_rest',
                            duration_samples=p.start_vgmticks-previous.end_vgmticks)
                rest_intervals.append(rest)
                if rest['duration_samples'] > bound:
                    protected.append(rest)
            previous = p
    if loop.get('status') == 'valid':
        protected.append(dict(start_vgmticks=loop['loop_start_samples'],
                              end_vgmticks=loop['decoded_end_samples'], kind='loop'))
    channels = {ch: [] for ch in range(9)}
    for e in events:
        if e.rising_mask and e.source_event_id in surviving_ids:
            channels[e.ch].append(SimpleNamespace(vgmticks=e.vgmticks, key_on_edge=True))
    if pcm_analysis is not None:
        channels[8] = [SimpleNamespace(vgmticks=p.start_vgmticks, key_on_edge=True)
                       for p in pcm_analysis.playbacks if p.playback_id not in omitted_pcm]
    fitted = infer_timing(channels)
    times = {0, original.source_end_vgmticks}
    times.update(w.source_vgmticks for w in pruned.writes)
    # Validation also observes nonchanging source events. Give them the same
    # ordered map, rather than reverting to nearest rounding between moved nodes.
    times.update(e.vgmticks for e in events)
    if pcm_analysis is not None:
        times.update(c.vgmticks for c in pcm_analysis.controls)
        times.update(t for p in pcm_analysis.playbacks for t in (p.start_vgmticks, p.end_vgmticks))
    for interval in protected:
        times.update((interval['start_vgmticks'], interval['end_vgmticks']))
    times = sorted(times)
    report = dict(status='unchanged', reason='no bounded target clock',
                  source_segments_unchanged=True, source_pcm_ir_unchanged=True,
                  source_origin_samples=0, before=original.timing_report(),
                  method='bounded_target_quantization', correction_bound_samples=bound,
                  normalization_ms=float(normalization_ms),
                  short_note_policy=pruning, omitted_pcm_playback_ids=omitted_pcm,
                  anchor_counts=dict(opm=sum(len(channels[ch]) for ch in range(8)), pcm=len(channels[8])),
                  preferred_fallback_multiplier=preferred,
                  fitted_clock=asdict(fitted) if fitted else None)
    multipliers = list(range(preferred, 0, -1))
    if fitted is not None:
        nominated = round(fitted.samples_per_step * 625 / 7056)
        report['nominated_multiplier'] = nominated
        if 1 <= nominated <= 255:
            multipliers = [nominated] + [n for n in multipliers if n != nominated]
    invalid_loop = loop.get('loop_offset') and loop.get('status') != 'valid'
    rejected = []
    selected = None
    for multiplier in (() if invalid_loop else multipliers):
        ticks = {t: mdx_tick(t, multiplier) for t in times}
        nearest = ticks.copy()
        collapsed = [r for r in protected if r['end_vgmticks'] > r['start_vgmticks']
                     and ticks[r['end_vgmticks']] <= ticks[r['start_vgmticks']]]
        if collapsed:
            allocated = bounded_ticks(times, protected, multiplier, bound)
            if allocated is not None:
                ticks = allocated
        errors = {t: projected_samples(ticks[t], multiplier) - t for t in times}
        worst = max(map(abs, errors.values()), default=0)
        collapsed = [r for r in protected if r['end_vgmticks'] > r['start_vgmticks']
                     and ticks[r['end_vgmticks']] <= ticks[r['start_vgmticks']]]
        ordered = ticks[0] == 0 and all(ticks[a] <= ticks[b] for a, b in zip(times, times[1:]))
        if worst > bound or collapsed or not ordered:
            rejected.append(dict(multiplier=multiplier, max_abs_error_samples=worst,
                                 collapsed_protected_intervals=len(collapsed), examples=collapsed[:3]))
            continue
        selected = replace(pruned, sample_multiplier=multiplier,
            boundary_ticks=tuple(ticks.items()),
            writes=tuple(replace(w, mdx_tick=ticks[w.source_vgmticks],
                                 projected_vgmticks=projected_samples(ticks[w.source_vgmticks], multiplier))
                         for w in pruned.writes),
            end_mdx_tick=ticks[original.source_end_vgmticks],
            end_projected_vgmticks=projected_samples(ticks[original.source_end_vgmticks], multiplier))
        report.update(status='applied', reason='short-note omission and bounded target clock; surviving gates stay positive, short rests may coalesce',
                      clock_selection=('musical_fit' if fitted and multiplier == report.get('nominated_multiplier')
                                       else 'bounded_target_quantization'),
                      max_abs_correction_samples=worst,
                      boundary_allocation='bounded_ordered' if ticks != nearest else 'nearest',
                      adjusted_nearest_boundaries=sum(ticks[t] != nearest[t] for t in times),
                      collapsed_positive_intervals=sum(ticks[a] == ticks[b] for a, b in zip(times, times[1:])),
                      collapsed_protected_intervals=0, candidate=selected.timing_report(),
                      coalesced_rest_intervals=[r for r in rest_intervals
                                               if ticks[r['start_vgmticks']] == ticks[r['end_vgmticks']]],
                      protected_intervals=protected)
        break
    if selected is not None:
        rests = report['coalesced_rest_intervals']
        report.update(coalesced_rest_count=len(rests),
                      coalesced_rest_duration_samples=sum(r['duration_samples'] for r in rests),
                      intentional_timing_loss=bool(rests))
    report['rejected_clock_candidates'] = rejected
    if selected is None:
        report.update(reason='declared source loop boundary is not valid' if invalid_loop else report['reason'],
                      short_note_omission_adopted=False, omitted_pcm_playback_ids=[])
        return original, report, []
    report['short_note_omission_adopted'] = True
    after_by_id = {(w.source_event_id, w.target_sequence): w for w in selected.writes}
    evidence = []
    for w in original.writes:
        after = after_by_id.get((w.source_event_id, w.target_sequence))
        evidence.append(dict(source_chip='opm', source_event_id=w.source_event_id,
            target_sequence=w.target_sequence,
            source_segment_ids=list(w.source_segment_ids), source_samples=w.source_vgmticks,
            register=w.register, data=w.data, before_tick=w.mdx_tick,
            before_projected_samples=w.projected_vgmticks,
            candidate_tick=after.mdx_tick if after else None,
            candidate_projected_samples=after.projected_vgmticks if after else None,
            candidate_error_samples=after.projected_vgmticks-w.source_vgmticks if after else None,
            output_action='retained' if after else 'omitted_short_note_key', projection_status='applied'))
    for t in times:
        evidence.append(dict(source_chip='shared', source_event_id=None, source_samples=t,
            before_tick=original.mdx_tick(t), before_projected_samples=original.projected_samples(original.mdx_tick(t)),
            candidate_tick=ticks[t], candidate_projected_samples=projected_samples(ticks[t], multiplier),
            candidate_error_samples=errors[t], projection_status='applied', output_action='boundary_projection'))
    if pcm_analysis is not None:
        for control in pcm_analysis.controls:
            t = control.vgmticks
            evidence.append(dict(source_chip='pcm', source_event_id=control.source_event_id,
                source_samples=t, before_tick=original.mdx_tick(t),
                before_projected_samples=original.projected_samples(original.mdx_tick(t)),
                candidate_tick=ticks[t], candidate_projected_samples=projected_samples(ticks[t], multiplier),
                candidate_error_samples=errors[t], projection_status='applied', output_action='pcm_control_boundary'))
    return selected, report, evidence
