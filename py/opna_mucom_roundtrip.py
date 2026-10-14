"""FM control-state verification for the diagnostic MUCOMVGM target.

Compiler byte normalization is checked separately from source semantic states
and operator edges. Neither comparison is an acoustic or musical-score test.
"""
from dataclasses import asdict

from opna import OpnaRegisterState


def semantic_state(state):
    values = asdict(state)
    # These describe observed writes/latch bookkeeping, rather than committed
    # FM controls. MUCOMVGM defers upper frequency writes until a lower commit.
    for field in ('key_register_raw', 'fnum_high_raw', 'frequency_high_latch_raw',
                  'special_frequency_high_latch_raw', 'special_frequencies_raw',
                  'channel_registers', 'shared_registers', 'prescaler_address',
                  'channel_enable_raw', 'lfo_raw'):
        values.pop(field)
    for operator in values['operators']:
        operator.pop('ssg_eg_raw')
    if values['timer_a_low_raw'] is not None:
        values['timer_a_low_raw'] &= 3
    return values


def known_differences(reference, observed, path=''):
    if reference is None:
        return []
    if isinstance(reference, dict):
        return [difference for key, value in reference.items()
                for difference in known_differences(value, observed.get(key), path + key + '.')]
    if isinstance(reference, (tuple, list)):
        if not isinstance(observed, (tuple, list)) or len(reference) != len(observed):
            return [path.rstrip('.')]
        return [difference for index, (left, right) in enumerate(zip(reference, observed))
                for difference in known_differences(left, right, path + str(index) + '.')]
    return [] if reference == observed else [path.rstrip('.')]


def compiler_controls(writes, initialization):
    """Predict v0.0.5 upper-pair buffering and ordinary write deduplication.

    This profile is deliberately separate from the source chip interpreter.
    Every emitted byte retains its target group for boundary state comparison.
    """
    cache = {(port, reg): data for tick, port, reg, data in initialization}
    upper = {}
    output = []
    for write in writes:
        port, reg, value = write.port, write.register, write.data
        key = port, reg
        if reg in (0xa4, 0xa5, 0xa6, 0xac, 0xad, 0xae):
            upper[key] = value
            continue
        if reg in (0xa0, 0xa1, 0xa2, 0xa8, 0xa9, 0xaa):
            high = upper.get((port, reg + 4))
            if high is None:
                cache.pop(key, None)
            else:
                if cache.get(key) == (high, value):
                    continue
                cache[key] = high, value
                output.append((write.source_event_id, write.source_vgmticks, port, reg + 4, high))
        elif key not in ((0, 0x28), (0, 0x10), (0, 0x0d), (1, 0x00)):
            if cache.get(key) == value:
                continue
            cache[key] = value
        output.append((write.source_event_id, write.source_vgmticks, port, reg, value))
    return tuple(output)


def compare(projection, returned, raw_controls, *, initialization, returned_clock_hz):
    """Compare the strict compiler stream, each source boundary, and key edges.

    Only an independently compiled, exact zero-time initialization prefix is
    classified as driver initialization. Its state still participates in the
    comparison; known source controls cannot be hidden by stripping the prefix.
    """
    raw_controls = tuple(raw_controls)
    initialization = tuple(initialization)
    prefix_matches = raw_controls[:len(initialization)] == initialization
    expected = compiler_controls(projection.writes, initialization)
    observed = raw_controls[len(initialization):] if prefix_matches else raw_controls
    expected_controls = tuple(row[1:] for row in expected)
    sequence_matches = observed == expected_controls
    clock_matches = returned_clock_hz == projection.clock_hz
    end_matches = returned.source_end_vgmticks == projection.source_end_vgmticks

    source_groups = {}
    for segment in projection.source_segments:
        if segment.source_event_id is not None:
            source_groups.setdefault(segment.source_event_id, []).append(segment)
    model = OpnaRegisterState()
    for tick, port, reg, data in initialization:
        if reg >= 0x20:
            model.write(reg, data, port)
    # Semantic expectations come from the original Segments, not from the
    # target schedule. Actual states come from the independently re-read VGM.
    returned_groups = {}
    for event in returned.events:
        returned_groups.setdefault(event.source_event_id, []).append(event)
    returned_states = {ch: OpnaRegisterState().snapshot(ch) for ch in range(6)}
    actual_groups = sorted(returned_groups.items())
    position = 0
    boundary_states = {}
    # Match raw write ordinals to actual event IDs, including waits in the VGM
    # event cursor. Each native event repeats one source write for shared state.
    event_by_control = []
    for event_id, group in actual_groups:
        event = group[0]
        event_by_control.append(((event.vgmticks, event.port, event.register, event.data), group))
    all_expected = [(None, *row) for row in initialization] + list(expected)
    if prefix_matches and sequence_matches:
        for source_id, tick, port, reg, data in all_expected:
            if reg < 0x20:
                continue  # Driver rhythm setup remains in Raw/unmodeled CSV.
            control, group = event_by_control[position]
            position += 1
            if control != (tick, port, reg, data):
                sequence_matches = False
                break
            for event in group:
                returned_states[event.ch] = event.state
            if source_id is not None:
                boundary_states[source_id] = dict(returned_states)

    mismatches = []
    if prefix_matches and sequence_matches:
        # Rebuild the initial returned state, retaining the compiler defaults.
        current = {ch: model.snapshot(ch) for ch in range(6)}
        for source_id, group in sorted(source_groups.items()):
            if source_id in boundary_states:
                current = boundary_states[source_id]
            for segment in group:
                for path in known_differences(semantic_state(segment.state),
                                             semantic_state(current[segment.ch])):
                    mismatches.append(dict(source_event_id=source_id, ch=segment.ch, field=path))

    source_edges = [(s.vgmticks, s.ch, s.rising_mask, s.falling_mask, s.state.key_mask)
                    for s in sorted(projection.source_segments, key=lambda s: (s.vgmticks, s.source_event_id or -1, s.ch))
                    if s.rising_mask or s.falling_mask]
    actual_edges = [(e.vgmticks, e.ch, e.rising_mask, e.falling_mask, e.state.key_mask)
                    for e in returned.events if e.rising_mask or e.falling_mask]
    edges_match = source_edges == actual_edges
    source_active = sum(bool(s.rising_mask) for s in projection.source_segments)
    result = dict(comparison_scope='single OPNA FM control state; non-FM and audio excluded',
                  source_clock_hz=projection.clock_hz, returned_clock_hz=returned_clock_hz,
                  clock_matches=clock_matches, initialization_prefix_matches=prefix_matches,
                  compiler_control_sequence_matches=sequence_matches,
                  source_end_vgmticks=projection.source_end_vgmticks,
                  returned_end_vgmticks=returned.source_end_vgmticks, end_matches=end_matches,
                  known_state_mismatches=len(mismatches), first_state_mismatches=mismatches[:20],
                  operator_edge_sequence_matches=edges_match,
                  source_attack_events=source_active,
                  source_key_edge_events=len(source_edges), returned_key_edge_events=len(actual_edges),
                  source_operator_keyons=sum(row[2].bit_count() for row in source_edges),
                  source_operator_keyoffs=sum(row[3].bit_count() for row in source_edges),
                  returned_operator_keyons=sum(row[2].bit_count() for row in actual_edges),
                  returned_operator_keyoffs=sum(row[3].bit_count() for row in actual_edges),
                  expected_compiler_controls=len(expected_controls), returned_controls=len(observed))
    result['passed'] = (prefix_matches and sequence_matches and clock_matches and end_matches
                        and not mismatches and edges_match)
    return result
