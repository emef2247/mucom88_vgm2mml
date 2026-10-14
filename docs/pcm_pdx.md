# Native MSM6258 PCM and PDX

The project uses the name MSM6258 for this chip and its PCM IR. VGM keeps
its existing command `0xB7`, DAC stream chip ID `0x17`, and header fields at
`0x90`/`0x94`; the naming change does not alter those identifiers or encoded
sample bytes. Upstream APIs and source filenames such as `okim6258.c` retain
their published spelling.

This document describes the native MSM6258-to-PDX path. OPNA
ADPCM-B uses a separate source IR and explicit decode/resample/re-encode target
stage in `opna_adpcm.py`; see [OPNA PCM IR](opna_adpcm.md). `opna_pcm.py` binds its
transcoded samples to this PDX/MDX path under explicit best-effort losses.
The transcoded samples must not be treated as byte-identical
source MSM6258 samples.

The canonical `vgm2mml.py --target mdx` path emits readable MDX MML and, when
PCM samples are present, a sixteen-track MDX plus PDX with initial E8 (PCM4/8
enable) on A. Only P carries PCM playback; Q-W contain finite end commands.
The mode requires an available PCM extension in the playback environment.
Six mode-projected listening cases played and stopped correctly in the user's
MMDSP environment; this is bounded evidence, not universal runtime certification.
OPM-only MML
conversion remains Python-only; typed PCM construction and packing require
the built `scripts/mdx_fixture_generator` helper. See README for commands.
Stopped PCM setup writes without any samples do not create an empty PDX or
require the helper; their source control evidence is still available in dumps.

## Implemented scope

- Direct VGM `0xB7` writes, one physical MSM6258 instance, 4-bit ADPCM with
  low nibble first and standard X68000 10-bit output. The chip must be declared
  in the VGM header; runtime clock writes do not invent an absent source chip.
- Uncompressed bank `0x04` and finite MSM6258 DAC streams: setup `0x90`,
  bank/step/base `0x91`, byte frequency `0x92`, `0x95` block starts without
  reverse/loop flags, and `0x93` command-count or bank-end modes. `0x94`,
  including stop-all `0xFF`, stops supply only. Zero frequency pauses an
  already started stream; a zero-frequency start is explicitly unsupported.
  Source commands at a tick precede derived supplies for that tick.
- Known independent decoder starts: observed stopped-to-PLAY transitions, or
  the explicitly labelled fresh-VGM initialization assumption. Repeated PLAY
  while playing is a continuation, not a new sample.
- Regular byte supply within one VGM sample of the rational delivery lattice,
  first byte within one sample of PLAY, and no unfed tail beyond the supplied
  bytes' nominal duration plus one sample. This is a conservative eligibility
  check, not a FIFO/decoder emulator or a measured consumption count.
- Exact standard nibble rates F0..F4: 3906.25, 15625/3, 7812.5, 31250/3 and
  15625 Hz. Incoming VGM byte-write frequency is a separate quantity.
- STOP boundaries, rests, numeric sample notes `n0,16` or `n0,%N` and explicit
  holds for notes longer than 256 target ticks. F7 precedes each non-final
  binary note chunk; its readable tie follows the chunk. Canonical PCM output
  is currently uncompressed; readable MML may use exact finite repeats.
  Runtime STOP/reset equivalence remains unverified.
- One bank of 96 samples, each at most 65535 bytes in the currently verified
  projection scope. Longer samples remain blocked as unverified extension
  behavior, not as proven extended-mode format loss. Encoded bytes remain unchanged; no waveform
  deduplication, resampling or ADPCM re-encoding is used.
- PCM-only songs and native OPM+PCM songs use the same inferred MDX clock and
  common source end. The existing six-VGM-sample boundary error bound includes
  PCM start/end and controls. Structured MDX normalization defaults ON, but
  PCM playback starts also contribute to musical clock inference. All PCM
  playback/control boundaries are checked together with OPM on one candidate
  clock; bounded, ordered candidates use that multiplier for both outputs.
  Sparse fits, collapsed intervals or candidate-only structure failures retain
  the baseline shared clock. Source PCM IR and encoded bytes are unchanged.
  Non-adoption and its reason are recorded in
  `*.mdx.normalization.json`; they are not PCM eligibility/runtime failure.

Other modes remain diagnosed: compressed bank `0x44`, stream reverse/loop or
other length modes, frequencies above 44100 Hz, active stream data-setup
changes, 3-bit encoding, 12-bit output, multiple chips, mid-play decoder rate
changes and unknown decoder starts. Their operands/block bytes remain retained.
Source-loop entry into active ADPCM is an observation, not a fabricated reset;
MDX song-loop omission is assessed separately in the target.

The stream scheduling and codec-flag profile is libvgm revision
`70e1d1fad8c03df2ce4bfb13f8599c56920dcdc8`, at 44100 Hz with its 32.32
counter. This identifies an implementation reference, not a physical decoder
oracle. Bit 2 set selects 4-bit ADPCM in that implementation; the earlier
converter interpreted it backwards. Authored public fixtures now use flag
`0x06`, retaining their original encoded payloads. See the
[stream implementation note](../field_notes/2026-10-09_pcm_stream_support.md)
for primary sources, hashes and independent C-counter checks.

## Projection policy and assessment

`--pcm-policy strict` is the default and blocks known semantic loss.
`--pcm-policy best-effort` allows a specifically defined fallback with explicit
diagnostics. The projection retains onset pan rather than emitting held pan
changes. Original MXDRV 2.06+17 Rel.X5-S PCM1 evidence described latched FC pan;
immediate pan behavior in the selected extension is not certified. Best-effort retains onset pan,
discards held changes without artificial attacks, records the affected source
intervals (including mute/audibility loss), and explicitly sets the next onset
pan. Strict blocks that projection before invoking the helper.

For completely reconstructed finite streams with known independent reset,
stable supported rate and no unresolved source issues, a second defined
best-effort fallback projects timestamped supplies as continuous PDX delivery
(`byte_supply_schedule_not_preserved`). The existing one-sample cadence check
and `independently_playable=false` source result are unchanged. Target
diagnostics retain per-playback cadence error and the nominal unsupplied PLAY
tail. Bytes and source start/STOP boundaries are retained through the shared
clock; no padding, re-encoding or extra decoder resets are inserted. The PDX
sample ends when its encoded bytes are exhausted, even if the projected note
extends to a later source STOP. The audible source tail and actual consumption
remain unverified.

Supplies while known/assumed stopped remain in the source table and observation
log. Under the pinned libvgm profile, stopped updates do not consume bytes and
stopped-to-PLAY reinitializes its FIFO. Best-effort explicitly omits those
supplies (`stopped_stream_supply_not_projected`); strict blocks that loss.
Native buffered-data behavior remains a separate runtime unknown. Direct B7
data outside known playback and unknown initial play state remain unresolved.

The VGM header song loop is currently not emitted, whether it enters a stopped
or active PCM state. Strict blocks `song_loop_not_emitted`; best-effort emits
one finite source pass and reports the loss. Finite phrase repeats are separate.

No fallback is defined for unsupported rates, 12-bit output, excessive native
sample lengths or unresolved source scheduling. Best-effort still blocks such
inputs, retaining known loss separately from unresolved evidence. Invalid
source/reference/output is `fail`; a required interpretation not established
is `unverified`. Unknown IOCS reset and actual consumption remain runtime
unknowns rather than known target loss.

`*.pcm.assessment.json` and CSV preserve source-linked item statuses and scopes:
`target_projection`, `runtime_validation`, and `artifact`. The JSON contains
`policy`, `target_profile`, `assessment_status`, `validation_status`,
`validation_run`, `artifact_status`, per-artifact states, `known_losses`,
`unverified_items`, `unexpected_mismatches`, and block reasons. Overall
assessment precedence is fail, unverified, lossy, pass; target projection has
its own aggregate. A valid target plan can be generated while runtime remains
unverified/not_run. Generation errors may set overall fail without implying
that a runtime comparison ran. Strict blocks known loss as lossy, not as an
unexpected playback mismatch. Reports survive blocked/error paths; previous
target artifacts are invalidated before the new attempt.

## Source evidence and target binding

`py/msm6258.py` holds immutable chip/source records and exact sample bytes.
Its transfers use packed ordered columns, avoiding one full state object per
data byte. State changes retain source event ID, byte address, original time,
clock/divider, raw pan, play state, reset observation and reset knowledge.
Byte equality plus codec defines sample identity; pan and rate belong to the
playback. Hash collisions are checked by actual byte equality.

`consumed_nibbles` stays unknown; `nominal_nibbles` is explicitly calculated.
An observed STOP/PLAY reset is distinguished from `vgm_initialization`.
Raw time is never replaced by an MDX tick. Source options include 10/12-bit
precision even when the MDX backend cannot represent them.

`py/pcm_mdx.py` allocates the immutable `sample_id -> bank/slot` binding once.
The typed target commands and the PDX manifest consume that same table. The
same ordered typed commands produce readable PCM MML and direct PCM MDX;
MML is never reparsed to recover the target plan. PDX-specific
limits live here and in the packer, not in the source interpreter. Future
Z_MUSIC output should consume this source evidence using its own binding and
sample-container writer; MDX text and PDX slot numbers are not common IR.

MSM6258 raw pan 0/1/2/3 maps to PCM `p3/p1/p2/p0`. MDX FM uses the opposite
left/right order for operands 1 and 2; do not reuse the FM mapping for PCM.

## Inspectable outputs

`--dump-passes` preserves these additional source artifacts:

| Artifact | Evidence |
|---|---|
| `*.pcm_raw.csv` | Ordered B7 writes, byte addresses and source event IDs |
| `*.pcm_commands.csv`, `*.pcm_blocks.csv` | Original stream/block operands and block spans |
| `*.pcm_stream_controls.csv` | Stream setup/start/stop state with original command provenance |
| `*.pcm_stream_supplies.csv` | Derived supplies, trigger event/address, byte index and bank/block offset |
| `*.pcm_state.csv` | Non-data control changes and reset knowledge |
| `*.pcm_segments.csv`, `*.pcm_issues.csv` | Playback boundaries, supply eligibility and rejection reasons |
| `*.pcm_observations.csv` | Nonfatal stopped-supply and active-loop-entry observations |
| `*.pcm_samples.csv`, `*.pcm_samples/*.adpcm` | Exact sample bytes and hashes |
| `*.pcm_blocks/`, `*.pcm_source.json` | Retained blocks, header and consumption scope |

Accepted target output retains `*.pcm_bindings.csv`, `*.pcm_projection.csv`,
`*.pcm_clock.csv`, `*.pcm_target_commands.csv`, `*.pcm.timing.json` and
`<stem>.pcm/{manifest.tsv,target.tsv,opm.mml}` plus canonical packing inputs
and PDX/MDX build logs. Assessment JSON/CSV are retained even when projection
is blocked. The projection records source and target boundaries and
their errors, reset origin, rational rate and F setting. The clock CSV projects
every source control boundary; it does not imply each register is replayed as
an MDX command. With pass dumps,
`*.mdx.structure.units.csv` also includes P units, PCM playback membership and
control trajectories. OPM source Segment IDs are never assigned to PCM units.

The helper uses soundlog's existing PdxBuilder, then Python independently reads
all 96 big-endian offset/length entries and verifies allocated payloads against
canonical bytes and unallocated empty slots. No new PDX encoder is introduced.

## Compilation and playback limits

The canonical PCM path invokes:

```text
mdx-fixture-generator --compile-pcm FM_ONLY.mml PLAN.tsv OUTPUT.mdx
```

The strict three-column TSV has header `kind\tvalue\tticks`, metadata rows
`pdx_name`, raw `tempo`, `end_tick`, then ordered bank/frequency/pan/gate/volume,
hold/note/rest/end commands. Python completes timing projection and duration
chunking. The helper validates command bounds, durations, shared FM tempo/end,
finite FM repeat offsets, standard layout and PDX references. It compiles only
the FM MML, preserves its typed tracks/tones/title, adds typed PCM using the
existing soundlog MdxBuilder, then serializes/reparses the sixteen-track document.
The builder adds E8 at the beginning of A and inactive Q-W. Validation compares
A after removing precisely that E8, B-H, P, tones, title and PDX name against
the compiled FM and typed PCM plan. It requires exactly one E8 and inactive
extra tracks; shared clock, timing, source PCM IR and PDX payloads are unchanged.
Python independently checks the emitted layout so an old helper cannot report
a standard-nine output as success. Rebuild the helper when updating this code.
PDX lookup is relative to OUTPUT, allowing inputs under `<stem>.pcm/`.
No new compiler, raw binary concatenation or PCM text compilation is used.
Native-length checks and a conservative 65535-byte combined MDX limit apply.
An explicit track-offset/combined-size capacity error is target loss
`mdx_capacity_exceeded`, with no splitting/truncation fallback. Existing MML,
PDX, source dumps and detailed target dumps remain inspectable. The assessment
marks MDX blocked and retains per-artifact generation states; these partial
outputs are not a completed MDX/PDX pair or an unexpected runtime mismatch.

Readable MML can still be compiled separately using the older helper mode:

mmlx 0.2.0 normally selects 16 tracks whenever a P..W track is present.
`--pcm-mode standard` checks inactive Q..W, removes the automatic PCM8 marker,
and serializes/reparses the typed document as standard nine tracks. It does
not patch raw MDX offsets manually or create a second MML compiler.

`--compile-only` loads the named PDX beside the MML and verifies every static
PCM reference before writing MDX. Missing/empty slots and ambiguous filenames
are errors. Static reference checks do not prove runtime decoder equivalence.

Pinned soundlog 0.15.0 clears raw sample continuation at an interior tied-note
or control boundary and does not retain the source VGM's reset/stop relationship.
Do not infer from this that standard MXDRV must physically reset the decoder
at every note. Native driver requests, ROM IOCS/DMA and chip controls need a
versioned independent baseline. These limits prevent certified PCM replay.
PCM compile-and-replay writes the structurally validated MDX, rejects VGM
replay and removes a stale requested VGM. The batch exporter replays the
canonical pair via `--from-mdx`, without recompiling its readable PCM. It retains
MML/MDX/PDX and reports `pcm_replay_unavailable` with a nonzero exit status.
`export_mdx.py --no-vgm` skips replay and reports successful MML/MDX/PDX
generation independently of runtime validation. It never recompiles readable
PCM MML as a substitute for a missing typed pair. Default replay and existing
roundtrip tooling remain available. FM-only replay is unaffected. Use an X68000 player for the PCM pair; hardware,
MMDSP GUI and waveform comparison remain separate validation work.

## Evidence and remaining work

Original public fixtures live in `tests/fixtures/public/opm_msm6258/`: PCM-only
reset/retrigger, held pan changes/mute, exact asset reuse, and concurrent OPM
with F0/F4 playback, plus a 512-tick hold followed by STOP/rest. Their independent expected schedule/hashes are regenerated
without calling the production analyzer. Target tests independently inspect
PDX and compiled MDX headers and encoded holds.

A bounded local direct-write case (KMSM009) retains all source data but fails
the strict delivery lattice at its 12th data write. Do not relax that test merely
to make the song convert. A validated transfer/FIFO/decoder timing model is
needed before supporting that capture. Its recording bytes are private.

Next work starts with a versioned native MXDRV/ROM IOCS baseline, including
mid-hold controls and reset/consumption, then independent C0/C1 analysis,
source scheduling/decoder evidence and certified replay.
See [PCM roundtrip validation](pcm_roundtrip_validation.md) for independent
A/B/C comparisons, required B_ref playback, evidence gaps and validation order.
The [2026-10-09 review](../field_notes/2026-10-09_pcm_mxdrv_roundtrip_review.md)
records NanoDrive8, the native driver disassembly and tested alternatives.
Z_MUSIC version/compiler/container choices remain future decisions.

Sources: [VGM 1.71 specification](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/vgmspec171.txt),
[MSM6258 behavior](https://github.com/mamedev/mame/blob/master/src/devices/sound/okim6258.cpp),
[X68000 chip configuration](https://github.com/mamedev/mame/blob/master/src/mame/sharp/x68k.cpp),
[PDX format](https://github.com/vampirefrog/mdxtools/blob/master/docs/PDX.md).
Dependency behavior is verified against the pinned Cargo sources and helper
tests, rather than inferred from successful final file generation.
