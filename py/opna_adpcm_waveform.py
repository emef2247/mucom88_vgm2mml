"""Nominal continuous ADPCM-B waveform derived from retained decoder epochs.

This is inspectable derived source interpretation, not a YM2608 emulator.
Control slices never restart decoding. Target codec and MDX timing live in
pcm_waveform_mdx; observed source State/Segments are not modified here.
"""
from array import array
import csv
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

from opna_adpcm import decode_adpcm_b
from opna_adpcm_playback import build_epochs, describe, dump_description

MAX_FRAMES = 32000000
MAX_DECODED_NIBBLES = 16000000


def _ceil(value):
    return -(-value.numerator // value.denominator)


@dataclass(frozen=True)
class WaveformGate:
    gate_id: int
    start_vgmticks: Fraction
    end_vgmticks: Fraction
    start_event_id: int | None
    end_event_id: int | None
    source_event_ids: tuple
    source_segment_ids: tuple


@dataclass(frozen=True)
class WaveformAnalysis:
    sample_rate: Fraction
    samples: array
    gates: tuple
    source_end_vgmticks: int
    epochs: tuple

    def dump(self, outdir, stem):
        root = Path(outdir)
        root.mkdir(parents=True, exist_ok=True)
        dump_description(describe(self.epochs), root, stem)
        payload = array('i', self.samples)
        if sys.byteorder != 'little':
            payload.byteswap()
        raw = payload.tobytes()
        (root / (stem + '.opna_adpcm.waveform.s32le')).write_bytes(raw)
        rows = [dict(gate_id=g.gate_id, start_num=g.start_vgmticks.numerator,
            start_den=g.start_vgmticks.denominator, end_num=g.end_vgmticks.numerator,
            end_den=g.end_vgmticks.denominator, source_event_ids=json.dumps(g.source_event_ids),
            source_segment_ids=json.dumps(g.source_segment_ids)) for g in self.gates]
        with (root / (stem + '.opna_adpcm.waveform_gates.csv')).open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]) if rows else ('gate_id',))
            writer.writeheader()
            writer.writerows(rows)
        (root / (stem + '.opna_adpcm.waveform.json')).write_text(json.dumps(dict(
            sample_rate_num=self.sample_rate.numerator, sample_rate_den=self.sample_rate.denominator,
            channels=2, format='signed 32-bit little endian interleaved stereo',
            frames=len(self.samples) // 2, sha256=hashlib.sha256(raw).hexdigest(),
            source_end_vgmticks=self.source_end_vgmticks,
            model='nominal ADPCM-B nibble decoder; linear interpolation; source pan and level/256 baked once',
            zero_delta_n='position pauses without decoder reset; nominal held value retained',
            natural_end='first exhausted encoded sample caps epoch; later controls do not restart it',
            hardware_limits='prefetch, DAC interpolation and bus timing not emulated'), indent=2) + '\n', encoding='utf-8')


def render(analysis, *, max_frames=MAX_FRAMES):
    """Decode each epoch once, retaining position across supported live controls.

    build_epochs rejects repeat, address/memory changes and unmodeled controls.
    A fixed derived 44.1 kHz waveform is an explicit nominal sampling model.
    """
    # This broad native diagnostic combines two cases. Resolve only zero
    # Delta-N under a known positive ordinary clock; header/mode blockers stay.
    resolved = ('zero_delta_n_or_clock',) if analysis.clock_hz > 0 and not analysis.clock_raw & 0xc0000000 else ()
    epochs = build_epochs(analysis, resolved_codes=resolved)
    frames = analysis.source_end_vgmticks if epochs else 0
    if not 0 <= frames <= max_frames:
        raise ValueError('ADPCM-B waveform exceeds the bounded frame scope')
    sizes = {s.sample_id: len(s.encoded_bytes) * 2 for s in analysis.samples}
    if sum(sizes[e.sample_id] for e in epochs) > MAX_DECODED_NIBBLES:
        raise ValueError('ADPCM-B epochs exceed the bounded nominal decode scope')
    samples = array('i', [0]) * (frames * 2)
    sources = {s.sample_id: s for s in analysis.samples}
    gates = []
    previous_end = Fraction(0)
    for epoch in epochs:
        stop = min(Fraction(epoch.end_vgmticks), epoch.nominal_end or Fraction(epoch.end_vgmticks))
        start = Fraction(epoch.start_vgmticks)
        if stop <= start:
            continue
        if start < previous_end:
            raise ValueError('Overlapping ADPCM-B decoder epochs are unsupported')
        previous_end = stop
        decoded = decode_adpcm_b(sources[epoch.sample_id].encoded_bytes)
        if not decoded:
            raise ValueError('ADPCM-B epoch has no decoded source samples')
        ids = tuple(sorted({event for s in epoch.segments for event in s.source_event_ids}))
        gates.append(WaveformGate(epoch.playback_id, start, stop,
            min(ids, default=None), max(ids, default=None), ids,
            tuple(s.segment_id for s in epoch.segments)))
        for segment, span in zip(epoch.segments, epoch.positions):
            end = min(Fraction(span.end_vgmticks), stop)
            if end <= span.start_vgmticks:
                continue
            first = max(0, _ceil(Fraction(span.start_vgmticks) - Fraction(1, 2)))
            last = min(frames, _ceil(end - Fraction(1, 2)))
            rate = float(span.rate / 44100)
            origin = float(span.position_start)
            for frame in range(first, last):
                position = origin + (frame + 0.5 - span.start_vgmticks) * rate
                # Position counts consumed nibbles: before the first nibble
                # the nominal predictor is zero, including an initial pause.
                consumed = max(0.0, min(float(len(decoded)), position))
                lo = int(consumed)
                weight = consumed - lo
                before = decoded[lo - 1] if lo else 0
                after = decoded[min(lo, len(decoded) - 1)]
                value = round((before * (1 - weight) + after * weight) * segment.level / 256)
                samples[frame * 2] = value if segment.pan & 2 else 0
                samples[frame * 2 + 1] = value if segment.pan & 1 else 0
    return WaveformAnalysis(Fraction(44100), samples, tuple(gates), analysis.source_end_vgmticks, epochs)


def project(analysis, *, source_raw, loop_start=None):
    """Create shared-clock target chunks while retaining the native waveform."""
    from pcm_waveform_mdx import project as project_waveform
    if hashlib.sha256(source_raw).hexdigest() != analysis.source_sha256:
        raise ValueError('ADPCM-B waveform source bytes differ from native source evidence')
    native = render(analysis)
    evidence = (dict(criterion='transcoding', status='lossy', code='adpcm_b_continuous_nominal_decode',
        detail='One decode per epoch; live level/pan/rate retained, nominal interpolation without hardware DAC/prefetch emulation',
        cause='source_interpretation', fallback='explicit_adpcm_b_waveform_transcoding'),
        dict(criterion='transcoding', status='lossy', code='adpcm_b_level_pan_baked',
        detail='Source level/256 and left/right enable are baked once; target mono mix uses centered volume128',
        cause='target_projection', fallback='explicit_adpcm_b_waveform_transcoding'))
    observations = tuple(dict(criterion='source_interpretation', status='unverified',
        code=observation, detail='Native memory image relies on the retained VGM ROM block assumption',
        cause='source_assumption') for observation in sorted({o for s in analysis.segments for o in s.observations}))
    return project_waveform(native, source_raw=source_raw, loop_start=loop_start,
        loop_count=analysis.loop_count, source_origin='projected_msm6258_from_opna_adpcm_b_continuous',
        evidence_prefix='adpcm_b', artifact_prefix='opna_pcm',
        source_description='Nominal continuous ADPCM-B waveform', source_evidence=evidence + observations)
