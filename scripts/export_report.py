"""Small output-only summaries; never treat encoded-byte checks as playback proof."""
from collections import Counter
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import sys
import textwrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py'))
from pcm_mdx import RATES
from vgm_io import read_vgm_bytes
from vgm_timing import command_times


def _json(path):
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected an object in {path.name}')
    return value


def _csv(path):
    if not path.is_file():
        return []
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def source_statistics(source):
    """Count one physical stream pass, operator edges separately from writes."""
    raw = read_vgm_bytes(source)
    masks = [0] * 8
    on = off = rising = falling = writes = end = 0
    commands = Counter()
    for event in command_times(raw):
        commands[event.command] += 1
        end = event.vgmticks + event.wait_samples
        if event.command != 0x54 or raw[event.address + 1] != 8:
            continue
        value = raw[event.address + 2]
        channel, mask = value & 7, (value >> 3) & 15
        writes += 1
        on += bool(mask)
        off += not mask
        rising += (mask & ~masks[channel]).bit_count()
        falling += (masks[channel] & ~mask).bit_count()
        masks[channel] = mask
    return dict(samples=end, opm_used=bool(commands[0x54]), opm_writes=commands[0x54], writes=writes,
                on=on, off=off, rising=rising, falling=falling)


def _codes(items):
    counts = Counter(str(item.get('code', 'unknown')) for item in items)
    parts = [f'{code} x{count}' for code, count in counts.most_common(8)]
    if len(counts) > 8:
        parts.append(f'+{len(counts) - 8} other codes')
    return ', '.join(parts) or 'none'


def _pdx_payload_check(folder, stem, pdx):
    bindings = _csv(folder / f'{stem}.pcm_bindings.csv')
    if not bindings or not pdx.is_file():
        return 'unmeasured (no current bindings/PDX pair)'
    payload = pdx.read_bytes()
    matched = checked = total = 0
    for binding in bindings:
        bank, slot = int(binding['bank']), int(binding['slot'])
        sample_path = (folder / f'{stem}.pcm' / binding['file']).resolve()
        if not sample_path.is_relative_to((folder / f'{stem}.pcm').resolve()):
            raise ValueError('PCM sample path leaves the generated sample folder')
        sample = sample_path.read_bytes()
        position = (bank * 96 + slot) * 8
        checked += 1
        total += len(sample)
        if bank != 0 or not 0 <= slot < 96 or position + 8 > len(payload):
            continue
        offset, length = struct.unpack_from('>II', payload, position)
        if offset < 768 or offset + length > len(payload):
            continue
        packed = payload[offset:offset + length]
        matched += (len(sample) == length and hashlib.sha256(sample).digest() ==
                    hashlib.sha256(packed).digest())
    return (f'{matched}/{checked} allocated samples byte-exact; {total} encoded bytes; '
            f'PDX {len(payload)} bytes (includes 768-byte table). '
            'Compared with packing inputs; not decoder/playback fidelity')


def _encoded_command_summary(folder, stem):
    rows = _csv(folder / f'{stem}.mdx.commands.csv')
    if not rows:
        return 'Compiled MDX command statistics: unmeasured (no command census)'
    counts = Counter(row['kind'] for row in rows)
    fm_notes = sum(row['kind'] == 'Note' and row['track'] in 'ABCDEFGH' for row in rows)
    pcm_notes = counts['Note'] - fm_notes
    masks = [int(row['operands_hex'].replace(' ', '')[2:4], 16) & 0x78
             for row in rows if row.get('opcode_hex', '').lower().removeprefix('0x') == 'fe'
             and row.get('operands_hex', '').lower().replace(' ', '').startswith('08')]
    return (f'Compiled MDX encoded commands: FM notes {fm_notes}; PCM notes {pcm_notes}; '
            f'holds {counts["KeyOffDisable"]}; rests {counts["Rest"]}; '
            f'raw register 0x08 requests {len(masks)} (On {sum(bool(mask) for mask in masks)}, '
            f'Off {sum(not mask for mask in masks)}). '
            'Repeats/loops not expanded; counts are not source Key-On/Off equivalence')


def _plain_text(lines):
    sections = {'Export summary', 'Source observations', 'Compiled target',
                'Output normalization', 'PCM assessment', 'Diagnostics'}
    rendered = []
    section = None
    for line in lines:
        if not line or line in sections:
            if line in sections:
                section = line
            if line or not rendered or rendered[-1]:
                rendered.append(line)
            continue
        fields = line.split('; ')
        for index, field in enumerate(fields):
            suffix = ';' if index < len(fields) - 1 else ''
            rendered.extend(textwrap.wrap(field + suffix, width=100,
                            initial_indent='  ' if index == 0 else '    ',
                            subsequent_indent='    ', break_long_words=False,
                            break_on_hyphens=False))
        if section != 'Export summary':
            rendered.append('')
    return '\n'.join(rendered).rstrip() + '\n'


def _rejected_clock_summary(normalization):
    candidates = normalization.get('rejected_clock_candidates', [])
    if not candidates:
        return []
    lines = [f'Rejected MDX clock candidates: {len(candidates)}; '
             'timing bounds and positive protected intervals constrain clock selection']
    for candidate in candidates[:3]:
        kinds = Counter(str(item.get('kind', 'unknown')) for item in candidate.get('examples', []))
        examples = ', '.join(f'{kind} x{count}' for kind, count in kinds.items()) or 'none recorded'
        reason = ('positive protected intervals would become zero-length on this clock'
                  if candidate.get('collapsed_protected_intervals', 0)
                  else 'boundary movement exceeds the normalization timing bound')
        lines.append(f'Clock multiplier {candidate.get("multiplier", "unknown")}: '
                     f'max boundary error {candidate.get("max_abs_error_samples", "unknown")} samples; '
                     f'collapsed protected intervals {candidate.get("collapsed_protected_intervals", 0)}; '
                     f'example kinds: {examples} (sampled examples, not total kind counts); '
                     f'reason: {reason}')
    if len(candidates) > 3:
        lines.append(f'Other rejected candidates: {len(candidates) - 3}; see normalization JSON')
    return lines


def write_export_report(source, folder, stem, row):
    """Write a report for success, blocked or failed conversion, without extra replay."""
    source, folder = Path(source), Path(folder)
    unavailable = []

    def optional(label, function, fallback):
        try:
            return function()
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ArithmeticError,
                struct.error) as error:
            unavailable.append(f'{label}: unavailable ({str(error)[:160]})')
            return fallback

    conversion_failed = row['status'].startswith('conversion_')
    conversion = (optional('Route diagnostics', lambda: _json(folder / f'{stem}.conversion.json'), {})
                  if not conversion_failed else {})
    assessment = (optional('PCM assessment', lambda: _json(folder / f'{stem}.pcm.assessment.json'), {})
                  if not conversion_failed else {})
    target_current = not conversion_failed and assessment.get('artifact_status') != 'blocked'
    if not target_current:
        conversion = {}
    pcm_current = target_current and assessment.get('artifact_status') == 'generated'
    timing = (optional('PCM timing', lambda: _json(folder / f'{stem}.pcm.timing.json'), {})
              if pcm_current else {})
    normalization = (optional('Normalization', lambda: _json(folder / f'{stem}.mdx.normalization.json'), {})
                     if target_current else {})
    lines = ['Export summary', '', f'Input: {source.name}', f'Export: {row["status"]}',
             'Export success means files generated; native playback/display is not tested.',
             f'Route: {conversion.get("chip_projection", "unmeasured")}',
             f'Compiler: {row.get("compiler", "unmeasured")}', '', 'Source observations', '']
    try:
        stats = source_statistics(source)
        lines.append(f'Source duration: {stats["samples"]} samples / '
                     f'{stats["samples"] / 44100:.6f} s (one stream pass; loops not expanded)')
        if stats['opm_used']:
            lines.extend([f'Source OPM register writes: {stats["opm_writes"]}',
                          f'OPM 0x08 writes: {stats["writes"]}; nonzero masks (Key-On requests) '
                          f'{stats["on"]}; zero masks (Key-Off requests) {stats["off"]}',
                          f'OPM operator edges: rising {stats["rising"]}; falling {stats["falling"]} '
                          '(4 operators/channel; repeated requests are not new edges)'])
        else:
            lines.append('Source OPM Key-On/Off: not applicable (no actual OPM writes; '
                         'for PSG inputs, PSG has no OPM Key-On signal)')
    except (OSError, ValueError, struct.error) as error:
        lines.append(f'Source statistics: unavailable ({str(error)[:180]})')
    census = (optional('Compiled MDX command statistics', lambda: _encoded_command_summary(folder, stem),
                       'Compiled MDX command statistics: unmeasured')
              if target_current and (folder / f'{stem}.mdx').is_file()
              else 'Compiled MDX command statistics: unmeasured (no current compiled artifact)')
    lines.extend(['', 'Compiled target', '', census,
                  'OPM pitch reproduction: unmeasured (no independent source/target pitch comparison)'])
    if normalization:
        pitch = normalization.get('selected', {}).get('pitch_projection', {})
        if pitch:
            lines.extend(['', 'OPM clock and pitch projection', '',
                          f'Source OPM clock: {pitch["source_clock_hz"]} Hz; '
                          f'target OPM clock: {pitch["target_clock_hz"]} Hz; applied={pitch["applied"]}',
                          f'Pitch method: {pitch["method"]}; '
                          f'maximum nominal rounding error {pitch["max_abs_nominal_error_cents"]:.6f} cents',
                          f'Target reset-zero assumptions: KC events {pitch["assumed_reset_kc_events"]}; '
                          f'KF events {pitch["assumed_reset_kf_events"]}; '
                          f'deferred pre-Key-On setup events {pitch["deferred_setup_events"]}',
                          f'Pitch scope: {pitch["scope"]}'])
            lines.append(f'Pitch range policy: {pitch.get("policy", "error")}; '
                         f'clamped events {pitch.get("clamped_events", 0)}; '
                         f'maximum clamped pitch error {pitch.get("max_abs_clamped_error_cents", 0):.6f} cents; '
                         f'known pitch loss={pitch.get("known_pitch_loss", False)}')
            for example in pitch.get('clamped_event_examples', []):
                lines.append(f'Clamped source event {example["source_event_id"]}, ch{example["ch"]}; '
                             f'pitch error {example["error_cents"]:.6f} cents')
        song_loop = normalization.get('song_loop', {})
        if song_loop:
            lines.extend(['', 'Declared VGM song loop', '',
                f'Song loop: enabled={song_loop.get("enabled")}; emitted={song_loop.get("emitted")}; '
                + song_loop.get('strategy', ''),
                f'Loop playback boundary: {song_loop.get("playback_start_samples")} source samples; '
                f'expanded end {song_loop.get("playback_end_samples")} samples; '
                'original command addresses and replay occurrences retained with --dump-passes'])
        lines.extend(['', 'Output normalization', ''])
        lines.append('Note normalization: ' + str(normalization.get('status', 'see normalization JSON')) +
                     '; ' + str(normalization.get('reason', ''))[:180])
        lines.append(f'Normalization parameter: {normalization.get("normalization_ms", 8)} ms; '
                     f'configured source samples {normalization.get("configured_threshold_samples", normalization.get("correction_bound_samples", "unmeasured"))}; '
                     'whole-gate omission cutoff before normalization and maximum target boundary movement; '
                     f'enabled={normalization.get("enabled", "unmeasured")}')
        selected = normalization.get('selected', {})
        if normalization.get('boundary_allocation'):
            lines.append(f'Boundary allocation: {normalization["boundary_allocation"]}; '
                         f'adjusted nearest boundaries {normalization.get("adjusted_nearest_boundaries", 0)}; '
                         'positive protected intervals retained within the configured movement bound')
        pruning = normalization.get('short_note_policy', {})
        if pruning:
            adopted = normalization.get('short_note_omission_adopted', False)
            threshold = normalization.get('normalization_ms', 8)
            threshold = f'{threshold:g}' if isinstance(threshold, (int, float)) else str(threshold)
            lines.append(f'Short-note omission (<={threshold} ms): adopted={adopted}; '
                         f'FM gates {pruning.get("omitted_gate_count", 0)}; '
                         f'FM duration {pruning.get("omitted_duration_samples", 0)} source samples; '
                         f'PCM playbacks {len(normalization.get("omitted_pcm_playback_ids", []))}. '
                         'Elapsed song time and source IR retained; intentional output loss')
            lines.append(f'Coalesced positive control intervals: '
                         f'{normalization.get("collapsed_positive_intervals", 0)}; '
                         f'maximum output movement {normalization.get("max_abs_correction_samples", 0)} samples')
            lines.append(f'Coalesced short rests: {normalization.get("coalesced_rest_count", 0)}; '
                         f'{normalization.get("coalesced_rest_duration_samples", 0)} source samples '
                         '(positive rest duration lost; ordered Key-Off/On commands retained)')
        if isinstance(selected, dict) and selected.get('tick_microseconds'):
            lines.append(f'Selected MDX clock: {selected["tick_microseconds"]} us/tick; '
                         f'tempo byte {selected.get("tempo_byte", "unknown")}; '
                         f'MML @t{selected.get("tempo_byte", "unknown")}; '
                         'nominal target clock, not measured driver interrupt load')
        lines.extend(optional('Rejected clock diagnostics',
                     lambda: _rejected_clock_summary(normalization), []))
    lines.extend(['', 'PCM assessment', ''])
    if assessment:
        lines.extend([f'PCM policy: {assessment.get("policy", "unknown")}; '
                      f'projection {assessment.get("assessment_status", "unknown")}; '
                      f'runtime {assessment.get("validation_status", "unknown")} '
                      f'({assessment.get("validation_run", "not_run")})',
                      f'PCM target: {assessment.get("target_profile", "unknown")}',
                      'PCM encoded payload: ' + (optional('PCM payload',
                          lambda: _pdx_payload_check(folder, stem, folder / f'{stem}.pdx'), 'unmeasured')
                          if pcm_current else 'unmeasured (no current generated PCM pair)')])

        def frequencies():
            rows = _csv(folder / f'{stem}.pcm_projection.csv')
            exact = 0
            for item in rows:
                index = int(item['mdx_frequency'])
                exact += (0 <= index < len(RATES) and Fraction(int(item['rate_num']),
                          int(item['rate_den'])) == RATES[index])
            return (f'PCM frequency mapping: {exact}/{len(rows)} playback spans exact (rational Hz)' if rows
                    else 'PCM frequency mapping: unmeasured (no projection rows)')

        lines.append(optional('PCM frequency mapping', frequencies, 'PCM frequency mapping: unmeasured')
                     if pcm_current else 'PCM frequency mapping: unmeasured (no current projection rows)')
        lines.append(f'PCM playback spans: {timing.get("pcm_playback_count", "unmeasured")}; '
                     f'max boundary error: {timing.get("pcm_max_abs_timing_error_samples", "unmeasured")} '
                     'source samples (44100 Hz); actual decoder consumption unmeasured')
        for label, key in (('Known losses', 'known_losses'), ('Unverified', 'unverified_items'),
                           ('Failures', 'unexpected_mismatches')):
            items = assessment.get(key, [])
            lines.append(optional(label, lambda: f'{label}: {len(items)}; {_codes(items)}',
                                  f'{label}: unavailable'))
        reasons = optional('Blocked reasons', lambda: list(dict.fromkeys(
            str(reason) for reason in assessment.get('block_reasons', []))), [])
        for reason in reasons[:3]:
            lines.append('Blocked: ' + reason.replace('\n', ' ')[:260])
        if len(reasons) > 3:
            lines.append(f'Blocked: +{len(reasons) - 3} other reasons; see assessment JSON')
    else:
        lines.append('PCM assessment: not available / no PCM projection')
    forced = conversion.get('projection_settings', {}).get('force_projection', {})
    if forced.get('enabled'):
        domain = forced.get('source_domain', 'SSG')
        lines.extend(['', 'Diagnostics', '',
            f"Force projection: enabled; unsupported {domain} intervals muted: {forced['omitted_intervals']}; "
            "source IR and elapsed time retained",
            f"Force omission details: {forced['intervals_csv']}; "
            "full records in conversion projection_settings and OPNA omission JSON"])
        for loss in forced.get('omissions', [])[:8]:
            lines.append(f"Target omission: {domain} ch{loss['source_channel']}; {loss['reason']}; "
                         f"source samples [{loss['start_vgmticks']}, {loss['end_vgmticks']}); "
                         f"seconds [{loss['start_vgmticks']/44100:.6f}, "
                         f"{loss['end_vgmticks']/44100:.6f}); entire interval muted")
        if forced['omitted_intervals'] > 8:
            lines.append(f"Other omitted intervals: {forced['omitted_intervals'] - 8}; see full CSV/JSON")
        if forced.get('fm_omitted_writes'):
            lines.extend([f"Force FM register-write omissions: {forced['fm_omitted_writes']}; "
                          "unsupported/unprojected register effects; native writes retained",
                          f"Force FM omission details: {forced['fm_writes_csv']}"])
            for loss in forced.get('fm_omissions', [])[:8]:
                lines.append(f"Target write omission: OPNA port{loss['port']}; "
                             f"register 0x{loss['register']:02X}; data 0x{loss['data']:02X}; "
                             f"source sample {loss['vgmticks']}; {loss['reason']}")
            if forced['fm_omitted_writes'] > 8:
                lines.append(f"Other omitted FM writes: {forced['fm_omitted_writes'] - 8}; see full CSV/JSON")
        if 'skipped_registers' in forced:
            from opna_force import format_register_list
            lines.extend(format_register_list(forced['skipped_registers']))
            lines.append('Register details: '+forced['registers_csv'])
    if target_current:
        from opna_force import capacity_lines
        capacity = capacity_lines(folder, stem)
        if capacity:
            lines.extend(['', 'Diagnostics', '', *capacity])
    for exclusion in conversion.get('excluded_sources', []):
        lines.append(f'Target omission: {exclusion["chip"]}; {exclusion["reason"]}')
    if row.get('detail') or unavailable:
        lines.extend(['', 'Diagnostics', ''])
    if row.get('detail'):
        lines.append('Detail: ' + row['detail'].replace('\n', ' ')[:320])
    lines.extend(unavailable)
    path = folder / f'{stem}.report.txt'
    path.write_text(_plain_text(lines), encoding='utf-8')
    return path
