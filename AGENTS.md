# Project AGENTS.md

## What this repo is

- Convert VGM into inspectable, source-derived musical Segments, then generate editable, structurally meaningful MUCOM88 MML.
- Intended native route: OPNA/YM2608 VGM -> native analysis -> musical Segments -> musical interpretation / structural analysis -> MUCOM88 MML (.muc), with required PCM handling.
- Current route (2026-10-14): default `--target mucom` generates musical MUC and native banks from YM2608; scripts/export_mucom.py compiles MUB. Explicit `--target mdx` and `--target mgs` retain inherited routes. docs/integration_baseline.json records the reset starting point, not current content identity.
- Preserve inherited OPM, OPLL, PSG/SSG, SCC and MSM6258 source-analysis paths. Their presence does not imply completed MUCOM88 projection.
- Main language: Python 3.
- Primary working environment: WSL.
- Reuse established musical interpretation, note-length correction and phrase/loop processing. Do not replace them with one MML fragment per Segment joined by ties.
- Read docs/project_knowledge.md before changing conversion behavior. Intended architecture and current implementation status are separate.

## How to run

Verify the current CLI in `README.md` and `python vgm2mml.py --help`. The default produces musical MUC; native MUB compilation is a separate batch-export stage.

- Canonical MUC converter: `python vgm2mml.py <input.vgm> --outdir outputs/<song>`
- MUC/MUB batch: `python scripts/export_mucom.py <vgm-or-directory> --outdir outputs/mucom88/<set>`
- Inherited MDX route: `python vgm2mml.py <input.vgm> --target mdx`
- MGSDRV compatibility, if retained by the current CLI: `python vgm2mml.py <input.vgm> --target mgs`
- Automated checks: `python -m unittest discover -s tests/scripts -v`
- For isolated conversion checks, use a separate output directory outside source fixtures (`--outdir <directory>` where supported).
- For work on `vgm2mml.py`, use `--dump-passes` whenever intermediate results are needed for inspection.
- If a requested command or option is not documented here or in `README.md`, inspect the repository and report the actual command. Do not invent a workflow.

## Layout

OPM/MDX/PDX entries describe inherited source, compatibility or reference infrastructure. They do not define MUCOM88 target constraints. Verify actual file roles and support status in the implementation.

- `vgm2mml.py`: canonical musical MUCOM88 entry point; inherited routes require explicit target.
- `py/opna_music.py`: native whole FM gates and internal source control trajectories.
- `py/musical_structure.py`: shared source musical-unit loop planning, reused by MDX and MUC.
- `py/opna_muc.py`, `py/mucom_fm.py`, `py/mucom_aux.py`: separate YM2608 timing/target projection and MUC rendering.
- `scripts/export_mucom.py`, `scripts/mucom_compiler.py`, `scripts/verify_mucom_native.py`: batch MUC/MUB export, compiler adapter and scoped native FM check.
- `py/vgm_reader.py`: VGM parsing / trace-side implementation.
- `py/opna.py`: native OPNA source analysis.
- `py.org/opna_muc.py`: locally archived old MUCOM88 projection, not an active module. Re-integration must reuse the established musical/structural pipeline.
- `py/opna_adpcm.py`, `py/opna_pcm.py`, `py/opna_ssg.py`: OPNA ADPCM/PCM and SSG processing; inspect implementation for current scope.
- `py/opm.py`: immutable native OPM state/Segment types, Segment construction, and state/Segment CSV dumping.
- `py/opm_conversion.py`: native OPM-to-MDX pipeline orchestration.
- `py/opm_mdx.py`, `py/opm_mdx_music.py`: native OPM target projection and structured MDX rendering.
- `py/msm6258.py`: backend-neutral PCM source evidence, encoded samples and playback spans.
- `py/pcm_mdx.py`: single-P PCM binding/projection, 16-track/E8 target layout and externally packed PDX byte verification.
- `py/opm_target_state.py`: explicitly projected OPM state/write trajectories and interval CSVs for PSG/SCC targets.
- `py/opm_performance.py`, `py/opm_target_vgm.py`: inferred PSG/SCC musical gates and inspectable OPM target VGM feeding the ordinary OPM converter.
- `py/opll.py`, `py/opll_mml.py`: OPLL processing and MML generation.
- `py/psg.py`, `py/scc.py`: event analysis and Segment construction.
- `py/chip_segments.py`: immutable PSG/SCC Segment types and CSV dumping.
- `py/psg_mml.py`, `py/scc_mml.py`: Segment-to-MML rendering and compatibility entry points.
- `py/segment_utils.py`: shared segment/pass processing.
- `py/mml_utils.py`: shared MML utilities.
- `tests/scripts/`: automated checks and shared test helpers; `tests/fixtures/`: source/reference material.
- `docs/project_knowledge.md`: project design context and transformation rules. Read before changing conversion behavior.
- `docs/opna_segments.md`: OPNA source evidence and Segment semantics.
- `docs/mucom_musical_projection.md`: MUCOM88 musical projection and current limitations.
- `docs/opm_segments.md`: native OPM state/Segment fields, source evidence and validation rules.
- `docs/pcm_pdx.md`: inherited MDX/PDX target rules and limits, not OPNA source semantics or MUCOM88 bank constraints.
- `docs/psg_scc_segments.md`: current PSG/SCC pipeline, Segment fields and validation limits.
- `inputs/`: source/reference material if present locally. Do not commit private game-derived material.
- `outputs/`: generated artifacts and intermediate results if present locally.
- `field_notes/...`: logical documentation references to external private notes, not a local directory; see External private field notes.
- `handoffs/current.md`: current restart state, if present.
- `mistaken.md`: do-not-repeat rules if present.

## Most important rule: preserve intermediate results

- Intermediate results are first-class project artifacts for development and validation.
- Do not design a conversion as an opaque VGM -> MML transformation when a meaningful intermediate representation can be emitted.
- Every non-trivial interpretation or transformation should be inspectable before the next transformation consumes it.
- Prefer CSV, human-readable representation that can be diffed and inspected outside Python.
- When adding a new transformation stage, add or preserve a way to dump its input and output.
- Do not remove, merge away, or silently bypass existing pass dumps merely because the final MML still sounds correct.
- A change is not considered well validated only because final MML was generated. Inspect the relevant intermediate result as part of debugging conversion behavior.

## Transformation boundary
Keep these conceptual stages separate:

`Raw → State → Segments → Musical interpretation / Structural analysis → Target projection → MML`

- Raw VGM: source commands, waits, register writes, and timing facts.
- State / trace: chip state reconstructed from those writes.
- Segment / interpreted event: notes, rests, volumes, instruments, rhythm events, envelopes, waveforms, legato/retrigger and similar musical interpretations.
- Musical interpretation / structural analysis: reconstruct sounding units and internal control trajectories, infer continuity, phrases, patterns and loops while retaining mappings to source Segments.
- Target projection: map that musical representation to YM2608/MUCOM88 capabilities, with explicit timing correction, allocation and loss diagnostics.
- Target MML: render the projected musical structure in MUCOM88 notation; adapt inherited target syntax explicitly. Textual compaction must preserve projected semantics.

- Do not move target-format convenience backward into earlier stages unless the user explicitly decides that the intermediate representation itself should change.

## Evidence and inference

- Preserve directly observed values separately from inferred values whenever practical.
- Do not overwrite source-derived timing/register/state information with a quantized or normalized value without retaining the pre-transformation value somewhere inspectable.
- If a value is inferred, make that fact visible in naming, fields, comments, or documentation where useful.
- Do not promote an experimental heuristic to a chip specification or project invariant.
- When behavior is unclear, inspect source VGM, traces, pass CSVs, and existing code before changing the algorithm.

## Private fixtures

- Current YM2608 failed-song regression belongs to the user. Stop and notify the target and purpose before any further private failed-song validation. Continue only authorized public/synthetic checks; avoid unnecessary broad investigation.

- Private VGM, ROM-derived data, WAV, MML test material, and other copyrighted fixtures stay outside git unless explicitly safe to commit.
- If a local fixture configuration exists, local checks may use it.
- If private fixtures are unavailable, skip those playback/data checks and report that limitation.
- Never paste private fixture contents into documentation, logs intended for commit, issues, or pull requests.

## Conventions

- Follow existing style in the nearest files.
- Keep changes small and attributable to one transformation stage when practical.
- Prefer existing utilities and pass structures over parallel implementations.
- Do not add comments that merely restate code.
- Keep source material, intermediate/generated data, and hand-written project knowledge conceptually separate.
- Preserve enough identifiers/timestamps/channel information in intermediate data to trace a final MML event back toward its source.

## Do not

- Do not rewrite the whole project merely to introduce this workflow.
- Do not put long-form design documentation into this file; put it in `docs/project_knowledge.md`.
- Do not collapse Raw, State, Segment, and Target into one representation for convenience.
- Do not discard intermediate information solely to make MML shorter or cleaner.
- Do not change timing, note boundaries, retrigger/legato behavior, volume, envelope, rhythm interpretation, SCC waveform handling, or OPLL patch handling without checking the relevant intermediate stage.
- Do not promote experimental mappings or heuristics to specifications.
- Do not treat source Segments containing LFO/AMS/PMS as automatically invalid. Reconstruct musical continuity first, then assess target support and report specific unsupported behavior or approximation.

## Done means

- The requested change is implemented.
- Relevant automated checks were run, or the blocker is reported.
- Relevant intermediate results were generated and inspected when conversion behavior changed.
- Final MML generation was checked when applicable.
- Any newly learned stable rule is recorded in the appropriate project knowledge / field note rather than being left only in chat or code archaeology.
- If `handoffs/current.md` is in use, update it with what changed, what is unfinished, the next allowed action, and what must not happen next.

## External private field notes

- Field notes are maintained in a separate private repository.
- Resolve this path relative to this project's repository root:
  `../my-external-intelligence/mucom88_vgm2mml/field_notes/`
- Treat references to `field_notes/...` in project documents as references to that external directory.
- Before changing established conversion behavior, search relevant external notes for prior decisions, rejected approaches and known regressions.
- If a relevant inherited decision is missing from this project's notes, also search the sibling `x68k_vgm2mml/field_notes/` and `msx_vgm2mml/field_notes/` directories within the same private repository. Keep new notes in this project's directory.
- Classify notes by their meaning, not their filenames. Notes mentioning MDX, OPM or MGSDRV may contain shared architectural decisions applicable to MUCOM88.
- If the external directory is unavailable or access is restricted, report that limitation. Do not infer that missing notes invalidate established decisions. Continue independent work using public principles; identify any decision that cannot be grounded without the missing notes.
- Save new dated experiments and findings in the external directory. Do not recreate a local field_notes directory or copy private notes into this public repository. If external writing is unavailable, report the unsaved findings and intended destination.
- Keep stable principles required for public maintenance in docs/project_knowledge.md, without copying private experimental details.
- Note changes belong to the private repository. Inspect its status and diff separately; stage only notes changed for the current task.
- When committing is requested for the task, commit its note changes separately in the private repository and report both repositories' commit status. Preserve unrelated changes; do not use a blanket staging command in the private repository. A public-project commit does not include the notes.

## When you learn something useful

- One-off observation or experiment: `YYYY-MM-DD_topic.md` in the external private field-notes directory defined above.
- Repeated failure / known trap: `mistaken.md` if present.
- Stable design principle or domain knowledge: `docs/project_knowledge.md`.
- Always-on short rule: only then add a concise bullet here.

## Project knowledge
Before changing parsing, timing, pass structure, note segmentation, volume, envelopes, rhythm, retrigger/legato, SCC waveforms, OPLL patches, quantization, or MML rendering, read `docs/project_knowledge.md`.

