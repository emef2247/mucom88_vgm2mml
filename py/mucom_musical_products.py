"""Independent source-reference and MUCOM blueprint products with owned publication."""
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath
import tempfile

from musical_hal import build_native_performance, build_score_guide, write_native_vgm
from musical_hal.score_normalization import normalize
from musical_hal.vgm_io import read_vgm_bytes


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _bounded(root, relative):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or any(c in str(relative) for c in '\\:'):
        raise ValueError('Invalid musical product path')
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Musical product leaves output directory')
    return path


def _atomic_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix='.json.tmp') as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps(value, indent=2)+'\n').encode('utf-8'))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _Recovery(dict):
    """Durable pre-publication copies retained until the ownership commit."""
    def __init__(self, root, paths):
        super().__init__((path, path.read_bytes() if path.exists() else None)
                         for path in dict.fromkeys(paths))
        self.folder = Path(tempfile.mkdtemp(dir=root, prefix='.publication-recovery-'))
        records = []
        for index, (path, data) in enumerate(self.items()):
            name = str(index) + '.bak' if data is not None else None
            if name is not None:
                (self.folder / name).write_bytes(data)
            records.append(dict(destination=str(path.resolve()), backup=name))
        (self.folder / 'recovery.json').write_text(json.dumps(records, indent=2), encoding='utf-8')

    def discard(self):
        shutil.rmtree(self.folder, ignore_errors=True)


def _restore_files(backups):
    errors = []
    for path, data in backups.items():
        try:
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        except OSError as error:
            errors.append(str(path) + ': ' + str(error))
    if errors:
        folder = getattr(backups, 'folder', None)
        raise OSError('Rollback incomplete; recovery copies: ' + str(folder) + '; ' + '; '.join(errors))
    if isinstance(backups, _Recovery):
        backups.discard()


def _publish(root, manifest_path, ownership, group, artifacts, source, force):
    """Commit files, stale-file removal and ownership together; roll back on error."""
    previous = ownership.get(group, {})
    incoming = {}
    for artifact in artifacts:
        local = artifact.get('local', artifact['relative'])
        destination = _bounded(root, local)
        if destination.resolve() == source.resolve():
            raise ValueError('Musical product would overwrite the source input')
        if local in incoming:
            raise ValueError('Duplicate musical product path')
        if any(local in entries for key, entries in ownership.items() if key != group):
            raise ValueError('Musical product overlaps another owned bundle')
        incoming[local] = artifact
    paths = {name: _bounded(root, name) for name in set(previous) | set(incoming)}
    for name, destination in paths.items():
        if destination.resolve() == source.resolve():
            raise ValueError('Owned musical product overlaps the source input')
        if destination.exists():
            digest = _hash(destination.read_bytes())
            old = previous.get(name)
            if old is None or digest != old['sha256']:
                raise FileExistsError('Protected existing musical product: '+str(destination))
    next_ownership = dict(ownership)
    next_ownership[group] = {name: dict(sha256=_hash(item['data']), kind=item['kind'],
        relative=item['relative']) for name, item in incoming.items()}
    payload = (json.dumps(next_ownership, indent=2)+'\n').encode('utf-8')
    writes = {paths[name]: artifact['data'] for name, artifact in incoming.items()}
    writes[manifest_path] = payload
    backups = _Recovery(root, [*paths.values(), manifest_path])
    with tempfile.TemporaryDirectory(dir=root, prefix='.musical-publish-') as folder:
        staged = {}
        for index, (path, data) in enumerate(writes.items()):
            candidate = Path(folder)/str(index)
            candidate.write_bytes(data)
            staged[path] = candidate
        try:
            for path, candidate in staged.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(candidate, path)
            for name in set(previous)-set(incoming):
                path = paths[name]
                if path.exists():
                    path.unlink()
        except Exception:
            _restore_files(backups)
            raise
    backups.discard()
    ownership.clear()
    ownership.update(next_ownership)
    return [dict(path=str(paths[name].resolve()), relative=item['relative'], kind=item['kind'])
            for name, item in incoming.items() if not item.get('internal')]


def prepare(source, output_root, stem, *, score_normalize=False, score_tempo=225,
            score_guided_loops=False, legacy_loops=False, enhance_macros=True,
            force=False, dump_passes=False, song_loop=True):
    """Prepare independent products; target conversion can proceed after any failure."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = Path(source).resolve()
    if not stem or stem in ('.', '..') or any(c in stem for c in '/\\:'):
        raise ValueError('Invalid native source product filename stem')
    report_path = root/(stem+'.musical_layer.json')
    manifest_path = root/(stem+'.musical_products.ownership.json')
    if source in (report_path.resolve(), manifest_path.resolve()):
        raise ValueError('Musical product metadata would overwrite the source input')
    report = dict(status='failed', source=dict(status='pending'),
        native_reference=dict(status='pending'), blueprint=dict(status='pending'),
        products=[], conversion_input=str(source), sample_rate=44100,
        source_time_domain='original_source_samples', report_path=str(report_path))
    # force controls target conversion policy; edited source products stay protected.
    ownership = {}
    try:
        if manifest_path.exists():
            ownership = json.loads(manifest_path.read_text(encoding='utf-8'))
            if not isinstance(ownership, dict):
                raise ValueError('Invalid musical product ownership manifest')
        passes = root/'_musical_source'/stem
        original = build_native_performance(source, passes, stem)
        guide = build_score_guide(original, repeats=score_guided_loops)
        guide.dump(passes, stem)
        report['source'] = dict(status='passed', source_sha256=original.source_sha256,
            evidence_root=str(passes), source_end_sample=original.end_sample)
    except Exception as error:
        report['source'] = dict(status='failed', detail=str(error), error_type=type(error).__name__)
        report['native_reference'] = dict(status='skipped', detail='Source analysis failed')
        report['blueprint'] = dict(status='skipped', detail='Source analysis failed')
        _atomic_json(report_path, report)
        return report
    try:
        reference = original
        artifacts = []
        with tempfile.TemporaryDirectory(dir=root, prefix='.musical-reference-') as folder:
            temporary = Path(folder)/'reference.vgm'
            if score_normalize:
                projected = normalize(original, tempo=score_tempo,
                    source_loop=song_loop and original.song_loop_sample is not None)
                projected.dump(passes, stem)
                reference = projected.performance
                report['normalization'] = projected.report
            raw=read_vgm_bytes(source)
            offset=int.from_bytes(raw[0x14:0x18],'little')
            gd3=None
            if offset:
                at=0x14+offset
                if raw[at:at+4]==b'Gd3 ' and at+12<=len(raw):
                    stop=at+12+int.from_bytes(raw[at+8:at+12],'little')
                    if stop>len(raw):raise ValueError('Truncated native GD3 metadata')
                    gd3=raw[at:stop]
            write_native_vgm(reference, temporary, gd3=gd3)
            data = temporary.read_bytes()
            artifacts.append(dict(local=stem+'.vgm', relative='tracks/'+stem+'.vgm',
                                  data=data, kind='native_reference'))
            if score_normalize:
                normalized = '_score_normalized/'+stem+'.vgm'
                conversion_data=data
                if song_loop and reference.song_loop_sample is not None:
                    write_native_vgm(reference,temporary,gd3=gd3,source_loop=True)
                    conversion_data=temporary.read_bytes()
                artifacts.append(dict(local=normalized, relative=normalized, data=conversion_data,
                                      kind='normalized_input', internal=True))
            report['products'].extend(_publish(root, manifest_path, ownership, 'reference', artifacts, source, force))
            report['native_reference'] = dict(status='passed', path=str(root/(stem+'.vgm')),
                                              normalized=bool(score_normalize))
            if score_normalize:
                report['conversion_input'] = str(root/'_score_normalized'/(stem+'.vgm'))
    except Exception as error:
        report['native_reference'] = dict(status='failed', detail=str(error), error_type=type(error).__name__)
    try:
        import mucom_blueprint
        artifacts, details = mucom_blueprint.render(original, guide, stem, tempo=score_tempo,
            legacy_loops=legacy_loops, enhance_macros=enhance_macros)
        if not artifacts and details.get('status')=='not_applicable':
            report['blueprint']=dict(details)
        else:
            if not artifacts:raise ValueError('Blueprint renderer produced no artifacts')
            report['products'].extend(_publish(root, manifest_path, ownership, 'blueprint', artifacts, source, force))
            report['blueprint'] = dict(details, status='passed')
    except Exception as error:
        report['blueprint'] = dict(status='failed', detail=str(error), error_type=type(error).__name__)
    statuses = [report[key]['status'] for key in ('native_reference', 'blueprint')]
    report['status'] = 'passed' if all(s in ('passed','not_applicable') for s in statuses) else 'partial' if 'passed' in statuses else 'failed'
    _atomic_json(report_path, report)
    return report
