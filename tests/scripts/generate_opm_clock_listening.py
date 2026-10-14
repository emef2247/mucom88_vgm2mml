"""Authored FM clock comparisons; fixed-clock references are not converter fixes."""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from mdx_compiler import compile_mxc
from mdx_reference_expectations import read_expectations

FIXTURES = ROOT / 'tests/fixtures/public/opm/clock_listening'
CLOCKS = ((256, 'CLK0256'), (2048, 'CLK2048'), (8192, 'CLK8192'), (16384, 'CLK16384'))
# These original notes are public MIT test material, not copied music.
MELODY = ('c', 'd', 'e', 'g', 'a', 'g', 'e', 'c')
VOICES = '''@0 = {
31,0,0,15,0,127,0,1,0,0,0,
31,0,0,15,0,127,0,1,0,0,0,
31,0,0,15,0,127,0,1,0,0,0,
31,0,0,15,0,24,0,1,0,0,0,
0,7,15
}
'''


def timed_note(note, ticks):
    lengths = []
    while ticks:
        duration = min(ticks, 256)
        lengths.append(f'{note}%{duration}')
        ticks -= duration
    return '&'.join(lengths)


def rest(ticks):
    lengths = []
    while ticks:
        duration = min(ticks, 128)
        lengths.append(f'r%{duration}')
        ticks -= duration
    return ' '.join(lengths)


def score(clock_us, stem):
    scale = 16384 // clock_us
    tempo = 256 - clock_us // 256
    lines = [f'#title "Public FM clock {clock_us} us"',
             '; Original MIT public test: identical physical performance at each clock.', VOICES,
             f'A @0 @t{tempo} q8 o4']
    for index, note in enumerate(MELODY):
        lines.append(f'A v{12 if index % 2 == 0 else 10} p{(index % 3) + 1} '
                     f'{timed_note(note, 48 * scale)} y8,0 {rest(8 * scale)}')
    lines.append(f'A y8,0 {rest(64 * scale)}')
    return '\n'.join(lines) + '\n'


def checked(command):
    subprocess.run([str(value) for value in command], cwd=ROOT, check=True,
                   capture_output=True, timeout=120)


def reference_timeline(result, clock_us):
    tick = Fraction(clock_us, 1000000)
    notes = [row for track in result['tracks'] if track['track'] == 'A'
             for row in track['events'] if row['kind'] == 'note']
    attacks = [row for row in notes if row['retrigger_intent']]
    releases = [row for row in notes if row['release_tick'] is not None]
    assert len(attacks) == len(releases) == len(MELODY), (attacks, releases)
    return [(row['midi_note'], str(row['start_tick'] * tick),
             str(release['release_tick'] * tick), row['pan'], row['volume_encoded'])
            for row, release in zip(attacks, releases)]


def generate(reference, generated, generator):
    reference.mkdir(parents=True, exist_ok=True)
    generated.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    rows, baseline = [], None
    for clock_us, stem in CLOCKS:
        source = reference / f'{stem}.MML'
        source.write_text(score(clock_us, stem), encoding='utf-8')
        mdx, vgm = reference / f'{stem}.MDX', reference / f'{stem}.vgm'
        compile_mxc(source, mdx, timeout=60, generator=generator)
        checked([generator, '--from-mdx', mdx, vgm])
        expected = read_expectations(mdx)
        timeline = reference_timeline(expected, clock_us)
        if baseline is None:
            baseline = timeline
        assert timeline == baseline, (stem, timeline, baseline)
        assert len(timeline) == 8, timeline
        assert expected['complete'], expected['tracks']
        assert all(track['termination']['kind'] == 'finite_end' for track in expected['tracks'])
        rows.append(dict(stem=stem, clock_us=clock_us,
                         duration_seconds=float(Fraction(512 * 16384, 1000000)),
                         mdx_sha256=hashlib.sha256(mdx.read_bytes()).hexdigest(),
                         vgm_sha256=hashlib.sha256(vgm.read_bytes()).hexdigest(),
                         attacks=len(timeline), releases=len(timeline), static_performance_comparison='pass',
                         listening='unverified'))
    authored_vgm = FIXTURES / 'CLOCK.vgm'
    authored_vgm.write_bytes((reference / 'CLK16384.vgm').read_bytes())
    (FIXTURES / 'CLOCK.reference.mml').write_text(score(16384, 'CLK16384'), encoding='utf-8')
    (FIXTURES / 'CLOCK.expected.json').write_text(json.dumps(dict(
        provenance='Original MIT authored FM test; native MXC plus soundlog FM replay',
        source_clock_us=16384, physical_reference_clock_us=16384, physical_end_units=512,
        timeline=baseline, variants=rows), indent=2) + '\n', encoding='utf-8')
    (FIXTURES / 'README.md').write_text(
        '# Authored FM clock listening\n\nOriginal MIT public test: eight notes, volume/pan changes, '
        'explicit register Key-Off and final silence. Duration 8.388608 seconds. '
        'CLOCK.vgm is replayed from the native MXC 16384 us reference. '
        'CLOCK.reference.mml is that authored 16384 us source; converter MML may choose a different clock for the same physical performance. '
        'Musical register updates are sparse; wait-command sizes do not measure musical-event density. '
        'The clock variants are controlled listening references, not converter output.\n',
        encoding='utf-8')
    checked([sys.executable, ROOT / 'vgm2mml.py', authored_vgm, '--target', 'mdx', '--outdir', generated, '--dump-passes'])
    with (generated / 'CLOCK.opm.segments.csv').open(newline='', encoding='utf-8') as stream:
        segments = list(csv.DictReader(stream))
    attacks = [int(row['vgmticks']) for row in segments
               if row['ch'] == '0' and int(row['rising_mask'])]
    releases = [int(row['vgmticks']) for row in segments
                if row['ch'] == '0' and int(row['falling_mask'])]
    assert len(attacks) == len(releases) == 8, (attacks, releases)
    for observed, intended in zip(attacks, baseline):
        assert abs(Fraction(observed, 44100) - Fraction(intended[1])) <= Fraction(1, 44100)
    for observed, intended in zip(releases, baseline):
        assert abs(Fraction(observed, 44100) - Fraction(intended[2])) <= Fraction(1, 44100)
    mml = generated / 'CLOCK.mdx.mml'
    compile_mxc(mml, generated / 'CLOCK.MDX', timeout=60, generator=generator)
    selected_tempos = re.findall(r'@t(\d+)', mml.read_text(encoding='utf-8'))
    checked([generator, '--inspect-commands', reference / 'CLK0256.MDX', reference / 'CLK0256.commands.csv'])
    with (reference / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (generated / 'clock_comparison.txt').write_text(
        'Controlled reference clocks: 256, 2048, 8192, 16384 us.\n'
        f'Canonical generated MML @t values: {", ".join(selected_tempos) or "none"}.\n'
        'Source timing and IR were not altered by this comparison generator.\n'
        'Source Segment: 8 observed Key-On and 8 Key-Off transitions; authored boundary error <= 1 VGM sample.\n'
        'Reference clocks have matching intended attacks/releases, pitch, volume and pan.\n'
        'Native playback/GUI results remain unverified until listening.\n', encoding='utf-8')
    print(f'References: {reference}\nCanonical converter output: {generated}\n@t: {selected_tempos}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-outdir', type=Path, default=ROOT / 'outputs/listen/clock_listening_reference')
    parser.add_argument('--generated-outdir', type=Path, default=ROOT / 'outputs/listen/clock_listening_generated')
    parser.add_argument('--generator', type=Path, default=ROOT / 'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator')
    args = parser.parse_args()
    generate(args.reference_outdir.resolve(), args.generated_outdir.resolve(), args.generator.resolve())
