"""Inspectable target omissions for partial MUCOM conversion."""
import csv
import json
from pathlib import Path

FIELDS = ('part', 'source_chip', 'port', 'register', 'bits', 'action', 'reason',
          'source_start_vgmticks', 'source_end_vgmticks', 'source_event_ids',
          'source_gate_ids', 'actual', 'limit', 'unit', 'details_json')


def _row(value):
    row = dict.fromkeys(FIELDS, '')
    row.update((key, value[key]) for key in FIELDS if key in value)
    row['part'] = value.get('part', value.get('source_part', 'PCM' if value.get('source_kind') else 'shared'))
    row['source_chip'] = value.get('source_chip', value.get('source_kind', ''))
    row['bits'] = value.get('bits', value.get('affected_bits', ''))
    row['source_start_vgmticks'] = value.get('source_start_vgmticks', value.get('start_vgmticks', value.get('vgmticks', '')))
    row['source_end_vgmticks'] = value.get('source_end_vgmticks', value.get('end_vgmticks', value.get('vgmticks_end', '')))
    row['source_event_ids'] = value.get('source_event_ids', [value['source_event_id']] if value.get('source_event_id') is not None else [])
    row['source_gate_ids'] = value.get('source_gate_ids', [])
    row['details_json'] = dict(value)
    return {key:('' if item is None else item) for key,item in row.items()}


def dump(omissions, outdir, stem, enabled, *, force_requested=True):
    root = Path(outdir)
    root.mkdir(parents=True, exist_ok=True)
    rows = [_row(value) for value in omissions]
    csv_path, json_path = root / (stem+'.mucom.force.csv'), root / (stem+'.mucom.force.json')
    summary = dict(enabled=bool(enabled), force_requested=bool(force_requested), omission_count=len(rows), omissions=rows,
                   csv=csv_path.name, json=json_path.name)
    with csv_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({key:json.dumps(value, ensure_ascii=False) if isinstance(value,(list,dict)) else value
                          for key,value in row.items()} for row in rows)
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    return summary


def format_lines(summary):
    if not summary.get('enabled'):
        return []
    rows = summary.get('omissions', [])
    label = 'Forced MUCOM projection' if summary.get('force_requested',True) else 'MUCOM explicit channel omission'
    lines = [label+': '+str(len(rows))+' omitted target operations']
    if rows:
        lines.append('  Part  Source  Port  Register  Bits  Action  Count  Reason  Capacity(actual/limit)')
        groups = {}
        for row in rows:
            key = tuple('' if row.get(field) is None else row.get(field, '') for field in ('part','source_chip','port','register','bits','action','reason','actual','limit','unit'))
            groups[key] = groups.get(key, 0) + 1
        for (part, chip, port, reg, bits, action, reason, actual, limit, unit), count in groups.items():
            if isinstance(reg, int):
                reg = f'0x{reg:02X}'
            amount = str(actual)+'/'+str(limit)+' '+str(unit) if actual != '' and limit != '' else ''
            lines.append('  '+'  '.join(str(value) for value in
                (part,chip,port,reg,bits,action,count,reason,amount)))
        lines.append('Omission details: '+str(summary.get('csv','')))
    return lines


def console_lines(outdir, stem):
    path = Path(outdir) / (stem+'.mucom.force.json')
    if not path.is_file():
        return []
    summary = json.loads(path.read_text(encoding='utf-8'))
    return format_lines(summary)
