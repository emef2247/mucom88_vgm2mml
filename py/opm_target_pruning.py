"""Explicit output-only loss policy for complete, short OPM key gates."""
from dataclasses import replace


def analyze_gates(segments, projection):
    """Inspect original key evidence against surviving projected writes.

    Full source State events may be supplied instead of Segments: this retains
    unchanged Key-On requests which otherwise disappear at Segment creation.
    No register/state value or source timestamp is inferred or overwritten.
    """
    writes = {w.source_event_id: w for w in projection.writes if w.register == 8}
    events = {}
    for event in segments:
        if event.register == 8 and event.source_event_id is not None:
            events.setdefault(event.source_event_id, event)
    by_channel = {}
    for event in sorted(events.values(), key=lambda e: (e.vgmticks, e.source_event_id)):
        by_channel.setdefault(event.ch, []).append(event)
    complete, intervals, rests, timestamps = [], [], [], []
    for ch, source_keys in sorted(by_channel.items()):
        for on, off in zip(source_keys, source_keys[1:]):
            mask = on.state.key_mask
            if (on.source_event_id not in writes or off.source_event_id not in writes
                    or not mask or on.data & 128 or on.rising_mask != mask
                    or on.falling_mask or (on.data >> 3 & 15) != mask
                    or off.data != ch or off.state.key_mask != 0
                    or off.rising_mask or off.falling_mask != mask):
                continue
            a, b = writes[on.source_event_id], writes[off.source_event_id]
            complete.append(dict(ch=ch, source_on_event_id=on.source_event_id,
                source_off_event_id=off.source_event_id,
                source_on_segment_ids=list(a.source_segment_ids),
                source_off_segment_ids=list(b.source_segment_ids),
                source_on_vgmticks=on.vgmticks, source_off_vgmticks=off.vgmticks,
                duration_samples=off.vgmticks - on.vgmticks,
                on_mdx_tick=a.mdx_tick, off_mdx_tick=b.mdx_tick, key_mask=mask))
        # Continuous nonzero-mask intervals include partial changes/retriggers;
        # only a full zero-mask operation ends them. Keep unclosed gates too.
        start = previous_end = None
        for event in source_keys:
            if event.source_event_id not in writes:
                continue
            timestamps.append(event.vgmticks)
            if event.state.key_mask and start is None:
                start = event.vgmticks
                if previous_end is not None and start > previous_end:
                    rests.append(dict(ch=ch, start_vgmticks=previous_end,
                                      end_vgmticks=start, duration_samples=start - previous_end))
            elif not event.state.key_mask and start is not None:
                if event.vgmticks > start:
                    intervals.append(dict(ch=ch, start_vgmticks=start,
                        end_vgmticks=event.vgmticks, duration_samples=event.vgmticks - start,
                        closed=True))
                previous_end, start = event.vgmticks, None
        if start is not None and projection.source_end_vgmticks > start:
            intervals.append(dict(ch=ch, start_vgmticks=start,
                end_vgmticks=projection.source_end_vgmticks,
                duration_samples=projection.source_end_vgmticks - start, closed=False))
    return dict(complete_gates=complete, survivor_gate_intervals=intervals,
                survivor_key_rest_intervals=rests,
                conservative_key_timestamps=sorted(set(timestamps)))


def omit_short_gates(segments, projection, max_samples=352):
    """Suppress only a complete isolated Key-On/Off pair within the threshold."""
    if not isinstance(max_samples, int) or isinstance(max_samples, bool) or max_samples < 0:
        raise ValueError('Short-gate threshold must be nonnegative integer source samples')
    segments = tuple(segments)
    facts = analyze_gates(segments, projection)
    omitted = [gate for gate in facts['complete_gates']
               if 0 <= gate['duration_samples'] <= max_samples]
    removed = {identity for gate in omitted
               for identity in (gate['source_on_event_id'], gate['source_off_event_id'])}
    after = replace(projection, writes=tuple(w for w in projection.writes
                                            if w.register != 8 or w.source_event_id not in removed))
    survivors = analyze_gates(segments, after)
    report = dict(status='applied' if omitted else 'unchanged',
        policy='omit_complete_short_key_gates', known_loss=bool(omitted),
        max_duration_samples=max_samples, omitted_gate_count=len(omitted),
        omitted_key_write_count=len(removed),
        omitted_duration_samples=sum(gate['duration_samples'] for gate in omitted),
        source_end_vgmticks=projection.source_end_vgmticks,
        song_timing_changed=False, omitted_gates=omitted,
        survivor_gate_intervals=survivors['survivor_gate_intervals'],
        survivor_key_rest_intervals=survivors['survivor_key_rest_intervals'],
        conservative_key_timestamps=survivors['conservative_key_timestamps'])
    return after, report


def omission_comparison_analysis(source, omitted_gates):
    """Derived validation expectation after authorized loss; never rewrite source IR."""
    from opm import OpmRegisterState
    removed = {identity for gate in omitted_gates
               for identity in (gate['source_on_event_id'], gate['source_off_event_id'])}
    if not removed:
        return source
    chip = OpmRegisterState()
    rebuilt, states, last_id = [], {}, None
    for event in source.events:
        if event.source_event_id in removed:
            continue
        if event.source_event_id != last_id:
            _, kind, rising, falling = chip.write(event.register, event.data)
            last_id = event.source_event_id
        updated = replace(event, state=chip.snapshot(event.ch), ev_type=kind,
                          rising_mask=rising, falling_mask=falling,
                          continuity_id=chip.continuity[event.ch])
        rebuilt.append(updated)
        states[event.source_event_id, event.ch] = updated
    segments = []
    for segment in source.segments:
        if segment.source_event_id in removed:
            continue
        event = states.get((segment.source_event_id, segment.ch))
        segments.append(replace(segment, state=event.state, ev_type=event.ev_type,
            rising_mask=event.rising_mask, falling_mask=event.falling_mask,
            continuity_id=event.continuity_id) if event else segment)
    return replace(source, events=tuple(rebuilt), segments=tuple(segments))
