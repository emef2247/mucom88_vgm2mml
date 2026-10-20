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
