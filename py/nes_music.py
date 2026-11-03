"""NES APU/FDS source bundle for the MUCOMSX (OPNA) target.

The musical HAL (musical_hal/nesapu.py) re-executes the APU and
FDS state machines on the source clock and emits Segments. This
module adapts those Segments into the note shapes
mucomsx_conversion consumes, following the dcsg_music bundle
precedent: source evidence stays in the HAL dumps, the views
built here are target-stage inputs and never replace the source
rows.

Lanes: 0 pulse1, 1 pulse2, 2 triangle, 3 noise, 4 FDS, 5 DPCM.
Melodic lanes (0, 1, 2, 4) become 'nesapu' notes whose segments
carry the source waveform (duty square / triangle steps / FDS
wave RAM) for additive OPNA FM projection. The noise lane
becomes 'psg' notes on channel 3 whose views are already
SSG-targeted (the conversion routes them to SSG part D). DPCM
notes are built from the trace slices; their ADPCM-B projection
is a separate stage, so the conversion gates them explicitly.
"""
from dataclasses import dataclass
from pathlib import Path
import json

from mml_envelopes import Note
from mucomsx_source import SourceBundle, _dump_commands, _dump_notes
from musical_hal.nesapu import NOISE_PERIODS, analyze
from pre_envelope_loops import prepare
from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_loop import read_loop_metadata
from vgm_timing import command_times

SSG_CLOCK_HZ = 1_996_800  # OPNA clock 7,987,200 / 4
LANES = ('pulse1', 'pulse2', 'triangle', 'noise', 'fds', 'dpcm')
MELODIC_PARTS = {0: 'A', 1: 'B', 2: 'C', 4: 'H'}


def _byte(value):
    return max(-128, min(127, int(round(value)))) & 0xFF


def pulse_waveform(duty):
    fraction = (0.125, 0.25, 0.5, 0.75)[duty & 3]
    high = int(round(32 * fraction))
    return bytes([127] * high + [128] * (32 - high)).hex()


def triangle_waveform():
    steps = list(range(15, -1, -1)) + list(range(16))
    return bytes(_byte((value - 7.5) * 255 / 15) for value in steps).hex()


def fds_waveform(wave):
    cells = []
    for index in range(0, 64, 2):
        cells.append((wave[index] + wave[index + 1]) / 2)
    return bytes(_byte((value - 31.5) * 255 / 63) for value in cells).hex()


def frequency_hz(kind, period, clock_hz, mod_ratio=1.0):
    if kind == 'pulse':
        return clock_hz / (16 * (period + 1))
    if kind == 'triangle':
        return clock_hz / (32 * (period + 1))
    if kind == 'fds':
        return clock_hz * period * mod_ratio / 2 ** 22
    raise ValueError(f'No pitch frequency for NES lane kind {kind}')


@dataclass(frozen=True)
class NesToneSegment:
    """Target-stage view of one NES melodic segment.

    tone_period/octave/scale are the identity fields the shared
    settings() helper reads; the NES pitch itself is recomputed
    from kind/period/clock at projection time.
    """
    tone_period: int
    kind: str
    duty: int = 0
    wave_id: int = -1
    waveform_hex: str = ''
    mod_ratio: float = 1.0
    clock_hz: int = 0
    octave: int = 4
    scale: str = 'c'
    mode: int = 0
    noise_period: int = 0
    envelope_enabled: int = 0
    envelope_period: int = 0
    envelope_shape: int = 0


@dataclass(frozen=True)
class NesNoiseView:
    """SSG-targeted view of one NES noise segment."""
    tone_period: int
    target_period: int
    noise_period: int
    target_noise_period: int
    mode: int = 2
    octave: int = 4
    scale: str = 'c'
    envelope_enabled: int = 0
    envelope_period: int = 0
    envelope_shape: int = 0


@dataclass(frozen=True)
class NesDpcmSegment:
    event_index: int
    slice_count: int = 0
    tone_period: int = 0
    kind: str = 'dpcm'
    duty: int = 0
    wave_id: int = -1
    mod_ratio: float = 1.0
    octave: int = 4
    scale: str = 'c'
    mode: int = 0
    noise_period: int = 0
    envelope_enabled: int = 0
    envelope_period: int = 0
    envelope_shape: int = 0


def _tone_key(segment):
    return (getattr(segment, 'kind', None), getattr(segment, 'tone_period', None),
            getattr(segment, 'duty', 0), getattr(segment, 'wave_id', -1),
            getattr(segment, 'mod_ratio', 1.0),
            getattr(segment, 'target_noise_period', None),
            getattr(segment, 'event_index', None))


def _group_notes(rows):
    """Group (start, stop, rest, volume, segment, index) rows into Notes.

    A new note starts on a gap, a rest/sound flip, a retrigger
    boundary (encoded by the caller as a forced split) or a tone
    key change; volume-only changes extend the note's runs.
    """
    notes = []
    for start, stop, rest, volume, segment, index, split in rows:
        if stop <= start:
            continue
        previous = notes[-1] if notes else None
        contiguous = (previous is not None
                      and previous.start + previous.length == start)
        if (not contiguous or split or previous is None
                or previous.rest != rest
                or (not rest and _tone_key(previous.segment) != _tone_key(segment))):
            notes.append(Note(segment, start, 0, rest))
        note = notes[-1]
        note.segment_indices.append(index)
        note.length += stop - start
        if not rest:
            if note.runs and note.runs[-1][0] == volume:
                note.runs[-1] = (volume, note.runs[-1][1] + stop - start)
            else:
                note.runs.append((volume, stop - start))
    return notes


def load_nes_bundle(path, outdir, stem=None, song_loop=True):
    """Build a MUCOMSX source bundle from a NES APU/FDS VGM."""
    path, outdir = Path(path), Path(outdir)
    raw = read_vgm_bytes(path)
    header = read_vgm_header(raw)
    header['opll_clock_raw'] = int.from_bytes(raw[0x10:0x14], 'little')
    original = tuple(command_times(raw))
    for event in original:
        if event.command in (0xb4, 0x67, 0x61, 0x62, 0x63, 0x64, 0x66):
            continue
        if 0x70 <= event.command <= 0x7f:
            continue
        raise ValueError(f'Unsupported active command mixed with NES APU: {event.command:#x}')
    metadata = read_loop_metadata(raw)
    if song_loop and metadata['status'] not in ('valid', 'no_loop'):
        raise ValueError(f"Invalid declared VGM loop: {metadata['status']}")
    trace = analyze(raw)
    clock_hz = trace.clock_hz
    if clock_hz <= 0:
        raise ValueError('NES APU source requires a declared NES APU clock')
    retriggers = {(channel, at) for channel, at, _ in trace.retriggers}
    by_channel = {}
    for index, segment in enumerate(trace.segments):
        by_channel.setdefault(segment.channel, []).append((index, segment))

    notes, segments = {}, {}
    melodic = {}
    for channel in (0, 1, 2, 4):
        rows = []
        for index, segment in by_channel.get(channel, []):
            state = segment.state
            if state.kind == 'pulse':
                wave = pulse_waveform(state.duty)
            elif state.kind == 'triangle':
                wave = triangle_waveform()
            else:
                # An FDS segment whose wave RAM was never written keeps
                # the analyzer's wave_id -1; the trace dump retains that
                # evidence, and the target view falls back to the
                # triangle steps rather than failing the whole song.
                wave = fds_waveform(trace.fds_waves[state.wave_id]) \
                    if 0 <= state.wave_id < len(trace.fds_waves) \
                    else triangle_waveform()
            view = NesToneSegment(tone_period=state.period, kind=state.kind,
                                  duty=state.duty, wave_id=state.wave_id,
                                  waveform_hex=wave,
                                  mod_ratio=state.mod_ratio, clock_hz=clock_hz)
            rows.append((segment.start_vgmticks, segment.end_vgmticks,
                         not state.audible, state.volume, view, index,
                         (channel, segment.start_vgmticks) in retriggers))
        if rows:
            melodic[channel] = _group_notes(rows)
            segments[channel] = [segment for _, segment in by_channel[channel]]
    if melodic:
        notes['nesapu'] = melodic

    noise_rows = []
    for index, segment in by_channel.get(3, []):
        state = segment.state
        rate_hz = clock_hz / NOISE_PERIODS[state.noise_period_index]
        target_noise = max(1, min(31, round(SSG_CLOCK_HZ / (16 * rate_hz))))
        view = NesNoiseView(tone_period=4095, target_period=4095,
                            noise_period=state.noise_period_index,
                            target_noise_period=target_noise)
        noise_rows.append((segment.start_vgmticks, segment.end_vgmticks,
                           not state.audible, state.volume, view, index,
                           (3, segment.start_vgmticks) in retriggers))
    if noise_rows:
        notes.setdefault('psg', {})[3] = _group_notes(noise_rows)
        segments.setdefault('psg', {})[3] = [s for _, s in by_channel[3]]

    dpcm_rows = []
    events = {index: event for index, event in enumerate(trace.dpcm_events)}
    for index, slice_ in enumerate(trace.dpcm_slices):
        view = NesDpcmSegment(event_index=slice_.event_index, slice_count=1)
        dpcm_rows.append((slice_.start_vgmticks, slice_.end_vgmticks, False,
                          slice_.volume, view, index,
                          index == 0 or trace.dpcm_slices[index - 1].event_index
                          != slice_.event_index))
    if dpcm_rows:
        notes.setdefault('nesapu', {})[5] = _group_notes(dpcm_rows)

    # Decode each played DPCM sample once and re-encode it as
    # ADPCM-B for the part-K bank. The decoded level sequence is
    # the source waveform at the trigger's DMC bit rate; the
    # fixed linear map to int16 is a level convention, not a
    # per-sample normalization. Playbacks keep their own rate
    # through the note's delta-N spelling at render time.
    from musical_hal.nesapu import DMC_RATE_PERIODS, _decode_dpcm, \
        _sample_memory
    from pcm_mucom import encode_adpcm_b
    memory, _covered = _sample_memory(raw)
    dpcm_samples, event_samples = {}, {}
    played = {slice_.event_index for slice_ in trace.dpcm_slices}
    for index in sorted(played):
        event = events[index]
        key = (event.address, event.length_bytes, event.rate_index)
        event_samples[index] = key
        if key in dpcm_samples:
            continue
        data = bytes(memory.get(event.address + offset, 0)
                     for offset in range(event.length_bytes))
        levels = _decode_dpcm(data, event.level)
        values = [int(round((level - 63.5) / 63.5 * 32767))
                  for level in levels]
        encoded, _pad = encode_adpcm_b(values)
        dpcm_samples[key] = dict(
            encoded=encoded, rate_num=clock_hz,
            rate_den=DMC_RATE_PERIODS[event.rate_index],
            loop=bool(event.loop), address=event.address,
            length_bytes=event.length_bytes,
            rate_index=event.rate_index)

    outdir.mkdir(parents=True, exist_ok=True)
    stem = stem or path.stem
    paths = {}
    trace.dump(outdir, stem)
    for name in ('writes', 'segments', 'dpcm', 'dpcm_slices', 'fds_waves',
                 'source.json'):
        candidate = outdir / f'{stem}.nesapu.{name}'
        if candidate.is_file():
            paths[f'nesapu_{name}'] = str(candidate)
    paths['commands'] = str(outdir / f'{stem}.msx.commands.csv')
    _dump_commands(paths['commands'], raw, original, 1)
    paths['notes'] = str(outdir / f'{stem}.msx.notes.csv')
    _dump_notes(paths['notes'], notes)
    metadata['nes_source_config'] = dict(clock_hz=clock_hz,
                                         lanes=sorted(notes.get('nesapu', {})))
    metadata['nes_trace_issues'] = list(trace.issues)
    metadata['nes_dpcm_events'] = len(events)
    analyses = {}
    for chip, channels in notes.items():
        plans, representatives = prepare(channels, chip)
        analyses[chip] = dict(sample_rows={ch: [] for ch in channels},
                              source_plans=plans, representatives=representatives,
                              source_kind='nesapu')
    if 'nesapu' in analyses:
        analyses['nesapu']['dpcm'] = dict(samples=dpcm_samples,
                                          event_samples=event_samples)
    seg_map = {'nesapu': {ch: rows for ch, rows in segments.items()
                          if isinstance(ch, int)}}
    if 'psg' in segments:
        seg_map['psg'] = segments['psg']
    bundle = SourceBundle(raw, header, metadata, trace.source_end_vgmticks,
                          None, original[-1].vgmticks,
                          seg_map, notes, analyses, paths)
    return bundle
