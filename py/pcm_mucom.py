"""Explicit MSM6258/SegaPCM source audio -> derived MUCOM ADPCM-B assets.

Native source evidence is dumped independently. Packaging boundaries and fresh
ADPCM-B predictors are target losses, never invented native decoder resets.
"""
from dataclasses import dataclass
from fractions import Fraction
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
from types import SimpleNamespace

from mucom_aux import TARGET_CLOCK_HZ, NOTES, _pcm_spelling

DELTA_N = round(Fraction(15625 * 144 * 65536, TARGET_CLOCK_HZ))
TARGET_RATE = Fraction(TARGET_CLOCK_HZ * DELTA_N, 144 * 65536)
MAX_SAMPLE_BYTES = 65536  # Four-byte units; length-minus-four must fit u16.
MAX_BANK_BYTES = 262144
MAX_SOURCE_FRAMES = 32_000_000


class ProjectionError(ValueError):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}



@dataclass(frozen=True)
class PackagingSegment:
    segment_id: str
    playback_id: str
    start_vgmticks: int
    end_vgmticks: int
    independently_playable: bool = False
    repeat: bool = False
    issues: tuple = ()
    termination: str = 'projected_chunk_boundary'


@dataclass(frozen=True)
class NormalizationView:
    source_end_vgmticks: int
    segments: tuple
    controls: tuple = ()


@dataclass(frozen=True)
class TargetPcmProjection:
    items: tuple
    bank: bytes
    bindings: list
    normalization_view: NormalizationView
    report: dict

    def build_units(self):
        from opna_muc import Unit
        result = []
        for item, row in zip(self.items, self.report['mapping']):
            start, end, prefix, identity = item
            controls, note = prefix.rsplit(' ', 1)
            ids, events = (row['segment_id'],), tuple(row['source_event_ids'])
            semantic = ('derived_adpcm_b', row['target_sha256'], DELTA_N,
                        row['target_pan'], 249, 'independent_target_predictor')
            result.append(Unit('K', start, end, 'pcm', ((start,end,controls,note),),
                semantic, ids, events, ((end-start,semantic),), ((ids,events),)))
        return result


def _next(predictor, step, nibble):
    delta = (2 * (nibble & 7) + 1) * step // 8
    predictor = max(-32768, min(32767, predictor + (-delta if nibble & 8 else delta)))
    step = max(127, min(24576, step * (57,57,57,57,77,102,128,153)[nibble & 7] // 64))
    return predictor, step


def encode_adpcm_b(values):
    """High nibble first; padding encodes zero PCM with the continuing predictor."""
    predictor, step, pending = 0, 127, None
    packed = bytearray()
    valid = len(values)
    padded = max(8, ((valid + 7) // 8) * 8)
    for index in range(padded):
        desired = values[index] if index < valid else 0
        candidates = [_next(predictor, step, nibble) for nibble in range(16)]
        nibble = min(range(16), key=lambda n: abs(candidates[n][0] - desired))
        predictor, step = candidates[nibble]
        if pending is None:
            pending = nibble << 4
        else:
            packed.append(pending | nibble)
            pending = None
    return bytes(packed), padded-valid


def _sample(samples, position):
    if not samples:
        return 0
    position = max(0.0, min(len(samples)-1, position))
    left = int(position)
    fraction = position - left
    return round(samples[left] * (1-fraction) + samples[min(left+1,len(samples)-1)] * fraction)


def _chunk_boundaries(start, end, loop_start):
    # Address capacity, not the inherited PDX eight-second packaging policy.
    maximum = max(1, int(Fraction(MAX_SAMPLE_BYTES*2-8, 1) / TARGET_RATE * 44100))
    while start < end:
        stop = min(end, start + maximum)
        if loop_start is not None and start < loop_start < stop:
            stop = loop_start
        yield start, stop
        start = stop


def _msm_source(raw, outdir, stem):
    from msm6258 import analyze
    from opna_adpcm import _msm_next
    native = analyze(raw)
    native.dump(outdir, stem)
    if native.issues and native.playbacks:
        raise ProjectionError('Unresolved MSM6258 source controls: '+', '.join(sorted({i.code for i in native.issues})))
    if native.options & 8:
        raise ProjectionError('MSM6258 12-bit output is not supported by this 10-bit decoder model')
    samples = {sample.sample_id: sample for sample in native.samples}
    intervals, decoded_frames = [], 0
    for playback in native.playbacks:
        if (playback.chip_instance or not playback.independently_playable
                or not playback.delivery_cadence_compatible or not playback.decoder_reset_known
                or playback.issues or playback.sample_id not in samples):
            raise ProjectionError(f'MSM6258 playback {playback.playback_id} cannot be independently decoded: '
                                  + ', '.join(playback.issues or ('unknown reset, cadence or sample',)))
        held_pan = [c for c in native.controls if c.register == 2
            and playback.start_vgmticks <= c.vgmticks < playback.end_vgmticks
            and c.pan != playback.pan]
        if held_pan:
            raise ProjectionError('MSM6258 held-pan changes require an explicit live-pan projection')
        source = samples[playback.sample_id]
        if source.codec != 'msm6258-adpcm4-low-first':
            raise ProjectionError('Only 4-bit low-nibble-first MSM6258 input is supported')
        decoded_frames += len(source.encoded_bytes)*2
        if decoded_frames > MAX_SOURCE_FRAMES:
            raise ProjectionError('MSM6258 decoded waveform exceeds the bounded frame capacity')
        signal, step, pcm = -2, 0, []
        for byte in source.encoded_bytes:
            for nibble in (byte & 15, byte >> 4):
                signal, step = _msm_next(signal, step, nibble)
                pcm.append(signal*64)
        start = playback.start_vgmticks
        exhaustion = Fraction(start) + Fraction(len(pcm)*44100,1)/playback.rate_hz
        end = min(playback.end_vgmticks, math.ceil(exhaustion))
        if end > start:
            intervals.append(dict(start=start,end=end,samples=pcm,rate=playback.rate_hz,
                origin=start,pan=(3,2,1,0)[playback.pan],source_ids=(playback.playback_id,),
                events=(playback.first_source_event_id,playback.last_source_event_id),
                exhaustion_clipped=exhaustion<playback.end_vgmticks))
    return native.source_end_vgmticks, intervals, dict(
        source_kind='msm6258', source_gate_count=len(native.playbacks),
        assumptions=['Nominal continuous low-nibble-first consumption from a known -2/0 reset; supplied bytes are not observed consumption'],
        losses=['MSM6258 decoded values are requantized to Yamaha ADPCM-B',
                'Available encoded sample exhaustion clips the projected sounding interval'],
        source_controls_retained=True)


def _sega_source(raw, outdir, stem, loop_start):
    from vgm_io import read_vgm_header
    from vgm_timing import command_times
    from segapcm import SegaPcmTrace
    from segapcm_playback import render
    trace = SegaPcmTrace(read_vgm_header(raw))
    end = 0
    for event_id,event in enumerate(command_times(raw)):
        cmd, pos = event.command, event.address+1
        end = event.vgmticks+event.wait_samples
        if cmd == 0xc0:
            trace.write(event_id,event,int.from_bytes(raw[pos:pos+2],'little'),raw[pos+2])
        elif cmd == 0x67 and raw[pos+1] == 0x80:
            length = int.from_bytes(raw[pos+2:pos+6],'little')
            trace.rom_block(event_id,event,length,raw[pos+6:pos+6+(length & 0x7fffffff)])
    trace.dump(outdir,stem,end)
    native = render(trace,end,loop_start,max_frames=MAX_SOURCE_FRAMES)
    native.dump(outdir,stem)
    spans = []
    for gate in sorted(native.gates,key=lambda g:(g.start_vgmticks,g.end_vgmticks)):
        start,stop = max(0,math.floor(gate.start_vgmticks)),min(end,math.ceil(gate.end_vgmticks))
        if stop <= start:
            continue
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1],stop)
        else:
            spans.append([start,stop])
    # Source amplitude is the native Sega model's summed byte*volume units.
    # The MSM path above has its separate nominal signed-16 decoder scale.
    mono = [round((native.samples[i]+native.samples[i+1])/2)
            for i in range(0,len(native.samples),2)]
    intervals = []
    for start,stop in spans:
        gates = [g for g in native.gates if g.start_vgmticks<stop and g.end_vgmticks>start]
        intervals.append(dict(start=start,end=stop,samples=mono,rate=native.sample_rate,
            origin=0,pan=3,source_ids=tuple(g.gate_id for g in gates),
            events=tuple(dict.fromkeys(g.start_source_event_id for g in gates)),exhaustion_clipped=False))
    losses = ['Native stereo is averaged to one mono PCM lane',
              'Fractional native gate boundaries are rounded outward',
              'Native mixed waveform is requantized to Yamaha ADPCM-B']
    if loop_start is not None:
        losses.append('One recorded source traversal is frozen; later loop decoder phase may differ')
    if native.missing_rom_reads:
        losses.append(f'Missing ROM was modeled as neutral PCM: {native.missing_rom_reads} reads')
    return end,intervals,dict(source_kind='segapcm',source_gate_count=len(native.gates),
        assumptions=[native.reset_profile,*native.issues],losses=losses,
        missing_rom_reads=native.missing_rom_reads)


def _project(end, intervals, source_report, loop_start):
    if loop_start is not None and not 0 <= loop_start < end:
        raise ProjectionError('PCM song-loop boundary is outside the source timeline')
    header,payload = bytearray(1024),bytearray()
    identities, bindings, items, mapping, segments = {},[],[],[],[]
    octave,note,detune = _pcm_spelling(SimpleNamespace(rate_num=TARGET_RATE.numerator,rate_den=TARGET_RATE.denominator))
    previous_end = 0
    for interval in intervals:
        if interval['start'] < previous_end:
            raise ProjectionError('Overlapping MSM6258 intervals require an explicit mix model')
        previous_end = interval['end']
        for start,stop in _chunk_boundaries(interval['start'],interval['end'],loop_start):
            count = max(1,round(Fraction(stop-start,44100)*TARGET_RATE))
            pcm,clipped = [],0
            initial = float(Fraction(start-interval['origin'],44100)*interval['rate'])
            step = float(interval['rate']/TARGET_RATE)
            for index in range(count):
                position = initial+index*step
                value = _sample(interval['samples'],position)
                clipped += not -32768 <= value <= 32767
                pcm.append(max(-32768,min(32767,value)))
            encoded,padding = encode_adpcm_b(pcm)
            digest = hashlib.sha256(encoded).hexdigest()
            key = (digest,encoded)
            slot = identities.get(key)
            if slot is None:
                context = dict(source_kind=source_report['source_kind'],
                    target_chunk_index=len(mapping), attempted_slot=len(bindings)+1,
                    source_start_vgmticks=start, source_end_vgmticks=stop,
                    source_start_seconds=start/44100, source_end_seconds=stop/44100,
                    source_gate_ids=list(interval['source_ids']),
                    source_event_ids=list(interval['events']),
                    existing_distinct_samples=len(bindings), sample_encoded_bytes=len(encoded),
                    sample_length_field=len(encoded)-4, valid_samples=count, padding_samples=padding,
                    existing_bank_payload_bytes=len(payload),
                    attempted_bank_payload_bytes=len(payload)+len(encoded),
                    bank_header_bytes=len(header), completed_chunks=list(mapping))

                def reject_capacity(code, actual, limit, unit, requirement='maximum'):
                    detail = dict(context, code=code, actual=actual, limit=limit,
                                  unit=unit, requirement=requirement)
                    description = (f'Derived PCM capacity [{code}]: actual={actual} {unit}; '
                        f'{requirement}={limit} {unit}; attempted slot={len(bindings)+1}; '
                        f'source samples={start}..{stop}; '
                        f'existing payload={len(payload)} bytes; candidate={len(encoded)} bytes '
                        '(bank payload excludes the 1024-byte header)')
                    raise ProjectionError(description, detail)

                if len(bindings) >= 32:
                    reject_capacity('sample_slot_count',len(bindings)+1,32,'slots')
                if len(encoded)-4 > 65535:
                    reject_capacity('sample_length_field',len(encoded)-4,65535,'bytes')
                if len(encoded)%4:
                    reject_capacity('sample_alignment',len(encoded)%4,0,'remainder bytes',
                                    'required remainder modulo 4')
                if len(payload)+len(encoded)>MAX_BANK_BYTES:
                    reject_capacity('bank_payload_bytes',len(payload)+len(encoded),MAX_BANK_BYTES,'bytes')
                slot = len(bindings)+1
                identities[key] = slot
                offset = (slot-1)*32
                name = f'DERIVED_{slot}'.encode('ascii')
                header[offset:offset+len(name)] = name
                header[offset+26] = 255
                struct.pack_into('<HH',header,offset+28,len(payload)//4,len(encoded)-4)
                bindings.append(dict(slot=slot,start_units=len(payload)//4,encoded_length=len(encoded),
                    target_sha256=digest,origin='derived_target_adpcm_b',codec='yamaha-adpcm-b-high-first'))
                payload.extend(encoded)
            segment_id = f"{source_report['source_kind']}:target:{len(mapping)}"
            prefix = f'@{slot} vm0 V-4 v249 p{interval["pan"]} o{octave} D{detune} {NOTES[note]}'
            items.append((start,stop,prefix,segment_id))
            segments.append(PackagingSegment(segment_id,segment_id,start,stop))
            mapping.append(dict(segment_id=segment_id,start_vgmticks=start,end_vgmticks=stop,
                source_gate_ids=list(interval['source_ids']),source_event_ids=list(interval['events']),
                target_slot=slot,target_sha256=digest,target_pan=interval['pan'],
                valid_samples=count,padding_samples=padding,clipped_samples=clipped,
                target_rate_num=TARGET_RATE.numerator,target_rate_den=TARGET_RATE.denominator,
                valid_duration_error_vgmticks=float(Fraction(count*44100,1)/TARGET_RATE-(stop-start)),
                exhaustion_clipped=interval['exhaustion_clipped']))
    report = dict(source_report,target_codec='yamaha-adpcm-b-high-first',target_delta_n=DELTA_N,
        target_rate_num=TARGET_RATE.numerator,target_rate_den=TARGET_RATE.denominator,
        target_level=249,target_level_gain=249/255,
        target_reset='Independent target predictor0/step127; no native reset claimed',
        source_ir_unchanged=True,normalization_omission_safe=False,mapping=mapping)
    if loop_start is not None:
        report['losses'].append('Target song loop restarts independently encoded chunks; native decoder phase is not reconstructed across infinite traversals')
    report['losses'] = [*report['losses'],'Linear resampling without antialias filtering',
        'Fresh target decoder at each packaging boundary; seams may be audible',
        'Native driver level249/255 attenuation; no gain compensation',
        'Four-byte alignment pads encoded zero-target samples, not zero bytes']
    return TargetPcmProjection(tuple(items),bytes(header+payload) if items else b'',bindings,
        NormalizationView(end,tuple(segments)),report)


def project_input(raw, *, kind, outdir, stem, loop_start=None):
    """Preserve native passes, then produce derived target PCM bank and K units."""
    root = Path(outdir)
    root.mkdir(parents=True,exist_ok=True)
    capacity_path = root / f'{stem}.pcm_mucom_capacity.json'
    capacity = dict(schema_version=1, source_kind=kind,
        source_sha256=hashlib.sha256(raw).hexdigest(), status='in_progress', stage='source_analysis',
        limits=dict(sample_slots=32, sample_encoded_bytes=MAX_SAMPLE_BYTES,
                    sample_length_field=65535, alignment_bytes=4,
                    bank_payload_bytes=MAX_BANK_BYTES, bank_header_bytes=1024),
        note='Capacity limits refer to this MUCOM bank profile; payload excludes its header')

    def save_capacity():
        capacity_path.write_text(json.dumps(capacity,indent=2)+'\n',encoding='utf-8')

    save_capacity()
    try:
        if kind == 'msm6258':
            end,intervals,report = _msm_source(raw,outdir,stem)
        elif kind == 'segapcm':
            end,intervals,report = _sega_source(raw,outdir,stem,loop_start)
        else:
            raise ProjectionError(f'Unsupported PCM interoperability source {kind}')
        capacity.update(stage='target_encoding_and_bank_binding')
        save_capacity()
        projection = _project(end,intervals,report,loop_start)
    except ProjectionError as error:
        capacity.update(status='blocked', detail=str(error), failure=error.diagnostics)
        save_capacity()
        raise ProjectionError(f'{error}; PCM capacity report: {capacity_path}',capacity) from error
    capacity.update(status='accepted', distinct_samples=len(projection.bindings),
        bank_payload_bytes=max(0,len(projection.bank)-1024),
        largest_sample_encoded_bytes=max((b['encoded_length'] for b in projection.bindings),default=0))
    save_capacity()
    path = root / f'{stem}.pcm_mucom_projection.json'
    path.write_text(json.dumps(projection.report,indent=2)+'\n',encoding='utf-8')
    mapping = projection.report['mapping']
    with (root/f'{stem}.pcm_mucom_mapping.csv').open('w',newline='',encoding='utf-8') as stream:
        fields = tuple(mapping[0]) if mapping else ('segment_id',)
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(mapping)
    return projection
