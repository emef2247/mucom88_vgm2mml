"""Run a fresh MUCOMSX DOS job with an explicitly supplied local boot image.

ROM/DOS paths belong to a local profile, never to the repository or defaults.
Only allowlisted generated files are read from FAT12 and exported. Compilation
success and VGM structural validity are distinct from acoustic equivalence.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from prepare_mucomsx import stage_score
from setup_mucomsx import DESTINATION, ROOT
sys.path.insert(0, str(ROOT / 'py'))
from vgm_timing import command_times

GENERATED = ('CHECK.MUB', 'SESSION.MUB', 'CHECK.VGM', 'DONE.OK', 'FAILED.ERR')
EXPECTED = ('CHECK.MUB', 'SESSION.MUB', 'CHECK.VGM')
MUB_SIGNATURES = (b'MUB8', b'muPb0100', b'msPb0100', b'msPb0101')


def fat12_files(image, names=GENERATED):
    """Read only named regular root files, rejecting malformed FAT12 chains."""
    data = Path(image).read_bytes() if isinstance(image, (str, Path)) else bytes(image)
    if len(data) < 36:
        raise ValueError('Truncated FAT12 boot sector')
    sector = int.from_bytes(data[11:13], 'little')
    spc, fats = data[13], data[16]
    reserved = int.from_bytes(data[14:16], 'little')
    count = int.from_bytes(data[17:19], 'little')
    total = int.from_bytes(data[19:21], 'little') or int.from_bytes(data[32:36], 'little')
    spf = int.from_bytes(data[22:24], 'little')
    if (sector not in (128, 256, 512, 1024, 2048, 4096) or not spc
            or spc & (spc - 1) or not reserved or not fats or not count or not spf or not total):
        raise ValueError('Invalid FAT12 geometry')
    root_sector = reserved + fats * spf
    root_sectors = (count * 32 + sector - 1) // sector
    data_sector = root_sector + root_sectors
    clusters = (total - data_sector) // spc
    if not 0 < clusters < 4085 or total * sector > len(data):
        raise ValueError('Truncated image or unsupported FAT12 cluster count')
    fat = data[reserved * sector:(reserved + spf) * sector]
    root = data[root_sector * sector:(root_sector + root_sectors) * sector]
    if (clusters + 2) * 3 // 2 > len(fat):
        raise ValueError('Truncated FAT12 allocation table')
    wanted, files = {str(name).upper() for name in names}, {}
    cluster_size = spc * sector
    for index in range(count):
        entry = root[index * 32:(index + 1) * 32]
        if not entry or entry[0] == 0:
            break
        if entry[0] == 0xe5 or entry[11] & 0x18 or entry[11] == 0x0f:
            continue
        try:
            base, ext = entry[:8].decode('ascii').rstrip(), entry[8:11].decode('ascii').rstrip()
        except UnicodeDecodeError:
            continue
        name = (base + ('.' + ext if ext else '')).upper()
        if name not in wanted:
            continue
        if name in files:
            raise ValueError('Duplicate allowlisted FAT12 filename: ' + name)
        size = int.from_bytes(entry[28:32], 'little')
        cluster = int.from_bytes(entry[26:28], 'little')
        if size > clusters * cluster_size:
            raise ValueError('FAT12 file size exceeds image capacity: ' + name)
        content, seen = bytearray(), set()
        while len(content) < size:
            if cluster < 2 or cluster >= clusters + 2 or cluster >= 0xff0 or cluster in seen:
                raise ValueError('Invalid, cyclic or truncated FAT12 chain: ' + name)
            seen.add(cluster)
            start = (data_sector + (cluster - 2) * spc) * sector
            content.extend(data[start:start + min(cluster_size, size - len(content))])
            offset = cluster + cluster // 2
            if offset + 2 > len(fat):
                raise ValueError('FAT12 chain points outside allocation table')
            word = int.from_bytes(fat[offset:offset + 2], 'little')
            cluster = word >> 4 if cluster & 1 else word & 0xfff
        files[name] = bytes(content)
    return files


def validate_artifacts(files, *, compile_only=False):
    """Check MUB signatures and optional finalized VGM structure, apart from DOS markers."""
    expected = ('CHECK.MUB',) if compile_only else EXPECTED
    for name in expected:
        if not files.get(name):
            raise ValueError('Missing or empty generated artifact: ' + name)
    for name in expected if compile_only else EXPECTED[:2]:
        if not any(files[name].startswith(signature) for signature in MUB_SIGNATURES):
            raise ValueError('Unrecognized MUCOM container signature: ' + name)
    if compile_only:
        return dict(mub_signatures={'CHECK.MUB': files['CHECK.MUB'][:8].decode('ascii', errors='replace')})
    raw = files['CHECK.VGM']
    if len(raw) < 0x40 or raw[:4] != b'Vgm ':
        raise ValueError('Generated VGM header is missing or not finalized')
    if int.from_bytes(raw[4:8], 'little') + 4 != len(raw):
        raise ValueError('Generated VGM EOF offset does not match finalized file size')
    events = tuple(command_times(raw))
    if not events or events[-1].command != 0x66:
        raise ValueError('Generated VGM has no decoded end command')
    end = events[-1].vgmticks
    declared = int.from_bytes(raw[0x18:0x1c], 'little')
    if end <= 0 or declared != end:
        raise ValueError('Generated VGM duration is empty or header/stream timestamps disagree')
    return dict(vgm_samples=end, vgm_commands=len(events),
                mub_signatures={name:files[name][:8].decode('ascii', errors='replace') for name in EXPECTED[:2]})


def _wait_for_artifacts(proc, image, timeout, *, settle_seconds=2, poll_seconds=1, compile_only=False):
    start = time.monotonic()
    stable_since, previous_hashes, last_error = None, None, 'DOS job has not completed'
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError('openMSX exited before generated artifacts were finalized')
        try:
            files = fat12_files(image)
            if 'FAILED.ERR' in files:
                raise RuntimeError('MUCOMSX DOS job reported FAILED.ERR')
            if 'DONE.OK' not in files:
                raise ValueError('MUCOMSX DOS completion marker is absent')
            validation = validate_artifacts(files, compile_only=compile_only)
            expected = ('CHECK.MUB',) if compile_only else EXPECTED
            hashes = tuple(hashlib.sha256(files[name]).hexdigest() for name in expected)
            if hashes != previous_hashes:
                previous_hashes, stable_since = hashes, time.monotonic()
            elif time.monotonic() - stable_since >= settle_seconds:
                return files, validation
        except ValueError as error:
            last_error = str(error)
            stable_since, previous_hashes = None, None
        time.sleep(min(poll_seconds, max(0, timeout - (time.monotonic() - start))))
    raise TimeoutError(f'MUCOMSX runtime timed out after {timeout} seconds: {last_error}')


def _tcl_word(value):
    value = str(value)
    if '\x00' in value:
        raise ValueError('NUL in openMSX configuration')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('[', '\\[').replace('\n', '\\n').replace('\r', '\\r') + '"'


def _stop(proc):
    if proc is None:
        return None
    if proc.poll() is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        return proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(timeout=10)


def run_job(muc, outdir, *, boot_disk, openmsx='openmsx', user_data=None,
            rom_path=None, machine='Panasonic_FS-A1GT', extension=None,
            timeout=180, tools=DESTINATION, compile_only=False):
    """Stage, compile and export in an isolated copy of a supplied DOS disk.

    The output directory must be new/empty. Input DOS images and source MUC/
    dependencies are never modified. Returns a persisted report on success or
    failure; callers must check status == 'passed', not merely file existence.
    """
    muc, outdir, boot_disk = Path(muc).resolve(), Path(outdir).resolve(), Path(boot_disk).resolve()
    if outdir.exists() and (not outdir.is_dir() or any(outdir.iterdir())):
        raise ValueError('Use a new or empty runtime output directory')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Runtime timeout must be finite and positive')
    if not boot_disk.is_file():
        raise ValueError('A local DOS2/Nextor boot disk is required')
    if muc.is_relative_to(outdir) or boot_disk.is_relative_to(outdir):
        raise ValueError('Runtime output must not contain source MUC or boot disk')
    outdir.mkdir(parents=True, exist_ok=True)
    report = dict(status='not_run', source=str(muc), files={}, validation_scope=(
                  'MUCOMSX MUB compilation; no playback or VGM validation' if compile_only else
                  'MUCOMSX compilation and exported VGM structure; no acoustic equivalence claim'))
    proc, started, stage = None, time.monotonic(), 'staging'
    try:
        stage_options = dict(compile_only=True) if compile_only else {}
        report['staging'] = stage_score(muc, outdir / 'job', tools, **stage_options)
        flat = outdir / 'flat'
        flat.mkdir()
        for source in (outdir / 'job').iterdir():
            if source.is_file() and re.fullmatch(r'[A-Za-z0-9_-]{1,8}\.[A-Za-z0-9_-]{1,3}', source.name):
                shutil.copyfile(source, flat / source.name.upper())
        autoexec = b'PATH A:\\\r\nRUN.BTM\r\n'
        (flat / 'AUTOEXEC.BAT').write_bytes(autoexec)
        image = outdir / 'work.dsk'
        shutil.copyfile(boot_disk, image)
        report['boot_disk_sha256'] = hashlib.sha256(boot_disk.read_bytes()).hexdigest()
        env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
        if user_data is not None:
            env['OPENMSX_USER_DATA'] = str(Path(user_data).resolve())
        else:
            isolated = outdir / 'openmsx_user'
            isolated.mkdir()
            env['OPENMSX_USER_DATA'] = str(isolated)
        init = ''
        if rom_path is not None:
            init += 'filepool add -path ' + _tcl_word(Path(rom_path).resolve()) + ' -types {system_rom}\n'
        init += 'machine ' + _tcl_word(machine) + '\n'
        if extension:
            init += 'ext ' + _tcl_word(extension) + '\n'
        init += 'diska ' + _tcl_word(image) + '\nset renderer none\n'
        prepare = init
        for name in (*GENERATED, 'AUTOEXEC.BAT', 'SOURCE.MUC', 'RUN.BAT', 'RUN.BTM'):
            prepare += 'catch {diskmanipulator delete diska ' + name + '}\n'
        prepare += 'diskmanipulator import diska ' + _tcl_word(flat) + '\nexit\n'
        (outdir / 'prepare.tcl').write_text(prepare, encoding='utf-8')
        command = [str(openmsx), '-machine', 'C-BIOS_MSX2', '-script']
        stage = 'disk_preparation'
        prepared = subprocess.run(command + [str(outdir / 'prepare.tcl')], env=env,
                                  capture_output=True, timeout=min(30, timeout))
        report['prepare_exit'] = prepared.returncode
        (outdir / 'prepare.log').write_bytes(prepared.stdout + prepared.stderr)
        if prepared.returncode:
            raise RuntimeError('openMSX disk preparation failed')
        staged_names = tuple(path.name for path in flat.iterdir())
        disk_files = fat12_files(image, staged_names)
        if any(disk_files.get(name) != (flat / name).read_bytes() for name in staged_names):
            raise RuntimeError('Prepared DOS image does not contain the exact staged job')
        if any(name in fat12_files(image) for name in GENERATED):
            raise RuntimeError('Prepared image still contains stale generated artifacts')
        boot = init + 'set throttle off\nset mute on\nset power on\n'
        (outdir / 'boot.tcl').write_text(boot, encoding='utf-8')
        stage = 'dos_execution'
        with (outdir / 'runtime.log').open('wb') as log:
            proc = subprocess.Popen(command + [str(outdir / 'boot.tcl')], env=env, stdout=log, stderr=log)
            generated, validation = _wait_for_artifacts(proc, image, timeout, **stage_options)
            report['runtime_exit'] = _stop(proc)
            proc = None
        # After closing the emulator, validate the final persisted disk again.
        generated = fat12_files(image)
        if 'FAILED.ERR' in generated or 'DONE.OK' not in generated:
            raise RuntimeError('Final DOS result markers do not indicate success')
        validation = validate_artifacts(generated, compile_only=compile_only)
        artifacts = outdir / 'artifacts'
        artifacts.mkdir()
        for name, data in generated.items():
            (artifacts / name).write_bytes(data)
            report['files'][name] = dict(path=str(artifacts / name), bytes=len(data),
                                       sha256=hashlib.sha256(data).hexdigest())
        report.update(status='passed', validation=validation,
                      termination='intentional after finalized artifacts remained stable for two seconds')
    except Exception as error:
        report.update(status='failed', failed_stage=stage, detail=str(error))
        if proc is not None:
            report['runtime_exit'] = _stop(proc)
            proc = None
        # Preserve only named generated failure evidence, never DOS system files.
        try:
            generated = fat12_files(outdir / 'work.dsk')
            artifacts = outdir / 'artifacts'
            artifacts.mkdir(exist_ok=True)
            for name, data in generated.items():
                (artifacts / name).write_bytes(data)
                report['files'][name] = dict(path=str(artifacts / name), bytes=len(data),
                                            sha256=hashlib.sha256(data).hexdigest())
        except (OSError, ValueError):
            pass
    finally:
        report['wall_seconds'] = time.monotonic() - started
        (outdir / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


PROFILE_KEYS = {'boot_disk', 'openmsx', 'user_data', 'rom_path', 'machine', 'extension', 'timeout', 'tools'}


def load_profile(path):
    options = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(options, dict) or set(options) - PROFILE_KEYS:
        raise ValueError('Runtime profile must be an object containing only supported runtime settings')
    return options


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('muc', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--profile', type=Path, help='Local JSON runtime settings; keep private paths out of git')
    parser.add_argument('--boot-disk', type=Path)
    parser.add_argument('--openmsx')
    parser.add_argument('--user-data', type=Path)
    parser.add_argument('--rom-path', type=Path)
    parser.add_argument('--machine')
    parser.add_argument('--extension')
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--tools', type=Path)
    parser.add_argument('--compile-only', action='store_true', help='Compile MUB without playback or VGM export')
    args = parser.parse_args(argv)
    try:
        options = load_profile(args.profile) if args.profile else {}
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for key in PROFILE_KEYS:
        value = getattr(args, key)
        if value is not None:
            options[key] = value
    if not options.get('boot_disk'):
        parser.error('Supply --boot-disk or boot_disk in a local --profile')
    try:
        report = run_job(args.muc, args.outdir, compile_only=args.compile_only, **options)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
