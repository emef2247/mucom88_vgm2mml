"""YM2203 (OPN) source -> MUCOMSX dialect generated under OPN assumptions.

The MUC text, DAT bank and part layout are the MUCOMSX (OPNA) target
conventions, but generation is restricted to what a YM2203 can sound:
FM parts A B C and SSG parts D E F, mono voices, no rhythm, no
ADPCM, no pan or LFO controls. The intended hardware is an OPN
cartridge decoded at the Makoto I/O addresses (#14-17h, e.g. the
PC-60msx55 in its Makoto-compatible setting) driven by MUCOMSX
itself; only the first register port pair exists on that board.

The board clocks the YM2203 at 4 MHz while MUCOMSX compiles pitch
for the 8 MHz Makoto frame, so FM states are projected at twice
their frame frequency: the MML spells one octave above the frame
pitch and the physical chip sounds the source pitch. The Timer-B
value is likewise selected for a 4 MHz master clock so the tempo
survives. The SSG needs no compensation (its effective clock is
2 MHz on both boards), so SSG tone segments keep the spelling the
native route uses. Whether MUCOMSX detects the OPN board and drives
its Timer B as assumed here is for the hardware trial; the compiler
is not run by this route.

SSG noise and hardware-envelope segments follow the native route's
staging: they stop the conversion unless --force turns them into
rests and records the omission.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from conversion_config import inspect_source
from vgm_io import read_vgm_bytes
from mucomsx_conversion import choose_timing, dump_rows, timed
from mucom_structure import limit_repeat_depth
from mucomsx_macros import compact_tracks
from mml_format import wrap_track
from source_loop_plan import SourceLoopPlan, expanded_tokens
from opn_muc import _shift_state, _ssg_note

BOARD_CLOCK_HZ = 4_000_000
FM_PARTS = 'ABC'
SSG_PARTS = 'DEF'
PARTS = FM_PARTS + SSG_PARTS


class ConversionError(ValueError):
    pass


class _ZeroBasedVoices(dict):
    """mucom_fm.project_state numbers voices from 1; MUCOMSX from 0."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value - 1)


def _board_state(state, clock_hz):
    """Re-express a state at twice its frame frequency, spelled well.

    Doubling fnum doubles the frequency project_state projects (the
    4 MHz board then sounds the source pitch under an 8 MHz compile).
    The detune-minimizing shift search is restricted to non-negative
    shifts so it cannot fall back to the un-doubled spelling family.
    """
    if state.fnum is None or state.block is None:
        return state
    return _shift_state(replace(state, fnum=state.fnum * 2), clock_hz,
                        shifts=(0, 1, 2, 3))


def convert_vgm(input_path, outdir, *, dump_passes=True, diagnostics_outdir=None,
                timing_bound_ms=8.0, loops=True, song_loop=True, macros=True,
                normalize_lengths=True, normalization_ms=8.0, fm_state_ms=1.0,
                force=False, software_volume_curves=True, legacy_loops=False,
                score_guided_loops=False, enhance_macros=True, **_ignored):
    from musical_hal.native_imports import analyze_source
    from musical_hal.native_imports.opn_music import build_opn_gates
    from musical_hal.native_imports.opna_music import (audit_fm_key_edges,
                                                       dump_gates,
                                                       semantic_state)
    from mucom_fm import project_state
    from opn_ssg import build_segments as build_ssg_segments
    source, output = Path(input_path), Path(outdir)
    output.mkdir(parents=True, exist_ok=True)
    passes = Path(diagnostics_outdir) if diagnostics_outdir else output
    passes.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    report_path = passes / (stem + '.mucomsx-opn.json')
    report = dict(status='analyzing', target='MUCOMSX v1.0-stable',
                  source=str(source),
                  options=dict(mapping='opn_on_mucomsx_opn_cartridge',
                               force=bool(force),
                               board_clock_hz=BOARD_CLOCK_HZ),
                  tracks={}, assessments=[], omissions=[],
                  source_evidence_unchanged=True,
                  diagnostics=[
                      'MUCOMSX (OPNA) dialect restricted to the OPN '
                      'subset: FM parts A B C, SSG parts D E F, mono '
                      'voices; no rhythm, ADPCM, pan or LFO output.',
                      'FM pitches are projected at twice the frame '
                      'frequency and the Timer-B value is selected '
                      f'for a {BOARD_CLOCK_HZ} Hz board clock, so an '
                      'OPN cartridge at the Makoto addresses sounds '
                      'the source pitch and tempo under MUCOMSX.',
                      'SSG tone spelling is uncompensated: the SSG '
                      'effective clock is 2 MHz on both boards.',
                      'Unverified on hardware: MUCOMSX detection of '
                      'an OPN board, its Timer-B drive from a '
                      'YM2203, and the t-value passthrough this '
                      'timing selection assumes.',
                      'SSG noise and hardware-envelope segments '
                      'require --force in this stage and are '
                      'recorded as omissions when forced.'])
    try:
        usage = inspect_source(source)
        report['source_usage'] = usage
        if 'opn' not in usage['used_chips']:
            raise ConversionError(
                'MUCOMSX-OPN route requires a YM2203 (OPN) source')
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
        gates = build_opn_gates(analysis)
        audit_fm_key_edges(analysis, gates)
        if normalize_lengths:
            from mucom_fm_state_normalization import filter_fm_states
            from fm_state_runs import fm_short_state_samples
            gates, short_states = filter_fm_states(
                gates, analysis,
                max_samples=fm_short_state_samples(fm_state_ms))
            report['fm_short_states'] = dict(
                threshold_ms=float(fm_state_ms),
                omitted=sum(row['action'] == 'omit_short_state'
                            for row in short_states),
                source_evidence_unchanged=True)
        dump_gates(gates, passes / (stem + '.opn.music.csv'))
        end = analysis.source_end_vgmticks
        loop = result['loop'].get('loop_start_samples') if song_loop else None
        if loop is not None and not 0 < loop < end:
            loop = None
        source_clock = analysis.events[0].clock_hz if analysis.events else 0
        ssg = build_ssg_segments(result['trace_paths']['opn'],
                                 end_vgmticks=end)
        ssg.dump(passes, stem)

        # Per-part note lists on the shared note model: gates and SSG
        # segments split at the loop point, rests filling the gaps;
        # control/segment boundaries ride in note.runs so
        # choose_timing protects them as timing boundaries.
        notes_by_part = {}
        for part in FM_PARTS:
            notes = []
            cursor = 0
            for gate in gates.get(part, ()):
                spans = [(gate.start, gate.end)]
                if loop is not None and gate.start < loop < gate.end:
                    spans = [(gate.start, loop), (loop, gate.end)]
                for span_index, (a, b) in enumerate(spans):
                    if a > cursor:
                        notes.append(SimpleNamespace(
                            start=cursor, length=a - cursor, rest=True,
                            runs=[], segment_indices=(),
                            fm_controls=(), continuation=False))
                    controls = tuple(
                        (max(a, c.start), min(b, c.end), c.state,
                         c.segment_ids)
                        for c in gate.controls
                        if max(a, c.start) < min(b, c.end))
                    notes.append(SimpleNamespace(
                        start=a, length=b - a, rest=False,
                        runs=[(0, stop - start)
                              for start, stop, _, _ in controls],
                        segment_indices=gate.source_segment_ids,
                        fm_controls=controls,
                        continuation=span_index > 0,
                        channel=gate.channel))
                    cursor = b
            if cursor < end:
                notes.append(SimpleNamespace(
                    start=cursor, length=end - cursor, rest=True, runs=[],
                    segment_indices=(), fm_controls=(), continuation=False))
            notes_by_part[part] = notes

        ssg_blockers = []
        for channel, part in enumerate(SSG_PARTS):
            notes = []
            cursor = 0
            for segment in ssg.segments[channel]:
                a, b = segment.vgmticks, segment.vgmticks_end
                if b <= a:
                    continue
                audible = segment.mode != 0 and bool(
                    segment.amplitude_register)
                needs_unmodeled = audible and (segment.mode != 1
                                               or segment.envelope_enabled)
                reason = None
                if needs_unmodeled:
                    reason = ('ssg_noise_not_rendered'
                              if segment.mode != 1
                              else 'ssg_hardware_envelope_not_rendered')
                    if not force:
                        ssg_blockers.append(
                            f'SSG {part}: segment at sample {a} needs '
                            + reason.replace('_', ' '))
                tone = audible and not needs_unmodeled \
                    and bool(segment.tone_period)
                spans = [(a, b)]
                if loop is not None and a < loop < b:
                    spans = [(a, loop), (loop, b)]
                for x, y in spans:
                    if x > cursor:
                        notes.append(SimpleNamespace(
                            start=cursor, length=x - cursor, rest=True,
                            runs=[], segment_indices=(),
                            ssg=None, continuation=False))
                    if tone:
                        note_name, octave = _ssg_note(segment)
                        notes.append(SimpleNamespace(
                            start=x, length=y - x, rest=False,
                            runs=[(0, y - x)], segment_indices=(),
                            ssg=dict(volume=segment.volume,
                                     note=note_name, octave=octave),
                            continuation=False))
                    else:
                        if reason is not None:
                            report['omissions'].append(dict(
                                part=part, source_start=x, source_end=y,
                                action='omit_segment', reason=reason))
                        notes.append(SimpleNamespace(
                            start=x, length=y - x, rest=True,
                            runs=[], segment_indices=(),
                            ssg=None, continuation=False))
                    cursor = y
            if cursor < end:
                notes.append(SimpleNamespace(
                    start=cursor, length=end - cursor, rest=True, runs=[],
                    segment_indices=(), ssg=None, continuation=False))
            notes_by_part[part] = notes
        if ssg_blockers:
            raise ConversionError(ssg_blockers[0] + '; use --force to '
                                  'omit those segments for now')
        ticks, timing = choose_timing(
            notes_by_part, end, loop,
            normalization_ms if normalize_lengths else timing_bound_ms,
            clock_hz=BOARD_CLOCK_HZ)
        report['timing'] = timing

        voices = _ZeroBasedVoices()
        pitch_rows = []
        units_rows = []
        rendered = {}
        plain = {}
        structure_reports = {}
        musical_note_units = 0
        for part in PARTS:
            rows = notes_by_part[part]
            commands = []
            keys = []
            previous_state = None
            current_volume = None
            current_octave = None
            pending = []
            for note in rows:
                duration = ticks[note.start + note.length] - ticks[note.start]
                if note.rest:
                    text = timed('r', duration)
                    command = (' '.join(pending) + ' ' + text).strip() \
                        if pending else text
                    pending = []
                    commands.append(command)
                    keys.append(('rest', note.length))
                    previous_state = None
                    continue
                musical_note_units += 1
                if part in SSG_PARTS:
                    words = list(pending)
                    pending = []
                    payload = note.ssg
                    if payload['volume'] != current_volume:
                        words.append(f"v{payload['volume']}")
                        current_volume = payload['volume']
                    if payload['octave'] != current_octave:
                        words.append(f"o{payload['octave']}")
                        current_octave = payload['octave']
                    text = timed(payload['note'], duration)
                    if text:
                        words.append(text)
                    command = ' '.join(words)
                    commands.append(command)
                    keys.append(('ssg', note.length,
                                 (payload['volume'], payload['note'],
                                  payload['octave'])))
                    units_rows.append(dict(
                        part=part, source_start=note.start,
                        source_end=note.start + note.length, rest=False,
                        source_segment_indices='[]', command=command))
                    continue
                words = list(pending)
                pending = []
                key_controls = []
                failed = False
                for index, (a, b, state, segment_ids) in \
                        enumerate(note.fm_controls):
                    initial = index == 0 and not note.continuation
                    board = _board_state(state, source_clock)
                    try:
                        projection = project_state(
                            board, source_clock, voices, initial=initial,
                            previous_state=None if initial else
                            (previous_state if previous_state is not None
                             else _board_state(
                                 note.fm_controls[index - 1][2],
                                 source_clock)),
                            target_channel=note.channel, mono=True)
                    except ValueError as error:
                        if not force:
                            raise ConversionError(
                                f'FM {part}: note at sample '
                                f'{note.start}: {error}') from error
                        report['omissions'].append(dict(
                            part=part, source_start=note.start,
                            source_end=note.start + note.length,
                            action='omit_note',
                            reason=f'fm_projection_failed: {error}'))
                        failed = True
                        break
                    if len(voices) > 32:
                        raise ConversionError(
                            'MUCOMSX used-FM-voice capacity 32 exceeded')
                    span = ticks[b] - ticks[a]
                    key_controls.append((b - a, semantic_state(state)))
                    pitch_rows.append(dict(
                        part=part, source_start=a,
                        source_fnum=state.fnum, source_block=state.block,
                        source_clock_hz=source_clock,
                        projected_frame_fnum=projection.target_fnum,
                        projected_frame_block=projection.target_block,
                        frequency_error_hz=projection.frequency_error_hz))
                    if span <= 0:
                        pending.extend(projection.commands)
                        previous_state = board
                        continue
                    words.extend(projection.commands)
                    text = timed(projection.note, span,
                                 continuation=index > 0 or note.continuation)
                    if text:
                        words.append(text)
                    previous_state = board
                if failed:
                    text = timed('r', duration)
                    command = (' '.join(pending) + ' ' + text).strip() \
                        if pending else text
                    pending = []
                    commands.append(command)
                    keys.append(('rest', note.length))
                    previous_state = None
                    musical_note_units -= 1
                    continue
                command = ' '.join(words)
                commands.append(command)
                keys.append(('fm', note.length, tuple(key_controls)))
                units_rows.append(dict(
                    part=part, source_start=note.start,
                    source_end=note.start + note.length, rest=False,
                    source_segment_indices=json.dumps(note.segment_indices),
                    command=command))
            split = next((i for i, n in enumerate(rows)
                          if loop is not None and n.start == loop), len(rows))
            chunks = []
            plain_chunks = []
            details = []
            for label, a, b in [('intro', 0, split), ('loop', split, len(rows))]:
                if a == b:
                    continue
                plan = SourceLoopPlan.build(
                    keys[a:b],
                    strategy='retained' if legacy_loops else 'structural')
                text, folds = plan.render(commands[a:b]) if loops \
                    else (' '.join(commands[a:b]), [])
                text, depths = limit_repeat_depth(text, maximum=16)
                if expanded_tokens(text) != expanded_tokens(
                        ' '.join(commands[a:b])):
                    raise AssertionError('Structural projection changed commands')
                plan.dump(passes / f'{stem}.{part}.{label}.structure.csv',
                          [n.segment_indices for n in rows[a:b]], folds)
                if label == 'loop':
                    chunks.append('L')
                    plain_chunks.append('L')
                chunks.extend([text] if '[' in text else commands[a:b])
                plain_chunks.extend(commands[a:b])
                details.append(dict(region=label, source_units=b - a,
                                    folds=folds, depth_expansions=depths))
            rendered[part] = chunks
            plain[part] = plain_chunks
            structure_reports[part] = details
            report['tracks'][part] = dict(
                chip='opn', source_channel=PARTS.index(part),
                musical_units=len(rows),
                plain_characters=len(' '.join(plain_chunks)),
                structured_characters=len(' '.join(chunks)))
        definitions, compacted, macro_report = compact_tracks(
            rendered, limit=32 if macros else 0,
            enhance_macros=enhance_macros)
        report['macros'] = macro_report
        header = [f'#title {stem}', '#driver mucomDotNET']
        bank_path = None
        if voices:
            bank = bytearray(8192)
            for record, index in voices.items():
                bank[index * 32:(index + 1) * 32] = record
            bank_path = output / (stem + '.dat')
            header.append('#voice ' + bank_path.name)
            report['voice_bank'] = str(bank_path)
        prefix = 'C128 t' + str(timing['timer_b']) + ' q0 '
        lines = header + definitions
        for part, chunks in compacted.items():
            lines.extend(wrap_track(part, prefix + ' '.join(chunks)))
        encoded = ('\r\n'.join(lines) + '\r\n').encode('cp932')
        dump_rows(passes / (stem + '.mucomsxopn.units.csv'), units_rows)
        dump_rows(passes / (stem + '.mucomsxopn.pitch.csv'), pitch_rows)
        (passes / (stem + '.mucomsxopn.structure.json')).write_text(
            json.dumps(structure_reports, indent=2) + '\n', encoding='utf-8')
        (passes / (stem + '.mucomsxopn.macros.json')).write_text(
            json.dumps(macro_report, indent=2) + '\n', encoding='utf-8')
        if len(encoded) > 65536:
            candidate = passes / (stem + '.mucomsxopn.rejected_muc.txt')
            candidate.write_bytes(encoded)
            report['rejected_candidate'] = str(candidate)
            raise ConversionError(
                f'MUCOMSX MUC input capacity exceeded: {len(encoded)}/65536 bytes')
        if any(len(line.encode('cp932')) > 1023 for line in lines):
            raise ConversionError('MUCOMSX physical line exceeds 1023 bytes')
        muc_path = output / (stem + '.muc')
        if bank_path is not None:
            bank_path.write_bytes(bank)
        muc_path.write_bytes(encoded)
        report.update(status='generated', muc_path=str(muc_path),
                      muc_bytes=len(encoded),
                      muc_sha256=hashlib.sha256(encoded).hexdigest(),
                      musical_note_units=musical_note_units,
                      source_end_sample=end,
                      target_loop_source_sample=loop,
                      compiler_status='not_run',
                      validation_scope='Source interpretation, OPN '
                      'projection under MUCOMSX dialect conventions, '
                      'structure expansion and MUC generation; no '
                      'listening equivalence claim')
        return report
    except ConversionError:
        report.update(status='conversion_failed')
        raise
    except Exception as error:
        report.update(status='conversion_failed', detail=str(error))
        raise ConversionError(str(error)) from error
    finally:
        report_path.write_text(json.dumps(report, indent=2, default=str) + '\n',
                               encoding='utf-8')
