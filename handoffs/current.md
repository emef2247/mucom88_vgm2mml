# Takeover and MSX re-sync to a52e52c (2026-11-01)

The v2.x working tree was committed by the user as codex/upgrade_to_v2.x
(809b63d). Muse continues on claude/upgrade-v2x (from 809b63d).

Done in 039b797: musical_hal snapshot advanced from MSX b78d675 to a52e52c
(NES APU register decode, source registration and native units; new
nesapu/native_nesapu snapshots; provenance manifest and its test pin
updated). Local psg_scc_performance/psg_scc_target advanced to the same
MSX state (they were byte-identical to b78d675), carrying the dedicated
PSG noise fix; mml_envelopes renders pitchless notes with a placeholder
pitch; regression test ported. The MSX production_v110 macro dispatch was
not ported (no corresponding package here).

Full suite on Muse's VM: failure set identical before and after the sync
(17 pre-existing failures/errors, environment-dependent; the new noise
regression test and the provenance test pass). The user's own environment
results may differ, as with the sibling projects.

Next: OPN2 (YM2612) VGM -> MUC. Design study is in Muse's workspace
(mucom88_opn2_design.md): target dialect is mucomMD2vgm's Mega Drive
mapping of MUCOM88 MML; entry point is conversion_config.inspect_source,
which does not inventory 0x52/0x53 yet. Decisions resolved by the user
(2026-11-01): new --target profile for MD, DAC diagnostics-only in v1,
DCSG separated into a later stage.
Do not resume the deferred x68k catch-up or DCSG dual-source work.

---

# OPN2 S1: inventory and register evidence (2026-11-01)

User approved the staged plan (new MD --target profile, DAC
diagnostics-only in v1, DCSG as its own later stage) and parallel work
alongside the Famicom debugging, which keeps priority.

S1 done: conversion_config.inspect_source inventories YM2612 writes
(0x52/0x53, dual 0xA2/0xA3 reported as an unsupported second instance)
with the header clock at 0x2C; musical_hal native_imports gains an
opn2 domain (WRITE/STREAM chip maps, CHIPS, opn2_clock_raw) and emits
{native}.opn2.registers.csv evidence with OPNA-shaped channel
attribution (new native_imports/opn2.py). No state/Segment
interpretation yet: analysis_available stays false for opn2 and the
provenance manifest records source.py's adaptation. SN76489 inventory
in the frontend is unchanged (arrives with the DCSG stage).
Tests: tests/scripts/test_opn2_source.py (authored synthetic VGMs).
Next: S2 gate/Segment extraction modeled on opna_music.

---

# Native SCC5 default for MUCOMSX (2026-10-29)

User requests SCC VGM -> native SCCA..SCCE by default, preserving shared MSX
PSG/SCC interpretation and structure. Implemented within --target mucomsx;
global default mucom is unchanged. --scc-output opna-fm retains the prior additive
compatibility route. PSG remains MSXD/E/F; no SSGExtend or FM voice bank for SCC.
User supplied author's response: real MSX version is in preparation. Use
--target mucomsx --muc-only for current Playground MUC checks; do not claim
installed DOS compiler compatibility or update binaries/MUB validators blindly.

Source wave bytes and linear volume runs retained; native wave64-ID pool,
frozen native period table and direct signed ?DB offsets. Original-device source
clock retained in diagnostics. Ordinary C128 rows avoid compact ?XU restrictions.
Native mixed-profile repeats at most2levels; exact loop/macro expansion checked.
FM-state filtering stays only on FM routes. Native MUC over64KiB is retained with
legacy-DOS capacity warning; no compilation guarantee. README unchanged.

Focused tests93passed; public psg_scc/001 and002 exported MUC-only. Source and
target dumps inspected. New mixed native profile preserves short interpreted
PSG/SCC partitions (no blanket8ms deletion). Public001 retained all743 sounding
partitions with zero omissions; interpreted attack boundaries are unchanged.
User owns local_only regression/listening. Changes saved, not committed/pushed.
See docs/mucomsx_conversion.md and private field note for current scope.

---

# MUCOM musical-layer catch-up (2026-10-29)

MSX checkpoint committed as b78d675; separate private note commit 2f43827.
The MUCOM port now connects namespaced source HAL, reconstructed source-native
VGM, MUCOM88 blueprint MUC plus native SMF MIDI, optional sample-based score
normalization, and shared export controls. FM threshold reaches OPNA/OPM target
FM and MUCOMSX OPLL/SCC-to-OPNA FM paths. Export default24ms, canonical1ms.

Source products remain available when production MUC fails; PCM has no blueprint.
Blueprint is MUCOM88 MUC, including for --target mucomsx. Fixed audition settings
replace source synthesis/dynamics. References retain original source chips.
See docs/msx_parity_plan.md for scope, defaults and source/target distinctions.

Validation:75 focused checks passed; final blueprint/service16 checks passed,
then15 service/CLI checks after preserving normalized input song loops. Three
public source products regenerated and MUCOM88 blueprint compiled. Source loop
retiming abstains until validated; normalized target input retains enabled loops.
Architect timing/publication review findings are closed.

Public/authored checks only; user owns local_only regression and listening.
Do not run private failed songs or broad emulator sweeps. Do not change README.
Prior MDX removal and curve backport changes are preserved. Unrelated -w.mub must
not be staged. MUCOM changes are not committed/pushed as part of this checkpoint.

---

# MDX toolchain removal (2026-10-25)

Removed inherited MDX export, compilation/replay, fixture-generator and external
inspector scripts plus their tool-specific tests. Public CLI no longer accepts
MDX/OPM output aliases. Native output and MGS compatibility remain; shared pure
source/structural modules and reference evidence were not purged. Removed the
corresponding MDX external-tool sections from THIRD_PARTY_NOTICES.md, retaining
required mapping attribution. README files outside the deleted Rust helper
are unchanged. Existing local tool installations and generated assets were not
deleted. Validation results follow in the task's private note. No commit/push.

---

# Shared PSG/SCC source-curve backport (2026-10-25)

Changes saved for: PSG -> OPNA SSG and MUCOMSX PSG/SCC interpretation feeding SCC -> OPNA FM.
Shared pure engines are synchronized with MSX; native adapters call analyze /
project at44100Hz before existing structure and target projection. Source
Segments/controls remain intact. Source-SSG register labels are mapped explicitly;
mixer writes stay protected. MGSDRV syntax/bank/tie rules were NOT transplanted.
DCSG and inherited MGS extract_notes are unchanged.

Minimal validation: 9 direct PSG/MUC,14 MUCOMSX source,5 SCC OPNA and11 public/authored MUC conversion tests passed.
No local_only fixture conversion or broad regression. The user owns listening
and full regression. Do not launch expensive compilers/emulators/catalog runs.
README unchanged. See docs/msx_curve_interpretation.md and external field_notes/
2026-10-25_shared_msx_curve_backport.md. No staging/commit/push. Preserve unrelated
working-tree files, including MUCOM's pre-existing -w.mub.

---

# Timeout default update (2026-10-24)

export_mucom conversion/compilation stage timeout now defaults to1200 seconds
for both CLI and Python batch API, including MUCOMSX runtime forwarding. Explicit
--timeout overrides remain supported. Segment-to-OPNA and OPM-to-OPNA catch-up
from x68k are deferred by user request; do not resume those changes here.

# Batch package layout update (2026-10-24)

Implemented common input-parent packages in `scripts/export_mucom.py` for both
MUCOM88 and MUCOMSX: flat `tracks/`, per-song `_diagnostics/`, unchanged staged
`_source_inputs/`, package manifest/results, and invocation aggregate summaries.
Optional outdir writes adjacent packages and excludes generated directories.
Validation: 25 export tests and 15 MUCOMSX batch/CLI/integration tests passed.
Public actual MUCOM88 and MUCOMSX MUB compilation passed under
`outputs/package_layout_20261024/`; publication/source hashes were inspected.
Metadata-vs-package collision and injected manifest-commit rollback are covered.
Existing unrelated FM normalization edits are preserved. No commit or staging.
Failed reruns retain prior successful products; bank/name conflicts stop unsafe
publication. See `docs/output_layout.md`. Public/synthetic validation only;
local_only regressions remain with the user. Do not resume SCC or drum inference
from earlier handoffs as part of this layout-only task.

# Latest user report: DCSG x2 — record only (2026-10-20)

User has 1% allowance and requests recording/status explanation only. Do not
resume implementation, reconversion or tests from this report. MS LAND dual
DCSG: 11/20 success, 9 MUC input-capacity failures (65536-byte limit). Exact
sizes and diagnostic run IDs recorded externally in
field_notes/2026-10-20_user_dcsg_dual_regression.md.
Current-code read confirms derived AY views -> shared MSX note/structure ->
PSG state spelling -> shared MUCOMSX macro portfolio, not a separate legacy
DCSG compactor. Run revision not supplied; do not assert which saved checkpoint
produced the reported numbers. No Git stage/commit/push.

---

# MUCOMSX PSG rendering / MSX macro parity — 2026-10-20

User authorized code changes and public-only reconversion. Private/local_only
regression belongs to the user. No Git stage, commit, push or branch changes.

Saved:
- `py/mucomsx_psg_render.py`: sequential state spelling on already interpreted
  PSG musical units before exact source-plan loop projection. Suppress duplicate
  P/D/o/software-v; shorter C128 durations and relative octave spelling. Forget
  state at L, preserve every E/w/m/S and tie, invalidate software-v after S.
- Original commands remain in units.csv; rendered_command and per-unit
  mucomsx.psg_render.json expose before/after and effective-event proof.
- `mucomsx_macros.py`: atomic baseline plus the four MSX width/rank searches,
 32 definitions,512-character bodies, merged tracks/nested AST, ancestor/tie/L
 safety and exact expanded tree proof. Source unit boundaries are not barriers.
- Source Segment/note/volume interpretation, clocks, routing and OPLL voice
 policy unchanged. SCC remains all-voice OPNA additive4, PSG native MSXD/E/F.
- README short overview; docs/mucomsx_conversion.md describes current contract.

Important envelope limit: MUCOMSX documents six-parameter software-envelope
presets, not arbitrary MGSDRV tick tables. Exact observed curves remain tied v
controls; macros may share literal command sequences. No ADSR fit or invented
release, E suppression, or claim of complete MGS envelope parity was added.

User requested an immediate saved checkpoint at10% allowance. Production code,
docs and this handoff are saved; no Git mutation. Existing completed public
results remain available. The lossless repeated-prefix macro-search improvement is saved and16 tests
pass, including old/new exhaustive candidate and complete-allocation equality.
Old long verification was stopped; remaining13 public inputs restarted under
outputs/mucomsx_psg_compaction_20261020/remaining/.

Validation in progress/checkpoint:98 distinct MUCOMSX public/synthetic checks
passed (95-test suite plus2 added boundary cases and1 exact-search equivalence case). Public reconversion completed38/51 so far; all38 generated MUC successfully.
Native compile-only passed35/35 completed selections. Remaining large public
macro searches need rerun after the exact-search speed improvement. Artifacts:
outputs/mucomsx_psg_compaction_20261020/
- after/results.csv and summary.json: whole requested public source set.
- native_compile/results.json: actual MUCOMSX compile-only results.
- remaining/results.csv:13 restarted fixtures using exact prefix pruning.
- comparison.json: source dump identity, event proof and sizes vs pre-state
  spelling with the same new macro portfolio. Not a pre-portfolio benchmark.
- before_state/:20 public comparison baselines, source artifacts retained.

Native trace limitation: pure-PSG authored explicit-E and experimental preset
probes both made recognized msPb0100 MUBs and DONE.OK, but CHECK.VGM was160 zero
bytes and timed out waiting for a finalized header. This is not source->MUC
or MUB compilation failure, nor proof that native PSG VGM is unsupported.
The preset experiment was NOT adopted. Do not claim native playback/roundtrip
identity from those results or silently relax runtime validation.

Active remaining public process writes remaining/results.csv and summary.json
as each fixture ends. Preserve it and inspect those files before starting any
new conversion. No completed result should be rerun just for a status update.

Next: finish the remaining public verification and compile its successful MUCs
if allowance permits; otherwise notify user of this
explicit checkpoint without claiming all51 or full roundtrip completion.
Remaining native VGM export issue requires separate diagnosis if requested.
Exact sample-time quantization can still prevent repeats that MGS's60Hz view
can fold;64KiB MUC and compiled DATA capacities remain enforced. Do not change
source timing or drop controls to manufacture compression. Do not resume
suspended scc_vgm_enhancer or OPLL preset/interface work without direction.

Prior audit: external field_notes/2026-10-20_msx_psg_structure_policy_audit.md.
Prior user regression/history: external
field_notes/2026-10-20_user_regression_scc_fm_opll_and_psg_size.md.
Current task findings: external
field_notes/2026-10-20_mucomsx_psg_state_and_macro_parity.md.

Native corroboration: authored OPNA SSG D-part before/after state spelling
both export valid VGMs, with identical effective register-state trajectories
and27552 samples. MUB376->352 bytes; VGM1367->1358 bytes;3 redundant OPNA
writes removed. This does not resolve the internal-PSG export probe failure.


## 2026-10-23 FM short-state alignment checkpoint

- Added `mucom_fm_state_normalization.py` to select <=44-source-sample ordinary
  FM states in the output-only gate view, using shared `fm_state_runs.py`.
- Connected both normalization clock planning and FM target projection; native
  OPNA and mapped OPM-to-OPNA use the same route. No-normalize bypasses it.
- Preserve native evidence, ordered key/global controls and release edits;
  protect tone/pan/modulation/nonuniform TL changes. CSV input/output/decisions
  remain inspectable. Public/authored checks passed: 6 short-state, 16 normalization, 7 ordered-key,
  6 OPM-to-OPNA tests (35 total). Authored conversion inspected decision CSV
  and verified explicit no-normalize bypass, plus mapped OPM short-pitch filtering.
- User owns local_only regression. Do not reconvert private failed songs.


### Configurable FM short-state update

The output-state threshold now follows `--normalization-ms` (default 8 ms / 352
source samples, custom 12 ms / 529 samples). Whole-gate and internal-state
policies remain separate. Final positive controls are protected when short as
`release_tail_state`, because MUC key-off does not restore the original full
state for the audible release. Public/authored checks after the saved update: 8 configurable-state tests, 16
normalization tests, 7 ordered-key tests and 6 OPM-to-OPNA tests passed (37 total).
MUC generation verified reported 8ms=352 and 12ms=529 thresholds, bypass output,
source Segment retention, decision CSV contents and short release-tail retention.
`git diff --check` passed; local_only was not executed.


### Independent FM state duration (supersedes combined 8ms setting above)

- `--fm-state-ms` / `fm_state_ms=1` now controls ordinary internal FM state
  filtering independently. `--normalization-ms=8` retains whole-gate/clock scope.
- Wired native MUCOM conversion and batch CLI; native OPNA and mapped OPM share
  both the selection and clock-planning threshold. No-normalize bypass remains.
- Nondefault FM-state values are rejected by the separate MUCOMSX MSX-source
  route; inherited MDX/MGS copied frontends are unchanged.
- User owns local_only duration sweeps. Do not run 1/2/4/8ms comparisons or add
  public fixtures. Only focused existing unit checks are intended next.

- Saved implementation before checking. Focused validation: 6 existing pure
  state-selection unit checks passed, API defaults verified (FM 1ms versus
  normalization 8ms), four changed Python entry/modules compiled, canonical and
  batch CLI help expose the new option, and `git diff --check` passed. No VGM
  fixture conversion or duration sweep was run for this independent-option update.

Additional check: public volume_sweep VGM -> inherited MGS MML succeeded with --dump-passes. MDX-only compatibility options are no longer exposed. scripts/README.md remains user-owned and contains historical MDX tool entries for the user to edit.
