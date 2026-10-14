"""Isolated native MXC compilation for the output-only export workflow."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def compiler_evidence_paths(prepared):
    prepared = Path(prepared)
    return prepared.with_suffix('.mdx'), prepared.with_suffix('.metadata.json')


def restore_mxc_title(raw, text):
    """Repair only MXC v1.01's observed empty title above 64 CP932 bytes."""
    marker = raw.find(b'\r\n\x1a')
    if marker < 0:
        raise RuntimeError('Native MDX has no title terminator')
    pdx_end = raw.find(b'\0', marker + 3)
    if pdx_end < 0 or len(raw) < pdx_end + 5:
        raise RuntimeError('Native MDX has no complete PDX/data header')
    expected = None
    in_comment = False
    for line in text.splitlines():
        if not in_comment:
            match = re.fullmatch(r'#title[ \t]+"([^"\r\n]*)"[ \t]*', line)
            if match:
                expected = match[1].encode('cp932')
                break
        _, in_comment = _parts(line, in_comment)
    native = raw[:marker]
    final = raw
    status = 'not_requested' if expected is None else 'unchanged'
    if expected is not None:
        if any(value in expected for value in (b'\r', b'\n', b'\x1a', b'\0')):
            raise ValueError('MML title contains an MDX header delimiter')
        if native != expected:
            if len(expected) <= 64 or native:
                raise RuntimeError('Native MXC title unexpectedly differs from the MML title')
            # All offsets are relative to the data after the PDX name. Keep the
            # whole suffix, including that name and offset table, unchanged.
            final = expected + raw[marker:]
            status = 'restored'
    report = dict(title_status=status,
                  reason='MXC v1.01 emitted an empty title above 64 bytes' if status == 'restored' else '',
                  expected_title_bytes=None if expected is None else len(expected),
                  native_title_bytes=len(native),
                  final_title_bytes=final.find(b'\r\n\x1a'),
                  native_sha256=hashlib.sha256(raw).hexdigest(),
                  final_sha256=hashlib.sha256(final).hexdigest(),
                  payload_sha256=hashlib.sha256(raw[marker:]).hexdigest(),
                  payload_unchanged=final[final.find(b'\r\n\x1a'):] == raw[marker:],
                  mdx_validation='unverified')
    return final, report


def _parts(text, in_comment):
    """Separate music syntax from quoted text and both comment forms."""
    parts = []
    position = 0
    while position < len(text):
        if in_comment:
            end = text.find('*/', position)
            stop = len(text) if end < 0 else end + 2
            parts.append((False, text[position:stop]))
            in_comment = end < 0
        elif text.startswith('/*', position):
            in_comment = True
            continue
        elif text[position] == ';':
            parts.append((False, text[position:]))
            break
        elif text[position] == '"':
            stop = position + 1
            while stop < len(text):
                if text[stop] == '\\':
                    stop += 2
                elif text[stop] == '"':
                    stop += 1
                    break
                else:
                    stop += 1
            parts.append((False, text[position:stop]))
        else:
            stop = position + 1
            while stop < len(text) and text[stop] not in ';"' and not text.startswith('/*', stop):
                stop += 1
            parts.append((True, text[position:stop]))
        position = stop
    return parts, in_comment


def prepare_mxc(text):
    """Render lossless syntax adaptations for the verified MXC v1.01 limits."""
    def rest(match):
        number, dots = int(match.group(2)), len(match.group(3))
        if match.group(1) == '%':
            numerator, denominator = number, 1
        else:
            numerator, denominator = 192, number
        numerator *= (1 << (dots + 1)) - 1
        denominator *= 1 << dots
        if denominator == 0 or numerator % denominator:
            return match.group(0)
        ticks = numerator // denominator
        if ticks <= 256:
            return match.group(0)
        count, tail = divmod(ticks, 128)
        return ' '.join(['r%128'] * count + ([f'r%{tail}'] if tail else []))

    lines = []
    previous = {}
    octaves = {}
    in_comment = False
    voice_depth = 0
    for line in text.splitlines(keepends=True):
        newline = '\n' if line.endswith('\n') else ''
        content = line[:-1] if newline else line
        match = re.match(r'^([ \t]*[A-H]+[ \t]+)(.*)$', content)
        music = match is not None and not in_comment and not voice_depth
        prefix, body = (match.group(1), match.group(2)) if music else ('', content)
        parts, in_comment = _parts(body, in_comment)
        if music:
            track = prefix.strip()
            octave = octaves.get(track)
            for index, (code, value) in enumerate(parts):
                if code:
                    value = re.sub(r'[ \t]+&', '&', value)
                    value = re.sub(r'(?<![A-Za-z0-9_])r(%?)(\d+)(\.*)(?![\d.])', rest, value)
                    def octave_token(token):
                        nonlocal octave
                        command = token.group(0)
                        if command.startswith('o'):
                            octave = int(token.group(1))
                        elif command in '[]/':
                            # Loop exits can carry a different runtime octave.
                            octave = None
                        elif octave is not None:
                            octave += 1 if command == '>' else -1
                            if command == '>' and octave == 8:
                                return 'o8'
                        return command
                    value = re.sub(r'o(\d+)|[<>\[\]/]', octave_token, value)
                    parts[index] = (code, value)
            octaves[track] = octave
            # Wrapping can put a tie on the next physical line of the same track.
            if parts and parts[0][0] and parts[0][1].lstrip().startswith('&') and track in previous:
                prior = lines[previous[track]][1]
                for index in range(len(prior) - 1, -1, -1):
                    if prior[index][0] and prior[index][1].strip():
                        prior[index] = (True, prior[index][1].rstrip() + '& ')
                        parts[0] = (True, parts[0][1].lstrip()[1:])
                        break
            previous[track] = len(lines)
        else:
            for code, value in parts:
                if code:
                    voice_depth += value.count('{') - value.count('}')
        lines.append((prefix, parts, newline))
    return ''.join(prefix + ''.join(value for _, value in parts) + newline
                   for prefix, parts, newline in lines)


def _tool(explicit, names, local_paths, option):
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise ValueError(f'Missing {option} tool: {candidate}')
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for local in local_paths:
        if local.is_file():
            return local.resolve()
    raise ValueError(f'Missing {option} tool; specify {option}, install it on PATH, '
                     'or follow docs/mdx_compiler_setup.md for persistent .tools/ setup')


def _checked(command, cwd, timeout, encoding):
    def decoded(value, codec):
        return value.decode(codec, errors='replace') if isinstance(value, bytes) else (value or '')
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        # Native DOS stdout is CP932; run68's own diagnostics use UTF-8.
        error.output = decoded(error.output, encoding)
        error.stderr = decoded(error.stderr, 'utf-8')
        raise
    result.stdout = decoded(result.stdout, encoding)
    result.stderr = decoded(result.stderr, 'utf-8')
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr
                           or f'Compiler exited with {result.returncode}')
    return result


def compile_mxc(source, output, *, timeout, mxc=None, run68=None, generator,
                prepared_output=None):
    """Publish only a fresh MDX accepted by the existing package parser."""
    native = _tool(mxc, ('MXC.X', 'mxc.x'),
                   (ROOT / '.tools/mxc/mxc.x',
                    ROOT / 'outputs/research/mxc_tools/extracted/mxc.x'), '--mxc')
    emulator = _tool(run68, ('run68', 'run68x'),
                     (ROOT / '.tools/run68x/build/run68',
                      ROOT / 'outputs/research/run68x/build/run68'), '--run68')
    # Strict encoding keeps unsupported metadata visible as a compilation error.
    text = prepare_mxc(Path(source).read_text(encoding='utf-8'))
    score = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n').encode('cp932')
    if prepared_output is not None:
        prepared_output = Path(prepared_output)
        native_copy, metadata = compiler_evidence_paths(prepared_output)
        for path in (prepared_output, native_copy, metadata):
            if not path.resolve().is_relative_to(prepared_output.parent.resolve()):
                raise ValueError(f'Compiler evidence path leaves its directory: {path}')
        prepared_output.parent.mkdir(parents=True, exist_ok=True)
        prepared_output.write_bytes(score)
    with tempfile.TemporaryDirectory(prefix='mdx-mxc-') as temporary:
        workspace = Path(temporary)
        shutil.copyfile(native, workspace / 'MXC.X')
        (workspace / 'SCORE.MML').write_bytes(score)
        result = _checked([str(emulator), str(workspace / 'MXC.X'), 'SCORE.MML'],
                          workspace, timeout, 'cp932')
        compiled = workspace / 'SCORE.mdx'
        if not compiled.is_file() or not compiled.stat().st_size:
            raise RuntimeError(result.stdout + result.stderr + '\nMXC produced no nonempty MDX')
        raw = compiled.read_bytes()
        if prepared_output is not None:
            native_copy.write_bytes(raw)
        final, report = restore_mxc_title(raw, text)
        report.update(native_compiler_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
                      prepared_source_sha256=hashlib.sha256(score).hexdigest(),
                      source_encoding='cp932', source_newlines='CRLF',
                      native_arguments=['SCORE.MML'])
        compiled.write_bytes(final)
        if prepared_output is not None:
            metadata.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        inspection = workspace / 'inspection.csv'
        try:
            _checked([str(generator), '--inspect-mdx', str(compiled), str(inspection)],
                     workspace, timeout, 'utf-8')
            if not inspection.is_file() or not inspection.stat().st_size:
                raise RuntimeError('MDX validation produced no inspection output')
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            report['mdx_validation'] = 'fail'
            if prepared_output is not None:
                metadata.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            raise
        report['mdx_validation'] = 'pass'
        if prepared_output is not None:
            metadata.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        shutil.copyfile(compiled, output)
