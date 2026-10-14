# VGM → Musical Segments → Structured MUCOM88 MML

## Project Knowledge & Design Principles

This document records enduring principles for `mucom88_vgm2mml`, inherited
where applicable from `x68k_vgm2mml` and `msx_vgm2mml`. It is a guide to
conversion boundaries, not a complete implementation inventory.

Current support, compiler/runtime profiles and constraints are in
[MUCOM musical projection](mucom_musical_projection.md). Focused source
specifications and normalization contracts are linked from the relevant
sections below. Presence of copied modules is not proof of connected support.

Distinguish confirmed specifications, implementation decisions, measurements,
hypotheses and unresolved questions. Do not promote a fixture observation,
test expectation or experimental heuristic to a project invariant.

Keep implementation history and measurements in existing external field notes;
keep this file small enough to read before changing conversion behavior.

---

# 1. Project Goal

`mucom88_vgm2mml` reconstructs inspectable, editable and structurally
meaningful musical representations from VGM sound-chip data.

**Source-derived Musical Segments are the primary deliverable. Musical
MUCOM88 MML is an output of that analysis, not its definition.** This is
shared with `msx_vgm2mml` and `x68k_vgm2mml`.

The default native route is:

```text
YM2608 VGM → Native State / Events → Segments / PCM IR
  → Musical interpretation / Structural analysis
  → YM2608 target projection → Musical MUC / required PCM assets
```

Source and target are independent axes. Retain inherited OPLL, OPM,
PSG/SSG, SCC and MSM6258 analysis; their presence does not prove completed
MUCOM88 projection. OPNA being the first native route does not make the
architecture OPNA-only. Diagnose unsupported source-to-target combinations.

Actual VGM command usage determines source selection. An unused clock is
not a routing request. Retain inactive compatibility initialization in the
inventory; diagnose unsupported active chips, combinations and streams.

Channel allocation, voice conversion and capacity exclusions belong to the
target. Preserve clocks, prescalers, event/sample order and source evidence.

Canonical conversion generates musical MUC and required PCM representations
or assets. Compiler bank packaging and MUB compilation are later steps.
PCM IR alone is not a bound, compilable bank. Matching stems do not establish
ownership of neighboring reference MUB/PCM files. Remove generated assets
only when recorded ownership, saved path and content identity match;
preserve unrelated files and avoid overwriting them.

Explicitly supported inherited MDX/MGSDRV outputs are compatibility routes.
Register replay, compilation and similar sound support validation; useful
musical structure with inspectable transformations remains the goal.
The implementation need not mirror the diagram one-to-one; do not refactor
merely to make module boundaries match it.

---

# 2. Canonical Frontend

`vgm2mml.py` is the canonical user-facing conversion entry point. Normal
conversion capabilities belong in its pipeline, with explicit options where
needed and specialized modules behind the interface.

The default output contract is musical YM2608 MUC with required PCM handling.
An intended contract is not proof that every feature is implemented.
`--target`, where supported, selects output format, not source chip or
synthesis algorithm. Verify actual options/aliases against implementation.
Source routing, projection, fidelity policy, notation and diagnostics differ.

Scripts may wrap canonical conversion for batch export, compilation,
experiments, diagnostics, regressions, fixture preparation and comparison.
They must not silently become independent production converters.
A new target or structural interpretation does not justify another frontend.

---

# 3. Primary Rule: Intermediate Results Are First-Class Artifacts

Generating inspectable intermediate results is a primary project
requirement, not merely a debugging aid.

A human should be able to determine, where relevant:

* which source register event caused a musical event;
* what chip state existed at that point;
* where Key-On/Key-Off or trigger edges occurred;
* which source pitch/volume/instrument/waveform values were present;
* when an event became an interpreted Segment;
* when musical structure was inferred from Segments;
* which information was changed or discarded by target projection.

CSV or equivalent pass output should favor human traceability over
compactness. Redundancy is acceptable when it makes an analyzed event
self-contained.

> Redundancy that improves traceability is acceptable.
> Loss of source information for structural elegance is not.

Do not remove, merge, split, normalize, or reshape an intermediate
representation merely because doing so makes final output cleaner or
makes a regression test pass.

**Tests are evidence, not the objective.** If a test conflicts with the
information-preservation model, first determine whether the
implementation, the test expectation, the structural interpretation, or
the target projection is wrong.

When an intermediate schema must change, explain the semantic reason and
what information is added, removed, or reinterpreted.

---

# 4. Transformation Boundaries

Keep the following responsibilities separate. A module may serve more than
one stage, but the meaning and provenance of each representation must stay
visible.

## 4.1 Raw Register Writes

Retain source commands, operands, timestamps and write order. Avoid musical
interpretation here.

## 4.2 Chip-Specific State / Events

Reconstruct effective native state, including latches, operator masks,
shared controls and playback evidence. Different chips need different
schemas; do not invent a lowest-common-denominator register model.

## 4.3 Inspectable Analysis Passes

Derive meaning progressively and preserve input/output evidence. Repeating
fields for human inspection is acceptable; passes are not disposable text.

## 4.4 Musical Segments

Segments are interpreted objects or state/sounding intervals with explicit
start/end, continuity, retrigger, waveform or rhythm identity where justified.
A Segment is not a raw write, and not every interval is a complete note.
Do not invent event-stage facts that require later interpretation.

Split/merge source Segments only for a justified source-interpretation
change, never solely for notation, compression, buffer limits or an oracle.

## 4.5 Musical Interpretation / Structural Analysis

Reconstruct sounding units with ordered internal control trajectories, then
continuity, phrases, patterns, loops, macros and related gestures. Source
Segment boundaries need not be note/tie boundaries; several Segments may
contribute to one gesture. Keep source events and membership mappings.

Whole-gate grouping must preserve partial Key transitions, retriggers,
release evidence and chip-specific exceptions. Released parameter writes
can still affect an audible envelope. Do not force every source into a
binary FM gate model or erase zero-time actions.

## 4.6 Target Projection / Rendering

Select target timing, pitch/control spelling, allocation, banks, loop/macro
syntax and compiler/runtime constraints. Render projected musical structure,
then compact text while preserving projected semantics.

Keep immutable native evidence, musical interpretation and target views
separate. Delay unavoidable loss and expose it with source-linked diagnostics.
Target convenience must not rewrite source history.

---

# 5. VGM Is a Register Event Stream, Not a Score

A VGM contains register writes and timing information, not conventional
musical notation.

Potentially meaningful information includes:

* Key-On / Key-Off timing
* KC/KF, F-number, BLOCK or tone-period changes
* instrument and volume changes
* operator parameter changes
* mixer, noise and envelope state
* SCC waveform changes
* retrigger behavior
* pitch changes and portamento-like behavior
* register-write ordering
* repeated writes that appear musically redundant

Do not aggressively merge or normalize events merely because they
eventually produce the same note.

A sparse-looking event stream may be correct. Preserve event sparsity
unless there is evidence that it is an artifact.

Conversely, do not assume every register-state change represents a new
musical note.

The transformation from register events to musical structure is an
explicit interpretation stage.

---

# 6. Preserve Evidence and Derived Meaning Separately

Preserve source representations alongside derived physical/musical values:
KC/KF or FNUM/BLOCK with `frequency_hz`/note, tone period with frequency,
waveform bytes with a stable identity, encoded PCM with decoded target data.
Derived values must not silently replace the evidence used to calculate them.

Unknown/unwritten state remains unknown unless an explicit documented reset
assumption applies. Non-applicable fields stay empty/null or chip-specific;
do not invent values to complete a schema.

Distinguish observed writes/addresses, reset assumptions, inferred onset,
nominal playback position/natural end, and target-quantized values. A value
used to spell target output is not a newly observed native source value.

---

# 7. Timing Is Source Evidence

VGM waits are source timing evidence.

Preserve native timing before target quantization.

The shared VGM source clock accumulates wait samples as integer samples
at 44100 Hz. Chips share the same stream origin; do not rebase each chip
at its first write.

Same-sample writes remain ordered.

A physical sub-tick interval must not disappear merely because a later
musical or target projection rounds it to zero.

A source timestamp, target tick, musical duration, and playback-driver
frame are different concepts.

Do not silently substitute one for another.

Tempo, quantization, grid inference, and target duration encoding are
interpretations. They are not source facts.

See `docs/vgm_timing.md`, where retained, for implementation details and
measured limitations.

---

# 8. Key Edges, Retrigger, Continuity, and Zero-Length Events

Retrigger and continuation are distinct.

Retain enough evidence to determine whether:

* a note was newly triggered;
* an existing sounding state continued;
* pitch/state changed while the key remained active;
* an operator mask changed;
* a new Segment is justified;
* a higher-level musical gesture may continue across a Segment boundary.

Do not equate zero target duration with an irrelevant source event.

Same-tick or zero-length events may contain real Key or rhythm edges.

For OPLL, distinguish an inferred musical `onset` from a real source
`key_on_edge`. Volume recovery or another inferred onset is not
permission to restart a hardware envelope.

For OPM and OPNA, preserve partial Key transitions and do not invent a complete
Key-On merely because target notation is easier that way.

Do not merge a real same-pitch Key-Off/Key-On sequence merely because the
resulting pitch is unchanged.

How a target represents an unrepresentable sub-tick event is a
target-stage decision. The source evidence must remain available.

---

# 9. Chip-Specific Durable Semantics

Retain each chip's native identities, pitch encoding, volume direction,
operator/key state, waveform, mixer, envelopes, noise and shared controls.
Register order, effective state and unknown fields must remain inspectable.
Parameter/modulation changes while held are not automatically attacks.

Reconstruct musical continuity before assessing target expressibility.
LFO/AMS/PMS presence is not by itself reason to reject source Segments;
unsupported target behavior needs a specific diagnostic. Compilation cannot
prove that modulation was reproduced.

FM, SSG, rhythm and ADPCM-B are distinct OPNA source domains. Preserve each;
do not silently output FM/SSG while omitting active rhythm or PCM.
Diagnostic register-MML dialects do not define normal musical MUC rules.

Source specifications: [OPNA](opna_segments.md), [OPM](opm_segments.md),
[PSG/SCC](psg_scc_segments.md), [OPNA SSG](opna_ssg.md),
[OPNA ADPCM-B](opna_adpcm.md). Broader schema/OPLL requirements are in
[Segment requirements](segment_intermediate_format.md); proposed schema
capability is not a claim of implemented support. MUCOM rules and current
limits belong in [target projection](mucom_musical_projection.md).

---

# 10. Rhythm Semantic Layer

A common drum vocabulary describes musical identity, not shared synthesis
implementation. Retain source names, trigger/key edges, timestamps, pitch,
volume and pan where applicable. Derive durations only when justified.

Overlapping percussion hit/dump/control actions are not one serial held
note. Preserve zero-time actions and identify driver-generated writes;
compilation does not show that envelope phase survived.

The vocabulary and source/Segment distinction are specified in
[Segment requirements](segment_intermediate_format.md#6-add-a-semantic-rhythm-segment).
Actual OPNA action projection is in [MUCOM projection](mucom_musical_projection.md).

---

# 11. Source Structure, Patterns, Envelopes, and Compression

Keep reversible source structures and mappings from candidates, occurrences,
envelopes, loops and macros to original Segments/events. A repeated candidate
is structural evidence, not permission to rewrite source Segments.

Search both between sounding units and within held/released trajectories.
Distinguish new attacks from held controls even when state values match.
Do not fabricate attacks when splitting at a target song-loop marker.

Keep native catalogs/trees alongside separate target-feasible views. Every
selected repeat must preserve native meaning and expand to the same ordered
projected commands, including initialization, zero-time actions and ties.
Record each view's own projection and which one was selected.

Compression must not introduce note boundaries, attacks, resets or timing
changes. Software-envelope extraction must not invent unobserved tails;
source volume trajectories and attack/restart semantics remain authoritative.

Count/depth limits, setter compaction and macros are target/compiler-specific.
Compose inner/outer/duration repeats before target limits; retain pre-limit
selection and report expansion. Text length and compiled size differ.
Shorter MML is not evidence of better conversion.

Algorithms and reports: [patterns and musical structure](melody_patterns.md).
MUC-specific choices: [MUCOM projection](mucom_musical_projection.md).

---

# 12. Source Loops and Target Loops

Declared VGM loops are source structure. Preserve state and provenance on
replay without inserting Key-On, Key-Off or decoder reset at a jump.
Structural candidates remain reversible, including overlapping alternatives.

Target song loops, finite phrase repeats, startup latency and projected loop
periods are distinct. A finite-song option changes target markers/seam
constraints, retaining source-loop facts and first-traversal EOF timing.
Validators must use the recorded target policy to choose source iterations.

Compiler acceptance, character count, compiled size and musical/source
equivalence are independent dimensions; success in one does not prove others.

[Reader replay](vgm_loop.md) also documents inherited MDX-specific behavior;
that warm-up/PDX policy is not automatically a MUCOM rule. See
[MUCOM timing and loops](mucom_musical_projection.md#時刻とループ).

---

# 13. Source Interpretation vs Musical Restructuring

`vgm2mml.py` preserves source timing and event semantics through native
analysis and Segment construction.

Musical quantization, normalization, continuity reconstruction, pattern
detection, and restructuring belong to explicit later analysis or
target-projection stages.

Keep this distinction explicit:

```text
source-faithful interpretation != intentional musical restructuring
```

A cleaner or more conventional MML representation does not justify
changing native source evidence.

Likewise, source Segment boundaries do not automatically define the
structure or token boundaries of the final MML.

Do not create a parallel conversion frontend solely to implement a
different structural interpretation of the same VGM input.

New structural approaches should operate as explicit stages within the
canonical conversion architecture.

---

# 14. MUCOM88 MML Is a Musical Target, Not a Segment Dump

MUC must represent reconstructed performance, not mechanically serialize
`Segment → fragment → tie → next Segment`. Distinguish state changes,
attacks, continuations, pitch/control changes, terminations and repetitions.
A Segment boundary does not require a note or tie; a tie is target syntax.

Reconstruct musical structure before textual compaction. Explicit target
controls may express meaningful behavior when traceable and justified;
shortening an incorrect flat renderer does not repair missing interpretation.

Reuse established sounding-unit reconstruction, held control processing,
note-length correction, continuity, phrase/pattern and loop processing from
x68k/MSX where semantics apply. Identify actual reuse versus required adapter
work. A fixed grid, fragment emission, one extra modulation command or blanket
modulation rejection does not substitute for connecting that pipeline.

Current MUC syntax, native driver side effects, projection artifacts,
compiler profiles and unresolved CH3/CSM/auxiliary limits are documented in
[MUCOM musical projection](mucom_musical_projection.md).

---

# 15. Target Formats Are Not Canonical Internal Representations

MUCOM88, MDX, MGSDRV and future targets differ in timing, voices, controls,
noise/envelopes, rhythm, PCM banks, ties/gates, allocation, loops and macros.
Do not shape source State/Segments solely around any target's syntax.

When approximating or discarding information, preserve earlier evidence,
report affected source objects, perform loss in projection and never rewrite
the source to pretend it lacked that information. Same-chip source validity,
compiler limitations, missing projection and timeout are different failures.
Prove target commands with native synthetic replay before relaxing guards.

Keep exact duration spelling separate from optional timing correction.
Estimator fit does not prove representability; inspect final boundaries,
intervals and cumulative error against the originals. Failed correction
retains the structured baseline renderer, never a flat Segment dump.

FM/SSG/rhythm/PCM share one target timeline and recorded map for rendering,
pass dumps and validation. Protect required positive intervals and command
order within the declared budget, or reject/fall back explicitly. Collapsed
internal controls are diagnosed separately from lost whole gates.

Nominal pitch/rate correction does not establish detune, envelope, key
scaling, LFO, timer or acoustic equivalence. Keep clocks/register bytes intact.
MDX 4MHz, PDX capacities/KC-KF rules and MGSDRV clocks/macros are not MUC rules.
Historical reference MML informs structure, not an exact MUC text oracle.

Compiler dialect/version, pitch tables, carrier volume, PCM addressing/rate,
banks and runtime/MUB compatibility require explicit target documentation.
Native MUCOM88, Windows and intended MSX/Makoto MUCOMSX/MUBPLAY use need
separate format/memory/clock/runtime checks; this objective is not verified
compatibility for all MUBs and is distinct from MGSDRV output.

See [normalization](output_normalization.md), [MUCOM projection](mucom_musical_projection.md)
and [inherited MGS compatibility](mgs_compatibility.md).

---

# 16. Validation: Correctness Has Multiple Meanings

Keep these objectives separate:

**Register correctness**
Reconstruct source-chip state and behavior accurately.

**Musical correctness**
Represent the intended performance usefully.

**Structural correctness**
Infer continuity, repetition, loops, and other musical organization
without contradicting source evidence.

**Target correctness**
Produce valid target data within target constraints.

Also distinguish:

* audible similarity;
* source behavioral equivalence;
* target command equivalence;
* musical equivalence;
* textual optimization;
* compiled-size optimization.

A final MML file, successful compilation, matching event count, or
passing test suite is not sufficient by itself to prove conversion
correctness.

For non-trivial behavior changes, inspect the relevant intermediate
results before and after the change and validate more than one
representative piece where practical.

Real/reference playback, emulator behavior, compiler roundtrips,
hardware observations, code inspection, and subjective listening are
different kinds of evidence.

Record which kind supports a conclusion.

Export statistics distinguish artifacts, encoded-byte identity, projected
frequency/timing and native load/playback/ending/display. Raw Key requests,
operator edges and compiled MUB note/hold counts are different measurements.
Do not combine them into unsupported fidelity percentages; label unperformed
comparisons explicitly.

Do not overfit a general rule to one song or fixture.

---

# 17. Tests and Fixtures

Use reproducible fixtures with scoped oracles across raw/state evidence,
Segments, musical structure, target projection and output where practical.
A fixture validates only the behavior it was designed and justified to test.

Before changing expectations, identify whether source semantics,
interpretation/Segments, structure, target projection or the oracle is wrong.
Never reshape intermediate evidence solely to satisfy an expected artifact.

Private copyrighted VGM/ROM/WAV/MML material stays out of tracked documents
and fixtures unless explicitly safe to commit. Respect the user's ownership
of private regression; unavailable or unexecuted checks must be reported.

---

# 18. Human-Readable Intermediate Output

Favor diagnosis over compactness: explicit field semantics, native encodings
beside physical values, null/non-applicable fields, timestamps, channels,
source indices, Segment IDs and pattern/occurrence memberships.

`--dump-passes` is a diagnostic contract. Every non-trivial stage needs an
inspectable input/output; do not remove dumps merely because final MML works.
Preserve enough mapping to trace a target event back toward its source.

Schema requirements: [intermediate CSVs](segment_intermediate_format.md#12-intermediate-csvs-must-be-human-inspectable).
Current MUC pass names: [target artifacts](mucom_musical_projection.md#中間成果物).

---

# 19. Development Workflow

For a non-trivial conversion change:

```text
1. Inspect the existing implementation.
2. Identify the conceptual stage that owns the problem.
3. Inspect representative source/intermediate evidence.
4. Explain current behavior.
5. Form a hypothesis.
6. Design the smallest useful experiment or test.
7. Implement the smallest justified change.
8. Run regression tests.
9. Inspect intermediate results again.
10. Validate structural interpretation where applicable.
11. Validate target output.
12. Record durable knowledge in the appropriate place.
```

Do not jump from a failing final artifact directly to reshaping Segments
or earlier passes.

Before changing established behavior, identify why it exists.

When adding a new target or major target behavior:

1. do not create a new production frontend by default;
2. reuse the canonical VGM reader and native analysis stages;
3. preserve existing intermediate evidence;
4. add target-specific projection only after source interpretation;
5. integrate normal user-facing operation through `vgm2mml.py`.

---

# 20. Documentation Placement

| Location | Responsibility |
|---|---|
| `AGENTS.md` | Short always-on operating rules and repository navigation |
| `docs/project_knowledge.md` | Enduring project-wide architectural/semantic principles |
| Focused `docs/*.md` | Current subsystem specifications, constraints, usage and unresolved support |
| External private `field_notes/*.md` | Dated experiments, measurements, fixture findings, regressions and rejected approaches |
| `mistaken.md` | Repeated failures and do-not-repeat rules |
| `handoffs/current.md` | Only the active restart state and next allowed action |

Promote a finding only when its scope and evidence justify a stable principle.
Keep actionable public constraints and their rationale accessible without
private notes; do not hide them as experiment history.

Field-note references mean the external directory configured in `AGENTS.md`,
not a local `field_notes` folder. Check existing notes before adding duplicate
results. Record unavailable access; do not invent missing decisions or paths.

Rewrite/prune handoffs when the active direction changes. Neither handoffs
nor this document are accumulating implementation histories.

---

# 21. Inherited Knowledge and Compatibility Paths

Retain inherited readers, native analysis, Segment generation and explicitly
supported compatibility output. Do not rewrite proven paths for superficial
uniformity. Changing the default target does not invalidate established
musical reconstruction and structural processing.

Share infrastructure for semantic reasons; specialize source-chip meanings
and target behavior. Copied modules do not prove the new entry path actually
executes their musical/normalization stages. Document connected stages and
adapters without claiming complete cross-repository code unification.

Classify inherited knowledge by meaning, not filename/chip name. Moving a
note does not revoke its shared principles. Recover relevant existing notes
before changing associated behavior; unavailable dated evidence does not
invalidate the public principles retained here.

Private notes are not public dependencies. Public maintenance must have
sufficient stable rules; do not invent support status or missing evidence.

---

## 22. PCM Source Evidence and YM2608 Target Projection

PCM source evidence is target-independent: preserve exact encoded bytes,
codec, timing, playback/control events, decoder/reset assumptions and
observed-versus-inferred distinctions. Encoded sample identity, memory
location, playback identity and decoder continuation are different concepts.
Byte equality or nominal duration cannot prove unknown decoder consumption.

Keep source control slices separate from whole playback epochs. Held
rate/level/pan updates are not sample attacks. Nominal source positions and
inferred natural ends form a separate interpretation; target timing/rate
needs its own consumption/exhaustion audit and explicit omission identities.
Memory/address/data/reset/repeat changes require their own semantic proof.

Delta-T, SegaPCM stepping and MSM6258 are distinct runtimes/codecs. Do not
turn PDX or MUC into universal PCM IR or import independent-note/slot/hardware
limits as OPNA source rules. Reuse decoders/structure/normalization only where
their meaning applies; preserve decoded/transcoded target data separately.

Target conversion, capacity, rate/address encoding, bank binding and compiler
packaging belong to projection. IR alone is not playable PCM output.
Strict projection rejects known loss; an explicitly supported best-effort
policy reports source-linked losses without fabricating unknown state.
Generation, compilation and runtime certification are separate outcomes.
Preserve unrelated reference assets.

Contracts: [PCM source and target](pcm_source_and_target.md),
[OPNA ADPCM-B](opna_adpcm.md), [SegaPCM](segapcm_segments.md),
[MUCOM PCM projection](mucom_musical_projection.md#pcm保持中制御と相互運用の境界).

---

# 23. Final Rule

Before simplifying, merging, splitting, normalizing, quantizing, discarding
or reinterpreting data:

1. Preserve the source evidence and expose it in an inspectable pass.
2. Make interpretation explicit and separate it from musical restructuring.
3. Defer unavoidable loss to target projection and diagnose its scope.
4. Verify that a human can trace output behavior back toward the VGM.

Losing that traceability requires strong justification. The design question
is what musical behavior the source supports, what changes at each stage,
and whether the interpretation/loss is intentional. A final target file
alone cannot answer it.
