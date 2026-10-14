"""Native OPM Segments to MDX-dialect MML register controls.

The target keeps per-channel controls and Key masks on tracks A..H.
Source Segments stay unchanged. MDX time is a target projection,
not a new source clock, note interpretation or acoustic simulation.
"""
import csv
from dataclasses import dataclass, replace
import json
from pathlib import Path
from functools import cached_property
from opm import register_channel, register_role
from opm_pitch_projection import TARGET_CLOCK_HZ, project_controls, pitch_report

# Fixed MXDRV playback tick at @t255: 256 us, or 7056/625 VGM samples.
MDX_SAMPLE_NUMERATOR = 7056
MDX_SAMPLE_DENOMINATOR = 625


@dataclass(frozen=True)
class MdxWrite:
    source_event_id: int
    source_segment_ids: tuple[int, ...]
    source_vgmticks: int
    mdx_tick: int
    projected_vgmticks: int
    register: int
    data: int
    source_register: int | None = None
    source_data: int | None = None
    target_sequence: int = 0

    @property
    def ch(self):
        return register_channel(self.register, self.data)

    @property
    def mdx_track(self):
        return chr(65 + (self.ch if self.ch is not None else 0))


@dataclass(frozen=True)
class MdxProjection:
    writes: tuple[MdxWrite, ...]
    source_end_vgmticks: int
    end_mdx_tick: int
    end_projected_vgmticks: int
    clock_hz: int
    sample_multiplier: int = 1
    source_clock_hz: int = TARGET_CLOCK_HZ
    pitch_evidence: tuple = ()
    pitch_policy: str = 'clamp'
    boundary_ticks: tuple = ()

    @cached_property
    def _boundary_map(self):
        return dict(self.boundary_ticks)

    def mdx_tick(self, samples):
        return self._boundary_map.get(samples, mdx_tick(samples, self.sample_multiplier))

    def projected_samples(self, tick):
        return projected_samples(tick, self.sample_multiplier)

    def timing_report(self):
        errors = [w.projected_vgmticks - w.source_vgmticks for w in self.writes]
        errors.append(self.end_projected_vgmticks - self.source_end_vgmticks)
        times = sorted({(w.source_vgmticks, w.mdx_tick) for w in self.writes}
                       | {(self.source_end_vgmticks, self.end_mdx_tick)})
        return dict(source_end_vgmticks=self.source_end_vgmticks,
                    projected_end_vgmticks=self.end_projected_vgmticks,
                    end_mdx_tick=self.end_mdx_tick, tempo_byte=256-self.sample_multiplier,
                    tick_microseconds=256*self.sample_multiplier,
                    max_abs_timing_error_samples=max(map(abs, errors), default=0),
                    collapsed_positive_intervals=sum(a[0] < b[0] and a[1] == b[1]
                                                     for a, b in zip(times, times[1:])),
                    controls=len(self.writes), clock_hz=self.clock_hz,
                    pitch_projection=pitch_report(self))


def mdx_tick(samples, sample_multiplier=1):
    """Nearest absolute MDX tick; no per-gap rounding accumulation."""
    if not isinstance(samples, int) or samples < 0:
        raise ValueError('Source sample positions must be nonnegative integers')
    numerator = MDX_SAMPLE_NUMERATOR * sample_multiplier
    return (samples * MDX_SAMPLE_DENOMINATOR + numerator // 2) // numerator


def projected_samples(tick, sample_multiplier=1):
    return tick * MDX_SAMPLE_NUMERATOR * sample_multiplier // MDX_SAMPLE_DENOMINATOR


def project_segments(segments, *, end_vgmticks, sample_multiplier=1, pitch_policy='clamp'):
    """Consume actual OpmSegments, deduplicating shared-write fanout.

    Source nonchanging writes omitted by Segment construction cannot be
    recovered here. Changed states, Key edges and retained test/timer writes
    are represented in source-event order, including zero-duration Segments.
    """
    if not isinstance(sample_multiplier, int) or not 1 <= sample_multiplier <= 255:
        raise ValueError('MDX sample multiplier must be in 1..255')
    if pitch_policy not in ('clamp', 'error'):
        raise ValueError('OPM pitch policy must be clamp or error')
    end_tick = mdx_tick(end_vgmticks, sample_multiplier)
    facts = set()
    grouped = {}
    for segment in segments:
        facts.add((segment.chip_instance, segment.chip_type, segment.clock_hz))
        if segment.source_event_id is None:
            continue
        identity = (segment.vgmticks, segment.register, segment.data)
        if not 0 <= segment.vgmticks <= end_vgmticks:
            raise ValueError('Segment control is outside source end')
        if (not isinstance(segment.register, int) or not 0 <= segment.register <= 255
                or not isinstance(segment.data, int) or not 0 <= segment.data <= 255):
            raise ValueError('Segment source control must contain byte register/data values')
        if segment.register in (8, 15) or segment.register >= 32:
            if segment.ch != register_channel(segment.register, segment.data):
                raise ValueError('Segment channel disagrees with register control')
        previous, ids = grouped.setdefault(segment.source_event_id, (identity, []))
        if previous != identity:
            raise ValueError('Conflicting shared-write Segment evidence')
        ids.append(segment.segment_id)
    if len(facts) != 1 or next(iter(facts))[:2] != (0, 'YM2151'):
        raise ValueError('MDX target requires one YM2151 instance with a positive source clock')
    source_clock = next(iter(facts))[2]
    if source_clock <= 0:
        raise ValueError('OPM source clock must be positive')
    events = [(event_id, identity, tuple(sorted(ids)))
              for event_id, (identity, ids) in sorted(grouped.items())]
    pitch_evidence = ()
    if source_clock != TARGET_CLOCK_HZ:
        controls, pitch_evidence = project_controls(events, source_clock, policy=pitch_policy)
    else:
        controls = [(event_id, samples, ids, register, data, register, data, 0)
                    for event_id, (samples, register, data), ids in events]
    writes = []
    last_sample = 0
    for _, (samples, _, _), _ in events:
        if samples < last_sample:
            raise ValueError('Segment source-event order is not chronological')
        last_sample = samples
    for event_id, samples, ids, register, data, source_register, source_data, sequence in controls:
        tick = mdx_tick(samples, sample_multiplier)
        writes.append(MdxWrite(event_id, tuple(sorted(ids)), samples, tick,
                               projected_samples(tick, sample_multiplier), register, data,
                               source_register, source_data, sequence))
    return MdxProjection(tuple(writes), end_vgmticks, end_tick,
                         projected_samples(end_tick, sample_multiplier), TARGET_CLOCK_HZ,
                         sample_multiplier, source_clock, pitch_evidence, pitch_policy)


def scheduled_projection(projection, *, track_layout='channels'):
    """Target track scheduling, retaining each write's original source identity.

    MDX processes A..H in order at a tick. Sorting only between tracks at the
    same target tick predicts that schedule; intra-track event order is intact.
    Source projection/CSV order is not rewritten.
    """
    if track_layout == 'conductor':
        return projection
    if track_layout != 'channels':
        raise ValueError('Unknown MDX track layout')
    return replace(projection, writes=tuple(sorted(projection.writes,
                   key=lambda w: (w.mdx_tick, w.mdx_track, w.source_event_id, w.target_sequence))))


def render(projection, *, title='OPM Segment replay', track_layout='channels',
           song_loop_start=None, song_loop_event_id=None):
    loop_tick = (projection.mdx_tick(song_loop_start)
                 if song_loop_start is not None else None)
    if loop_tick is not None and not 0 <= loop_tick < projection.end_mdx_tick:
        raise ValueError('Song loop must have a positive target duration')
    title = str(title).replace('"', "'").replace('\r', ' ').replace('\n', ' ')
    scheduled_projection(projection, track_layout=track_layout)
    layout_comment = ('; OPM Segment target: ch0..7 -> A..H; shared controls on A, noise on H.'
                      if track_layout == 'channels' else
                      '; OPM Segment target: original source controls on conductor A.')
    lines = [f'#title "{title}"', layout_comment,
             f'; MDX @t{256-projection.sample_multiplier} = {256*projection.sample_multiplier} us; r is a time advance, not inferred acoustic silence.',
             '; Source samples and target timing are retained in the controls CSV.',
             '/* Track A */',
             f'A @t{256-projection.sample_multiplier}']
    tracks = {'A': []}
    for write in projection.writes:
        track = write.mdx_track if track_layout == 'channels' else 'A'
        tracks.setdefault(track, []).append(write)
    for track, writes in sorted(tracks.items()):
        if track != 'A':
            lines.append(f'/* Track {track} */')
        cursor = 0
        marker_emitted = False
        def advance(gap):
            while gap:
                count = min(gap, 65535)
                lines.append(f'{track} r%{count}')
                gap -= count
        for write in writes:
            after_marker = (write.source_event_id >= song_loop_event_id
                            if song_loop_event_id is not None else
                            song_loop_start is not None and write.source_vgmticks >= song_loop_start)
            if loop_tick is not None and not marker_emitted and after_marker:
                advance(loop_tick-cursor)
                cursor=loop_tick
                lines.append(f'{track} L')
                marker_emitted=True
            advance(write.mdx_tick - cursor)
            cursor = write.mdx_tick
            # MXC v1.01 accepts full-line comments, but parses an inline ';'
            # after a channel command as music. Keep provenance off that line.
            lines.append(f'; event {write.source_event_id}, sample {write.source_vgmticks}')
            lines.append(f'{track} y{write.register},{write.data}')
        if loop_tick is not None and not marker_emitted:
            advance(loop_tick-cursor)
            cursor=loop_tick
            lines.append(f'{track} L')
        advance(projection.end_mdx_tick - cursor)
    return '\n'.join(lines) + '\n'


def dump_projection(projection, path, *, track_layout='channels'):
    fields = ('source_event_id', 'source_segment_ids', 'source_vgmticks', 'mdx_tick',
              'projected_vgmticks', 'timing_error_samples', 'register', 'data', 'ch', 'mdx_track',
              'logical_track', 'stream_scope', 'event_role',
              'source_register', 'source_data', 'target_sequence')
    with Path(path).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for write in projection.writes:
            row = dict(write.__dict__, ch=write.ch,
                       logical_track=0 if write.ch is None else write.ch + 1,
                       stream_scope='common' if write.ch is None else 'channel',
                       event_role=register_role(write.register),
                       mdx_track=write.mdx_track if track_layout == 'channels' else 'A')
            row['source_segment_ids'] = json.dumps(write.source_segment_ids)
            row['timing_error_samples'] = write.projected_vgmticks - write.source_vgmticks
            writer.writerow(row)
    if projection.pitch_evidence:
        pitch_path = Path(path).with_name(Path(path).name.replace('.controls.csv', '.pitch.csv'))
        if pitch_path == Path(path):
            pitch_path = Path(str(path) + '.pitch.csv')
        with pitch_path.open('w', newline='', encoding='utf-8') as stream:
            fields = tuple(dict.fromkeys(k for row in projection.pitch_evidence for k in row))
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            for row in projection.pitch_evidence:
                writer.writerow(dict(row, source_segment_ids=json.dumps(row['source_segment_ids'])))
