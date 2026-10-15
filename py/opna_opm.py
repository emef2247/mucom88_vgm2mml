"""Native OPNA FM/SSG Segments -> exact-sample projected OPM -> existing MDX path.

Register/channel mapping references vgm-conv's OPNToOPMConverter (ISC).
Native committed 11-bit pitch uses the existing 4 MHz OPM pitch helper.
Source evidence is never rewritten as target convenience.
"""
import csv
import json
import struct
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from psg_scc_opm import opm_pitch
from opna import OpnaUnmodeledWrite, dump_unmodeled_writes
from opna_force import fm_mute_reasons
from vgm_reader import parse_vgm
from vgm_io import read_vgm_bytes
from vgm_timing import command_times


TARGET_CLOCK = 4000000
REFERENCE = '780a20ed505e23eb21aa9c6ce3f349029718b5a0'
NON_FM = {'ssg', 'rhythm', 'adpcm_b', 'irq_flag_control'}


def _unmapped_operator_slot(write):
    return (write.domain == 'uninterpreted' and write.chip_type == 'YM2608'
            and write.chip_instance == 0 and write.port in (0, 1)
            and 0x30 <= write.register <= 0x9f and write.register & 3 == 3)


def dump_fm_omissions(analysis, outdir, stem, *, force):
    omissions = [dict(asdict(w),
                      reason='unmapped_fm_operator_slot' if _unmapped_operator_slot(w) else
                             'unsupported_register_effect:' + w.domain,
                      action='omit_write_from_target')
                 for w in analysis.unmodeled_writes
                 if force and w.domain not in ('ssg', 'adpcm_b')]
    out = Path(outdir)
    csv_name = stem + '.opna_fm.omissions.csv'
    columns = [*OpnaUnmodeledWrite.__dataclass_fields__, 'reason', 'action']
    with (out / csv_name).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(omissions)
    summary = dict(enabled=force, scope='unsupported_fm_and_nonprojected_controls',
                   omitted_writes=len(omissions), source_ir_unchanged=True,
                   sample_rate_hz=44100, writes_csv=csv_name, omissions=omissions)
    (out / (stem + '.opna_fm.omissions.json')).write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def project(analysis, *, clock_hz, pitch_policy='clamp', channel_map=None, force=False):
    """Return target write rows plus explicit losses, retaining source identities."""
    if not 0 < clock_hz < 0x40000000:
        raise ValueError('OPNA FM projection requires one positive-clock YM2608')
    if pitch_policy not in ('clamp', 'error'):
        raise ValueError('OPM pitch policy must be clamp or error')
    channel_map = ({ch: ch + (1 if ch >= 3 else 0) for ch in range(6)}
                   if channel_map is None else dict(channel_map))
    if (any(type(ch) is not int or not 0 <= ch < 6 for ch in channel_map)
            or any(type(ch) is not int or not 0 <= ch < 8 for ch in channel_map.values())
            or len(set(channel_map.values())) != len(channel_map)):
        raise ValueError('OPNA FM channel mapping must use distinct OPM channels')
    if analysis.csm_observed and not force:
        raise ValueError('OPNA CSM timer-generated key events cannot project to ordinary OPM FM')
    if not force and any(w.domain not in NON_FM for w in analysis.unmodeled_writes):
        raise ValueError('OPNA FM projection encountered an unknown register effect')
    losses = Counter(w.domain for w in analysis.unmodeled_writes)
    if force:
        omitted = sum(w.domain not in NON_FM for w in analysis.unmodeled_writes)
        if omitted:
            losses['forced_unmodeled_register_writes'] = omitted
    rows, cache, groups = [], {}, {}
    for s in analysis.segments:
        if s.chip_instance or s.clock_raw != clock_hz:
            raise ValueError('OPNA FM projection requires one unflagged YM2608 clock')
        if s.state.channel3_special_enabled and not force:
            raise ValueError('OPNA CH3 special operator frequencies are not supported by OPM projection')
        if s.source_event_id is not None:
            groups.setdefault(s.source_event_id, []).append(s)

    def emit(s, register, data, reason, *, frequency=None, actual=None):
        # Preserve every Key request and repeated operator edge ordering.
        if register != 8 and cache.get(register) == data:
            return
        cache[register] = data
        rows.append(dict(write_id=len(rows), state_origin='projected_opm', source_chip='opna_fm',
            source_event_id=s.source_event_id if s else None,
            source_segment_id=s.segment_id if s else None,
            original_event_id=s.original_event_id if s else None,
            loop_iteration=s.loop_iteration if s else 0,
            source_vgmticks=s.vgmticks if s else 0,
            source_ch=s.ch if s else None, source_register=s.register if s else None,
            source_data=s.data if s else None, target_register=register, target_data=data,
            reason=reason, source_frequency_hz=frequency, target_frequency_hz=actual,
            target_ch=channel_map.get(s.ch) if s and register != 0x18 else None,
            source_row=None, inferred_key=False, synthetic_phase=None))

    emit(None, 0x18, 0xc2, 'vgm-conv initial LFO frequency')

    def target_channel(s):
        return channel_map.get(s.ch)  # Default vgm-conv uses OPM 0..2/4..6.

    def channel_control(s):
        state = s.state
        pan = (state.left_enabled or 0) << 6 | (state.right_enabled or 0) << 7
        if s.ch >= 3 and state.six_channel_enabled == 0:
            pan = 0  # Output gate, not a synthetic key/envelope restart.
        if force and fm_mute_reasons(s):
            pan = 0  # Keep ordinary keys/controls underneath the explicit output gate.
        return pan | (state.feedback or 0) << 3 | (state.algorithm or 0)

    def pitch(s):
        state, ch = s.state, target_channel(s)
        if ch is None:
            return
        if state.fnum is None or state.block is None:
            losses['unknown_pitch_target_reset_zero_assumption'] += 1
            fnum = state.fnum_low_raw or 0
            block = 0
        else:
            fnum, block = state.fnum, state.block
        frequency = fnum * clock_hz * (6 / state.fm_prescale_divisor) / (144 * 2 ** (20 - block))
        if frequency:
            try:
                kc, kf, actual = opm_pitch(frequency, TARGET_CLOCK)
            except ValueError:
                if pitch_policy == 'error':
                    raise
                kc, kf, actual = opm_pitch(frequency, TARGET_CLOCK, clamp=True)
                losses['pitch_range_clamped'] += 1
        else:
            kc = kf = 0
            actual = None
            losses['zero_frequency_target_minimum_pitch'] += 1
        emit(s, 0x28 + ch, kc, 'committed native FNUM/BLOCK to 4MHz KC', frequency=frequency, actual=actual)
        emit(s, 0x30 + ch, kf, 'committed native FNUM/BLOCK to KF', frequency=frequency, actual=actual)

    for _, segments in sorted(groups.items()):
        s = segments[0]
        state, reg, ch = s.state, s.register, target_channel(s)
        if ch is None and not (s.port == 0 and reg in (0x22, 0x24, 0x25, 0x26, 0x27,
                                                       0x29, 0x2d, 0x2e, 0x2f)):
            continue
        if 0x30 <= reg <= 0x8f:
            bank = (reg >> 2) & 3
            op = state.operators[bank]
            data = {0x30: lambda: op.dt << 4 | op.mul,
                    0x40: lambda: op.tl,
                    0x50: lambda: op.ks << 6 | op.ar,
                    0x60: lambda: op.am_enabled << 7 | op.d1r,
                    0x70: lambda: op.d2r, 0x80: lambda: op.sl << 4 | op.rr}[reg & 0xf0]()
            emit(s, 0x40 + ((reg & 0xf0) - 0x30) * 2 + bank * 8 + ch,
                 data, 'vgm-conv operator bank mapping; DT2 reset zero')
        elif 0x90 <= reg <= 0x9f:
            if state.operators[(reg >> 2) & 3].ssg_eg_enabled:
                losses['ssg_eg_not_available_on_opm'] += 1
            if force:
                emit(s, 0x20 + ch, channel_control(s), 'forced FM SSG-EG output gate')
        elif 0xb0 <= reg <= 0xb2 or 0xb4 <= reg <= 0xb6:
            # Target-only reset assumptions for unwritten counterpart controls.
            emit(s, 0x20 + ch, channel_control(s),
                 'vgm-conv RL swap and FB/algorithm')
            if reg >= 0xb4:
                emit(s, 0x38 + ch, state.pms << 4 | state.ams, 'vgm-conv PMS/AMS mapping')
        elif 0xa0 <= reg <= 0xa2:
            pitch(s)
        elif s.port == 0 and reg == 0x28:
            if s.ch >= 3 and state.six_channel_enabled is None:
                losses['unknown_output_enable_target_enabled_assumption'] += 1
            emit(s, 8, state.key_mask << 3 | ch, 'vgm-conv operator key mask')
        elif s.port == 0 and reg == 0x29:
            for item in segments:
                if item.ch >= 3 and target_channel(item) is not None:
                    emit(item, 0x20 + target_channel(item), channel_control(item),
                         'OPNA six-channel output enable as RL gate; held keys preserved')
        elif s.port == 0 and reg in (0x2d, 0x2e, 0x2f):
            for item in segments:
                if item.state.fnum is not None:
                    pitch(item)
        elif s.port == 0 and reg == 0x22:
            emit(s, 0x18, (0xc2, 0xc9, 0xcb, 0xcd, 0xcf, 0xd6, 0xfb, 0xff)[state.lfo_rate],
                 'vgm-conv approximate LFO frequency')
            if state.lfo_enabled:
                losses['lfo_depth_waveform_and_speed_not_equivalent'] += 1
        elif s.port == 0 and reg in (0x24, 0x25, 0x26, 0x27):
            losses['source_timer_controls_not_projected'] += 1
            if force and reg == 0x27:
                for item in segments:
                    if item.ch == 2 and target_channel(item) is not None:
                        emit(item, 0x20 + target_channel(item), channel_control(item),
                             'forced CH3/CSM output gate; ordinary keys retained')
        # High FNUM writes affect source latches; forced CH3 modes stay output-gated.
    return rows, dict(losses)


def write_vgm(rows, path, *, end, loop_start=None, loop_event_id=None):
    """Serialize exact source samples; MDX quantization belongs downstream."""
    commands, cursor, loop_address = bytearray(), 0, None
    if not 0 <= end <= 0xffffffff or (loop_start is not None and not 0 <= loop_start < end):
        raise ValueError('Projected OPNA duration/loop is outside the VGM field range')
    def wait(gap):
        while gap:
            count = min(gap, 65535)
            commands.extend(b'\x61' + struct.pack('<H', count))
            gap -= count
    def advance(sample, source_event_id=None, *, final=False):
        nonlocal cursor, loop_address
        if sample < cursor:
            raise ValueError('OPNA projection writes are not in source sample order')
        at_loop = (loop_event_id is None or final or sample > (loop_start or 0)
                   or source_event_id is not None and source_event_id >= loop_event_id)
        if loop_start is not None and loop_address is None and cursor <= loop_start <= sample and at_loop:
            wait(loop_start - cursor)
            cursor = loop_start
            loop_address = 0x40 + len(commands)
        wait(sample - cursor)
        cursor = sample
    for row in rows:
        advance(row['source_vgmticks'], row['source_event_id'])
        row['target_command_address'] = 0x40 + len(commands)
        commands.extend((0x54, row['target_register'], row['target_data']))
    advance(end, final=True)
    commands.append(0x66)
    raw = bytearray(0x40) + commands
    raw[:4] = b'Vgm '
    for offset, value in ((4, len(raw)-4), (8, 0x171), (0x18, end), (0x30, TARGET_CLOCK)):
        struct.pack_into('<I', raw, offset, value)
    if loop_address is not None:
        struct.pack_into('<I', raw, 0x1c, loop_address-0x1c)
        struct.pack_into('<I', raw, 0x20, end-loop_start)
    identities = {e.address: i for i, e in enumerate(command_times(raw)) if e.command == 0x54}
    for row in rows:
        row['target_source_event_id'] = identities[row['target_command_address']]
    Path(path).write_bytes(raw)


def convert(source, outdir, *, dump_passes=False, song_loop=True, force=False, **options):
    """Reconstruct native Segments, project FM, reuse canonical OPM conversion."""
    from opm_conversion import convert as convert_opm
    source, outdir = Path(source), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for suffix in ('.mdx.mml', '.opna-opm.json', '.mdx.normalization.json',
                   '.mdx.normalization.csv', '.mdx.before.normalize.mml',
                   '.opna_ssg.omissions.csv', '.opna_ssg.omissions.json',
                   '.opna_fm.omissions.csv', '.opna_fm.omissions.json', '.opna.unmodeled.csv',
                   '.opna_force.skipped_registers.csv', '.opna_force.skipped_registers.json'):
        artifact = outdir / (source.stem + suffix)
        if artifact.resolve() == source.resolve() or not artifact.resolve().is_relative_to(outdir.resolve()):
            raise ValueError('OPNA conversion output would replace or leave its source/output boundary')
        artifact.unlink(missing_ok=True)
    with TemporaryDirectory(prefix='opna-opm-') as temporary:
        work = outdir if dump_passes else Path(temporary)
        metadata, source_loop, pcm_metadata = {}, {}, {}
        parse_vgm(str(source), str(work), opna_metadata=metadata, dump_opna_segments=True,
                  opna_pcm_metadata=pcm_metadata,
                  loop_metadata=source_loop, dump_loop=dump_passes, loop_count=2 if song_loop else 1)
        from opna_adpcm import project_msm6258
        native_pcm = pcm_metadata['analysis']
        native_pcm.dump(outdir, source.stem)
        from opna_force_pcm import prepare as prepare_forced_pcm
        projection_pcm, pcm_omissions = prepare_forced_pcm(native_pcm, force=force)
        # A control slice is not a fresh decoder. Keep the existing independent
        # route, but reconstruct the whole epoch when live controls are present.
        continuous_pcm = any(s.continuation_index or
            'controls_during_decoder_epoch' in s.issues or
            (s.rate_num == 0 and native_pcm.clock_hz > 0) for s in projection_pcm.segments)
        target_pcm = None if continuous_pcm else project_msm6258(projection_pcm)
        if target_pcm is not None:
            target_pcm.dump(outdir, source.stem)
        if any(row['domain'] != 'adpcm_b_data_block' for row in metadata['auxiliary_commands']):
            raise ValueError('Unexpanded OPNA DAC streams may affect FM; projection refused')
        analysis = metadata['analysis']
        if analysis is None or not metadata['write_count']:
            raise ValueError('Input has no OPNA register evidence')
        from opna_ssg import build_segments as build_ssg
        from opna_ssg_opm import allocate, dump_allocation, dump_omissions, merge, project as project_ssg
        ssg = build_ssg(metadata['csv_path'], end_vgmticks=analysis.source_end_vgmticks)
        if dump_passes or force:
            ssg.dump(outdir, source.stem)
        if force:
            dump_unmodeled_writes(analysis, csv_path=outdir / (source.stem + '.opna.unmodeled.csv'))
        fm_map, ssg_map, allocation = allocate(analysis, ssg)
        allocation_summary = dump_allocation(allocation, outdir, source.stem)
        rows, losses = project(analysis, clock_hz=metadata['clock_raw'],
                               pitch_policy=options.get('pitch_policy', 'clamp'),
                               channel_map=fm_map, force=force)
        fm_force = dump_fm_omissions(analysis, outdir, source.stem, force=force)
        losses.pop('ssg', None)  # SSG writes now enter their own target projection.
        folder = work / 'projected_opm'
        folder.mkdir(exist_ok=True)
        target = folder / (source.stem + '.vgm')
        if target.resolve() == source.resolve():
            raise ValueError('Projected OPM output must not replace its OPNA source')
        loop_start = (source_loop['replay_start_samples']
                      if song_loop and source_loop.get('status') == 'valid' else None)
        ssg_rows, ssg_baseline, ssg_performance = project_ssg(ssg, ssg_map,
            loop_start=loop_start, pitch_policy=options.get('pitch_policy', 'clamp'), force=force)
        force_summary = dump_omissions(ssg_baseline.settings['forced_omissions'],
                                      outdir, source.stem, force=force)
        force_summary.update(scope='opna_partial_hw_projection',
                             fm_omitted_writes=fm_force['omitted_writes'],
                             fm_writes_csv=fm_force['writes_csv'], fm_omissions=fm_force['omissions'],
                             pcm_part_omitted=bool(pcm_omissions))
        from opna_force import collect as collect_force, dump as dump_force
        decisions = collect_force(analysis, ssg, ssg_baseline.settings['forced_omissions'],
                                  fm_force['omissions'], pcm_omissions=pcm_omissions,
                                  dropped_ssg=allocation_summary['dropped_ssg'], force=force)
        register_report = dump_force(decisions, outdir, source.stem, force=force)
        force_summary.update(skipped_registers=register_report['skipped_registers'],
                             registers_csv=register_report['registers_csv'],
                             decision_count=register_report['decision_count'])
        if force_summary['omitted_intervals']:
            losses['forced_ssg_intervals_muted'] = force_summary['omitted_intervals']
        if dump_passes:
            ssg_baseline.dump(outdir, source.stem + '.ssg')
            ssg_performance.dump(outdir, source.stem + '.ssg')
        if allocation_summary['dropped_ssg']:
            losses['ssg_channels_dropped_for_opm_capacity'] = len(allocation_summary['dropped_ssg'])
        if ssg_map:
            losses['ssg_inferred_gate_and_fm_timbre_approximation'] = len(ssg_map)
        rows = merge(rows, ssg_rows)
        write_vgm(rows, target, end=analysis.source_end_vgmticks, loop_start=loop_start,
                  loop_event_id=source_loop.get('replay_start_event_id'))
        if continuous_pcm:
            from opna_adpcm_waveform import project as project_continuous_pcm
            pcm_bridge = project_continuous_pcm(projection_pcm,
                source_raw=read_vgm_bytes(source), loop_start=loop_start)
            pcm_bridge.native.dump(outdir, source.stem)
            pcm_bridge.dump(outdir, source.stem)
        else:
            from opna_pcm import adapt
            pcm_bridge = adapt(projection_pcm, target_pcm, source_raw=read_vgm_bytes(source),
                               loop_start=loop_start)
            pcm_bridge.dump_bridge(outdir, source.stem)
        mapping = folder / (source.stem + '.source_map.csv')
        with mapping.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        settings = dict(state_origin='projected_opm', source_chip='YM2608', target_chip='YM2151',
            source_clock_hz=metadata['clock_hz'], target_clock_hz=TARGET_CLOCK,
            mapping='vgm-conv OPNToOPMConverter', reference_revision=REFERENCE,
            channel_allocation=allocation_summary, force_projection=force_summary,
            ssg_projection=dict(model='existing PSG FM AL4/FB7', channels=len(ssg_map),
                inferred_attacks=sum(w.inferred_key and bool(w.data & 0x78) for w in ssg_performance.writes),
                noise_and_hardware_envelope=('unsupported intervals muted by explicit --force'
                    if force else 'same explicit rejection as existing PSG target'),
                clock='per Segment master/4, master/2 or master; exact source samples before MDX',
                source_provenance='opna_ssg; baseline/performance reuse generic PSG model'),
            source_end_vgmticks=analysis.source_end_vgmticks, excluded_or_approximated=losses,
            pitch='committed 11-bit FNUM; existing nominal OPM KC/KF projection',
            target_only_assumptions='unwritten counterpart controls/pitch use reset zero',
            limitations='FM EG/DT rates unretimed; ADPCM-B transcoding is approximate; no acoustic equivalence',
            pcm_ir=dict(source_codec='ym2608-adpcm-b', target_codec='msm6258-adpcm4',
                        native_segments=len(native_pcm.segments), target_samples=len(pcm_bridge.samples),
                        eligible_playbacks=sum(p.independently_playable for p in pcm_bridge.playbacks),
                        ineligible_playbacks=sum(not p.independently_playable for p in pcm_bridge.playbacks),
                        continuous_decoder_epochs=continuous_pcm,
                        native_issues=[issue.code for issue in native_pcm.issues],
                        candidate_pdx_samples=len(pcm_bridge.samples), pdx_distinct_samples=None,
                        policy=options.get('pcm_policy', 'strict'),
                        integration='transcoded MSM6258 IR to shared-clock MDX P track and external PDX packer'),
            source_loop=source_loop, passes_retained=dump_passes,
            source_map=str(Path('projected_opm') / mapping.name) if dump_passes else None)
        (folder / 'provenance.json').write_text(json.dumps(settings, indent=2)+'\n', encoding='utf-8')
        (outdir / (source.stem + '.opna-opm.json')).write_text(
            json.dumps(settings, indent=2)+'\n', encoding='utf-8')
        result, _, _ = convert_opm(target, outdir, dump_passes=dump_passes,
            pcm_analysis_override=pcm_bridge, source_passes_outdir=folder,
            song_loop=song_loop, replayed_song_loop=True, force=force,
            normalization_source_times={r['target_source_event_id']: r['source_vgmticks'] for r in rows}, **options)
        timing_path = outdir / (source.stem + '.pcm.timing.json')
        final_pcm = json.loads(timing_path.read_text(encoding='utf-8')) if timing_path.is_file() else {}
        settings['pcm_ir']['pdx_distinct_samples'] = final_pcm.get('pcm_sample_count', 0)
        (folder / 'provenance.json').write_text(json.dumps(settings, indent=2)+'\n', encoding='utf-8')
        (outdir / (source.stem + '.opna-opm.json')).write_text(
            json.dumps(settings, indent=2)+'\n', encoding='utf-8')
    return result, settings
