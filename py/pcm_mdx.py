"""MDX-only PCM binding and projection of backend-neutral source evidence."""
import csv
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
import struct
import re
import subprocess
import sys

from mdx_duration import duration_spelling
from opm_mdx import mdx_tick, projected_samples
from pcm_assessment import PcmAssessment, ProjectionError, PAN_EVIDENCE
from pcm_stream import REFERENCE_REVISION

# Standard MXDRV F0..F4 nibble rates, not incoming VGM byte-write rates.
RATES = tuple(Fraction(clock, divider) for clock, divider in
              ((4000000, 1024), (4000000, 768), (8000000, 1024),
               (8000000, 768), (8000000, 512)))
PCM_PAN = (3, 1, 2, 0)  # MSM6258 disable bits -> MDX ADPCM pan (FM uses opposite L/R).


@dataclass(frozen=True)
class PcmBinding:
    sample_id: int
    bank: int
    slot: int
    file: str


@dataclass(frozen=True)
class PcmMdxUnit:
    track: str
    start_tick: int
    end_tick: int
    source_start_vgmticks: int
    source_end_vgmticks: int
    kind: str
    command: str
    source_event_ids: tuple
    source_segment_ids: tuple
    trajectory: tuple
    source_pcm_playback_ids: tuple = ()
    voice_id: int | None = None
    target_note: str = ''
    fallback_reason: str = ''
    unlooped_command: str = ''

    @property
    def key(self):
        return (self.kind, self.end_tick - self.start_tick, self.target_note,
                tuple((time, reg, value) for time, reg, value, _ in self.trajectory),
                self.command)


@dataclass(frozen=True)
class PcmMdxProjection:
    bindings: tuple[PcmBinding, ...]
    units: tuple[PcmMdxUnit, ...]
    rows: tuple[dict, ...]
    pdx_name: str
    sample_multiplier: int
    clock_rows: tuple[dict, ...] = ()
    assessment: PcmAssessment | None = None
    typed_commands: tuple[dict, ...] = ()
    omitted_playbacks: tuple[dict, ...] = ()
    source_origin: str = 'native_msm6258'
    force_capacity: object = None

    @property
    def boundary_times(self):
        return tuple(time for row in self.rows
                     for time in (row['source_start_vgmticks'], row['source_end_vgmticks']))

    def summary(self):
        projected = self.source_origin != 'native_msm6258'
        return dict(pcm_force_capacity=self.force_capacity.summary() if self.force_capacity else None,
                    pcm_sample_count=len(self.bindings),
                    pcm_source_origin=self.source_origin,
                    pcm_playback_count=sum(u.kind == 'pcm_note' for u in self.units),
                    pcm_pdx_file=self.pdx_name if self.bindings else None,
                    pcm_target='MDX PCM4/8 enabled; single P track, one bank, 96 slots',
                    pcm_track_count=16, pcm_mode_command='E8',
                    pcm_inactive_tracks=list('QRSTUVW'),
                    pcm_sample_bytes_preserved=True,
                    pcm_omitted_playback_count=len(self.omitted_playbacks),
                    pcm_omitted_duration_samples=sum(p['duration_samples'] for p in self.omitted_playbacks),
                    pcm_intended_omissions=list(self.omitted_playbacks),
                    pcm_byte_preservation_scope=(
                        'transcoded MSM6258 target bytes stored unchanged in PDX; '
                        'original source memory/encoded bytes retained separately in native source IR'
                        if projected else 'encoded playback samples stored in PDX; '
                        'all source supply operations are retained in source IR'),
                    pcm_consumption_scope=(
                        'projected independent PDX notes; no observed MSM6258 byte supply'
                        if projected else 'direct or profile-derived byte supply; '
                        'decoder consumption not measured'),
                    pcm_max_abs_timing_error_samples=max(
                        [abs(row[field]) for row in self.rows
                         for field in ('start_error_samples', 'end_error_samples')]
                        + [abs(row['error_samples']) for row in self.clock_rows], default=0))

    def dump(self, outdir, stem):
        out = Path(outdir)
        if self.force_capacity is not None:
            self.force_capacity.dump(out, stem)
        for suffix, rows, fields in (
            ('bindings', [asdict(b) for b in self.bindings], tuple(PcmBinding.__dataclass_fields__)),
            ('projection', self.rows, tuple(self.rows[0]) if self.rows else ('playback_id',)),
            ('clock', self.clock_rows, tuple(self.clock_rows[0]) if self.clock_rows else ('source_event_id',)),
            ('target_commands', self.typed_commands, tuple(self.typed_commands[0]) if self.typed_commands else ('kind',)),
            ('omitted_playbacks', self.omitted_playbacks,
             tuple(self.omitted_playbacks[0]) if self.omitted_playbacks else ('playback_id',)),
        ):
            with (out / f'{stem}.pcm_{suffix}.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)
        self.write_manifest(out, stem)

    def write_manifest(self, outdir, stem):
        folder = Path(outdir) / (stem + '.pcm')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'target.tsv'
        with path.open('w', newline='', encoding='utf-8') as stream:
            stream.write('kind\tvalue\tticks\n')
            stream.write(f'pdx_name\t{self.pdx_name}\t\n')
            stream.write(f'tempo\t{256-self.sample_multiplier}\t\n')
            end_tick = max((u.end_tick for u in self.units), default=0)
            stream.write(f'end_tick\t{end_tick}\t\n')
            for command in self.typed_commands:
                stream.write(f"{command['kind']}\t{command['value']}\t{command['ticks']}\n")
        return path


def boundaries(analysis):
    return tuple(sorted({analysis.source_end_vgmticks}
                        | {p.start_vgmticks for p in analysis.playbacks}
                        | {p.end_vgmticks for p in analysis.playbacks}
                        | {c.vgmticks for c in analysis.controls}))


def project(analysis, *, stem, sample_multiplier=1, policy='strict', omit_playback_ids=(), normalization_ms=8,
            tick_mapper=None, song_loop_start=None, force=False):
    """Bind unchanged samples; omit nominated notes within the source-time policy."""
    from conversion_config import normalization_samples
    omission_bound = normalization_samples(normalization_ms)
    target_tick = tick_mapper or (lambda t: mdx_tick(t, sample_multiplier))
    assessment = PcmAssessment(policy)
    for item in getattr(analysis, 'target_evidence', ()):
        assessment.add(**item)
    def reject(detail, *, code='projection_undefined', status='unverified', criterion='eligibility'):
        assessment.add(criterion, status, code, detail,
                       cause='target_constraint' if status == 'lossy' else '')
        assessment.block(detail)
        raise ProjectionError(detail, assessment)
    if any(c in stem for c in ('"', '/', '\\', '\r', '\n', '\x00')):
        reject('Input filename cannot form a safe MDX PCM filename', status='fail', code='invalid_filename')
    if analysis.issues:
        for issue in analysis.issues:
            invalid = issue.code in ('absent_chip_declaration', 'invalid_loop_address',
                                      'reserved_options', 'reserved_pan_bits', 'reserved_divider_bits')
            assessment.add('source_evidence', 'fail' if invalid else 'unverified',
                           issue.code, issue.detail, cause='invalid_source' if invalid else 'source_unresolved',
                           source_event_id=issue.source_event_id, start_vgmticks=issue.vgmticks)
        detail = 'PCM source is not representable: ' + '; '.join(
            dict.fromkeys(f'{issue.code}: {issue.detail}' for issue in analysis.issues))
        assessment.block(detail)
    if analysis.options & 8:
        assessment.add('sample', 'lossy', 'output_precision',
                       'Standard X68000 MDX PCM requires 10-bit output; 12-bit source output is unsupported',
                       cause='target_constraint')
        assessment.block('Standard X68000 MDX PCM requires 10-bit output')
    if len(analysis.samples) > 96 and not force:
        reject('Initial standard PDX target supports at most 96 distinct samples', status='lossy', code='slot_capacity')
    for sample in analysis.samples:
        if sample.codec != 'msm6258-adpcm4-low-first':
            assessment.add('sample', 'lossy', 'sample_codec',
                           'Standard PDX target requires 4-bit low-nibble-first MSM6258 ADPCM',
                           cause='target_constraint', source_value=sample.codec)
            assessment.block('Standard PDX target requires 4-bit low-nibble-first MSM6258 ADPCM')
        if not sample.encoded_bytes or len(sample.encoded_bytes) > 0xffffff:
            reject('PDX sample must contain 1..16777215 encoded bytes', status='fail', code='invalid_sample_length')
        if len(sample.encoded_bytes) > 65535 and not force:
            assessment.add('sample', 'unverified', 'sample_length_exceeds_projection_scope',
                           'Samples above 65535 bytes are outside the verified projection scope; '
                           'extended-mode playback has not been validated and no truncation fallback is defined',
                           cause='implementation_scope', source_value=len(sample.encoded_bytes),
                           evidence='Earlier PCM1 low-word limit is not an extended-mode limit')
            assessment.block('Sample length exceeds verified 65535-byte projection scope; no defined fallback')
    if assessment.block_reasons:
        raise ProjectionError('; '.join(assessment.block_reasons), assessment)
    capacity = None
    omit_ids = set(omit_playback_ids)
    if force:
        from pcm_force_capacity import select_capacity
        capacity = select_capacity(analysis, omit_playback_ids=omit_ids, normalization_ms=normalization_ms)
    capacity_omit_ids = capacity.omitted_playback_ids if capacity is not None else frozenset()
    binding_samples = capacity.analysis.samples if capacity is not None else analysis.samples
    if analysis.source_loop_vgmticks is not None and song_loop_start is None:
        assessment.add('loop', 'lossy', 'song_loop_not_emitted',
                       'MDX projection currently emits one finite source pass; the VGM song loop is lost',
                       cause='target_constraint', start_vgmticks=analysis.source_loop_vgmticks,
                       fallback='finite_source_pass', source_value=analysis.source_loop_vgmticks,
                       projected_value=None)
    for observation in analysis.observations:
        if observation.code == 'stream_supply_while_stopped':
            assessment.add('delivery', 'lossy', 'stopped_stream_supply_not_projected',
                           'Supplies during chip STOP are retained in source IR but omitted from PDX playback',
                           cause='target_constraint', source_event_id=observation.source_event_id,
                           start_vgmticks=observation.vgmticks,
                           fallback='omit_stopped_supplies_under_libvgm_reset_profile',
                           evidence=f'libvgm {REFERENCE_REVISION} okim6258.c: '
                                    'stopped update is silent; stop-to-play reinitializes FIFO')
            assessment.add('buffer', 'unverified', 'native_buffer_reset_unconfirmed',
                           'Stopped-byte carryover is resolved only under the libvgm reset profile; '
                           'native buffered-data behavior is unverified', scope='runtime_validation')
    bindings = tuple(PcmBinding(s.sample_id, 0, i, f'samples/{s.sample_id:03}.adpcm')
                     for i, s in enumerate(binding_samples))
    by_sample = {b.sample_id: b for b in bindings}
    source_sample_ids = {s.sample_id for s in analysis.samples}
    by_event = {c.source_event_id: c for c in analysis.controls}
    units, rows, commands, omitted = [], [], [], []
    cursor = source_cursor = 0
    previous_source_end = 0
    loop_tick = target_tick(song_loop_start) if song_loop_start is not None else None
    loop_emitted = False
    if loop_tick is not None and not 0 <= loop_tick < target_tick(analysis.source_end_vgmticks):
        reject('PCM song loop requires a positive shared loop period', code='invalid_song_loop', status='fail')

    def emit(kind, value='', ticks='', start_tick=0, playback_id=None, source_event_id=None):
        row = dict(kind=kind, value=value, ticks=ticks, start_tick=start_tick,
                   playback_id=playback_id, source_event_id=source_event_id)
        commands.append(row)
        return row

    def render(sequence):
        tokens = []
        held = False
        for command in sequence:
            kind, value, ticks = command['kind'], command['value'], command['ticks']
            if kind in ('note', 'rest'):
                tokens.append((f'n{value},' if kind == 'note' else 'r') + duration_spelling(ticks))
                if held:
                    tokens.append('&')
                    held = False
            elif kind == 'hold':
                # MDX F7 precedes its note; readable MML spells the hold after it.
                held = True
            elif kind == 'song_loop':
                tokens.append('L')
            elif kind != 'end':
                tokens.append({'bank': '@', 'frequency': 'F', 'pan': 'p', 'gate': 'q',
                               'volume': '@v'}[kind] + str(127 if kind == 'volume' else value))
        return ' '.join(tokens)

    def duration_commands(kind, start, end, *, slot='', playback_id=None, source_event_id=None):
        sequence = []
        position = start
        while position < end:
            count = min(end - position, 256 if kind == 'note' else 128)
            if kind == 'note' and position + count < end:
                sequence.append(emit('hold', start_tick=position, playback_id=playback_id,
                                     source_event_id=source_event_id))
            sequence.append(emit(kind, slot, count, position, playback_id, source_event_id))
            position += count
        return sequence

    def unit(start, end, kind, text, source_start, source_end, playback_ids=(),
             event_ids=(), trajectory=(), note=''):
        return PcmMdxUnit('P', start, end, source_start, source_end, kind, text,
                          tuple(event_ids), (), tuple(trajectory), tuple(playback_ids),
                          target_note=note, unlooped_command=text)

    def song_loop_marker():
        nonlocal loop_emitted
        emit('song_loop', start_tick=loop_tick)
        units.append(unit(loop_tick, loop_tick, 'song_loop', 'L',
                          song_loop_start, song_loop_start))
        loop_emitted = True

    def rest_units(start, end, source_start, source_end):
        if loop_tick is not None and not loop_emitted and start <= loop_tick <= end:
            if start < loop_tick:
                units.append(unit(start, loop_tick, 'pcm_rest',
                                  render(duration_commands('rest', start, loop_tick)),
                                  source_start, song_loop_start))
            song_loop_marker()
            start, source_start = loop_tick, song_loop_start
        if start < end:
            units.append(unit(start, end, 'pcm_rest', render(duration_commands('rest', start, end)),
                              source_start, source_end))

    for p in analysis.playbacks:
        schedule_fallback = (set(p.issues) == {'irregular_byte_supply'}
                             and p.decoder_reset_known and p.pan is not None
                             and p.stream_transfer_end - p.stream_transfer_start == p.supplied_bytes
                             and p.supplied_bytes > 0 and not analysis.issues)
        if (not p.independently_playable and not schedule_fallback) or p.sample_id not in source_sample_ids:
            for reason in p.issues:
                known = reason in ('rate_change_during_playback', 'unsupported_adpcm3')
                assessment.add('playback', 'lossy' if known else 'unverified', reason,
                               f'PCM playback {p.playback_id}: {reason}',
                               cause='target_constraint' if known else 'source_unresolved',
                               playback_id=p.playback_id, start_vgmticks=p.start_vgmticks,
                               end_vgmticks=p.end_vgmticks)
            reject(f'PCM playback {p.playback_id} cannot begin an independent PDX note: '
                   + ', '.join(p.issues), code='independent_sample_unknown')
        if schedule_fallback:
            assessment.add('delivery', 'lossy', 'byte_supply_schedule_not_preserved',
                           'Timestamped stream byte writes are projected as continuous PDX delivery; '
                           'a sample ends at byte exhaustion even if source PLAY remains asserted',
                           cause='target_constraint', playback_id=p.playback_id,
                           start_vgmticks=p.start_vgmticks, end_vgmticks=p.end_vgmticks,
                           source_value=dict(max_cadence_error_vgmticks=p.delivery_max_error_vgmticks,
                                             unsupplied_play_tail_vgmticks=p.unfed_tail_vgmticks),
                           fallback='continuous_pdx_delivery')
        try:
            frequency = RATES.index(p.rate_hz)
        except ValueError:
            reject(f'PCM rate {p.rate_num}/{p.rate_den} Hz has no exact standard MDX F0..F4 setting',
                   status='lossy', code='unsupported_rate', criterion='rate')
        if p.end_vgmticks <= p.start_vgmticks:
            reject(f'PCM playback {p.playback_id} has no positive source interval',
                   status='fail', code='invalid_playback_length')
        if p.start_vgmticks < previous_source_end:
            reject('Multiple overlapping physical PCM playbacks are unsupported', code='overlap')
        previous_source_end = p.end_vgmticks
        if any(event_id not in by_event for event_id in p.control_event_ids):
            reject(f'PCM playback {p.playback_id} references missing source controls',
                   status='fail', code='missing_playback_control')
        if p.playback_id in capacity_omit_ids:
            continue
        if song_loop_start is not None and (
                p.start_vgmticks < song_loop_start < p.end_vgmticks
                or p.end_vgmticks == analysis.source_end_vgmticks and p.termination == 'source_end'):
            reject('PCM playback continues across the song-loop boundary; independent PDX restart is undefined',
                   code='song_loop_pcm_continuation', criterion='loop')
        changed_pan = []
        current_pan = p.pan
        for event_id in p.control_event_ids:
            control = by_event[event_id]
            if control.register == 2 and control.pan != current_pan:
                changed_pan.append(control)
                current_pan = control.pan
        changed_pan = [c for c in changed_pan if c.vgmticks < p.end_vgmticks]
        for index, c in enumerate(changed_pan):
            affected_end = changed_pan[index + 1].vgmticks if index + 1 < len(changed_pan) else p.end_vgmticks
            if c.pan != p.pan and affected_end > c.vgmticks:
                assessment.add('pan', 'lossy', 'held_pan_latched',
                               'Held PCM pan change is lost; source mute/audibility may differ',
                               cause='projection_constraint', playback_id=p.playback_id,
                               source_event_id=c.source_event_id, start_vgmticks=c.vgmticks,
                               end_vgmticks=affected_end, source_value=c.pan, projected_value=p.pan,
                               fallback='hold_start_pan_until_next_attack', evidence=PAN_EVIDENCE)
        duration = p.end_vgmticks - p.start_vgmticks
        if p.playback_id in omit_ids and duration <= omission_bound:
            omitted.append(dict(playback_id=p.playback_id, sample_id=p.sample_id,
                                source_start_vgmticks=p.start_vgmticks,
                                source_end_vgmticks=p.end_vgmticks, duration_samples=duration,
                                reason=f'intentional output omission within {normalization_ms:g} ms',
                                source_first_event_id=p.first_source_event_id,
                                source_last_event_id=p.last_source_event_id))
            continue
        start, end = target_tick(p.start_vgmticks), target_tick(p.end_vgmticks)
        if end <= start:
            reject(f'PCM playback {p.playback_id} collapses on the MDX clock', status='lossy', code='clock_collapse')
        if start < cursor:
            reject('Multiple overlapping physical PCM playbacks are unsupported', code='overlap')
        if start > cursor:
            rest_units(cursor, start, source_cursor, p.start_vgmticks)
        if loop_tick is not None and not loop_emitted and start == loop_tick:
            song_loop_marker()
        binding = by_sample[p.sample_id]
        pan = PCM_PAN[p.pan]
        body = [emit(kind, value, start_tick=start, playback_id=p.playback_id,
                     source_event_id=p.first_source_event_id)
                for kind, value in (('bank', binding.bank), ('frequency', frequency),
                                    ('pan', pan), ('gate', 8), ('volume', 128))]
        trajectory = []
        body.extend(duration_commands('note', start, end, slot=binding.slot,
                                      playback_id=p.playback_id, source_event_id=p.first_source_event_id))
        text = render(body)
        units.append(unit(start, end, 'pcm_note', text, p.start_vgmticks, p.end_vgmticks,
                          (p.playback_id,), (p.first_source_event_id, *p.control_event_ids,
                                            p.last_source_event_id), trajectory,
                          f'n{binding.slot}'))
        rows.append(dict(playback_id=p.playback_id, sample_id=p.sample_id,
                         bank=binding.bank, slot=binding.slot, mdx_track='P',
                         source_start_vgmticks=p.start_vgmticks,
                         source_end_vgmticks=p.end_vgmticks,
                         start_mdx_tick=start, end_mdx_tick=end,
                         start_error_samples=projected_samples(start, sample_multiplier)-p.start_vgmticks,
                         end_error_samples=projected_samples(end, sample_multiplier)-p.end_vgmticks,
                         rate_num=p.rate_num, rate_den=p.rate_den, mdx_frequency=frequency,
                         reset_origin=p.reset_origin, reset_observed=p.reset_observed,
                         delivery_fallback='continuous_pdx_delivery' if schedule_fallback else '',
                         delivery_max_error_vgmticks=p.delivery_max_error_vgmticks,
                         unfed_tail_vgmticks=p.unfed_tail_vgmticks,
                         source_first_event_id=p.first_source_event_id,
                         source_last_event_id=p.last_source_event_id))
        cursor = end
        source_cursor = p.end_vgmticks
    end = target_tick(analysis.source_end_vgmticks)
    if end > cursor:
        rest_units(cursor, end, source_cursor, analysis.source_end_vgmticks)
    if loop_tick is not None and not loop_emitted:
        reject('PCM loop marker does not fall on an independent playback/rest boundary', code='song_loop_pcm_boundary')
    clock_rows = tuple(dict(source_event_id=c.source_event_id, source_address=c.address,
                            source_vgmticks=c.vgmticks, register=c.register, data=c.data,
                            mdx_tick=target_tick(c.vgmticks),
                            error_samples=projected_samples(target_tick(c.vgmticks),
                                                            sample_multiplier)-c.vgmticks)
                       for c in analysis.controls)
    projected_pcm = getattr(analysis, 'source_origin', None)
    assessment.add('sample', 'pass', 'sample_bytes_exact',
                   'PDX binding retains transcoded MSM6258 target IR bytes; not original source PCM bytes'
                   if projected_pcm else 'PDX binding retains encoded source sample bytes')
    assessment.add('reset', 'unverified', 'iocs_reset_unconfirmed',
                   'Physical decoder reset/continuation through the selected PCM extension is not yet confirmed',
                   scope='runtime_validation')
    assessment.add('consumption', 'unverified', 'consumed_nibbles_unknown',
                   'Projected continuous PDX delivery does not establish actual decoder consumption'
                   if projected_pcm else 'Observed byte supply does not establish actual decoder consumption',
                   scope='runtime_validation')
    if policy == 'strict' and any(item['status'] == 'lossy' for item in assessment.items):
        detail = 'Strict PCM projection blocks known loss: ' + ', '.join(
            dict.fromkeys(item['code'] for item in assessment.items if item['status'] == 'lossy'))
        assessment.block(detail)
        raise ProjectionError(detail, assessment)
    if capacity is not None:
        for row in capacity.rows:
            if row['action'] == 'omit':
                assessment.add('capacity', 'lossy', 'force_pcm_' + row['cap_kind'], row['reason'],
                               cause='explicit_force_capacity', playback_id=row['playback_id'],
                               source_event_id=row['source_first_event_id'],
                               start_vgmticks=row['source_start_vgmticks'],
                               end_vgmticks=row['source_end_vgmticks'],
                               source_value=dict(sample_id=row['sample_id'], actual=row['actual'], limit=row['limit']),
                               projected_value='timed rest', fallback='explicit_force_omit_whole_sample')
    emit('end', start_tick=end)
    return PcmMdxProjection(bindings, tuple(units), tuple(rows), stem+'.pdx', sample_multiplier,
                            clock_rows, assessment, tuple(commands), tuple(omitted),
                            getattr(analysis, 'source_origin', 'native_msm6258'), capacity)


def default_generator():
    suffix = '.exe' if sys.platform == 'win32' else ''
    return Path(__file__).resolve().parents[1] / 'scripts/mdx_fixture_generator/target/release' / ('mdx-fixture-generator'+suffix)


def write_pdx(plan, analysis, outdir, stem, *, generator=None):
    """Use the existing library externally; verify its table/payload independently."""
    out = Path(outdir)
    folder = out / (stem + '.pcm')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'samples').mkdir(exist_ok=True)
    samples = {s.sample_id: s for s in analysis.samples}
    manifest = folder / 'manifest.tsv'
    with manifest.open('w', newline='', encoding='utf-8') as stream:
        stream.write('sample_id\tbank\tslot\tfile\n')
        for b in plan.bindings:
            (folder / b.file).write_bytes(samples[b.sample_id].encoded_bytes)
            stream.write(f'{b.sample_id}\t{b.bank}\t{b.slot}\t{b.file}\n')
    executable = Path(generator or default_generator()).resolve()
    pdx = out / plan.pdx_name
    if not executable.is_file():
        raise ValueError('PCM output requires the built MDX helper; build scripts/mdx_fixture_generator or pass --pcm-generator')
    pdx.unlink(missing_ok=True)
    try:
        result = subprocess.run([str(executable), '--build-pdx', str(manifest.resolve()), str(pdx.resolve())],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        (folder / 'pdx_build.log').write_text(str(error) + '\n', encoding='utf-8')
        pdx.unlink(missing_ok=True)
        raise ValueError(f'PDX helper could not run: {error}') from error
    (folder / 'pdx_build.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        pdx.unlink(missing_ok=True)
        raise ValueError('PDX construction failed: ' + result.stdout + result.stderr)
    payload = pdx.read_bytes()
    def reject(detail):
        pdx.unlink(missing_ok=True)
        raise ValueError(detail)
    if len(payload) < 768:
        reject('PDX builder returned a truncated table')
    expected = {b.slot: samples[b.sample_id].encoded_bytes for b in plan.bindings}
    for slot in range(96):
        offset, length = struct.unpack_from('>II', payload, slot*8)
        if slot in expected:
            if not 768 <= offset <= len(payload) or offset+length > len(payload) or payload[offset:offset+length] != expected[slot]:
                reject(f'PDX payload does not match canonical sample at slot {slot}')
        elif offset or length:
            reject(f'PDX builder populated unallocated slot {slot}')
    return pdx


def write_mdx(plan, fm_structure, outdir, stem, *, generator=None):
    """Compile only FM notation; PCM is built from the same typed target plan."""
    out = Path(outdir)
    folder = out / (stem + '.pcm')
    folder.mkdir(parents=True, exist_ok=True)
    fm = folder / 'opm.mml'
    fm.write_text(fm_structure.text, encoding='utf-8', newline='\n')
    manifest = plan.write_manifest(out, stem)
    executable = Path(generator or default_generator()).resolve()
    mdx = out / (stem + '.mdx')
    if not executable.is_file():
        raise ValueError('PCM output requires the built MDX helper; build scripts/mdx_fixture_generator or pass --pcm-generator')
    mdx.unlink(missing_ok=True)
    try:
        result = subprocess.run([str(executable), '--compile-pcm', str(fm.resolve()),
                                 str(manifest.resolve()), str(mdx.resolve())],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        (folder / 'mdx_build.log').write_text(str(error) + '\n', encoding='utf-8')
        mdx.unlink(missing_ok=True)
        raise ValueError(f'PCM MDX helper could not run: {error}') from error
    (folder / 'mdx_build.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode or not mdx.is_file() or not mdx.stat().st_size:
        mdx.unlink(missing_ok=True)
        detail = result.stdout + result.stderr
        if re.search(r'MDX track \d+ offset exceeds the maximum 0xfffe|Combined native MDX exceeds 65535 bytes', detail):
            assessment = plan.assessment
            assessment.add('format', 'lossy', 'mdx_capacity_exceeded',
                           'The projected data exceed native MDX capacity; no truncation or splitting fallback is defined',
                           cause='target_constraint', evidence=detail.strip())
            assessment.block('MDX capacity exceeded; generated MML and PDX are retained')
            raise ProjectionError('MDX capacity exceeded; generated MML and PDX are retained: ' + detail,
                                  assessment)
        raise ValueError('PCM MDX construction failed: ' + detail)
    try:
        validate_extended_layout(mdx.read_bytes())
    except ValueError:
        mdx.unlink(missing_ok=True)
        raise
    return mdx


def validate_extended_layout(raw):
    """Reject stale helpers selecting standard9 instead of the required target mode.

    Full typed command validation is performed by the helper. This independent
    envelope check verifies its mode selection before recording generated output.
    """
    try:
        title_end = raw.index(b'\r\n\x1a') + 3
        data_start = raw.index(0, title_end) + 1
        offsets = struct.unpack_from('>17H', raw, data_start)
        tone_offset, *tracks = offsets
        if min(t for t in offsets if t) != 34:
            raise ValueError('invalid extended header')
        if tracks != sorted(set(tracks)) or not all(34 <= t < len(raw) - data_start for t in tracks):
            raise ValueError('invalid extended track offsets')
        if tone_offset and not 34 <= tone_offset <= len(raw) - data_start:
            raise ValueError('invalid tone offset')
        if raw[data_start + tracks[0]] != 0xe8:
            raise ValueError('missing initial E8')
        for start in tracks[9:]:
            end = min([len(raw) - data_start] + [t for t in offsets if t > start])
            if end - start < 2 or raw[data_start + start:data_start + start + 2] != b'\xf1\x00':
                raise ValueError('active extra PCM track')
    except (ValueError, struct.error, IndexError) as error:
        raise ValueError('PCM MDX requires 16 tracks, initial E8 and inactive Q-W; '
                         'rebuild scripts/mdx_fixture_generator or update --pcm-generator') from error
