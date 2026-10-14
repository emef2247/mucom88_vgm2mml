"""Prepare independent MDX -> MML reference evidence with external mdxtools."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mml_structure(text):
    """Return encoded repeat nesting/counts, without interpreting musical events."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    tracks = {}
    for line in text.splitlines():
        match = re.match(r'^([A-HP-W])\s+(.*)', line)
        if match:
            tracks.setdefault(match[1], []).append(match[2])
    repeats, errors, markers = [], [], 0
    for track, lines in tracks.items():
        stack = []
        for token in re.finditer(r'\[|\](\d*)|/|(?<![A-Za-z])L(?![A-Za-z])', ''.join(lines)):
            value = token.group()
            if value == '[':
                stack.append({'track': track, 'depth': len(stack) + 1, 'count': None, 'exits': 0})
                repeats.append(stack[-1])
            elif value.startswith(']'):
                if not stack:
                    errors.append(f'{track}: unmatched repeat end')
                else:
                    stack.pop()['count'] = int(token.group(1) or 2)
            elif value == '/':
                if not stack:
                    errors.append(f'{track}: repeat exit outside a repeat')
                else:
                    stack[-1]['exits'] += 1
            else:
                markers += 1
        if stack:
            errors.append(f'{track}: unclosed repeat')
    return {'repeats': repeats, 'song_loop_markers': markers, 'errors': errors}


def dump_structure(text):
    """Read mdxdump's repeat sequence; its output omits track boundaries."""
    repeats, stack, errors = [], [], []
    loops = 0
    omitted = []
    for line in text.splitlines():
        start = re.fullmatch(r'RepeatStart (\d+) \d+', line)
        if start:
            stack.append({'depth': len(stack) + 1, 'count': int(start[1]), 'exits': 0})
            repeats.append(stack[-1])
        elif line.startswith('RepeatEnd '):
            if stack:
                stack.pop()
            else:
                errors.append('unmatched repeat end in dump')
        elif line.startswith('RepeatEscape '):
            if stack:
                stack[-1]['exits'] += 1
            else:
                errors.append('repeat exit outside a repeat in dump')
        elif line.startswith('PerformanceEnd '):
            loops += int(int(line.split()[1]) != 0)
        elif line.startswith(('PCM8Enable', 'FadeOut', 'UndefinedCommand')):
            omitted.append(line.split()[0])
    if stack:
        errors.append('unclosed repeat in dump')
    return {'repeats': repeats, 'song_loops': loops, 'review_commands': omitted, 'errors': errors}


def audit_structure(mml, dump):
    emitted = mml_structure(mml)
    encoded = dump_structure(dump)
    comparable = [{k: r[k] for k in ('depth', 'count', 'exits')} for r in emitted['repeats']]
    issues = emitted['errors'] + encoded['errors']
    if comparable != encoded['repeats']:
        issues.append('repeat nesting/count/exit mismatch')
    if emitted['song_loop_markers'] != encoded['song_loops']:
        issues.append('song loop marker count mismatch')
    if encoded['review_commands']:
        issues.append('commands needing manual review: ' + ', '.join(encoded['review_commands']))
    return {'mml': emitted, 'mdx_dump': encoded, 'issues': issues,
            'scope': 'delimiter nesting, repeat counts and exit/loop marker counts only; '
                     'loop destinations, exit positions and playback equivalence require separate validation'}


def run_tool(command, folder, label, timeout):
    result = {'command': command, 'status': 'failed'}
    try:
        run = subprocess.run(command, capture_output=True, timeout=timeout)
        stdout, stderr = run.stdout, run.stderr
        result.update(exit_status=run.returncode, status='completed' if run.returncode == 0 else 'failed')
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b'', error.stderr or b''
        result.update(status='timeout', exit_status=None)
    except OSError as error:
        stdout, stderr = b'', str(error).encode('utf-8')
        result.update(exit_status=None)
    for suffix, data in [('stdout', stdout), ('stderr', stderr)]:
        path = folder / f'{label}.{suffix}'
        path.write_bytes(data)
        result[suffix + '_sha256'] = digest(path)
    return result, stdout


def decompile(source, folder, mdx2mml, mdxdump, timeout=60, *, tool_metadata=None):
    folder.mkdir(parents=True, exist_ok=True)
    result = {'source': str(source.resolve()), 'source_sha256': digest(source),
              'role': 'independent reference; never converter input or recovered original score',
              'status': 'tool_failed'}
    if tool_metadata is not None:
        result['tools'] = tool_metadata
    run, raw = run_tool([str(mdx2mml), '-u', str(source.resolve())], folder, 'mdx2mml', timeout)
    result['mdx2mml'] = run
    # Preserve even partial output. Success requires more than a zero exit status.
    mml = folder / 'decompiled.mml'
    mml.write_bytes(raw)
    result['mml_sha256'] = digest(mml)
    dumped, dump_raw = run_tool([str(mdxdump), str(source.resolve())], folder, 'mdxdump', timeout)
    result['mdxdump'] = dumped
    if run['status'] == 'completed' and dumped['status'] == 'completed':
        try:
            text = raw.decode('utf-8')
            if '#title ' not in text or '/* Track A */' not in text:
                raise ValueError('missing expected mdx2mml header/track output')
            dump_text = dump_raw.decode('cp932', errors='replace')
            command = (r'^(?:Rest|Note|Set\w+|Pan|VolumeInc|VolumeDec|DisableKeyOff|'
                       r'Repeat\w+|Detune|Portamento|PerformanceEnd|KeyOnDelay|SyncSend|'
                       r'SyncWait|ADPCMNoiseFreq|LFO\w+|OPMLFO\w*|PCM8Enable|'
                       r'PCM8ExpansionShift|FadeOut|UndefinedCommand)(?:\s|$)')
            if not (re.search(r'^title ', dump_text, re.M) and
                    re.search(r'^pdxfile ', dump_text, re.M) and
                    re.search(command, dump_text, re.M)):
                raise ValueError('missing expected mdxdump header/command output')
            result['structure'] = audit_structure(text, dump_text)
            result['status'] = 'needs_review' if result['structure']['issues'] else 'decompiled'
        except (UnicodeDecodeError, ValueError) as error:
            result.update(status='needs_review', error=str(error))
    (folder / 'reference.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def tool_identity(path, revision):
    return {'path': str(path.resolve()), 'sha256': digest(path), 'upstream_revision': revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='MDX file or directory to scan recursively')
    parser.add_argument('--outdir', type=Path, required=True, help='Separate directory for reference evidence')
    parser.add_argument('--mdx2mml', type=Path, required=True)
    parser.add_argument('--mdxdump', type=Path, required=True)
    parser.add_argument('--tool-revision', help='Record the upstream checkout revision used to build tools')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    source, outdir = args.input.resolve(), args.outdir.resolve()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if source == outdir or (source.is_dir() and outdir.is_relative_to(source)):
        parser.error('--outdir must be separate from the input tree')
    tools = {}
    for name in ('mdx2mml', 'mdxdump'):
        path = getattr(args, name).resolve()
        if not path.is_file():
            parser.error(f'External {name} not found: {path}')
        setattr(args, name, path)
        tools[name] = tool_identity(path, args.tool_revision)
    sources = ([source] if source.is_file() and source.suffix.lower() == '.mdx' else
               sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.mdx'))
    if not sources:
        parser.error('No MDX files found')
    # A dedicated, empty destination prevents reference/source files being overwritten.
    if outdir.exists() and any(outdir.iterdir()):
        parser.error('--outdir must be empty; use a fresh directory for each run')
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sources:
        relative = path.relative_to(source) if source.is_dir() else Path(path.name)
        # Retain the extension, so case-sensitive same-stem MDX paths do not collide.
        result = decompile(path, outdir / relative, args.mdx2mml, args.mdxdump, args.timeout,
                           tool_metadata=tools)
        result['relative_source'] = str(relative)
        rows.append(result)
        print(str(relative) + ': ' + result['status'], flush=True)
        (outdir / 'manifest.json').write_text(json.dumps({'tools': tools, 'cases': rows}, indent=2) + '\n', encoding='utf-8')
    return int(any(row['status'] != 'decompiled' for row in rows))


if __name__ == '__main__':
    raise SystemExit(main())
