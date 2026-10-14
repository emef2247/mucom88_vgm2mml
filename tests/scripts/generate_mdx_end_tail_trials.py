"""Data-only FM-tail versus PCM-tail trials; preserve failing originals."""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from generate_local_mdx_reference_phrases import private_destination
from mdx_reference_expectations import read_expectations, write_expectations

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from mdx_compiler import compile_mxc


def add_tail(text, title, track, ticks):
    if track not in ('A', 'P') or ticks < 1:
        raise ValueError('Trial requires A/P and positive ticks')
    if len(re.findall(r'^#title .*$', text, flags=re.M)) != 1:
        raise ValueError('Exactly one authored title required')
    count, remainder = divmod(ticks, 128)
    rests = ['r%128']*count+([f'r%{remainder}'] if remainder else [])
    return re.sub(r'^#title .*$', '#title "'+title+'"', text, flags=re.M).rstrip()+'\n'+track+' '+' '.join(rests)+'\n'


def verify_tail(before, after, selected, ticks):
    if not before['complete'] or not after['complete']:
        raise ValueError('Incomplete diagnostic interpretation')
    summaries = []
    for original, candidate in zip(before['tracks'], after['tracks']):
        name = original['track']
        if name != candidate['track'] or original['termination']['kind'] != 'finite_end':
            raise ValueError('Requires identical finite source layout')
        end = original['duration_ticks']
        expected_end = end+ticks if name == selected else end
        if candidate['duration_ticks'] != expected_end or candidate['termination']['kind'] != 'finite_end':
            raise ValueError('Unexpected trial termination')
        def commands(track, end):
            return [(c['tick'], c['opcode'], c['operands_hex']) for c in track['commands']
                    if c['opcode'] != 0xf1 and c['tick'] < end]
        if commands(original, end) != commands(candidate, end):
            raise ValueError('Original executed commands changed before ending')
        ignore = {'file_offset', 'event_id'}
        clean = lambda rows: [{k: v for k, v in e.items() if k not in ignore} for e in rows]
        if clean(original['events']) != clean([e for e in candidate['events'] if e['start_tick'] < end]):
            raise ValueError('Original event/state changed')
        additions = [e for e in candidate['events'] if e['start_tick'] >= end]
        if any(e['kind'] != 'rest' for e in additions) or sum(e['duration_ticks'] for e in additions) != (ticks if name == selected else 0):
            raise ValueError('Only the selected trailing rests may be added')
        summaries.append(dict(track=name, original_end_tick=end, new_end_tick=expected_end,
                              prefix_comparison='pass', added_rest_ticks=ticks if name==selected else 0))
    if [v['raw_hex'] for v in before['tones']] != [v['raw_hex'] for v in after['tones']]:
        raise ValueError('Voice definitions changed')
    return summaries


def generate(out, helper):
    out = private_destination(out)
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for folder, stem, pdx_name, ticks in (
        (ROOT/'tests/fixtures/public/mdx_reference', 'RATES', 'PUBPCM.PDX', 1024),
        (ROOT/'tests/fixtures/local_only/derived_mdx/FF4SIREN', 'FS432', 'FF4SIREN.PDX', 384)):
        # Byte-preserved controls, including the already-failing original MDX.
        for name in (stem+'.MDX', stem+'.MML', pdx_name):
            shutil.copy2(folder/name, out/name)
        before = read_expectations(folder/(stem+'.MDX'), folder/pdx_name)
        original_tempo = {c['values'][0] for t in before['tracks'] for c in t['controls'] if c['kind']=='tempo'}
        if len(original_tempo) != 1:
            raise ValueError('Trial requires one constant tempo')
        tick_seconds = Fraction(1024*(256-original_tempo.pop()), 4000000)
        for suffix, track in (('F','A'), ('P','P')):
            trial = stem+suffix
            mml = out/(trial+'.MML')
            mml.write_text(add_tail((folder/(stem+'.MML')).read_text(encoding='utf-8'),
                                   'Ending trial '+trial, track, ticks), encoding='utf-8')
            mdx = out/(trial+'.MDX')
            compile_mxc(mml, mdx, timeout=60, generator=helper, prepared_output=out/'compiler_inputs'/(trial+'.mxc.mml'))
            after = read_expectations(mdx, out/pdx_name)
            checks = verify_tail(before, after, track, ticks)
            if (out/pdx_name).read_bytes() != (folder/pdx_name).read_bytes():
                raise ValueError('PDX bytes changed')
            after['mdx']['path'] = '../../'+mdx.name
            after['pdx']['path'] = '../../'+pdx_name
            write_expectations(after, out/'expected'/trial)
            original_end = max(t['duration_ticks'] for t in before['tracks'])
            new_end = max(t['duration_ticks'] for t in after['tracks'])
            cases.append(dict(stem=trial, original=stem, selected_track=track,
                added_ticks=ticks, original_end_seconds=float(original_end*tick_seconds),
                new_end_seconds=float(new_end*tick_seconds), added_seconds=float(ticks*tick_seconds),
                mdx_sha256=hashlib.sha256(mdx.read_bytes()).hexdigest(),
                pdx_sha256=hashlib.sha256((out/pdx_name).read_bytes()).hexdigest(),
                track_checks=checks, static_generation='pass', native_outcome='unverified',
                meaning='Diagnostic change to termination order; not reference-equivalent or a confirmed fix'))
            print(trial, 'unchanged original events/controls/voices/PDX; added tail: pass')
    (out/'validation.json').write_text(json.dumps(dict(cases=cases, production_changed=False), indent=2)+'\n')
    mdxinfo = ROOT/'outputs/mdx-reference-tools/mdxtools/mdxinfo'
    info = subprocess.run([str(mdxinfo), '-u', '-H', *[str(out/(c['stem']+'.MDX')) for c in cases]],
                          capture_output=True, text=True, check=True)
    (out/'mdxinfo.tsv').write_text(info.stdout)
    (out/'mdxinfo.stderr.txt').write_text(info.stderr)
    metadata = list(csv.DictReader(info.stdout.splitlines(), delimiter='\t'))
    if len(metadata) != len(cases) or any(row['Error'] != 'Success' or row['Tracks'] != '9'
                                         or not Path(row['PDX file']).is_file() for row in metadata):
        raise ValueError('mdxinfo metadata/PDX validation failed')
    rows = '\n'.join(f'| {c["stem"]}.MDX | {c["selected_track"]} | {c["original_end_seconds"]:.3f} s | {c["new_end_seconds"]:.3f} s |'
                     for c in cases)
    (out/'README.md').write_text(
        '# Finite-ending tail trials (private package)\n\n'
        'Includes private reference-derived music: do not redistribute. '
        'Playback needs the MDX files plus PUBPCM.PDX and FF4SIREN.PDX together. '
        'Original RATES/FS432 files are copied unchanged for comparison.\n\n'
        '| Trial | Rest added only to track | Original whole-song end | New end |\n'
        '| --- | --- | --- | --- |\n'+rows+'\n\n'
        'F extends FM track A only, leaving every PCM command unchanged. '
        'P extends PCM track P only, leaving FM unchanged. Within each pair the '
        'added rest duration is identical. No sample bytes, original notes, '
        'controls, gate, pan, rate or voices change. RATES already ends with '
        '72 ticks of rest on A and P; these trials extend lifetime/ending order '
        'rather than testing whether any rest exists.\n\n'
        'Start each trial from a silent state; repeat once to distinguish residual '
        'noise from the previous file. Disable automatic repeat. Record whether '
        'noise starts at the original end or only at the new end, whether it '
        'disappears, and whether F7 during the added silent interval stops it. '
        'Record F7 after final end separately. Use the provided observation CSV.\n\n'
        'Static comparisons and mdxinfo are evidence about generated data. All '
        'native outcomes are unverified. A delayed/disappearing noise does not '
        'by itself prove buffer overrun or a permanent fix. Source IR and '
        'production converter are unchanged.\n', encoding='utf-8')
    observation = out/'listening_observations.csv'
    if not observation.exists():
        observation.write_text('case,silent_start,repeat_result,noise_at_original_end,noise_at_new_end,F7_during_tail,F7_after_end,notes\n'+
                               ''.join(c['stem']+',unverified,unverified,unverified,unverified,unverified,unverified,\n' for c in cases))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, default=ROOT/'outputs/listen/finite_end_tail')
    parser.add_argument('--generator', type=Path, default=ROOT/'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator')
    args = parser.parse_args()
    generate(args.outdir, args.generator.resolve())


if __name__ == '__main__':
    main()
