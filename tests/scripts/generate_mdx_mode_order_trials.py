"""Controlled mode and pre-note ordering trials; never change production conversion."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from generate_local_mdx_reference_phrases import private_destination
from mdx_reference_expectations import read_mdx
from mdx_pdx_structure_audit import audit, export, pdx_banks

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from mdx_compiler import compile_mxc


def run(args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, check=True)
    return result.stdout


def clean_event(event):
    return {k: v for k, v in event.items() if k not in ('file_offset', 'event_id', 'bank_origin')}


def verify_mode(before, after, extended):
    if not before['complete'] or not after['complete']:
        raise ValueError('Incomplete interpretation')
    if len(after['tracks']) != (16 if extended else 9):
        raise ValueError('Wrong diagnostic layout')
    for original, candidate in zip(before['tracks'], after['tracks']):
        if original['duration_ticks'] != candidate['duration_ticks']:
            raise ValueError('Timing changed')
        if [clean_event(e) for e in original['events']] != [clean_event(e) for e in candidate['events']]:
            raise ValueError('Requested event state changed')
        commands = lambda t: [(c['tick'], c['opcode'], c['operands_hex']) for c in t['commands'] if c['opcode'] != 0xe8]
        if commands(original) != commands(candidate):
            raise ValueError('Executed command order or operands changed')
    if [t['raw_hex'] for t in before['tones']] != [t['raw_hex'] for t in after['tones']]:
        raise ValueError('Voice definitions changed')
    for t in after['tracks'][9:]:
        if t['events'] or t['controls'] or t['termination']['kind'] != 'finite_end':
            raise ValueError('Additional PCM tracks must be inactive')
    if before['title'] != after['title'] or before['pdx_name'] != after['pdx_name']:
        raise ValueError('Metadata changed')


def generate(out, probe, generator):
    out = private_destination(out)
    out.mkdir(parents=True, exist_ok=True)
    source = ROOT/'tests/fixtures/public/mdx_reference'
    for name in ('RATES.MDX', 'RATES.MML', 'PUBPCM.PDX'):
        shutil.copy2(source/name, out/name)
    before = read_mdx((out/'RATES.MDX').read_bytes())
    cases = []
    for stem, mode in (('RT9', 'mdx9'), ('RT16', 'mdx16')):
        log = run([probe, mode, out/'RATES.MDX', out/(stem+'.MDX')])
        after = read_mdx((out/(stem+'.MDX')).read_bytes(), allow_pcm8=True)
        verify_mode(before, after, mode == 'mdx16')
        result = audit(out/(stem+'.MDX'), out/'PUBPCM.PDX', 1)
        export(result, out/'expected'/stem)
        cases.append(dict(stem=stem, route=mode, static='pass', native='unverified',
            identical_bytes=(out/(stem+'.MDX')).read_bytes()==(out/'RATES.MDX').read_bytes(),
            mdx_sha256=result['mdx_sha256'], note='E8 changes playback mode as well as layout' if mode=='mdx16' else 'Typed nine-track reserialization control'))
        (out/(stem+'.log')).write_text(log)
    run([generator, '--compile-only', out/'RATES.MML', out/'RTM16.MDX'])
    after = read_mdx((out/'RTM16.MDX').read_bytes(), allow_pcm8=True)
    # Compilation may choose different raw control spellings; reject any
    # changed requested state before treating this as a comparable trial.
    for a, b in zip(before['tracks'], after['tracks']):
        if [clean_event(e) for e in a['events']] != [clean_event(e) for e in b['events']]:
            raise ValueError('mmlx changed the requested event state')
    before_tones = {t['voice']: t['raw_hex'] for t in before['tones']}
    after_tones = {t['voice']: t['raw_hex'] for t in after['tones']}
    used_voices = {e['voice'] for t in before['tracks'][:8] for e in t['events'] if e['kind']=='note'}
    if any(before_tones[v] != after_tones.get(v) for v in used_voices):
        raise ValueError('mmlx changed a used voice definition')
    result = audit(out/'RTM16.MDX', out/'PUBPCM.PDX', 1)
    export(result, out/'expected/RTM16')
    cases.append(dict(stem='RTM16', route='mmlx0.2.0 default16', static='pass', native='unverified',
        mdx_sha256=result['mdx_sha256'], unused_added_voices=sorted(after_tones.keys()-before_tones.keys()),
        note='Same authored MML, separately compiled; requested events and used tones checked; unused tones may be retained'))

    # Repacking is a separate static test, never mixed into the mode trials.
    repacks = []
    for stem, banks in (('RAY01C',3), ('RAYFOR',1)):
        original = ROOT/'outputs/listen/finite_end_references'/(stem+'.PDX')
        destination = out/'pdx_repack'/(stem+'.PDX')
        destination.parent.mkdir(exist_ok=True)
        run([probe, 'pdx', original, destination])
        left, right = pdx_banks(original.read_bytes(),banks), pdx_banks(destination.read_bytes(),banks)
        if [(r['length'],r['sha256']) for r in left] != [(r['length'],r['sha256']) for r in right]:
            raise ValueError('Bank/slot sample content changed')
        repacks.append(dict(file=stem+'.PDX', banks=banks,
            whole_bytes_identical=original.read_bytes()==destination.read_bytes(),
            all_bindings_length_payload='pass', native='not_tested',
            changed_offsets=sum(a['offset']!=b['offset'] for a,b in zip(left,right)),
            original_bytes=original.stat().st_size, repacked_bytes=destination.stat().st_size))

    header = (source/'FMSTATE.MML').read_text().split('\nA ')[0]
    header = '\n'.join(line for line in header.splitlines() if not line.startswith(('#title','#pcmfile')))
    probes = {
        'ORDBASE':'@0 v12 p1 o4 l16 c d',
        'ORDPRE':'@0 o4 l16 p1 v12 c d',
        'ORDVO':'v12 @0 p1 o4 l16 c d',
        'ORDPOST':'@0 v12 p1 o4 l16 c v8 p2 o5 d',
        'ORDOCT':'@0 v12 p1 o4 c16 >d16',
    }
    ordering = {}
    for stem, body in probes.items():
        score = out/(stem+'.MML')
        score.write_text(f'#title "Control ordering {stem}"\n'+header+'\nA @t240 q8 '+body+'\n')
        output = out/(stem+'.MDX')
        compile_mxc(score, output, timeout=60, generator=generator, prepared_output=out/'compiler_inputs'/(stem+'.mxc.mml'))
        parsed = read_mdx(output.read_bytes())
        track = parsed['tracks'][0]
        ordering[stem] = dict(body=body, notes=[clean_event(e) for e in track['events']],
            ordered_commands=[dict(tick=c['tick'],opcode=c['opcode_hex'],operands=c['operands_hex']) for c in track['commands']],
            native='unverified')
    base = ordering['ORDBASE']['notes']
    assert ordering['ORDPRE']['notes'] == base
    assert ordering['ORDVO']['notes'] == base
    post = ordering['ORDPOST']['notes']
    assert post[0] == base[0]
    assert post[1]['midi_note'] == base[1]['midi_note']+12
    assert (post[1]['volume_encoded'],post[1]['pan']) == (8,2)
    assert ordering['ORDOCT']['notes'][1]['midi_note'] == base[1]['midi_note']+12
    (out/'validation.json').write_text(json.dumps(dict(mode_cases=cases,pdx_repacks=repacks,ordering=ordering,production_changed=False),indent=2)+'\n')
    info = run([ROOT/'outputs/mdx-reference-tools/mdxtools/mdxinfo','-u','-H',*[out/(stem+'.MDX') for stem in ('RATES','RT9','RT16','RTM16',*probes)]])
    (out/'mdxinfo.tsv').write_text(info)
    (out/'README.md').write_text(
        '# Mode and command-order diagnostics\n\n'
        'Production conversion is unchanged. RATES is the existing failed native-MXC original; '
        'RT9 reserializes those exact typed commands as9 tracks; RT16 retains them and adds '
        'inactive Q..W and initial E8; RTM16 compiles the same authored MML with mmlx0.2.0 '
        'default16. All four use byte-identical PUBPCM.PDX and the same title. E8 deliberately '
        'changes the PCM playback path, not merely file layout. Native outcomes are unverified.\n\n'
        'Listen to RATES, RT9, RT16 and RTM16 from silence with repeat disabled; record display, '
        'audible music, natural ending and sustained silence after ending. Keep PDX unchanged. '
        'Do not mix the separate pdx_repack/ products into this test.\n\n'
        'ORD* are optional short authored FM-only order probes. ORDBASE/ORDPRE/ORDVO have '
        'identical requested notes/volume/pan/timing. ORDPOST intentionally changes volume, '
        'pan and octave after the first note, affecting the second; ORDOCT raises only octave '
        'for the second note. Octave/default length are compiler state; no independent octave '
        'command exists in their compiled MDX. Static results do not certify MMDSP animation.\n\n'
        'validation.json and expected/ retain full comparison evidence. Private repacked '
        'RayForce samples must not be redistributed.\n')
    print(json.dumps(dict(mode_cases=cases,pdx_repacks=repacks,ordering='pre-note permutations passed; post-note affects following note'),indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir',type=Path,default=ROOT/'outputs/listen/mode_order_trials')
    parser.add_argument('--probe',type=Path,default=ROOT/'outputs/reference_validation_2026-10-10/typed_structure_probe/target/release/typed-structure-probe')
    parser.add_argument('--generator',type=Path,default=ROOT/'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator')
    args=parser.parse_args()
    generate(args.outdir,args.probe,args.generator)


if __name__=='__main__':
    main()
