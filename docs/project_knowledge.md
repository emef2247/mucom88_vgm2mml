> Current implementation (2026-10-14): default YM2608 -> musical MUC with
> native DAT/ADPCM-B banks, batch MUB compilation and scoped native FM checks.
> Source gates and shared structural processing remain separate from target
> timing/commands. See mucom_musical_projection.md for support and limits;
> inherited MDX constraints do not define YM2608 target rules.

<!-- Adapted from the supplied x68k_vgm2mml project_knowledge.md.
Stable common principles retained; target-specific MDX rules adapted or removed.
Document references are lookup candidates, not verified repository inventory. -->

# VGM → Musical Segments → Structured MUCOM88 MML

## Project Knowledge & Design Principles

This document records **stable project principles and durable domain
knowledge** for `emef2247/mucom88_vgm2mml`.

Keep this file small enough to read before changing conversion behavior.
Implementation history, measurements, fixture-specific results, rejected
experiments, and dated observations belong in `field_notes/` or focused
documents under `docs/`.

Not every statement is a formal specification. Distinguish:

* confirmed facts / specifications
* implementation decisions
* measurements
* hypotheses
* unresolved questions

Do not silently promote a hypothesis, test expectation, or
fixture-specific observation into a project invariant.

Export statistics must separate generated artifacts, encoded-byte identity,
projected frequency/timing checks and native load/playback/ending/display.
Raw Key-On/Off requests, operator edges and compiled MUB note/hold counts are
different measurements. Do not combine them into an unsupported fidelity
percentage; explicitly label independent comparisons not yet performed.

---

# 1. Project Goal

`mucom88_vgm2mml` converts VGM sound-chip data into human-readable,
editable, and structurally meaningful musical representations.

**Source-derived Musical Segments are the primary deliverable. Musical
MUCOM88 MML is an output of that analysis, not the definition of it.**
This principle is shared with `msx_vgm2mml` and `x68k_vgm2mml`.

The default output is **musical MUCOM88 MML (`.muc`) for YM2608/OPNA**,
with required PCM representations/assets. The default native route is:

```text
OPNA / YM2608 VGM
        ↓
Native OPNA Analysis
        ↓
Musical Segments
        ↓
Musical Interpretation / Structural Analysis
        ↓
YM2608 Target Projection
        ↓
MUCOM88 MML + required PCM assets
```

Source and target are independent axes. Retain inherited OPLL, OPM,
PSG/SSG, SCC and MSM6258 source readers and Segment/PCM analysis.
Their presence does not prove that their MUCOM88 projection is implemented.
Do not redefine the architecture as OPNA-only because OPNA is the first
native route. Diagnose unsupported source-to-target combinations explicitly.

Actual VGM command usage determines source selection. An unused clock
is not a source-selection request. Diagnose unsupported active chips,
combinations and streams rather than silently omitting them. Keep inactive
compatibility initialization handling explicit in the source inventory.

Channel allocation, voice conversion and capacity exclusions are YM2608
target constraints. Retain source evidence and allocation diagnostics;
never rewrite source Segments. Preserve native clocks, SSG prescaler evidence
and event/sample order before target projection.

Canonical conversion produces musical MUC and required PCM representations
or assets. Compiler-specific bank packaging and MUB compilation are later
steps. PCM IR alone is not a bound, compilable bank. A matching filename stem
does not establish ownership of adjacent MUB/PCM reference files. Remove
previous generated assets only when recorded ownership, saved path and
content identity match; preserve unrelated files and avoid overwriting them.

Inherited MDX/MGSDRV outputs, where retained and explicitly supported, are
compatibility paths, not the default target or the design center.

The goal is not merely similar sound, compilation or passing tests.
Reconstruct useful musical structure while preserving inspectable source
evidence and transformations. Register replay/roundtrip is supporting
validation, not the purpose of this project.

Conceptually:

```text
VGM
 ↓
Raw Register Writes / PCM Evidence
 ↓
Native Chip State / Events
 ↓
Inspectable Analysis Passes
 ↓
Musical Segments / PCM IR
 ↓
Musical Interpretation / Structural Analysis
 ↓
Target Projection
 ↓
MUCOM88 MML / explicitly retained compatibility outputs
```

The implementation need not mirror this diagram one-to-one.
Do not refactor merely to make module boundaries match it.

---

# 2. Canonical Frontend

`vgm2mml.py` is the **canonical user-facing conversion entry point**.

Normal conversion functionality should be reachable through
`vgm2mml.py` using explicit source/target/output options where necessary.
The default output contract is musical MUCOM88 MUC for YM2608 with
required PCM handling. This is the intended behavior, not a claim that
current implementation already satisfies it. If `--target` is supported,
it selects an output format, not a source chip or synthesis algorithm.
Document actual option names and aliases from implementation rather than
importing MDX CLI assumptions. Source routing, projection model, fidelity
policy, notation and diagnostics are separate concepts.

Target-specific scripts under `scripts/` may exist for:

* experiments;
* diagnostics;
* regression analysis;
* fixture preparation;
* comparison;
* one-off development tools.

They must not silently become independent production conversion
frontends.

Do not create a second normal VGM conversion entry point merely because
a new target or interpretation is being implemented.

If target-specific logic becomes part of normal conversion, integrate
that capability into the canonical `vgm2mml.py` pipeline while keeping
target-specific implementation details in appropriate modules.

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

Keep these responsibilities conceptually separate.

## 4.1 Raw Register Writes

Preserve what was written to the source chip, when it was written, and
ordering between writes.

Avoid musical interpretation here.

## 4.2 Chip-Specific State / Events

Reconstruct the meaning of register writes using the native semantics of
the source chip.

Examples:

* OPNA: FNUM/BLOCK, frequency latches, operator Key state and shared controls
* OPM: KC/KF, operator parameters, partial Key state and shared controls
* OPLL: FNUM, BLOCK, instrument, volume, Key state
* PSG/SSG: tone period, mixer, noise, fixed/hardware envelope state
* SCC: frequency, volume, waveform state
* rhythm modes: trigger and per-instrument state

Do not force different chips into a lowest-common-denominator state
schema.

## 4.3 Inspectable Analysis Passes

Analysis may progressively derive higher-level information. Later passes
may repeat earlier fields when this improves human inspection.

A pass is not disposable debug text.

## 4.4 Musical Segments

Segments represent interpreted musical objects or sounding intervals.

This is where concepts such as:

* start/end;
* duration;
* retrigger;
* continuity;
* note identity;
* waveform identity;
* rhythm identity;

may be derived.

A Segment is not a raw register event.

Do not invent information at the event/state stage that can only be
inferred during Segment construction.

Do not split or merge Segments solely to accommodate target notation,
compression, buffer limits, or a regression oracle. Such operations
belong to a later representation unless they reflect a justified change
in musical interpretation.

## 4.5 Structural Analysis

Structural analysis operates **after source semantics and musical
Segments have been reconstructed**.

Its purpose is to discover useful musical organization such as:

* note continuity;
* repeated phrases;
* source loops;
* candidate musical loops;
* repeated control patterns;
* reusable macros;
* sustained musical gestures;
* higher-level relationships between adjacent Segments.

Structural analysis must not rewrite source history.

A Segment boundary is evidence about interpreted source behavior. It is
not automatically a required MML note boundary.

Likewise, several Segments may contribute to one higher-level musical
gesture when continuity is justified.

Structural analysis should preserve mappings back to the Segments and
source events from which the structure was inferred.

## 4.6 Target Projection

The target stage decides how interpreted musical information and
structure can be represented in MUCOM88 MML for YM2608, explicitly retained
compatibility formats, or future targets.

Target limitations and conveniences must not rewrite source history.

Information loss should occur as late as practical and should remain
inspectable.

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

Whenever practical, preserve both:

1. the source-chip representation; and
2. the physical or musical quantity derived from it.

For example, Yamaha FM pitch analysis may retain:

```text
KC / KF
```

or, for chips using that representation:

```text
FNUM
BLOCK
```

alongside:

```text
frequency_hz
musical_note
```

For PSG, retain source tone period when deriving frequency.

For SCC, retain waveform data or a stable waveform identity.

Derived values must not silently replace source evidence.

Unknown or unwritten source state must remain unknown unless an explicit,
documented reset assumption applies.

Do not fill absent fields with invented values.

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

## 9.1 OPM / YM2151

OPM/YM2151 is an inherited source-analysis domain, not the default target.

Native OPM analysis must preserve OPM semantics rather than invent
OPLL-like FNUM/BLOCK or preset voices.

Preserve where relevant:

* all four operator identities;
* KC/KF;
* Key-On/Key-Off state;
* partial operator Key masks;
* algorithm and feedback;
* operator parameters;
* pan;
* PMS/AMS;
* LFO and shared controls;
* noise state;
* same-time transitions;
* register ordering;
* unknown/unwritten state.

Parameter changes must not create artificial Key-Ons.

A held OPM note may contain multiple source-state changes without
representing multiple musical attacks.

Segment boundaries created by parameter changes must therefore not be
translated mechanically into separate target notes joined by ties.

MUCOM88 rendering should consume interpreted musical continuity and target
projection, not merely serialize each Segment independently.

Retain `docs/opm_segments.md` where available for source semantics. Historical
MDX documents can supply shared design evidence, not MUCOM88 syntax.

## 9.2 OPLL / YM2413

Pitch uses FNUM and BLOCK; preserve both when available.

OPLL volume direction is inverted relative to conventional loudness:

```text
VOL = 0   -> loudest
VOL = 15  -> quietest / near silence
```

Register writes need not arrive in convenient note-oriented order.
Reconstruct effective state without assuming all parameters precede
Key-On.

User-defined patch registers are meaningful source data. Do not silently
replace them with perceptually similar presets.

OPLL rhythm trigger edges and channel 6--8 pitch state should be
preserved before target projection.

An inherited MGSDRV layout may differ between melodic and rhythm modes.
MUCOM88 rhythm mapping requires its own target decision. Neither target
layout may delete source Segments.

## 9.3 OPNA / YM2608

OPNA/YM2608 is the primary native route and the default target chip.
Native analysis keeps FNUM/BLOCK, frequency-latch evidence, per-operator
parameters and Key edges in its own source IR. Do not project into OPM
merely to construct source Segments. Register-bank operator order and
key-mask order differ. Committed frequency is not the last upper-register
write; retain both write evidence and effective pairs.

Retain algorithm/feedback, operator identity, partial Key masks, pan,
PMS/AMS, LFO/shared controls and unknown/unwritten state where relevant.
Parameter/modulation changes while held do not imply new attacks.
Presence of LFO, AMS or PMS is not itself a reason to reject the whole
musical conversion: first reconstruct sounding units and internal controls,
then assess target expressibility and explicit loss/unsupported policies.
Do not claim unsupported modulation has been reproduced because MUC compiles.

FM, SSG, rhythm and ADPCM-B are distinct source domains. Retain their
native evidence separately and document actual support per route.
Do not silently emit FM/SSG while omitting unimplemented rhythm or PCM.
Retain source SSG prescaler semantics before target pitch/timing projection.

MUCOMVGM register-MML is an optional diagnostic route for interpretation.
It is not normal musical MUC. Its virtual timing and command/pitch/PCM
conventions must not define native MUCOM88 output. MUB-to-VGM replay checks
specific target properties; it does not replace musical/structural analysis.

See `docs/opna_segments.md` and `docs/mucom_musical_projection.md` where
retained. Support-status statements require implementation evidence and
must not narrow the project's architectural purpose.

## 9.4 PSG / SSG

PSG state is not "OPLL with different parameters."

Preserve tone period, tone/noise enable state, noise period, mixer state,
fixed volume, and hardware envelope state as required for interpretation.

Noise and hardware envelope behavior are musical source data.

If the target needs IDs, macros, reconstructed commands, or conversion
to another chip family, retain decoded source state in an earlier pass.

## 9.5 SCC

Programmable waveform data is first-class source information.

Preserve waveform bytes or a stable identity, timing of waveform changes,
channel use, pitch, and volume where relevant.

Exact equality may be used for stable deduplication; stronger
normalization requires explicit justification.

---

# 10. Rhythm Semantic Layer

Where a common musical rhythm vocabulary is useful, use:

```text
BD
SD
TOM
HH
CYM
RIM
```

This vocabulary describes musical meaning, not source implementation.

Source-specific terminology may still be retained in diagnostic/source
fields.

Rhythm event/state representations should preserve source trigger state,
timestamp, volume, source pitch fields and pan where applicable.

Segment construction may derive sounding intervals later.

Do not infer duration earlier than the available evidence justifies.

---

# 11. Source Structure, Patterns, Envelopes, and Compression

Pattern detection and compression must operate without destroying the
information-preservation pipeline.

A repeated candidate is evidence of structural similarity, not automatic
permission to rewrite source Segments.

Prefer reversible structural plans and retain mappings from patterns,
occurrences, envelopes, loops, or macros back to the original
Segments/events.

Target loops/macros may be selected only when their expansion preserves
the required target command semantics.

Do not introduce new note boundaries, attacks, state resets, or
quantization merely to improve compression.

For software-envelope extraction, do not invent unobserved tails.

Source volume trajectories and attack/restart semantics remain
authoritative.

Target output size and driver/compiler limits are real constraints, but
solving them belongs at or near target projection.

Shorter MML is not evidence of a better conversion.

See `docs/melody_patterns.md` and focused field notes for current
algorithms and experimental results where those documents are retained.

---

# 12. Source Loops and Target Loops

Declared VGM loops are source structure and must be preserved without
inventing a Key-On at the loop boundary.

Structural loop candidates should remain reversible until target
selection.

Overlapping or nested alternatives may be useful to later macro or loop
selection.

Compiler acceptance, MML character count, compiled size, and
musical/source equivalence are separate validation dimensions.

Success in one does not prove the others.

See `docs/vgm_loop.md`, where retained, for VGM loop details.

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

MUCOM88 MML generation must not be implemented as a mechanical one-to-one
serialization of Segment rows.

In particular:

```text
Segment -> MML fragment -> tie -> next Segment
```

is not a general model of musical reconstruction.

Segments exist primarily to preserve interpreted source state and
boundaries. MUCOM88 MML should instead represent the reconstructed musical
performance as naturally and structurally as the available evidence
allows.

The MUCOM88 path should distinguish at least:

```text
source/state change
musical attack
musical continuation
pitch change
target control change
note termination
structural repetition
```

A source parameter change during a held note does not automatically
require a new note token.

A Segment boundary does not automatically require a tie.

A tie is a MUCOM88/MML representation choice, not a generic relationship
between adjacent Segments.

When ordinary MUCOM88 notation cannot faithfully express a meaningful source
behavior, explicit target controls may be used where justified. Such
controls must remain traceable to source evidence.

Musical structure should be reconstructed before textual compaction.

Do not optimize an incorrect flat representation merely because it can
be made shorter with loops, ties, or macros.

**Reuse established musical interpretation and structural processing from
x68k_vgm2mml / msx_vgm2mml, adapting target projection and notation to MUCOM88.
Repository separation does not justify a new flat Segment renderer.**

Where native Key evidence permits it, interpret Key-On through Key-Off as
one sounding unit with internal pitch, volume and control trajectories.
Preserve partial Key transitions, retriggers, release evidence and chip-specific
exceptions. Keep source Segments intact; build a separate musical view linked
to them. Do not force every source chip into a binary FM gate model.

Connect this view to the established note-length correction, continuity,
phrase/pattern and loop processing where semantically applicable. Identify
which stages are reused and which require MUCOM-specific adaptation.
A fixed global grid/tempo and fragment-by-fragment emission do not substitute
for that integration. Do not solve a missing musical pipeline only by adding
one modulation command or rejecting all Segments containing modulation.

---

# 15. Target Formats Are Not Canonical Internal Representations

MUCOM88 MML, MDX MML, MGSDRV MML and other targets have different constraints.
Target-specific choices include duration encoding, voices/control commands,
noise/envelopes, rhythm, sample banks, ties/rests/gates, loops/macros,
channel allocation and runtime timing. Do not shape source State/Segments
solely around one target's syntax.

When a target approximates or discards information:

1. preserve source information in an earlier inspectable stage;
2. report approximation and affected source objects;
3. perform loss in target projection;
4. do not rewrite earlier representations to pretend the source lacked it.

Reuse proven shared musical/structural processing when native semantics
are preserved. Adapt MUCOM88 syntax/compiler/runtime behavior explicitly.
Do not import MDX macro exclusions, PDX limits, KC/KF ranges, 4 MHz target
clocks or MGSDRV command behavior as MUCOM88 rules.

Exact duration spelling and optional target timing correction are separate.
Retain source times, report actual timing errors and check cumulative errors
against original boundaries. Estimator fit alone does not establish
representability. Failure to adopt correction must retain the structured
musical renderer with documented baseline timing, not select a Segment dump.

FM, SSG, rhythm and PCM must share a coherent target song timeline.
Keep source samples, musical timing and driver timing separate. Use validated
MUCOM-specific bounds/options/fallbacks rather than blindly copying MDX
normalization constants or short-note defaults.

Compiler dialect/version, pitch tables, carrier-volume mapping, rhythm
commands, PCM rate/address conventions, banks and MUB limits belong in
focused MUCOM documentation with supporting evidence. Compilation,
selected MUB-to-VGM checks and actual playback are separate results.
Native and diagnostic dialects must remain distinct.

Where retained, MGSDRV syntax references remain compatibility references:

* `https://p.gigamix.jp/mgsdrv/MGSDR320.TXT`
* `https://z80.msx.click/index.php?title=MGSDRV_MML_11_JP`

They do not specify MUCOM88 syntax. Historical MDX reference MML can supply
musical structure evidence, not an exact-text oracle for MUC output.

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

Do not overfit a general rule to one song or fixture.

---

# 17. Tests and Fixtures

Fixtures should make specific behaviors reproducible and, where
practical, cover the relevant layers:

```text
source VGM
raw/state trace
analysis pass
Segments
structural interpretation
target projection
target output
```

Do not blindly update expected artifacts when a regression test fails.

First determine whether:

1. the implementation violated source semantics;
2. the interpretation/Segment logic is wrong;
3. the structural interpretation is wrong;
4. the target projection is wrong;
5. the test expectation encodes obsolete or incorrect behavior.

**Never change an intermediate representation solely to satisfy an
expected artifact.**

A fixture is an oracle only for the behavior it was designed and
justified to test.

Private copyrighted source material must not be copied into tracked
documentation or fixtures unless explicitly safe to commit.

---

# 18. Human-Readable Intermediate Output

Intermediate output should favor diagnosis over compactness.

Use explicit names with stable semantics.

Prefer `frequency_hz` for a physical frequency while retaining native
source representations such as:

```text
KC
KF
FNUM
BLOCK
tone_period
```

where applicable.

Do not fill non-applicable fields with misleading values.

Use empty/null values or chip-specific schemas.

Preserve enough timestamps, channel/source indices, Segment IDs,
pattern/occurrence IDs, structural mappings, and other identifiers to
trace target behavior back toward source evidence.

PASS files generated by `--dump-passes` are part of the project's
diagnostic contract, not arbitrary temporary dumps.

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

Keep documentation layered so the highest-priority rules remain visible.

## `AGENTS.md`

Only short, always-on operating rules and repository navigation.

## `docs/project_knowledge.md`

Only stable architectural principles and durable chip/domain knowledge
that should guide future work.

## Focused `docs/*.md`

Current design and usage of substantial subsystems, for example:

* timing;
* loops;
* patterns;
* OPNA and inherited source Segments;
* musical MUCOM88 MML / YM2608 projection;
* PCM banks and compiler/runtime compatibility;
* OPLL rhythm;
* compatibility targets.

## `field_notes/*.md`

Dated experiments, measurements, fixture-specific findings, regressions,
benchmarks, rejected approaches, and provisional conclusions.

A finding may move from a field note into this file only after it becomes
a stable project principle or durable domain rule.

Do not append implementation history to this document merely because it
may be useful later.

## `handoffs/current.md`

Keep only information needed to continue the **current active work**.

Do not use the handoff as an accumulating project history.

When the active development direction changes, rewrite or prune the
handoff so obsolete work does not compete with the current objective.

---

# 21. Inherited Knowledge and Compatibility Paths

Retain inherited readers, native analysis and Segment-generation paths.
Preserve existing compatible outputs where explicitly supported. Do not
refactor proven paths solely for superficial uniformity.

Compatibility does not require MUCOM88 projection to follow historical
MDX/MGSDRV target choices. Changing the default target also does not
invalidate established musical reconstruction/shared structural processing.
Share infrastructure for semantic reasons; keep chip semantics and target
behavior specialized where meanings differ.

**Classify inherited documentation by meaning, not filename or chip name.**
A note mentioning MDX/OPM may record a cross-target architectural decision.
Deleting or moving it does not revoke that decision. Before pruning notes,
retain durable common principles here or in focused shared-design documents.

The supplied diagnostic report identifies these historical recovery candidates:

* `2026-10-07_mdx_musical_output.md`: sounding units with internal pitch,
  volume and control changes;
* `2026-10-08_psg_opm_musical_connection.md`: connect to established musical
  processing rather than serialize Segments with commands and ties;
* `2026-10-10_shared_output_normalization.md`: preserve source Segments
  while correcting note lengths and structuring the output view.

These are recovery candidates, not a claim that the files exist here or
that their full contents have been verified. Recover relevant notes from
the source repository/history or an explicitly configured private notes
repository before changing associated behavior. The durable principles
above remain applicable when dated notes are unavailable.

Private notes need not be public dependencies. Record local lookup locations
in appropriate local instructions; retain enough stable public principles
for developers without private access to maintain the architecture.
Do not invent missing notes, paths or support status.

---

## 22. PCM Source Evidence and YM2608 Target Projection

PCM source evidence is target-independent. Preserve exact encoded bytes,
source timing, playback/control events, decoder evidence and observed-versus-
assumed distinctions. Raw → State → Segment/PCM IR → Target boundaries
remain explicit.

Sample identity uses codec and exact supplied bytes; memory location,
playback identity, timing and decoder continuation are separate. Unknown
consumption/runtime behavior cannot be inferred from byte equality or nominal
duration. Retain encoded source and decoded/transcoded target data separately.

Native OPNA ADPCM-B and MSM6258 use distinct codecs/semantics. Do not import
MSM6258 independent-note eligibility, PDX slot limits or X68000 constraints
as OPNA source rules. Retain explicit memory-initialization assumptions and
unsupported decoder epochs in source observations and target diagnostics.

YM2608 sample conversion, capacity, bank layout, address/rate encoding and
MUCOM compiler packaging belong to target projection. PCM IR must later bind
to actual bank data, sample references and MUC playback/dependency commands.
Do not silently omit PCM or label IR alone as playable output.

Strict projection rejects known semantic loss; explicitly supported
best-effort policies report source-linked losses. They cannot fabricate
unknown source decoder state. Generated assets, compilation and runtime
validation are separate results. Preserve unrelated reference MUB/PCM assets.

Use focused PCM source/target and MUCOM projection documentation where
retained. Historical PDX documents describe a different target.

---

# 23. Final Rule

When uncertain whether to simplify, merge, split, normalize, quantize,
discard, restructure, or reinterpret data:

1. preserve the source evidence;
2. expose it in an inspectable intermediate result;
3. make the interpretation explicit;
4. distinguish source interpretation from musical restructuring;
5. defer irreversible loss until the target stage;
6. ask whether a human can still trace the final result back toward the
   VGM.

If the answer to step 6 becomes "no", the change requires strong
justification.

The central design question is not:

> "How can we make this conversion simpler or make this test pass?"

Nor is it:

> "How can every Segment be represented directly in MML?"

It is:

> "What musical behavior can be reconstructed from the source evidence,
> what information is being changed or lost at each transformation stage,
> and is that interpretation or loss intentional?"

A final target file is not sufficient evidence that the conversion is
correct.

The path from source register stream through interpreted musical
structure to the target must remain inspectable.


---

## 24. Target Timing, Clock Projection and Playback Compatibility

Keep native clock/pitch bytes and original sample timestamps intact.
Handle clock differences in separate YM2608 target projection with explicit
target clock, pitch/rate rounding, range checks and loss diagnostics.
Nominal pitch correction does not automatically correct detune, envelopes,
key scaling, LFO, timers or acoustic behavior.

Output normalization distinguishes complete short sounding gates from short
internal Segment/control slices. Partial, repeated/unclosed Key operations
require conservative handling. Any supported omission policy retains source
State/Segments, PCM bytes and elapsed song time, reports lost events/durations
and uses an explicit output policy. The user-selected MUCOM default is now
8 ms (floor352 samples), implementing the inherited two-part policy below;
MDX hardware multipliers remain separate and are never MUCOM clock rules.

If rounding collapses a protected positive interval, preserve required order
and start/end separation within the declared movement budget, or reject/fall
back explicitly. Check final boundary errors, intervals and cumulative timing.
Store chosen timestamps in a shared projection used by renderers, pass outputs
and validators.

Native MUCOM88, MUCOM88 Windows and intended MSX/Makoto YM2608 playback
require explicit compiler, MUB-format, bank, memory, clock and runtime checks.
The inherited MUCOM document names MUCOMSX/MUBPLAY as the intended MSX route;
this is a compatibility objective requiring separate verification, not proof
that all generated MUB files already work there. This route is distinct from
MGSDRV output in `msx_vgm2mml`.

Use MUB-to-VGM replay where available to check identified target properties.
Keep these checks subordinate to the musical output goal and separate from
listening/native playback evidence.


## 25. Whole gates, target controls and native evidence

Whole FM gates own ordered internal trajectories; source continuity and State
semantics define the gate, not emitted note spelling. Released parameter writes
can affect an audible envelope and belong to source-linked control units. Keep
zero-time native edges in source evidence and distinguish unsupported operator
gates from ordinary held parameter changes.

The shared source-plan solver may be reused across targets. Native repeat syntax,
count/nesting limits and target command equivalence remain renderer decisions.
When structure cannot be emitted, keep its catalog and report expansion.

MUCOM native voice loading, volume and hardware-LFO commands can overwrite
operator fields. Restore projected observed fields in the correct command order;
DAT equality alone does not show the driver retained TL/AM flags. Validate real
compiled native output independently, beyond compilation and generated commands.

Use one target timeline across engines. A collapsed internal control interval is
an explicitly reported timing loss, distinct from a lost whole gate. Diagnostics
and roundtrip validators consume the recorded chosen clock, not a fixed template.
Startup latency and loop-period projection are separate from source boundaries.


## 26. Same-chip projection limits and ordered auxiliary actions

YM2608 source parameters do not become invalid because a target renderer lacks
syntax for them. Distinguish source validity, compiler limitations, missing
musical projection and processing timeout in diagnostics. Preserve source values;
prove target commands with native synthetic replay before relaxing restrictions.

Partial operator masks are internal gate trajectories until a full-off boundary;
source continuity IDs may change inside that whole gate. Preserve zero-time key
order and compare actual native operator edges, beyond positive intervals.

Percussion triggers naturally overlap. Timed hit/dump/control actions are distinct
from one serial held note. Native driver-generated auxiliary writes must be
identified; compiler success does not show they preserved envelope phase.

Inactive PCM supplies and mixer-only metadata need not block FM/SSG rendering.
Boundary-only decoder writes are distinct from positive-time continuation changes.
Native target binding must retain the original codec bytes and source eligibility
observations instead of rewriting the source IR to match target convenience.

Index repeated membership lookups without altering evidence order, zero-time
members or inclusive gate endpoints. Profile actual stage costs; a faster gate
builder does not imply every large raw/state CSV pipeline is now fast.


An explicit finite-song target option disables song-loop markers and seam timing
constraints only at target projection. Retain declared source-loop evidence and
first-traversal EOF timing; phrase-repeat compression is a separate policy.
Validators use the recorded target song-loop policy to choose source iterations.


## 27. Source structure and target-feasible repeat views

Apply the shared source repeat planner inside whole sounding gates and released
control trajectories as well as between musical units. New attacks and held
controls are distinct source actions even when their register state is equal.
A split at the target song-loop marker must not fabricate another attack.

Keep native trees/catalogs and source memberships intact. A separate target view
may add projected relative timing to native equality keys to choose an existing
wider source repeat when a narrower repeat cannot retain target intervals.
Each selected body must still match the native sequence and expand to the same
ordered projected commands. Preserve each tree's own projection report, plus an
explicit record of the selected view. Initial commands, zero-time actions, ties
and loop entry/back edges remain part of semantic validation.

Compose inner, outer and target-duration repeats before enforcing native depth.
Retain the pre-limit selected form and record expansions; pre-limit selections
are different from final emitted repeats. Compiler/driver setter compaction is
target-specific and requires independent effective-state and native replay
checks. Shared physical line formatting carries no chip/timing constraints.
MUC character savings are not proof of MUB data-size savings.


## 28. Shared normalization contract and native target allocation

Share musical policies and source planners, not target clock or notation rules.
The user-selected default normalization bound is8ms, floor352 original samples.
It controls both explicit complete short-gate omission and maximum absolute
source-to-target boundary movement. Full-mask closed isolated OPNA FM pairs can
qualify; partial/repeated/unclosed keys and loop-crossing gates stay retained.
A short internal control slice is never a short whole note. Non-key controls
inside omitted gates and after earlier retained gates both remain eligible.

Use an immutable output-only view while retaining native evidence, timestamps,
Segment IDs, and PCM bytes. Validate source PCM eligibility and bind bank bytes
before omission; independent observed-reset short decoder epochs may qualify,
whereas inferred natural ends and EOF are not observed closures for this policy.
All-omitted output retains finite elapsed song time. Report authorized loss.

Share source attack-clock inference and the ordered integer-domain allocator.
Each target supplies its own exact domains and constraints. MUCOM uses native
Timer-B, origin0 and a C128 adapter for a nominated shared musical fit; do not
import MGS confidence tolerances as output budgets. Protected intervals remain
positive, short rests may coalesce with ordered off/on retained. Adopt policy
and clock atomically, or restore full baseline explicitly. Renderers and native
validators consume the same recorded map and omission identities, including
original source event IDs across repeated source loops.

Retain native structural catalogs alongside an accepted-clock semantic view.
Normalized equality preserves attacks, held actions, voice/pitch/control order,
and source membership; repeat expansion must equal the full projected command
sequence. Chip-specific evidence adapters can differ without replacing shared
planners with unrelated target-text heuristics. Public compiler success alone
cannot show that released-envelope controls or source musical semantics survived.


## 29. Boundary recovery and native driver initialization

An unobserved initial FM pitch is not an invalid clock or automatically a short
whole note. Resolve only a prefix with zero accepted target time, from a complete
observed pitch in the same gate and clock. Preserve source values, identities,
key order and donor evidence. Explicit initial key commands require initialized
target pitch and a tied first positive note without an extra attack. Native
previous-code0 invalidation collides with tied o1c code0; choose another exact
note+detune decomposition in the target adapter, not a source pitch rewrite.

SSG source-loop proof uses observed register assignments with both first-pass
and inherited EOF seeds on the accepted map. Compare meaningful state across
ALL positive intervals: an inactive register at entry can become relevant later.
Retain shared raw controls, zero-time EOF state and source provenance. A restored
same-clock fixed-envelope head can support split/continuation; delayed positive
differences remain unsupported. Phase and cross-part bus ordering need separate
checks; per-channel register proof is not universal waveform equivalence.

SSG native D operates before octave shifting. Software E initialization changes
live envelope phase: configure on attack, silence re-entry or HW->SW transition,
not each held control. HW->SW needs a distinct output unit under this driver.
Avoid overwriting shared noise state from tone-only lanes. Validate native
period/mixer/volume trajectories over every interior interval, not only midpoints.

Persist stage start metadata before work so timeouts retain usable evidence.
Exact integer arithmetic may replace Fraction-heavy candidate loops only with
independent rounding/domain/acceptance parity. Microbenchmarks do not establish
private-song throughput or waive user-owned regression boundaries.


# 30. PCM decoder epochs and live control projection

Retain encoded source samples and immutable native control slices separately
from the whole decoder playback epoch. A held rate/level/pan update is not a
new sample attack. Source-derived nominal playback positions and inferred
natural ends are another inspectable interpretation, never observed addresses
or target-quantized source State. Target time/rate allocation needs its own
position/exhaustion audit; omitted epochs must be identified as omissions.

Chip-specific runtimes (SegaPCM address stepping versus Delta-T decoding) stay
separate. Shared provenance/timeline principles do not make MSM6258/PDX or MUC
a universal PCM IR. Reuse the existing source decoders/structure/normalization
where their semantics apply; certify native live controls in the target adapter.
Do not broadly waive an epoch diagnostic without checking changed registers
and bits. Memory/address/data/reset/repeat changes require a distinct proof.

The first target PCM note has decoder-initialization side effects. Protect its
positive interval before live register updates; a zero-clock omitted attack can
otherwise overwrite those updates. Keep installed compiler/replay profiles
explicit, including their measured startup phase and supported MUC dialect.
