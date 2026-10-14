"""Remove MUCOM loop markers and compile local MUC fixtures with MUCOMVGM.

The originals, compiler logs and provenance manifest remain in a local output
directory. Source encodings and line endings are retained; only compiler input
copies use UTF-8. No MUB files are created.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / 'tests/fixtures/local_only/opna/mucom88/mucom88win_yk2mml191012'
DEFAULT_TOOL = ROOT / '.tools/mucomvgm-0.0.5/mucomvgm/mucomvgm.exe'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def decode_source(data):
    if data.startswith(b'\xef\xbb\xbf'):
        return data.decode('utf-8-sig'), 'utf-8-sig'
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return data.decode('cp932'), 'cp932'


def remove_loop_markers(text):
    """Remove command L only inside channel or macro bodies, outside strings."""
    result, count, macro_depth = [], 0, 0
    for line in text.splitlines(keepends=True):
        match = re.match(r'^(\s*[A-K]+\s+|\s*#\s*\*\s*\d+\s*\{)', line)
        continuation = re.match(r'^\s+L(?:\s|$)', line)
        if not match and not macro_depth and not continuation:
            result.append(line)
            continue
        macro_start = bool(re.match(r'^\s*#\s*\*\s*\d+\s*\{', line))
        prefix, body = (line[:match.end()], line[match.end():]) if match else ('', line)
        if macro_start:
            macro_depth = 1  # Opening brace is already in the preserved prefix.
        quoted = False
        kept = []
        for index, char in enumerate(body):
            if char == '"':
                quoted = not quoted
            if char == ';' and not quoted:
                kept.append(body[index:])
                break
            if char == 'L' and not quoted:
                count += 1
            else:
                kept.append(char)
            if macro_start or macro_depth:
                if char == '{' and not quoted:
                    macro_depth += 1
                elif char == '}' and not quoted:
                    macro_depth = max(0, macro_depth-1)
        result.append(prefix + ''.join(kept))
    return ''.join(result), count


def tool_path(path, tool):
    if sys.platform != 'win32' and tool.suffix.lower() == '.exe':
        return subprocess.check_output(['wslpath', '-w', str(path.resolve())], text=True).strip()
    return str(path.resolve())


def adapt_raw_voices(text, muc):
    """Translate unsupported @% raw register voices through a temporary DAT.

    MUCOM88 raw voices contain the same 25 register bytes as DAT slot bytes
    1..25. No musical commands or source dependency files are rewritten.
    """
    lines = text.splitlines(keepends=True)
    blocks = []
    index = 0
    while index < len(lines):
        match = re.fullmatch(r'\s*@%\s*(\d+)\s*', lines[index].split(';', 1)[0].strip())
        if not match:
            index += 1
            continue
        voice_id = int(match.group(1))
        if not 0 <= voice_id <= 255:
            raise ValueError(f'Raw voice ID outside DAT range: {voice_id}')
        first = index
        index += 1
        values = []
        while len(values) < 25 and index < len(lines):
            body = lines[index].split(';', 1)[0].strip()
            if body and not re.fullmatch(r'[\s,$0-9A-Fa-f]+', body):
                raise ValueError(f'Invalid raw register voice @{voice_id}')
            values.extend(int(token[1:], 16) if token.startswith('$') else int(token)
                          for token in re.findall(r'\$[0-9A-Fa-f]+|\d+', body))
            index += 1
        if len(values) != 25 or any(value > 255 for value in values):
            raise ValueError(f'Raw voice @{voice_id} requires exactly 25 bytes')
        blocks.append((voice_id, bytes(values)))
        # These lines are not parsed by MUCOMVGM. Remove them only from its
        # input copy, after transferring every register byte into the DAT.
        for number in range(first, index):
            lines[number] = '\n'
    if not blocks:
        return text, None, {}
    directive = re.search(r'^\s*#voice\s+([^\r\n;]+)', text, flags=re.MULTILINE | re.IGNORECASE)
    data = bytearray((muc.parent / directive.group(1).strip()).read_bytes() if directive else b'')
    required = (max(voice_id for voice_id, _ in blocks) + 1) * 32
    if len(data) < required:
        data.extend(b'\0' * (required - len(data)))
    for voice_id, registers in blocks:
        data[voice_id * 32 + 1:voice_id * 32 + 26] = registers
    with tempfile.NamedTemporaryFile(prefix='.' + muc.stem + '.mucomvgm-', suffix='.dat',
                                     dir=muc.parent, delete=False) as stream:
        voice_copy = Path(stream.name)
        stream.write(data)
    adapted = ''.join(lines)
    if directive:
        adapted = re.sub(r'^(\s*#voice\s+)[^\r\n;]+', lambda match: match.group(1) + voice_copy.name,
                         adapted, count=1, flags=re.MULTILINE | re.IGNORECASE)
    else:
        adapted = '#voice ' + voice_copy.name + '\n' + adapted
    evidence = dict(raw_voice_adaptation='temporary_dat_slot_register_bytes_1_to_25',
                    raw_voice_ids=','.join(str(voice_id) for voice_id, _ in blocks),
                    raw_voice_register_sha256=sha256(b''.join(registers for _, registers in blocks)),
                    adapted_voice_dat_sha256=sha256(data),
                    compiler_voice_limit='MUCOMVGM DAT loader masks DR register AM bit7')
    return adapted, voice_copy, evidence


def save_manifest(folder, source, tool, compiler_hash, rows):
    manifest = dict(source=str(source), compiler=str(tool), compiler_sha256=compiler_hash,
                    successful=sum(row['status'] == 'generated' for row in rows),
                    failed=sum(row['status'] == 'failed' for row in rows), files=rows)
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (folder / 'manifest.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare_one(muc, source, tool, folder, timeout):
    relative = muc.relative_to(source)
    row = dict(source=relative.as_posix(), status='failed')
    input_copy = destination = voice_copy = None
    try:
        original = muc.read_bytes()
        row['input_before_sha256'] = sha256(original)
        backup = folder / 'source_backups' / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists() and backup.read_bytes() != original:
            backup = backup.with_name(backup.name + '.' + sha256(original)[:12])
        if not backup.exists():
            backup.write_bytes(original)
        text, encoding = decode_source(original)
        text, removed = remove_loop_markers(text)
        edited = text.encode(encoding)
        row.update(encoding=encoding, removed_L=removed, input_sha256=sha256(edited))
        if edited != original:
            muc.write_bytes(edited)
        compiler_text, voice_copy, voice_evidence = adapt_raw_voices(text, muc)
        row.update(voice_evidence, compiler_input_sha256=sha256(compiler_text.encode('utf-8')))
        # Keep dependency lookup in the original folder while adapting only the
        # transient compiler input encoding.
        with tempfile.NamedTemporaryFile(prefix='.' + muc.stem + '.mucomvgm-', suffix='.muc',
                                         dir=muc.parent, delete=False) as stream:
            input_copy = Path(stream.name)
            stream.write(compiler_text.encode('utf-8'))
        destination = input_copy.with_suffix('.vgm')
        log = folder / 'logs' / relative.with_suffix('.compile.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        options = dict(cwd=muc.parent, capture_output=True, timeout=timeout)
        if sys.platform == 'win32':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        command = [str(tool), tool_path(input_copy, tool), tool_path(destination, tool)]
        try:
            run = subprocess.run(command, **options)
            log.write_bytes(run.stdout + run.stderr)
        except subprocess.TimeoutExpired as error:
            log.write_bytes((error.stdout or b'') + (error.stderr or b'') + b'\nCompilation timed out\n')
            raise
        row.update(exit_code=run.returncode, log=str(log))
        if run.returncode or not destination.is_file():
            message = (run.stderr or run.stdout).decode('utf-8', errors='replace').strip().splitlines()
            raise RuntimeError(message[-1] if message else 'Compiler did not create a VGM')
        vgm = destination.read_bytes()
        if len(vgm) < 0x40 or vgm[:4] != b'Vgm ':
            raise ValueError('Compiler output is not a VGM file')
        output = muc.with_suffix('.vgm')
        if output.exists() and output.read_bytes() != vgm:
            old = output.read_bytes()
            saved = folder / 'vgm_backups' / relative.with_suffix('.vgm')
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.with_name(saved.name + '.' + sha256(old)[:12]).write_bytes(old)
        if not output.exists() or output.read_bytes() != vgm:
            destination.replace(output)
        row.update(status='generated', output=str(output), output_sha256=sha256(vgm),
                   output_bytes=len(vgm), duration_samples=int.from_bytes(vgm[0x18:0x1c], 'little'))
    except Exception as error:
        row['error'] = f'{type(error).__name__}: {error}'
    finally:
        for path in (input_copy, destination, voice_copy):
            if path is not None:
                path.unlink(missing_ok=True)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--mucomvgm', type=Path, default=DEFAULT_TOOL)
    parser.add_argument('--outdir', type=Path, default=ROOT / 'outputs/local_only/mucom88_fixture_preparation')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    source, tool, folder = args.source.resolve(), args.mucomvgm.resolve(), args.outdir.resolve()
    if not source.is_dir() or not tool.is_file():
        parser.error('Source directory and MUCOMVGM executable must exist')
    if folder.is_relative_to(source):
        parser.error('--outdir must be outside the source fixture directory')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in source.rglob('*') if path.is_file() and path.suffix.lower() == '.muc'
                   and not any(part.lower() in ('org', 'backup', 'backups') for part in path.relative_to(source).parts[:-1]))
    rows = []
    compiler_hash = sha256(tool.read_bytes())
    for muc in files:
        row = prepare_one(muc, source, tool, folder, args.timeout)
        rows.append(row)
        save_manifest(folder, source, tool, compiler_hash, rows)
        print(f"{len(rows)}/{len(files)} {row['status']}: {row['source']} {row.get('error', '')}", flush=True)
    print(f"Generated {sum(row['status'] == 'generated' for row in rows)}/{len(rows)}; manifest: {folder / 'manifest.json'}")
    return int(any(row['status'] == 'failed' for row in rows))


if __name__ == '__main__':
    raise SystemExit(main())
