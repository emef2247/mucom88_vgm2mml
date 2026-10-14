"""Inspectable replay of projected OPM controls, without musical inference.

Target ticks describe held register state, not native source note Segments.
The only initialization assumption is cleared Key gates; unwritten registers
remain unknown. Every write survives, including same-tick and nochange writes.
"""
import csv
from dataclasses import asdict, dataclass
import json

from opm import KEY_BITS, OPERATOR_NAMES, OpmRegisterState, OpmState, register_channel, register_role


@dataclass(frozen=True)
class TargetStateEvent:
    event_id: int
    write_id: int | None
    source_chip: str | None
    source_ch: int | None
    source_row: int | None
    vgmticks: int | None
    mdx_tick: int
    target_ch: int
    register: int | None
    data: int | None
    reason: str
    changed: bool
    rising_mask: int
    falling_mask: int
    state: OpmState


@dataclass(frozen=True)
class TargetStateInterval:
    interval_id: int
    target_ch: int
    mdx_tick: int
    mdx_tick_end: int
    event_ids: tuple[int, ...]
    write_ids: tuple[int, ...]
    state: OpmState

    @property
    def duration_ticks(self):
        return self.mdx_tick_end - self.mdx_tick


def _flatten(record, *, end_tick, source_end_vgmticks):
    row = asdict(record)
    state = row.pop('state')
    for name, operator in zip(OPERATOR_NAMES, state.pop('operators')):
        row.update((f'{name}_{field}', value) for field, value in operator.items())
    for field in ('channel_registers', 'shared_registers'):
        state[field] = json.dumps(state[field], separators=(',', ':'))
    row.update(state)
    row.update(state_origin='projected_opm', target_track=chr(65 + record.target_ch),
               end_mdx_tick=end_tick, source_end_vgmticks=source_end_vgmticks,
               key_reset_assumed=True, key_observed=record.state.key_register_raw is not None,
               gate_state='held' if record.state.key_mask else 'released')
    for name, bit in KEY_BITS.items():
        row[f'{name}_key_on'] = int(bool(record.state.key_mask & bit))
    if isinstance(record, TargetStateEvent):
        row['event_role'] = 'initial' if record.register is None else register_role(record.register)
        for name, bit in KEY_BITS.items():
            row[f'{name}_key_on_edge'] = int(bool(record.rising_mask & bit))
            row[f'{name}_key_off_edge'] = int(bool(record.falling_mask & bit))
    else:
        row['duration_ticks'] = record.duration_ticks
        for field in ('event_ids', 'write_ids'):
            row[field] = json.dumps(row[field], separators=(',', ':'))
    return row


@dataclass(frozen=True)
class TargetTrajectory:
    events: tuple[TargetStateEvent, ...]
    intervals: tuple[TargetStateInterval, ...]
    end_tick: int
    source_end_vgmticks: int

    def summary(self):
        writes = tuple(e for e in self.events if e.write_id is not None)
        return dict(state_origin='projected_opm', target_state_events=len(self.events),
                    target_state_write_events=len(writes), target_state_intervals=len(self.intervals),
                    target_state_channels=len({e.target_ch for e in self.events}),
                    target_state_nonchange_writes=sum(not e.changed for e in writes),
                    end_mdx_tick=self.end_tick, source_end_vgmticks=self.source_end_vgmticks,
                    key_reset_assumed=True, musical_note_count_not_inferred=True)

    def dump(self, *, state_csv, intervals_csv):
        state = OpmRegisterState().snapshot(0)
        empty_event = TargetStateEvent(0, None, None, None, None, None, 0, 0,
                                       None, None, 'initial cleared Key assumption', False, 0, 0, state)
        empty_interval = TargetStateInterval(0, 0, 0, 0, (), (), state)
        for path, records, empty in ((state_csv, self.events, empty_event),
                                     (intervals_csv, self.intervals, empty_interval)):
            def flatten(record):
                return _flatten(record, end_tick=self.end_tick,
                                source_end_vgmticks=self.source_end_vgmticks)
            with open(path, 'w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(flatten(empty)), lineterminator='\n')
                writer.writeheader()
                writer.writerows(flatten(record) for record in records)


def _integer(value, low, high=None):
    return type(value) is int and value >= low and (high is None or value <= high)


def build_target_trajectory(writes, *, end_tick, source_end_vgmticks):
    """Replay a scheduled, channel-owned TargetWrite stream without changing it.

    Order is (target tick, target channel, write ID), as in scheduled_writes().
    Source samples are provenance and need not be globally monotonic after
    target scheduling. Chip-wide controls are outside this projection contract.
    """
    if not _integer(end_tick, 0) or not _integer(source_end_vgmticks, 0):
        raise ValueError('Target and source ends must be nonnegative integers')
    writes = tuple(writes)
    seen, previous = set(), None
    for w in writes:
        if not (_integer(w.write_id, 0) and _integer(w.mdx_tick, 0, end_tick)
                and _integer(w.vgmticks, 0, source_end_vgmticks)
                and _integer(w.target_ch, 0, 7) and _integer(w.register, 0, 255)
                and _integer(w.data, 0, 255)):
            raise ValueError('Invalid target register evidence or end bounds')
        if w.source_chip not in ('psg', 'scc') or not _integer(
                w.source_ch, 0, 2 if w.source_chip == 'psg' else 4):
            raise ValueError('Invalid projection source provenance')
        if w.source_row is not None and not _integer(w.source_row, 0):
            raise ValueError('Invalid projection source row')
        if register_channel(w.register, w.data) != w.target_ch:
            raise ValueError('Target channel disagrees with register ownership')
        order = (w.mdx_tick, w.target_ch, w.write_id)
        if w.write_id in seen or (previous is not None and order < previous):
            raise ValueError('Target writes must be scheduled in order with unique IDs')
        seen.add(w.write_id)
        previous = order

    chip = OpmRegisterState()
    events, intervals, boundaries = [], [], {}
    for ch in sorted({w.target_ch for w in writes}):
        event = TargetStateEvent(len(events), None, None, None, None, None, 0, ch,
                                 None, None, 'initial cleared Key assumption', False, 0, 0,
                                 chip.snapshot(ch))
        events.append(event)
        boundaries[ch] = [event]

    def close(ch, end):
        boundary = boundaries[ch]
        if end > boundary[-1].mdx_tick:
            intervals.append(TargetStateInterval(
                len(intervals), ch, boundary[-1].mdx_tick, end,
                tuple(e.event_id for e in boundary),
                tuple(e.write_id for e in boundary if e.write_id is not None), boundary[-1].state))

    for w in writes:
        ch = w.target_ch
        prior = boundaries[ch][-1]
        if w.mdx_tick != prior.mdx_tick:
            close(ch, w.mdx_tick)
            boundaries[ch] = []
        _, _, rising, falling = chip.write(w.register, w.data)
        state = chip.snapshot(ch)
        event = TargetStateEvent(len(events), w.write_id, w.source_chip, w.source_ch,
                                 w.source_row, w.vgmticks, w.mdx_tick, ch, w.register, w.data,
                                 w.reason, state != prior.state, rising, falling, state)
        events.append(event)
        boundaries[ch].append(event)
    for ch in sorted(boundaries):
        close(ch, end_tick)
    return TargetTrajectory(tuple(events), tuple(intervals), end_tick, source_end_vgmticks)
