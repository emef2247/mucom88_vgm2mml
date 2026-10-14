"""Publish short, consistent listening filenames while retaining full diagnostics."""
import hashlib
import json
from pathlib import Path
import re
import shutil

from gd3 import read_gd3, title_from_gd3
from conversion_config import inspect_source, select_mdx_route


def safe_stem(relative):
    original = Path(relative).stem
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {
        prefix + str(number) for prefix in ('COM', 'LPT') for number in range(1, 10)}
    if re.fullmatch('[A-Za-z0-9_]{1,8}', original) and original.upper() not in reserved:
        return original
    prefix = re.sub('[^A-Z0-9]', '', original.upper())[:2].ljust(2, 'T')
    return prefix + hashlib.sha256(Path(relative).as_posix().encode('utf-8')).hexdigest()[:6].upper()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_title(path, target):
    """Preserve each existing route's metadata policy and original-stem fallback."""
    try:
        route = select_mdx_route(inspect_source(path), compatibility_target=target)
    except ValueError:
        # Conversion will diagnose unsupported source semantics; do not replace that error.
        return path.stem, path.stem
    if route == 'psg-scc-to-opm':
        title = title_from_gd3(path, path.stem, 'ja')
        return title, title
    fields = read_gd3(path)
    if fields and (fields[1].strip() or fields[0].strip()):
        return None, fields[1].strip() or fields[0].strip()
    return path.stem, path.stem


def _bounded(path, output):
    if not path.resolve().is_relative_to(output):
        raise ValueError(f'Listening output path leaves output directory: {path}')
    return path


def _save_manifest(path, entries):
    temporary = path.with_suffix('.json.tmp')
    _bounded(temporary, path.parent.resolve())
    temporary.write_text(json.dumps(dict(schema_version=1, entries=entries), indent=2,
                                     ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def run_listening_batch(source, output, *, core, options):
    """Stage byte-identical source names before conversion; never patch MDX names."""
    from export_mdx import _save_results
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.exists():
        raise ValueError(f'Input does not exist: {source}')
    if source.is_dir():
        if source == output or source in output.parents or output in source.parents:
            raise ValueError('Input and output directories must be separate trees')
        files = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in ('.vgm', '.vgz'))
    else:
        if source.suffix.lower() not in ('.vgm', '.vgz') or source.is_relative_to(output):
            raise ValueError('Input must be a separate VGM/VGZ file or directory')
        files = [source]
    if not files:
        raise ValueError('No VGM/VGZ files found')
    manifest_path = _bounded(output / 'listening_manifest.json', output)
    previous = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schema_version') != 1 or not isinstance(manifest.get('entries'), dict):
            raise ValueError('Invalid listening ownership manifest')
        previous = manifest['entries']
    planned = []
    names = set()
    for path in files:
        relative = path.relative_to(source) if source.is_dir() else Path(path.name)
        key = relative.as_posix()
        stem = safe_stem(relative)
        if stem.casefold() in names:
            raise ValueError(f'Listening filename collision for {relative}; use separate output trees')
        names.add(stem.casefold())
        folder = _bounded(output / 'tracks' / stem, output)
        staged = _bounded(output / '_source_inputs' / (stem + path.suffix.lower()), output)
        owned = previous.get(key, {})
        # Check every prior publication before removing any, including stale optional VGM/PDX.
        for published, digest in owned.get('published', {}).items():
            target = _bounded(output / published, output)
            if target.parent != folder or target.suffix not in ('.mml', '.mdx', '.pdx', '.txt', '.vgm'):
                raise ValueError(f'Invalid listening ownership entry: {target}')
            if target.is_file() and _digest(target) != digest:
                raise ValueError(f'Refusing to replace modified listening artifact: {target}')
        for extension in ('.mml', '.mdx', '.pdx', '.txt', '.vgm'):
            target = _bounded(folder / (stem + extension), output)
            if target.exists() and str(target.relative_to(output)).replace('\\', '/') not in owned.get('published', {}):
                raise ValueError(f'Refusing to overwrite unowned listening artifact: {target}')
        expected_staged = owned.get('staged_sha256', owned.get('source_sha256'))
        reuse_staged = False
        if staged.exists() and _digest(staged) != expected_staged:
            # A stop between copyfile and manifest save can leave an identical,
            # unregistered input. Adopt it without overwriting any bytes.
            if expected_staged is not None or _digest(staged) != _digest(path):
                raise ValueError(f'Refusing to replace unowned or modified staged source: {staged}')
            reuse_staged = True
        planned.append((path, key, stem, folder, staged, owned, reuse_staged))
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    entries = dict(previous)
    for path, key, stem, folder, staged, owned, reuse_staged in planned:
        folder.mkdir(parents=True, exist_ok=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        source_sha256 = _digest(path)
        if reuse_staged:
            if not staged.is_file() or _digest(staged) != source_sha256:
                raise ValueError(f'Staged source changed before ownership recovery: {staged}')
        else:
            shutil.copyfile(path, staged)
        if _digest(staged) != source_sha256:
            raise ValueError('Staged source bytes changed unexpectedly')
        # Staging ownership is recoverable even if core setup raises before returning a row.
        # Existing publications retain the provenance of their last completed export.
        entries[key] = dict(owned) if owned else dict(input=key, source_path=str(path),
                                                     source_sha256=source_sha256, published={})
        entries[key].update(safe_stem=stem, staged_input=str(staged.relative_to(output)),
                            staged_sha256=source_sha256)
        _save_manifest(manifest_path, entries)
        title_override, title = original_title(path, options.get('target', 'mdx'))
        diagnostics = _bounded(output / '_diagnostics' / stem, output)
        row = core(staged, diagnostics, **options, listening_layout=False, _title=title_override,
                   _report_source=path, _display_name=key)[0]
        # Recheck ownership after generation: a user may have edited a publication meanwhile.
        for published, digest in owned.get('published', {}).items():
            target = _bounded(output / published, output)
            if target.exists() and (not target.is_file() or _digest(target) != digest):
                raise ValueError(f'Refusing to replace modified listening artifact: {target}')
        for extension in ('.mml', '.mdx', '.pdx', '.txt', '.vgm'):
            target = _bounded(folder / (stem + extension), output)
            if target.exists() and str(target.relative_to(output)).replace('\\', '/') not in owned.get('published', {}):
                raise ValueError(f'Refusing to overwrite unowned listening artifact: {target}')
        for published in owned.get('published', {}):
            _bounded(output / published, output).unlink(missing_ok=True)
        # A current failure row supersedes old successful artifacts; preflight exceptions do not.
        entries[key] = dict(input=key, source_path=str(path), safe_stem=stem,
                            source_sha256=source_sha256, staged_sha256=source_sha256,
                            staged_input=str(staged.relative_to(output)), published={})
        _save_manifest(manifest_path, entries)
        row['input'] = key
        row['safe_stem'] = stem
        row['source_sha256'] = entries[key]['source_sha256']
        for label in ('compiler_input', 'compiler_native_mdx', 'compiler_metadata',
                      'pcm_assessment', 'error_log'):
            if row.get(label):
                row[label] = str((diagnostics / row[label]).relative_to(output))
        for label, extension in (('mml', '.mml'), ('mdx', '.mdx'), ('pdx', '.pdx'),
                                 ('report', '.txt'), ('vgm', '.vgm')):
            diagnostic = row.get(label)
            row[label] = ''
            if not diagnostic:
                continue
            current = _bounded(diagnostics / diagnostic, output)
            # Partial/failing binaries remain diagnostic evidence, not listening publications.
            if label in ('mdx', 'pdx', 'vgm') and row['status'] != 'success':
                continue
            target = _bounded(folder / (stem + extension), output)
            shutil.copyfile(current, target)
            row[label] = str(target.relative_to(output))
            entries[key]['published'][str(target.relative_to(output)).replace('\\', '/')] = _digest(target)
        entries[key]['status'] = row['status']
        entries[key]['title'] = title
        _save_manifest(manifest_path, entries)
        rows.append(row)
        _save_results(output, rows)
    return rows
