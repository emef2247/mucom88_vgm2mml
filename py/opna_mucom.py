"""Diagnostic OPNA Segment-to-MUCOM register-MML projection.

This is a semantic FM control probe, not musical note/instrument MML. Source
register identifiers select a typed field; source data bytes are never replayed.
MUCOMVGM's virtual sample clock is required for the exact-sample rest profile.
"""
import csv
from dataclasses import dataclass
import json

from opna import OpnaSegment, register_channel

CLOCK_HZ = 7987200


@dataclass(frozen=True)
class MucomWrite:
    source_event_id: int
    source_vgmticks: int
    port: int
    register: int
    data: int
    segment_ids: tuple[int, ...]


@dataclass(frozen=True)
class MucomProjection:
    writes: tuple[MucomWrite, ...]
    source_end_vgmticks: int
    clock_hz: int
    source_segments: tuple[OpnaSegment, ...]
    diagnostics: tuple[str, ...]


def _pack(*fields):
    value = 0
    for item, shift, mask in fields:
        if item is None:
            raise ValueError('MUCOM projection requires a known typed parameter for an observed write')
        if type(item) is not int or not 0 <= item <= mask:
            raise ValueError('MUCOM projection typed parameter is outside its native range')
        value |= item << shift
    return value


def _encode(segment, diagnostics):
    state, register = segment.state, segment.register
    port, local = divmod(segment.ch, 3)
    if 0x30 <= register <= 0x9f:
        bank = (register & 15) // 4
        operator = state.operators[bank]
        group = register & 0xf0
        fields = {
            0x30: ((operator.dt, 4, 7), (operator.mul, 0, 15)),
            0x40: ((operator.tl, 0, 127),),
            0x50: ((operator.ks, 6, 3), (operator.ar, 0, 31)),
            0x60: ((operator.am_enabled, 7, 1), (operator.d1r, 0, 31)),
            0x70: ((operator.d2r, 0, 31),),
            0x80: ((operator.sl, 4, 15), (operator.rr, 0, 15)),
            0x90: ((operator.ssg_eg_enabled, 3, 1), (operator.ssg_eg_shape, 0, 7)),
        }[group]
        return ((port, group + bank * 4 + local, _pack(*fields)),)
    if 0xb0 <= register <= 0xb2:
        return ((port, 0xb0 + local, _pack((state.feedback, 3, 7), (state.algorithm, 0, 7))),)
    if 0xb4 <= register <= 0xb6:
        return ((port, 0xb4 + local, _pack((state.left_enabled, 7, 1), (state.right_enabled, 6, 1),
                                         (state.ams, 4, 3), (state.pms, 0, 7))),)
    if segment.port == 0 and register == 0x28:
        channel = local | (4 if port else 0)
        return ((0, 0x28, _pack((state.key_mask, 4, 15)) | channel),)
    if 0xa4 <= register <= 0xa6 or segment.port == 0 and 0xac <= register <= 0xae:
        return ()  # Latch-only source state has no committed pitch change.
    if 0xa0 <= register <= 0xa2:
        low_reg = 0xa0 + local
        fnum, block = state.fnum, state.block
        low_raw, high_raw = state.fnum_low_raw, state.fnum_high_raw
    elif segment.port == 0 and 0xa8 <= register <= 0xaa:
        port, low_reg = 0, register
        index = register - 0xa8
        name = ('op3', 'op1', 'op2')[index]
        _, fnum, block = next(value for value in state.special_frequencies if value[0] == name)
        low_raw, high_raw = state.special_frequencies_raw[index]
    else:
        shared = {
            0x24: state.timer_a_high_raw, 0x25: state.timer_a_low_raw,
            0x26: state.timer_b_raw, 0x27: state.mode_raw,
        }
        if segment.port == 0 and register in shared:
            data = shared[register]
            if data is None:
                raise ValueError('MUCOM shared control has no source-state value')
            return ((0, register, data & (3 if register == 0x25 else 255)),)
        if segment.port == 0 and register == 0x22:
            return ((0, register, _pack((state.lfo_enabled, 3, 1), (state.lfo_rate, 0, 7))),)
        if segment.port == 0 and register == 0x29:
            if state.channel_enable_raw is None:
                raise ValueError('MUCOM output enable has no source-state value')
            data = (state.channel_enable_raw & 127) | _pack((state.six_channel_enabled, 7, 1))
            return ((0, register, data),)
        if segment.port == 0 and register in (0x2d, 0x2e, 0x2f):
            if state.prescaler_address != register:
                raise ValueError('MUCOM prescaler boundary disagrees with source-state address')
            return ((0, state.prescaler_address, 0),)
        raise ValueError('MUCOM projection accepts modeled native FM Segment boundaries only')
    if fnum is not None and block is not None:
        high = _pack((block, 3, 7), (fnum >> 8, 0, 7))
        low = fnum & 255
    else:
        low, high = low_raw, None if high_raw is None else high_raw & 63
        diagnostics.add('Partial pitch state emitted without inventing an unwritten frequency high value')
    if low is None:
        raise ValueError('MUCOM pitch commit has no known low frequency byte')
    # The compiler/writer pairs frequency highs per register, unlike native
    # cross-port latches. Each low therefore explicitly carries its own high.
    return (((port, low_reg + 4, high),) if high is not None else ()) + ((port, low_reg, low),)


def project(segments, *, end_vgmticks, clock_hz):
    """Project immutable native FM boundaries; initial unknown state emits nothing."""
    if type(end_vgmticks) is not int or end_vgmticks < 0:
        raise ValueError('MUCOM source end must be a nonnegative integer sample')
    if type(clock_hz) is not int or clock_hz != CLOCK_HZ:
        raise ValueError('Diagnostic MUCOMVGM profile requires YM2608 clock 7987200 Hz')
    source_segments = tuple(segments)
    groups = {}
    diagnostics = {'Diagnostic register-MML; SSG/rhythm/ADPCM and acoustic envelope verification excluded',
                   'MUCOMVGM virtual sample clock C192 T55125; not an ordinary MUCOM88 tempo'}
    for segment in source_segments:
        if not isinstance(segment, OpnaSegment):
            raise ValueError('MUCOM projection requires native OPNA Segments')
        if (segment.chip_type != 'YM2608' or segment.chip_instance != 0
                or segment.clock_hz != clock_hz or segment.clock_raw != clock_hz):
            raise ValueError('Diagnostic MUCOM profile supports one declared YM2608 instance only')
        if not 0 <= segment.vgmticks <= segment.vgmticks_end <= end_vgmticks:
            raise ValueError('MUCOM Segment lies outside its source timeline')
        if segment.state.csm_enabled == 1:
            raise ValueError('MUCOM CSM timer-generated key edges are not modeled')
        if segment.source_event_id is None:
            if segment.ev_type != 'initial':
                raise ValueError('MUCOM observed boundary lacks a source event identifier')
            continue
        key = (segment.chip_instance, segment.source_event_id)
        groups.setdefault(key, []).append(segment)
    writes = []
    prescaler_dummy = {}
    ordered = sorted(groups.values(), key=lambda group: (group[0].vgmticks, group[0].source_event_id))
    for group in ordered:
        first = group[0]
        if any((item.vgmticks, item.port, item.register) != (first.vgmticks, first.port, first.register)
               for item in group):
            raise ValueError('MUCOM source event group has inconsistent boundary provenance')
        target = register_channel(first.register, 0, first.port) if first.register != 0x28 else first.ch
        chosen = next((item for item in group if item.ch == target), first)
        encoded = _encode(chosen, diagnostics)
        ids = tuple(sorted(item.segment_id for item in group))
        for port, register, data in encoded:
            if port == 0 and register in (0x2d, 0x2e, 0x2f):
                # Address selection drives the prescaler; its data is unused.
                # Alternate dummy data so the compiler cache retains repeats.
                data = prescaler_dummy.get(register, 1) ^ 1
                prescaler_dummy[register] = data
                diagnostics.add('Prescaler unused data alternates to retain address strobes through writer deduplication')
            writes.append(MucomWrite(first.source_event_id, first.vgmticks, port, register, data, ids))
    return MucomProjection(tuple(writes), end_vgmticks, clock_hz, source_segments, tuple(sorted(diagnostics)))


def render(projection):
    """Render one ordered control track with exact virtual-sample rests."""
    tokens = ['C192', 'T55125']
    cursor = 0
    def rest(samples):
        while samples:
            amount = min(samples, 255)
            tokens.append(f'r%{amount}')
            samples -= amount
    for write in projection.writes:
        if write.source_vgmticks < cursor:
            raise ValueError('MUCOM writes must be ordered in source time')
        rest(write.source_vgmticks - cursor)
        cursor = write.source_vgmticks
        # Decimal address admits the compiler's full 0..511 port namespace.
        tokens.append(f'y{write.register + write.port * 256},${write.data:02X}')
    if cursor > projection.source_end_vgmticks:
        raise ValueError('MUCOM write exceeds source end')
    rest(projection.source_end_vgmticks - cursor)
    lines = ['#title OPNA diagnostic FM register projection',
             '; Native Segment state -> semantic controls; not musical note MML.',
             '; Requires MUCOMVGM virtual sample clock; no acoustic playback claim.',
             'A ' + ' '.join(tokens)]
    return '\n'.join(lines) + '\n'


def dump_projection(projection, *, csv_path):
    with open(csv_path, 'w', encoding='utf-8', newline='') as stream:
        fields = list(MucomWrite.__dataclass_fields__)
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for write in projection.writes:
            row = dict(write.__dict__)
            row['segment_ids'] = json.dumps(write.segment_ids, separators=(',', ':'))
            writer.writerow(row)
