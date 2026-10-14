"""Diagnostic full-bank MDX/PDX structure audit, independent of VGM decoding."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import struct

from mdx_reference_expectations import ReferenceError, read_mdx


def pdx_banks(raw, banks):
    """Bank count is explicit: do not infer tables from arbitrary sample bytes."""
    if not 1 <= banks <= 256 or len(raw) < banks * 768:
        raise ReferenceError('PDX does not contain the requested complete tables')
    table_end = banks * 768
    rows = []
    for index in range(banks * 96):
        offset, length = struct.unpack_from('>II', raw, index * 8)
        valid = not length or table_end <= offset <= len(raw) and offset + length <= len(raw)
        payload = raw[offset:offset + length] if length and valid else b''
        rows.append(dict(bank=index // 96, slot=index % 96,
            offset=offset, length=length, empty=not length, bounds_valid=valid,
            sha256=hashlib.sha256(payload).hexdigest() if length and valid else None))
    return rows


def audit(mdx_path, pdx_path, banks):
    raw, pdx = Path(mdx_path).read_bytes(), Path(pdx_path).read_bytes()
    mdx = read_mdx(raw, allow_pcm8=True)
    slots = pdx_banks(pdx, banks)
    by_slot = {(s['bank'], s['slot']): s for s in slots}
    controls, bindings, endings = [], [], []
    for track in mdx['tracks']:
        for c in track['controls']:
            controls.append(dict(c))
        end = track['commands'][-1]
        endings.append(dict(track=track['track'], complete=track['complete'],
            end_kind=track['termination']['kind'] if track['termination'] else None,
            end_tick=track['duration_ticks'], start_offset=track['start_file_offset'],
            end_offset=track['end_file_offset'], end_opcode=end['opcode_hex'],
            end_operands=end['operands_hex'],
            bytes_after_end=None if end['length'] is None else
                track['end_file_offset'] - (end['file_offset'] + end['length'])))
        if track['track'] not in 'PQRSTUVW':
            continue
        for event in track['events']:
            if event['kind'] != 'note':
                continue
            slot = by_slot.get((event['bank'], event['slot']))
            status = 'missing_bank' if slot is None else 'empty' if slot['empty'] else 'valid' if slot['bounds_valid'] else 'out_of_bounds'
            bindings.append(dict(track=track['track'], event_id=event['event_id'],
                start_tick=event['start_tick'], end_tick=event['end_tick'],
                bank=event['bank'], slot=event['slot'], rate_code=event['rate_code'],
                pan=event['pan'], volume_encoded=event['volume_encoded'],
                q=event['q'], hold=event['hold'], continuation=event['continuation'],
                binding_status=status, sample_bytes=None if slot is None else slot['length'],
                sample_sha256=None if slot is None else slot['sha256']))
    result = dict(schema='mdx-pdx-structure-audit-v1', mdx=str(mdx_path), pdx=str(pdx_path),
        mdx_sha256=hashlib.sha256(raw).hexdigest(), pdx_sha256=hashlib.sha256(pdx).hexdigest(),
        pdx_banks=banks, pdx_bank_count_origin='explicit diagnostic input',
        pdx_table_bytes=banks * 768, pdx_bytes=len(pdx), mdx_bytes=len(raw),
        track_layout=mdx['track_layout'], track_count=len(mdx['tracks']),
        tone_count=len(mdx['tones']), decode_complete=mdx['complete'],
        pcm_mode_commands=[c for c in controls if c['opcode_hex'] == 'e8'],
        endings=endings, pdx_slots=slots, pcm_bindings=bindings, controls=controls,
        invalid_pdx_slots=sum(not s['bounds_valid'] for s in slots),
        invalid_pcm_bindings=sum(b['binding_status'] != 'valid' for b in bindings),
        pcm_rates=dict(Counter(str(b['rate_code']) for b in bindings)),
        pcm_banks_used=dict(Counter(str(b['bank']) for b in bindings)),
        native_stopping='separate user listening evidence; not inferred',
        pdx_sample_codec='Not encoded in the PDX table; depends on MDX playback mode/rate')
    return result


def export(result, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / 'audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    for name in ('pdx_slots', 'pcm_bindings', 'controls', 'endings'):
        rows = result[name]
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (outdir / (name + '.csv')).open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mdx', type=Path)
    parser.add_argument('pdx', type=Path)
    parser.add_argument('--pdx-banks', type=int, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.mdx, args.pdx, args.pdx_banks)
    export(result, args.outdir)
    print(json.dumps({k: result[k] for k in ('track_count', 'pdx_banks', 'invalid_pdx_slots',
        'invalid_pcm_bindings', 'pcm_rates', 'pcm_banks_used', 'decode_complete')}))


if __name__ == '__main__':
    main()
