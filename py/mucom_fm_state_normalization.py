"""Inspectable MSX-aligned short-state selection for target FM gates.

Native analysis and complete key-off/release histories are not modified.
"""
from bisect import bisect_left
import csv
from dataclasses import replace
from pathlib import Path

from fm_state_runs import FM_SHORT_STATE_SAMPLES, select_state_runs
from opna_music import semantic_state


def filter_fm_states(tracks, analysis, *, max_samples=FM_SHORT_STATE_SAMPLES):
    effects = sorted({event.vgmticks for event in (*analysis.events, *getattr(analysis, 'unmodeled_writes', ()))
                      if event.register in (0x22, 0x24, 0x25, 0x26, 0x27, 0x29, 0x2d, 0x2e, 0x2f)})
    result, decisions = {}, []
    for part, gates in tracks.items():
        converted = []
        for gate in gates:
            controls = gate.controls
            protected = set()
            reasons = {}
            def protect(indices, reason):
                for member in indices:
                    protected.add(member)
                    reasons.setdefault(member, set()).add(reason)
            # Key-off leaves frequency/level registers in place during release.
            # There is no full-state restore at the release boundary in MUC.
            # Keep the final sounding state rather than leak an approximation
            # into the unbounded native release tail.
            last_positive = next((i for i in reversed(range(len(controls)))
                                  if controls[i].end > controls[i].start), None)
            if (last_positive is not None and
                    controls[last_positive].end-controls[last_positive].start <= max_samples):
                protect((last_positive,), 'release_tail_state')
            for index in range(1, len(controls)):
                before, after = controls[index-1].state, controls[index].state
                def fixed_state(state):
                    return semantic_state(replace(state, fnum=None, block=None,
                        operators=tuple(replace(op, tl=None) for op in state.operators)))
                if fixed_state(before) != fixed_state(after):
                    protect((index-1, index), 'tone_pan_key_or_modulation_change')
                old, new = tuple(op.tl for op in before.operators), tuple(op.tl for op in after.operators)
                if old != new and (None in old or None in new or
                                  len({b-a for a,b in zip(old,new)}) != 1):
                    protect((index-1, index), 'nonuniform_operator_level_change')
            for index, control in enumerate(controls):
                # Zero-time ordered controls and partial operator transitions
                # are side effects, not disposable transient tonal states.
                if control.start == control.end:
                    protect((index,), 'ordered_zero_time_control')
                if index and control.state.key_mask != controls[index-1].state.key_mask:
                    protect((index-1, index), 'partial_operator_key')
                position = bisect_left(effects, control.start)
                if position < len(effects) and effects[position] <= control.end:
                    protect((index,), 'ordered_global_effect')
            kept, dropped = select_state_runs(
                tuple((c.start, c.end, c.state) for c in controls),
                max_samples=max_samples, protected=protected)
            dropped = set(dropped)
            selected = []
            for position, index in enumerate(kept):
                control = controls[index]
                start = gate.start if position == 0 else control.start
                end = controls[kept[position+1]].start if position+1 < len(kept) else gate.end
                current = replace(control, start=start, end=end)
                if (selected and selected[-1].end == current.start
                        and semantic_state(selected[-1].state) == semantic_state(current.state)
                        and index not in protected and kept[position-1] not in protected):
                    previous = selected.pop()
                    current = replace(previous, end=current.end,
                        segment_ids=previous.segment_ids+current.segment_ids,
                        event_ids=previous.event_ids+current.event_ids)
                selected.append(current)
            for index, control in enumerate(controls):
                decisions.append(dict(part=part, gate_start=gate.start, gate_end=gate.end,
                    control_index=index, source_start=control.start, source_end=control.end,
                    source_segment_ids=';'.join(map(str,control.segment_ids)),
                    source_event_ids=';'.join(map(str,control.event_ids)),
                    action='omit_short_state' if index in dropped else 'retain',
                    protected=index in protected, protection_reason=';'.join(sorted(reasons.get(index, ()))), threshold_samples=max_samples))
            converted.append(replace(gate, controls=tuple(selected)))
        result[part] = tuple(converted)
    return result, decisions


def dump_decisions(rows, path):
    fields = ('part', 'gate_start', 'gate_end', 'control_index', 'source_start',
              'source_end', 'source_segment_ids', 'source_event_ids', 'action',
              'protected', 'protection_reason', 'threshold_samples')
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def msx_fm_runs(note, chip):
    """Expose a target note's states in original sample time for CSV inspection."""
    if note.rest:
        return []
    if chip == 'opll':
        return [(row.tick_start, row.tick_end,
                 (row.fnum, row.block, row.inst, row.vol, row.sus, item.patch, item.patch_changes))
                for row, item in zip(note.sample_slices, note.sample_items)]
    if chip != 'scc':
        raise ValueError('Only OPLL and SCC-to-OPNA FM notes use this adapter')
    result = []
    cursor = note.start
    for volume, length in note.runs:
        result.append((cursor, cursor + length, volume))
        cursor += length
    return result


def filter_msx_fm_note(note, chip, *, part='', max_samples=FM_SHORT_STATE_SAMPLES, seams=()):
    """Select held target FM states without editing native notes or their gates.

    SCC pitch/waveform changes already delimit interpreted musical units. Only
    their internal target-FM level runs are eligible here. OPLL patch, sustain,
    key and zero-time side effects stay protected, as does each release state.
    """
    import copy
    from types import SimpleNamespace

    runs = msx_fm_runs(note, chip)
    if not runs:
        return note, []
    protected = set()
    reasons = {}
    def protect(index, reason):
        protected.add(index)
        reasons.setdefault(index, set()).add(reason)
    last_positive = next((i for i in reversed(range(len(runs))) if runs[i][1] > runs[i][0]), None)
    if last_positive is not None:
        protect(last_positive, 'release_tail_state')
    for index, (start, end, state) in enumerate(runs):
        if start == end:
            protect(index, 'ordered_zero_time_control')
        if any(start <= seam <= end for seam in seams):
            protect(index, 'song_loop_carried_state')
        if chip == 'opll':
            row, item = note.sample_slices[index], note.sample_items[index]
            if item.patch_changes:
                protect(index, 'ordered_patch_writes')
                if index:
                    protect(index - 1, 'ordered_patch_writes')
            if index:
                old, old_item = note.sample_slices[index - 1], note.sample_items[index - 1]
                if (old.inst, old.sus, old.keyon, old_item.patch) != (row.inst, row.sus, row.keyon, item.patch):
                    protect(index - 1, 'tone_sustain_or_key_change')
                    protect(index, 'tone_sustain_or_key_change')
    kept, dropped = select_state_runs(runs, max_samples=max_samples, protected=protected)
    view = SimpleNamespace(**vars(note))
    view.runs = []
    slices, items = [], []
    for position, index in enumerate(kept):
        start = note.start if position == 0 else runs[index][0]
        end = runs[kept[position + 1]][0] if position + 1 < len(kept) else note.start + note.length
        if chip == 'opll':
            row = copy.copy(note.sample_slices[index])
            row.ticks = row.tick_start = start
            row.l = end - start
            row.tick_end = end
            item = replace(note.sample_items[index], tick=start, duration=end-start, advance=end-start)
            slices.append(row)
            items.append(item)
            volume = 15 - row.vol
        else:
            volume = runs[index][2]
        view.runs.append((volume, end-start))
    if chip == 'opll':
        view.sample_slices = tuple(slices)
        view.sample_items = tuple(items)
    dropped = set(dropped)
    decisions = [dict(part=part, gate_start=note.start, gate_end=note.start+note.length,
        control_index=index, source_start=start, source_end=end,
        source_segment_ids=';'.join(map(str, note.segment_indices)), source_event_ids='',
        action='omit_short_state' if index in dropped else 'retain', protected=index in protected,
        protection_reason=';'.join(sorted(reasons.get(index, ()))), threshold_samples=max_samples)
        for index, (start, end, _) in enumerate(runs)]
    return view, decisions
