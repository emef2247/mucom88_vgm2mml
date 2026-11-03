"""Batch export musical MUCOM88 MUC and compiled MUB with inspectable passes."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py'))
from mucom_musical_products import _Recovery, _restore_files
from conversion_config import normalization_samples
FIELDS = ('input', 'status', 'detail', 'muc', 'mub', 'pcm_bank', 'voice_bank', 'diagnostics',
          'conversion_log', 'compilation_log', 'report', 'report_status', 'report_error',
          'reference_vgm', 'blueprint', 'blueprint_midi', 'blueprint_voice',
          'source_status', 'source_products_status', 'source_products_detail')


def compile_muc(muc, mub, **kwargs):
    # The native adapter is separate from conversion and target rendering.
    from mucom_compiler import compile_muc as native_compile
    return native_compile(muc, mub, **kwargs)


def compile_mucomsx(muc, mub, *, runtime_options, timeout, log_path):
    from mucomsx_runtime import run_job, validate_artifacts
    options = dict(runtime_options, timeout=timeout)
    job = log_path.parent / 'mucomsx'
    report = run_job(muc, job, compile_only=True, **options)
    log_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if report['status'] != 'passed':
        raise RuntimeError('MUCOMSX ' + report.get('failed_stage', 'compilation') + ': '
                           + report.get('detail', 'compilation failed'))
    generated = job / 'artifacts' / 'CHECK.MUB'
    validate_artifacts({'CHECK.MUB': generated.read_bytes()}, compile_only=True)
    shutil.copyfile(generated, mub)


def _text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')


def _bounded(path, output):
    if not path.resolve().is_relative_to(output):
        raise ValueError(f'Output path leaves the selected output directory: {path}')
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_results(output, rows):
    with _bounded(output / 'results.csv', output).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows({field: row.get(field, '') for field in FIELDS} for row in rows)


def write_export_report(path, row):
    path.write_text('\n'.join(f'{field}: {row.get(field, "")}' for field in FIELDS
                              if field not in ('report', 'report_status', 'report_error')) + '\n',
                    encoding='utf-8')


def _dependencies(muc):
    text = muc.read_bytes().decode('cp932', errors='replace')
    names = []
    for match in re.finditer(r'^\s*#(pcm|voice)\s+(.+?)\s*$', text, re.MULTILINE | re.IGNORECASE):
        name = match.group(2).strip().strip('"')
        if not name or '/' in name or '\\' in name or ':' in name or name in ('.', '..'):
            raise ValueError(f'Dependency must be a local owned filename: {name}')
        bank = muc.parent / name
        if not bank.is_file() or not bank.stat().st_size:
            raise ValueError(f'Converter produced no referenced dependency: {name}')
        if (match.group(1).lower(), bank) not in names:
            names.append((match.group(1).lower(), bank))
    return names


def _validate_export_options(*, target='mucom', fm_state_ms=24.0, opll_rhythm='native',
                             psg_enhance_cents=None, scc_output='native', opll_output='native', runtime_profile=None, muc_only=False,
                             mucom88=None, pcm_only=False, pcm_policy='strict',
                             pcm_profile='standard', opm_channel_policy='auto',
                             normalization_ms=8.0, score_tempo=225, **unused):
    if target not in ('mucom','mucomsx'):
        raise ValueError('Batch target must be mucom or mucomsx')
    if scc_output not in ('native','opna-fm'):
        raise ValueError('SCC output must be native or opna-fm')
    if target!='mucomsx' and scc_output!='native':
        raise ValueError('SCC routing requires --target mucomsx')
    if opll_output not in ('native','opna-fm','layered'):
        raise ValueError('OPLL output must be native, opna-fm or layered')
    if target!='mucomsx' and opll_output!='native':
        raise ValueError('OPLL routing requires --target mucomsx')
    if opll_rhythm not in ('native','layered'):
        raise ValueError('OPLL rhythm routing must be native or layered')
    if target!='mucomsx' and (opll_rhythm!='native' or psg_enhance_cents is not None):
        raise ValueError('OPLL rhythm routing and PSG enhancement require --target mucomsx')
    if psg_enhance_cents is not None:
        import math
        if not math.isfinite(psg_enhance_cents) or not -100 <= psg_enhance_cents <= 100:
            raise ValueError('PSG enhancement detune must be in -100..100 cents')
    runtime_options = None
    if runtime_profile is not None and (target!='mucomsx' or muc_only):
        raise ValueError('--runtime-profile requires --target mucomsx without --muc-only')
    if target=='mucomsx':
        if mucom88 is not None or pcm_only or pcm_policy!='strict' or pcm_profile!='standard' or opm_channel_policy!='auto':
            raise ValueError('MUCOMSX export does not support MUCOM88 compiler, PCM or OPM policy options')
        if not muc_only:
            from mucomsx_runtime import load_profile
            profile = Path(runtime_profile) if runtime_profile is not None else ROOT / '.tools/mucomsx-runtime.json'
            if not profile.is_file():
                raise ValueError('MUCOMSX MUB compilation requires a local --runtime-profile '
                                 '(default: .tools/mucomsx-runtime.json); use --muc-only for MUC output')
            runtime_options = load_profile(profile)
            if not runtime_options.get('boot_disk') or not Path(runtime_options['boot_disk']).is_file():
                raise ValueError('MUCOMSX runtime profile requires an existing local boot_disk')
    from pcm_profiles import get_profile
    get_profile(pcm_profile)
    normalization_samples(normalization_ms)
    from musical_hal.score_normalization import score_tempo as validate_score_tempo
    validate_score_tempo(score_tempo)
    from fm_state_runs import fm_short_state_samples
    fm_short_state_samples(fm_state_ms)
    return runtime_options


def _run_batch(source, output, *, mucom88=None, muc_only=False, timeout=1200, song_loop=True,
              normalize_lengths=True, normalization_ms=8.0, fm_state_ms=24.0, pcm_only=False, pcm_policy='strict', force=False,
              opm_channel_policy='auto', pcm_profile='standard', target='mucom',
              opll_rhythm='native', psg_enhance_cents=None, scc_output='native', opll_output='native', runtime_profile=None,
              score_normalize=False, score_tempo=225, score_guided_loops=False,
              legacy_loops=False, legacy_macros=False, software_volume_curves=True,
              dump_passes=True, _quiet=False):
    runtime_options = _validate_export_options(**locals())
    source, output = Path(source).resolve(), Path(output).resolve()
    if timeout <= 0:
        raise ValueError('Timeout must be positive')
    if not source.exists():
        raise ValueError(f'Input does not exist: {source}')
    if source.is_dir():
        if source == output or source in output.parents or output in source.parents:
            raise ValueError('Input and output directories must be separate trees')
        files = sorted(p for p in source.rglob('*')
                       if p.is_file() and p.suffix.lower() in ('.vgm', '.vgz'))
    else:
        if source.suffix.lower() not in ('.vgm', '.vgz') or source.is_relative_to(output):
            raise ValueError('Input must be a separate VGM/VGZ file or directory')
        files = [source]
    if not files:
        raise ValueError('No VGM/VGZ files found')
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = _bounded(output / 'export_manifest.json', output)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if not isinstance(manifest, dict):
        raise ValueError('Invalid export ownership manifest')
    counts = {}
    for path in files:
        counts[path.stem.casefold()] = counts.get(path.stem.casefold(), 0) + 1
    rows = []
    for path in files:
        relative = path.relative_to(source) if source.is_dir() else Path(path.name)
        stem = path.stem
        if counts[stem.casefold()] > 1:
            stem += '_' + hashlib.sha256(relative.as_posix().encode('utf-8')).hexdigest()[:8]
        folder = _bounded(output / 'tracks' / stem, output)
        diagnostics = _bounded(output / '_diagnostics' / stem, output)
        row = dict.fromkeys(FIELDS, '')
        row['input'] = relative.as_posix()
        stage = 'conversion'
        run_folder = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            diagnostics.mkdir(parents=True, exist_ok=True)
            owned = manifest.get(stem, {})
            if owned and owned.get('source') != str(path):
                raise ValueError(f'Output folder belongs to another input: {folder}')
            for name, digest in owned.get('files', {}).items():
                artifact = _bounded(folder / name, folder.resolve())
                if artifact.exists() and (not artifact.is_file() or _digest(artifact) != digest):
                    raise ValueError(f'Refusing to replace modified artifact: {artifact}')
            for name in (path.stem + '.muc', path.stem + '.mub'):
                if (folder / name).exists() and name not in owned.get('files', {}):
                    raise ValueError(f'Refusing to replace unowned artifact: {folder / name}')
            for name in owned.get('files', {}):
                _bounded(folder / name, folder.resolve()).unlink(missing_ok=True)
            manifest[stem] = dict(source=str(path), files={})
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            run_folder = Path(tempfile.mkdtemp(prefix='run_', dir=diagnostics))
            row['diagnostics'] = run_folder.relative_to(output).as_posix()
            conversion_log = run_folder / 'conversion.log'
            row['conversion_log'] = conversion_log.relative_to(output).as_posix()
            command = [sys.executable, str(ROOT / 'vgm2mml.py'), str(path),
                       '--outdir', str(run_folder), '--target', target, '--dump-passes',
                       '--normalize-lengths' if normalize_lengths else '--no-normalize-lengths',
                       '--normalization-ms', str(normalization_ms),
                       '--fm-state-ms', str(fm_state_ms), '--score-tempo', str(score_tempo)]
            for enabled, flag in ((score_normalize, '--score-normalize'),
                                  (score_guided_loops, '--score-guided-loops'),
                                  (legacy_loops, '--legacy-loops'),
                                  (legacy_macros, '--legacy-macros'),
                                  (not software_volume_curves, '--no-software-volume-curves')):
                if enabled:
                    command.append(flag)
            if target=='mucom':
                command.extend(['--pcm-policy',pcm_policy,'--pcm-profile',pcm_profile])
                command.extend(('--opm-channel-policy',opm_channel_policy))
            if target=='mucomsx':
                command.extend(('--scc-output',scc_output))
                if opll_output!='native':command.extend(('--opll-output',opll_output))
                if opll_rhythm!='native':command.extend(('--opll-rhythm',opll_rhythm))
                if psg_enhance_cents is not None:
                    command.extend(('--psg-enhance-cents',str(psg_enhance_cents)))
            if pcm_only: command.append('--pcm-only')
            if force: command.append('--force')
            if not song_loop:
                command.append('--no-song-loop')
            try:
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                        encoding='utf-8', errors='replace', timeout=timeout)
            except subprocess.TimeoutExpired as error:
                conversion_log.write_text(_text(error.stdout) + _text(error.stderr), encoding='utf-8')
                raise
            conversion_log.write_text(result.stdout + result.stderr, encoding='utf-8')
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr or f'Converter exited with {result.returncode}')
            generated_muc = run_folder / (path.stem + '.muc')
            if not generated_muc.is_file() or not generated_muc.stat().st_size:
                raise RuntimeError('Converter produced no nonempty musical MUC')
            dependencies = _dependencies(generated_muc)
            publications = list(dict.fromkeys([generated_muc, *(p for kind, p in dependencies)]))
            for artifact in publications:
                destination = _bounded(folder / artifact.name, output)
                if destination.exists():
                    raise ValueError(f'Refusing to replace unowned artifact: {destination}')
            for artifact in publications:
                shutil.copyfile(artifact, folder / artifact.name)
                manifest[stem]['files'][artifact.name] = _digest(folder / artifact.name)
            muc, mub = folder / generated_muc.name, folder / (path.stem + '.mub')
            row['muc'] = muc.relative_to(output).as_posix()
            for kind, field in (('pcm', 'pcm_bank'), ('voice', 'voice_bank')):
                row[field] = ';'.join((folder / p.name).relative_to(output).as_posix()
                                      for tag, p in dependencies if tag == kind)
            if muc_only:
                row['status'] = 'muc_only'
                row['detail'] = 'MUC generated; MUB compilation was not requested'
            else:
                stage = 'compilation'
                compilation_log = run_folder / 'compilation.log'
                row['compilation_log'] = compilation_log.relative_to(output).as_posix()
                try:
                    if target=='mucomsx':
                        compile_mucomsx(muc, mub, runtime_options=runtime_options,
                                        timeout=timeout, log_path=compilation_log)
                    else:
                        compile_muc(muc, mub, mucom88=mucom88, timeout=timeout, log_path=compilation_log)
                    if not mub.is_file() or not mub.stat().st_size:
                        raise RuntimeError('Compiler produced no nonempty MUB')
                except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                    mub.unlink(missing_ok=True)
                    raise
                row['mub'] = mub.relative_to(output).as_posix()
                manifest[stem]['files'][mub.name] = _digest(mub)
                row['status'] = 'success'
        except subprocess.TimeoutExpired:
            row['status'] = stage + '_failed'
            row['detail'] = f'{stage} timed out after {timeout} seconds'
            if stage=='conversion' and run_folder is not None:
                try:
                    snapshot=json.loads((run_folder/(path.stem+('.mucomsx.json' if target=='mucomsx' else '.mucom.json'))).read_text(encoding='utf-8'))
                    if snapshot.get('stage'):
                        row['detail']+='; MUC stage: '+str(snapshot['stage'])
                except (OSError,ValueError,TypeError):
                    pass
        except (OSError, RuntimeError, ValueError, ImportError) as error:
            row['status'] = stage + '_failed'
            row['detail'] = str(error).strip()
        if run_folder is not None:
            _source_product_rows(run_folder, path.stem, output, row)
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        if run_folder is not None:
            try:
                report = run_folder / 'report.txt'
                write_export_report(report, row)
                if target=='mucom' and (force or opm_channel_policy=='omit'):
                    from mucom_force import console_lines
                    lines=console_lines(run_folder,path.stem)
                    if lines:
                        with report.open('a',encoding='utf-8') as stream:
                            stream.write('\n'+'\n'.join(lines)+'\n')
                row['report'] = report.relative_to(output).as_posix()
                row['report_status'] = 'success'
            except (OSError, ValueError) as error:
                row['report_status'] = 'failed'
                row['report_error'] = str(error)
        rows.append(row)
        _save_results(output, rows)
        message=f"{relative}: {row['status']}"
        if row['detail'] and row['status'].endswith('_failed'):
            message+=': '+row['detail'].splitlines()[-1]
        if row['status'].endswith('_failed') and row['report']:
            message+='; report='+str(output/row['report'])
        if not _quiet:
            print(message,flush=True)
        if not _quiet and target=='mucom' and (force or opm_channel_policy=='omit') and run_folder is not None:
            try:
                from mucom_force import console_lines
                for line in console_lines(run_folder,path.stem):
                    print(line,flush=True)
            except (OSError,ValueError,TypeError) as error:
                print('Force summary unavailable: '+str(error),flush=True)
    return rows



_PATH_FIELDS = ('muc', 'mub', 'pcm_bank', 'voice_bank', 'diagnostics',
                'conversion_log', 'compilation_log', 'report',
                'reference_vgm', 'blueprint', 'blueprint_midi', 'blueprint_voice')
_PRODUCT_FIELDS = ('muc', 'mub', 'pcm_bank', 'voice_bank')
_SOURCE_FIELDS = ('reference_vgm', 'blueprint', 'blueprint_midi', 'blueprint_voice')
_GENERATED_DIRS = {'tracks', 'blueprint', '_diagnostics', '_source_inputs'}


def _source_product_rows(run_folder, stem, output, row):
    """Read independent source outputs even when target conversion failed."""
    reports = (run_folder / 'diagnostics' / (stem + '.musical_layer.json'),
               run_folder / (stem + '.musical_layer.json'))
    report_path = next((p for p in reports if p.is_file()), None)
    if report_path is None:
        row['source_products_status'] = 'not_available'
        return
    try:
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if not isinstance(report, dict):
            raise ValueError('Invalid source product report')
        row['source_status'] = str(report.get('status', 'unknown'))
        products = report.get('products', [])
        if not isinstance(products, list):
            raise ValueError('Invalid source product list')
        paths = {field: [] for field in _SOURCE_FIELDS}
        for product in products:
            kind = product['kind']
            if kind == 'native_reference':
                kind = 'reference_vgm'
            if kind not in paths:
                raise ValueError('Unknown source product kind: ' + str(kind))
            relative = Path(product['relative'])
            parent = 'tracks' if kind == 'reference_vgm' else 'blueprint'
            suffix = dict(reference_vgm='.vgm', blueprint='.muc',
                          blueprint_midi='.midi', blueprint_voice='.dat')[kind]
            pattern = re.escape(stem) + (r'(?:\.part[0-9]+)?' if parent == 'blueprint' else '') + re.escape(suffix)
            if (relative.parent != Path(parent) or not re.fullmatch(pattern, relative.name)
                    or relative.is_absolute()):
                raise ValueError('Invalid source product destination: ' + str(relative))
            source = Path(product['path'])
            if source.name != relative.name:
                raise ValueError('Source product basename does not match its destination')
            if not source.is_absolute():
                raise ValueError('Source product path must be absolute')
            _bounded(source, run_folder.resolve())
            if not source.is_file() or not source.stat().st_size:
                raise ValueError('Missing source product: ' + str(source))
            paths[kind].append(source.relative_to(output).as_posix())
        for field, values in paths.items():
            row[field] = ';'.join(values)
        row['source_products_status'] = 'ready' if any(paths.values()) else 'not_available'
    except (OSError, ValueError, KeyError, TypeError) as error:
        row['source_products_status'] = 'failed'
        row['source_products_detail'] = str(error)
        for field in _SOURCE_FIELDS:
            row[field] = ''


def _restore_source_products(installed, backups):
    if isinstance(backups, _Recovery):
        _restore_files(backups)
    else:
        _restore_files({**{path: None for path in installed}, **backups})


def _publish_source_products(raw, row, core_output, package, output, entries, key):
    """Publish independently owned products without changing target bundle status."""
    candidates = {}
    for field in _SOURCE_FIELDS:
        for value in filter(None, raw.get(field, '').split(';')):
            source = _bounded(core_output / value, core_output.resolve())
            parent = 'tracks' if field == 'reference_vgm' else 'blueprint'
            destination = _bounded(package / parent / source.name, output)
            folded = destination.relative_to(package).as_posix().casefold()
            if folded in candidates:
                raise ValueError('Duplicate source product destination: ' + str(destination))
            candidates[folded] = (source, destination)
    if not candidates:
        return [], {}
    active_groups = {destination.relative_to(package).parts[0] for _, destination in candidates.values()}
    owned, claimed = {}, set()
    for owner, entry in entries.items():
        for ownership in ('published', 'source_products'):
            publications = entry.get(ownership, {})
            if not isinstance(publications, dict):
                raise ValueError('Invalid source product ownership')
            for name, digest in publications.items():
                path = _bounded(package / name, package)
                relative = path.relative_to(package)
                folded = relative.as_posix().casefold()
                if (relative.parent not in (Path('tracks'), Path('blueprint'))
                        or folded in claimed):
                    raise ValueError('Invalid or overlapping source ownership: ' + name)
                claimed.add(folded)
                if owner == key and ownership == 'source_products':
                    if relative.parts[0] not in active_groups:
                        continue
                    if path.exists() and (not path.is_file() or _digest(path) != digest):
                        raise ValueError('Refusing to replace modified source product: ' + str(path))
                    owned[folded] = path
                elif folded in candidates:
                    raise ValueError('Source product collides with another publication: ' + name)
    for folded, (_, destination) in candidates.items():
        if destination.parent.exists():
            existing = {p.name.casefold(): p for p in destination.parent.iterdir()}
            current = existing.get(destination.name.casefold())
            if current is not None and (folded not in owned or current != owned[folded]):
                raise ValueError('Refusing to replace unowned source product: ' + str(current))
    backups = _Recovery(package, [*owned.values(), *(destination for _, destination in candidates.values())])
    installed = []
    old_ownership = entries[key].get('source_products', {})
    try:
        for source, destination in candidates.values():
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, filename = tempfile.mkstemp(prefix='.' + destination.name + '.',
                                                   suffix='.tmp', dir=destination.parent)
            os.close(descriptor)
            temporary = Path(filename)
            try:
                shutil.copyfile(source, temporary)
                temporary.replace(destination)
                installed.append(destination)
            finally:
                temporary.unlink(missing_ok=True)
        for folded, path in owned.items():
            if folded not in candidates:
                path.unlink(missing_ok=True)
        entries[key]['source_products'] = {
            **{name: digest for name, digest in old_ownership.items() if Path(name).parts[0] not in active_groups},
            **{p.relative_to(package).as_posix(): _digest(p) for _, p in candidates.values()}}
        for field in _SOURCE_FIELDS:
            parent = 'tracks' if field == 'reference_vgm' else 'blueprint'
            row[field] = ';'.join((package / parent / Path(v).name).relative_to(output).as_posix()
                                  for v in filter(None, raw.get(field, '').split(';')))
        row['source_products_status'] = 'published'
        return installed, backups
    except (OSError, ValueError):
        try:
            _restore_source_products(installed, backups)
        finally:
            entries[key]['source_products'] = old_ownership
        raise


def _atomic_json(path, data):
    descriptor, filename = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest(package):
    path = _bounded(package / 'listening_manifest.json', package)
    if not path.exists():
        return dict(schema_version=1, entries={})
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('entries'), dict):
        raise ValueError(f'Invalid listening ownership manifest: {path}')
    return data


def _owned_files(package, entries, key):
    owned = {}
    claimed = set()
    for owner, entry in entries.items():
        publications = entry.get('published', {})
        if not isinstance(publications, dict):
            raise ValueError('Invalid publication ownership')
        for name, digest in publications.items():
            path = _bounded(package / name, package)
            if path.parent != package / 'tracks' or path.name.casefold() in claimed:
                raise ValueError(f'Invalid or overlapping publication ownership: {name}')
            claimed.add(path.name.casefold())
            if path.exists() and (not path.is_file() or _digest(path) != digest):
                raise ValueError(f'Refusing to replace modified artifact: {path}')
            if owner == key:
                owned[path.name.casefold()] = (path, digest)
    return owned


def _check_destinations(package, candidates, owned):
    folder = _bounded(package / 'tracks', package)
    existing = {p.name.casefold(): p for p in folder.iterdir()} if folder.exists() else {}
    names = set()
    for name in candidates:
        folded = name.casefold()
        if folded in names:
            raise ValueError(f'Case-insensitive artifact collision: {name}')
        names.add(folded)
        if folded in existing:
            current = existing[folded]
            if folded not in owned or current != owned[folded][0] or _digest(current) != owned[folded][1]:
                raise ValueError(f'Refusing to replace unowned artifact or bank collision: {current}')
        _bounded(folder / name, package)


def _package_rows(rows, package, output):
    prefix = package.relative_to(output)
    converted = []
    for row in rows:
        item = dict(row)
        item['input'] = Path(item['input']).name
        for field in _PATH_FIELDS:
            if item.get(field):
                item[field] = ';'.join(Path(value).relative_to(prefix).as_posix()
                                       for value in item[field].split(';'))
        converted.append(item)
    return converted


def run_batch(source, output=None, **options):
    """Export package-local flat listening products without changing conversion."""
    _validate_export_options(**options)
    if options.get('timeout', 1200) <= 0:
        raise ValueError('Timeout must be positive')
    source = Path(source).resolve()
    if not source.exists():
        raise ValueError(f'Input does not exist: {source}')
    source_root = source if source.is_dir() else source.parent
    adjacent = output is None
    output = source_root if adjacent else Path(output).resolve()
    if not adjacent and ((source.is_dir() and (source_root == output or source_root in output.parents or output in source_root.parents))
                         or (source.is_file() and source.is_relative_to(output))):
        raise ValueError('Input and output directories must be separate trees')
    if source.is_dir():
        files = []
        for directory, children, names in os.walk(source):
            children[:] = sorted(n for n in children if n.casefold() not in _GENERATED_DIRS)
            files.extend(Path(directory) / n for n in sorted(names)
                         if Path(n).suffix.lower() in ('.vgm', '.vgz'))
    elif source.suffix.lower() in ('.vgm', '.vgz'):
        files = [source]
    else:
        raise ValueError('Input must be a VGM/VGZ file or directory')
    if not files:
        raise ValueError('No VGM/VGZ files found')
    routes, packages, names, parent_names = [], {}, set(), {}
    for path in files:
        relative = path.relative_to(source_root)
        package = _bounded(output / relative.parent, output)
        parent_key = relative.parent.as_posix().casefold()
        if parent_key in parent_names and parent_names[parent_key] != relative.parent:
            raise ValueError(f'Case-insensitive package collision: {relative.parent}')
        parent_names[parent_key] = relative.parent
        identity = (relative.parent.as_posix() + '/' + path.stem).casefold()
        if identity in names:
            raise ValueError(f'Case-insensitive publication collision: {relative}')
        names.add(identity)
        if package not in packages:
            data = _manifest(package)
            packages[package] = data
        data = packages[package]
        key = path.name
        old = data['entries'].get(key, {})
        if old and old.get('source_path') != str(path):
            raise ValueError(f'Output belongs to another input: {package / key}')
        # Validate all known artifacts, including dependencies, before conversion.
        owned = _owned_files(package, data['entries'], key)
        _check_destinations(package, [path.stem + '.muc', path.stem + '.mub'], owned)
        staged = _bounded(package / '_source_inputs' / path.name, output)
        source_hash = _digest(path)
        if staged.exists():
            expected = old.get('staged_sha256', source_hash)
            if not staged.is_file() or _digest(staged) != expected:
                raise ValueError(f'Refusing to replace modified staged input: {staged}')
        routes.append((path, relative, package, key, staged, source_hash))
    # Include metadata and implicit package parents in the all-input preflight.
    planned_files, planned_directories = set(), set()
    def register(path, directory=False):
        relative_path = path.relative_to(output)
        key_path = relative_path.as_posix().casefold()
        (planned_directories if directory else planned_files).add(key_path)
        for parent in relative_path.parents:
            planned_directories.add(parent.as_posix().casefold())
    for path, _, package, _, staged, _ in routes:
        for name in ('results.csv', 'listening_manifest.json'):
            register(package / name)
        register(staged)
        for suffix in ('.muc', '.mub'):
            register(package / 'tracks' / (path.stem + suffix))
        register(package / '_diagnostics' / path.stem, directory=True)
    register(output / '_diagnostics' / 'results.csv')
    register(output / '_diagnostics' / 'results.json')
    conflicts = planned_files & planned_directories
    if conflicts:
        raise ValueError('Output file/directory collision: ' + ', '.join(sorted(conflicts)))
    rows = []
    reserved = {(package, (path.stem + suffix).casefold()): key
                for path, _, package, key, _, _ in routes for suffix in ('.muc', '.mub')}
    for path, relative, package, key, staged, source_hash in routes:
        package.mkdir(parents=True, exist_ok=True)
        tracks = _bounded(package / 'tracks', output)
        diagnostics = _bounded(package / '_diagnostics' / path.stem, output)
        tracks.mkdir(parents=True, exist_ok=True)
        diagnostics.mkdir(parents=True, exist_ok=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, staged)
        if _digest(staged) != source_hash:
            raise ValueError(f'Source changed during staging: {path}')
        data = packages[package]
        old = data['entries'].get(key, {})
        entry = dict(old, input=key, source_path=str(path),
                     source_sha256=old.get('source_sha256', source_hash) if old.get('published') else source_hash,
                     staged_input=staged.relative_to(package).as_posix(), staged_sha256=source_hash,
                     stem=path.stem, published=dict(old.get('published', {})))
        data['entries'][key] = entry
        _atomic_json(package / 'listening_manifest.json', data)
        attempt = Path(tempfile.mkdtemp(prefix='attempt_', dir=diagnostics))
        core_output = attempt / 'work'
        raw = _run_batch(staged, core_output, **options, _quiet=True)[0]
        row = dict(raw, input=relative.as_posix())
        for field in _PATH_FIELDS:
            if row.get(field):
                row[field] = ';'.join((core_output / value).relative_to(output).as_posix()
                                      for value in row[field].split(';'))
        candidate_paths = {}
        for field in _PRODUCT_FIELDS:
            if raw[field]:
                for value in raw[field].split(';'):
                    candidate = core_output / value
                    if candidate.name.casefold() in candidate_paths and candidate_paths[candidate.name.casefold()] != candidate:
                        raise ValueError(f'Artifact basename collision: {candidate.name}')
                    candidate_paths[candidate.name.casefold()] = candidate
        before_publication = json.loads(json.dumps(entry))
        source_installed, source_backups = [], {}
        try:
            source_installed, source_backups = _publish_source_products(
                raw, row, core_output, package, output, data['entries'], key)
        except (OSError, ValueError) as error:
            row['source_products_status'] = 'publication_failed'
            row['source_products_detail'] = str(error)
            for field in _SOURCE_FIELDS:
                row[field] = ''
        after_source_publication = json.loads(json.dumps(entry))
        backups, installed, owned = {}, [], {}
        complete = row['status'] in ('success', 'muc_only')
        preserve = not complete and old.get('status') in ('success', 'muc_only')
        try:
            for name in candidate_paths:
                if reserved.get((package, name), key) != key:
                    raise ValueError(f'Dependency collides with another score or binary: {candidate_paths[name].name}')
            owned = _owned_files(package, data['entries'], key)
            _check_destinations(package, [p.name for p in candidate_paths.values()], owned)
            if candidate_paths and not preserve:
                backups = {name: p.read_bytes() for name, (p, _) in owned.items() if p.exists()}
                temporaries = []
                try:
                    for candidate in candidate_paths.values():
                        final = tracks / candidate.name
                        descriptor, filename = tempfile.mkstemp(prefix='.' + candidate.name + '.', suffix='.tmp', dir=tracks)
                        os.close(descriptor)
                        temporary = Path(filename)
                        temporaries.append(temporary)
                        shutil.copyfile(candidate, temporary)
                        temporary.replace(final)
                        installed.append(final)
                    for name, (old_path, _) in owned.items():
                        if name not in candidate_paths:
                            old_path.unlink(missing_ok=True)
                    entry['source_sha256'] = source_hash
                    entry['published_source_sha256'] = source_hash
                    entry['published'] = {('tracks/' + p.name): _digest(tracks / p.name)
                                          for p in candidate_paths.values()}
                except (OSError, ValueError):
                    for final in installed:
                        final.unlink(missing_ok=True)
                    for name, content in backups.items():
                        owned[name][0].write_bytes(content)
                    entry.clear()
                    entry.update(after_source_publication)
                    installed = []
                    raise
                finally:
                    for temporary in temporaries:
                        temporary.unlink(missing_ok=True)
                for field in _PRODUCT_FIELDS:
                    row[field] = ';'.join((tracks / Path(v).name).relative_to(output).as_posix()
                                          for v in raw[field].split(';')) if raw[field] else ''
            elif preserve:
                for field in _PRODUCT_FIELDS:
                    row[field] = ''
                row['detail'] = (row['detail'] + '; prior published bundle retained').strip('; ')
        except (OSError, ValueError) as error:
            row['status'] = 'publication_failed'
            row['detail'] = str(error)
            for field in _PRODUCT_FIELDS:
                row[field] = ''
        if row['report_status'] != 'failed':
            try:
                final_report = attempt / 'report.txt'
                write_export_report(final_report, row)
                row['report'] = final_report.relative_to(output).as_posix()
                row['report_status'] = 'success'
            except (OSError, ValueError) as error:
                row['report_status'] = 'failed'
                row['report_error'] = str(error)
        entry['latest_attempt_status'] = row['status']
        entry['diagnostics'] = diagnostics.relative_to(package).as_posix()
        if not preserve and row['status'] != 'publication_failed':
            entry['status'] = row['status']
        entry.setdefault('status', row['status'])
        entry['latest_attempt'] = _package_rows([row], package, output)[0]
        try:
            _atomic_json(package / 'listening_manifest.json', data)
            if isinstance(source_backups, _Recovery):
                source_backups.discard()
        except (OSError, ValueError) as error:
            # Publication and ownership commit form one transaction.
            recovery_error = ''
            try:
                _restore_source_products(source_installed, source_backups)
            except OSError as restore_error:
                recovery_error = '; ' + str(restore_error)
            for field in _SOURCE_FIELDS:
                row[field] = ''
            row['source_products_status'] = 'publication_failed'
            for final in installed:
                final.unlink(missing_ok=True)
            for name, content in backups.items():
                owned[name][0].write_bytes(content)
            entry.clear()
            entry.update(before_publication)
            row['status'] = 'publication_failed'
            row['detail'] = f'Ownership commit failed: {error}' + recovery_error
            for field in _PRODUCT_FIELDS:
                row[field] = ''
            entry.setdefault('status', row['status'])
            entry['latest_attempt_status'] = row['status']
            entry['latest_attempt'] = _package_rows([row], package, output)[0]
            try:
                _atomic_json(package / 'listening_manifest.json', data)
            except (OSError, ValueError):
                row['detail'] += '; manifest retry failed; inspect the retained attempt report'
            try:
                write_export_report(attempt / 'report.txt', row)
            except (OSError, ValueError):
                pass
        rows.append(row)
        # Keep previous latest rows when a later invocation selects only one song.
        local_rows = [e['latest_attempt'] for e in data['entries'].values() if e.get('latest_attempt')]
        _save_results(package, local_rows)
        (output / '_diagnostics').mkdir(parents=True, exist_ok=True)
        _save_results(output / '_diagnostics', rows)
        _atomic_json(output / '_diagnostics' / 'results.json', rows)
        message = f"{relative}: {row['status']}"
        if row['detail'] and row['status'].endswith('_failed'):
            message += ': ' + row['detail'].splitlines()[-1]
        if row['report'] and row['status'].endswith('_failed'):
            message += '; report=' + str(output / row['report'])
        print(message, flush=True)
        if options.get('target', 'mucom') == 'mucom' and (options.get('force') or options.get('opm_channel_policy') == 'omit'):
            try:
                from mucom_force import console_lines
                for line in console_lines(output / row['diagnostics'], path.stem):
                    print(line, flush=True)
            except (OSError, ValueError, TypeError) as error:
                print('Force summary unavailable: ' + str(error), flush=True)
    return rows

def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('Value must be positive')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='VGM/VGZ file or directory to scan recursively')
    parser.add_argument('--outdir', type=Path, help='Output tree (default: packages beside source files)')
    parser.add_argument('--target', choices=('mucom','mucomsx'), default='mucom',
                        help='Musical MUC/MUB target (default: mucom)')
    parser.add_argument('--runtime-profile', type=Path,
                        help='MUCOMSX local DOS/openMSX profile (default: .tools/mucomsx-runtime.json)')
    parser.add_argument('--opll-rhythm', choices=('native','layered'), default='native',
                        help='MUCOMSX only: native OPLL percussion, optionally doubled by OPNA rhythm')
    parser.add_argument('--opll-output', choices=('native','opna-fm','layered'), default='native',
                        help='MUCOMSX OPLL routing: native (default), OPNA FM measured-law rendering, or layered doubling')
    parser.add_argument('--scc-output', choices=('native','opna-fm'), default='native',
                        help='MUCOMSX: native five-channel SCC (default), or legacy OPNA FM approximation')
    parser.add_argument('--psg-enhance-cents', type=float,
                        help='MUCOMSX only: add an OPNA SSG layer detuned by -100..100 cents')
    parser.add_argument('--mucom88', type=Path, help='Native unattended MUCOM88 compiler')
    parser.add_argument('--muc-only', action='store_true', help='MUC export without MUB compilation')
    parser.add_argument('--force', action='store_true', help='Continue with explicit target omissions and capacity diagnostics')
    parser.add_argument('--opm-channel-policy', choices=('auto','error','omit','dynamic'), default='auto',
                        help='OPM8 to OPNA6: auto uses dynamic six-voice allocation with force; error/omit retain fixed policy')
    parser.add_argument('--pcm-only', action='store_true', help='Explicit external PCM extraction; other engines excluded')
    parser.add_argument('--pcm-profile', choices=('standard','low','ultra_low'), default='standard',
                        help='Derived MSM6258/SegaPCM PCM rate: standard, half-rate low or quarter-rate ultra_low')
    parser.add_argument('--pcm-policy', choices=('strict','best-effort'), default='strict')
    parser.add_argument('--no-song-loop', action='store_true', help='Emit the first source traversal without a song loop')
    parser.add_argument('--normalize-lengths', dest='normalize_lengths', action='store_true',
                               help=argparse.SUPPRESS)
    parser.add_argument('--no-normalize-lengths', dest='normalize_lengths', action='store_false',
                               help=argparse.SUPPRESS)
    parser.set_defaults(normalize_lengths=True)
    parser.add_argument('--normalization-ms', type=float, default=8.0,
                        help='Whole-note omission threshold and output timing bound in milliseconds (default: 8)')
    parser.add_argument('--timeout', type=positive, default=1200, help='Seconds per conversion/compilation stage (default: 1200)')
    parser.add_argument('--fm-state-ms', type=float, default=24.0,
                        help='FM internal-state threshold in milliseconds (default: 24)')
    parser.add_argument('--score-normalize', action='store_true')
    parser.add_argument('--score-tempo', type=positive, default=225)
    parser.add_argument('--score-guided-loops', action='store_true')
    parser.add_argument('--legacy-loops', action='store_true')
    parser.add_argument('--legacy-macros', action='store_true')
    parser.add_argument('--no-software-volume-curves', dest='software_volume_curves',
                        action='store_false', default=True)
    parser.add_argument('--dump-passes', action='store_true')
    args = parser.parse_args()
    if '--normalize-lengths' in sys.argv[1:] and '--no-normalize-lengths' in sys.argv[1:]:
        parser.error('--normalize-lengths and --no-normalize-lengths are mutually exclusive')
    if args.target=='mucomsx':
        unsupported={'--mucom88','--pcm-only','--pcm-profile','--pcm-policy','--opm-channel-policy'}
        requested=sorted({flag for option in sys.argv[1:] for flag in unsupported
                          if flag.startswith(option.split('=',1)[0])})
        if requested:
            parser.error('--target mucomsx does not support '+', '.join(requested))
    if args.target!='mucomsx' and any(
            option.split('=',1)[0] in ('--opll-rhythm','--psg-enhance-cents','--scc-output','--opll-output')
            for option in sys.argv[1:]):
        parser.error('--opll-rhythm and --psg-enhance-cents require --target mucomsx')
    try:
        rows = run_batch(args.input, args.outdir, mucom88=args.mucom88,
                         muc_only=args.muc_only, timeout=args.timeout, song_loop=not args.no_song_loop,
                         normalize_lengths=args.normalize_lengths, normalization_ms=args.normalization_ms,
                         fm_state_ms=args.fm_state_ms, score_normalize=args.score_normalize,
                         score_tempo=args.score_tempo, score_guided_loops=args.score_guided_loops,
                         legacy_loops=args.legacy_loops, legacy_macros=args.legacy_macros,
                         software_volume_curves=args.software_volume_curves,
                         dump_passes=args.dump_passes,
                         pcm_only=args.pcm_only,pcm_policy=args.pcm_policy,force=args.force,pcm_profile=args.pcm_profile,
                         opm_channel_policy=args.opm_channel_policy,target=args.target,
                         opll_rhythm=args.opll_rhythm,psg_enhance_cents=args.psg_enhance_cents,scc_output=args.scc_output,opll_output=args.opll_output,
                         runtime_profile=args.runtime_profile)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    expected = 'muc_only' if args.muc_only else 'success'
    return 0 if all(row['status'] == expected and row['report_status'] != 'failed' for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
