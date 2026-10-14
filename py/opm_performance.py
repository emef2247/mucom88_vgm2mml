"""Explicit musical gate inference over an unchanged PSG/SCC OPM projection.

Audibility edges become target Key events. This intentionally changes oscillator
phase at attacks; pitch and level changes within a sounding span keep its gate.
"""
import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from opm_mdx import mdx_tick, projected_samples
from opm_target_state import build_target_trajectory


@dataclass(frozen=True)
class PerformanceWrite:
    write_id: int
    source_chip: str
    source_ch: int | None
    source_row: int | None
    vgmticks: int
    mdx_tick: int
    target_ch: int
    register: int
    data: int
    reason: str
    baseline_write_id: int | None
    performance_row_id: int | None
    inferred_key: bool


@dataclass(frozen=True)
class PerformancePlan:
    writes: tuple
    rows: tuple
    source_end: int
    settings: dict

    @property
    def end_tick(self):
        return mdx_tick(self.source_end)

    def scheduled_writes(self):
        return sorted(self.writes, key=lambda w: (w.mdx_tick, w.target_ch, w.write_id))

    def dump(self, out, stem):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)

        def csv_file(suffix, rows, fields):
            with (out / (stem + suffix)).open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)

        csv_file('.opm_performance.csv', self.rows, list(self.rows[0]) if self.rows else
                 ['performance_row_id', 'source_chip', 'source_ch', 'source_row',
                  'vgmticks', 'vgmticks_end', 'inferred_transition'])
        csv_file('.opm_performance.writes.csv', [asdict(w) for w in self.writes],
                 list(PerformanceWrite.__dataclass_fields__))
        trajectory = build_target_trajectory(self.scheduled_writes(), end_tick=self.end_tick,
                                             source_end_vgmticks=self.source_end)
        paths = [out / (stem + '.opm_performance.state.csv'),
                 out / (stem + '.opm_performance.intervals.csv')]
        trajectory.dump(state_csv=paths[0], intervals_csv=paths[1])
        for path in paths:
            with path.open(encoding='utf-8', newline='') as stream:
                reader = csv.DictReader(stream)
                fields = reader.fieldnames + ['key_origin', 'source_key_observed']
                rows = [dict(r, key_origin='musical_attack_inferred', source_key_observed=False)
                        for r in reader]
            with path.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)
        report = dict(self.settings, source_end_vgmticks=self.source_end,
                      end_mdx_tick=self.end_tick, returned_end_expected=projected_samples(self.end_tick),
                      performance_writes=len(self.writes), performance_rows=len(self.rows),
                      inferred_attacks=sum(w.inferred_key and bool(w.data & 0x78) for w in self.writes),
                      inferred_releases=sum(w.inferred_key and not w.data & 0x78 for w in self.writes))
        (out / (stem + '.opm_performance.json')).write_text(
            json.dumps(report, indent=2) + '\n', encoding='utf-8')


def build_performance(baseline, *, terminate=True):
    """Infer gates without modifying source rows or baseline register evidence."""
    build_target_trajectory(baseline.scheduled_writes(), end_tick=baseline.end_tick,
                            source_end_vgmticks=baseline.source_end)
    writes, rows = [], []
    grouped = {}
    for w in baseline.writes:
        grouped.setdefault((w.source_chip, w.source_ch, w.source_row), []).append(w)

    def copy(w, row_id):
        writes.append(PerformanceWrite(len(writes), w.source_chip, w.source_ch, w.source_row,
                      w.vgmticks, w.mdx_tick, w.target_ch, w.register, w.data, w.reason,
                      w.write_id, row_id, False))

    def key(chip, ch, source_row, sample, target, active, row_id):
        writes.append(PerformanceWrite(len(writes), chip, ch, source_row, sample, mdx_tick(sample),
                      target, 8, target | (0x78 if active else 0),
                      'inferred musical attack' if active else 'inferred musical release',
                      None, row_id, True))

    parts = sorted({(r['source_chip'], r['source_ch']) for r in baseline.rows},
                   key=lambda part: part[1] + (5 if part[0] == 'psg' else 0))
    for chip, ch in parts:
        target = ch + (5 if chip == 'psg' else 0)
        detached = grouped.get((chip, ch, None), [])
        initial = [w for w in detached if not w.reason.startswith('terminal ')]
        terminal = [w for w in detached if w.reason.startswith('terminal ')]
        for w in initial:
            if w.register != 8 or not w.data & 0x78:
                copy(w, None)
        gate = False
        for source in (r for r in baseline.rows if (r['source_chip'], r['source_ch']) == (chip, ch)):
            row_id, first = len(rows), len(writes)
            audible = bool(source['target_audible'])
            row_writes = grouped.get((chip, ch, source['source_row']), [])
            before = gate
            if gate and not audible:
                key(chip, ch, source['source_row'], source['vgmticks'], target, False, row_id)
            for w in row_writes:
                copy(w, row_id)
            if audible and not gate:
                key(chip, ch, source['source_row'], source['vgmticks'], target, True, row_id)
            gate = audible
            transition = ('attack' if audible else 'release') if before != audible else (
                'continuation' if audible else 'rest')
            rows.append(dict(source, performance_row_id=row_id, gate_before=before, gate_after=gate,
                             inferred_transition=transition, state_origin='projected_opm',
                             key_origin='musical_attack_inferred', source_key_observed=False,
                             performance_write_ids=json.dumps(list(range(first, len(writes)))),
                             baseline_write_ids=json.dumps([w.write_id for w in row_writes])))
        if gate and terminate:
            key(chip, ch, None, baseline.source_end, target, False, None)
        for w in terminal if terminate else ():
            if w.register != 8:
                copy(w, None)
    settings = dict(baseline.settings, sourcegate='inferred_from_audibility',
                    onset_policy='audibility_edges', phase_preserved=False,
                    key_origin='musical_attack_inferred', source_key_observed=False)
    plan = PerformancePlan(tuple(writes), tuple(rows), baseline.source_end, settings)
    build_target_trajectory(plan.scheduled_writes(), end_tick=plan.end_tick,
                            source_end_vgmticks=plan.source_end)
    return plan
