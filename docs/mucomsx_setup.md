# MUCOMSX tool profile and SCC enhancement transfer

## Scope and status

MUCOMSX is a separate compiler/player profile, not a renamed mucomDotNET host
executable. This milestone installs the official versioned tools and stages
inspectable DOS jobs. The existing converter and `export_mucom.py` default
remain unchanged. No `--target mucomsx` or automatic MUCOMSX exporter is claimed.
SCC enhancement from the suspended sibling project is not connected yet.

The intended route is:

```text
VGM -> native State / Segments / PCM IR -> musical units and structure
    -> explicit MUCOMSX projection -> editable MUC / required owned assets
    -> MUCOMSX compiler/session -> MUB and/or VGM -> source analysis comparison
```

Direct Segment-to-VGM projection can be an auxiliary oracle. It must not replace
MUC -> compiler/player -> VGM when validating the actual MUC renderer.

## Installation and staging

```sh
python scripts/setup_mucomsx.py
python scripts/setup_mucomsx.py --check
python scripts/prepare_mucomsx.py --pattern basic --outdir outputs/mucomsx_jobs/basic
```

The installer pins release `v1.0-stable`, runtime archive `MucoMSX_261016.zip`,
SHA-256 `582a72344d30982f79b61135f82746813c8b739ad7a4c00c7cb5530d3f4a42d6`.
It checks the archive, member checksums and manifest before installing to ignored
`.tools/mucomsx-v1.0-stable`. All five COM tools, factory data and component
notices stay together. Edited installed files are preserved by refusing updates.
The public repository contains no COM executables, ROMs, DOS system files,
private disk images or third-party factory voice bank.

The programs require MSX-DOS2 or compatible Nextor and at least 512KiB mapper
RAM. openMSX is an optional execution environment, not the DOS operating system.
Use your own firmware and boot media. `--dos-system-dir DIRECTORY` on setup
checks COMMAND2.COM and either MSXDOS2.SYS or NEXTOR.SYS; it does not boot DOS.
A host setup success is not a compiler/playback success.

`prepare_mucomsx.py --pattern basic|scc3|fm3|msx-psg` stages original short tooling
patterns. `--muc FILE` instead stages an existing MUC; it does not convert VGM
or silently make incompatible MUC syntax valid. Use a fresh/empty job folder.
The helper binds local explicit voice/PCM dependencies to unique DOS 8.3 names,
preserves their bytes, normalizes a separate score copy to CRLF and checks the
65,536-byte source and 1,023-byte line limits. Original source/assets stay intact.
Source-owned implicit VOICE.DAT/MUCOMPCM.BIN companions are also copied. Needed
missing implicit banks are rejected; factory voices are never substituted for
arbitrary songs. Inline voice coverage is not analyzed by this staging helper.

Copy a staged job to the DOS working disk, release any previous MUCOMSX session,
change to the job directory and run `RUN.BTM`. Its separate steps are:

```text
MUC2MUB SOURCE.MUC CHECK.MUB
MUCPLAY /LOAD SOURCE.MUC
MUCPLAY /COMPILE
MUCPLAY /MUB SESSION.MUB
MUCPLAY /VGM CHECK.VGM /L1
MUCPLAY /RELEASE
```

`/LOAD` is required after changing disk source/assets: `/COMPILE` reuses loaded
state. `/L1` requests one playthrough, not one second. Offline VGM export needs
DOS/mapper resources but no physical MAKOTO playback hardware. Fresh staging
avoids old generated artifacts. RUN.BTM clears its own prior markers and three
named result files on rerun; it checks release and expected output existence
before writing DONE.OK. Preserve wanted results before rerunning.

Inspect job.json, DOS diagnostics, CHECK.MUB, SESSION.MUB and CHECK.VGM. Compare
complete MUB bytes from the two paths using identical source/assets; file
presence, exit status and byte identity are distinct from musical correctness.
The setup and staging scripts never label a job as executed.

## Public tooling verification (2026-10-19)

Eight authored setup/staging safety checks passed. The exact authored SSG bytes
retained as `tests/fixtures/public/mucomsx/BASIC.MUC` were mounted as TEST.MUC
on a separate copy of a user-provided Nextor disk, with existing licensed BIOS
files and a headless Panasonic FS-A1GT / 512KiB mapper configuration. The original
boot image hash remained unchanged; no private song was executed.

MUC2MUB and the MUCPLAY session each produced a 302-byte MUB8; complete bytes
matched. Session export produced a 1,145-byte VGM, version 1.51, 220,928 samples,
with 200 OPNA writes. The actual exported header declared **8,000,000Hz OPNA**.
This is an observed default export profile, not a measurement of physical
hardware clock. Do not inherit the converter/probe's existing 7,987,200Hz pitch
or timer assumptions without explicit MUCOMSX-profile validation.

Only the ordinary SSG pattern was executed. SCC3, FM3 and MSX-PSG patterns are
prepared syntax/tooling probes, not executed support claims. No source VGM
conversion, sound comparison or listening regression is claimed by this check.
The small public summary is `tests/fixtures/public/mucomsx/tooling_validation.json`;
full generated evidence remains under ignored `outputs/mucomsx_smoke`.

## Confirmed capabilities and limits

The machine-readable inventory is [mucomsx_profile.json](mucomsx_profile.json).
It describes the pinned tool profile, not completed converter support.

| Topic | Official profile / remaining constraint |
| --- | --- |
| MUC input | 65,536 bytes including tags/comments/newlines |
| Editor | 60,000 bytes after normalization |
| Compiled music DATA | 122,880 bytes including structures/voices |
| Single page stream | OPNA 59,546 bytes; native MSX 59,106 bytes |
| FM voices used | At most 32 per song in the documented compiler |
| PCM | Separate bank/chip limit; not made unlimited by mapper DATA capacity |
| CH3 special mode | C0-C3 can own operators through EXON/EXn; ALG/FB/pan/LFO remain shared |
| MSX-MUSIC | Six mapped melodic parts plus rhythm; documented OPNA-voice-to-ROM-preset approximation |
| Internal PSG | MSXD/E/F, separate from unprefixed OPNA D/E/F |
| SCC/SCC+ | SSGExtend routes unprefixed D/E/F tones to three SCC parts; not five independent native parts |
| Containers | MUB8, supported muPb0100, MUCOMSX-specific msPb0100 / msPb0101 |
| VGM | Compiler/session export exists; throughput and source fidelity require separate checks |

MUCOMSX-specific msPb headers must not be accepted as ordinary upstream muPb by
existing validators or assumed playable in every MUB player. Additional OPM or
multiple OPNA chips are not supported by this profile. Pages are shared physical
channels, not additional hardware voices. Installing MUCOMSX does not itself
implement CH3 source interpretation or remove the converter's current guards.

## SCC transfer boundaries and next implementation

Preserve all five source SCC channels: the user explicitly confirmed that all
five must also be considered for enhancement. A four-part default from the
suspended enhancer is not the new contract. Native three-part SCC output alone
does not meet the five-part requirement.

Reuse the established MSX source analysis, whole-note/volume-run interpretation,
phrase/repeat structure and provenance. Candidate transfers from the enhancer
are waveform analysis, independently measured YM2608 voice candidates and
chip/clock/core-specific fit identities. Adapt them to this repository's source
musical units and MUC rendering, with input/output CSV/JSON at each new stage.
Do not copy the suspended enhancer's register emitter, loop anchoring, EOF gate
handling or artifact lifecycle: final review found unresolved issues there.

Keep three output concepts separate:

1. Native SCC-family approximation through MUCOMSX's three-part SSG extension.
   It shares unprefixed D/E/F with OPNA SSG, so source PSG routing must be chosen
   explicitly (internal MSX PSG is another profile), never silently overwritten.
2. SCC -> ordinary OPNA FM enhancement, considering all five SCC voices within
   six physical FM channels and evaluating a spare residual lane independently.
   Native OPNA FM can compete for these channels; allocation is target-specific.
3. Optional CH3 operator-frequency candidates, evaluated independently after
   compiler/export validation. They consume C's operators, not four extra FM
   channels, and require shared ALG/FB/pan/LFO plus slot key/loop validation.

A safe implementation order is tooling proof -> explicit MUCOMSX dialect and
container adapter -> SCC source-unit integration -> ordinary OPNA fit/render
adapter -> constrained CH3 experiments -> public MUC/MUB/VGM comparisons.
Native SCC/OPLL/PSG projection fidelity must be measured rather than inferred
from the availability of similarly named MUCOMSX targets. User owns private
listening/regression; no private song batch belongs to this setup milestone.

## Official references

Documentation was inspected at revision
`75f75f0c3f9044f49c79f7f7224f22c42c443e26`; the separately pinned release archive
is the executed binary authority.

- [Official site](https://toughkiddev.github.io/MUCOMSX/)
- [Pinned release](https://github.com/ToughkidDev/MUCOMSX/releases/tag/v1.0-stable)
- [Compiler manual](https://github.com/ToughkidDev/MUCOMSX/blob/75f75f0c3f9044f49c79f7f7224f22c42c443e26/MUC2MUB/Muc2MubE.md)
- [Session and VGM export](https://github.com/ToughkidDev/MUCOMSX/blob/75f75f0c3f9044f49c79f7f7224f22c42c443e26/MUCPlay/MUCPlayE.md)
- [Extension / CH3 reference](https://github.com/ToughkidDev/MUCOMSX/blob/75f75f0c3f9044f49c79f7f7224f22c42c443e26/shared/docs/MUCOMDOTNET_EXTENSIONS.en.md)
- [MSX-MUSIC / PSG targets](https://github.com/ToughkidDev/MUCOMSX/blob/75f75f0c3f9044f49c79f7f7224f22c42c443e26/shared/docs/MSX_TARGETS.en.md)
- [SCC/SCC+ SSG extension](https://github.com/ToughkidDev/MUCOMSX/blob/75f75f0c3f9044f49c79f7f7224f22c42c443e26/shared/docs/SSG_EXTEND.en.md)
