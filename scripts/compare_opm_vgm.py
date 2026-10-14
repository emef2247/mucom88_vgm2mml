"""Inspect two OPM VGM state/Segment trajectories without a waveform oracle.

Channel mappings are explicit. Absolute sample positions are retained; the
report distinguishes state differences from differing key/retrigger commands.
"""
import argparse
from collections import Counter
import csv
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py'))
from opm import NOTE_NAMES, OPERATOR_NAMES, build_segments, dump_analysis, register_role
from vgm_io import read_vgm_bytes
from vgm_reader import parse_vgm
from vgm_timing import command_times


def command_counts(raw):
    counts, roles, repeated = Counter(), Counter(), Counter()
    values, end = {}, 0
    wait_bytes = 0
    loop_offset = struct.unpack_from('<I', raw, 0x1c)[0]
    loop_address = 0x1c + loop_offset if loop_offset else None
    loop_entry = None
    for event in command_times(raw):
        if event.address == loop_address:
            loop_entry = event.vgmticks
        counts[f'{event.command:02x}'] += 1
        end = event.vgmticks + event.wait_samples
        if event.wait_samples:
            wait_bytes += 3 if event.command == 0x61 else 1
        if event.command == 0x54:
            register, data = raw[event.address + 1:event.address + 3]
            role = register_role(register)
            roles[role] += 1
            if values.get(register) == data:
                repeated[role] += 1
            values[register] = data
    return dict(uncompressed_bytes=len(raw), source_end_vgmticks=end,
                command_counts=dict(counts), wait_encoding_bytes=wait_bytes,
                opm_writes_by_role=dict(roles), same_value_writes_by_role=dict(repeated),
                loop_entry_vgmticks=loop_entry, loop_header_samples=struct.unpack_from('<I', raw, 0x20)[0],
                traversal='Stored traversal only; header loops are not replayed.',
                same_value_note='Repeated values may have side effects; no writes are removed.')


def analyze(path, output):
    metadata = {}
    parse_vgm(str(path), str(output), opm_metadata=metadata)
    if not metadata.get('clock_hz') or metadata.get('dual_chip'):
        raise ValueError('Comparison requires one declared OPM chip per input')
    analysis = build_segments(metadata['csv_path'], end_vgmticks=metadata['source_end_vgmticks'])
    dump_analysis(analysis, state_csv=output / 'state.csv', segments_csv=output / 'segments.csv')
    return analysis, command_counts(read_vgm_bytes(path))


def state_fields(state):
    # These are control facts. Key release does not establish acoustic silence.
    fields = dict(key_mask=state.key_mask, pitch=(state.kc_raw, state.kf_raw),
                  routing=(state.left_enabled, state.right_enabled),
                  algorithm_feedback=(state.algorithm, state.feedback),
                  pms=state.pms, ams=state.ams)
    for name, operator in zip(OPERATOR_NAMES, state.operators):
        for field, value in asdict(operator).items():
            fields[f'{name}.{field}'] = value
    return fields


def nominal_pitch(segment):
    state = segment.state
    if state.octave is None or state.note is None or state.kf is None:
        return None
    return 12 * state.octave + NOTE_NAMES.index(state.note) + state.kf / 64


def known(value):
    return value is not None and (not isinstance(value, tuple) or all(known(v) for v in value))


def compare_segments(left, right, mapping):
    """Compare positive-duration states at the union of absolute boundaries.

    No alignment, quantization or gate-to-silence inference is applied here.
    Short boundary discrepancies remain visible instead of passing a tolerance.
    """
    rows, channels = [], []
    horizon = min(left.source_end_vgmticks, right.source_end_vgmticks)
    for left_ch, right_ch in mapping:
        a = sorted((s for s in left.segments if s.ch == left_ch and s.duration_samples > 0),
                   key=lambda s: s.vgmticks)
        b = sorted((s for s in right.segments if s.ch == right_ch and s.duration_samples > 0),
                   key=lambda s: s.vgmticks)
        boundaries = sorted({0, horizon} | {t for segments in (a, b) for s in segments
                            for t in (s.vgmticks, s.vgmticks_end) if 0 <= t <= horizon})
        ia = ib = 0
        differing, unknown = Counter(), Counter()
        pitch_deltas = Counter()
        missing = 0
        for start, end in zip(boundaries, boundaries[1:]):
            while ia < len(a) and a[ia].vgmticks_end <= start:
                ia += 1
            while ib < len(b) and b[ib].vgmticks_end <= start:
                ib += 1
            sa = a[ia] if ia < len(a) and a[ia].vgmticks <= start < a[ia].vgmticks_end else None
            sb = b[ib] if ib < len(b) and b[ib].vgmticks <= start < b[ib].vgmticks_end else None
            row = dict(left_channel=left_ch, right_channel=right_ch, start_vgmticks=start,
                       end_vgmticks=end, left_segment_id=sa.segment_id if sa else '',
                       right_segment_id=sb.segment_id if sb else '', differing_fields='', unknown_fields='')
            if sa is None or sb is None:
                missing += end - start
                row['unknown_fields'] = 'missing_segment'
            else:
                fa, fb = state_fields(sa.state), state_fields(sb.state)
                diff, unk = [], []
                for field in fa:
                    row['left_' + field] = json.dumps(fa[field], separators=(',', ':'))
                    row['right_' + field] = json.dumps(fb[field], separators=(',', ':'))
                    if not known(fa[field]) or not known(fb[field]):
                        unknown[field] += end - start
                        unk.append(field)
                    elif fa[field] != fb[field]:
                        differing[field] += end - start
                        diff.append(field)
                row.update(differing_fields=';'.join(diff), unknown_fields=';'.join(unk))
                pa, pb = nominal_pitch(sa), nominal_pitch(sb)
                held_routed = bool(sa.state.key_mask and sb.state.key_mask and
                                   (sa.state.left_enabled or sa.state.right_enabled) and
                                   (sb.state.left_enabled or sb.state.right_enabled))
                if pa is not None and pb is not None:
                    cents = 100 * (pb - pa) + 1200 * math.log2(sb.clock_hz / sa.clock_hz)
                    row['nominal_pitch_delta_cents'] = cents
                    if held_routed:
                        pitch_deltas[f'{cents:.6f}'] += end - start
                row['both_keys_held_and_routed'] = held_routed
            rows.append(row)
        left_keys = [(e.vgmticks, e.data & 0xf8) for e in left.events if e.ch == left_ch and e.register == 8]
        right_keys = [(e.vgmticks, e.data & 0xf8) for e in right.events if e.ch == right_ch and e.register == 8]
        channels.append(dict(left_channel=left_ch, right_channel=right_ch,
                             compared_samples=horizon, missing_segment_samples=missing,
                             differing_samples_by_field=dict(differing), unknown_samples_by_field=dict(unknown),
                             nominal_pitch_delta_cents_samples=dict(pitch_deltas),
                             left_key_commands=len(left_keys), right_key_commands=len(right_keys),
                             key_commands_identical=left_keys == right_keys,
                             left_rising_edges=sum(bool(e.rising_mask) for e in left.events if e.ch == left_ch),
                             right_rising_edges=sum(bool(e.rising_mask) for e in right.events if e.ch == right_ch),
                             left_falling_edges=sum(bool(e.falling_mask) for e in left.events if e.ch == left_ch),
                             right_falling_edges=sum(bool(e.falling_mask) for e in right.events if e.ch == right_ch)))
    return dict(comparison='OPM Segment control-state diagnostics', channel_mapping=mapping,
                left_end_vgmticks=left.source_end_vgmticks, right_end_vgmticks=right.source_end_vgmticks,
                end_difference_samples=right.source_end_vgmticks-left.source_end_vgmticks,
                compared_through_vgmticks=horizon, channels=channels,
                left_unmapped_keyed_channels=sorted({s.ch for s in left.segments if s.state.key_mask}
                                                   - {a for a, _ in mapping}),
                right_unmapped_keyed_channels=sorted({s.ch for s in right.segments if s.state.key_mask}
                                                    - {b for _, b in mapping}),
                waveform_equivalence_checked=False, timing_alignment='absolute samples; no warping or tolerance',
                pitch_delta_scope='Nominal KC/KF/clock cents while both keys are held and routed; no chip frequency-table or envelope simulation.',
                scope='All mapped positive-duration control states, including released/muted states; key command counts include zero-duration events.',
                key_state_scope='Key commands and edges only; instantaneous state/order at each Key requires the separate strict roundtrip verifier.',
                excluded_state_fields='Shared LFO/timer/noise controls are retained in dumps but not compared by this focused report.'), rows


def run_comparison(left_path, right_path, output, mapping):
    left_path, right_path, output = Path(left_path).resolve(), Path(right_path).resolve(), Path(output).resolve()
    if any(path.is_relative_to(output) for path in (left_path, right_path)):
        raise ValueError('Comparison output must not contain either input')
    if len(set(a for a, _ in mapping)) != len(mapping) or len(set(b for _, b in mapping)) != len(mapping):
        raise ValueError('Channel mapping must be one-to-one')
    if not mapping or any(not 0 <= channel < 8 for pair in mapping for channel in pair):
        raise ValueError('Channel mapping requires channels 0..7')
    output.mkdir(parents=True, exist_ok=True)
    left, left_counts = analyze(left_path, output / 'left')
    right, right_counts = analyze(right_path, output / 'right')
    report, rows = compare_segments(left, right, mapping)
    report.update(left_input=str(left_path), right_input=str(right_path),
                  left_file_sha256=hashlib.sha256(left_path.read_bytes()).hexdigest(),
                  right_file_sha256=hashlib.sha256(right_path.read_bytes()).hexdigest(),
                  left_clock_hz=sorted({e.clock_hz for e in left.events}),
                  right_clock_hz=sorted({e.clock_hz for e in right.events}),
                  left_commands=left_counts, right_commands=right_counts)
    with (output / 'intervals.csv').open('w', newline='', encoding='utf-8') as stream:
        fields = list(dict.fromkeys(field for row in rows for field in row)) or ['start_vgmticks', 'end_vgmticks']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (output / 'key_events.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('side', 'channel', 'vgmticks', 'event_id', 'data', 'rising_mask', 'falling_mask'))
        writer.writeheader()
        for side, analysis in (('left', left), ('right', right)):
            for event in analysis.events:
                if event.register == 8:
                    writer.writerow(dict(side=side, channel=event.ch, vgmticks=event.vgmticks,
                                         event_id=event.source_event_id, data=event.data,
                                         rising_mask=event.rising_mask, falling_mask=event.falling_mask))
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--channel-map', default='0:0,1:1,2:2,3:3,4:4,5:5,6:6,7:7',
                        help='Explicit zero-based left:right pairs, e.g. 5:4,6:5,7:6 for the inspected PSG comparisons')
    args = parser.parse_args()
    try:
        mapping = tuple(tuple(map(int, pair.split(':'))) for pair in args.channel_map.split(','))
        if any(len(pair) != 2 for pair in mapping):
            raise ValueError('Each channel mapping needs left:right')
        report = run_comparison(args.left, args.right, args.outdir, mapping)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Segment comparison: {args.outdir / "report.json"}; end difference {report["end_difference_samples"]} samples')


if __name__ == '__main__':
    main()
