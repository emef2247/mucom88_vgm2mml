"""Bounded, read-only original MDX ending audit; never an acoustic-stop oracle."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from mdx_reference_expectations import ReferenceError, read_mdx, read_pdx


def _latest(rows, field):
    values = [r[field] for r in rows if r.get(field) is not None]
    return max(values) if values else None


def _relation(pcm, fm):
    if pcm is None:
        return 'no_pcm_note'
    if fm is None or pcm > fm:
        return 'pcm_later'
    return 'equal' if pcm == fm else 'fm_later'


def summarize(result):
    """Compare expanded requested timelines, keeping finite vs loop separate."""
    tracks = result['tracks']
    pcm = [t for t in tracks if t['track'] in 'PQRSTUVW']
    fm = [t for t in tracks if t['track'] in 'ABCDEFGH']
    def notes(group):
        return [e for t in group for e in t['events'] if e['kind'] == 'note']
    pcm_notes, fm_notes = notes(pcm), notes(fm)
    endings = {t['track']: t['termination'] for t in tracks}
    finite = result['complete'] and all(e and e['kind'] == 'finite_end' for e in endings.values())
    loops = [t['track'] for t in tracks if t['termination'] and t['termination']['kind'] == 'song_loop']
    incomplete = [t['track'] for t in tracks if not t['complete']]
    pcm_last_note = _latest(pcm_notes, 'end_tick')
    fm_last_note = _latest(fm_notes, 'end_tick')
    pcm_last_gate = _latest(pcm_notes, 'release_tick')
    fm_last_gate = _latest(fm_notes, 'release_tick')
    # Held segments have no expiry request; a later note or rest can release them.
    # Do not substitute their nominal gate as an actual release.
    last_pcm = [dict(track=t['track'], **{k: v for k, v in t['events'][-1].items()
                     if k != 'track'}) for t in pcm if t['events']]
    unknowns = []
    if incomplete:
        unknowns.append('unsupported/malformed command or expansion bound: timeline is partial')
    if loops:
        unknowns.append('song loops: only intro plus one executed cycle; no finite ending')
    if any(e.get('key_delay_ticks') for e in pcm_notes + fm_notes):
        unknowns.append('nonzero key delay: acoustic/key-on offsets not reconstructed')
    if any(t['events'] and t['events'][-1].get('hold') for t in tracks):
        unknowns.append('final held note has no explicit gate release request')
    unknowns.append('physical PCM STOP and acoustic end are not verified by MDX decoding')
    return dict(
        static_decode_complete=result['complete'], finite=finite,
        track_layout=result['track_layout'], loop_tracks=loops,
        incomplete_tracks=incomplete,
        pcm_note_count=len(pcm_notes), fm_note_count=len(fm_notes),
        pcm_last_note_end_tick=pcm_last_note, fm_last_note_end_tick=fm_last_note,
        note_end_relation=_relation(pcm_last_note, fm_last_note),
        pcm_last_release_request_tick=pcm_last_gate,
        fm_last_release_request_tick=fm_last_gate,
        gate_request_relation=_relation(pcm_last_gate, fm_last_gate),
        pcm_sequence_end_tick=max((t['duration_ticks'] for t in pcm), default=None),
        fm_sequence_end_tick=max((t['duration_ticks'] for t in fm), default=None),
        finite_pcm_later_candidate=finite and _relation(pcm_last_note, fm_last_note) == 'pcm_later',
        last_pcm_events=last_pcm,
        latest_pcm_note_events=[e for e in pcm_notes if e['end_tick'] == pcm_last_note],
        latest_fm_note_events=[e for e in fm_notes if e['end_tick'] == fm_last_note],
        track_endings=endings, unknowns=unknowns)


def resolve_pdx(mdx_path, declared):
    if not declared:
        return None
    # Native references commonly omit the extension; never infer a stem match
    # when the MDX explicitly names another PDX.
    name = declared if declared.lower().endswith('.pdx') else declared + '.PDX'
    return next((p for p in mdx_path.parent.iterdir()
                 if p.is_file() and p.name.casefold() == name.casefold()), None)


def audit(root, outdir, max_commands=1000000):
    root, outdir = Path(root), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for mdx in sorted(p for p in root.rglob('*') if p.suffix.lower() == '.mdx'):
        relative = mdx.relative_to(root)
        row = dict(mdx=str(relative))
        detail_dir = outdir / relative.with_suffix('')
        detail_dir.mkdir(parents=True, exist_ok=True)
        try:
            data = mdx.read_bytes()
            decoded = read_mdx(data, allow_pcm8=True, max_commands=max_commands)
            row.update(summarize(decoded))
            row['mdx_sha256'] = hashlib.sha256(data).hexdigest()
            row['declared_pdx'] = decoded['pdx_name']
            pdx = resolve_pdx(mdx, decoded['pdx_name'])
            row['pdx_pair_exists'] = pdx is not None
            row['resolved_pdx'] = pdx.name if pdx else None
            if pdx:
                parsed, _ = read_pdx(pdx.read_bytes())
                row['pdx_nonempty_slots_bank0'] = sum(not s['empty'] for s in parsed['slots'])
                row['pdx_bank0_bounds_valid'] = all(s['bounds_valid'] for s in parsed['slots'])
                row['pdx_scope'] = 'first 96 slots only; PCM8 extended banks and playback modes unverified'
            (detail_dir / 'decoded.json').write_text(json.dumps(decoded, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            commands = [c for t in decoded['tracks'] for c in t['commands']]
            fields = list(dict.fromkeys(k for c in commands for k in c))
            with (detail_dir / 'commands.csv').open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(commands)
        except (ReferenceError, OSError) as error:
            row.update(static_decode_complete=False, finite=False, error=str(error))
        summaries.append(row)
    (outdir / 'summary.json').write_text(json.dumps(dict(
        schema='mdx-reference-ending-audit-v1',
        domain='expanded MDX requested ticks; not physical sound duration',
        references=summaries), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    fields = list(dict.fromkeys(k for r in summaries for k in r))
    with (outdir / 'summary.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                          for k, v in r.items()} for r in summaries)
    overview = ['mdx', 'resolved_pdx', 'pdx_pair_exists', 'static_decode_complete',
                'finite', 'note_end_relation', 'pcm_last_note_end_tick',
                'fm_last_note_end_tick', 'pcm_last_release_request_tick',
                'fm_last_release_request_tick', 'pcm_sequence_end_tick',
                'fm_sequence_end_tick', 'finite_pcm_later_candidate']
    with (outdir / 'overview.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=overview)
        writer.writeheader()
        writer.writerows({k: r.get(k) for k in overview} for r in summaries)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--outdir', required=True)
    parser.add_argument('--max-commands', type=int, default=1000000)
    args = parser.parse_args()
    rows = audit(args.root, args.outdir, args.max_commands)
    for r in rows:
        print(json.dumps({k: r.get(k) for k in ('mdx', 'static_decode_complete', 'finite',
            'loop_tracks', 'incomplete_tracks', 'pcm_last_note_end_tick',
            'fm_last_note_end_tick', 'note_end_relation', 'pdx_pair_exists', 'error')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
