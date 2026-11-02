"""YM2203 (OPN) source -> native MUCOM88 MML.

The source analysis and musical gates are the shared ones (native
imports). This module is the native expression of them: FM channels
1-3 become parts A, B, C with voices pooled into a DAT bank (native
projection via mucom_fm.project_state in mono mode — the chip has no
pan and no LFO), and the SSG channels become parts D, E, F with
tone-only spelling. Timings are quantized to the native C128 clock
grid at a fixed Timer B whose samples-per-clock keeps the 60 Hz
source frame within the baseline timing bound.

SSG noise and hardware-envelope segments are not rendered yet: they
stop the conversion unless --force turns them into rests and records
the omission, the same staging the MD target used for the DCSG.
"""
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path

from conversion_config import inspect_source
from mucom_fm import FNUM, NOTES, TARGET_CLOCK_HZ, project_state
from vgm_io import read_vgm_bytes

TIMER_B = 227
WHOLE_NOTE_CLOCKS = 128
SAMPLES_PER_CLOCK = (256 - TIMER_B) * 2304 * 44100 / 7_987_200
FM_PARTS = 'ABC'
SSG_PARTS = 'DEF'
MAX_PIECE_CLOCKS = 111


class ConversionError(ValueError):
    pass


def _clocks(samples):
    return round(samples / SAMPLES_PER_CLOCK)


def _timed(note, length, *, key_delay=None):
    """Native % clock lengths in <=111-clock pieces, tied like opna_muc."""
    parts = []
    while length:
        chunk = min(MAX_PIECE_CLOCKS, length)
        if parts and note != 'r':
            parts.append('&')
        if key_delay and note != 'r':
            parts.append(key_delay)
        parts.append(f'{note}%{chunk}')
        length -= chunk
    return parts


def _ssg_note(segment):
    frequency = segment.source_clock_hz / (16 * segment.tone_period)
    midi = round(69 + 12 * math.log2(frequency / 440))
    return NOTES[midi % 12], midi // 12 - 1


def _shift_state(state, clock_hz, shifts=(-1, 0, 1, 2, 3)):
    """Re-express (fnum, block) at the block minimizing target detune.

    project_state spells at the state's own block, which suits sources
    whose clock is near the native clock. A YM2203 runs at half the
    native clock, so most source fnums land an octave below the FNUM
    table at their block and would spell as a wrong note name with a
    detune of hundreds. Shifting (fnum, block) by (x2^k, -k) keeps
    fnum * 2**block — and therefore the projected frequency — exact,
    so only the spelling changes. The source state is not modified;
    the original values remain in the native segments CSV. Callers
    working on a pre-scaled state (an intentional frequency change)
    restrict ``shifts`` so the search cannot undo the scaling.
    """
    if state.fnum is None or state.block is None:
        return state
    target = state.fnum * clock_hz * 6 / state.fm_prescale_divisor \
        / TARGET_CLOCK_HZ
    best = None
    for shift in shifts:
        block = state.block - shift
        if not 0 <= block <= 7:
            continue
        candidate = target * 2 ** shift
        detune = min((abs(candidate - table) for table in FNUM),
                     default=0)
        if best is None or detune < best[0]:
            best = (detune, shift, block)
    if best is None or best[1] == 0:
        return state
    from dataclasses import replace
    return replace(state, fnum=state.fnum * 2 ** best[1], block=best[2])


def convert_vgm(input_path, outdir, *, dump_passes=True, diagnostics_outdir=None,
                timing_bound_ms=8.0, loops=True, song_loop=True,
                normalize_lengths=True, normalization_ms=8.0, fm_state_ms=1.0,
                pcm_only=False, pcm_policy='strict', force=False,
                opm_channel_policy='auto', pcm_profile='standard',
                software_volume_curves=True, legacy_loops=False,
                score_guided_loops=False, enhance_macros=True, **_ignored):
    from musical_hal.native_imports import analyze_source
    from musical_hal.native_imports.opn_music import build_opn_gates
    from musical_hal.native_imports.opna_music import (audit_fm_key_edges,
                                                       dump_gates)
    from opn_ssg import build_segments as build_ssg_segments
    source, output = Path(input_path), Path(outdir)
    output.mkdir(parents=True, exist_ok=True)
    passes = Path(diagnostics_outdir) if diagnostics_outdir else output
    passes.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    manifest = output / (stem + '.mucom.json')
    report = dict(schema_version=2, target='mucom88', status='analyzing',
                  source=str(source),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  source_passes=str(passes), diagnostics=[
                      'Native MUCOM88 dialect: FM parts A B C with a DAT '
                      'voice bank, SSG parts D E F, % clock lengths on a '
                      'C128 grid.',
                      f'Gate timings are quantized at Timer B {TIMER_B} '
                      f'({SAMPLES_PER_CLOCK:.2f} samples per clock); the '
                      '60 Hz source frame stays within the baseline '
                      'timing bound.',
                      'SSG pitches are spelled to the nearest semitone '
                      'at the SSG clock; SSG detune is not emitted.',
                      'SSG noise and hardware-envelope segments require '
                      '--force in this stage and are recorded as '
                      'omissions when forced.'],
                  tracks={}, fm_projection=[], ssg_projection=[],
                  stage_timings={},
                  options=dict(force=bool(force)),
                  timing=dict(timer_b=TIMER_B,
                              whole_note_clocks=WHOLE_NOTE_CLOCKS,
                              samples_per_clock=SAMPLES_PER_CLOCK))
    omissions = []

    def save():
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8')

    usage = inspect_source(source)
    report['source_usage'] = usage
    if 'opn' not in usage['used_chips']:
        raise ConversionError('MUCOM target requires a YM2203 (OPN) source '
                              'for this route')
    blockers = [entry for entry in usage['unsupported_commands']
                if 'clock declaration' in entry['reason']]
    if blockers:
        raise ConversionError(blockers[0]['reason'])
    others = [chip for chip in usage['used_chips'] if chip != 'opn']
    if others:
        raise ConversionError('Unsupported source projection: '
                              + ', '.join(sorted(others)))

    raw = read_vgm_bytes(source)
    result = analyze_source(raw, passes, stem)
    if result['errors'].get('opn'):
        raise ConversionError('OPN analysis failed: '
                              + result['errors']['opn']['detail'])
    analysis = result['opn']
    if analysis is None:
        raise ConversionError('OPN analysis unavailable')
    clock_hz = analysis.events[0].clock_hz if analysis.events else 0
    gates = build_opn_gates(analysis)
    audit_fm_key_edges(analysis, gates)
    dump_gates(gates, passes / (stem + '.opn.music.csv'))
    ssg = build_ssg_segments(result['trace_paths']['opn'],
                             end_vgmticks=result['source_end_sample'])
    ssg.dump(passes, stem)
    loop_sample = result['loop'].get('loop_start_samples')
    loop_clock = (_clocks(loop_sample)
                  if song_loop and loop_sample is not None else None)

    voices = {}
    musical_note_units = 0
    part_tokens = {}
    for part in FM_PARTS:
        channel_gates = gates.get(part, ())
        tokens = []
        position = 0
        loop_marked = False

        def rest_until(target_clock):
            nonlocal position
            if target_clock > position:
                tokens.extend(_timed('r', target_clock - position))
                position = target_clock

        for gate in channel_gates:
            gate_start, gate_end = _clocks(gate.start), _clocks(gate.end)
            if gate_end <= gate_start:
                continue
            if loop_clock is not None and position < loop_clock < gate_start:
                rest_until(loop_clock)
                if part == 'A' and not loop_marked:
                    tokens.append('L')
                    loop_marked = True
            rest_until(gate_start)
            if loop_clock is not None and position == loop_clock \
                    and part == 'A' and not loop_marked:
                tokens.append('L')
                loop_marked = True
            spans = []
            for index, control in enumerate(gate.controls):
                start = max(gate_start, _clocks(control.start))
                end = min(gate_end, _clocks(control.end))
                if index == 0:
                    start = gate_start
                if index == len(gate.controls) - 1:
                    end = gate_end
                if end > start:
                    spans.append((control, start, end))
            if not spans:
                continue
            previous_state = None
            rendered = True
            for index, (control, start, end) in enumerate(spans):
                shifted = _shift_state(control.state, clock_hz)
                try:
                    projection = project_state(
                        shifted, clock_hz, voices,
                        initial=index == 0, previous_state=previous_state,
                        target_channel=gate.channel, mono=True)
                except ValueError as error:
                    if not force:
                        raise ConversionError(
                            f'FM {part}: gate at sample {gate.start}: '
                            f'{error}') from error
                    omissions.append(dict(
                        part=part, source_start=gate.start,
                        source_end=gate.end, action='omit_gate',
                        reason=f'fm_projection_failed: {error}'))
                    rendered = False
                    break
                previous_state = shifted
                commands = list(projection.commands)
                key_delay = next((c for c in commands
                                  if c.startswith('KD')), None)
                if key_delay is not None:
                    commands.remove(key_delay)
                tokens.extend(commands)
                tokens.extend(_timed(projection.note, end - start,
                                     key_delay=key_delay))
                position = end
                report['fm_projection'].append(dict(
                    part=part, source_start=control.start,
                    source_end=control.end,
                    source_segment_ids=control.segment_ids,
                    source_event_ids=control.event_ids,
                    voice_id=projection.voice_id,
                    note=projection.note,
                    target_fnum=projection.target_fnum,
                    target_block=projection.target_block,
                    detune=projection.target_fnum
                    - FNUM[NOTES.index(projection.note)],
                    frequency_error_hz=projection.frequency_error_hz))
            if rendered:
                musical_note_units += 1
            else:
                rest_until(gate_end)
        report['tracks'][part] = dict(gates=len(channel_gates))
        part_tokens[part] = tokens

    ssg_blockers = []
    for channel, part in enumerate(SSG_PARTS):
        tokens = []
        position = 0
        current_volume = None
        current_octave = None
        for segment in ssg.segments[channel]:
            duration = segment.vgmticks_end - segment.vgmticks
            if duration <= 0:
                continue
            start, end = _clocks(segment.vgmticks), _clocks(segment.vgmticks_end)
            if end <= start:
                continue
            audible = segment.mode != 0 and bool(segment.amplitude_register)
            needs_unmodeled = audible and (segment.mode != 1
                                           or segment.envelope_enabled)
            if needs_unmodeled:
                reason = ('ssg_noise_not_rendered' if segment.mode != 1
                          else 'ssg_hardware_envelope_not_rendered')
                if not force:
                    ssg_blockers.append(
                        f'SSG {part}: segment at sample {segment.vgmticks} '
                        f'needs {reason.replace("_", " ")}')
                    continue
                omissions.append(dict(
                    part=part, source_start=segment.vgmticks,
                    source_end=segment.vgmticks_end,
                    action='omit_segment', reason=reason))
                audible = False
            if start > position:
                tokens.extend(_timed('r', start - position))
                position = start
            if not audible or not segment.tone_period:
                tokens.extend(_timed('r', end - position))
                position = end
                continue
            if current_volume != segment.volume:
                tokens.append(f'v{segment.volume}')
                current_volume = segment.volume
            note, octave = _ssg_note(segment)
            if current_octave != octave:
                tokens.append(f'o{octave}')
                current_octave = octave
            tokens.extend(_timed(note, end - position))
            position = end
            musical_note_units += 1
            report['ssg_projection'].append(dict(
                part=part, source_start=segment.vgmticks,
                source_end=segment.vgmticks_end,
                tone_period=segment.tone_period, note=note, octave=octave,
                volume=segment.volume))
        report['tracks'][part] = dict(
            segments=len(ssg.segments[channel]))
        part_tokens[part] = tokens
    if ssg_blockers:
        raise ConversionError(ssg_blockers[0] + '; use --force to omit '
                              'those segments for now')
    if omissions:
        report['force_projection'] = dict(enabled=True, omissions=omissions)

    lines = [f'#title {stem}', '#driver mucomDotNET',
             '; Source-derived musical units; inspect source and target '
             'pass CSVs.']
    voice_path = output / (stem + '.dat') if voices else None
    if voice_path:
        lines.append('#voice ' + voice_path.name)
    for part in FM_PARTS + SSG_PARTS:
        tokens = part_tokens.get(part) or _timed('r', WHOLE_NOTE_CLOCKS)
        lines.append(f'{part} C128 t{TIMER_B} q0 ' + ' '.join(tokens))
    muc_path = output / (stem + '.muc')
    muc_path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('cp932'))
    if voice_path:
        ordered = [record for record, _ in
                   sorted(voices.items(), key=lambda item: item[1])]
        voice_path.write_bytes(bytes(32) + b''.join(ordered))
    report.update(status='converted', muc_path=str(muc_path),
                  musical_note_units=musical_note_units,
                  muc_sha256=hashlib.sha256(muc_path.read_bytes()).hexdigest())
    save()
    return report
