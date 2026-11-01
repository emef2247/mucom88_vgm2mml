# Native MUCOMSX conversion checkpoint

This is an explicit `--target mucomsx` route. The default `mucom` route still
means the existing OPNA/MUCOM88 conversion. Native source evidence is retained;
MUCOMSX limitations are target constraints, not definitions of source Segments.

## Interpretation and structure

PSG and SCC use the inherited MSX note extraction, exact volume runs and
pre-envelope structural planner. OPLL uses the inherited continuous keyed
units and `OpllLoopPlan`, including inner Segment repeats and its outer-only
versus inner-plan selection. Exact command expansion authorizes finite loops.
The shared MSX macro candidate kernel is adapted to balanced MUC command trees;
MGSDRV syntax, arbitrary envelope-table packaging and its capacity limits are
not imported into the new target.

Raw commands, source boundaries, native Segments, interpreted notes, envelope
candidates, source trees, target units, pitch, assessments and macro decisions
remain inspectable. Oversized target candidates are retained as diagnostic
text instead of being published as a successful MUC in the pinned DOS profile;
the separate native SCC5 profile records its own capacity warnings below.

## PSG state spelling and macro selection

The connected PSG route first reconstructs complete sounding units and their
observed volume runs with the inherited MSX interpretation. Pitch events,
volume recovery and hardware-envelope resets retain their established note
boundaries. Several source Segments may belong to one note; no extra attack is
created to shorten MUC. Source Segments, interpreted units and volume runs are
retained before target normalization.

Rendering follows the MSX ordering: source structure candidates, self-contained
target commands, sequential state spelling, exact finite-loop projection, then
macro selection. Repeated P/D/o and software-v assignments are omitted only
when that part already has the required emitted state. One-step octave changes
may use `>`/`<`; exact shorter divisor lengths use the generated `C128` clock.
State is forgotten at the song-loop entry, including a held continuation.
Every E setup, shared noise-period write, hardware-envelope period/shape action
and tie remains ordered. A hardware-envelope shape invalidates the remembered
software volume so the next `v` still restores ordinary volume mode.

`*.mucomsx.units.csv` retains `command` before state spelling and adds
`rendered_command`. `*.mucomsx.psg_render.json` records every before/after unit,
removed setters, loop-entry resets, and equality of effective state at timed
events plus ordered E/noise/hardware-envelope/tie actions. This generated-subset
proof complements, rather than replaces, native compiler/runtime checks. Source
structure and macro expansion continue to have separate exact token proofs.

Macros use the adopted MSX portfolio: retain the atomic baseline and compare
structured `(candidate width, first candidate rank)` allocations `(24,0)`,
`(64,0)`, `(64,1)`, `(64,2)`. Search the merged track and balanced loop children,
allow at most 32 definitions and 512 characters per searched body, and select
the smallest combined definition/track text before physical line wrapping. The baseline
wins equal-size choices. Candidate selection protects tie boundaries, song-loop
markers and overlapping ancestor/child occurrences; definitions contain no
macro calls. Exact repeated-prefix pruning skips only singleton prefixes, whose
extensions cannot repeat; it does not impose a heuristic candidate cap. Exact expanded tokens and loop trees must agree. The compiler's
1023-byte physical-line limit is a separate constraint. Macro shortening is a
source-text benefit, not a promise of smaller compiled track DATA.

### Software-envelope boundary

The pinned MUCOMSX interface documents six-parameter software-envelope presets
(`al,ar,dr,sl,sr,rr`), rather than MGSDRV's arbitrary tick-by-tick envelope tables.
Consequently the observed PSG curves remain exact tied `v` trajectories at the
selected target ticks. Literal macros can share matching command sequences;
this is not parameterized envelope playback across arbitrary pitches. The
converter does not fit an ADSR envelope, infer a release tail, or claim that
candidate-curve CSVs are native envelope definitions. All E setups are retained,
including new attacks. A future native-envelope fit needs its own timing and
retrigger proof before replacing explicit controls.

Exact sample-time projection can produce different integer durations for
source repetitions. Such repetitions stay visible in source structure but are
expanded when target commands differ. This is a remaining difference from the
MGS 60 Hz timing projection; these spelling/macro changes do not change source
timing to force a repeat.

## Source use and initialization

Select actual active source commands rather than clock declarations. Exported
MSX files can contain initialization for unused engines. The existing source
inventory identifies compatibility initialization; raw writes and native
initialization evidence remain available. An undeclared SCC reset with no
nonzero volume is not treated as a sounding SCC part. This does not permit
silently dropping an active unknown engine or malformed input.

## Native SCC5 profile (2026-10-29)

The default SCC route of `--target mucomsx` now emits the native chip-extension
MUC understood by MUCPlayground/MDPlayer4Mac. The author confirmed that the real
MSX version is still in preparation. The pinned local MSX-DOS compiler is not
claimed to support these commands or containers. Generate MUC for regression:

```sh
python scripts/export_mucom.py INPUT --outdir OUTPUT --target mucomsx --muc-only
```

No `--scc-output` argument is needed for native SCC. The global default target
remains `mucom` for existing OPNA conversion. Native SCC input does not silently
change that target. Blueprint stays plain MUCOM88 MUC with fixed audition voices;
reference VGM retains source chips and is not proof of this MUC's playback.

Ordinary native rows use the existing C128/Timer-B allocation and explicit clock
lengths. The `?XU` compact-stream format is not used because its C192, octave and
length constraints differ. Native-part finite repeats are limited to two nested
levels, expanding deeper target repeats with exact command-equivalence checks.
The macro lexer recognizes emitted `?DB` operands atomically. Existing source
phrase/volume interpretation is reused, without importing MGSDRV `@e` syntax or
60 Hz envelope tables. `--fm-state-ms` does not filter the native SCC trajectory;
it still applies to the explicit OPNA-FM compatibility route.

The legacy 65,536-byte DOS input ceiling is a warning for this new native profile,
not a reason to discard the generated MUC. New runtime limits remain unverified;
MUC generation is not a guarantee of compilation. Physical line and wave-pool
limits remain enforced. The mixed native SCC profile preserves interpreted PSG/SCC partitions, including
short internal register-transition slices. Target timing must represent their
positive intervals; the generic legacy short-partition deletion is not applied.
This does not join or tie independently interpreted attacks. Other profiles keep
their existing normalization policy.

`*.mucomsx.scc_waves.csv` preserves wave IDs, source bytes and signed values.
`*.mucomsx.units.csv` records source lineage, volume runs and emitted commands;
pitch CSV adds native `target_chip`, `target_period`, frequencies and clamp
status. The inherited `projected_opna_period` column remains for compatibility
but holds the SCC period on native SCC rows. `native_scc` and routing reports
identify the native profile and its pending MSX runtime validation.

The fixed pitch-table values and direct-period semantics were checked against
[NativeNotePeriods.cs](https://github.com/ToughkidDev/MDPlayer4Mac/blob/MDPlayer4Mac/macos/MucoNative/NativeNotePeriods.cs)
and the upstream packed standard-player/compiler implementation. See the
[official extension manual](https://github.com/ToughkidDev/MUCOMSX/blob/main/MUCPlayground/manual-data.js)
for `SCCA`–`SCCE`, `?WD`, `?DB` and ordinary-row syntax.

## Native routing

- PSG: internal PSG parts MSXD/E/F by default.
- SCC: native `SCCA` through `SCCE` by default. Content-deduplicated signed
  32-byte waves are emitted as `?WD` definitions (at most64). Original linear
  volume runs become held `v` controls with a flat native envelope; arbitrary
  curves are not replaced with a guessed `?EP` profile. Native SCC period-table
  notes plus a direct `?DB` offset preserve the projected physical period.
  No OPNA FM channels or FM voice DAT are required for this route.
- `--scc-output opna-fm`: explicit compatibility route using one four-carrier
  OPNA additive FM channel per SCC part, allocated in A/B/C/H/I/J order. Harmonic,
  phase, DC, envelope and gain losses remain reported. Native source evidence
  and the default internal PSG route are unchanged.
- OPLL: native MSXA/B/C/H/I/J and MSXG percussion. The pinned MUCOMSX native
  MUC interface exposes six melody parts plus rhythm. The YM2413 chip itself
  is not limited to six melodies. Source channels 7–9 remain in the IR;
  conversion stops if they require output. Only explicit `--force` permits
  diagnosed omission. No automatic OPLL-to-OPNA substitution is performed.
- `--opll-rhythm layered`: retain native percussion and add OPNA rhythm G.
  The attack-only source interpretation and target level/release limitations
  are reported. The default is native percussion alone.

MUCOMSX accepts OPNA-oriented FM DAT voices for native MSX-MUSIC and selects
one of its 15 OPLL ROM presets. This is distinct from passing source YM2413
registers or a custom patch through unchanged. The converter retains source
patch trajectories and reports that approximation. Native exported OPLL pitch
was checked with authored patterns; that is not proof of custom-patch,
envelope, phase or complete musical equivalence.

OPLL melody enhancement on OPNA, and a future reference-based preset mapping,
are deferred. No Nuked-OPLL implementation or preset data has been copied for
that deferred work. The current reconstructed preset data comes from the
BSD-licensed ymfm reference, with its notices retained in the helper module.

## Optional PSG enhancement and DCSG

`--psg-enhance-cents N` explicitly adds an OPNA SSG copy of native PSG. No
universal detune is inferred from the reference material. Periods are quantized.
This option uses unprefixed D/E/F for the additional OPNA SSG layer. SCC uses
OPNA FM independently of this option. The six OPNA FM channels remain a real
resource limit; a five-voice SCC source normally consumes five of them.

Single/dual SN76489-family decoding retains latches, raw flags, independent
chip state and all four source voices per chip. The derived AY views reuse the
same note and structural interpretation. The current dual-engine projection
uses internal PSG for chip 1 and OPNA SSG for chip 2. Noise prefers an unused
tone slot; otherwise tone/noise share a selected channel and its volume.
This is not eight independently controlled AY voices. Noise LFSR, periodic
noise and logarithmic gain differences are diagnosed. Moving a single chip's
fourth noise voice to a separate free SSG channel remains a checkpoint item.

## Timing, gain and validation status

Use one shared target clock and retain exact source sample positions.
Normalization may omit complete short source-time gates before quantization;
source Segments and elapsed song time remain intact. Unfinished terminal gates
are conservatively retained. Positive song duration and protected sounding
intervals must retain positive target duration.

Native software-volume updates can follow their command by one driver tick.
The current timing allocation reserves half the requested error budget for
that observed latency. Compiler-safe ties precede intervening controls; putting
volume before a same-note tie was observed to lose that control during compiler
merging and has been corrected.

SCC-to-OPNA additive gain is a target approximation, not acoustic calibration.
Carrier TL controls follow source software-volume runs while retaining the
selected harmonic ratios. Source waveforms and volume values remain unchanged.
Earlier native SCC/SSGExtend gain measurements do not validate this FM route.

Sixty public MSX source fixtures passed source analysis. Focused authored
PSG/SCC/OPLL patterns compiled and exported through the pinned MUCOMSX tools;
Earlier SSGExtend SCC waveform bytes and a native OPLL pitch correction were
inspected; the SCC waveform route has since been superseded by FM projection.
Broader
MUC generation exposed input-capacity failures in long mixed compositions.
Structured macro improvements recovered some of them. Those measurements
predate the final removal of OPLL-to-OPNA overflow substitution.

The full current round-trip suite is not complete at this checkpoint. Run
`scripts/verify_mucomsx_public.py` with a fresh output directory and an ignored
local runtime profile. It covers the requested public PSG, OPLL, PSG/SCC,
PSG/OPLL and Mutopia collections plus authored native fixtures. Source analysis,
MUC generation, native compile/export and native control comparison have
separate statuses. OPNA projections are identified exclusions, not native
matches. A valid MUB or VGM alone does not establish listening equivalence.

External binaries, BIOS/DOS images, local runtime profiles, private measurements
and generated validation artifacts are excluded from the public repository.

## Rhythm song-loop boundaries

A song-loop marker may split the target interval following a percussion attack.
The second fragment is a tied continuation; no new mask or source attack is
invented there. An actual source attack at the marker keeps its attack commands.
When forced loop omission shortens the emitted timeline, later replay attacks
remain in source evidence but are excluded from target rhythm projection with
original source indices and an explicit exclusion reason. Native compilation
has been checked; exported onset/mask fidelity across the tied loop seam still
requires verification.
