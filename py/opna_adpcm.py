"""OPNA ADPCM-B ROM evidence and an explicit, lossy MSM6258 target IR.

This is not a YM2608 emulator. Native end/rate estimates use the nominal
nibble clock; target decoding omits the chip's prefetch/DAC interpolation.
Unknown modes remain inspectable and never become mislabeled MSM6258 samples.
"""
import csv
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct

from vgm_io import read_vgm_header
from vgm_timing import command_times

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_ROM_BYTES = 2 * 1024 * 1024
MAX_TARGET_SAMPLES = 1000000
MAX_TOTAL_TARGET_SAMPLES = 8000000
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class AdpcmIssue:
    code: str
    source_event_id: int | None
    vgmticks: int
    detail: str
    original_source_event_id: int | None = None


@dataclass(frozen=True)
class AdpcmBlock:
    block_id: int
    source_event_id: int
    address: int
    vgmticks: int
    loop_iteration: int
    original_vgmticks: int
    chip_instance: int
    total_rom_bytes: int | None
    destination: int | None
    payload: bytes
    accepted: bool
    original_source_event_id: int = 0


@dataclass(frozen=True)
class AdpcmControl:
    source_event_id: int
    address: int
    vgmticks: int
    loop_iteration: int
    original_vgmticks: int
    chip_instance: int
    register: int
    data: int
    registers: tuple[int, ...]
    original_source_event_id: int = 0
    port: int = 1
    fm_prescaler_divisor: int = 6


@dataclass(frozen=True)
class AdpcmSample:
    sample_id: int
    codec: str
    encoded_bytes: bytes
    sha256: str
    start_address: int
    end_address_exclusive: int
    block_ids: tuple[int, ...]


@dataclass(frozen=True)
class AdpcmSegment:
    segment_id: int
    playback_id: int
    continuation_index: int
    sample_id: int | None
    start_vgmticks: int
    end_vgmticks: int
    source_event_ids: tuple[int, ...]
    loop_iteration: int
    original_vgmticks: int
    rate_num: int
    rate_den: int
    level: int
    pan: int
    repeat: bool
    termination: str
    natural_end_num: int | None
    natural_end_den: int | None
    independently_playable: bool
    issues: tuple[str, ...]
    registers: tuple[int, ...]
    original_source_event_ids: tuple[int, ...] = ()
    memory_mode: str = 'rom'
    observations: tuple[str, ...] = ()
    nominal_cycle_vgmticks_num: int | None = None
    nominal_cycle_vgmticks_den: int | None = None


@dataclass(frozen=True)
class AdpcmAnalysis:
    source_end_vgmticks: int
    clock_hz: int
    clock_raw: int
    loop_count: int
    blocks: tuple[AdpcmBlock, ...]
    controls: tuple[AdpcmControl, ...]
    samples: tuple[AdpcmSample, ...]
    segments: tuple[AdpcmSegment, ...]
    issues: tuple[AdpcmIssue, ...]
    source_sha256: str
    initialization: str = 'explicit target-independent reset assumption: registers zero; limit=65535'

    def dump(self, outdir, stem):
        return dump(self, outdir, stem)


def _ceil(value):
    return -(-value.numerator // value.denominator)


def analyze(raw, *, loop_count=1):
    """Retain raw blocks and register state; interpret bounded ROM playbacks.

    Declared single-chip external ROM/read-only seeded RAM8 can be eligible.
    State resets are labeled assumptions; they are not fabricated VGM writes.
    Parameter changes are preserved as continuation slices, not fresh decoders.
    """
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('OPNA ADPCM source exceeds 64 MiB evidence limit')
    header = read_vgm_header(raw)
    clock_raw = header['opna_clock_raw']
    clock = clock_raw & 0x3fffffff
    registers = [0] * 17
    registers[12:14] = [255, 255]
    blocks, controls, samples, segments, issues = [], [], [], [], []
    memory, known, owners = bytearray(), bytearray(), []
    active = None
    playback_id = 0
    evidence_bytes = 0
    sample_bytes = 0
    event_count = 0
    prescaler = 6
    global_codes = set()

    def issue(code, event, detail):
        issues.append(AdpcmIssue(code, event_count - 1 if event else None,
                                 event.vgmticks if event else 0, detail,
                                 event.source_event_id if event else None))

    if not clock or clock_raw & 0xc0000000 or header['extra_header_offset']:
        global_codes.add('unsupported_clock_or_chip_configuration')
        issue('unsupported_clock_or_chip_configuration', None,
              'Requires one positive ordinary YM2608 clock and no extra clock header')

    def finish(tick, termination, event=None):
        nonlocal active
        if active is None:
            return
        if len(segments) >= 100000:
            raise ValueError('OPNA ADPCM exceeds 100000 Segment evidence rows')
        rate = active['rate']
        estimate = active['natural_end']
        segments.append(AdpcmSegment(
            len(segments), active['id'], active['continuation'], active['sample'],
            active['slice_start'], max(tick, active['slice_start']),
            tuple(active['events'] + ([event_count - 1] if event else [])),
            active['loop_iteration'], active['original_tick'], rate.numerator, rate.denominator,
            active['regs'][11], (active['regs'][1] >> 6) & 3,
            bool(active['regs'][0] & 16), termination,
            estimate.numerator if estimate is not None else None,
            estimate.denominator if estimate is not None else None,
            not active['codes'] and active['continuation'] == 0,
            tuple(sorted(active['codes'])), active['regs'],
            tuple(active['original_events'] + ([event.source_event_id] if event else [])),
            active['memory_mode'], tuple(active['observations']),
            active['nominal_cycle'].numerator if active['nominal_cycle'] is not None else None,
            active['nominal_cycle'].denominator if active['nominal_cycle'] is not None else None))

    def invalidate(code, event, detail):
        if active is None:
            return
        active['codes'].add(code)
        active['natural_end'] = None
        # One decoder epoch cannot be independently restarted at earlier slices.
        for index, segment in enumerate(segments):
            if segment.playback_id == active['id']:
                segments[index] = replace(segment, independently_playable=False,
                    issues=tuple(sorted(set(segment.issues) | {code})))
        issue(code, event, detail)

    for event in command_times(raw, loop_count=loop_count):
        event_count += 1
        if event_count > 2000000:
            raise ValueError('OPNA ADPCM evidence exceeds two million commands')
        tick, pos, cmd = event.vgmticks, event.address, event.command
        if active and active['natural_end'] is not None and _ceil(active['natural_end']) <= tick:
            finish(_ceil(active['natural_end']), 'inferred_nominal_natural_end')
            active = None
        if cmd == 0x67 and raw[pos + 2] == 0x81:
            if len(blocks) >= 10000:
                raise ValueError('OPNA ADPCM exceeds 10000 ROM block evidence rows')
            size_flags = int.from_bytes(raw[pos + 3:pos + 7], 'little')
            payload = bytes(raw[pos + 7:pos + 7 + (size_flags & 0x7fffffff)])
            evidence_bytes += len(payload)
            if evidence_bytes > MAX_EVIDENCE_BYTES:
                raise ValueError('OPNA ADPCM block evidence exceeds 64 MiB')
            instance = size_flags >> 31
            total = int.from_bytes(payload[:4], 'little') if len(payload) >= 8 else None
            destination = int.from_bytes(payload[4:8], 'little') if len(payload) >= 8 else None
            content = payload[8:]
            accepted = (not instance and total is not None and
                        0 < total <= MAX_ROM_BYTES and destination + len(content) <= total)
            if memory and total != len(memory):
                accepted = False
                issue('changing_rom_size', event, 'ROM-size changes are not interpreted')
            block_id = len(blocks)
            blocks.append(AdpcmBlock(block_id, event_count - 1, pos, tick,
                event.loop_iteration, event.original_vgmticks, instance, total,
                destination, payload, accepted, event.source_event_id))
            if not accepted:
                global_codes.add('unresolved_rom_block_history')
                issue('unsupported_or_invalid_rom_block', event,
                      'Requires primary-chip block, valid payload bounds and at most 2 MiB ROM')
                invalidate('memory_change_during_playback', event, 'Unresolved ROM block while playing')
            else:
                if active:
                    invalidate('memory_change_during_playback', event, 'ROM write while decoder epoch is active')
                if not memory:
                    memory, known, owners = bytearray(total), bytearray(total), [-1] * total
                memory[destination:destination + len(content)] = content
                known[destination:destination + len(content)] = b'\1' * len(content)
                owners[destination:destination + len(content)] = [block_id] * len(content)
        elif 0x90 <= cmd <= 0x95 or cmd == 0x68:
            global_codes.add('unsupported_stream_or_ram_transfer')
            issue('unsupported_stream_or_ram_transfer', event,
                  'Stream/PCM RAM commands are not expanded into ADPCM-B controls')
            invalidate('unsupported_stream_or_ram_transfer', event, 'Unexpanded stream/transfer')
        elif cmd in (0x56, 0xa6) and raw[pos + 1] in (0x2d, 0x2e, 0x2f):
            reg, data = raw[pos + 1:pos + 3]
            instance = int(cmd == 0xa6)
            previous_prescaler = prescaler
            if not instance:
                if reg == 0x2d:
                    prescaler = 6
                elif reg == 0x2e and prescaler == 6:
                    prescaler = 3
                elif reg == 0x2f:
                    prescaler = 2
            if len(controls) >= 100000:
                raise ValueError('OPNA ADPCM exceeds 100000 register evidence rows')
            controls.append(AdpcmControl(event_count - 1, pos, tick, event.loop_iteration,
                event.original_vgmticks, instance, reg, data, tuple(registers),
                event.source_event_id, port=0, fm_prescaler_divisor=prescaler))
            if instance:
                global_codes.add('unsupported_second_chip')
                issue('unsupported_second_chip', event, 'Second-chip prescaler controls retained only')
            elif prescaler != previous_prescaler:
                invalidate('unsupported_adpcm_prescaler', event,
                    'FM/ADPCM cadence changed; the default-prescale nominal nibble rate no longer applies')
                if active:
                    finish(tick, 'prescaler_change', event)
                    active['continuation'] += 1
                    active['slice_start'] = tick
                    active['events'] = [event_count - 1]
                    active['original_events'] = [event.source_event_id]
                    active['loop_iteration'] = event.loop_iteration
                    active['original_tick'] = event.original_vgmticks
                    active['rate'] = Fraction(0)
                    active['nominal_cycle'] = None
        elif cmd in (0x57, 0xa7) and raw[pos + 1] <= 0x10:
            if len(controls) >= 100000:
                raise ValueError('OPNA ADPCM exceeds 100000 register evidence rows')
            reg, data = raw[pos + 1:pos + 3]
            instance = int(cmd == 0xa7)
            previous_data = registers[reg]
            if not instance:
                registers[reg] = data
            controls.append(AdpcmControl(event_count - 1, pos, tick, event.loop_iteration,
                event.original_vgmticks, instance, reg, data, tuple(registers), event.source_event_id,
                fm_prescaler_divisor=prescaler))
            if instance:
                global_codes.add('unsupported_second_chip')
                issue('unsupported_second_chip', event, 'Second-chip ADPCM-B controls retained only')
                continue
            if reg == 8 and registers[0] & 0x40:
                global_codes.add('unsupported_record_memory_mutation')
                issue('unsupported_record_memory_mutation', event,
                      'Recording bus writes may alter the seeded memory image')
            if reg == 0:
                if data & 0x40:
                    issue('unsupported_record_mode', event, 'Recording is retained without emulating RAM writes')
                    if data & 0x20:
                        global_codes.add('unsupported_record_memory_history')
                        issue('unsupported_record_memory_history', event,
                              'External recording may mutate seeded memory; later cleared registers do not restore it')
                if active:
                    finish(tick, 'reset' if data & 1 else 'retrigger_or_control_stop', event)
                    active = None
                if data & 0x80 and not data & 1:
                    codes = set(global_codes)
                    if prescaler != 6:
                        codes.add('unsupported_adpcm_prescaler')
                    if data & 0x40 or not data & 0x20:
                        codes.add('unsupported_cpu_record_or_ram_mode')
                    if data & 0x0e or registers[1] & 0x3c:
                        codes.add('unsupported_control_bits_or_memory_mode')
                    if any(registers[index] for index in (6, 7, 8, 14, 15)):
                        codes.add('unmodeled_data_or_prescale_control')
                    memory_mode = 'rom' if registers[1] & 1 else 'ram8' if registers[1] & 2 else 'ram1'
                    observations = ([f'{memory_mode}_seeded_from_vgm_rom_block_assumption']
                                    if memory_mode in ('ram8', 'ram1') else [])
                    if observations:
                        issue(observations[0], event,
                              f'Read-only {memory_mode} image seeded from type81 ROM evidence')
                    if data & 16:
                        codes.add('repeat_requires_continuous_decoder_timing')
                    address_shift = 5 if registers[1] & 3 else 2
                    start = (registers[2] | registers[3] << 8) << address_shift
                    end = ((registers[4] | registers[5] << 8) + 1) << address_shift
                    limit = registers[12] | registers[13] << 8
                    if start >= end or (start >> address_shift) <= limit < ((end >> address_shift) - 1):
                        codes.add('unsupported_address_wrap_or_limit')
                    sample_id = None
                    if start < end <= len(memory) and all(known[start:end]):
                        encoded = bytes(memory[start:end])
                        digest = hashlib.sha256(encoded).hexdigest()
                        source_blocks = tuple(sorted(set(owners[start:end])))
                        identity = (start, end, digest, source_blocks)
                        existing = next((s for s in samples if
                            (s.start_address, s.end_address_exclusive, s.sha256, s.block_ids) == identity), None)
                        if existing:
                            sample_id = existing.sample_id
                        elif sample_bytes + len(encoded) <= MAX_EVIDENCE_BYTES:
                            sample_id = len(samples)
                            samples.append(AdpcmSample(sample_id, 'yamaha_adpcm_b_high_nibble_first',
                                encoded, digest, start, end, source_blocks))
                            sample_bytes += len(encoded)
                        else:
                            codes.add('source_sample_evidence_size_limit')
                    else:
                        codes.add('unmapped_or_missing_rom_bytes')
                    delta = registers[9] | registers[10] << 8
                    # This initial nominal model is valid only at default prescale.
                    rate = Fraction(clock * delta, 144 * 65536) if prescaler == 6 else Fraction(0)
                    if not clock or not delta:
                        codes.add('zero_delta_n_or_clock')
                    cycle = (Fraction((end - start) * 2 * 44100, 1) / rate
                             if rate > 0 and start < end else None)
                    estimate = Fraction(tick) + cycle if not codes and cycle is not None else None
                    for code in sorted(codes):
                        issue(code, event, 'Playback retained; independent target transcode is ineligible')
                    active = dict(id=playback_id, continuation=0, sample=sample_id,
                        slice_start=tick, events=[event_count - 1], original_events=[event.source_event_id],
                        loop_iteration=event.loop_iteration,
                        original_tick=event.original_vgmticks, rate=rate, regs=tuple(registers),
                        codes=codes, natural_end=estimate, nominal_cycle=cycle,
                        memory_mode=memory_mode, observations=observations)
                    playback_id += 1
            elif active and reg != 16 and (data != previous_data or reg == 8):
                invalidate('controls_during_decoder_epoch', event,
                    'Mid-playback or same-timestamp parameter writes require continuous decoding')
                finish(tick, 'parameter_change', event)
                active['continuation'] += 1
                active['slice_start'] = tick
                active['events'] = [event_count - 1]
                active['original_events'] = [event.source_event_id]
                active['loop_iteration'] = event.loop_iteration
                active['original_tick'] = event.original_vgmticks
                active['regs'] = tuple(registers)
                active['rate'] = (Fraction(clock * (registers[9] | registers[10] << 8), 144 * 65536)
                                  if prescaler == 6 else Fraction(0))
                # Initial repeat-cycle estimate is preserved as epoch evidence,
                # but no new cycle is guessed after a held-epoch rate change.
                active['nominal_cycle'] = None
            if reg in (6, 7, 8, 14, 15) and data:
                issue('unmodeled_data_or_prescale_control', event,
                      'Data/prescale/extra controls are retained without a bus interpretation')
        if cmd == 0x66:
            finish(tick, 'source_eof_without_reset')
            source_end = tick
    # Late stream or dual-chip evidence invalidates earlier target candidates too.
    if global_codes:
        segments = [replace(s, independently_playable=False,
            issues=tuple(sorted(set(s.issues) | global_codes))) for s in segments]
    return AdpcmAnalysis(source_end, clock, clock_raw, loop_count, tuple(blocks),
        tuple(controls), tuple(samples), tuple(segments), tuple(issues), hashlib.sha256(raw).hexdigest())


def decode_adpcm_b(encoded):
    """Decode nominal signed 16-bit values, not the YM2608 analog waveform."""
    predictor, step = 0, 127
    result = []
    for byte in encoded:
        for nibble in (byte >> 4, byte & 15):
            delta = ((2 * (nibble & 7) + 1) * step) // 8
            predictor = max(-32768, min(32767, predictor + (-delta if nibble & 8 else delta)))
            step = max(127, min(24576, step * (57, 57, 57, 57, 77, 102, 128, 153)[nibble & 7] // 64))
            result.append(predictor)
    return result


_MSM_STEPS = tuple((16 * 11 ** i) // (10 ** i) for i in range(49))
_MSM_INDEX = (-1, -1, -1, -1, 2, 4, 6, 8)


def _msm_next(signal, index, nibble):
    step = _MSM_STEPS[index]
    delta = step // 8
    if nibble & 4:
        delta += step
    if nibble & 2:
        delta += step // 2
    if nibble & 1:
        delta += step // 4
    return (max(-512, min(511, signal + (-delta if nibble & 8 else delta))),
            max(0, min(48, index + _MSM_INDEX[nibble & 7])))


def encode_msm6258(pcm):
    """Greedy independent 4-bit MSM6258 encoder, 10-bit output, reset predictor -2."""
    signal, index = -2, 0
    packed = bytearray()
    error_squared = 0
    low = None
    for value in pcm:
        desired = max(-512, min(511, round(value / 64)))
        nibble = min(range(16), key=lambda n: abs(_msm_next(signal, index, n)[0] - desired))
        signal, index = _msm_next(signal, index, nibble)
        error_squared += (signal * 64 - value) ** 2
        if low is None:
            low = nibble
        else:
            packed.append(low | nibble << 4)
            low = None
    if low is not None:
        # This padding nibble is not part of sample_count; consumers must honor it.
        padding = min(range(16), key=lambda n: abs(_msm_next(signal, index, n)[0] - signal))
        packed.append(low | padding << 4)
    return bytes(packed), error_squared


@dataclass(frozen=True)
class Msm6258Sample:
    sample_id: int
    source_sample_id: int
    source_segment_id: int
    codec: str
    encoded_bytes: bytes
    sha256: str
    source_pcm_sha256: str
    target_pcm_sha256: str
    sample_count: int
    padding_nibbles: int
    clock_hz: int
    divider: int
    rate_num: int
    rate_den: int
    level_baked: int
    pan: int
    squared_error_sum: int
    mdx_frequency_index: int = 0


@dataclass(frozen=True)
class Msm6258Playback:
    playback_id: int
    source_playback_id: int
    source_segment_id: int
    sample_id: int | None
    start_vgmticks: int
    end_vgmticks: int
    source_event_ids: tuple[int, ...]
    eligible: bool
    source_rate_num: int
    source_rate_den: int
    target_rate_num: int | None
    target_rate_den: int | None
    level: int
    pan: int
    diagnostics: tuple[str, ...]
    target_clock_hz: int | None = None
    target_divider: int | None = None
    mdx_frequency_index: int | None = None


@dataclass(frozen=True)
class Msm6258Projection:
    source_end_vgmticks: int
    clock_hz: int
    samples: tuple[Msm6258Sample, ...]
    playbacks: tuple[Msm6258Playback, ...]
    diagnostics: tuple[str, ...]
    rate_profile: str = 'MXDRV F0..F4; clock_hz is platform base, samples record actual PCM clock'

    def dump(self, outdir, stem):
        return dump_target(self, outdir, stem)


def project_msm6258(analysis, *, clock_hz=4000000):
    """Project independent native spans into samples, retaining the source clock.

    Linear resampling, level baking and greedy ADPCM requantization are explicit
    approximations. No PDX allocation or MDX command scheduling occurs here.
    """
    from pcm_mdx import RATES
    if clock_hz != 4000000:
        raise ValueError('Initial MSM6258 target uses the X68000 4/8 MHz MXDRV platform profile')
    samples, playbacks = [], []
    total_output = 0
    for segment in analysis.segments:
        diagnostics = list(segment.issues) + list(segment.observations)
        sample_id = None
        target_rate = None
        target_clock = divider = frequency_index = None
        if not segment.independently_playable or segment.sample_id is None:
            diagnostics.append('continuous_or_unresolved_source_not_transcoded')
        elif segment.end_vgmticks <= segment.start_vgmticks:
            diagnostics.append('zero_duration_source_span')
        else:
            source = analysis.samples[segment.sample_id]
            source_rate = Fraction(segment.rate_num, segment.rate_den)
            frequency_index = min(range(len(RATES)), key=lambda index: abs(RATES[index] - source_rate))
            target_rate = RATES[frequency_index]
            target_clock = 4000000 if frequency_index < 2 else 8000000
            divider = (1024, 768, 1024, 768, 512)[frequency_index]
            duration = Fraction(segment.end_vgmticks - segment.start_vgmticks, 44100)
            # Do not project beyond the nominal encoded sample end.
            count = min(_ceil(duration * target_rate),
                        _ceil(Fraction(len(source.encoded_bytes) * 2) * target_rate / source_rate))
            if len(source.encoded_bytes) * 2 > MAX_TARGET_SAMPLES or count > MAX_TARGET_SAMPLES:
                diagnostics.append('target_sample_size_limit')
            elif total_output + count > MAX_TOTAL_TARGET_SAMPLES:
                diagnostics.append('aggregate_target_sample_size_limit')
            else:
                decoded = decode_adpcm_b(source.encoded_bytes)
                pcm = []
                for i in range(count):
                    position = Fraction(i) * source_rate / target_rate
                    lo = min(position.numerator // position.denominator, len(decoded) - 1)
                    hi = min(lo + 1, len(decoded) - 1)
                    fraction = position - lo
                    interpolated = Fraction(decoded[lo]) * (1 - fraction) + Fraction(decoded[hi]) * fraction
                    # OPNA level is linear /256; bake once, keep source value in IR.
                    pcm.append(max(-32768, min(32767, round(interpolated * segment.level / 256))))
                encoded, error = encode_msm6258(pcm)
                sample_id = len(samples)
                source_pcm = b''.join(struct.pack('<h', value) for value in decoded)
                target_pcm = b''.join(struct.pack('<h', value) for value in pcm)
                samples.append(Msm6258Sample(sample_id, source.sample_id, segment.segment_id,
                    'msm6258-adpcm4-low-first', encoded,
                    hashlib.sha256(encoded).hexdigest(), hashlib.sha256(source_pcm).hexdigest(),
                    hashlib.sha256(target_pcm).hexdigest(), count, count % 2, target_clock,
                    divider, target_rate.numerator, target_rate.denominator, segment.level,
                    segment.pan, error, frequency_index))
                total_output += count
                diagnostics.extend(('nominal_decoder_without_ym2608_prefetch_or_dac_interpolation',
                    'linear_resampling_without_antialias_filter', 'volume_baked_into_sample',
                    'greedy_msm6258_adpcm_requantization', '10bit_output_dynamic_range_loss',
                    'native_pan_retained_not_assigned_to_mdx'))
                if count % 2:
                    diagnostics.append('final_padding_nibble_requires_consumer_length_control')
        playbacks.append(Msm6258Playback(len(playbacks), segment.playback_id, segment.segment_id,
            sample_id, segment.start_vgmticks, segment.end_vgmticks, segment.source_event_ids,
            sample_id is not None, segment.rate_num, segment.rate_den,
            target_rate.numerator if target_rate else None, target_rate.denominator if target_rate else None,
            segment.level, segment.pan, tuple(diagnostics), target_clock, divider, frequency_index))
    return Msm6258Projection(analysis.source_end_vgmticks, clock_hz, tuple(samples), tuple(playbacks),
        ('Target PCM IR only: no PDX, track binding, runtime delivery or acoustic equivalence verified',))


def _csv(path, rows, columns):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (tuple, list, dict)) else value
                             for key, value in row.items()})


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def dump(analysis, outdir, stem):
    """Emit immutable source blocks/samples and inspectable native state/spans."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    block_rows, sample_rows = [], []
    for block in analysis.blocks:
        file = f'{stem}.opna_adpcm.block{block.block_id:04d}.bin'
        (out / file).write_bytes(block.payload)
        row = asdict(block)
        row.pop('payload')
        row.update(file=file, payload_bytes=len(block.payload), sha256=hashlib.sha256(block.payload).hexdigest())
        block_rows.append(row)
        files.append(file)
    for sample in analysis.samples:
        file = f'{stem}.opna_adpcm.sample{sample.sample_id:04d}.bin'
        (out / file).write_bytes(sample.encoded_bytes)
        row = asdict(sample)
        row.pop('encoded_bytes')
        row.update(file=file, encoded_bytes=len(sample.encoded_bytes))
        sample_rows.append(row)
        files.append(file)
    for suffix, values, cls in (('blocks', block_rows, AdpcmBlock),
                               ('controls', [asdict(x) for x in analysis.controls], AdpcmControl),
                               ('segments', [asdict(x) for x in analysis.segments], AdpcmSegment),
                               ('issues', [asdict(x) for x in analysis.issues], AdpcmIssue)):
        file = f'{stem}.opna_adpcm.{suffix}.csv'
        columns = list(values[0]) if values else [name for name in cls.__dataclass_fields__ if name != 'payload']
        _csv(out / file, values, columns)
        files.append(file)
    summary = dict(source_end_vgmticks=analysis.source_end_vgmticks, clock_hz=analysis.clock_hz,
        clock_raw=analysis.clock_raw, loop_count=analysis.loop_count, initialization=analysis.initialization,
        source_sha256=analysis.source_sha256, timing_model='nominal rational nibble rate; natural end is inferred',
        blocks=block_rows, samples=sample_rows, segments=[asdict(x) for x in analysis.segments],
        issues=[asdict(x) for x in analysis.issues], current_files=files)
    file = f'{stem}.opna_adpcm.analysis.json'
    _write_json(out / file, summary)
    return {suffix: out / f'{stem}.opna_adpcm.{suffix}' for suffix in
            ('blocks.csv', 'controls.csv', 'segments.csv', 'issues.csv', 'analysis.json')}


def dump_target(projection, outdir, stem):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    samples, files = [], []
    for sample in projection.samples:
        file = f'{stem}.msm6258_ir.sample{sample.sample_id:04d}.bin'
        (out / file).write_bytes(sample.encoded_bytes)
        row = asdict(sample)
        row.pop('encoded_bytes')
        row.update(file=file, encoded_bytes=len(sample.encoded_bytes))
        samples.append(row)
        files.append(file)
    playbacks = [asdict(x) for x in projection.playbacks]
    file = f'{stem}.msm6258_ir.playbacks.csv'
    _csv(out / file, playbacks, list(Msm6258Playback.__dataclass_fields__))
    files.append(file)
    file = f'{stem}.msm6258_ir.analysis.json'
    _write_json(out / file, dict(source_end_vgmticks=projection.source_end_vgmticks,
        clock_hz=projection.clock_hz, samples=samples, playbacks=playbacks,
        rate_profile=projection.rate_profile, diagnostics=projection.diagnostics, current_files=files))
    return {'analysis': out / file, 'playbacks': out / f'{stem}.msm6258_ir.playbacks.csv'}
