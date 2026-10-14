"""Run the native unattended mucomDotNET compiler separately from conversion."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PCM_COMPILER = ROOT / '.tools/mucomdotnet-pcm/output/NET6/mucomDotNETConsole.exe'
PCM_REPLAY = PCM_COMPILER.with_name('mucomDotNET2vgm.exe')


def requires_pcm_extension(score):
    return any(re.search(r'\by0,1,\d+,\d+',line.split(';',1)[0]) for line in score.splitlines())


def _tool_path(path, compiler):
    if sys.platform != 'win32' and compiler.suffix.lower() == '.exe':
        run = subprocess.run(['wslpath', '-w', str(path.resolve())],
                             capture_output=True, text=True, check=True)
        return run.stdout.strip()
    return str(path.resolve())


def compile_muc(muc, mub, *, mucom88=None, timeout=180, log_path=None):
    """Resolve owned dependencies in a separate, inspectable compiler input."""
    muc, mub = Path(muc).resolve(), Path(mub).resolve()
    score = muc.read_bytes().decode('cp932')
    extended = requires_pcm_extension(score)
    default = PCM_COMPILER if extended else (shutil.which('mucomDotNETConsole.exe') or
                    ROOT / '.tools/mucom88-260407/mucom88win260407/mucomdotnet/compiler/mucomDotNETConsole.exe')
    compiler = Path(mucom88 or default).resolve()
    if extended and not compiler.is_file():
        raise ValueError('PCM live controls require multi-port y: run python scripts/setup_mucom_pcm_tools.py, '
                         'or specify a compatible mucomDotNET compiler with --mucom88')
    if not compiler.is_file():
        raise ValueError('Native MUCOM88 compiler unavailable; specify --mucom88 mucomDotNETConsole.exe')
    if timeout <= 0:
        raise ValueError('Timeout must be positive')
    if mub == muc:
        raise ValueError('Compiler output must differ from the input score')
    mub.unlink(missing_ok=True)
    log_path = Path(log_path or muc.with_suffix('.compile.log')).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='compiler_', dir=log_path.parent))
    prepared = folder / muc.name
    def resolve_tag(match):
        name = match.group(2).strip().strip('"')
        if not name or '/' in name or '\\' in name or ':' in name or name in ('.', '..'):
            raise ValueError(f'Compiler dependency must be a local owned filename: {name}')
        dependency = muc.parent / name
        if not dependency.is_file() or not dependency.stat().st_size:
            raise ValueError(f'Compiler dependency unavailable: {dependency}')
        return f'#{match.group(1)} {_tool_path(dependency, compiler)}'
    score = re.sub(r'^[ \t]*#(pcm|voice)[ \t]+([^\r\n]+)', resolve_tag, score,
                   flags=re.MULTILINE | re.IGNORECASE)
    # The native console splits on CRLF; LF input can silently produce zero tracks.
    score = score.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')
    prepared.write_bytes(score.encode('cp932'))
    generated = prepared.with_suffix('.mub')
    command = [str(compiler), _tool_path(prepared, compiler)]
    options = dict(cwd=folder, capture_output=True, timeout=timeout)
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        run = subprocess.run(command, **options)
    except subprocess.TimeoutExpired as error:
        log_path.write_bytes((error.stdout or b'') + (error.stderr or b'') + b'\nCompilation timed out\n')
        generated.unlink(missing_ok=True)
        raise
    log_path.write_bytes(run.stdout + run.stderr)
    try:transcript=(run.stdout+run.stderr).decode('utf-8')
    except UnicodeDecodeError:transcript=(run.stdout+run.stderr).decode('cp932',errors='replace')
    native_errors=re.findall(r'^.*\[\s*ERROR\s*\].*$',transcript,re.MULTILINE)
    if run.returncode or not generated.is_file() or not generated.stat().st_size:
        generated.unlink(missing_ok=True)
        detail=native_errors[0].strip() if native_errors else f'exit={run.returncode}'
        raise RuntimeError(f'MUCOM88 compilation failed: {detail}; see {log_path}')
    # The console can exit zero and emit a valid header with no score commands.
    # Its per-channel total counts are compiler evidence, not playback validation.
    summary = re.search(r'\[\s*Total count\s*\]\r?\n[^\r\n]*', transcript)
    counts = re.findall(r'\b[A-K]\s*:\s*(\d+)', summary.group(0)) if summary else []
    if (generated.read_bytes()[:4] != b'MUB8' or not counts
            or not any(int(count) > 0 for count in counts)
            or re.search(r'\[\s*ERROR\s*\]', transcript, re.IGNORECASE)):
        generated.unlink(missing_ok=True)
        raise RuntimeError(f'MUCOM88 compiler produced no nonempty track counts; see {log_path}')
    shutil.copyfile(generated, mub)
    return mub
