# Authored MXC reference patterns (provisional native-playback baseline)

These short scores and triangle-wave PCM samples were written for this project
and are distributed under its MIT license. No FF4SIREN melody, voice definition
or PCM sample was copied. The FF4SIREN reference established the native MXC
invocation; these fixtures exercise comparable note/rest/hold and sample binding
features with original content.

Each MML was compiled by native MXC v1.01 through the existing run68 adapter.
PDX packing used the external helper's `--build-pdx` command, then an independent
reader verified sample bounds, empty slots, lengths and exact encoded bytes.
The encoder uses mdxtools' ADPCM library with original exact-length input glue.
MDX compilation never uses mmlx for these fixtures.

## Listening in MMDSP

Copy all four `.MDX` files and **PUBPCM.PDX into the same directory** on X68000.
No other file is needed for playback. MML/JSON/CSV are validation evidence.
Disable automatic repeat for this finite-ending check.

| MDX | Approximate length | Expected observation |
| --- | --- | --- |
| GATEEND | 0.69 seconds | Repeated short FM/PCM sounds; both long and short assets require gating before exhaustion; final silence |
| HOLDEND | 1.57 seconds | One continuous FM/PCM sound for about 1.31 seconds, then about 0.26 seconds of silence; no restart at the tie |
| RATES | 0.98 seconds | Same sample at F4/F0, p1/p2/p3; F4 exhausts before the gate, F0 requires gating before sample exhaustion |
| FMSTATE | 0.79 seconds | FM-only sound: pitch, voice, volume and pan changes; first three notes hold the same pitch across control changes |

Check the title, FM A keyboard/level-meter activity, sound during the rests,
finite end and whether any PCM sound remains after stop. Record observations
for each file separately. Pan is specified here by its MML code because PCM and
FM pan operands have different left/right conventions. Expected note durations
and release requests are established; decoder resets, waveform behavior and
physical STOP require separate observation.

User listening results (2026-10-10): all four files animate correctly and sound
is audible; HOLDEND stops correctly; RATES leaves continuing noise after the
data ends. GATEEND's stopping result was not separately reported. See
`listening_results.json`. RATES is a retained failing runtime case, not a
successful stopping baseline. Static `validation.json` generation checks are
separate from these user observations and are not overwritten by listening.
The subsequent report distinguishes manual F7: during RATES playback it stops
sound; after the residual noise begins at completion it does not. Both A and P
already have 72 ticks (294.912 ms) of final rest. Missing any trailing rest is
therefore not a demonstrated common cause. Original FF4SIREN loops indefinitely
and manual F7 stops it; that is not a successful finite-ending PCM example.

HOLDEND's long sample lasts nominally 7.68 seconds, beyond its requested release
at 1.31 seconds and track end at 1.57 seconds. Check the silent interval starting
at 1.31 seconds separately from track termination. In a second playback, press
MMDSP Stop at about 0.5 seconds and check immediate cessation separately from the
MDX-requested release; manual Stop is a player/driver observation.

These assets are ready for listening, not a declaration that vgm2mml Segment/PCM
IR extraction has passed. A trustworthy source VGM capture and comparison are
still needed. Existing soundlog PCM replay is not used to certify that capture.

## Expectations and regeneration

- `<stem>.MML`: original handwritten score intent.
- `samples/*.adpcm`, `samples.tsv`: pre-pack neutral sample data and explicit
  PDX bank/slot binding. Sample0 is 60,000 bytes; sample1 is 400 bytes.
- `<stem>.pcm_ir.expected.json`: sample identities and authored playback
  requests. Timing is in declared reference MDX ticks, not invented VGM time.
- `expected/<stem>/`: direct MDX/PDX JSON, command/event/control/tone CSVs,
  PDX table, encoded samples and `pcm_ir.expected.json`.
- `validation.json`: static generation checks and separate not-run/unverified
  extraction/native-listening statuses.

PCM expectations preserve unknown transfer timestamps, source event IDs,
decoder resets, consumed nibbles and physical STOP. They are a comparison
dataset, not a fabricated complete `PcmAnalysis` record. Reference tick duration
is 4,096 microseconds from the authored `@t240`: Timer B uses
1024 * (256 - tempo) clock cycles at 4 MHz. Earlier seconds annotations were
four times too large; this correction changes annotations, not MDX/PDX bytes.
It also corrects GATEEND's short asset classification: q4 requests a 49.152 ms
gate, just before its nominal 51.2 ms exhaustion. Natural exhaustion before the
gate is exercised by RATES at F4, not by GATEEND. The retained GATEEND title
remains unchanged to preserve the already-listened binary.

Both PCM expectation variants declare `pcm-ir-reference-expectation-v1` and an
explicit `variant`: `authored-score-intent` at the top level and
`decoded-mdx-pdx-requests` in each direct-reader folder. Their event fields differ
because one states handwritten intent and the other retains decoded commands.
`validation.json` lists the fields actually compared; its pass status does not
claim all OPM state trajectories or converter extraction were checked.

Regenerate under WSL from the repository root:

```sh
python3 tests/scripts/generate_mdx_reference_fixtures.py
python3 -m unittest discover -s tests/scripts -p 'test_mdx_reference*.py' -v
```

Regeneration needs gcc, native MXC/run68, the built external MDX helper and the
local mdxtools source checkout. Tool paths can be supplied with `--generator`
and `--mdxtools-root`; the existing MXC adapter locates MXC/run68. Prepared native
inputs and compiler metadata remain under ignored
`outputs/reference_validation_2026-10-10/public_build/`.
