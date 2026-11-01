"""YM2612 (OPN2) source -> MUCOM88 MML in the mucomMD2vgm Mega Drive dialect.

The source analysis and musical gates are the shared ones (native
imports); this module is only the MD expression of them: FM channels
1-6 become parts A, B, C, H, I, J, voices are pooled as @N
definitions, and gate timings are quantized to MUCOM clocks at
tempo 150 (two clocks per 60 Hz frame, the grid MD sources use).
SN76489 parts and the DAC channel arrive with their own stages.
"""
from dataclasses import asdict
import csv
import hashlib
import json
import math
from pathlib import Path

from conversion_config import inspect_source
from vgm_io import read_vgm_bytes

TEMPO = 150
SAMPLES_PER_CLOCK = 44100 * 60 / (TEMPO * 48)
PARTS = 'ABCHIJ'
REFERENCE_CLOCK_HZ = 7_987_200  # FNUM table reference (mucom_fm).
MAX_PIECE_CLOCKS = 192

from mucom_fm import FNUM, NOTES


class ConversionError(ValueError):
    pass


def _spell(fnum, block, clock_hz):
    """Mirror mucom_fm's spelling: nearest table entry at the source
    block, with the residual carried as a detune in source fnum units."""
    normalized = fnum * clock_hz / REFERENCE_CLOCK_HZ
    at_block4 = normalized / 2 ** (block - 4) if block >= 4 \
        else normalized * 2 ** (4 - block)
    note_index = min(range(12), key=lambda i: abs(FNUM[i] - at_block4))
    spelled = FNUM[note_index] * 2 ** (block - 4) * REFERENCE_CLOCK_HZ / clock_hz \
        if block >= 4 else \
        FNUM[note_index] / 2 ** (4 - block) * REFERENCE_CLOCK_HZ / clock_hz
    return block + 1, NOTES[note_index], round(fnum - spelled)


def _voice_key(state):
    if state.algorithm is None or state.feedback is None:
        return None
    operators = []
    for op in state.operators:
        # SSG-EG is the only field whose unwritten state is a defined
        # power-on value (0 = disabled); everything else must be observed.
        values = (op.dt, op.mul, op.tl, op.ks, op.ar, op.am_enabled,
                  op.d1r, op.d2r, op.sl, op.rr, op.ssg_eg_raw or 0)
        if any(value is None for value in values):
            return None
        operators.append(values)
    return (state.algorithm, state.feedback, tuple(operators))


def _voice_lines(voice_id, key):
    algorithm, feedback, operators = key
    lines = [f'@N{voice_id}', f'{algorithm} , {feedback}']
    for index in (0, 2, 1, 3):  # register-bank order -> logical 1,2,3,4
        dt, mul, tl, ks, ar, am, d1r, d2r, sl, rr, ssg = operators[index]
        dt_signed = (dt & 3) * (-1 if dt & 4 else 1)
        lines.append(
            f' {ar:>2}, {d1r:>2}, {d2r:>2}, {rr:>2}, {sl:>2}, {tl:>3}, '
            f'{ks}, {mul:>2}, {dt_signed:>2}, {am}, {ssg or 0}')
    return lines


def _clocks(samples):
    return round(samples / SAMPLES_PER_CLOCK)


def _pieces(start_clock, end_clock):
    while end_clock - start_clock > MAX_PIECE_CLOCKS:
        yield start_clock, start_clock + MAX_PIECE_CLOCKS
        start_clock += MAX_PIECE_CLOCKS
    if end_clock > start_clock:
        yield start_clock, end_clock


def convert_vgm(input_path, outdir, *, dump_passes=True, diagnostics_outdir=None,
                timing_bound_ms=8.0, loops=True, song_loop=True,
                normalize_lengths=True, normalization_ms=8.0, fm_state_ms=1.0,
                pcm_only=False, pcm_policy='strict', force=False,
                opm_channel_policy='auto', pcm_profile='standard',
                software_volume_curves=True, legacy_loops=False,
                score_guided_loops=False, enhance_macros=True, **_ignored):
    from musical_hal.native_imports import analyze_source
    from musical_hal.native_imports.opn2_music import build_opn2_gates
    from musical_hal.native_imports.opna_music import (audit_fm_key_edges,
                                                       dump_gates)
    source, output = Path(input_path), Path(outdir)
    output.mkdir(parents=True, exist_ok=True)
    passes = Path(diagnostics_outdir) if diagnostics_outdir else output
    passes.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    manifest = output / (stem + '.mucom.json')
    report = dict(schema_version=2, target='mucomMD2vgm', status='analyzing',
                  source=str(source),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  source_passes=str(passes), diagnostics=[
                      'MD dialect per the mucomMD2vgm command memo: FM parts '
                      'A B C H I J, @N voice definitions.',
                      '@N operator rows are emitted in logical order '
                      '1,2,3,4 with signed DT; confirm at the first '
                      'mucomMD2vgm compile.',
                      'Gate timings are quantized to MUCOM clocks at '
                      'tempo 150 (two clocks per 60 Hz frame).'],
                  tracks={}, fm_projection=[], stage_timings={},
                  options=dict(force=bool(force)),
                  timing=dict(timer_b=TEMPO, tempo=TEMPO,
                              samples_per_clock=SAMPLES_PER_CLOCK))
    omissions = []

    def save():
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8')

    usage = inspect_source(source)
    report['source_usage'] = usage
    if 'opn2' not in usage['used_chips']:
        raise ConversionError('MD target requires a YM2612 (OPN2) source')
    blockers = [entry for entry in usage['unsupported_commands']
                if 'clock declaration' in entry['reason']
                or 'Second OPN2' in entry['reason']]
    if blockers:
        raise ConversionError(blockers[0]['reason'])
    others = [chip for chip in usage['used_chips'] if chip != 'opn2']
    if others:
        raise ConversionError('Unsupported source projection: '
                              + ', '.join(sorted(others)))

    raw = read_vgm_bytes(source)
    result = analyze_source(raw, passes, stem)
    if result['errors'].get('opn2'):
        raise ConversionError('OPN2 analysis failed: '
                              + result['errors']['opn2']['detail'])
    analysis = result['opn2']
    if analysis is None:
        raise ConversionError('OPN2 analysis unavailable')
    if 'dcsg' in result['active_chips']:
        if not force:
            raise ConversionError(
                'SN76489 parts arrive with the DCSG stage; '
                'use --force to omit them for now')
        omissions.append(dict(part='DEFG', source_chip='dcsg',
                              action='omit_source_engine',
                              reason='dcsg_stage_not_implemented'))
        report['force_projection'] = dict(enabled=True, omissions=omissions)
    clock_hz = analysis.events[0].clock_hz if analysis.events else 0
    gates = build_opn2_gates(analysis)
    audit_fm_key_edges(analysis, gates)
    dump_gates(gates, passes / (stem + '.opn2.music.csv'))
    loop_sample = result['loop'].get('loop_start_samples')
    loop_clock = (_clocks(loop_sample)
                  if song_loop and loop_sample is not None else None)

    voices = {}
    voice_order = []
    for channel_gates in gates.values():
        for gate in channel_gates:
            for control in gate.controls:
                key = _voice_key(control.state)
                if key is not None and key not in voices:
                    voices[key] = len(voice_order)
                    voice_order.append(key)

    lines = ['#mucom88 1.7', f'#title {stem}', '#composer vgm2mml (OPN2)']
    for voice_id, key in enumerate(voice_order):
        lines.extend(_voice_lines(voice_id, key))
    musical_note_units = 0
    for part in PARTS:
        channel_gates = gates.get(part, ())
        tokens = []
        if part == 'A':
            tokens.append(f't{TEMPO}')
        position = 0  # in clocks
        current = dict(voice=None, pan=None, octave=None, detune=None,
                       modulation=None)
        sounding_key_mask = None

        def rest_until(target_clock):
            nonlocal position
            for start, end in _pieces(position, target_clock):
                tokens.append(f'r{end - start}')
            position = max(position, target_clock)

        for gate in channel_gates:
            gate_start = _clocks(gate.start)
            gate_end = _clocks(gate.end)
            if gate_end <= gate_start:
                continue
            if loop_clock is not None and position < loop_clock < gate_start:
                rest_until(loop_clock)
                if part == 'A':
                    tokens.append('L')
            rest_until(gate_start)
            if loop_clock is not None and position == loop_clock and part == 'A':
                tokens.append('L')
            musical_note_units += 1
            for index, control in enumerate(gate.controls):
                control_start = max(gate_start, _clocks(control.start))
                control_end = min(gate_end, _clocks(control.end))
                if index == 0:
                    control_start = gate_start
                if index == len(gate.controls) - 1:
                    control_end = gate_end
                if control_end <= control_start:
                    continue
                state = control.state
                prefix = []
                key = _voice_key(state)
                if key is not None and current['voice'] != key:
                    prefix.append(f'@N{voices[key]}')
                    current['voice'] = key
                pan = ((state.left_enabled or 0) << 1) | (state.right_enabled or 0)
                if pan and current['pan'] != pan:
                    prefix.append(f'p{pan}')
                    current['pan'] = pan
                modulation = (state.lfo_enabled or 0, state.lfo_rate or 0,
                              state.pms or 0, state.ams or 0)
                if current['modulation'] is not None and modulation != current['modulation']:
                    if modulation[0]:
                        prefix.append(f'H{modulation[1]},{modulation[2]},{modulation[3]}')
                    else:
                        prefix.append(f'H{modulation[1]},{modulation[2]},{modulation[3]}')
                        prefix.append('y$22,0')
                current['modulation'] = modulation
                if state.fnum is None or state.block is None:
                    raise ConversionError(
                        f'FM {part}: gate at sample {gate.start} has no '
                        'observed pitch')
                octave, note, detune = _spell(state.fnum, state.block, clock_hz)
                if current['octave'] != octave:
                    prefix.append(f'o{octave}')
                    current['octave'] = octave
                if current['detune'] != detune:
                    prefix.append(f'D{detune}')
                    current['detune'] = detune
                if index > 0 and sounding_key_mask is not None \
                        and state.key_mask != sounding_key_mask:
                    key_channel = gate.channel if gate.channel < 3 \
                        else gate.channel + 1
                    prefix.append(f'y$28,{state.key_mask << 4 | key_channel}')
                sounding_key_mask = state.key_mask
                pieces = list(_pieces(control_start, control_end))
                for piece_index, (start, end) in enumerate(pieces):
                    token = ''.join(prefix) if piece_index == 0 else ''
                    token += f'{note}{end - start}'
                    tokens.append(token)
                    if piece_index < len(pieces) - 1 or index < len(gate.controls) - 1:
                        tokens[-1] += '&'
                    prefix = []
                position = control_end
                report['fm_projection'].append(dict(
                    part=part, source_start=control.start,
                    source_end=control.end,
                    source_segment_ids=control.segment_ids,
                    source_event_ids=control.event_ids,
                    voice_id=voices.get(key) if key is not None else None,
                    note=note, octave=octave, detune=detune))
            sounding_key_mask = None
        report['tracks'][part] = dict(gates=len(channel_gates))
        lines.append(f'{part} ' + ' '.join(tokens) if tokens else f'{part} r1')
    muc_path = output / (stem + '.muc')
    muc_path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('cp932'))
    report.update(status='converted', muc_path=str(muc_path),
                  musical_note_units=musical_note_units,
                  muc_sha256=hashlib.sha256(muc_path.read_bytes()).hexdigest())
    save()
    return report
