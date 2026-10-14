"""Bridge transcoded MSM6258 target IR into the existing MDX/PDX contract.

No native byte supplies or physical resets are fabricated. The adapter's
playbacks describe independent PDX notes under an explicit target profile.
"""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from msm6258 import PcmAnalysis, PcmPlayback, PcmSample, TransferLog


@dataclass(frozen=True)
class ProjectedPcmAnalysis(PcmAnalysis):
    target_evidence: tuple[dict, ...] = ()
    target_mapping: tuple[dict, ...] = ()
    source_origin: str = 'projected_msm6258_from_opna_adpcm_b'

    def dump(self, outdir, stem):
        super().dump(outdir, stem)
        self.dump_bridge(outdir, stem)

    def dump_bridge(self, outdir, stem):
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / (stem + '.opna_pcm_bridge.json')
        path.write_text(json.dumps(dict(source_origin=self.source_origin,
            source_end_vgmticks=self.source_end_vgmticks, source_loop_vgmticks=self.source_loop_vgmticks,
            source_sha256=hashlib.sha256(self.source_raw).hexdigest(), loop_count=self.loop_count,
            source_supply_evidence='none; encoded bytes come from transcoded target IR',
            decoder_reset='independent PDX note assumption; physical runtime reset unverified',
            target_sample_count=len(self.samples), playback_count=len(self.playbacks),
            target_mapping=self.target_mapping, target_evidence=self.target_evidence),
            indent=2) + '\n', encoding='utf-8')
        return path


def adapt(native, target, *, source_raw, loop_start=None):
    """Keep all playback candidates, including ineligible decoder epochs.

    Only byte-identical target payloads share a PDX slot. Native Yamaha bytes
    remain in their own IR and are never passed to the PDX packer.
    """
    if native.source_end_vgmticks != target.source_end_vgmticks:
        raise ValueError('Native OPNA and target PCM IR have different source ends')
    source_raw = bytes(source_raw)
    if hashlib.sha256(source_raw).hexdigest() != native.source_sha256:
        raise ValueError('PCM bridge source bytes differ from native OPNA evidence')
    source_samples = {s.sample_id: s for s in target.samples}
    source_segments = {s.segment_id: s for s in native.segments}
    samples, playbacks, mapping, evidence = [], [], [], []
    byte_identity = {}

    def loss(playback, code, detail, *, source_value=None, projected_value=None):
        evidence.append(dict(criterion='transcoding', status='lossy', code=code, detail=detail,
            cause='target_projection', playback_id=playback.playback_id,
            start_vgmticks=playback.start_vgmticks, end_vgmticks=playback.end_vgmticks,
            source_value=source_value, projected_value=projected_value,
            fallback='explicit_adpcm_b_to_msm6258_transcoding'))

    for p in target.playbacks:
        segment = source_segments[p.source_segment_id]
        sample = source_samples.get(p.sample_id)
        eligible = p.eligible and segment.independently_playable and sample is not None
        sample_id = None
        if eligible:
            encoded = sample.encoded_bytes
            if encoded not in byte_identity:
                sample_id = len(samples)
                byte_identity[encoded] = sample_id
                samples.append(PcmSample(sample_id, 'msm6258-adpcm4-low-first', encoded,
                                         hashlib.sha256(encoded).hexdigest()))
            sample_id = byte_identity[encoded]
            loss(p, 'adpcm_b_nominal_decode', 'Yamaha decoder prefetch and DAC interpolation are not emulated')
            loss(p, 'msm6258_requantization', 'Independent 10-bit MSM6258 ADPCM encoding approximates the decoded waveform',
                 projected_value=dict(squared_error_sum=sample.squared_error_sum))
            loss(p, 'pcm_linear_resampling', 'PCM resampling uses linear interpolation without antialias filtering',
                 source_value=dict(rate_num=p.source_rate_num, rate_den=p.source_rate_den),
                 projected_value=dict(rate_num=sample.rate_num, rate_den=sample.rate_den))
            loss(p, 'pcm_volume_baked', 'Source level is baked into the target sample using level/256',
                 source_value=p.level, projected_value='sample amplitude; MDX volume128')
            if sample.padding_nibbles:
                loss(p, 'pcm_padding_nibble', 'PDX contains the full final byte, including its extra padding nibble',
                     source_value=sample.sample_count, projected_value=len(encoded) * 2)
            duration = Fraction(len(encoded) * 2 * 44100 * sample.rate_den, sample.rate_num)
            loss(p, 'pcm_encoded_duration_rounding', 'Encoded sample exhaustion may differ from the preserved playback gate',
                 source_value=p.end_vgmticks-p.start_vgmticks,
                 projected_value=dict(numerator=duration.numerator, denominator=duration.denominator))
            for observation in segment.observations:
                evidence.append(dict(criterion='source_interpretation', status='unverified',
                    code=observation, detail='Native source interpretation relies on the recorded memory-image assumption',
                    playback_id=p.playback_id, start_vgmticks=p.start_vgmticks,
                    end_vgmticks=p.end_vgmticks, cause='source_assumption'))
        native_ids = tuple(p.source_event_ids)
        first = native_ids[0] if native_ids else -1
        last = native_ids[-1] if native_ids else first
        rate_num = sample.rate_num if eligible else 0
        rate_den = sample.rate_den if eligible else 1
        # OPNA enable bits (L=2/R=1) -> MSM6258 disable bits. pcm_mdx
        # applies its existing MSM6258-to-MDX pan map exactly once.
        pan = p.pan ^ 3
        issues = () if eligible else tuple(dict.fromkeys((*segment.issues,
            *p.diagnostics, 'opna_decoder_epoch_not_independent')))
        playbacks.append(PcmPlayback(
            playback_id=p.playback_id, chip_instance=0, sample_id=sample_id,
            start_vgmticks=p.start_vgmticks, end_vgmticks=p.end_vgmticks,
            first_source_event_id=first, last_source_event_id=last,
            transfer_start=0, transfer_end=0, supplied_bytes=0,
            clock_hz=sample.clock_hz if eligible else target.clock_hz,
            divider=sample.divider if eligible else 1024, rate_num=rate_num, rate_den=rate_den,
            pan=pan, reset_observed=False, decoder_reset_known=eligible,
            reset_origin='projected_independent_pdx_decoder_assumption',
            termination='source_end' if segment.termination == 'source_eof_without_reset' else segment.termination,
            delivery_cadence_compatible=eligible, independently_playable=eligible,
            nominal_nibbles=len(sample.encoded_bytes)*2 if eligible else None,
            consumed_nibbles=None, issues=issues, control_event_ids=()))
        mapping.append(dict(playback_id=p.playback_id, native_playback_id=p.source_playback_id,
            native_segment_id=p.source_segment_id, target_sample_id=p.sample_id,
            pdx_sample_id=sample_id, native_pan=p.pan, msm6258_pan=pan,
            encoded_target_bytes=len(sample.encoded_bytes) if eligible else 0,
            original_source_event_ids=segment.original_source_event_ids,
            source_event_ids=native_ids, eligible=eligible))
    return ProjectedPcmAnalysis(
        source_end_vgmticks=target.source_end_vgmticks, samples=tuple(samples),
        playbacks=tuple(playbacks), controls=(), issues=(), transfers=TransferLog(b''),
        raw_commands=(), source_raw=source_raw, clock_hz=target.clock_hz, options=4,
        initialization='derived MSM6258 target IR; no observed native byte supply/reset claimed',
        source_loop_vgmticks=loop_start, blocks=(), loop_count=native.loop_count,
        target_evidence=tuple(evidence), target_mapping=tuple(mapping))
