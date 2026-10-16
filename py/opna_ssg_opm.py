"""Song-wide FM-first allocation and the existing PSG FM target for OPNA SSG."""
import csv
import json
from pathlib import Path

from chip_segments import SccAnalysis
from opm_performance import build_performance
from psg_scc_opm import project as project_psg


def allocate(fm, ssg):
    # Any key use reserves the whole part, including release tails and output
    # gating. Register initialization alone does not reserve a target channel.
    used_fm = {s.ch for s in fm.segments if s.state.key_mask or s.rising_mask}
    used_ssg = {ch for ch, items in ssg.segments.items()
                if any(s.mode and (s.volume or s.envelope_enabled) for s in items)}
    fm_map = {ch: ch + (1 if ch >= 3 else 0) for ch in sorted(used_fm)}
    available = [ch for ch in range(8) if ch not in fm_map.values()]
    ssg_map = dict(zip(sorted(used_ssg), available))
    rows = []
    for domain, count, used, mapping in (('fm', 6, used_fm, fm_map),
                                         ('ssg', 3, used_ssg, ssg_map)):
        for ch in range(count):
            target = mapping.get(ch)
            rows.append(dict(source_domain=domain, source_ch=ch, used=ch in used,
                target_ch=target, target_track=chr(65 + target) if target is not None else '',
                status='assigned' if target is not None else 'dropped' if ch in used else 'unused',
                reason='FM priority' if domain == 'fm' and ch in used else
                       'remaining OPM channel' if target is not None else
                       'OPM eight-channel capacity; FM has priority' if ch in used else
                       'no source key activity' if domain == 'fm' else 'no SSG audibility'))
    return fm_map, ssg_map, rows


def dump_allocation(rows, outdir, stem):
    out = Path(outdir)
    with (out / (stem + '.opna_channel_allocation.csv')).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(policy='whole-song FM > SSG; stable source SSG channel order',
        capacity=8, assigned=sum(row['status'] == 'assigned' for row in rows),
        dropped_ssg=[row['source_ch'] for row in rows if row['status'] == 'dropped'],
        parts=rows, fm_usage='any key activity; release tails remain reserved',
        ssg_usage='mixer enabled with nonzero fixed volume or hardware envelope',
        runtime_channel_reassignment=False)
    (out / (stem + '.opna_channel_allocation.json')).write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def project(ssg, channel_map, *, loop_start=None, pitch_policy='clamp', force=False):
    # A discarded ninth part cannot reject retained FM/SSG because of a target
    # mode limitation. Every part remains in the independent native SSG dump.
    selected = {ch: ssg.segments[ch] for ch in channel_map}
    baseline = project_psg(selected, SccAnalysis({}, ()), psg_clock=ssg.clock_hz / 4,
        scc_clock=0, end_vgmticks=ssg.source_end_vgmticks,
        psg_model='fm', pitch_policy=pitch_policy, force=force)
    omissions = []
    for loss in baseline.settings['forced_omissions']:
        segment = selected[loss['source_ch']][loss['source_row']]
        omissions.append(dict(source_ch=loss['source_ch'], source_channel=loss['source_channel'],
            source_row=loss['source_row'], source_event_id=segment.source_event_id,
            original_event_id=segment.original_event_id, loop_iteration=segment.loop_iteration,
            start_vgmticks=loss['start_vgmticks'], end_vgmticks=loss['end_vgmticks'],
            target_ch=channel_map[loss['source_ch']], source_mode=segment.mode,
            source_volume=segment.volume, envelope_enabled=segment.envelope_enabled,
            reason=loss['reason'].replace('psg_', 'ssg_'), action='mute_entire_ssg_interval'))
    omitted = {(r['source_ch'], r['source_row']): r for r in omissions}
    for row in baseline.rows:
        original = ssg.segments[row['source_ch']][row['source_row']]
        loss = omitted.get((row['source_ch'], row['source_row']))
        row.update(source_envelope_enabled=original.envelope_enabled,
                   projected_mode=0 if loss else original.mode)
        if loss:
            row.update(source_mode=original.mode, source_volume=original.volume,
                       approximation='--force: ' + loss['reason'] + '; entire interval muted')
    baseline.settings.update(force_enabled=force, forced_omissions=omissions, source_origin='opna_ssg',
        source_clock_profile='YM2608 prescaler: master/4, master/2 or master',
        source_clock_per_segment=True,
        stage='canonical PSG profile before final OPNA channel remapping',
        final_opm_channel_map=channel_map)
    performance = build_performance(baseline, terminate=loop_start is None)
    rows = []
    for write in performance.writes:
        target = channel_map[write.source_ch]
        register = write.register if write.register == 8 else (write.register & ~7) | target
        data = (write.data & ~7) | target if register == 8 else write.data
        segment = selected[write.source_ch][write.source_row] if write.source_row is not None else None
        interpreted = (performance.rows[write.performance_row_id]
                       if write.performance_row_id is not None else {})
        rows.append(dict(write_id=len(rows), state_origin='projected_opm', source_chip='opna_ssg',
            source_event_id=segment.source_event_id if segment else None,
            source_segment_id=None,
            original_event_id=segment.original_event_id if segment else None,
            loop_iteration=segment.loop_iteration if segment else 0,
            source_vgmticks=write.vgmticks, source_ch=write.source_ch,
            source_register=segment.register if segment else None,
            source_data=segment.data if segment else None,
            target_register=register, target_data=data,
            reason='SSG via PSG FM: ' + write.reason,
            source_frequency_hz=interpreted.get('frequency_hz') or None,
            target_frequency_hz=interpreted.get('target_frequency_hz') or None,
            target_ch=target, source_row=write.source_row, inferred_key=write.inferred_key,
            synthetic_phase=('terminal' if write.vgmticks == ssg.source_end_vgmticks else 'initial')
                            if segment is None else None))
    return rows, baseline, performance



def dump_omissions(omissions, outdir, stem, *, force):
    out = Path(outdir)
    columns = ['source_ch', 'source_channel', 'source_row', 'source_event_id',
               'original_event_id', 'loop_iteration', 'start_vgmticks', 'end_vgmticks',
               'target_ch', 'source_mode', 'source_volume', 'envelope_enabled', 'reason', 'action']
    csv_name = stem + '.opna_ssg.omissions.csv'
    with (out / csv_name).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(omissions)
    summary = dict(enabled=force, scope='opna_ssg_noise_and_hardware_envelope',
        omitted_intervals=len(omissions),
        omitted_channel_samples=sum(r['end_vgmticks'] - r['start_vgmticks'] for r in omissions),
        source_ir_unchanged=True, elapsed_time_unchanged=True, sample_rate_hz=44100,
        intervals_csv=csv_name, omissions=omissions)
    (out / (stem + '.opna_ssg.omissions.json')).write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def merge(fm_rows, ssg_rows):
    def key(row):
        identity = row['source_event_id']
        order = (float('inf') if row.get('synthetic_phase') == 'terminal' else -1) if identity is None else identity
        return row['source_vgmticks'], order, row['write_id']
    rows = sorted([*fm_rows, *ssg_rows], key=key)
    for index, row in enumerate(rows):
        row['write_id'] = index
    return rows
