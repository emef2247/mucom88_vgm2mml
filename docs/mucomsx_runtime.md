# Local MUCOMSX compile/export verification

`scripts/mucomsx_runtime.py` stages a fresh DOS job with the pinned MUCOMSX
tools, copies an explicitly supplied local DOS2/Nextor disk, and runs openMSX
without a display or audio output. It never modifies the source MUC, its banks,
or the supplied boot image. ROM and DOS assets are not distributed by this
project. The output directory must be new or empty.

A local, ignored JSON profile avoids embedding private paths in scripts:

```json
{
  "boot_disk": "/path/to/local/dos2-or-nextor.dsk",
  "openmsx": "openmsx",
  "user_data": "/path/to/local/openmsx/share",
  "rom_path": "/path/to/local/systemroms",
  "machine": "Panasonic_FS-A1GT",
  "extension": "LOCAL_NEXTOR_EXTENSION",
  "timeout": 180
}
```

`user_data`, `rom_path` and `extension` are optional settings for an existing
local emulator environment. The DOS command interpreter must support BTM
control flow; native checks use COMMAND2 version 2.44. A DOS2/Nextor kernel
alone does not establish command-interpreter compatibility. With no `user_data`, an isolated directory is
created inside the runtime job. The selected machine needs adequate mapper RAM
and a working DOS2/Nextor startup environment. The example names do not install
or supply those assets.

```sh
python scripts/mucomsx_runtime.py outputs/example/song.muc   --outdir outputs/example/native-check   --profile .tools/mucomsx-runtime.json
```

Individual options such as `--boot-disk`, `--openmsx`, `--rom-path` and
`--timeout` override profile settings. The Python API is
`run_job(muc, outdir, *, boot_disk, openmsx='openmsx', user_data=None,
rom_path=None, machine='Panasonic_FS-A1GT', extension=None, timeout=180,
tools=DESTINATION)`.

The staged source is `SOURCE.MUC`. `RUN.BTM` compiles `CHECK.MUB`, loads and
compiles the resident session, exports `SESSION.MUB` and `CHECK.VGM`, and writes
`DONE.OK` or `FAILED.ERR`. Existing generated names are removed only from the
working image before staging. Every staged root file is checked against its
expected bytes before execution.

A completion marker alone is insufficient: the VGM exporter may still be
finalizing its header. Success requires nonempty generated containers with
recognized signatures, VGM magic, a finalized EOF matching file size, a decoded
end command and a positive sample total matching the header. Generated file
hashes must then remain unchanged for at least two seconds. The emulator is
terminated intentionally only after that proof, and the persisted image is
checked again. An early emulator exit, DOS failure marker, malformed output or
timeout remains a failed verification.

Only `CHECK.MUB`, `SESSION.MUB`, `CHECK.VGM`, `DONE.OK` and `FAILED.ERR` are
extracted from the FAT12 root. The reader rejects malformed geometry, cyclic or
truncated chains and duplicate generated names. DOS system files and unrelated
boot-disk contents are not exported or listed. Keep the whole working image
private: it is still a copy of the user's DOS disk.

Inspect `validation.json`, `prepare.log`, `runtime.log` and the allowlisted
`artifacts/`. `status: passed` establishes native compilation and structural VGM
validity, not acoustic equivalence. Compare the resulting chip trajectories
separately for round-trip validation. Authored FAT12 and mocked-process tests
run without ROMs, DOS images, or an emulator session.

## Batch MUB compilation

Select the MUCOMSX target explicitly; the default batch target remains `mucom`.
The existing local profile is used by default. It must refer to accessible DOS
and emulator assets in the environment where Python runs (WSL paths for WSL).

```sh
python scripts/export_mucom.py INPUT_DIRECTORY --target mucomsx \
  --outdir outputs/mucomsx_set --force
```

Use `--runtime-profile PATH` for a different profile. The batch `--timeout`
controls each conversion and native compilation stage, overriding the profile's
runtime timeout. Use `--muc-only` to skip native compilation; no runtime profile
is required in that mode.

Each successful song publishes its MUC, dependencies and MUB under `tracks/`.
`results.csv` and per-song reports distinguish conversion and compilation
failures. Compilation logs and isolated DOS job evidence remain under each
song's new `_diagnostics/<song>/run_*/mucomsx/` directory. Failed or incomplete
MUBs are not published; the generated MUC and failure evidence remain available.
Other songs continue after a failure.

Batch MUB generation uses `MUC2MUB` only: it does not play music or export a VGM.
Completion requires `DONE.OK`, no failure marker, a recognized nonempty MUB
container, stable file hashes and final verification after emulator termination.
This establishes compilation, not playback or round-trip equivalence. The
standalone runtime's existing full verification remains the default; pass
`--compile-only` there to request the same MUB-only mode.

The runtime invokes `RUN.BTM` directly from `AUTOEXEC.BAT`. COMMAND2 2.44
supports batch control-flow commands such as `GOTO` in BTM files; a BAT job or
an unsupported `CALL` launcher can leave the DOS prompt idle or execute failure
handling after successful compilation. The working copy removes both old
`RUN.BAT` and current `RUN.BTM` names before staging.
