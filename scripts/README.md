# MUCOM88 export and diagnostic tools

- `export_mucom.py`: canonical YM2608 VGM -> musical MUC -> native MUB batch;
  `--muc-only` omits compilation. See [usage](../README.md#mucとmubを生成する).
- `mucom_compiler.py`: native mucomDotNET adapter; resolves local banks in
  retained isolated inputs, verifies nonempty MUB8 and compiler track counts.
- `verify_mucom_native.py`: offline native MUB -> VGM check for FM gates and
  positive control intervals. Requires the matching projection JSON. Scope and
  commands: [native check](../docs/mucom_musical_projection.md#ネイティブfm往復検証).
- `generate_mucom88_public_fixtures.py`: self-authored FM/SSG/rhythm/PCM patterns;
  WING MUCOMVGM prepares source VGM, distinct from native MUB compilation.

The remaining tools retain inherited MDX/MGS compatibility and development
roles. Their target limitations do not define MUCOM output.

# Export and diagnostic tools

Normal single-file conversion uses `python vgm2mml.py`; conversion implementations live in `py/`.

- `setup_tools.sh`: repeatable WSL setup for hash-checked MXC, pinned run68x and the locked Rust helper, outside `outputs/`. Add `--with-mdxtools` for independent metadata/decompiler tools. See `docs/mdx_compiler_setup.md`.

- `export_mdx.py`: single-file or recursive folder export for listening. FM defaults to native MXC via run68, with explicit `--compiler mmlx` available. Calls the canonical frontend, parses the compiled MDX and replays it with soundlog. Writes MML/MDX/VGM per FM song, exact MXC input under `_compiler_inputs/`, or the existing typed MML/MDX/PDX pair with an explicit PCM replay limitation. Records compiler/failures without comparing playback. See [usage](../README.x68k.md#mmlmdxvgmを生成して聴く).
- `mdx_compiler.py`: isolated MXC invocation, strict CP932 encoding and lossless compiler-dialect preparation; canonical generated MML stays unchanged.
- `compare_opm_vgm.py`: explicit channel-mapped OPM state/Segment, Key-event, nominal-pitch and command-size diagnostics. Saves both inputs' raw/state/Segment CSVs; does not certify waveform, loop seams or native display behavior.
- `verify_opm_mdx_roundtrip.py`: OPM state/Segment roundtrip; CLI defaults to MXC via run68, soundlog replays the compiled MDX, and the existing comparator remains unchanged. Explicit mmlx is available. Prepared scores and selected compiler are recorded.
- `verify_opna_mucom_roundtrip.py`: development-only OPNA FM control-state roundtrip through WING's MUCOMVGM. Keeps source/target/re-read passes and checks known state, operator edge order and exact sample boundaries. Its register-MML is a validation artifact, separate from the X68000 MDX output. See `docs/opna_mucom_roundtrip.md`.
- `mdx_fixture_generator/`, `generate_opm_public_fixtures.py`: external MDX replay and authored fixture preparation; the helper's own MML compilation modes use mmlx.
- `decompile_mdx_references.py`: external mdxtools MDX-to-MML reference preparation, provenance and limited structural audit. See `docs/mdx_reference_mml.md`; references are separate from normal VGM conversion.
- `psg_scc_to_mdx.py`: batch projection audit with optional compiler verification and reference comparison; shares `py/psg_scc_conversion.py`.
- `audit_mdx_metadata.py`, `render_additive_mdx_notes.py`, `analyze_wav.py`: metadata, additive-model experiments and acoustic diagnostics (NumPy for WAV analysis).
- `batch_vgm_to_mgs.py`, `collect_mgs.py`, `compile_mgs.mjs`, `mml_to_vgm.mjs`, `mgs_to_vgm.mjs`: compatibility batch/compiler/replay diagnostics. Batch conversion invokes the canonical frontend with `--target mgs`.
- `check_opll_key_edges.py`, `check_reference_pitch.py`, `compare_reference_vgmticks.py`, `audit_reference_loop_windows.py`, `audit_mml_loops.py`, `verify_pitch_roundtrip.py`: compatibility evidence checks, retained because tests and source semantics depend on them.
- `batch_vgm_conv.py`: external converter comparison.
- `experiment_*.py`: retained development experiments; not normal conversion frontends.

For the inherited MDX route, external compiler/player tools and Node dependencies are optional; its PCM output requires the Rust helper's typed MDX assembly and PDX packing modes. Inspect the documented tool arguments and dependency errors before use. The Rust generator has its own README and locked dependencies. Build outputs are ignored.

## MUCOMSX preparation

- `setup_mucomsx.py`: verify/install the pinned official external MSX-DOS runtime set; keep notices and edited-bank protection. Does not download ROM/DOS files or change the default compiler.
- `prepare_mucomsx.py`: stage an authored tooling pattern or existing MUC as a fresh DOS 8.3 job with owned dependencies and explicit staged-only status.

See [MUCOMSX setup and scope](../docs/mucomsx_setup.md).

- MUCOMSX batch MUB: `python scripts/export_mucom.py INPUT_DIRECTORY --target mucomsx --outdir OUTPUT_DIRECTORY`.
  Uses the local `.tools/mucomsx-runtime.json` profile; use `--runtime-profile`
  to override it or `--muc-only` to skip compilation. See
  [native runtime](../docs/mucomsx_runtime.md#batch-mub-compilation).
