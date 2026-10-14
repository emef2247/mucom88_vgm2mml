"""Authored MXC reference assets for Segment/PCM IR validation and listening.

No converter is used to generate expectations. External MXC and PDX packing
are checked against authored intent and an independent MDX/PDX reader.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from mdx_compiler import compile_mxc
from mdx_reference_expectations import read_expectations, write_expectations

DESTINATION = ROOT / 'tests/fixtures/public/mdx_reference'
TICK_SECONDS = Fraction(16 * 1024, 4000000)

# Original glue, linked to mdxtools' encoder without copying its algorithm.
# Read pairs explicitly: the upstream standalone feof loop emits an extra byte.
ENCODER_GLUE = r'''
#include <stdint.h>
#include <stdio.h>
#include "adpcm.h"
int main(void) {
    struct adpcm_status state;
    int16_t pair[2];
    adpcm_init(&state);
    for (;;) {
        size_t count = fread(pair, sizeof(*pair), 2, stdin);
        if (!count) return ferror(stdin) ? 2 : 0;
        if (count != 2) return 3;
        unsigned char low = adpcm_encode(pair[0], &state) & 15;
        unsigned char high = adpcm_encode(pair[1], &state) & 15;
        if (putchar(low | (high << 4)) == EOF) return 4;
    }
}
'''


def voice(number, multiplier):
    rows = [f'31,0,0,15,0,{level},0,{multiplier},0,0,0'
            for level in (127, 127, 127, 24)]
    return '@{} = {{\n{}\n0,7,15\n}}\n'.format(number, ',\n'.join(rows) + ',')


def authored_cases():
    # Tick, duration, slot; q4 deliberately gates a long sample early.
    yield dict(stem='GATEEND', title='Public: early stop and sample exhaustion', end=168,
        fm='A @0 @t240 q8 v12 p3 o4 c16 d16 e16 r16 c16 r16 d16 r16 e16 r16 r4',
        pcm='P @0 F4 p3 q4 @v100 n0,16 r16 n0,16 r16 n1,8 r8 n0,16 r16 r4',
        q=4, requests=[(0, 12, 0, False, 4, 3), (24, 12, 0, False, 4, 3),
                       (48, 24, 1, False, 4, 3), (96, 12, 0, False, 4, 3)],
        fm_notes=[(0,12,48), (12,12,50), (24,12,52), (48,12,48),
                  (72,12,50), (96,12,52)],
        purpose='Long and short assets gated before exhaustion; repeated triggers and final silence')
    yield dict(stem='HOLDEND', title='Public: held PCM then finite stop', end=384,
        fm='A @0 @t240 q8 v12 p3 o4 c%192&c%128 r%64',
        pcm='P @0 F4 p3 q8 @v100 n0,%192&n0,%128 r%64', q=8,
        requests=[(0,192,0,True,4,3), (192,128,0,False,4,3)],
        fm_notes=[(0,192,48), (192,128,48)],
        purpose='One held sample across two notes, second unheld note releases before asset exhaustion')
    yield dict(stem='RATES', title='Public: PCM rates and stereo positions', end=240,
        fm='A @0 @t240 q8 v12 p3 o4 c%24 r%24 d%24 r%24 e%24 r%24 c%24 r%72',
        pcm='P @0 q8 @v100 F4 p1 n1,%24 r%24 F0 p2 n1,%24 r%24 F4 p3 n1,%24 r%24 n1,%24 r%72',
        q=8, requests=[(0,24,1,False,4,1), (48,24,1,False,0,2),
                       (96,24,1,False,4,3), (144,24,1,False,4,3)],
        fm_notes=[(0,24,48), (48,24,50), (96,24,52), (144,24,48)],
        purpose='Same short asset at F4/F0 and p1/p2/p3, separated by rests')
    yield dict(stem='FMSTATE', title='Public: FM keyboard voice volume and pan', end=192,
        fm='A @0 @t240 q8 v12 p3 o4 c16&v8p1c16&p2c16 r16 @1 v12 p3 g16 a16 >c16 <r16 [c16 r16]2 r4',
        pcm='P r%192', q=8, requests=[],
        fm_notes=[(0,12,48), (12,12,48), (24,12,48), (48,12,55),
                  (60,12,57), (72,12,60), (96,12,48), (120,12,48)],
        purpose='Held FM pitch with in-note volume/pan changes, voice change, octave change and finite repeat')


def _run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return result


def make_samples(out, mdxtools):
    with tempfile.TemporaryDirectory(prefix='public-adpcm-') as temporary:
        folder = Path(temporary)
        glue = folder / 'encode.c'
        glue.write_text(ENCODER_GLUE)
        encoder = folder / 'encode'
        _run(['gcc', '-O2', '-I', str(mdxtools), str(glue),
              str(mdxtools / 'adpcm.c'), '-o', str(encoder)])
        samples = []
        for sample_id, (count, period) in enumerate(((120000, 48), (800, 24))):
            linear = []
            for i in range(count):
                phase = i % period
                triangle = (phase if phase < period//2 else period-phase) * 4 - period
                envelope = min(i, count-1-i, 128)
                linear.append(triangle * 300 * envelope // (period * 128))
            raw = struct.pack('<' + 'h' * count, *linear)
            encoded = _run([str(encoder)], input=raw).stdout
            if len(encoded) != count // 2:
                raise ValueError('Encoder returned an unexpected sample length')
            filename = f'samples/{sample_id:03d}.adpcm'
            (out / filename).parent.mkdir(parents=True, exist_ok=True)
            (out / filename).write_bytes(encoded)
            samples.append(dict(sample_id=sample_id, codec='msm6258-adpcm4-low-first',
                encoded_file=filename, byte_length=len(encoded),
                sha256=hashlib.sha256(encoded).hexdigest(),
                source='original integer triangle with authored fade',
                authored_linear_samples=count, period_samples=period))
        return samples


def intended_pcm(case, samples):
    events = []
    prior_hold = False
    for start, duration, slot, hold, rate, pan in case['requests']:
        gate = (duration - 1) * case['q'] // 8 + 1
        events.append(dict(sample_id=slot, start_tick=start, end_tick=start+duration,
            duration_ticks=duration, requested_gate_end_tick=None if hold else start+gate,
            outgoing_hold=hold, continuation=prior_hold,
            rate_code=rate, pan_code=pan, requested_volume=100,
            start_seconds=str(start*TICK_SECONDS),
            gate_end_seconds=None if hold else str((start+gate)*TICK_SECONDS),
            physical_stop_observed=None, decoder_reset_observed=None,
            consumed_nibbles=None, transfer_event_ids=None))
        prior_hold = hold
    return dict(schema='pcm-ir-reference-expectation-v1',
        variant='authored-score-intent',
        provenance='authored sample bytes and score intent; no vgm2mml output',
        clock_domain='reference MDX ticks; source VGM timing not captured',
        tick_seconds=str(TICK_SECONDS), samples=samples,
        playback_requests=events,
        unverified=['physical STOP', 'decoder reset/consumption', 'VGM transfer schedule'],
        bindings=[dict(sample_id=s['sample_id'], bank=0, slot=s['sample_id']) for s in samples])


def check_intent(case, actual):
    if not actual['complete']:
        raise ValueError('Reference interpretation incomplete: ' + case['stem'])
    tracks = {t['track']: t for t in actual['tracks']}
    if any(tracks[name]['duration_ticks'] != case['end'] for name in ('A', 'P')):
        raise ValueError('Native compiler duration differs from authored score')
    fm = [(e['start_tick'], e['duration_ticks'], e['midi_note'])
          for e in tracks['A']['events'] if e['kind'] == 'note']
    if fm != case['fm_notes']:
        raise ValueError(f'Native FM note expectations differ: {case["stem"]}: {fm}')
    pcm = [(e['start_tick'], e['duration_ticks'], e['slot'], e['hold'], e['rate_code'], e['pan'])
           for e in tracks['P']['events'] if e['kind'] == 'note']
    if pcm != case['requests']:
        raise ValueError(f'Native PCM note expectations differ: {case["stem"]}: {pcm}')
    for event in tracks['P']['events']:
        if event['kind'] == 'note' and event['gate_ticks'] != (event['duration_ticks']-1)*case['q']//8+1:
            raise ValueError('Compiled gate differs from authored q')


def generate(out, mdxtools, helper):
    out.mkdir(parents=True, exist_ok=True)
    samples = make_samples(out, mdxtools)
    manifest = out / 'samples.tsv'
    manifest.write_text('sample_id\tbank\tslot\tfile\n' + ''.join(
        f'{s["sample_id"]}\t0\t{s["sample_id"]}\t{s["encoded_file"]}\n' for s in samples))
    _run([str(helper), '--build-pdx', str(manifest), str(out / 'PUBPCM.PDX')])
    records = []
    evidence = ROOT / 'outputs/reference_validation_2026-10-10/public_build'
    evidence.mkdir(parents=True, exist_ok=True)
    for case in authored_cases():
        mml = out / (case['stem'] + '.MML')
        mml.write_text(f'#title "{case["title"]}"\n#pcmfile "PUBPCM.PDX"\n'
            '; Original public validation score, MIT; no reference music copied.\n'
            + voice(0,1) + voice(1,2) + case['fm'] + '\n' + case['pcm'] + '\n')
        mdx = out / (case['stem'] + '.MDX')
        compile_mxc(mml, mdx, timeout=60, generator=helper,
                    prepared_output=evidence / (case['stem'] + '.mxc.mml'))
        actual = read_expectations(mdx, out / 'PUBPCM.PDX')
        check_intent(case, actual)
        for sample in actual['pdx']['samples']:
            authored = samples[sample['sample_id']]
            if sample['sha256'] != authored['sha256'] or sample['byte_length'] != authored['byte_length']:
                raise ValueError('Packed PDX differs from authored sample')
        if any(not s['bounds_valid'] or (s['empty'] and s['offset']) for s in actual['pdx']['slots']):
            raise ValueError('PDX table bounds/empty slots invalid')
        actual['mdx']['path'] = '../../' + mdx.name
        actual['pdx']['path'] = '../../PUBPCM.PDX'
        write_expectations(actual, out / 'expected' / case['stem'])
        (out / (case['stem'] + '.pcm_ir.expected.json')).write_text(
            json.dumps(intended_pcm(case, samples), indent=2) + '\n')
        records.append(dict(stem=case['stem'], purpose=case['purpose'],
            end_ticks=case['end'], nominal_seconds=float(case['end']*TICK_SECONDS),
            authored_intent_comparison='pass', pdx_bytes='pass',
            checked_fields=['FM pitch/onset/duration', 'A/P total ticks',
                'PCM onset/duration/sample slot/hold/rate/pan/gate ticks',
                'PDX sample bytes/lengths/table bounds/empty slots'],
            mdx_sha256=hashlib.sha256(mdx.read_bytes()).hexdigest(),
            extraction_comparison='not_run', native_mmdsp_listening='unverified'))
        print(case['stem'], 'MXC / authored intent / PDX bytes: pass')
    (out / 'validation.json').write_text(json.dumps(dict(
        fixture_origin='entirely authored for this project', license='MIT',
        compiler='native MXC v1.01 via existing adapter',
        pcm_packer='external mdx-fixture-generator --build-pdx',
        pdx_sha256=hashlib.sha256((out/'PUBPCM.PDX').read_bytes()).hexdigest(),
        converter_used_for_expectations=False, cases=records), indent=2) + '\n')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, default=DESTINATION)
    parser.add_argument('--mdxtools-root', type=Path, default=ROOT/'outputs/mdx-reference-tools/mdxtools')
    parser.add_argument('--generator', type=Path, default=ROOT/'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator')
    args = parser.parse_args()
    generate(args.outdir.resolve(), args.mdxtools_root.resolve(), args.generator.resolve())


if __name__ == '__main__':
    main()
