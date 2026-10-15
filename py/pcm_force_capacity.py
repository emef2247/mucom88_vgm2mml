"""Explicit, source-preserving PCM capacity selection for audition output."""
import csv
from dataclasses import dataclass, replace
import json
from pathlib import Path


SLOT_LIMIT = 96
VERIFIED_SAMPLE_BYTES = 65535


@dataclass(frozen=True)
class CapacityView:
    analysis: object
    rows: tuple[dict, ...]

    @property
    def omitted_playback_ids(self):
        return frozenset(row['playback_id'] for row in self.rows if row['action'] == 'omit')

    def summary(self):
        omitted = [row for row in self.rows if row['action'] == 'omit']
        normalized = [row for row in self.rows if row['action'] == 'omit_normalization']
        return dict(enabled=True, target_view='force_capacity_binding',
                    source_unchanged=True, slot_limit=SLOT_LIMIT,
                    verified_sample_byte_scope=VERIFIED_SAMPLE_BYTES,
                    retained_sample_ids=[s.sample_id for s in self.analysis.samples],
                    omitted_playback_count=len(omitted),
                    omitted_playback_ids=[row['playback_id'] for row in omitted],
                    omitted_sample_ids=list(dict.fromkeys(row['sample_id'] for row in omitted)),
                    omitted_duration_samples=sum(row['duration_samples'] for row in omitted),
                    normalization_omitted_playback_ids=[row['playback_id'] for row in normalized],
                    normalization_omitted_duration_samples=sum(row['duration_samples'] for row in normalized),
                    all_pcm_omitted=bool(self.rows) and not self.analysis.playbacks,
                    source_end_vgmticks=self.analysis.source_end_vgmticks,
                    source_loop_vgmticks=self.analysis.source_loop_vgmticks)

    def dump(self, outdir, stem):
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        report = dict(schema_version=1, summary=self.summary(), rows=self.rows)
        (out / f'{stem}.pcm.force_capacity.json').write_text(
            json.dumps(report, indent=2) + '\n', encoding='utf-8')
        fields = tuple(self.rows[0]) if self.rows else ('playback_id', 'sample_id', 'action')
        with (out / f'{stem}.pcm.force_capacity.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(self.rows)


def select_capacity(analysis, *, omit_playback_ids=(), normalization_ms=8):
    """Select whole samples by first use; callers still validate original evidence.

    The byte scope is the verified projection scope, not the PDX format limit.
    Final normalization omissions consume no slots; their source evidence is
    still validated by the caller and their diagnostics remain separate.
    Oversized samples consume no slot. Once 96 slots are bound, later new
    identities stay omitted; later uses of already bound samples survive.
    """
    from conversion_config import normalization_samples
    omission_bound = normalization_samples(normalization_ms)
    normalization_ids = set(omit_playback_ids)
    samples = {s.sample_id: s for s in analysis.samples}
    retained, rejected, rows = {}, {}, []
    ordered = sorted(analysis.playbacks,
                     key=lambda p: (p.start_vgmticks, p.first_source_event_id, p.playback_id))
    for p in ordered:
        sample = samples.get(p.sample_id)
        kind, actual, limit, reason = '', None, None, ''
        normalized = (p.playback_id in normalization_ids
                      and p.end_vgmticks - p.start_vgmticks <= omission_bound)
        if normalized:
            reason = f'intentional output omission within {normalization_ms:g} ms; no binding needed'
        elif p.sample_id in rejected:
            kind, actual, limit, reason = rejected[p.sample_id]
        elif sample is not None and p.sample_id not in retained:
            if len(sample.encoded_bytes) > VERIFIED_SAMPLE_BYTES:
                kind, actual, limit = 'verified_sample_byte_scope', len(sample.encoded_bytes), VERIFIED_SAMPLE_BYTES
                reason = 'whole sample exceeds verified projection byte scope; no truncation'
            elif len(retained) >= SLOT_LIMIT:
                kind, actual, limit = 'slot_capacity', len(retained) + 1, SLOT_LIMIT
                reason = 'single PDX bank is full; later new sample omitted'
            else:
                retained[p.sample_id] = sample
            if kind:
                rejected[p.sample_id] = (kind, actual, limit, reason)
        rows.append(dict(playback_id=p.playback_id, sample_id=p.sample_id,
                         source_first_event_id=p.first_source_event_id,
                         source_last_event_id=p.last_source_event_id,
                         source_start_vgmticks=p.start_vgmticks,
                         source_end_vgmticks=p.end_vgmticks,
                         duration_samples=p.end_vgmticks-p.start_vgmticks,
                         action='omit_normalization' if normalized else 'omit' if kind else 'retain', cap_kind=kind,
                         actual=actual, limit=limit, reason=reason))
    omitted = {row['playback_id'] for row in rows if row['action'] != 'retain'}
    target = replace(analysis, samples=tuple(retained.values()),
                     playbacks=tuple(p for p in analysis.playbacks if p.playback_id not in omitted))
    return CapacityView(target, tuple(rows))
