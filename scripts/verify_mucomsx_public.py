"""Verify public MSX sources through MUCOMSX MUC and optional native export.

Use a new output directory for every run. Native compilation/export requires an
explicit local runtime profile. Comparison reports control evidence, not audio
or phase equivalence; OPNA projections remain separately identified exclusions.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'tests/fixtures/public'
sys.path.insert(0, str(ROOT / 'py'))
from conversion_config import inspect_source
from mucomsx_conversion import convert_vgm
from mucomsx_roundtrip import compare
from mucomsx_runtime import run_job
from vgm_io import read_vgm_bytes
from vgm_timing import command_times
from dcsg_music import load_dcsg_bundle

GROUPS = ('psg', 'opll', 'psg_scc', 'psg_opll', 'psg_scc_opll')
PROFILE_KEYS = {'boot_disk', 'openmsx', 'user_data', 'rom_path', 'machine',
                'extension', 'timeout', 'tools'}
FIELDS = ('source', 'category', 'status', 'source_status', 'conversion_status',
          'native_status', 'roundtrip_status', 'detail', 'muc', 'exported_vgm',
          'roundtrip_report', 'checked_intervals', 'mismatched_intervals',
          'opna_projection_exclusions', 'diagnostics', 'wall_seconds')


@dataclass(frozen=True)
class Fixture:
    source: Path
    relative: str
    category: str


def discover_fixtures(public=PUBLIC):
    public = Path(public)
    result = []
    for group in (*GROUPS, 'mucomsx/source'):
        directory = public / group
        paths = sorted(p for p in directory.rglob('*')
                       if p.is_file() and p.suffix.lower() in ('.vgm', '.vgz'))
        if not paths:
            raise ValueError('Public fixture directory is empty or absent: ' + str(directory))
        result.extend(Fixture(p, p.relative_to(public).as_posix(),
                              'authored_mucomsx' if group == 'mucomsx/source' else group)
                      for p in paths)
    return result


def _json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str) + '\n', encoding='utf-8')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _profile(path, timeout):
    if path is None:
        return None
    path = Path(path).resolve()
    if path.is_relative_to(ROOT):
        check = subprocess.run(['git', '-c', 'safe.directory='+str(ROOT), 'check-ignore', '--quiet', str(path.relative_to(ROOT))],
                               cwd=ROOT, capture_output=True)
        if check.returncode:
            raise ValueError('Keep the local runtime profile in a git-ignored path or outside this repository')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) - PROFILE_KEYS or not data.get('boot_disk'):
        raise ValueError('Runtime profile must contain boot_disk and only supported runtime settings')
    options = dict(data)
    options['timeout'] = timeout
    return options


def _inspect_fixture(source, folder):
    raw = read_vgm_bytes(source)
    if any(event.command in (0x50, 0x30) for event in command_times(raw)):
        bundle = load_dcsg_bundle(source, folder / 'source', song_loop=False)
        return dict(used_chips=['dcsg'], unsupported_commands=[],
                    source_paths=bundle.paths, source_metadata=bundle.metadata,
                    source_header=bundle.header)
    return inspect_source(source)


def _overall(row):
    for phase in ('source', 'conversion', 'native'):
        if row[phase + '_status'] == 'failed':
            return phase + '_failed'
    status = row['roundtrip_status']
    if status in ('failed', 'mismatched', 'not_comparable'):
        return 'roundtrip_' + status
    return 'validated' if status == 'passed' else 'muc_generated_runtime_not_run'


def _fixture_job(fixture, folder, runtime, timeout):
    started = time.monotonic()
    row = dict.fromkeys(FIELDS, '')
    row.update(source=fixture.relative, category=fixture.category,
               source_status='not_run', conversion_status='not_run',
               native_status='not_run', roundtrip_status='not_run',
               diagnostics=str(folder), checked_intervals=0,
               mismatched_intervals=0, opna_projection_exclusions=0)
    phase = 'source'
    try:
        folder.mkdir(parents=True, exist_ok=False)
        usage = _inspect_fixture(fixture.source, folder)
        _json(folder / 'source_usage.json', usage)
        if usage['unsupported_commands']:
            issue = usage['unsupported_commands'][0]
            raise ValueError(f"{issue['reason']} ({issue['command']} at {issue['address']:#x})")
        row['source_status'] = 'passed'
        phase = 'conversion'
        converted = folder / 'conversion'
        try:
            projection = convert_vgm(fixture.source, converted, normalize_lengths=False,
                                     song_loop=False, force=False, dump_passes=True)
        except Exception:
            report = converted / (fixture.source.stem + '.mucomsx.json')
            if report.is_file():
                try:
                    snapshot = json.loads(report.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    snapshot = {}
                if snapshot.get('failed_stage') == 'source_analysis':
                    phase = 'source'
                    row['conversion_status'] = 'not_run'
            raise
        muc = Path(projection['muc_path'])
        if projection.get('status') != 'generated' or not muc.is_file() or not muc.stat().st_size:
            raise ValueError('Converter did not produce a nonempty generated MUC')
        row.update(conversion_status='passed', muc=str(muc))
        _json(folder / 'projection.json', projection)
        _json(folder / 'input_identity.json', dict(source_sha256=_sha(fixture.source), muc_sha256=_sha(muc)))
        if runtime is None:
            row['detail'] = 'Native compilation/export and roundtrip were not run: no runtime profile supplied'
            return row
        phase = 'native'
        options = dict(runtime, timeout=timeout)
        native = run_job(muc, folder / 'native', **options)
        _json(folder / 'native_result.json', native)
        if native.get('status') != 'passed':
            raise ValueError(native.get('detail', 'Native compilation/export failed'))
        target = Path(native.get('files', {}).get('CHECK.VGM', {}).get('path', ''))
        if not target.is_file():
            raise ValueError('Native run reported success without an exported CHECK.VGM')
        row.update(native_status='passed', exported_vgm=str(target))
        phase = 'roundtrip'
        comparison = compare(fixture.source, target, projection)
        checked = comparison['checked_intervals']
        mismatched = comparison['mismatched_intervals']
        status = 'not_comparable' if not checked else 'mismatched' if mismatched else 'passed'
        comparison['status'] = status
        comparison['validation_scope'] = 'Native-chip control evidence; OPNA projections excluded; no acoustic equivalence claim'
        report = folder / 'roundtrip.json'
        _json(report, comparison)
        row.update(roundtrip_status=status, roundtrip_report=str(report),
                   checked_intervals=checked, mismatched_intervals=mismatched,
                   opna_projection_exclusions=len(comparison.get('opna_projection_exclusions', [])))
        if status == 'not_comparable':
            row['detail'] = 'No native-chip intervals were comparable; inspect projection exclusions'
        elif status == 'mismatched':
            row['detail'] = 'Native-chip control mismatches are recorded in roundtrip.json'
    except Exception as error:
        row[phase + '_status'] = 'failed'
        row['detail'] = str(error)
    finally:
        row['status'] = _overall(row)
        row['wall_seconds'] = round(time.monotonic() - started, 3)
        try:
            _json(folder / 'result.json', row)
        except OSError as error:
            row['detail'] += '; could not persist fixture result: ' + str(error)
    return row


def _save(output, rows, total):
    ordered = sorted(rows, key=lambda row: row['source'])
    temporary = output / 'results.csv.tmp'
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(ordered)
    temporary.replace(output / 'results.csv')
    _json(output / 'summary.json', dict(total_fixtures=total, completed=len(rows),
          status_counts=dict(Counter(row['status'] for row in rows)),
          phase_counts={phase:dict(Counter(row[phase + '_status'] for row in rows))
                        for phase in ('source', 'conversion', 'native', 'roundtrip')},
          validation_scope='Control comparison only; compilation/export and projection exclusions are reported separately'))


def run_public(outdir, *, runtime_profile=None, jobs=1, timeout=180, fixtures=None):
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= 3:
        raise ValueError('Jobs must be an integer from 1 to 3')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Individual runtime timeout must be finite and positive')
    runtime = _profile(runtime_profile, timeout)
    selected = list(discover_fixtures() if fixtures is None else fixtures)
    if not selected:
        raise ValueError('No public fixtures selected')
    output = Path(outdir).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new or empty verification output directory; cached outputs are not reused')
    destinations = []
    for fixture in selected:
        relative = Path(fixture.relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Fixture identity must be a bounded relative path')
        destination = output / 'fixtures' / relative
        if fixture.source.resolve().is_relative_to(output):
            raise ValueError('Verification output must not contain source fixtures')
        destinations.append(destination)
    if len({str(path).casefold() for path in destinations}) != len(destinations):
        raise ValueError('Fixture output identities must be unique')
    output.mkdir(parents=True, exist_ok=True)
    code_paths = sorted({*ROOT.glob('py/*.py'), *ROOT.glob('scripts/*.py'), ROOT / 'vgm2mml.py'})
    _json(output / 'verification_manifest.json', dict(schema=1,
          created_utc=datetime.now(timezone.utc).isoformat(), reuse='disabled; new/empty output directory required',
          parameters=dict(jobs=jobs, timeout=timeout, runtime_requested=runtime is not None,
                          normalize_lengths=False, song_loop=False, force=False),
          fixtures=[dict(source=f.relative, category=f.category, sha256=_sha(f.source)) for f in selected],
          code_sha256={str(path.relative_to(ROOT)):_sha(path) for path in code_paths}))
    rows = []
    _save(output, rows, len(selected))
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(_fixture_job, fixture, folder, runtime, timeout):fixture
                   for fixture, folder in zip(selected, destinations)}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            _save(output, rows, len(selected))
            print(f"{row['source']}: {row['status']}" + (': ' + row['detail'] if row['detail'] else ''), flush=True)
    return sorted(rows, key=lambda row: row['source'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, required=True, help='New or empty output directory; no cached output reuse')
    parser.add_argument('--runtime-profile', type=Path, help='Ignored/local JSON settings for native MUCOMSX compilation/export')
    parser.add_argument('--jobs', type=int, default=1, choices=range(1, 4), help='Concurrent fixtures (default: 1; maximum: 3)')
    parser.add_argument('--timeout', type=float, default=180, help='Seconds per native runtime job (default: 180)')
    args = parser.parse_args(argv)
    try:
        rows = run_public(args.outdir, runtime_profile=args.runtime_profile, jobs=args.jobs, timeout=args.timeout)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    good = {'validated', 'muc_generated_runtime_not_run'}
    return 0 if all(row['status'] in good for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
