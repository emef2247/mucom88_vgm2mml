"""Explicit SegaPCM stereo mix -> independent mono MSM6258 target chunks.

Native playback gates and unclipped stereo remain in segapcm_playback IR. These
chunks are target packaging, not new source Key-On events or observed supplies.
"""
from dataclasses import dataclass, field, replace
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path

from msm6258 import PcmAnalysis, PcmPlayback, PcmSample, TransferLog
from opna_adpcm import encode_msm6258


TARGET_RATE = 15625
CHUNK_TICKS = 8 * 44100
MAX_BYTES = 65535


def _ceil(value):
    return -(-value.numerator // value.denominator)


def _even_count(value):
    # Two samples fill one ADPCM byte; never add an undocumented padding nibble.
    return max(2, 2 * round(value / 2))


def _loss(code, detail, **values):
    return dict(criterion='transcoding', status='lossy', code=code, detail=detail,
                cause='target_projection', fallback='explicit_segapcm_mono_transcoding', **values)


@dataclass(frozen=True)
class ProjectedSegaPcmAnalysis(PcmAnalysis):
    target_evidence: tuple[dict, ...] = ()
    target_mapping: tuple[dict, ...] = ()
    source_origin: str = 'projected_msm6258_from_segapcm'
    normalization_omission_safe: bool = False
    native: object = field(default=None, repr=False, compare=False)
    chunks: tuple[tuple[int, int, tuple[int, ...]], ...] = ()
    aligned_sample_multiplier: int | None = None

    def align_to_clock(self, tick_mapper, sample_multiplier):
        """Encode the original waveform to each final shared MDX gate duration.

        The source interval is unchanged. Its audio is stretched/compressed to
        the selected target gate; nearest even sample count differs by <= one
        F4 sample. A collapsed positive interval is an error, never omission.
        """
        return _encode(self, tick_mapper, sample_multiplier)

    def dump(self, outdir, stem):
        super().dump(outdir, stem)
        out = Path(outdir)
        fields = tuple(self.target_mapping[0]) if self.target_mapping else ('playback_id',)
        with (out / (stem + '.segapcm_pcm_mapping.csv')).open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(self.target_mapping)
        rates = self.native.sample_rate
        path = out / (stem + '.segapcm_pcm_bridge.json')
        path.write_text(json.dumps(dict(source_origin=self.source_origin,
            source_end_vgmticks=self.source_end_vgmticks,
            source_loop_vgmticks=self.source_loop_vgmticks, loop_count=self.loop_count,
            source_sha256=hashlib.sha256(self.source_raw).hexdigest(),
            native_rate_num=rates.numerator, native_rate_den=rates.denominator,
            target_rate_hz=TARGET_RATE, target_clock_hz=8000000, target_divider=512,
            mdx_frequency_index=4, target_pan=3, channel_mix='(left + right) / 2',
            source_supply_evidence='none; derived target ADPCM, not original SegaPCM bytes',
            decoder_reset='independent PDX note assumption; physical runtime reset unverified',
            normalization_omission_safe=False,
            aligned_sample_multiplier=self.aligned_sample_multiplier,
            target_mapping=self.target_mapping, target_evidence=self.target_evidence),
            indent=2) + '\n', encoding='utf-8')
        return path


def project(native, *, source_raw, loop_start=None, loop_count=1):
    """Package the union of native active gates, with exact loop/EOF boundaries."""
    end = native.source_end_vgmticks
    if loop_start is not None and not 0 <= loop_start < end:
        raise ValueError('SegaPCM loop boundary must be inside the source timeline')
    spans = []
    for gate in sorted(native.gates, key=lambda g: (g.start_vgmticks, g.end_vgmticks)):
        start = max(0, gate.start_vgmticks.numerator // gate.start_vgmticks.denominator)
        stop = min(end, _ceil(gate.end_vgmticks))
        if stop <= start:
            continue
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], stop)
            spans[-1][2].append(gate.gate_id)
        else:
            spans.append([start, stop, [gate.gate_id]])
    chunks = []
    gates_by_id = {gate.gate_id: gate for gate in native.gates}
    for start, stop, gate_ids in spans:
        while start < stop:
            boundary = min(stop, start + CHUNK_TICKS)
            if loop_start is not None and start < loop_start < boundary:
                boundary = loop_start
            overlap = tuple(gate_id for gate_id in gate_ids
                            if gates_by_id[gate_id].start_vgmticks < boundary
                            and gates_by_id[gate_id].end_vgmticks > start)
            chunks.append((start, boundary, overlap))
            start = boundary
    base = ProjectedSegaPcmAnalysis(source_end_vgmticks=end, samples=(), playbacks=(),
        controls=(), issues=(), transfers=TransferLog(b''), raw_commands=(),
        source_raw=bytes(source_raw), clock_hz=8000000, options=4,
        initialization='derived mono MSM6258 target; no native byte supply/reset claimed',
        source_loop_vgmticks=loop_start, blocks=(), loop_count=loop_count,
        native=native, chunks=tuple(chunks))
    return _encode(base)


def _encode(base, tick_mapper=None, multiplier=None):
    native = base.native
    stereo = native.samples
    frames = len(stereo) // 2
    gates = {gate.gate_id: gate for gate in native.gates}
    samples, playbacks, mapping = [], [], []
    identities = {}
    cached_rows = {row['playback_id']: row for row in base.target_mapping}
    cached_samples = {sample.sample_id: sample for sample in base.samples}
    evidence = [
        _loss('segapcm_stereo_mono_mix', 'Sixteen stereo voices are mixed to one centered mono PDX track'),
        _loss('segapcm_linear_resampling', 'Native mix is linearly resampled without antialias filtering',
              source_value=dict(rate_num=native.sample_rate.numerator, rate_den=native.sample_rate.denominator),
              projected_value=TARGET_RATE),
        _loss('segapcm_independent_chunk_reset', 'Each target chunk assumes a fresh MSM6258 predictor; seams may be audible'),
        _loss('segapcm_msm6258_requantization', 'Native 8-bit PCM mix is requantized to 10-bit MSM6258 ADPCM'),
        _loss('segapcm_fractional_gate_packaging', 'Fractional native gate bounds are rounded outward by less than one source tick; native gates retained'),
    ]
    if base.source_loop_vgmticks is not None:
        evidence.append(_loss('segapcm_frozen_loop_audio',
            'The recorded traversal is frozen into PDX; later source loop traversals may carry different runtime phase'))
    if native.reset_profile:
        evidence.append(dict(criterion='source_interpretation', status='unverified',
            code='segapcm_runtime_model', detail=native.reset_profile, cause='source_assumption'))
    if native.missing_rom_reads:
        evidence.append(_loss('segapcm_missing_rom_neutral_fill',
            'Unavailable source ROM bytes were interpreted as neutral80; original sparse ROM retained',
            source_value=native.missing_rom_reads))
    for issue in native.issues:
        evidence.append(dict(criterion='source_interpretation', status='unverified',
            code='segapcm_source_observation', detail=issue, cause='source_assumption'))
    for playback_id, (start, end, gate_ids) in enumerate(base.chunks):
        if tick_mapper is None:
            wanted = Fraction((end - start) * TARGET_RATE, 44100)
            start_tick = end_tick = None
        else:
            start_tick, end_tick = tick_mapper(start), tick_mapper(end)
            if end_tick <= start_tick:
                raise ValueError('Shared MDX clock collapses a positive SegaPCM target chunk')
            wanted = Fraction((end_tick - start_tick) * multiplier * 256 * TARGET_RATE, 1000000)
        count = _even_count(wanted)
        if count // 2 > MAX_BYTES:
            raise ValueError('Aligned SegaPCM chunk exceeds the 65535-byte verified PDX scope')
        counters = dict(clipped=0, stereo_difference=0)
        rate = float(native.sample_rate / 44100)
        first_frame = start * rate - 0.5
        step = (end - start) * rate / count

        def mono():
            for index in range(count):
                position = first_frame + (index + 0.5) * step
                left_index = max(0, min(frames - 1, int(position // 1)))
                right_index = min(frames - 1, left_index + 1)
                weight = max(0.0, min(1.0, position - left_index))
                if not frames:
                    yield 0
                    continue
                left = stereo[left_index * 2] * (1 - weight) + stereo[right_index * 2] * weight
                right = stereo[left_index * 2 + 1] * (1 - weight) + stereo[right_index * 2 + 1] * weight
                counters['stereo_difference'] += left != right
                value = round((left + right) / 2)
                counters['clipped'] += not -32768 <= value <= 32767
                yield max(-32768, min(32767, value))

        cached = cached_rows.get(playback_id)
        if cached and cached['target_sample_count'] == count:
            encoded = cached_samples[cached['sample_id']].encoded_bytes
            error = cached['squared_error_sum']
            counters.update(clipped=cached['clipped_target_samples'],
                            stereo_difference=cached['stereo_difference_samples'])
        else:
            encoded, error = encode_msm6258(mono())
        sample_id = identities.get(encoded)
        if sample_id is None:
            sample_id = len(samples)
            identities[encoded] = sample_id
            samples.append(PcmSample(sample_id, 'msm6258-adpcm4-low-first', encoded,
                                     hashlib.sha256(encoded).hexdigest()))
        event_ids = tuple(sorted({event for gate_id in gate_ids
                                 for event in (gates[gate_id].start_event_id, gates[gate_id].end_event_id)
                                 if event is not None}))
        playbacks.append(PcmPlayback(playback_id, 0, sample_id, start, end,
            min(event_ids, default=-1), max(event_ids, default=-1), 0, 0, 0,
            8000000, 512, TARGET_RATE, 1, 0, False, True,
            'projected_independent_pdx_decoder_assumption', 'projected_chunk_boundary',
            True, True, count, None, (), ()))
        mapping.append(dict(playback_id=playback_id, sample_id=sample_id,
            source_start_vgmticks=start, source_end_vgmticks=end, native_gate_ids=gate_ids,
            source_event_ids=event_ids, target_start_tick=start_tick, target_end_tick=end_tick,
            target_sample_count=count, encoded_bytes=len(encoded), padding_nibbles=0,
            clipped_target_samples=counters['clipped'], stereo_difference_samples=counters['stereo_difference'],
            squared_error_sum=error, target_duration_error_samples=float(count - wanted)))
        if counters['clipped']:
            evidence.append(_loss('segapcm_mono_clipping', 'Mono mix exceeds signed 16-bit range and is clipped',
                playback_id=playback_id, source_value=counters['clipped']))
    if tick_mapper is not None:
        evidence.append(_loss('segapcm_audio_shared_clock_alignment',
            'Original chunk audio is stretched to the final MDX gate; even-count rounding error is at most one F4 sample',
            projected_value=dict(sample_multiplier=multiplier, target_rate_hz=TARGET_RATE)))
    else:
        evidence.append(_loss('segapcm_target_rate_rounding',
            'Initial target sample length uses nearest even F4 sample count; final shared-clock alignment follows'))
    return replace(base, samples=tuple(samples), playbacks=tuple(playbacks),
                   target_mapping=tuple(mapping), target_evidence=tuple(evidence),
                   aligned_sample_multiplier=multiplier)
