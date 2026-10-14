# Native PCM source evidence and target placement

The MSM6258/MDX rules below describe an inherited adapter. They are not OPNA
source rules or MUCOM bank constraints. Cross-chip contracts are specified in
[decoder epochs](#decoder-epochs-and-cross-chip-boundaries) below.

MSM6258 encoded sample identity uses codec plus exact supplied bytes. Decoder
reset/continuation, rational nibble rate, raw pan and source timing belong to
playback evidence, not sample identity. Hashes index candidates; actual bytes
resolve equality. Observed STOP/PLAY resets and a fresh-VGM initialization
assumption must remain distinguishable. Supplied bytes and nominal duration
do not establish measured decoder consumption or waveform equivalence.

Target-independent PCM records preserve source options, blocks/commands,
ordered B7 transfers and control/playback spans. MDX PDX bank/slot limits and
allocation belong to the target. PCM MML text is optional: target MDX commands,
PDX and any readable rendering must consume the same immutable binding.
Future Z_MUSIC output must consume source evidence with its own
placement; neither MDX text nor PDX slot numbers become the common IR.

Finite bank-4 DAC stream supplies are a separate derived table with triggering
command, transfer sequence, original bank/block position and VGM time. They do
not masquerade as original B7 writes. The source scheduling profile is pinned
libvgm at 44100 Hz/32.32; original same-tick commands precede DAC supplies.
Stream STOP/exhaustion never inserts chip STOP/reset. Bit-2 codec interpretation
and stopped-to-PLAY FIFO behavior name that reference profile, not native IOCS
or silicon certification. `consumed_nibbles` remains unknown.

Conservative source cadence eligibility remains unchanged when target
best-effort collapses a fully reconstructed finite stream into continuous
PDX delivery. Keep source bytes/times, cadence error and unsupplied PLAY tail,
and report discarded delivery timing in target assessment. Known/assumed
stopped supplies and active song-loop entry remain source observations; target
omission, native buffer uncertainty and unimplemented song looping are distinct
results. Neither byte equality nor a nominal tail duration proves audible
equivalence. A target capacity failure retains completed MML/PDX and target
dumps, but must not be reported as a completed MDX/PDX pair.

PCM validation separates target note/gate intent, IOCS/DMA playback requests,
chip PLAY/STOP/decoder state and VGM stream scheduling. A stream stop is not
a chip stop; cursor restart is not by itself decoder reset. Use a versioned
MXDRV/IOCS profile and an independent playback baseline. Matching A/B after
reusing an uncertified player is internal consistency, not standard playback
proof. Unknown consumption/reset/effective-control fields remain unverified.

Keep MDX header/repeat offsets, PDX sample offsets and sample byte lengths
distinct. Standard PCM1 reads a 32-bit PDX offset and the low 16-bit length;
a PDX file or sample address above 65535 is not itself a sample-length overflow.
PCM NOTE selects a slot; standard MML does not provide a verified arbitrary
sample-byte seek. Source EOF is not an observed chip STOP. A finite track's
F1/00 and its preceding note gate are separate: in the inspected MXDRV +17
PCM1 path, an unheld q8 gate expires before processing duration expiry, while
F1/00 has no unconditional PCM1 cut. Port STOP/END stubs can confirm this call
intent but cannot certify native cessation. Do not infer mandatory trailing
rests, payload stop markers or alignment padding from an unresolved noise report.
Conform generated MDX to the selected MXDRV reference; native verification is
not a reason to leave known request handling unimplemented in a replay backend.
PDX does not adapt stopping behavior to the user's playback environment. A stub
in a separate diagnostic player is neither a cause of MMDSP playback failure
nor evidence that the generated MDX is correct at runtime.
See field_notes/2026-10-09_pcm_pdx_end_review.md for bounded reference evidence.

MDX target projection owns fidelity/eligibility and explicit approximation;
never rewrite common PCM evidence to fit target limits. Strict conversion
disallows known semantic loss. An explicitly selected best-effort policy may
emit usable MDX+PDX using a defined fallback with source-linked loss diagnostics.
Separate pass, lossy (known target loss), unverified and fail, and keep artifact
generation status separate. Unknown IOCS behavior is not a confirmed target
constraint; unexpected mismatches are not acceptable best-effort loss. Known
losses and unresolved items can coexist and must both remain visible. Lossy
artifacts do not pass strict roundtrip validation. Target projection success
does not certify runtime equivalence; unexecuted runtime comparisons stay
unverified/not_run even when all requested artifacts were generated.

For MXDRV 2.06+17 Rel.X5-S PCM1, held FC pan changes do not request an immediate
IOCS update. The defined best-effort projection retains onset pan until the
next attack, records each affected source interval, and inserts no artificial
retrigger. F7 precedes the note whose gate keyoff it suppresses; readable ties
follow that note. One typed PCM target command list drives both MDX construction
and readable rendering. Native PCM1 uses the low length word; that evidence
must not be promoted to a limit of other playback modes.

PCM packages now select the reference-tested extension route: sixteen tracks,
one initial E8 on A, P playback and inactive Q-W. The typed builder preserves
compiled A-H commands except the new mode marker, exact P commands, voices,
title and PDX reference. This target-only choice does not change source PCM IR,
the shared clock or encoded sample bytes. It requires a PCM extension in the
player; six named listening artifacts passed in the user's MMDSP environment,
not all possible drivers or inputs. Retained bank/slot/sample-size limits are
the current verified projection scope, not newly established extension limits.
Held pan is still deliberately omitted and diagnosed as projection loss;
immediate extension pan/reset behavior remains unverified. Above-65535 sample
support is blocked as unverified, without claiming a proven target-format loss.
Inspect the actual serialized mode before reporting generation success; an old
helper must not silently emit the former standard-nine profile.

OPM and PCM share one MDX clock and common source end. Target-only
normalization cannot move one chip's boundaries independently. PCM pan
operands 1/2 have the opposite left/right meaning from MDX FM. Standard X68000
PCM uses 4-bit encoding and 10-bit output; preserving bytes alone cannot
represent a 12-bit decoder clamp. Keep unsupported source values inspectable
and diagnose them in the target.
The normalization default must not itself reject PCM input. Until PCM-aware
correction is verified, keep the baseline shared OPM/PCM clock and record
non-adoption separately from PCM loss/eligibility and runtime validation.
Data Bank/DAC Stream decoding is a separate change from frontend cleanup;
unsupported stream semantics and their source evidence remain explicit.

See `docs/pcm_pdx.md` for current eligibility and tool limitations and
`docs/pcm_roundtrip_validation.md` for the independent validation design.

SegaPCM uses a separate runtime IR, then an explicit stereo-to-mono MSM6258
bridge. Original ROM/control evidence, native unclipped stereo, runtime gates
and target ADPCM chunks are different representations. Chunk boundaries and
independent target-decoder assumptions are not source supply/reset facts.
Short packaging tails survive normalization; sample lengths follow the final
shared clock. See `docs/segapcm_segments.md` for artifacts and known losses.


## Decoder epochs and cross-chip boundaries

Encoded identity is codec plus exact bytes, separate from memory location,
playback identity, reset/continuation, cadence and controls. Native immutable
control slices and a whole decoder epoch are different representations.
Held rate/level/pan updates do not start a new sample attack.

Retain source-derived nominal positions and inferred natural ends as a separate
inspectable interpretation. They are not observed addresses/consumption, nor
permission to replace native Segment boundaries with target-quantized time.
Accepted target time/rate allocation requires a separate position/exhaustion
audit; identify omitted epochs explicitly rather than pretending they played.
Unknown runtime consumption cannot be proven by bytes or nominal duration.

SegaPCM address stepping and OPNA Delta-T decoding stay chip-specific.
Reuse source decoders, structure and normalization where meanings apply;
neither MSM6258/PDX nor MUC becomes a universal PCM IR. A future cross-chip
bridge retains native position/control evidence before projecting codec,
gain/pan, rate/loop, channel capacity, bank and target playback behavior.

Before waiving an epoch diagnostic, inspect changed registers and bits.
Memory/address/data/reset/repeat changes require distinct proof; certification
of one live level/pan/rate route is not broad permission to ignore controls.
First-note decoder initialization and compiler/replay dialect/startup phase
belong to a versioned target profile, with positive timing protection when
initialization would otherwise overwrite live controls.

Source details: [OPNA ADPCM-B](opna_adpcm.md), [SegaPCM](segapcm_segments.md).
Current live-control commands, banks, first-note protection, installed profiles
and unresolved MUCOM limits: [MUCOM PCM projection](mucom_musical_projection.md#pcm保持中制御と相互運用の境界).
