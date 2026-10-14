"""Independent MDX/PDX requested-performance expectations (standard9 by default).

No converter or sound-log reader is used. MDX ticks and release requests do not
assert VGM transfer cadence, decoder reset/consumption, or physical sound end.
Unsupported commands stop that track with an explicit incomplete diagnostic.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct


class ReferenceError(ValueError):
    """Malformed or unsupported reference structure."""


def _word(data, offset, signed=False):
    if offset < 0 or offset + 2 > len(data):
        raise ReferenceError('word outside file at {}'.format(offset))
    return int.from_bytes(data[offset:offset + 2], 'big', signed=signed)


def _gate(duration, q):
    if q <= 8:
        return (duration - 1) * q // 8 + 1
    if q >= 128:
        return max(1, duration - (256 - q))
    raise ReferenceError('unsupported gate parameter {}'.format(q))


def read_pdx(data):
    """Read all 96 standard PDX slots; sample bytes remain external at export."""
    if len(data) < 768:
        raise ReferenceError('standard PDX needs a 768-byte table')
    slots, samples, payloads, seen = [], [], {}, {}
    for slot in range(96):
        offset, length = struct.unpack_from('>II', data, slot * 8)
        valid = length == 0 or (offset >= 768 and offset + length <= len(data))
        row = dict(bank=0, slot=slot, table_offset=slot * 8,
                   offset=offset, length=length, standard_pcm1_length=length & 65535,
                   length_high_word=length >> 16, empty=length == 0,
                   bounds_valid=valid, sample_id=None)
        if length and valid:
            payload = data[offset:offset + length]
            digest = hashlib.sha256(payload).hexdigest()
            if digest not in seen:
                sample_id = len(samples)
                seen[digest] = sample_id
                filename = 'samples/{:03d}.adpcm'.format(sample_id)
                samples.append(dict(sample_id=sample_id,
                                    codec='msm6258-adpcm4-low-first',
                                    sha256=digest, byte_length=length,
                                    nominal_nibbles=length * 2,
                                    encoded_file=filename))
                payloads[filename] = payload.hex()
            row['sample_id'] = seen[digest]
            row['sha256'] = digest
        slots.append(row)
    return dict(slots=slots, samples=samples), payloads


def _track(data, start, limit, label, max_commands, pcm8=False):
    is_pcm = label in 'PQRSTUVW'
    commands, events, controls, diagnostics = [], [], [], []
    state = dict(q=8, q_origin='standard_driver_profile_default', voice=None,
                 bank=0, bank_origin='reference_initial_bank_zero_assumption' if pcm8 else 'standard_pcm1_profile', pan=None,
                 volume=None, detune=None, key_delay=None, rate=None)
    repeats, tick, pc, held = [], 0, start, False
    finish = None
    lengths = {0xff: 2, 0xfe: 3, 0xfd: 2, 0xfc: 2, 0xfb: 2,
               0xfa: 1, 0xf9: 1, 0xf8: 2, 0xf7: 1, 0xf6: 3,
               0xf5: 3, 0xf4: 3, 0xf3: 3, 0xf2: 3, 0xf0: 2, 0xed: 2, 0xe9: 2,
               0xe8: 1}
    pending_hold = False
    try:
        for execution in range(max_commands):
            if not start <= pc < limit:
                raise ReferenceError('command destination outside track: {}'.format(pc))
            op = data[pc]
            size = 1 if op < 128 else 2 if op < 224 else lengths.get(op)
            if op in (0xec, 0xeb, 0xea):
                if pc + 1 >= limit:
                    raise ReferenceError('truncated LFO command')
                size = 2 if data[pc + 1] in (128, 129) else 6
            if op == 0xf1:
                if pc + 1 >= limit:
                    raise ReferenceError('truncated F1')
                size = 2 if data[pc + 1] == 0 else 3
            row = dict(track=label, execution=execution, file_offset=pc,
                       track_offset=pc - start, tick=tick, opcode=op,
                       opcode_hex='{:02x}'.format(op), length=size,
                       operands_hex='')
            commands.append(row)
            if size is None:
                raise ReferenceError('unsupported opcode 0x{:02x} at {}'.format(op, pc))
            if pc + size > limit:
                raise ReferenceError('truncated command at {}'.format(pc))
            operands = data[pc + 1:pc + size]
            row['operands_hex'] = operands.hex()
            after = pc + size
            if op < 128:
                duration = op + 1
                events.append(dict(track=label, event_id=len(events), kind='rest',
                                   file_offset=pc, opcode=op, start_tick=tick,
                                   duration_ticks=duration, end_tick=tick + duration,
                                   release_intent='rest', physical_stop_verified=False))
                tick += duration
                held = False
                pending_hold = False
            elif op < 224:
                duration = operands[0] + 1
                gate = _gate(duration, state['q'])
                event = dict(track=label, event_id=len(events), kind='note',
                             file_offset=pc, opcode=op, start_tick=tick,
                             duration_ticks=duration, end_tick=tick + duration,
                             gate_ticks=gate, gate_end_tick=tick + gate,
                             q=state['q'], q_origin=state['q_origin'],
                             hold=pending_hold, continuation=held,
                             retrigger_intent=not held,
                             release_intent='held' if pending_hold else 'gate_expiry',
                             release_tick=None if pending_hold else tick + gate,
                             key_delay_ticks=state['key_delay'],
                             pan=state['pan'], volume_encoded=state['volume'],
                             decoder_reset_known=None, consumed_nibbles=None,
                             physical_stop_verified=False)
                if is_pcm:
                    event.update(bank=state['bank'], bank_origin=state['bank_origin'],
                                 slot=op - 128, rate_code=state['rate'],
                                 sample_id=None, nominal_sample_nibbles=None,
                                 nominal_sample_exhaustion_seconds=None)
                else:
                    event.update(midi_note=op - 128 + 3, voice=state['voice'],
                                 detune_64ths=state['detune'])
                events.append(event)
                tick += duration
                held, pending_hold = pending_hold, False
            elif op == 0xf1:
                if size == 2:
                    finish = dict(kind='finite_end', tick=tick, file_offset=pc,
                                  physical_stop_verified=False)
                else:
                    destination = after + _word(data, pc + 1, True)
                    if not start <= destination < limit:
                        raise ReferenceError('song loop destination outside track')
                    visited = [r for r in commands if r['file_offset'] == destination]
                    if not visited:
                        raise ReferenceError('song loop destination is not an executed command')
                    loop_start = visited[0]['tick']
                    finish = dict(kind='song_loop', tick=tick, file_offset=pc,
                                  destination_file_offset=destination,
                                  destination_track_offset=destination - start,
                                  loop_start_tick=loop_start,
                                  cycle_ticks=tick - loop_start,
                                  expansion='intro_and_one_song_cycle')
                break
            else:
                control = dict(row, kind={0xff: 'tempo', 0xfe: 'opm_register',
                    0xfd: 'bank' if is_pcm else 'voice', 0xfc: 'pan',
                    0xfb: 'volume', 0xfa: 'volume_decrease', 0xf9: 'volume_increase',
                    0xf8: 'gate', 0xf7: 'hold', 0xf6: 'repeat_start',
                    0xf5: 'repeat_end', 0xf4: 'repeat_escape', 0xf3: 'detune',
                    0xf2: 'portamento', 0xe8: 'pcm8_enable',
                    0xf0: 'key_delay', 0xed: 'pcm_rate' if is_pcm else 'noise',
                    0xec: 'pitch_lfo', 0xeb: 'amplitude_lfo', 0xea: 'opm_lfo',
                    0xe9: 'lfo_key_delay'}[op])
                controls.append(control)
                if op in (0xec, 0xeb, 0xea, 0xe9, 0xf2, 0xe8):
                    control['values'] = list(operands)
                    control['interpretation'] = 'encoded_control_only; modulation trajectory unverified'
                    if op == 0xe8:
                        control['interpretation'] = 'encoded PCM8-enable only; physical playback mode unverified'
                elif op in (0xff, 0xfe):
                    control['values'] = list(operands)
                elif op == 0xfd:
                    state['bank' if is_pcm else 'voice'] = operands[0]
                    if is_pcm:
                        state['bank_origin'] = 'encoded_command'
                    control['value'] = operands[0]
                elif op in (0xfc, 0xfb, 0xf0, 0xed):
                    state[{0xfc: 'pan', 0xfb: 'volume', 0xf0: 'key_delay', 0xed: 'rate'}[op]] = operands[0]
                    control['value'] = operands[0]
                elif op in (0xfa, 0xf9):
                    # Encoded relative intent is certain; saturation/TL conversion
                    # and initial implicit level are not reconstructed here.
                    state['volume'] = None
                    control['delta_intent'] = -1 if op == 0xfa else 1
                elif op == 0xf8:
                    _gate(1, operands[0])
                    state['q'] = operands[0]
                    state['q_origin'] = 'encoded_command'
                    control['value'] = operands[0]
                elif op == 0xf7:
                    pending_hold = True
                elif op == 0xf3:
                    state['detune'] = _word(data, pc + 1, True)
                    control['value'] = state['detune']
                elif op == 0xf6:
                    if operands[0] == 0:
                        raise ReferenceError('zero repeat count is outside supported profile')
                    repeats.append(dict(start=pc, body=after, remaining=operands[0]))
                    control['count'] = operands[0]
                elif op == 0xf5:
                    destination = after + _word(data, pc + 1, True)
                    control['destination_file_offset'] = destination
                    if not repeats or destination != repeats[-1]['body']:
                        raise ReferenceError('repeat end does not match active repeat body')
                    repeats[-1]['remaining'] -= 1
                    control['remaining'] = repeats[-1]['remaining']
                    if repeats[-1]['remaining']:
                        after = destination
                    else:
                        repeats.pop()
                elif op == 0xf4:
                    # F4 points to the two-byte F5 operand, not its opcode.
                    operand_at = after + _word(data, pc + 1, True)
                    destination = operand_at + 2
                    control['destination_file_offset'] = destination
                    if not repeats or operand_at <= after or operand_at + 2 > limit or data[operand_at - 1] != 0xf5:
                        raise ReferenceError('repeat escape does not point to repeat-end operand')
                    if destination + _word(data, operand_at, True) != repeats[-1]['body']:
                        raise ReferenceError('repeat escape does not match active repeat')
                    control['taken'] = repeats[-1]['remaining'] == 1
                    if control['taken']:
                        after = destination
                        repeats.pop()
            pc = after
        else:
            raise ReferenceError('expanded command bound exceeded')
    except ReferenceError as error:
        diagnostics.append(dict(track=label, file_offset=pc, tick=tick,
                                code='incomplete_reference', detail=str(error)))
    return dict(track=label, start_file_offset=start, end_file_offset=limit,
                complete=not diagnostics and finish is not None,
                duration_ticks=tick, termination=finish, diagnostics=diagnostics,
                commands=commands, events=events, controls=controls)


def read_mdx(data, *, max_commands=1000000, allow_pcm8=False):
    title_end = data.find(b'\r\n\x1a')
    if title_end < 0:
        raise ReferenceError('missing MDX title terminator')
    pdx_start = title_end + 3
    pdx_end = data.find(b'\x00', pdx_start)
    if pdx_end < 0:
        raise ReferenceError('missing PDX-name terminator')
    base = pdx_end + 1
    initial_offsets = [_word(data, base + 2 * i) for i in range(10)]
    first = min((offset for offset in initial_offsets if offset not in (0, 65535)), default=0)
    track_count = 16 if first == 34 and allow_pcm8 else 9
    if first != 20 and not (first == 34 and allow_pcm8):
        raise ReferenceError('unsupported header size/minimum data offset {}'.format(first))
    header_end = base + 2 + 2 * track_count
    tone_offset = _word(data, base)
    tone_start = base + tone_offset if tone_offset else None
    starts = [base + _word(data, base + 2 + 2 * i) for i in range(track_count)]
    if (tone_start is not None and not header_end <= tone_start <= len(data)) or any(not header_end <= x < len(data) for x in starts) or len(set(starts)) != len(starts):
        raise ReferenceError('invalid tone/track offsets')
    if tone_start in starts:
        raise ReferenceError('tone and track regions overlap')
    region_starts = starts + ([tone_start] if tone_start is not None else [])
    tracks = []
    for i, start in enumerate(starts):
        limit = min([x for x in region_starts if x > start] + [len(data)])
        tracks.append(_track(data, start, limit, 'ABCDEFGHPQRSTUVW'[i], max_commands,
                             pcm8=track_count == 16))
    tones = []
    tone_end = min([x for x in starts if tone_start is not None and x > tone_start] + [len(data)]) if tone_start is not None else None
    if tone_start is not None and (tone_end - tone_start) % 27:
        raise ReferenceError('tone block is not a multiple of 27 bytes')
    for offset in range(tone_start, tone_end, 27) if tone_start is not None else ():
        raw = data[offset:offset + 27]
        operators = []
        for i, name in enumerate(('M1', 'M2', 'C1', 'C2')):
            a, b, c, d, e, f = [raw[3 + group * 4 + i] for group in range(6)]
            operators.append(dict(operator=name, dt1=a >> 4 & 7, mul=a & 15,
                                  tl=b & 127, ks=c >> 6, ar=c & 31,
                                  ame=d >> 7, d1r=d & 31, dt2=e >> 6,
                                  d2r=e & 31, d1l=f >> 4, rr=f & 15))
        tones.append(dict(file_offset=offset, voice=raw[0], raw_hex=raw.hex(),
                          feedback=raw[1] >> 3 & 7, algorithm=raw[1] & 7,
                          slot_mask=raw[2], operators=operators))
    return dict(title=data[:title_end].decode('shift_jis', errors='replace'),
                pdx_name=data[pdx_start:pdx_end].decode('shift_jis', errors='replace'),
                data_base_file_offset=base, tone_start_file_offset=tone_start,
                tracks=tracks, tones=tones, complete=all(t['complete'] for t in tracks),
                track_layout='pcm8_16' if track_count == 16 else 'standard9',
                decoder_profile_scope='encoded MDX requested timeline; PCM8 runtime unverified' if track_count == 16 else 'standard9_PCM1_requested_events',
                completeness_domain='supported encoded commands and bounded requested events',
                effective_modulation_trajectory_verified=False)


def read_expectations(mdx_path, pdx_path=None, *, max_commands=1000000):
    mdx_path = Path(mdx_path)
    mdx = mdx_path.read_bytes()
    result = dict(schema='mdx-reference-expectations-v1',
                  expectation_domain='MDX requested performance; not observed VGM source evidence',
                  driver_profile='MXDRV2.06+17 standard9_PCM1', clock_domain='MDX ticks',
                  gate_profile_evidence='native InitChannel and SetNoteLengthCommandFunc; q default 8, encoded duration N-1',
                  unknowns=['VGM transfer timestamps/cadence', 'decoder reset',
                            'decoder consumption', 'physical stop', 'acoustic end'],
                  mdx=dict(path=str(mdx_path), sha256=hashlib.sha256(mdx).hexdigest(),
                           byte_length=len(mdx)), **read_mdx(mdx, max_commands=max_commands))
    payloads = {}
    if pdx_path is not None:
        pdx_path = Path(pdx_path)
        pdx = pdx_path.read_bytes()
        parsed, payloads = read_pdx(pdx)
        result['pdx'] = dict(path=str(pdx_path), sha256=hashlib.sha256(pdx).hexdigest(),
                             byte_length=len(pdx), **parsed)
        if any(not s['bounds_valid'] or s['length_high_word'] for s in parsed['slots']):
            result['complete'] = False
            result['pdx']['diagnostics'] = ['out-of-bounds slot or nonstandard PCM1 length high word']
        slots = {(s['bank'], s['slot']): s for s in parsed['slots']}
        samples = {s['sample_id']: s for s in parsed['samples']}
        for track in result['tracks']:
            if track['track'] != 'P':
                continue
            for event in track['events']:
                if event['kind'] != 'note':
                    continue
                binding = slots.get((event['bank'], event['slot']))
                event['binding_status'] = 'unavailable' if binding is None else 'empty' if binding['empty'] else 'valid' if binding['bounds_valid'] else 'out_of_bounds'
                if binding and binding['sample_id'] is not None:
                    sample = samples[binding['sample_id']]
                    event['sample_id'] = sample['sample_id']
                    event['nominal_sample_nibbles'] = sample['nominal_nibbles']
                    # IOCS rate code 0..4 nominal rates; no decoder clock claim.
                    rates = (3906.25, 5208.333333333333, 7812.5, 10416.666666666666, 15625)
                    rate = event['rate_code']
                    if rate is not None and 0 <= rate < len(rates):
                        event['nominal_sample_exhaustion_seconds'] = sample['nominal_nibbles'] / rates[rate]
    result['_encoded_payloads'] = payloads
    return result


def write_expectations(result, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for filename, payload in result.get('_encoded_payloads', {}).items():
        path = outdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes.fromhex(payload))
    serializable = {k: v for k, v in result.items() if k != '_encoded_payloads'}
    (outdir / 'expectations.json').write_text(json.dumps(serializable, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    pcm_expected = dict(
        schema='pcm-ir-reference-expectation-v1',
        variant='decoded-mdx-pdx-requests',
        provenance='direct original MDX commands and PDX table/payload; not converter output',
        expectation_domain='neutral samples plus reference playback requests; not observed source PcmAnalysis',
        clock_domain='MDX ticks; original VGM timeline not captured',
        samples=result.get('pdx', {}).get('samples', []),
        sample_bindings=[dict(bank=s['bank'], slot=s['slot'], sample_id=s['sample_id'])
                         for s in result.get('pdx', {}).get('slots', []) if not s['empty']],
        playback_requests=[e for t in result['tracks'] if t['track'] == 'P'
                           for e in t['events'] if e['kind'] == 'note'],
        termination=[t['termination'] for t in result['tracks'] if t['track'] == 'P'],
        static_decode_complete=result['complete'],
        observed_source_fields=dict(transfer_timestamps=None, source_event_ids=None,
                                   decoder_reset=None, consumed_nibbles=None, physical_stop=None),
        extraction_comparison='not_run', native_playback_verification='unverified')
    (outdir / 'pcm_ir.expected.json').write_text(
        json.dumps(pcm_expected, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tables = {name: [row for t in result['tracks'] for row in t[name]]
              for name in ('commands', 'events', 'controls')}
    tables['pdx_slots'] = result.get('pdx', {}).get('slots', [])
    tables['tones'] = result['tones']
    tables['samples'] = result.get('pdx', {}).get('samples', [])
    for name, rows in tables.items():
        fields = list(dict.fromkeys(key for row in rows for key in row))
        with (outdir / (name + '.csv')).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    return outdir / 'expectations.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mdx')
    parser.add_argument('--pdx')
    parser.add_argument('--outdir', required=True)
    parser.add_argument('--max-commands', type=int, default=1000000)
    args = parser.parse_args()
    result = read_expectations(args.mdx, args.pdx, max_commands=args.max_commands)
    path = write_expectations(result, args.outdir)
    print(json.dumps(dict(path=str(path), complete=result['complete'],
                          tracks=len(result['tracks']),
                          events=sum(len(t['events']) for t in result['tracks']),
                          samples=len(result.get('pdx', {}).get('samples', [])))))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())



