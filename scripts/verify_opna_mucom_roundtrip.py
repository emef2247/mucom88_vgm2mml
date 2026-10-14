"""Diagnostic OPNA VGM -> native Segments -> MUCOMVGM MML -> VGM check."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py'))
from opna_mucom import project, render, dump_projection
from opna_mucom_roundtrip import compare
from vgm_reader import parse_vgm


def source_files(path):
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in ('.vgm', '.vgz'))


def raw_controls(path):
    with open(path, encoding='utf-8', newline='') as stream:
        return tuple(tuple(int(row[field]) for field in ('vgmticks', 'port', 'register', 'data'))
                     for row in csv.DictReader(stream))


def tool_path(path, tool):
    if sys.platform != 'win32' and tool.suffix.lower() == '.exe':
        translated = subprocess.run(['wslpath', '-w', str(path.resolve())],
                                    capture_output=True, text=True, check=True)
        return translated.stdout.strip()
    return str(path.resolve())


def compile_muc(tool, muc, destination, *, timeout=60):
    destination.unlink(missing_ok=True)
    command = [str(tool.resolve()), tool_path(muc, tool), tool_path(destination, tool)]
    options = dict(cwd=muc.parent, capture_output=True, timeout=timeout)
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        run = subprocess.run(command, **options)
    except subprocess.TimeoutExpired as error:
        muc.with_suffix('.compile.log').write_bytes((error.stdout or b'') + (error.stderr or b'')
                                                   + b'\nCompilation timed out\n')
        raise
    muc.with_suffix('.compile.log').write_bytes(run.stdout + run.stderr)
    if run.returncode or not destination.is_file():
        raise RuntimeError(f'MUCOMVGM compilation failed, exit={run.returncode}; see {muc.with_suffix(".compile.log")}')


def analyze(source, folder, *, loop_count=1, pcm=False):
    facts, pcm_facts = {}, {}
    parse_vgm(str(source), str(folder), opna_metadata=facts, dump_opna_segments=True,
              opna_pcm_metadata=pcm_facts if pcm else None,
              loop_count=loop_count, include_vgmticks=True)
    if facts['analysis'] is None:
        raise ValueError('Source does not contain declared OPNA register evidence')
    if pcm:
        facts['pcm_analysis'] = pcm_facts['analysis']
        facts['pcm_analysis'].dump(folder, Path(source).stem)
    return facts


def calibration(tool, folder):
    folder.mkdir(parents=True, exist_ok=True)
    muc, vgm = folder / 'driver_init.muc', folder / 'driver_init.vgm'
    muc.write_text('A C192 T55125 r%1\n', encoding='utf-8')
    compile_muc(tool, muc, vgm)
    facts = analyze(vgm, folder / 'passes')
    initialization = raw_controls(facts['csv_path'])
    if (facts['clock_hz'] != 7987200 or facts['source_end_vgmticks'] != 1
            or any(tick for tick, port, reg, data in initialization)
            or facts['key_counts']['operator_keyons']):
        raise ValueError('Compiler does not support the tested sample-clock/driver profile')
    (folder / 'profile.json').write_text(json.dumps(dict(
        compiler=str(tool.resolve()), executable_sha256=hashlib.sha256(tool.read_bytes()).hexdigest(),
        clock_hz=facts['clock_hz'], sample_clock='C192 T55125; r%1 = one VGM sample',
        initialization=initialization), indent=2) + '\n', encoding='utf-8')
    return initialization


def verify(source, folder, tool, initialization, *, loop_count=1):
    folder.mkdir(parents=True, exist_ok=True)
    source_facts = analyze(source, folder / 'source', loop_count=loop_count, pcm=True)
    original = source_facts['analysis']
    if any(command['domain'] == 'dac_stream' for command in source_facts['auxiliary_commands']):
        raise ValueError('OPNA DAC streams are not expanded; their possible FM register effects cannot be verified')
    known_non_fm = {'ssg', 'rhythm', 'adpcm_b', 'irq_flag_control'}
    unknown_domains = {write.domain for write in original.unmodeled_writes} - known_non_fm
    if unknown_domains:
        raise ValueError('Unmodeled source register effects cannot be verified: ' + ', '.join(sorted(unknown_domains)))
    projected = project(original.segments, end_vgmticks=original.source_end_vgmticks,
                        clock_hz=source_facts['clock_hz'])
    muc, returned_vgm = folder / 'generated.muc', folder / 'returned.vgm'
    dump_projection(projected, csv_path=folder / 'target_writes.csv')
    muc.write_text(render(projected), encoding='utf-8')
    compile_muc(tool, muc, returned_vgm)
    returned_facts = analyze(returned_vgm, folder / 'returned')
    controls = raw_controls(returned_facts['csv_path'])
    report = compare(projected, returned_facts['analysis'], controls,
                     initialization=initialization, returned_clock_hz=returned_facts['clock_hz'])
    report.update(source_unmodeled_domains=dict(Counter(w.domain for w in original.unmodeled_writes)),
                  source_auxiliary_commands=len(source_facts['auxiliary_commands']),
                  source_issues=source_facts['issues'], target_diagnostics=projected.diagnostics,
                  loop_count=loop_count,
                  comparison_scope='Native OPNA FM controls and Segments; PCM/SSG/rhythm/audio excluded',
                  pcm=dict(compared=False, status='not_compared',
                           source_segments=len(source_facts['pcm_analysis'].segments),
                           source_ir_saved=True))
    active = report['source_attack_events']
    report['status'] = ('passed' if active else 'no_active_fm') if report['passed'] else 'mismatch'
    (folder / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def save_results(folder, rows):
    (folder / 'results.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (folder / 'results.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='OPNA VGM or a directory of VGM/VGZ sources')
    parser.add_argument('--outdir', type=Path, default=ROOT / 'outputs/opna/mucom_roundtrip')
    parser.add_argument('--mucomvgm', type=Path, default=ROOT / '.tools/mucomvgm-0.0.5/mucomvgm/mucomvgm.exe')
    parser.add_argument('--loop-count', type=int, default=1, help='Finite declared source-loop traversals (default 1)')
    parser.add_argument('--fixture-manifest', type=Path,
                        help='Verify original MUC/VGM/compiler hashes from prepare_mucom88_fixtures.py')
    args = parser.parse_args(argv)
    source, output, tool = args.input.resolve(), args.outdir.resolve(), args.mucomvgm.resolve()
    if not source.exists() or not tool.is_file():
        parser.error('Input and MUCOMVGM compiler must exist')
    if args.loop_count < 1:
        parser.error('--loop-count must be positive')
    source_tree = source if source.is_dir() else source.parent
    if output.is_relative_to(source_tree):
        parser.error('--outdir must be outside the source fixture directory')
    paths = source_files(source)
    if not paths:
        parser.error('No VGM/VGZ sources found')
    output.mkdir(parents=True, exist_ok=True)
    source_provenance = {}
    if args.fixture_manifest:
        manifest = json.loads(args.fixture_manifest.read_text(encoding='utf-8'))
        if manifest['compiler_sha256'] != hashlib.sha256(tool.read_bytes()).hexdigest():
            parser.error('Fixture manifest was produced by a different MUCOMVGM executable')
        records = {Path(row['output']).resolve(): row for row in manifest['files'] if row['status'] == 'generated'}
        for path in paths:
            row = records.get(path.resolve())
            if row is None or row['output_sha256'] != hashlib.sha256(path.read_bytes()).hexdigest():
                parser.error('Input VGM does not match fixture generation manifest')
            original_muc = Path(manifest['source']) / row['source']
            if row['input_sha256'] != hashlib.sha256(original_muc.read_bytes()).hexdigest():
                parser.error('Original MUC no longer matches fixture generation manifest')
            source_provenance[path] = dict(original_muc=str(original_muc),
                original_muc_sha256=row['input_sha256'], input_vgm_sha256=row['output_sha256'],
                compiler_input_sha256=row['compiler_input_sha256'],
                compiler_sha256=manifest['compiler_sha256'])
    try:
        initialization = calibration(tool, output / '_compiler_profile')
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    rows = []
    for path in paths:
        relative = path.relative_to(source) if source.is_dir() else Path(path.name)
        folder = output / 'tracks' / relative  # Includes suffix to keep .vgm/.vgz cases distinct.
        try:
            report = verify(path, folder, tool, initialization, loop_count=args.loop_count)
            report['source_generation'] = source_provenance.get(path)
            row = dict(input=str(relative), **report)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            row = dict(input=str(relative), status='failed', passed=False, error=str(error))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'failure.json').write_text(json.dumps(row, indent=2) + '\n', encoding='utf-8')
        rows.append(row)
        print(f'{relative}: {row["status"]}', flush=True)
        save_results(output, rows)
    print('Results:', dict(Counter(row['status'] for row in rows)), flush=True)
    return 0 if all(row['passed'] for row in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
