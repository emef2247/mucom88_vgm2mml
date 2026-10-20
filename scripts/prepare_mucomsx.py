"""Stage a fresh DOS 8.3 MUCOMSX compile/export job; does not execute DOS."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from setup_mucomsx import DESTINATION, ROOT, verify_installation

PATTERNS = {'basic': 'BASIC.MUC', 'scc3': 'SCC3.MUC', 'fm3': 'FM3.MUC', 'msx-psg': 'MSXPSG.MUC'}


def stage_score(source, outdir, tools=DESTINATION, *, public_pattern=False, compile_only=False):
    source, outdir, tools = Path(source).resolve(), Path(outdir).resolve(), Path(tools).resolve()
    if outdir.exists() and (not outdir.is_dir() or any(outdir.iterdir())):
        raise ValueError('Use a new or empty job directory; existing artifacts are preserved')
    if source.is_relative_to(outdir):
        raise ValueError('The job directory must not contain its source')
    verify_installation(tools)
    score = source.read_bytes().decode('cp932')
    if '\x00' in score or '\x1a' in score:
        raise ValueError('MUC contains NUL or DOS end-of-text inside source')
    score = score.replace('\r\n', '\n').replace('\r', '\n')
    files = {}
    dependencies = []
    def asset(match):
        tag, name = match.group(1), match.group(2).strip().strip('"')
        if not name or any(c in name for c in '/\\:') or name in ('.', '..'):
            raise ValueError('Expected a local asset filename: ' + name)
        path = source.parent / name
        if not path.is_file() and public_pattern and name.upper() == 'FMVOICE.DAT':
            path = tools / 'FMVOICE.DAT'
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('MUC asset unavailable: ' + str(path))
        staged = 'A%02d.%s' % (len(dependencies), 'DAT' if tag.lower() == 'voice' else 'BIN')
        data = path.read_bytes()
        files[staged] = data
        dependencies.append(dict(tag=tag, source=str(path), staged=staged,
                                 sha256=hashlib.sha256(data).hexdigest()))
        return '#' + tag + ' ' + staged
    explicit_tags = {match.group(1).lower() for match in re.finditer(
        r'^#(voice|pcm)[ \t]+', score, flags=re.MULTILINE | re.IGNORECASE)}
    active_parts = set()
    for line in score.splitlines():
        selector = re.match(r'^(?:MSX)?([A-K][A-K0-9]*)[ \t]+', line)
        if selector:
            active_parts.update(selector[1])
    for tag, name, needed in (('voice', 'VOICE.DAT', bool(active_parts & set('ABCHIJ'))),
                              ('pcm', 'MUCOMPCM.BIN', 'K' in active_parts)):
        if tag in explicit_tags:
            continue
        path = source.parent / name
        if path.is_file() and path.stat().st_size:
            data = path.read_bytes()
            files[name] = data
            dependencies.append(dict(tag=tag, source=str(path), staged=name, implicit=True,
                                     sha256=hashlib.sha256(data).hexdigest()))
        elif needed:
            raise ValueError('Implicit ' + name + ' is unavailable; provide the source-owned bank or an explicit #'
                             + tag + ' reference. Staging does not analyze inline voice coverage.')
    score = re.sub(r'^#(voice|pcm)[ \t]+([^\n]+)', asset, score, flags=re.MULTILINE | re.IGNORECASE)
    lines = score.split('\n')
    if any(len(line.encode('cp932')) > 1023 for line in lines):
        raise ValueError('MUCOMSX source line exceeds 1023 bytes')
    prepared = score.replace('\n', '\r\n').encode('cp932')
    if len(prepared) > 65536:
        raise ValueError('MUCOMSX source exceeds 65536 bytes after DOS newline/asset preparation')
    files['SOURCE.MUC'] = prepared
    batch = [
        'IF EXIST DONE.OK DEL DONE.OK', 'IF EXIST FAILED.ERR DEL FAILED.ERR',
        'IF EXIST CHECK.MUB DEL CHECK.MUB', 'IF EXIST SESSION.MUB DEL SESSION.MUB',
        'IF EXIST CHECK.VGM DEL CHECK.VGM',
        'MUC2MUB SOURCE.MUC CHECK.MUB', 'IF ERRORLEVEL 1 GOTO FAIL',
        'MUCPLAY /LOAD SOURCE.MUC', 'IF ERRORLEVEL 1 GOTO FAIL',
        'MUCPLAY /COMPILE', 'IF ERRORLEVEL 1 GOTO FAIL',
        'MUCPLAY /MUB SESSION.MUB', 'IF ERRORLEVEL 1 GOTO FAIL',
        'MUCPLAY /VGM CHECK.VGM /L1', 'IF ERRORLEVEL 1 GOTO FAIL',
        'MUCPLAY /RELEASE', 'IF ERRORLEVEL 1 GOTO FAIL',
        'IF NOT EXIST CHECK.MUB GOTO FAIL', 'IF NOT EXIST SESSION.MUB GOTO FAIL',
        'IF NOT EXIST CHECK.VGM GOTO FAIL', 'ECHO FINISHED > DONE.OK', 'GOTO END',
        ':FAIL', 'MUCPLAY /RELEASE', 'ECHO FAILED > FAILED.ERR', ':END', '']
    if compile_only:
        batch = [
            'IF EXIST DONE.OK DEL DONE.OK', 'IF EXIST FAILED.ERR DEL FAILED.ERR',
            'IF EXIST CHECK.MUB DEL CHECK.MUB',
            'MUC2MUB SOURCE.MUC CHECK.MUB', 'IF ERRORLEVEL 1 GOTO FAIL',
            'IF NOT EXIST CHECK.MUB GOTO FAIL', 'ECHO FINISHED > DONE.OK', 'GOTO END',
            ':FAIL', 'ECHO FAILED > FAILED.ERR', ':END', '']
    files['RUN.BTM'] = '\r\n'.join(batch).encode('ascii')
    metadata = json.loads((tools / 'source.json').read_text(encoding='utf-8'))
    for name in metadata['files']:
        if name in ('MUC2MUB.COM', 'MUCPLAY.COM', 'NOTICE.txt') or name.startswith('LICENSES/'):
            files[name] = (tools / name).read_bytes()
    report = dict(status='staged_not_executed', profile='mucomsx-v1.0-stable', source=str(source),
                  original_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  prepared_source_bytes=len(prepared), dependencies=dependencies,
                  expected_artifacts=(['CHECK.MUB', 'DONE.OK'] if compile_only else
                                      ['CHECK.MUB', 'SESSION.MUB', 'CHECK.VGM', 'DONE.OK']),
                  instructions='Boot DOS2/Nextor, release previous MUCOMSX sessions, CD to this job and run RUN.BTM. Staging is not compilation.',
                  files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    outdir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = outdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (outdir / 'job.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choose = parser.add_mutually_exclusive_group()
    choose.add_argument('--pattern', choices=tuple(PATTERNS), default=None)
    choose.add_argument('--muc', type=Path, help='Existing MUC, not a VGM conversion request')
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--tools', type=Path, default=DESTINATION)
    args = parser.parse_args(argv)
    pattern = args.pattern or 'basic'
    source = args.muc or ROOT / 'tests/fixtures/public/mucomsx' / PATTERNS[pattern]
    result = stage_score(source, args.outdir, args.tools, public_pattern=args.muc is None)
    print('Staged MUCOMSX job (not executed): ' + str(args.outdir))
    print('DOS command in job directory: RUN.BTM')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
