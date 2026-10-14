"""Create private finite reference phrases without using the VGM converter.

This bounded MML reader handles the selected reference's explicit lengths and
finite repeats. Unknown syntax or a cut through a timed token is rejected.
Private score text and sample bytes are written only beneath ignored roots.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from mdx_reference_expectations import read_expectations, write_expectations

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from mdx_compiler import compile_mxc

REFERENCE = ROOT/'tests/fixtures/local_only/opm_oki6258/mdx_pdx/FinalFantasy/FF4/MACHAN'
DESTINATION = ROOT/'tests/fixtures/local_only/derived_mdx/FF4SIREN'
TOKEN = re.compile(
    r'\]|\[|L|MP\d+,\d+,\d+|MD\d+|@(?:q|t|v)?\d+|'
    r'n\d+,(?:%\d+|\d+)\.*|[a-gr][+-]?(?:%\d+|\d+)\.*'
    r'(?:\^(?:%\d+|\d+)\.*)*|[ovptFD][+-]?\d+|[<>()]|\d+')
TIMED = re.compile(r'^(?:n\d+,|[a-gr][+-]?)(.*)$')


def duration(token):
    match = TIMED.fullmatch(token)
    if not match:
        return Fraction(0)
    total = Fraction(0)
    for part in match[1].split('^'):
        base = part.rstrip('.')
        length = Fraction(int(base[1:])) if base.startswith('%') else Fraction(192, int(base))
        for dot in range(len(part)-len(base)):
            length += (Fraction(int(base[1:])) if base.startswith('%') else Fraction(192, int(base))) / (2**(dot+1))
        total += length
    return total


def score_parts(text):
    prefix, tracks = [], {name: [] for name in 'ABCDEFGHP'}
    seen_music = False
    for number, line in enumerate(text.replace('\x1a', '').splitlines(), 1):
        match = re.match(r'^([A-H]|P)\s+(.*)$', line)
        if match:
            seen_music = True
            tracks[match[1]].append((number, match[2]))
        elif not seen_music:
            prefix.append(line)
        elif line.strip():
            raise ValueError(f'Unexpected non-track text after music at line {number}')
    return prefix, tracks


def tokens(lines):
    result = []
    for number, line in lines:
        position = 0
        while position < len(line):
            if line[position].isspace():
                position += 1
                continue
            match = TOKEN.match(line, position)
            if match is None:
                raise ValueError(f'Unsupported private MML syntax at line {number}, column {position+1}')
            result.append(dict(text=match[0], source_line=number))
            position = match.end()
    return result


def expand(items):
    """Expand finite source repeats with line/iteration provenance, not music inference."""
    def parse(position, nested):
        nodes = []
        while position < len(items):
            item = items[position]
            position += 1
            if item['text'] == '[':
                body, position = parse(position, True)
                if position == len(items) or not items[position]['text'].isdigit():
                    raise ValueError('Explicit finite repeat count required')
                count = int(items[position]['text'])
                if not 1 <= count <= 255:
                    raise ValueError('Repeat count outside supported range')
                nodes.append(dict(body=body, count=count, source_line=item['source_line']))
                position += 1
            elif item['text'] == ']':
                if not nested:
                    raise ValueError('Unmatched repeat end')
                return nodes, position
            elif item['text'].isdigit():
                raise ValueError('Unexpected standalone repeat count')
            else:
                nodes.append(item)
        if nested:
            raise ValueError('Unterminated repeat')
        return nodes, position
    nodes, _ = parse(0, False)
    result = []
    def visit(nodes, path):
        for node in nodes:
            if 'body' in node:
                for iteration in range(node['count']):
                    visit(node['body'], path+[(node['source_line'], iteration+1)])
            else:
                result.append(dict(node, repeat_iterations=path))
                if len(result) > 100000:
                    raise ValueError('Expanded token bound exceeded')
    visit(nodes, [])
    return result


def cut_track(lines, end_tick):
    selected, provenance, tick = [], [], Fraction(0)
    for item in expand(tokens(lines)):
        if tick == end_tick:
            break
        if item['text'] == 'L':
            continue
        length = duration(item['text'])
        if tick+length > end_tick:
            raise ValueError('Cut crosses a source note/rest; no shortening is permitted')
        selected.append(item['text'])
        provenance.append(dict(item, start_tick=int(tick), duration_ticks=int(length)))
        tick += length
    if tick != end_tick:
        raise ValueError('Reference ends before requested cut')
    return ' '.join(selected), provenance


def retained_commands(track, end_tick):
    # Repeat mechanics are intentionally absent from the short expanded scores;
    # every musical/control command in execution order must remain identical.
    return [(c['tick'], c['opcode'], c['operands_hex']) for c in track['commands']
            if c['tick'] < end_tick and c['opcode'] not in (0xf1, 0xf4, 0xf5, 0xf6)]


def compare_reference(original, candidate, cuts):
    if not original['complete'] or not candidate['complete']:
        raise ValueError('Incomplete independent reference interpretation')
    originals = {t['track']: t for t in original['tracks']}
    report = []
    for track in candidate['tracks']:
        name, end = track['track'], cuts[track['track']]
        reference = originals[name]
        before = retained_commands(reference, end)
        after = retained_commands(track, end)
        if before != after:
            index = next((i for i, pair in enumerate(zip(before, after)) if pair[0] != pair[1]), min(len(before), len(after)))
            raise ValueError(f'Reference command mismatch in {name} at index {index}')
        if track['duration_ticks'] != end or track['termination']['kind'] != 'finite_end':
            raise ValueError(f'Wrong finite boundary in {name}')
        expected_events = [e for e in reference['events'] if e['start_tick'] < end]
        if any(e['end_tick'] > end or (e['end_tick'] == end and e.get('hold')) for e in expected_events):
            raise ValueError(f'Unsafe event/held-note boundary in {name}')
        ignore = {'file_offset', 'event_id'}
        clean = lambda rows: [{k: v for k, v in e.items() if k not in ignore} for e in rows]
        if clean(expected_events) != clean(track['events']):
            raise ValueError(f'Event/state expectation mismatch in {name}')
        report.append(dict(track=name, reference_end_tick=end, generated_end_tick=track['duration_ticks'],
                           musical_control_commands=len(before), events=len(expected_events), comparison='pass'))
    if [t['raw_hex'] for t in original['tones']] != [t['raw_hex'] for t in candidate['tones']]:
        raise ValueError('Reference tone definitions changed')
    return report


def private_destination(path):
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in
               (ROOT/'tests/fixtures/local_only', ROOT/'outputs')):
        raise ValueError('Private assets must remain under ignored local_only or outputs')
    return resolved


def generate(reference, out, helper):
    out = private_destination(out)
    out.mkdir(parents=True, exist_ok=True)
    text = (reference/'FF4SIREN.MML').read_bytes().decode('cp932').rstrip('\x1a')
    prefix, tracks = score_parts(text)
    original = read_expectations(reference/'FF4SIREN.MDX', reference/'FF4SIREN.PDX')
    write_expectations(original, out/'original_expected')
    samples = original['pdx']['samples']
    manifest = out/'samples.tsv'
    manifest.write_text('sample_id\tbank\tslot\tfile\n'+''.join(
        f'{s["sample_id"]}\t{s["bank"]}\t{s["slot"]}\toriginal_expected/{samples[s["sample_id"]]["encoded_file"]}\n'
        for s in original['pdx']['slots'] if not s['empty']))
    pdx = out/'FF4SIREN.PDX'
    subprocess.run([str(helper), '--build-pdx', str(manifest), str(pdx)], check=True, capture_output=True)
    if pdx.read_bytes() != (reference/'FF4SIREN.PDX').read_bytes():
        raise ValueError('Repacked PDX does not reproduce the entire reference file')
    results = []
    for stem, end in [('FS432', 432), ('FS768', 768), ('FSONE', None)]:
        cuts = {t['track']: t['duration_ticks'] if end is None else end for t in original['tracks']}
        header = [line for line in prefix if not line.startswith(('#title', '#pcmfile'))]
        score = [f'#title "FF4SIREN private reference {stem}"', '#pcmfile "FF4SIREN.PDX"', *header]
        provenance = []
        for name, lines in tracks.items():
            if end is None:
                # Preserve finite repeat structure in the full one-traversal case.
                for number, body in lines:
                    score.append(name+' '+''.join(i['text']+' ' for i in tokens([(number, body)]) if i['text'] != 'L'))
                tick = Fraction(0)
                for item in expand(tokens(lines)):
                    if item['text'] != 'L':
                        length = duration(item['text'])
                        provenance.append(dict(item, track=name, start_tick=int(tick), duration_ticks=int(length)))
                        tick += length
            else:
                body, rows = cut_track(lines, end)
                for i in range(0, len(body.split()), 32):
                    score.append(name+' '+' '.join(body.split()[i:i+32]))
                provenance.extend(dict(row, track=name) for row in rows)
        mml = out/(stem+'.MML')
        mml.write_text('\n'.join(score)+'\n', encoding='utf-8')
        mdx = out/(stem+'.MDX')
        compile_mxc(mml, mdx, timeout=60, generator=helper, prepared_output=out/'compiler_inputs'/(stem+'.mxc.mml'))
        candidate = read_expectations(mdx, pdx)
        checks = compare_reference(original, candidate, cuts)
        if end is None:
            for before, after in zip(original['tracks'], candidate['tracks']):
                # Also retain zero-time controls at the original cycle boundary.
                original_commands = retained_commands(before, cuts[before['track']]+1)
                if original_commands != retained_commands(after, cuts[after['track']]+1):
                    raise ValueError('One-cycle terminal controls changed')
        candidate['mdx']['path'] = '../../'+mdx.name
        candidate['pdx']['path'] = '../../FF4SIREN.PDX'
        write_expectations(candidate, out/'expected'/stem)
        pcm_expected = json.loads((out/'expected'/stem/'pcm_ir.expected.json').read_text())
        for sample in pcm_expected['samples']:
            sample['encoded_file'] = f'expected/{stem}/'+sample['encoded_file']
        (out/(stem+'.pcm_ir.expected.json')).write_text(json.dumps(pcm_expected, indent=2)+'\n')
        with (out/(stem+'.source_tokens.csv')).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['track', 'source_line', 'text', 'repeat_iterations', 'start_tick', 'duration_ticks'])
            writer.writeheader()
            writer.writerows(provenance)
        tempo = [c['values'][0] for t in candidate['tracks'] for c in t['controls'] if c['kind']=='tempo']
        if len(set(tempo)) != 1:
            raise ValueError('Expected a single constant reference tempo')
        tick_seconds = Fraction(1024*(256-tempo[0]), 4000000)
        results.append(dict(stem=stem, cuts=cuts, nominal_seconds=float(max(cuts.values())*tick_seconds),
            reference_execution_comparison='pass', track_checks=checks,
            pcm_requests=sum(e['kind']=='note' for t in candidate['tracks'] if t['track']=='P' for e in t['events']),
            deliberate_changes=['finite end replaces song loop', 'title and PDX filename casing'],
            repeat_structure='original retained' if end is None else 'finite repeats expanded to select prefix; execution order unchanged',
            forced_stop_added=False, silence_padding_added=False, native_playback='unverified', extraction_comparison='not_run'))
        print(stem, 'reference commands/events/tones: pass')
    report = dict(origin='private reference-derived data; do not redistribute',
        source_hashes={ext: hashlib.sha256((reference/('FF4SIREN.'+ext)).read_bytes()).hexdigest() for ext in ('MML','MDX','PDX')},
        pdx_repacked_whole_file='pass', source_pcm_ir_observed=False,
        physical_stop_verified=False, cases=results)
    (out/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
    rows = '\n'.join(f'| {c["stem"]}.MDX | {c["nominal_seconds"]:.2f} s | {c["pcm_requests"]} |'
                     for c in results)
    (out/'README.md').write_text(
        '# Private FF4SIREN reference phrases\n\n'
        'Reference-derived music and PCM; local_only, do not redistribute.\n\n'
        'Copy FS432.MDX, FS768.MDX, FSONE.MDX and FF4SIREN.PDX together for playback. '
        'Disable automatic repeat to observe the finite ending.\n\n'
        '| File | Nominal duration at 4 MHz | PCM NOTE requests |\n'
        '| --- | --- | --- |\n'+rows+'\n\n'
        'FS432 retains the original beginning through tick 432, including the first '
        'PCM phrase. FS768 retains the beginning through tick 768. Finite repeats '
        'are expanded only to select these prefixes; original musical/control '
        'command execution, note lengths and simultaneous track timing are checked.\n\n'
        'FSONE retains the original finite repeats and one complete traversal, '
        'removing only the song-loop markers. G ends at tick 3660, the other tracks '
        'at 3648; its original 12-tick rest offset and detune are preserved.\n\n'
        'All four original voice definitions, gate/LFO/detune/volume/pan/tempo '
        'commands and exact PCM bytes/binding are retained. Unspecified PCM pan '
        'and volume stay unspecified. No forced-stop command, added silence, '
        'sample re-encoding, or cross-boundary note shortening is introduced. '
        'Title and PDX-name casing are deliberately changed.\n\n'
        'PDX is repacked from original_expected sample bytes with the external '
        'packer and matches the whole original PDX byte for byte. MML uses native '
        'MXC. validation.json and expected/ contain direct command/event/control/'
        'tone/slot comparisons. source_tokens.csv retains source lines and repeat '
        'iterations; compiler_inputs/ preserves prepared native input.\n\n'
        'Root *.pcm_ir.expected.json sample paths are relative to this directory. '
        'Expected datasets distinguish MDX-requested playback from source PCM IR; '
        'physical stopping/reset/consumption are unknown until observed. '
        'This does not certify VGM capture or vgm2mml extraction.\n\n'
        'Listen for normal phrase playback, FM keyboard/meter animation, and PCM '
        'sound after finite end. Record each case separately; native playback is '
        'unverified until checked. Do not replace these finite endings with extra '
        'rests to hide a failure.\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-dir', type=Path, default=REFERENCE)
    parser.add_argument('--outdir', type=Path, default=DESTINATION)
    parser.add_argument('--generator', type=Path, default=ROOT/'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator')
    args = parser.parse_args()
    generate(args.reference_dir.resolve(), args.outdir, args.generator.resolve())


if __name__ == '__main__':
    main()
