"""Explicit DCSG -> two three-channel AY musical views for MUCOMSX.

Native DCSG evidence remains independent. One AY engine hosts each DCSG chip;
noise uses a free tone slot, or the least-error shared tone/noise amplitude.
That sharing and the different LFSR are approximations, not extra source notes.
"""
from dataclasses import asdict, dataclass, replace
from math import log2
from pathlib import Path
import csv
import json

from chip_segments import PsgSegment
from dcsg import analyze, dump_analysis
from mml_envelopes import extract_notes
from pre_envelope_loops import prepare
from mucomsx_source import SourceBundle, _dump_commands, _dump_notes
from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_loop import read_loop_metadata
from vgm_timing import command_times

TARGET_CLOCK = 2_000_000


@dataclass(frozen=True, kw_only=True)
class DcsgPsgView(PsgSegment):
    target_period: int
    target_noise_period: int
    source_kind: str
    source_members: tuple
    source_instance: int
    source_channel: int
    source_frequency_hz: float | None
    target_frequency_hz: float | None
    source_event_id: int | None
    noise_source_event_id: int | None
    noise_source_members: tuple
    projection_reason: str


def sn_amplitude(attenuation):
    return 0.0 if attenuation is None or attenuation == 15 else 10 ** (-attenuation / 10)


def ay_amplitude(level):
    # Explicit nominal logarithmic approximation; no measured AY DAC table.
    return 0.0 if level == 0 else 10 ** ((level - 15) * 3 / 20)


def ay_level(amplitude):
    return min(range(16), key=lambda level: abs(ay_amplitude(level) - amplitude))


def noise_slot(tone_amplitudes, noise_amplitude, previous=None):
    """Minimize squared tone+noise gain error, preferring stable ties."""
    if noise_amplitude <= 0:
        return None, None, 0.0
    candidates = []
    free = {ch for ch, tone in enumerate(tone_amplitudes) if tone <= 0}
    for ch, tone in enumerate(tone_amplitudes):
        if free and ch not in free:
            continue
        for level in range(16):
            target = ay_amplitude(level)
            # A free tone channel has no unwanted tone contribution because its
            # tone mixer is disabled, so only noise gain contributes there.
            error = (target - noise_amplitude) ** 2
            if tone > 0:
                error += (target - tone) ** 2
            candidates.append((error, ch != previous, ch, level))
    error, _, ch, level = min(candidates)
    return ch, level, error


def project_views(analysis):
    """Return derived sample-time PSG rows, interval audits and approximations."""
    if analysis.config.t6w28:
        raise ValueError('T6W28 cross-chip routing is not supported by the DCSG AY adapter')
    if any(i['code'] == 'undeclared_dcsg_instance' for i in analysis.issues):
        raise ValueError('DCSG projection requires declared source clock and chip instances')
    tracks = {}
    for segment in analysis.segments:
        tracks.setdefault((segment.chip_instance, segment.ch), []).append(segment)
    views, audit, assessments = {}, [], []
    for instance in sorted({k[0] for k in tracks}):
        source = [tracks.get((instance, ch), []) for ch in range(4)]
        cuts = sorted({0, analysis.end_sample} | {
            edge for rows in source for segment in rows
            for edge in (segment.start_sample, segment.end_sample)})
        cursors, previous_slot = [0] * 4, None
        for start, stop in zip(cuts, cuts[1:]):
            if stop <= start:
                continue
            state = []
            for ch in range(4):
                rows = source[ch]
                while cursors[ch] + 1 < len(rows) and rows[cursors[ch]].end_sample <= start:
                    cursors[ch] += 1
                row = rows[cursors[ch]] if rows else None
                state.append(row if row and row.start_sample <= start < row.end_sample else None)
            if analysis.config.stereo:
                for channel, source_row in enumerate(state):
                    if source_row and source_row.audible and source_row.stereo_left != source_row.stereo_right:
                        assessments.append(dict(source_instance=instance, source_channel=channel,
                            start_sample=start, end_sample=stop, reason='source_stereo_collapsed_to_mono',
                            source_members=(source_row.segment_id,)))
            tone_amp = [sn_amplitude(s.attenuation) if s and s.audible and s.frequency_hz else 0.0
                        for s in state[:3]]
            noise = state[3]
            noise_amp = sn_amplitude(noise.attenuation) if (noise and noise.audible
                        and noise.noise_shift_hz) else 0.0
            slot, shared_level, gain_error = noise_slot(tone_amp, noise_amp, previous_slot)
            if slot is not None:
                previous_slot = slot
                noise_period_exact = TARGET_CLOCK / (16 * noise.noise_shift_hz)
                noise_period = min(31, max(1, round(noise_period_exact)))
                reason = ('dcsg_periodic_noise_approximated_by_ay_noise'
                          if not noise.noise_control & 4 else 'dcsg_lfsr_approximated_by_ay_noise')
                if abs(noise_period - noise_period_exact) > 1e-9:
                    reason += ';noise_clock_rounded_or_clamped'
                if tone_amp[slot] > 0:
                    reason += ';independent_fourth_voice_gain_shared'
                assessments.append(dict(source_instance=instance, start_sample=start,
                                        end_sample=stop, reason=reason, target_channel=slot,
                                        source_noise_control=noise.noise_control,
                                        source_noise_shift_hz=noise.noise_shift_hz,
                                        target_noise_period=noise_period,
                                        source_members=(noise.segment_id,), gain_error=gain_error))
            else:
                noise_period, reason = 0, ''
            for ch, row in enumerate(state[:3]):
                target_ch = instance * 3 + ch
                is_tone, is_noise = tone_amp[ch] > 0, ch == slot
                mode = int(is_tone) | (2 if is_noise else 0)
                level = shared_level if is_noise else ay_level(tone_amp[ch])
                frequency = row.frequency_hz if row else None
                exact_period = TARGET_CLOCK / (16 * frequency) if frequency else 1
                period = min(4095, max(1, round(exact_period)))
                if is_tone and period != round(exact_period):
                    assessments.append(dict(source_instance=instance, source_channel=ch,
                        start_sample=start, end_sample=stop, reason='tone_period_clamped',
                        source_members=(row.segment_id,), target_period=period))
                if row and row.audible and not row.frequency_hz:
                    assessments.append(dict(source_instance=instance, source_channel=ch,
                        start_sample=start, end_sample=stop,
                        reason='tone_dc_or_unknown_period_not_pitched', source_members=(row.segment_id,)))
                members = (row.segment_id,) if row else ()
                noise_members = (noise.segment_id,) if is_noise else ()
                view = DcsgPsgView(ev_type='dcsg_projection', time=start / 44100,
                    ch=target_ch, ticks=start, l=stop - start,
                    tone_period=period, volume=level, octave=4, scale='c' if mode and level else 'r',
                    volume_delta=0, pass3_row=(), vgmticks=start, vgmticks_end=stop,
                    mode=mode, noise_period=noise_period if is_noise else 0,
                    envelope_enabled=0, envelope_period=0, envelope_shape=0,
                    mixer=0, amplitude_register=level, target_period=period,
                    target_noise_period=noise_period if is_noise else 0,
                    source_kind='dcsg', source_members=members,
                    source_instance=instance, source_channel=ch,
                    source_frequency_hz=frequency,
                    target_frequency_hz=TARGET_CLOCK / (16 * period) if is_tone else None,
                    source_event_id=row.source_event_id if row else None,
                    noise_source_event_id=noise.source_event_id if is_noise else None,
                    noise_source_members=noise_members, projection_reason=reason if is_noise else '')
                rows = views.setdefault(target_ch, [])
                # Preserve source-membership in audit even when an unchanged
                # view could otherwise be merged textually.
                rows.append(view)
            audit.append(dict(source_instance=instance, start_sample=start,
                              end_sample=stop, source_members=tuple(s.segment_id if s else None for s in state),
                              source_tone_amplitudes=tuple(tone_amp), source_noise_amplitude=noise_amp,
                              noise_target_channel=slot, noise_shared_level=shared_level,
                              noise_gain_error=gain_error))
    return views, audit, assessments


def _dump(path, rows):
    keys = tuple(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (tuple, list, dict))
                             else value for key, value in row.items()})


def load_dcsg_bundle(path, outdir, stem=None, song_loop=True):
    """Build a MUCOMSX source bundle; target views never replace native rows."""
    path, outdir = Path(path), Path(outdir)
    raw = read_vgm_bytes(path)
    header = read_vgm_header(raw)
    header['opll_clock_raw'] = int.from_bytes(raw[0x10:0x14], 'little')
    original = tuple(command_times(raw))
    for event in original:
        if event.command in (0x50, 0x30, 0x4f, 0x3f, 0x61, 0x62, 0x63, 0x64, 0x66):
            continue
        if 0x70 <= event.command <= 0x7f:
            continue
        raise ValueError(f'Unsupported active command mixed with DCSG: {event.command:#x}')
    metadata = read_loop_metadata(raw)
    first_end = original[-1].vgmticks
    replay = song_loop and metadata['status'] == 'valid' and bool(metadata['decoded_loop_samples'])
    if song_loop and metadata['status'] not in ('valid', 'no_loop'):
        raise ValueError(f"Invalid declared VGM loop: {metadata['status']}")
    if song_loop and metadata['status'] == 'valid' and not replay:
        raise ValueError('Cannot replay a zero-duration declared VGM loop')
    analysis = analyze(raw, loop_count=2 if replay else 1)
    views, audit, assessments = project_views(analysis)
    notes = extract_notes(views, 'psg')
    plans, representatives = prepare(notes, 'psg')
    outdir.mkdir(parents=True, exist_ok=True)
    stem = stem or path.stem
    paths = {f'dcsg_{key}': value for key, value in dump_analysis(analysis, outdir, stem).items()}
    paths['commands'] = str(outdir / f'{stem}.msx.commands.csv')
    _dump_commands(paths['commands'], raw, original, 2 if replay else 1)
    paths['dcsg_projection_views'] = str(outdir / f'{stem}.dcsg.ay_views.csv')
    _dump(paths['dcsg_projection_views'], [asdict(row) for rows in views.values() for row in rows])
    paths['dcsg_projection_allocation'] = str(outdir / f'{stem}.dcsg.ay_allocation.csv')
    _dump(paths['dcsg_projection_allocation'], audit)
    paths['dcsg_projection_assessments'] = str(outdir / f'{stem}.dcsg.ay_assessments.csv')
    _dump(paths['dcsg_projection_assessments'], assessments)
    paths['notes'] = str(outdir / f'{stem}.msx.notes.csv')
    _dump_notes(paths['notes'], {'psg': notes})
    source_channels = {}
    for row in analysis.segments:
        source_channels.setdefault(row.chip_instance * 4 + row.ch, []).append(row)
    metadata['dcsg_source_config'] = asdict(analysis.config)
    metadata['dcsg_source_issues'] = analysis.issues
    metadata['dcsg_projection_assessments'] = assessments
    metadata['dcsg_target_engines'] = {'0': 'MSX_PSG', '1': 'OPNA_SSG'}
    detail = dict(sample_rows=views, source_plans=plans, representatives=representatives,
                  source_kind='dcsg', source_analysis=analysis,
                  projection_audit=audit, projection_assessments=assessments)
    bundle = SourceBundle(raw, header, metadata, analysis.end_sample,
                          first_end if replay else None, first_end,
                          {'dcsg': source_channels}, {'psg': notes},
                          {'psg': detail, 'dcsg_projection': detail}, paths)
    bundle.native_psg_parts = 3
    return bundle
