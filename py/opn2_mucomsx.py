"""YM2612 (OPN2) source -> OPNA mapping in the MUCOMSX dialect.

The musical evidence is the shared OPN2 analysis and gate extraction;
the expression reuses the OPNA projection (mucom_fm.project_state:
clock-projected pitch spelling and 32-byte DAT voices) and the
MUCOMSX target conventions (choose_timing Timer-B selection, % tick
lengths, structural loop plans, macro compaction, DAT bank file).
SN76489 parts and the DAC channel arrive with their own stages; the
DAC's intended home here is OPNA ADPCM-B.
"""
from dataclasses import asdict
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

PARTS = 'ABCHIJ'


class ConversionError(ValueError):
    pass


class _ZeroBasedVoices(dict):
    """mucom_fm.project_state numbers voices from 1; MUCOMSX from 0."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value - 1)


def convert_vgm(input_path, outdir, *, dump_passes=True, diagnostics_outdir=None,
                timing_bound_ms=8.0, loops=True, song_loop=True, macros=True,
                normalize_lengths=True, normalization_ms=8.0, fm_state_ms=1.0,
                force=False, software_volume_curves=True, legacy_loops=False,
                score_guided_loops=False, enhance_macros=True,
                opll_rhythm='native', psg_enhance_cents=None,
                scc_output='native', opll_output='native', **_ignored):
    from musical_hal.native_imports import analyze_source
    from musical_hal.native_imports.opn2_music import build_opn2_gates
    from musical_hal.native_imports.opna_music import (audit_fm_key_edges,
                                                       dump_gates,
                                                       semantic_state)
    from mucom_fm import project_state
    if opll_rhythm != 'native' or psg_enhance_cents is not None \
            or scc_output != 'native' or opll_output != 'native':
        raise ConversionError(
            'MSX source options do not apply to the OPN2 mapping')
    source, output = Path(input_path), Path(outdir)
    output.mkdir(parents=True, exist_ok=True)
    passes = Path(diagnostics_outdir) if diagnostics_outdir else output
    passes.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    report_path = passes / (stem + '.mucomsx.json')
    report = dict(status='analyzing', target='MUCOMSX v1.0-stable',
                  source=str(source),
                  options=dict(mapping='opn2_to_opna', force=bool(force)),
                  tracks={}, assessments=[], omissions=[],
                  source_evidence_unchanged=True,
                  diagnostics=[
                      'OPN2 FM projected to OPNA via mucom_fm (clock '
                      '7.67 MHz -> 7.99 MHz pitch/voice projection).',
                      'Whole-gate output normalization is not applied '
                      'in this first mapping increment; FM short-state '
                      'filtering is.'])
    try:
        usage = inspect_source(source)
        report['source_usage'] = usage
        if 'opn2' not in usage['used_chips']:
            raise ConversionError('OPN2 mapping requires a YM2612 source')
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
                    'SN76489 parts are not mapped yet; '
                    'use --force to omit them for now')
            report['omissions'].append(dict(
                chip='dcsg', channel='', source_start=0,
                source_end=analysis.source_end_vgmticks,
                segment_indices='[]', action='omit_source_engine',
                reason='dcsg_to_ssg_mapping_not_implemented'))
        gates = build_opn2_gates(analysis)
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
        dump_gates(gates, passes / (stem + '.opn2.music.csv'))
        end = analysis.source_end_vgmticks
        loop = result['loop'].get('loop_start_samples') if song_loop else None
        if loop is not None and not 0 < loop < end:
            loop = None
        source_clock = analysis.events[0].clock_hz if analysis.events else 0

        # Per-part note lists: gates split at the loop point, rests
        # filling the gaps; control boundaries ride in note.runs so
        # choose_timing protects them as timing boundaries.
        notes_by_part = {}
        for part in PARTS:
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
        ticks, timing = choose_timing(
            notes_by_part, end, loop,
            normalization_ms if normalize_lengths else timing_bound_ms)
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
            pending = []
            for note in rows:
                if note.rest:
                    text = timed('r', ticks[note.start + note.length]
                                 - ticks[note.start])
                    command = (' '.join(pending) + ' ' + text).strip() \
                        if pending else text
                    pending = []
                    commands.append(command)
                    keys.append(('rest', note.length))
                    previous_state = None
                    continue
                musical_note_units += 1
                words = list(pending)
                pending = []
                key_controls = []
                for index, (a, b, state, segment_ids) in \
                        enumerate(note.fm_controls):
                    initial = index == 0 and not note.continuation
                    try:
                        projection = project_state(
                            state, source_clock, voices, initial=initial,
                            previous_state=None if initial else
                            (previous_state if previous_state is not None
                             else note.fm_controls[index - 1][2]),
                            target_channel=note.channel)
                    except ValueError as error:
                        raise ConversionError(f'FM {part}: {error}') from error
                    if len(voices) > 32:
                        raise ConversionError(
                            'MUCOMSX used-FM-voice capacity 32 exceeded')
                    duration = ticks[b] - ticks[a]
                    key_controls.append((b - a, semantic_state(state)))
                    pitch_rows.append(dict(
                        part=part, source_start=a,
                        source_fnum=state.fnum, source_block=state.block,
                        source_clock_hz=source_clock,
                        projected_opna_fnum=projection.target_fnum,
                        projected_opna_block=projection.target_block,
                        frequency_error_hz=projection.frequency_error_hz))
                    if duration <= 0:
                        pending.extend(projection.commands)
                        previous_state = state
                        continue
                    words.extend(projection.commands)
                    text = timed(projection.note, duration,
                                 continuation=index > 0 or note.continuation)
                    if text:
                        words.append(text)
                    previous_state = state
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
                chip='opn2', source_channel=PARTS.index(part),
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
        dump_rows(passes / (stem + '.mucomsx.units.csv'), units_rows)
        dump_rows(passes / (stem + '.mucomsx.pitch.csv'), pitch_rows)
        (passes / (stem + '.mucomsx.structure.json')).write_text(
            json.dumps(structure_reports, indent=2) + '\n', encoding='utf-8')
        (passes / (stem + '.mucomsx.macros.json')).write_text(
            json.dumps(macro_report, indent=2) + '\n', encoding='utf-8')
        if len(encoded) > 65536:
            candidate = passes / (stem + '.mucomsx.rejected_muc.txt')
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
                      musical_note_units=musical_note_units,
                      source_end_sample=end,
                      target_loop_source_sample=loop,
                      compiler_status='not_run',
                      validation_scope='Source interpretation, OPNA '
                      'projection, structure expansion and MUC generation; '
                      'no listening equivalence claim')
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
