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
