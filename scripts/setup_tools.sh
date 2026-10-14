#!/usr/bin/env bash
# Install pinned external tools locally; never install globally or under outputs.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
tools="$root/.tools"
with_mdxtools=false
for option in "$@"; do
    case "$option" in
        --with-mdxtools) with_mdxtools=true ;;
        --help|-h)
            printf '%s\n' 'Usage: bash scripts/setup_tools.sh [--with-mdxtools]' \
                'Installs MXC v1.01, pinned run68x and the locked Rust helper.' \
                '--with-mdxtools also builds mdxinfo, pdxinfo, mdxdump and mdx2mml.' \
                'Requires Python 3, curl, git, CMake, make, a C compiler and Rust/Cargo.' \
                'A fresh MXC installation also requires lha or lhasa (LH1 support).'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$option" >&2; exit 2 ;;
    esac
done

for required in python3 curl git cmake make cc sha256sum; do
    if ! command -v "$required" >/dev/null; then
        printf 'Missing prerequisite: %s. See docs/mdx_compiler_setup.md\n' "$required" >&2
        exit 1
    fi
done
cargo=$(command -v cargo || true)
if [[ -z "$cargo" && -x "$HOME/.cargo/bin/cargo" ]]; then
    cargo="$HOME/.cargo/bin/cargo"
fi
if [[ -z "$cargo" ]]; then
    printf 'Missing Rust/Cargo. See docs/mdx_compiler_setup.md\n' >&2
    exit 1
fi

checksum() {
    [[ -f "$2" ]] && printf '%s  %s\n' "$1" "$2" | sha256sum -c - >/dev/null 2>&1
}

checkout() {
    local url=$1 directory=$2 revision=$3
    if [[ ! -e "$directory" ]]; then
        git clone "$url" "$directory"
    fi
    if [[ $(git -C "$directory" remote get-url origin) != "$url" ]]; then
        printf 'Unexpected repository at %s; refusing to change it.\n' "$directory" >&2
        exit 1
    fi
    if ! git -C "$directory" diff --quiet || ! git -C "$directory" diff --cached --quiet; then
        printf 'Local source changes at %s; preserve them before setup.\n' "$directory" >&2
        exit 1
    fi
    if ! git -C "$directory" cat-file -e "$revision^{commit}" 2>/dev/null; then
        git -C "$directory" fetch origin "$revision"
    fi
    git -C "$directory" checkout --detach "$revision"
    [[ $(git -C "$directory" rev-parse HEAD) == "$revision" ]]
}

mxc_hash=38def25dae39ae35a16668f086843ae9f0d00295e50fd034b7b4108f7c762086
archive_hash=4d4a6f00a100c728f5f4b217998e66ca8e6fa530cdfe1a5ee7c03f70715d98c4
mkdir -p "$tools/mxc"
if ! checksum "$mxc_hash" "$tools/mxc/mxc.x"; then
    if [[ -e "$tools/mxc/mxc.x" ]]; then
        printf 'Existing MXC hash differs; refusing to overwrite %s\n' "$tools/mxc/mxc.x" >&2
        exit 1
    fi
    decoder=$(command -v lha || command -v lhasa || true)
    if [[ -z "$decoder" ]]; then
        printf 'Missing LH1 decoder. On Ubuntu: sudo apt-get install lhasa\n' >&2
        exit 1
    fi
    archive="$tools/mxc/MDX_TOOL.lzh"
    if ! checksum "$archive_hash" "$archive"; then
        curl --fail --location --retry 3 \
            https://nfggames.com/x68000/Mirrors/x68pub/x68tools/SOUND/MXDRV/MDX_TOOL.lzh \
            --output "$archive.download"
        if ! checksum "$archive_hash" "$archive.download"; then
            printf 'MXC archive checksum mismatch; download retained for inspection.\n' >&2
            exit 1
        fi
        mv -- "$archive.download" "$archive"
    fi
    (cd "$tools/mxc" && "$decoder" x MDX_TOOL.lzh mxc.x mxc.doc)
fi
checksum "$mxc_hash" "$tools/mxc/mxc.x"

checkout https://github.com/kg68k/run68x.git "$tools/run68x" \
    fc28826dd795b1f0a3241f0b03fe2353e12e7233
cmake -S "$tools/run68x" -B "$tools/run68x/build" \
    -DCMAKE_C_FLAGS=-Wno-error=format-truncation
cmake --build "$tools/run68x/build" -j2
"$cargo" build --release --locked --manifest-path "$root/scripts/mdx_fixture_generator/Cargo.toml"

if "$with_mdxtools"; then
    checkout https://github.com/vampirefrog/mdxtools.git "$tools/mdxtools" \
        9c8539fec2757fcf7c85d1986171b50ebe2ef1e5
    git -C "$tools/mdxtools" submodule foreach --recursive \
        'git diff --quiet && git diff --cached --quiet'
    git -C "$tools/mdxtools" submodule update --init --recursive
    make -C "$tools/mdxtools" mdxinfo pdxinfo mdxdump mdx2mml \
        CC=cc CFLAGS='-O2 -Wall -D_GNU_SOURCE -Ix68ksjis' LIBS=
fi

python3 - "$tools" "$root" "$with_mdxtools" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import subprocess

tools, root = map(Path, sys.argv[1:3])
binaries = {
    'mxc': tools / 'mxc/mxc.x',
    'run68': tools / 'run68x/build/run68',
    'mdx-fixture-generator': root / 'scripts/mdx_fixture_generator/target/release/mdx-fixture-generator',
}
if sys.argv[3] == 'true':
    binaries.update((name, tools / 'mdxtools' / name)
                    for name in ('mdxinfo', 'pdxinfo', 'mdxdump', 'mdx2mml'))
for name, path in binaries.items():
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError(f'Missing installed binary: {path}')
report = {
    'run68x_revision': subprocess.check_output(
        ['git', '-C', str(tools / 'run68x'), 'rev-parse', 'HEAD'], text=True).strip(),
    'cargo_lock_sha256': hashlib.sha256(
        (root / 'scripts/mdx_fixture_generator/Cargo.lock').read_bytes()).hexdigest(),
    'binaries': {name: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in binaries.items()},
}
if sys.argv[3] == 'true':
    report['mdxtools_revision'] = subprocess.check_output(
        ['git', '-C', str(tools / 'mdxtools'), 'rev-parse', 'HEAD'], text=True).strip()
(tools / 'setup_manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print('Setup complete. Installed tools: ' + ', '.join(binaries))
print('Manifest: ' + str(tools / 'setup_manifest.json'))
PY
