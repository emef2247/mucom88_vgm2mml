"""PSG/SCC to OPM target orchestration, shared by the CLI and diagnostics."""
import shutil
import tempfile
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_timing import command_times
from vgm_reader import parse_vgm
from psg import build_segments as build_psg
from scc import build_segments as build_scc
from psg_scc_opm import project


@dataclass(frozen=True)
class StructuredContext:
    performance: object
    analysis: object
    projection: object
    normalization: dict | None = None

def source_facts(raw):
    header = read_vgm_header(raw)
    clocks = [header['ay_clock_raw'], header['scc_clock_raw']]
    ay_type, ay_flags = header['ay_type'], header['ay_flags']
    end = 0
    used_psg = used_scc = False
    silent_opll_writes = 0
    absent_scc_writes = 0
    for event in command_times(raw):
        cmd = event.command
        end = event.vgmticks + event.wait_samples
        if cmd in (0x61, 0x62, 0x63, 0x64, 0x66) or 0x70 <= cmd <= 0x7f:
            continue
        if cmd == 0xa0:
            used_psg = True
            if not clocks[0] or raw[event.address + 1] > 15:
                raise ValueError('Unsupported AY instance/register or missing clock')
        elif cmd == 0x51:
            reg, data = raw[event.address + 1:event.address + 3]
            if (0x20 <= reg <= 0x28 and data & 0x10) or (reg == 0x0e and data & 0x20 and data & 0x1f):
                raise ValueError('Active OPLL is not supported by the PSG/SCC OPM target')
            silent_opll_writes += 1
        elif cmd == 0xd2:
            used_scc = True
            port = raw[event.address + 1]
            if not clocks[1]:
                if port == 2 and raw[event.address + 3] & 15:
                    raise ValueError('SCC volume written without a clock')
                absent_scc_writes += 1
            if port not in (0, 1, 2, 3):
                raise ValueError('SCC test/variant/instance writes require further target support')
        else:
            raise ValueError(f'Unsupported source command in this prototype: {cmd:#x}')
    clocks = [clocks[0] if used_psg else 0, clocks[1] if used_scc else 0]
    if any(c & 0xc0000000 for c in clocks):
        raise ValueError('Dual chips and SCC variants are not implemented in this target')
    if used_psg and (ay_type != 0 or ay_flags not in (0, 1, 2, 3)):
        raise ValueError('Volume/pitch profile supports AY8910 legacy/single output flags only')
    return dict(psg_clock=clocks[0], scc_clock=clocks[1], end_vgmticks=end,
                ay_type=ay_type, ay_flags=ay_flags, silent_opll_writes=silent_opll_writes, absent_scc_writes=absent_scc_writes)


def _read_generated_csv(path):
    """Read full provenance fields, restoring the process-wide CSV limit."""
    previous_limit = csv.field_size_limit()
    # UTF-8 field character counts cannot exceed the containing file's bytes.
    read_limit = max(previous_limit, path.stat().st_size)
    try:
        csv.field_size_limit(read_limit)
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            return list(reader.fieldnames or ()), list(reader)
    finally:
        csv.field_size_limit(previous_limit)


def _source_normalization_check(performance, source_loop, projection, report, *, source_map=None):
    from opm_mdx import mdx_tick, projected_samples
    if source_loop.get('loop_offset') and source_loop.get('status') != 'valid':
        return dict(accepted=False, reason='Original source loop boundary is not valid')
    times = {0, performance.source_end}
    times.update(w.vgmticks for w in performance.writes)
    for row in performance.rows:
        times.update((row['vgmticks'], row['vgmticks_end']))
    if source_loop.get('status') == 'valid':
        times.update((source_loop['loop_start_samples'], source_loop['decoded_end_samples']))
    times = sorted(times)
    # The intermediate OPM stream is emitted on the conventional MDX lattice.
    ticks = [projection.mdx_tick(projected_samples(mdx_tick(time))) for time in times]
    errors = [projection.projected_samples(tick) - time for time, tick in zip(times, ticks)]
    bound = report['correction_bound_samples'] + 6
    collapsed = sum(a == b for a, b in zip(ticks, ticks[1:]))
    worst = max(map(abs, errors), default=0)
    if report.get('short_note_policy') is not None:
        omission_bound = report['short_note_policy']['max_duration_samples']
        identities = {int(r['target_source_event_id']): int(r['write_id']) for r in source_map or ()}
        source_writes = {w.write_id: w for w in performance.writes}
        omitted = set()
        for gate in report['short_note_policy']['omitted_gates']:
            try:
                on = source_writes[identities[gate['source_on_event_id']]]
                off = source_writes[identities[gate['source_off_event_id']]]
            except KeyError:
                return dict(accepted=False, reason='Short-note omission has no original source mapping')
            if not 0 <= off.vgmticks-on.vgmticks <= omission_bound:
                return dict(accepted=False, reason='Projected short note exceeds configured original source duration')
            omitted.update((on.write_id, off.write_id))
        key_times = {}
        for w in performance.writes:
            if w.register == 8 and w.write_id not in omitted:
                key_times.setdefault(w.target_ch, []).append(w)
        def target_tick(time):
            return projection.mdx_tick(projected_samples(mdx_tick(time)))
        protected_collapsed = sum(a.vgmticks < b.vgmticks and
                                  (a.data & 0x78 or b.vgmticks-a.vgmticks > omission_bound) and
                                  target_tick(a.vgmticks) == target_tick(b.vgmticks)
                                  for writes in key_times.values() for a, b in zip(writes, writes[1:]))
        if source_loop.get('status') == 'valid':
            protected_collapsed += (target_tick(source_loop['loop_start_samples'])
                                    >= target_tick(source_loop['decoded_end_samples']))
        accepted = not protected_collapsed and worst <= bound
        return dict(accepted=accepted,
                    reason=('Original source timing is bounded; surviving key operations remain ordered' if accepted
                            else 'Bounded output violates surviving PSG/SCC key timing/order'),
                    boundary_count=len(times), coalesced_control_intervals=collapsed,
                    collapsed_protected_intervals=protected_collapsed,
                    omitted_performance_write_ids=sorted(omitted),
                    max_abs_source_to_final_error_samples=worst,
                    source_to_final_timing_bound_samples=bound)
    accepted = not collapsed and worst <= bound
    return dict(accepted=accepted, reason=('Original source boundaries remain bounded and ordered' if accepted
                else 'Normalized clock violates original PSG/SCC boundary timing/order'),
                boundary_count=len(times), collapsed_positive_intervals=collapsed,
                max_abs_source_to_final_error_samples=worst,
                source_to_final_timing_bound_samples=bound)


def _mark_projected_evidence(folder, source, target, performance, projection, normalization):
    """Label generated OPM evidence; original PSG/SCC CSVs remain unchanged."""
    mapping = folder / (source.stem + '.source_map.csv')
    columns, rows = _read_generated_csv(mapping)
    errors = []
    omitted_ids = {identity for gate in normalization.get('short_note_policy', {}).get('omitted_gates', [])
                   for identity in (gate['source_on_event_id'], gate['source_off_event_id'])}
    if not normalization.get('short_note_omission_adopted'):
        omitted_ids.clear()
    for row in rows:
        tick = projection.mdx_tick(int(row['target_vgmticks']))
        sample = projection.projected_samples(tick)
        row.update(final_mdx_tick=tick, final_projected_vgmticks=sample,
                   source_to_final_error_samples=sample-int(row['vgmticks']),
                   output_action=('omitted_short_note_key' if int(row['target_source_event_id']) in omitted_ids
                                  else 'retained'))
        errors.append(row['source_to_final_error_samples'])
    errors.append(projection.end_projected_vgmticks-performance.source_end)
    for name in ('final_mdx_tick', 'final_projected_vgmticks', 'source_to_final_error_samples', 'output_action'):
        columns.append(name)
    with mapping.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    for path in folder.glob('*.csv'):
        fields, records = _read_generated_csv(path)
        if not fields:
            continue
        if 'state_origin' not in fields:
            fields.append('state_origin')
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(dict(row, state_origin='projected_opm') for row in records)
    bound = (normalization['source_projection_check']['source_to_final_timing_bound_samples']
             if normalization['adopted'] else 12)
    provenance = dict(state_origin='projected_opm', source_vgm=str(source.resolve()),
                      source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                      projected_opm_vgm=str(target.resolve()),
                      projected_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                      source_chip_evidence='PSG/SCC; no native OPM claimed',
                      opm_pipeline='opm_conversion.convert', settings=performance.settings,
                      source_end_vgmticks=performance.source_end,
                      projected_opm_end_vgmticks=projection.source_end_vgmticks,
                      final_mdx_end_vgmticks=projection.end_projected_vgmticks,
                      max_abs_source_to_final_error_samples=max(map(abs, errors), default=0),
                      source_to_final_timing_bound_samples=bound, mapping_csv=mapping.name,
                      length_normalization=normalization)
    (folder / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    if provenance['max_abs_source_to_final_error_samples'] > bound:
        raise ValueError('Projected note clock exceeds the combined source timing bound')


def _convert(source, out, *, psg_gain=None, scc_gain=.125, title=None, psg_model='fm', pitch_policy=None,
             notation='structured', loops=True, normalize_lengths=None, projection_mode='musical', normalization_ms=8, song_loop=True, force=False):
    if notation not in ('structured', 'registers'):
        raise ValueError('PSG/SCC OPM notation must be structured or registers')
    source, out = Path(source), Path(out)
    facts = source_facts(read_vgm_bytes(source))
    out.mkdir(parents=True, exist_ok=True)
    source_loop = {}
    paths = parse_vgm(str(source), str(out), include_vgmticks=True, dump_loop=True,
                      loop_metadata=source_loop, loop_count=2 if song_loop else 1)
    loop_start = (source_loop.get('replay_start_samples')
                  if song_loop and source_loop.get('status') == 'valid' else None)
    facts['end_vgmticks'] = source_loop.get('playback_end_samples', facts['end_vgmticks'])
    psg = build_psg(paths[2], str(out), stem=source.stem, dump_passes=True)
    scc = build_scc(paths[3], str(out), stem=source.stem, dump_passes=True)
    plan = project(psg, scc, psg_clock=facts['psg_clock'], scc_clock=facts['scc_clock'],
                   end_vgmticks=facts['end_vgmticks'], psg_gain=psg_gain, scc_gain=scc_gain,
                   psg_model=psg_model, pitch_policy=pitch_policy, force=force)
    if force:
        from psg_force import dump_decisions
        plan.settings['force_projection'] = dump_decisions(plan, psg, out, source.stem)
    plan.settings.update(ay_type=facts['ay_type'], ay_flags=facts['ay_flags'],
                          silent_opll_writes=facts['silent_opll_writes'], absent_scc_writes=facts['absent_scc_writes'],
                          projection_mode=projection_mode)
    plan.dump(out, source.stem)
    mml = out / (source.stem + '.mdx.mml')
    title = title or source.stem + ' - PSG/SCC OPM (' + psg_model + ')'
    if projection_mode == 'musical':
        from opm_performance import build_performance
        from opm_target_vgm import write_target_vgm
        from opm_conversion import convert as convert_opm
        performance = build_performance(plan, terminate=loop_start is None)
        performance.dump(out, source.stem)
        folder = out / 'projected_opm'
        folder.mkdir(exist_ok=True)
        target = write_target_vgm(performance, folder / (source.stem + '.vgm'),
                                  mapping_csv=folder / (source.stem + '.source_map.csv'), song_loop_start=loop_start)
        _, source_map = _read_generated_csv(folder / (source.stem + '.source_map.csv'))
        native_mml, analysis, projection = convert_opm(target, folder, dump_passes=True, title=title, loops=loops,
            song_loop=song_loop, replayed_song_loop=True,
            normalize_lengths=normalize_lengths, normalization_ms=normalization_ms,
            normalization_source_times={int(r['target_source_event_id']): int(r['vgmticks'])
                                        for r in source_map},
            normalization_validator=lambda candidate, report: _source_normalization_check(
                performance, source_loop, candidate, report, source_map=source_map))
        normalization_path = folder / (source.stem + '.mdx.normalization.json')
        normalization = json.loads(normalization_path.read_text(encoding='utf-8'))
        shutil.copyfile(normalization_path, out / normalization_path.name)
        plan.structured_context = StructuredContext(performance, analysis, projection, normalization)
        _mark_projected_evidence(folder, source, target, performance, projection, normalization)
        text = native_mml.read_text(encoding='utf-8')
        text = '; PSG/SCC OPM target; PSG model=' + psg_model + '.\n; Musical onsets inferred from audibility; oscillator phase differs from the held baseline.\n' + text
        mml.write_text(text, encoding='utf-8')
    else:
        if loop_start is not None:
            raise ValueError('Declared song loops require musical PSG/SCC projection; use --no-song-loop for held-register compatibility')
        mml.write_text(plan.render(title), encoding='utf-8')
        report = dict(requested=normalize_lengths, enabled=False, adopted=False, status='unchanged',
                      reason='Target-clock correction is not applicable to held registers compatibility',
                      notation=notation, source_segments_unchanged=True)
        (out / (source.stem + '.mdx.normalization.json')).write_text(
            json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return mml, plan



def convert(source, out, *, psg_gain=None, scc_gain=.125, title=None, psg_model='fm', pitch_policy=None,
            dump_passes=True, notation='structured', loops=True, normalize_lengths=None, projection_mode=None, normalization_ms=8, song_loop=True, force=False):
    """Keep native evidence on request; default standalone audit keeps all passes."""
    from conversion_config import normalization_enabled
    normalization_enabled(normalize_lengths, notation=notation)
    from conversion_config import normalization_samples
    normalization_samples(normalization_ms)
    projection_mode = projection_mode or ('musical' if notation == 'structured' else 'held-register-compatibility')
    if (projection_mode, notation) not in (('musical', 'structured'), ('held-register-compatibility', 'registers')):
        raise ValueError('Projection mode and notation are incompatible')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for suffix in ('.mdx.mml', '.mdx.normalization.json',
                   '.psg_force.skipped_registers.csv', '.psg_force.skipped_registers.json'):
        artifact = out / (Path(source).stem + suffix)
        if artifact.resolve() == Path(source).resolve():
            raise ValueError('Conversion output must not replace its source input')
        artifact.unlink(missing_ok=True)
    if dump_passes or force:
        return _convert(source, out, psg_gain=psg_gain, scc_gain=scc_gain, title=title,
                        psg_model=psg_model, pitch_policy=pitch_policy, notation=notation, loops=loops,
                        normalize_lengths=normalize_lengths, projection_mode=projection_mode, normalization_ms=normalization_ms, song_loop=song_loop, force=force)
    with tempfile.TemporaryDirectory(prefix='psg-scc-opm-') as temp:
        mml, plan = _convert(source, temp, psg_gain=psg_gain, scc_gain=scc_gain, title=title,
                        psg_model=psg_model, pitch_policy=pitch_policy, notation=notation, loops=loops,
                        normalize_lengths=normalize_lengths, projection_mode=projection_mode, normalization_ms=normalization_ms, song_loop=song_loop, force=force)
        result = out / mml.name
        shutil.copyfile(mml, result)
        normalization = mml.with_name(Path(source).stem + '.mdx.normalization.json')
        shutil.copyfile(normalization, out / normalization.name)
    return result, plan


