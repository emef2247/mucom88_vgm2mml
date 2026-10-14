"""FMSTATE-based public clock controls, separate from converter output."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from mdx_compiler import compile_mxc
from mdx_reference_expectations import read_expectations

FIXTURE = ROOT / 'tests/fixtures/public/mdx_reference'
CLOCKS = (256, 2048, 4096, 8192, 16384)


def rest(ticks):
    parts = []
    while ticks:
        length = min(128, ticks)
        parts.append(f'r%{length}')
        ticks -= length
    return ' '.join(parts)


def score(clock_us, baseline_text):
    if clock_us not in CLOCKS:
        raise ValueError('unsupported control clock')
    unit = int(Fraction(12 * 4096, clock_us))
    long_rest = int(Fraction(48 * 4096, clock_us))
    header = baseline_text.split('\nA ', 1)[0]
    header = header.replace('Public: FM keyboard voice volume and pan',
                            f'Public FMSTATE clock {clock_us} us')
    note = lambda pitch: f'{pitch}%{unit}'
    phrase = (f'@0 q8 v12 p3 o4 {note("c")}&v8p1{note("c")}&p2{note("c")} '
              f'{rest(unit)} @1 v12 p3 {note("g")} {note("a")} >{note("c")} '
              f'<{rest(unit)} [{note("c")} {rest(unit)}]2 {rest(long_rest)}')
    duration = int(Fraction(1536 * 4096, clock_us))
    return f'{header}\nA @t{256 - clock_us // 256} [{phrase}]8\nP {rest(duration)}\n'


def performance(events, clock_us):
    """Discard encoding splits, retaining requested attacks/releases and state intervals."""
    tick = Fraction(clock_us, 1000000)
    intervals, attacks, releases = [], [], []
    for event in events:
        start, end = event['start_tick'] * tick, event['end_tick'] * tick
        state = (event['kind'],) + (tuple(event[key] for key in
                 ('midi_note', 'voice', 'volume_encoded', 'pan')) if event['kind'] == 'note' else ())
        if event['kind'] == 'note':
            if event['retrigger_intent']:
                attacks.append((start, event['midi_note']))
            if event['release_tick'] is not None:
                releases.append(event['release_tick'] * tick)
        merge = event['kind'] == 'rest' or event.get('continuation', False)
        if intervals and merge and intervals[-1][1] == start and intervals[-1][2] == state:
            intervals[-1] = (intervals[-1][0], end, state)
        else:
            intervals.append((start, end, state))
    return dict(intervals=intervals, attacks=attacks, releases=releases)


def repeated_expectation(baseline):
    events = []
    for repetition in range(8):
        for event in baseline['tracks'][0]['events']:
            row = dict(event)
            for key in ('start_tick', 'end_tick', 'release_tick'):
                if row.get(key) is not None:
                    row[key] += repetition * 192
            events.append(row)
    return performance(events, 4096)


def validate(result, clock_us, baseline):
    assert result['complete'] and len(result['tracks']) == 9
    assert result['pdx_name'] == 'PUBPCM.PDX'
    tempos = [(track['track'], control['tick'], control['values'])
              for track in result['tracks'] for control in track['controls']
              if control['kind'] == 'tempo']
    assert tempos == [('A', 0, [256 - clock_us // 256])], tempos
    assert [tone['raw_hex'] for tone in result['tones']] == [
        tone['raw_hex'] for tone in baseline['tones']]
    end = Fraction(1536 * 4096, 1000000)
    for track in result['tracks']:
        assert track['termination']['kind'] == 'finite_end', track
        expected_end = end if track['track'] in ('A', 'P') else 0
        assert Fraction(track['duration_ticks'] * clock_us, 1000000) == expected_end
        if track['track'] == 'P':
            assert all(event['kind'] == 'rest' for event in track['events'])
    actual = performance(result['tracks'][0]['events'], clock_us)
    assert actual == repeated_expectation(baseline), (clock_us, actual)
    return actual


def render(renderer, mdx, output):
    subprocess.run([str(renderer), str(mdx), str(output)], cwd=mdx.parent,
                   check=True, capture_output=True, timeout=120)
    with wave.open(str(output), 'rb') as audio:
        frames = audio.readframes(audio.getnframes())
        width = audio.getsampwidth()
        samples = [int.from_bytes(frames[i:i + width], 'little', signed=True)
                   for i in range(0, len(frames), width)]
        if width == 1:
            samples = [value - 128 for value in frames]
        peak = max(map(abs, samples), default=0)
        assert peak > 0, f'Independent renderer produced silence: {mdx}'
        return dict(peak=peak, frames=audio.getnframes(), sample_rate=audio.getframerate(),
                    duration_seconds=audio.getnframes() / audio.getframerate(),
                    timing_oracle=False, native_playback_verified=False,
                    audio_quality_oracle=False,
                    limitation='mdxtools mdx2pcm writes half of each interleaved mixing block; diagnostic nonzero check only')


def generate(outdir, generator, renderer=None):
    outdir.mkdir(parents=True, exist_ok=True)
    copied = {}
    for name in ('FMSTATE.MML', 'FMSTATE.MDX', 'PUBPCM.PDX'):
        shutil.copyfile(FIXTURE / name, outdir / name)
        copied[name] = hashlib.sha256((outdir / name).read_bytes()).hexdigest()
        assert (outdir / name).read_bytes() == (FIXTURE / name).read_bytes()
    baseline = read_expectations(FIXTURE / 'FMSTATE.MDX')
    text = (FIXTURE / 'FMSTATE.MML').read_text(encoding='utf-8')
    fm_only = outdir / 'FMONLY.MML'
    fm_only.write_text(text.replace('#pcmfile "PUBPCM.PDX"\n', ''), encoding='utf-8')
    fm_only_mdx = outdir / 'FMONLY.MDX'
    compile_mxc(fm_only, fm_only_mdx, timeout=60, generator=generator)
    fm_only_result = read_expectations(fm_only_mdx)
    assert fm_only_result['complete'] and fm_only_result['pdx_name'] == ''
    assert fm_only_mdx.read_bytes()[fm_only_result['data_base_file_offset']:] == (
        FIXTURE / 'FMSTATE.MDX').read_bytes()[baseline['data_base_file_offset']:]
    fm_only_case = dict(stem='FMONLY', difference='Only #pcmfile directive removed',
                        body_byte_identical_to_baseline=True,
                        mdx_sha256=hashlib.sha256(fm_only_mdx.read_bytes()).hexdigest(),
                        native_playback='unverified', native_display='unverified', native_stop='unverified')
    if renderer:
        fm_only_case['independent_render'] = render(renderer, fm_only_mdx, outdir / 'FMONLY.wav')
    cases = []
    for clock_us in CLOCKS:
        stem = f'FM{clock_us:04d}'
        source, mdx = outdir / f'{stem}.MML', outdir / f'{stem}.MDX'
        source.write_text(score(clock_us, text), encoding='utf-8')
        compile_mxc(source, mdx, timeout=60, generator=generator)
        result = read_expectations(mdx)
        timeline = validate(result, clock_us, baseline)
        case = dict(stem=stem, clock_us=clock_us, nominal_duration_seconds=6.291456,
                    mdx_sha256=hashlib.sha256(mdx.read_bytes()).hexdigest(),
                    static_requested_performance='pass', attacks=len(timeline['attacks']),
                    releases=len(timeline['releases']), native_playback='unverified',
                    native_display='unverified', native_stop='unverified')
        if renderer:
            case['independent_render'] = render(renderer, mdx, outdir / f'{stem}.wav')
        cases.append(case)
    report = dict(provenance='Original public MIT FMSTATE phrase; no converter or source IR changes',
                  baseline_copies=copied, baseline_previous_user_result='audio/display pass; not refreshed',
                  comparisons='Independent MDX requested pitch, voice, volume, pan, hold, attack/release timing',
                  physical_playback_oracle=False, empty_pdx_control=fm_only_case, cases=cases)
    if renderer:
        report['baseline_independent_render'] = render(renderer, outdir / 'FMSTATE.MDX', outdir / 'FMSTATE.wav')
    (outdir / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (outdir / 'README.txt').write_text(
        'FMSTATE.MML/MDX and PUBPCM.PDX are byte-identical public baseline copies.\n'
        'Listen first to FMSTATE, then FMONLY, then FM4096, then the remaining clocks.\n'
        'FMONLY removes only the PCM file directive; MDX music/tone data are byte-identical to FMSTATE.\n'
        'FM0256, FM2048, FM4096, FM8192, FM16384 repeat its FM phrase eight times.\n'
        'Keep PUBPCM.PDX alongside every MDX (referenced, but no PCM notes).\n'
        'Nominal duration: 6.291456 seconds. A and P have identical finite durations.\n'
        'These are compiler listening controls, not vgm2mml conversion results.\n'
        'Native playback, display and stopping are unverified until listening.\n'
        'Optional WAVs are diagnostics only: mdx2pcm drops half of each mixing block.\n'
        'Nonzero WAV samples do not verify native playback, timing or audio quality.\n',
        encoding='utf-8')
    print(f'Clock controls: {outdir}; {len(cases)} static requested-performance comparisons passed')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, default=ROOT / 'outputs/listen/clock_controls')
    parser.add_argument('--generator', type=Path, default=ROOT / 'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator')
    parser.add_argument('--renderer', type=Path)
    args = parser.parse_args()
    generate(args.outdir.resolve(), args.generator.resolve(), args.renderer.resolve() if args.renderer else None)
