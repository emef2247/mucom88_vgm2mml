"""Validate musical projection through the existing OPM semantic comparator."""
from dataclasses import asdict

from opm import OpmRegisterState
from opm_mdx_structure import compare_hybrid
from opm_roundtrip import _known_differences


def _key_points(analysis, clock=None):
    initial = OpmRegisterState()
    states, points = {}, []
    for event in analysis.events:
        before = states.get(event.ch, initial.snapshot(event.ch))
        if event.register == 8:
            sample = event.vgmticks if clock is None else clock.projected_samples(clock.mdx_tick(event.vgmticks))
            points.append((sample, event.ch, event.data, before, event.state))
        states[event.ch] = event.state
    return points


def _state_differences(expected, actual):
    left, right = asdict(expected), asdict(actual)
    errors = []
    for name in ('channel_registers', 'shared_registers'):
        observed = dict(right.pop(name))
        errors.extend(f'{name}.{reg}' for reg, value in left.pop(name) if observed.get(reg) != value)
    return errors + _known_differences(left, right)


def compare_performance(context, actual, *, initialization):
    """Check all known states, exact Key commands and state at each Key point.

    Unlike the old additive preview check, muted states are included. Note
    expansion can change non-Key register command ordering; this is not an
    assertion of identical raw streams or cross-chip acoustic equivalence.
    """
    correction = context.normalization or {}
    tolerance = correction['correction_bound_samples'] if correction.get('adopted') else 6
    from opm_target_pruning import omission_comparison_analysis
    omitted = correction.get('short_note_policy', {}).get('omitted_gates', []) if correction.get('short_note_omission_adopted') else []
    expected_analysis = omission_comparison_analysis(context.analysis, omitted)
    result = compare_hybrid(context.projection, expected_analysis, actual, initialization=initialization,
                            source_timing_tolerance_samples=tolerance)
    expected = _key_points(expected_analysis, context.projection)
    observed = _key_points(actual)
    commands_match = [p[:3] for p in expected] == [p[:3] for p in observed]
    errors = []
    if commands_match:
        for index, (a, b) in enumerate(zip(expected, observed)):
            for side, reference, replayed in (('before', a[3], b[3]), ('after', a[4], b[4])):
                errors.extend(f'key {index} {side}: {name}' for name in _state_differences(reference, replayed))
    result.update(comparison='projected_musical_opm', state_origin='projected_opm',
                  length_normalization=correction,
                  intentional_omitted_gates=len(omitted),
                  exact_source_equivalence=not omitted and not correction.get('adopted'),
                  source_key_observed=False, phase_preserved=False,
                  key_commands_match=commands_match, key_point_state_mismatches=len(errors),
                  first_key_point_mismatches=errors[:10],
                  scope='Generated musical OPM target, all known/muted states and Key points; not source waveform equivalence')
    result['passed'] = result['passed'] and commands_match and not errors
    return result
