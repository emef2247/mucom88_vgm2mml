"""MSX source analysis for MUCOMSX, before target allocation or rendering.

Native 60 Hz Segments are retained. Explicit sample-time views let the existing
MSX note and structure interpreters consume the common integer source clock.
"""
import copy
import csv
import json
from dataclasses import dataclass, replace
from pathlib import Path

from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_loop import read_loop_metadata
from vgm_reader import parse_vgm
from vgm_timing import command_times
from conversion_config import inspect_source
import psg
import scc
import opll
import opll_segments
import melody_patterns
from mml_envelopes import extract_notes
from opll_note_units import group_notes
from opll_inner_loops import OpllLoopPlan
from pre_envelope_loops import prepare
from mml_utils import get_ticks


@dataclass
class SourceBundle:
    raw: bytes
    header: dict
    metadata: dict
    end_sample: int
    loop_sample: int | None
    first_end_sample: int
    segments: dict
    notes: dict
    analyses: dict
    paths: dict


def _sample_rows(segments):
    result = {}
    for ch, rows in segments.items():
        result[ch] = []
        for row in rows:
            start, end = row.vgmticks, row.vgmticks_end
            if start is None or end is None or end < start:
                raise ValueError(f'MSX ch{ch}: missing or invalid source sample boundaries')
            if hasattr(row, 'pass3_row'):
                view = replace(row, ticks=start, l=end - start)
            else:
                view = copy.copy(row)
                view.ticks = view.tick_start = start
                view.l = end - start
                view.tick_end = end
            result[ch].append(view)
    return result


def _validate_commands(raw, events, header, usage):
    waits = {0x61, 0x62, 0x63, 0x64, 0x66}
    for event in events:
        cmd, pos = event.command, event.address + 1
        if cmd in waits or 0x70 <= cmd <= 0x7f:
            continue
        if cmd == 0xa0:
            if header['ay_clock_raw'] & 0x40000000 or raw[pos] & 0x80:
                raise ValueError('MUCOMSX source analysis does not support a second AY chip')
            continue
        if cmd == 0x51:
            if header['opll_clock_raw'] & 0xc0000000:
                raise ValueError('Unsupported OPLL source variant or dual chip')
            continue
        if cmd == 0xd2:
            if (not header['scc_clock_raw'] & 0x3fffffff
                    and 'scc' not in usage['compatibility_initialization']):
                raise ValueError('SCC writes require a declared SCC source clock')
            if header['scc_clock_raw'] & 0x40000000 or raw[pos] not in range(4):
                raise ValueError('Unsupported SCC source port or second chip')
            continue
        raise ValueError(f'Unsupported active MSX source command {cmd:#x} at {event.address:#x}')


def _capture_scc_initialization(raw, destination, loop_count):
    """Retain undeclared reset-state evidence using the existing SCC decoder.

    The ordinary reader gates SCC CSV capture on its clock declaration. This
    separate view decodes only preflight-confirmed inactive initialization;
    it does not supply a missing clock or make it a sounding target source.
    """
    from vgm_reader import _SccState
    state = _SccState()
    state._include_vgmticks = True
    for event in command_times(raw, loop_count=loop_count):
        if event.command == 0xd2:
            state._vgmticks = event.vgmticks
            port, reg, value = raw[event.address + 1:event.address + 4]
            base = {0: 0x9800, 1: 0x9880, 2: 0x988a, 3: 0x988f}[port]
            state.write_scc(event.vgmticks / 44100, base + reg, value)
    state.output_trace_csv(str(destination))


def _dump_commands(path, raw, original, loop_count):
    sizes = {event.address: (original[i + 1].address - event.address
                             if i + 1 < len(original) else 1)
             for i, event in enumerate(original)}
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(('occurrence_event_id', 'source_event_id', 'loop_iteration',
                         'address', 'start_sample', 'command_hex'))
        for index, event in enumerate(command_times(raw, loop_count=loop_count)):
            data = raw[event.address:event.address + sizes[event.address]]
            writer.writerow((index, event.source_event_id, event.loop_iteration,
                             event.address, event.vgmticks, data.hex()))


def _close_trace(source_path, destination, end_sample):
    """Add an observed EOF boundary in a separate analysis-input view.

    The copied state has no register-write meaning. Original decoder traces
    remain unchanged and the boundary is visibly named in the pass artifacts.
    """
    with open(source_path, newline='') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    last = {}
    for row in rows:
        if row['ch'] != '-1':
            last[row['ch']] = row
    for row in last.values():
        boundary = dict(row)
        boundary.update({'#type': 'sourceEnd', 'time': repr(end_sample / 44100),
                         'ticks': str(get_ticks(end_sample / 44100)),
                         'vgmticks': str(end_sample)})
        rows.append(boundary)
    with open(destination, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _dump_inner_plans(prefix, plan):
    """Consolidate complete child keys/membership/trees without per-note files."""
    members_path = Path(str(prefix) + '.inner_members.csv')
    markers_path = Path(str(prefix) + '.inner_source_markers.csv')
    with members_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(('note_unit_id', 'step', 'segment_index',
                         'structural_representative', 'source_key'))
        for note_id, (indices, child) in enumerate(zip(plan.members, plan.inner)):
            representatives = set(child.representatives())
            writer.writerows((note_id, step, index, int(step in representatives), repr(child.keys[step]))
                             for step, index in enumerate(indices))
    with markers_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(('note_unit_id', 'marker_id', 'pattern_id', 'parent_marker_id',
                         'depth', 'start_unit', 'end_unit', 'body_units', 'repeats',
                         'segment_indices'))
        for note_id, (indices, child) in enumerate(zip(plan.members, plan.inner)):
            if child.structure is None:
                continue
            patterns, markers = {}, []
            def visit(nodes, parent='', depth=1):
                for node in nodes:
                    if not node.children:
                        continue
                    width = (node.end - node.start) // node.repeats
                    signature = child.keys[node.start:node.start + width]
                    pattern_id = patterns.setdefault(signature, len(patterns))
                    identity = len(markers)
                    markers.append((note_id, identity, pattern_id, parent, depth,
                                    node.start, node.end, width, node.repeats,
                                    json.dumps(indices[node.start:node.end])))
                    visit(node.children, identity, depth + 1)
            visit(child.structure.tree)
            writer.writerows(markers)
    return [str(members_path), str(prefix) + '.inner_repeat_candidates.csv', str(markers_path)]


def _dump_integrated(path, segments):
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(('chip', 'channel', 'segment_index', 'start_sample',
                         'end_sample', 'source_ticks', 'source_length', 'state'))
        for chip, channels in segments.items():
            for ch, rows in sorted(channels.items()):
                for index, row in enumerate(rows):
                    fields = (row.__dataclass_fields__ if hasattr(row, '__dataclass_fields__')
                              else dict.fromkeys(row.__slots__))
                    state = {key: getattr(row, key, None) for key in fields}
                    writer.writerow((chip, ch, index, row.vgmticks, row.vgmticks_end,
                                     row.ticks, row.l, json.dumps(state, default=str)))


def _dump_notes(path, notes):
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(('chip', 'channel', 'note_index', 'start_sample',
                         'end_sample', 'rest', 'segment_indices', 'volume_runs'))
        for chip, channels in notes.items():
            for ch, rows in sorted(channels.items()):
                for index, note in enumerate(rows):
                    writer.writerow((chip, ch, index, note.start, note.start + note.length,
                                     int(note.rest), json.dumps(note.segment_indices),
                                     json.dumps(note.runs)))


def load_source(path, outdir, stem=None, song_loop=True):
    """Decode and reuse native MSX musical interpretation, without target limits.

    A valid requested loop is replayed once with the decoder's continuous chip
    state. The repeated-body entrance is the first end, while original declared
    loop facts remain in metadata. This is evidence, not a loop-seam proof.
    """
    path, outdir = Path(path), Path(outdir)
    raw = read_vgm_bytes(path)
    header = read_vgm_header(raw)
    header['opll_clock_raw'] = int.from_bytes(raw[0x10:0x14], 'little')
    original = tuple(command_times(raw))
    usage = inspect_source(path)
    _validate_commands(raw, original, header, usage)
    if usage['unsupported_commands']:
        issue = usage['unsupported_commands'][0]
        raise ValueError(f"Unsupported MSX source: {issue['reason']} at {issue['address']:#x}")
    metadata = read_loop_metadata(raw)
    first_end = original[-1].vgmticks
    replay = (song_loop and metadata['status'] == 'valid'
              and bool(metadata['decoded_loop_samples']))
    if song_loop and metadata['status'] not in ('valid', 'no_loop'):
        raise ValueError(f"Invalid declared VGM loop: {metadata['status']}")
    if song_loop and metadata['status'] == 'valid' and not replay:
        raise ValueError('Cannot replay a zero-duration declared VGM loop')
    outdir.mkdir(parents=True, exist_ok=True)
    stem = stem or path.stem
    trace_paths = parse_vgm(str(path), str(outdir), loop_metadata=metadata,
                            dump_loop=True, include_vgmticks=True,
                            loop_count=2 if replay else 1)
    keys = ('psg_log', 'scc_log', 'psg_trace', 'scc_trace', 'opll_log',
            'opll_trace', 'opll_voice', 'opll_regs')
    paths = dict(zip(keys, map(str, trace_paths)))
    paths['commands'] = str(outdir / f'{stem}.msx.commands.csv')
    _dump_commands(paths['commands'], raw, original, 2 if replay else 1)
    paths['source_usage'] = str(outdir / f'{stem}.msx.source_usage.json')
    Path(paths['source_usage']).write_text(json.dumps(usage, indent=2) + '\n', encoding='utf-8')
    metadata['source_usage'] = usage
    if ('scc' in usage['compatibility_initialization']
            and not header['scc_clock_raw'] & 0x3fffffff):
        init_path = outdir / f'{stem}.scc.initialization_trace.csv'
        _capture_scc_initialization(raw, init_path, 2 if replay else 1)
        paths['scc_initialization_trace'] = str(init_path)

    segments, notes, analyses = {}, {}, {}
    active = {event.command for event in original}
    end_sample = metadata.get('playback_end_samples', first_end)
    for chip, command in (('psg', 0xa0), ('scc', 0xd2), ('opll', 0x51)):
        if command in active:
            boundary_path = outdir / f'{stem}.{chip}.source_boundaries.csv'
            trace_input = paths.get(f'{chip}_initialization_trace', paths[f'{chip}_trace'])
            _close_trace(trace_input, boundary_path, end_sample)
            paths[f'{chip}_analysis_input'] = str(boundary_path)
    if 0xa0 in active:
        segments['psg'] = psg.build_segments(paths['psg_analysis_input'], str(outdir), stem=stem)
    if 0xd2 in active:
        native_scc = scc.build_segments(paths['scc_analysis_input'], str(outdir), stem=stem)
        segments['scc'] = native_scc.segments
        analyses['scc_waveforms'] = native_scc.waveforms
    if 0x51 in active:
        segments['opll'], bpm = opll._build_segments(paths['opll_analysis_input'], debug=True)
        opll_segments.dump_segments(segments['opll'], outdir / f'{stem}.opll.segments.csv')
        analyses['opll_bpm'] = bpm
    for chip, channels in segments.items():
        native_analysis = melody_patterns.dump_analysis(
            channels, chip, str(outdir), stem,
            paths['opll_voice'] if chip == 'opll' else None)
        views = _sample_rows(channels)
        detail = {'melody': native_analysis, 'sample_rows': views}
        if chip != 'opll':
            notes[chip] = extract_notes(views, chip)
            detail['source_plans'], detail['representatives'] = prepare(notes[chip], chip)
            for ch, rows in notes[chip].items():
                for note in rows:
                    note.segment = channels[ch][note.segment_indices[0]]
        else:
            sample_voice = outdir / f'{stem}.opll.sample_voice.csv'
            with open(paths['opll_voice'], newline='') as source:
                reader = csv.DictReader(source)
                with open(sample_voice, 'w', newline='', encoding='utf-8') as target:
                    writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
                    writer.writeheader()
                    for row in reader:
                        row['ticks'] = row['vgmticks']
                        writer.writerow(row)
            paths['opll_sample_voice'] = str(sample_voice)
            sample_analysis = melody_patterns.analyze(views, chip, str(sample_voice))
            notes[chip], detail['units'], detail['sample_items'] = {}, {}, {}
            detail['source_plans'], detail['source_plan_paths'] = {}, {}
            detail['native_notes'], detail['native_units'] = {}, {}
            for ch, (items, _, _) in native_analysis.items():
                detail['native_notes'][ch], detail['native_units'][ch] = group_notes(channels[ch], items)
                sample_items = sample_analysis[ch][0]
                grouped, units = group_notes(views[ch], sample_items)
                for note in grouped:
                    note.segment = channels[ch][note.segment_indices[0]]
                    note.slices = tuple(channels[ch][i] for i in note.segment_indices)
                    note.sample_slices = tuple(views[ch][i] for i in note.segment_indices)
                    note.items = tuple(items[i] for i in note.segment_indices)
                    note.sample_items = tuple(sample_items[i] for i in note.segment_indices)
                    note.rest = not bool(note.segment.keyon)
                    note.runs = [(15 - row.vol, row.l) for row in note.sample_slices if row.l > 0]
                notes[chip][ch] = grouped
                detail['units'][ch] = units
                detail['sample_items'][ch] = tuple(sample_items)
                plan = OpllLoopPlan.build(grouped, units, sample_items)
                detail['source_plans'][ch] = plan
                plan_path = outdir / f'{stem}.opll.ch{ch}.source_structure.csv'
                # No command rendering or target selection occurs here. Empty
                # projection reports deliberately distinguish source candidates.
                plan.dump(plan_path, [n.segment_indices for n in grouped], [])
                inner_paths = _dump_inner_plans(str(plan_path).removesuffix('.csv'), plan)
                detail['source_plan_paths'][ch] = dict(outer=str(plan_path), inner=inner_paths)

        analyses[chip] = detail
    paths['segments'] = str(outdir / f'{stem}.msx.segments.csv')
    paths['notes'] = str(outdir / f'{stem}.msx.notes.csv')
    _dump_integrated(paths['segments'], segments)
    _dump_notes(paths['notes'], notes)
    end = metadata.get('playback_end_samples', first_end)
    return SourceBundle(raw, header, metadata, end, first_end if replay else None,
                        first_end, segments, notes, analyses, paths)
