# YM2608 adapter corrections checkpoint (2026-10-14)

User reported broad private VGM conversion_failed results. Actual causes were
renderer restrictions (partial operator keys, EOF-held loop gates, SSG shape and
rhythm chronology), PCM source-diagnostic overrejection and stage timeouts.
This was not general FM tone incompatibility with YM2608.

Implemented: source whole gates support ordered partial masks/CID changes;
KD128 suppresses inactive slots and refreshes each <=111-clock note fragment,
including stable-carry loop split. Initial zero-time partial edges use direct
key commands and tie before first timed note. Native synthetic masks/long notes
verified. Source State/Segments stay intact.

Loop support: ordered full-off head event can close EOF-held FM when it maps to
L's exact target clock. FM/SSG stable semantic carry ties across L/EOF; other
positive crossings reject with reasons. Seam CSV/JSON retains IDs and facts.
Rhythm uses G @0 timing carriers plus ordered hit/control/dump commands, avoiding
unwanted nonzero native automatic dumps. SSG shapes0..15 use global G corrections
with explicit extra same-clock driver-reset/phase limitation; shared HW envelope
overlap remains unsupported. No waveform/listening equality claim.

PCM: inactive supplies/init diagnostics do not block sounding engines; ordinary
clock plus extra header with no clock table is resolved only in target projection.
Single positive decoder epoch plus zero-time start/end edits binds original bytes
without changing native IR eligibility/continuation fields. Genuine positive-time
continuation/repeat/unmapped data still reject. External mixer volume metadata
is distinct from decoder configuration. Installed native compiler rejects p0 and
B4 generic writes; FM pan0 is diagnosed, no fabricated approximate mute.

Performance: gate evidence uses index+bisect; released membership uses sorted
gate containment. Source order, zero-time and inclusive endpoint membership kept.
Raw/state CSV parse/dump may still exceed180s for large inputs. Conversion stage
is visible in log/JSON; batch prints actual error and report path. Compiler errors
surface native ERROR text, including the console's exit-zero failures.

Public26 native MUC/MUB compilations pass; post-compiler diagnostic14 tests pass.
Public/synthetic focused70 tests pass; final loop9 tests include one added native
partial-carry test. Public native compile results and historical prior20 FM checks
are separate records; docs/mucom_support_revision.json records this update.
Public artifacts: outputs/diagnostics/ym2608_support_revision/.

USER POLICY: Private failing-song regression belongs to user. Further private
validation must STOP before running, then notify target and purpose. No ongoing
private runs. No broad investigation or new feature scope is needed for this
checkpoint. Do not infer full real-song support from narrow public success.
Next allowed action: await user regression results, then fix that observed stage.
Keep archives/native tools/private fixtures/generated outputs outside commits.
No stage/commit/push; existing staged state and unrelated external notes preserved.


## Finite-song regression option (2026-10-14)

Implemented `--no-song-loop` in canonical mucom CLI and export_mucom.py.
Batch forwards it to conversion; `--timeout 600` applies separately to conversion
and compilation. Native source loop CSV is retained. Target song_loop metadata
records enabled policy, source start and target start. Disabled target song loops
skip FM/SSG seam analysis, marker insertion and seam clock constraints; every
participating part ends at source first-traversal EOF. Finite phrase compression
remains independent. Native FM verifier uses one traversal for this projection.

Validation: focused export/integration/FM-edge/loop suites: 41 passed. Public
mixed_intro.vgm generates MUC/MUB with both requested flags; native FM comparison
passes with source_iterations=1. Artifacts: outputs/diagnostics/no_song_loop/.
Source loop CSV, target units CSV and song_loop JSON inspected by regression test.
No private song conversion/replay, staging, commit or push. User performs private
regression next. Continue the existing stop-before-private-validation policy.


## Musical MUC structure connection (2026-10-14)

Connected shared source planning inside FM/auxiliary control trajectories and
released-control runs, preserving source State/Segments. New Unit control keys
and memberships separate attack from held actions and retain loop-split members.
Native trees/catalogs and separate target-timing-feasible views are retained;
each selected repeat is checked against native keys and expanded target tokens.
Outer-only and nested renderings are compared; total native depth16/count255
constraints retain a pre-depth-limit view and explicit expansion diagnostics.
Each tree's own projection report is distinct from selected-loop rows.

MUCOM-only compaction safely omits known o/D, spells exact C128 note/rest lengths
and folds identical existing timed chunks with ordered KD/ties retained. Native
side-effect writes, voice/volume/pan/H/KD controls are not blindly discarded.
Shared mml_format.wrap_track is reexported by mdx_format for compatibility.
Output MUC is physically wrapped with part prefixes and repeat boundaries.

Passes: .mucom.plain.muc, .before_depth_limit.muc, .uncompacted.muc; native and
timing_loops catalogs/trees/projection; selected_loops; control_units,
inner_repeat_candidates, inner_loops, compaction CSV; source/target summaries.
Selections before depth limiting are separate from final emitted_repeat_commands.

Validation: MUCOM85 checks plus one added audit case and MDX29 shared-format
checks pass. Public26 MUC/MUB compile; native FM20 source comparisons pass.
Paired plain/final native register timelines match for five public auxiliary
patterns and authored normal/partial held FM. Strict .1ms authored native test
is separate from the fixed-timeline width6 target-feasibility regression.
Artifacts: outputs/diagnostics/muc_structure_revision/.
Architect review corrected mismatched projection provenance and pre-limit counts.
No private conversions/replay, stage/commit/push. User handles failed-song
regression next; 64KB failure improvement is unverified. Preserve prior stop-
before-private-validation policy and unrelated staged state.


## Native normalization checkpoint / user-requested pause (2026-10-14)

User requested default --normalization-ms 8 and a pause at a good checkpoint,
then asked only for the intended architecture. Implementation work is paused.
Do not continue implementation or private regression until the user resumes.

Current uncommitted working changes: native CLI/export default normalize_lengths
True and normalization_ms 8.0; --no-normalize-lengths restores strict baseline.
New mucom_normalization.py applies original-time, closed full-mask FM omission,
output-only immutable derived-state replay, shared source-clock nomination and
exact integer-domain ordered native Timer-B allocation. Auxiliary eligibility
and bank packing precede omission. SSG/rhythm stay retained; single independent
observed-reset PCM gates may be omitted while retaining source IR and bank bytes.
All-short output is finite silence; non-key controls are retained independently
of remaining gates. Native source dumps remain intact; separate output State,
Segments/music, normalization JSON and selected timing CSV are emitted.

MUCOM structuring retains native/target-timing catalogs plus a normalized
FM/rest semantic view using accepted target lengths. The verifier consumes the
recorded clock allocation and explicit source key omission IDs, also in loops.
No nearest fallback for missing recorded timestamps.

Checks completed: normalization policy 12, structure 11, integration 10,
verifier normalization 9 (includes real all-omitted and mixed MUB/FM checks),
CLI/export 20 and shared ordered-domain/MDX compatibility 14 pass. Public MUC/MUB
42/42 success (mucom88 26 + from_fm16; 16 inputs are duplicate fixture copies).
Native FM 36/36 pass; all42 adopted normalization. Auxiliary engine waveform
or native equivalence is not claimed. Public artifacts:
outputs/diagnostics/mucom_normalization_revision/public/export_summary.json
outputs/diagnostics/mucom_normalization_revision/public/native_summary.json
No private conversions, replay, staging, commit or push.

Pending at pause: final implementation review, aggregate focused checks after
all edits, durable docs/README/private field-note updates, and explicit PCM
short-omission/zero-pulse synthetic edge coverage. Current docs still describe
older strict timing selection and need reconciliation before declaring done.
Consider checking PCM source-end consistency in the normalization adapter.
Keep unrelated staged/working changes and private field notes intact.

Architecture question: sharing copied source modules alone is insufficient.
A shared musical interpretation/normalization/structure contract must feed
separate native target adapters. Share policies/planners/ordered timing solver,
while MDX and MUCOM supply independent clock domains, syntax and constraints.
Integration tests must verify stage execution and intermediate semantics, not
only source parser compatibility or successful MUC compilation.


## Native normalization finalized after user resume (2026-10-14)

User resumed the task; the prior pause instruction is revoked. Native default
--normalization-ms8 and normalization enabled are implemented. User clarified
that sharing the inherited musical pipeline and keeping target adapters apart
matters more than restarting the repository from a clean copy.

Final architect review confirmed immutable source evidence, shared planners and
ordered allocator, native Timer-B domains, source membership, and policy-aware
recorded-map verification. It found one release-control eligibility hole:
later omitted gates hid earlier retained-gate release edits. Fixed by retaining
the union of baseline release eligibility and omitted-gate eligibility. Added
long-gate -> off -> TL39 -> later omitted short-gate regression inspecting the
emitted source-linked control row. Native FM gate verification deliberately
excludes released envelopes; do not claim it detects this class of defect.

Added matching FM/PCM source-end validation, zero-duration canonical omission,
and synthetic short observed-reset PCM with unchanged full bank bytes and
native PCM IR. Normalization policy suite is now16 checks. Final aggregate
MUCOM/export/ordered timing suite:138 tests passed, zero skipped/failures/errors.
Logs:outputs/diagnostics/mucom_normalization_revision/focused_tests.log/.json.
Earlier public batch remains42/42 MUC/MUB success,36/36 FM native comparisons;
16 duplicate fixture copies are included. All42 adopted normalized timing.
WSL finite mixed_intro export succeeded with --normalization-ms8,
--no-song-loop and --timeout600. Artifacts: same revision directory/wsl_finite.
README, musical projection docs and stable project knowledge now describe the
actual default, two-part policy, native/normalized structure views, atomic
fallback, recorded map, output-only State views and known validation limits.

Remaining: user owns private regression and listening. Source-gate omission
and target timing can change music by the explicit recorded policy; no claim
of source waveform identity, auxiliary roundtrip equivalence, or resolution of
private64KB compilation failures. Stop and notify before any private validation.
Further review/refactoring may improve chip adapter reuse, but do not replace
shared source planning/ordered allocation with target-text heuristics.
No staging, commit or push; unrelated staged and working changes are preserved.


## Conditional FM/SSG boundary recovery and timing diagnostics (2026-10-14)

User authorized implementation of proposed regression-error countermeasures;
user retains private regression responsibility. No private song conversion,
replay or listening was performed. Existing private diagnostic summaries were
read earlier to classify failures; public committed notes contain no music data.

FM resolve_gate_pitch uses only same-gate/same-accepted-clock complete observed
pitch to spell an initial prefix whose whole target duration is zero. Source
State/Segments/control keys/members remain untouched; donor audit JSON/CSV is
emitted. Positive unknown target time or no donor still rejects specifically.
The normalized clock CSV/JSON is now persisted before FM consumes it, including
rejection paths. First projected-zero controls preserve explicit initial key
order, seed target pitch before key-on, and tie the first positive note to avoid
another attack. Full and partial synthetic native FM edges pass.

Source-domain opna_ssg_loop replays actual loop assignments from first and EOF
seeds and checks effective state across every positive target interval. It
catches entry-inactive fields becoming active later. Seam analysis runs after
timing; unused fields do not force equality, observed same-clock fixed-envelope
restore permits split/continuation and reasserts certified head controls atL.
EOF zero-time state is retained; EOF tie follows actual tail sounding state.
Delayed positive mismatches and unsupported envelope-phase restoration still
reject and record proof failures. New ssg_loop_checks CSV/JSON preserves evidence.
No unconditional first-pass head reset, second-pass private conversion or raw
source rewrite was introduced. Global/multiple-noise phase/order identity is not
certified by the per-channel proof.

Native SSG validation exposed two existing adapter defects: wrong D sign/unit,
and software E reinitialization inside tied notes. Corrected D before-octave
scaling and E only at true attacks/silence/HW->SW; HW->SW gets a separate output
unit. Tone-only lanes do not write sharednoise. Native FRQ_DF clears previous
code0, colliding with tied o1c encoded0 in FM/SSG. Alternative legal note+exact
D spelling avoids that driver bug. Native o1D0 probe confirmed all12 SSG table
entries. Official source reference (ignored diagnostics) is music2.reference.cs;
public docs link the upstream source, no vendor code copied into production.

Integer-domain Timer-B arithmetic eliminates per-point Fraction allocation;
independent parity covers all256 timers, ties/signs/large samples, feasible and
rejected candidate maps. Synthetic arithmetic-only 2.217s->0.365s (~6x), not an
end-to-end/private speed claim. stage_timings/current start Unix/current completed
are persisted before work, finished on error/success; timeout export shows the
last persisted MUC stage. Source parsing can still dominate long inputs.

Final156 related tests passed, no skipped/errors/failures. Native SSG sweep tests
inspect EVERY positive interior register interval (±2sample boundary tolerance),
including period0..4095, bothDsigns/multipleoctaves, heldvolume, true silence and
HW->SW. Synthetic SSG loop checks source two traversals; FM full/partial prefix
checks compiled/native replay pass. Public26 MUC/MUB compile,20FMnative checks
pass (17initial+3 regenerated after aliasfix);6FM-absent are not_applicable.
Combined outputs/diagnostics/mucom_boundary_revision/validation.json;
focused_tests.log/.json, public/, public_recheck/, ssg_table/, ssg_probe/.
WSL mixed_intro export with flags --no-song-loop --timeout600 and implicit8ms
normalization succeeded (wsl_smoke/). README/docs/project knowledge updated.

User runs private regression next. Do not claim RTYPE/XANADO failures or all
180s timeouts are resolved until user results arrive. Remain conservative on
positive-time unknownpitch and delayed active loop restoration. No staging,
commit or push; unrelated changes preserved.

## 2026-10-15: Voice bank filename

User-requested naming change only: generated `<stem>_voice.dat` is now
`<stem>.dat`; MUC #voice and manifest paths follow the same name. This preserves
8.3 voice filenames for input stems up to eight characters (MUCOMMSX use).
Existing integration/export tests updated: 10+18 tests pass. No private
regression, refactoring, staging, commit or push. Resume only requested work.


## 2026-10-15: OPNA PCM live controls implemented; stop for user regression

Read-only x68k SegaPCM review completed. Its observed State/Segments, separate
chip-specific runtime and separate MSM6258 adapter are the relevant boundaries;
no Sega renderer or target PDX model was imported as an OPNA decoder/common IR.

Existing native OPNA IR unchanged. New opna_adpcm_playback certifies epoch
control bits and separately records nominal rational source nibble positions.
mucom_aux projects live level/pan/Delta-N to explicit port1 multiport y commands,
one initial sample note and identical-code held ties per decoder epoch. Existing
musical structure/normalization is reused. First PCM slice is positive-protected
so note initialization cannot overwrite collapsed live controls. Source inferred
natural end does not newly cut the live epoch; accepted target-time consumption
and any normalization omission have their own audit. Runtime/cycle/waveform
identity is not certified. Address/memory-mode/data/repeat/heldPCM L remain guarded;
initial level250..255 is still rejected.

Old Windows260407 dotNET rejects four-argument y. Official fixed NET6 tools are
separately installed under .tools/mucomdotnet-pcm. New setup_mucom_pcm_tools.py
reproduces the verified archive and retains GPLv3 upstream LICENSE. Automatic
compiler/replay selection changes only multiport-y MUC. Ordinary MUC keeps old
tools. Replay startup phase is a measured profile (old1clock, fixedNET6 0clock),
not an inferred per-song timing correction; tolerance remains2samples.

Final250 related tests: failures0/errors0;1 existing MDX-helper integration skip.
PCM-specific17 tests pass, including6 native compile/replay cases. Existing
public26 MUC/MUB pass;20 native FM checks pass,6 FM-absent not_applicable.
Public heldctrl.vgm and WSL batch CLI implicit8ms --no-song-loop --timeout600
both compile successfully. Saved/inspected source position CSV and target audit.
Artifacts: outputs/diagnostics/opna_pcm_controls/{tests_final.json,tests_final.log,
public_validation.json,public_baseline/,public_pcm/,wsl_pcm/,probe/,mixed_fm_pcm/}.
Public authored fixture: tests/fixtures/public/opna/pcm_controls; generator
in tests/scripts/opna_pcm_fixture.py. README links focused PCM/profile docs.
Private note: external mucom88 field_notes/2026-10-15_opna_pcm_controls.md.

No private song reconversion/compile/replay, staging, commit or push. Preserve
unrelated staged reset/copy changes. Stop here for user's regression/listening.
SegaPCM<->OPNA conversion, new ADPCM-B encoder/mix/allocation, source-loop
certification, CH3 projection and broader refactoring are not implemented.


## 2026-10-15: README aligned with x68k structure before initial publication

README rewritten around the x68k order: normal conversion/input-output, song
loop, MML confidence, PCM, options/compatibility, troubleshooting, layout,
license and acknowledgments. MUCOM-specific defaults verified with both CLI
help commands (canonical outdir defaults to outputs; batch lacks --no-loops).
Detailed conversion/profile/validation history links to docs rather than chat
history or obsolete local archives. WING MUCOMVGM, mucomDotNET, source project,
third-party notices and unsupported CH3/CSM/PCM constraints remain explicit.
Local link targets/code fences/examples checked. Documentation-only task;
no conversion, staging, commit or push. User requested README review before
initial commit/push; publication has not been performed.
