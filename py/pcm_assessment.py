"""MDX target assessments, separate from immutable PCM source evidence."""
import csv
import hashlib
import json
from pathlib import Path


TARGET_PROFILE = 'MDX PCM4/8 enabled (E8); 16 tracks; single P projection'
PAN_EVIDENCE = ('Projection retains onset pan; original PCM1 evidence: mxdrv17.s FC/ED '
                'store track state until new ADPCMOUT. Extended-mode live pan is not certified')


def generated_binary_artifacts(outdir, stem):
    """Identify unchanged MDX/PDX files from a previous generated assessment."""
    out = Path(outdir)
    try:
        previous = json.loads((out / (stem + '.pcm.assessment.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ()
    if not isinstance(previous, dict) or not isinstance(previous.get('artifacts'), dict):
        return ()
    owned = []
    for name in ('mdx', 'pdx'):
        path = out / (stem + '.' + name)
        entry = previous.get('artifacts', {}).get(name, {})
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            continue
        try:
            if (entry.get('status') == 'generated' and entry.get('sha256')
                    and Path(entry.get('path', '')).resolve() == path.resolve()
                    and path.is_file()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']):
                owned.append(path)
        except OSError:
            continue
    return tuple(owned)


def aggregate(items):
    statuses = {item['status'] for item in items}
    return next((status for status in ('fail', 'unverified', 'lossy', 'pass')
                 if status in statuses), 'unverified')


class PcmAssessment:
    def __init__(self, policy):
        if policy not in ('strict', 'best-effort'):
            raise ValueError('PCM policy must be strict or best-effort')
        self.policy = policy
        self.target_profile = TARGET_PROFILE
        self.artifact_status = 'pending'
        self.validation_run = 'not_run'
        self.items = []
        self.block_reasons = []
        self.artifacts = {}
        self.add('runtime', 'unverified', 'runtime_not_run',
                 'Independent MXDRV/IOCS runtime comparison has not run', scope='runtime_validation')

    def add(self, criterion, status, code, detail, *, scope='target_projection',
            cause='', playback_id=None, source_event_id=None, start_vgmticks=None,
            end_vgmticks=None, source_value=None, projected_value=None,
            fallback='', evidence=''):
        if status not in ('pass', 'lossy', 'unverified', 'fail'):
            raise ValueError('Invalid PCM assessment status')
        self.items.append(dict(criterion=criterion, status=status, code=code,
                               detail=detail, scope=scope, cause=cause,
                               playback_id=playback_id, source_event_id=source_event_id,
                               start_vgmticks=start_vgmticks, end_vgmticks=end_vgmticks,
                               source_value=source_value, projected_value=projected_value,
                               fallback=fallback, evidence=evidence))

    @property
    def assessment_status(self):
        return aggregate(item for item in self.items if item['scope'] == 'target_projection')

    def block(self, reason):
        self.artifact_status = 'blocked'
        self.block_reasons.append(reason)

    def generated(self):
        self.artifact_status = 'generated'

    def error(self, detail):
        self.artifact_status = 'error'
        self.add('artifact', 'fail', 'artifact_generation_failed', detail,
                 scope='artifact', cause='implementation_or_external_tool')

    def as_dict(self):
        return dict(schema_version=1, policy=self.policy, target_profile=self.target_profile,
                    target_layout=dict(track_count=16, pcm_mode_command='E8',
                                       active_pcm_tracks=['P'], inactive_pcm_tracks=list('QRSTUVW')),
                    artifact_status=self.artifact_status, assessment_status=self.assessment_status,
                    validation_status=aggregate(self.items), validation_run=self.validation_run,
                    artifacts=self.artifacts,
                    block_reasons=self.block_reasons, items=self.items,
                    known_losses=[item for item in self.items if item['status'] == 'lossy'],
                    unverified_items=[item for item in self.items if item['status'] == 'unverified'],
                    unexpected_mismatches=[item for item in self.items if item['status'] == 'fail'])

    def dump(self, outdir, stem):
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f'{stem}.pcm.assessment.json').write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        fields = tuple(self.items[0])
        with (out / f'{stem}.pcm.assessment.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            for item in self.items:
                writer.writerow({key: json.dumps(value, ensure_ascii=False)
                                 if isinstance(value, (list, dict)) else value
                                 for key, value in item.items()})


class ProjectionError(ValueError):
    def __init__(self, detail, assessment):
        super().__init__(detail)
        self.assessment = assessment
