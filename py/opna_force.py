"""Inspectable target decisions for explicitly forced partial OPNA conversion."""
from collections import Counter
import csv
import json
from pathlib import Path


def fm_mute_reasons(segment):
    state = segment.state
    reasons = []
    if segment.ch == 2:
        if state.channel3_special_enabled:
            reasons.append('ch3_special_frequency')
        if state.csm_enabled:
            reasons.append('csm_automatic_keys')
    if any(op.ssg_eg_enabled for op in state.operators):
        reasons.append('fm_ssg_eg')
    return tuple(reasons)


def collect(analysis, ssg, ssg_omissions, fm_omissions, *, pcm_omissions=(),
            dropped_ssg=(), force=False):
    """Keep write omission, partial effect, approximation and gated spans distinct."""
    if not force:
        return []
    rows = []
    columns = ('source_event_id', 'original_event_id', 'loop_iteration', 'original_vgmticks',
               'address', 'vgmticks', 'vgmticks_end', 'chip_instance', 'port', 'register',
               'data', 'source_ch', 'source_part', 'source_segment_id', 'reason', 'action', 'record_kind',
               'affected_bits', 'trigger_port', 'trigger_register', 'trigger_data')

    def add(source, reason, action, *, kind='write', port=None, register=None,
            data=None, bits=None, use_feature=False):
        row = {k: None for k in columns}
        tick = getattr(source, 'vgmticks', 0)
        row.update(source_event_id=getattr(source, 'source_event_id', None),
            original_event_id=getattr(source, 'original_event_id', None),
            loop_iteration=getattr(source, 'loop_iteration', 0),
            original_vgmticks=getattr(source, 'original_vgmticks', tick),
            address=getattr(source, 'address', None), vgmticks=tick,
            vgmticks_end=getattr(source, 'vgmticks_end', tick),
            chip_instance=getattr(source, 'chip_instance', 0),
            port=getattr(source, 'port', None), register=getattr(source, 'register', None),
            data=getattr(source, 'data', None), source_ch=getattr(source, 'ch', None),
            source_part=(('SSG' if hasattr(source, 'envelope_enabled') else 'FM') +
                         str(source.ch + 1)) if hasattr(source, 'ch') else 'shared',
            source_segment_id=getattr(source, 'segment_id', None),
            trigger_port=getattr(source, 'port', None),
            trigger_register=getattr(source, 'register', None), trigger_data=getattr(source, 'data', None),
            reason=reason, action=action, record_kind=kind, affected_bits=bits)
        if use_feature:
            row.update(port=port, register=register, data=data)
        rows.append(row)

    for item in fm_omissions:
        row = {k: None for k in columns}
        row.update({k: item[k] for k in columns if k in item})
        row.update(source_part='shared', vgmticks_end=item['vgmticks'], record_kind='write',
                   trigger_port=item['port'], trigger_register=item['register'], trigger_data=item['data'])
        rows.append(row)
    seen = set()
    for event in analysis.events:
        if event.source_event_id in seen:
            continue
        seen.add(event.source_event_id)
        reg = event.register
        if event.port == 0 and reg in (0x24, 0x25, 0x26, 0x27):
            add(event, 'source_timer_effect_not_projected', 'omit_effect',
                bits=0x3f if reg == 0x27 else 0xff)
        elif event.port == 0 and reg in (0xa8, 0xa9, 0xaa, 0xac, 0xad, 0xae):
            add(event, 'ch3_operator_frequency_not_projected', 'omit_effect')
        elif reg is not None and 0x90 <= reg <= 0x9f:
            add(event, 'fm_ssg_eg_control_not_projected', 'omit_effect', bits=0x0f)
        elif event.port == 0 and reg == 0x22 and event.state.lfo_enabled:
            add(event, 'opna_opm_lfo_not_equivalent', 'approximation')

    for segment in analysis.segments:
        if segment.vgmticks_end <= segment.vgmticks:
            continue
        for reason in fm_mute_reasons(segment):
            if reason in ('ch3_special_frequency', 'csm_automatic_keys'):
                add(segment, reason, 'mute_channel_interval', kind='interval',
                    port=0, register=0x27, data=segment.state.mode_raw,
                    bits=0xc0 if reason == 'ch3_special_frequency' else 0x80, use_feature=True)
            else:
                for bank, operator in enumerate(segment.state.operators):
                    if operator.ssg_eg_enabled:
                        add(segment, reason, 'mute_channel_interval', kind='interval',
                            port=segment.ch//3, register=0x90+bank*4+segment.ch%3,
                            data=operator.ssg_eg_raw, bits=0x0f, use_feature=True)

    for loss in ssg_omissions:
        segment = ssg.segments[loss['source_ch']][loss['source_row']]
        references = []
        if 'ssg_noise' in loss['reason']:
            references += [(6, 0x1f), (7, 0x3f)]
        if 'ssg_hardware_envelope' in loss['reason']:
            references += [(8+segment.ch, 0x10), (11, 0xff), (12, 0xff), (13, 0x0f)]
        for register, bits in references:
            add(segment, loss['reason'], 'mute_ssg_interval', kind='interval',
                port=0, register=register, data=segment.registers[register],
                bits=bits, use_feature=True)
    for ch in dropped_ssg:
        for segment in ssg.segments[ch]:
            if segment.vgmticks_end > segment.vgmticks and segment.mode and (segment.volume or segment.envelope_enabled):
                add(segment, 'existing_opm_channel_allocation_limit', 'omit_ssg_part',
                    kind='interval', port=0, register=8+ch, data=segment.amplitude_register, use_feature=True)
    for item in pcm_omissions:
        row = {k: None for k in columns}
        row.update({k: item[k] for k in columns if k in item})
        row['source_part'] = 'PCM'
        rows.append(row)
    return rows


def dump(rows, outdir, stem, *, force, prefix="opna_force"):
    out = Path(outdir)
    csv_name = stem + '.' + prefix + '.skipped_registers.csv'
    columns = list(rows[0]) if rows else [
        'source_event_id','original_event_id','loop_iteration','original_vgmticks','address',
        'vgmticks','vgmticks_end','chip_instance','port','register','data','source_ch','source_part',
        'source_segment_id','reason','action','record_kind','affected_bits',
        'trigger_port','trigger_register','trigger_data']
    with (out/csv_name).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter((r['port'],r['register'],r['action'],r['reason'],r['record_kind'],r['affected_bits'],r.get('source_part','shared'))
                     for r in rows)
    summary = [dict(port=p,register=r,action=a,reason=why,record_kind=kind,affected_bits=bits,source_part=part,count=n)
               for (p,r,a,why,kind,bits,part),n in sorted(counts.items(),
                   key=lambda item: (-1 if item[0][0] is None else item[0][0],
                                     -1 if item[0][1] is None else item[0][1],
                                     item[0][2], item[0][3], item[0][4],
                                     -1 if item[0][5] is None else item[0][5]))]
    result = dict(enabled=force, decision_count=len(rows), registers_csv=csv_name,
                  skipped_registers=summary, source_ir_unchanged=True,
                  count_scope='projection decisions including expanded-loop occurrences; write/interval/part counts are distinct',
                  records=rows)
    (out/(stem+'.'+prefix+'.skipped_registers.json')).write_text(
        json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def format_register_list(summary):
    lines = ['Forced projection: omitted/approximated HW register effects',
             '  Part     Port  Register  Bits  Action                    Count(kind)  Reason']
    if not summary:
        return lines + ['  none']
    for row in summary:
        port = '?' if row['port'] is None else str(row['port'])
        register = '?' if row['register'] is None else f"0x{row['register']:02X}"
        bits = '--' if row['affected_bits'] is None else f"{row['affected_bits']:02X}"
        lines.append(f"  {row.get('source_part', 'shared'):<7}  {port:<4}  {register:<8}  {bits:<4}  {row['action']:<24}  "
                     f"{row['count']}({row['record_kind']})  {row['reason']}")
    return lines


def capacity_lines(outdir, stem):
    path = Path(outdir)/(stem+'.pcm.force_capacity.json')
    if not path.is_file():
        return []
    report = json.loads(path.read_text(encoding='utf-8'))
    summary = report['summary']
    lines = [f"Forced PCM capacity: retained {len(summary['retained_sample_ids'])} samples; "
             f"omitted {len(summary['omitted_sample_ids'])} samples / "
             f"{summary['omitted_playback_count']} playbacks"]
    seen = set()
    for row in report['rows']:
        if row['action'] != 'omit' or row['sample_id'] in seen:
            continue
        seen.add(row['sample_id'])
        lines.append(f"  sample {row['sample_id']}: {row['cap_kind']}; "
                     f"actual {row['actual']}, limit {row['limit']}; {row['reason']}")
    lines.append('  Details: '+path.name.replace('.json','.csv'))
    return lines


def pcm_artifact_lines(outdir, stem):
    folder = Path(outdir)
    timing = folder/(stem+'.pcm.timing.json')
    if not timing.is_file():
        return []
    report = json.loads(timing.read_text(encoding='utf-8'))
    pdx = report.get('pcm_pdx_file')
    if not pdx:
        return ['PDX: not generated (no retained PCM samples)',
                'MDX: not generated by PCM stage; FM MML is available for compilation']
    lines = []
    if (folder/pdx).is_file():
        lines.append('PDX: '+str(folder/pdx))
    if (folder/(stem+'.mdx')).is_file():
        lines.append('MDX: '+str(folder/(stem+'.mdx')))
    return lines


def force_console_lines(outdir, stem):
    folder = Path(outdir)
    lines = []
    for prefix in ('opna_force', 'psg_force'):
        registry = folder/(stem+'.'+prefix+'.skipped_registers.json')
        if registry.is_file():
            summary = json.loads(registry.read_text(encoding='utf-8'))
            lines.extend(format_register_list(summary['skipped_registers']))
            lines.append('Register details: '+summary['registers_csv'])
    lines.extend(capacity_lines(folder, stem))
    return lines
