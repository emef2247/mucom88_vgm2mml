"""Install the pinned external MUCOMSX MSX-DOS toolchain, without ROM/DOS files."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = 'v1.0-stable'
ARCHIVE = 'MucoMSX_261016.zip'
SHA256 = '582a72344d30982f79b61135f82746813c8b739ad7a4c00c7cb5530d3f4a42d6'
URL = f'https://github.com/ToughkidDev/MUCOMSX/releases/download/{RELEASE}/{ARCHIVE}'
DESTINATION = ROOT / '.tools' / 'mucomsx-v1.0-stable'
PROGRAMS = ('MUC2MUB.COM', 'MUCPLAY.COM', 'MUBPLAY.COM', 'MUCEDIT.COM', 'MUCVOICE.COM')
PROFILE = ROOT / 'docs' / 'mucomsx_profile.json'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def archive_files(data, expected_sha256=SHA256):
    if sha256(data) != expected_sha256:
        raise ValueError('MUCOMSX archive SHA-256 mismatch; existing installation preserved')
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 10 * 1024 * 1024:
            raise ValueError('MUCOMSX archive expands beyond the allowed runtime size')
        for entry in archive.infolist():
            name = entry.filename
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or '..' in path.parts or
                    any(c in name for c in ('\\', ':', '\x00')) or
                    stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('Unsafe MUCOMSX archive path: ' + repr(name))
            if entry.is_dir():
                continue
            if name in result:
                raise ValueError('Duplicate MUCOMSX archive path: ' + name)
            result[name] = archive.read(entry)
    required = set(PROGRAMS) | {'NOTICE.txt', 'manifest.json', 'SHA256SUMS.txt',
                              'LICENSES/MUCOM88-CC-BY-NC-SA-4.0.txt'}
    if not required.issubset(result):
        raise ValueError('Incomplete MUCOMSX runtime archive')
    manifest = json.loads(result['manifest.json'])
    if manifest.get('package_kind') != 'runtime':
        raise ValueError('Expected MUCOMSX runtime package')
    for name, details in manifest['files'].items():
        if (name not in result or len(result[name]) != details['bytes'] or
                sha256(result[name]) != details['sha256']):
            raise ValueError('MUCOMSX manifest mismatch: ' + name)
    checksums = {}
    for line in result['SHA256SUMS.txt'].decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] in checksums:
            raise ValueError('Malformed MUCOMSX checksum list')
        checksums[match[2]] = match[1]
    if set(checksums) != set(result) - {'SHA256SUMS.txt'}:
        raise ValueError('MUCOMSX checksum list does not cover archive contents')
    for name, digest in checksums.items():
        if sha256(result[name]) != digest:
            raise ValueError('MUCOMSX internal checksum mismatch: ' + name)
    return result


def install(data, destination=DESTINATION, expected_sha256=SHA256):
    files = archive_files(data, expected_sha256)
    destination = Path(destination).resolve()
    metadata = dict(repository='https://github.com/ToughkidDev/MUCOMSX',
                    release=RELEASE, archive=ARCHIVE, url=URL, sha256=expected_sha256,
                    files={name: sha256(content) for name, content in files.items()})
    files['source.json'] = (json.dumps(metadata, indent=2) + '\n').encode('utf-8')
    # Preflight every destination before writing any; edited factory DATs are assets.
    for name, content in files.items():
        target = destination / name
        if not target.resolve().is_relative_to(destination) or target.is_symlink():
            raise ValueError('MUCOMSX install path escapes destination: ' + name)
        if target.exists() and (not target.is_file() or target.read_bytes() != content):
            raise ValueError('Refusing to overwrite modified MUCOMSX file: ' + str(target))
    for name, content in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
    return metadata


def verify_installation(destination=DESTINATION):
    destination = Path(destination)
    metadata = json.loads((destination / 'source.json').read_text(encoding='utf-8'))
    if metadata.get('sha256') != SHA256 or metadata.get('release') != RELEASE:
        raise ValueError('MUCOMSX installation uses a different release identity')
    # Reuse pinned archive hashes, not only mutable installed source.json metadata.
    cache = ROOT / '.tools' / 'downloads' / ARCHIVE
    if not cache.is_file():
        raise ValueError('Pinned archive needed for independent installed-file verification: ' + str(cache))
    expected = archive_files(cache.read_bytes())
    for name, content in expected.items():
        path = destination / name
        if (path.is_symlink() or not path.resolve().is_relative_to(destination.resolve()) or
                not path.is_file() or path.read_bytes() != content):
            raise ValueError('MUCOMSX installed file differs: ' + name)
    return metadata


def environment_report(destination=DESTINATION, dos_system_dir=None):
    dos = Path(dos_system_dir).resolve() if dos_system_dir else None
    found = {p.name.upper(): p for p in dos.iterdir() if p.is_file()} if dos and dos.is_dir() else {}
    present = {name for name, path in found.items() if path.stat().st_size}
    kernel = next((name for name in ('NEXTOR.SYS', 'MSXDOS2.SYS') if name in present), None)
    missing = ([] if 'COMMAND2.COM' in present else ['COMMAND2.COM']) + ([] if kernel else ['MSXDOS2.SYS or NEXTOR.SYS'])
    return dict(tool_directory=str(Path(destination).resolve()),
                toolchain_verified=True, host_executable=False,
                openmsx=shutil.which('openmsx'), dos_system_directory=str(dos) if dos else None,
                dos_kernel=kernel, missing_dos_files=missing, runtime_executed=False,
                status='runtime_not_tested' if not missing else 'dos_runtime_missing',
                note='COM programs require MSX-DOS2/Nextor and >=512KiB mapper; openMSX alone is insufficient')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use an already downloaded official archive')
    parser.add_argument('--destination', type=Path, default=DESTINATION)
    parser.add_argument('--dos-system-dir', type=Path, help='Existing licensed DOS2 system files; not downloaded')
    parser.add_argument('--check', action='store_true', help='Verify installed files; no download')
    args = parser.parse_args(argv)
    if not args.check:
        cache = ROOT / '.tools' / 'downloads' / ARCHIVE
        if args.archive:
            data = args.archive.read_bytes()
        elif cache.is_file():
            data = cache.read_bytes()
        else:
            request = urllib.request.Request(URL, headers={'User-Agent': 'mucom88-vgm2mml-tool-setup'})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(2 * 1024 * 1024 + 1)
        if cache.exists() and cache.read_bytes() != data:
            raise ValueError('Refusing to replace a different cached archive')
        install(data, args.destination)
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            cache.write_bytes(data)
        elif cache.read_bytes() != data:
            raise ValueError('Refusing to replace a different cached archive')
    verify_installation(args.destination)
    report = environment_report(args.destination, args.dos_system_dir)
    (args.destination / 'environment.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Verified MUCOMSX toolchain: ' + str(args.destination))
    print('Environment: ' + report['status'] + '; DOS execution not performed by setup')
    print('Bundled component licenses: ' + str(args.destination / 'NOTICE.txt'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
