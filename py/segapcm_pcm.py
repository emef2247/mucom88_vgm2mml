"""SegaPCM-specific evidence for the shared waveform-to-MDX PCM projection."""
from opna_adpcm import encode_msm6258
from pcm_waveform_mdx import (CHUNK_TICKS, MAX_BYTES, TARGET_RATE,
    ProjectedWaveformAnalysis as ProjectedSegaPcmAnalysis, project as project_waveform, _loss)


def project(native, *, source_raw, loop_start=None, loop_count=1):
    evidence = []
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
    return project_waveform(native, source_raw=source_raw, loop_start=loop_start,
        loop_count=loop_count, source_origin='projected_msm6258_from_segapcm',
        evidence_prefix='segapcm', artifact_prefix='segapcm_pcm',
        source_description='Native 8-bit SegaPCM mix', source_evidence=evidence,
        encoder=encode_msm6258)
