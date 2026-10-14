# OPNA ADPCM-B source and MSM6258 target IR

`py/opna_adpcm.py` separates observed YM2608 external sample ROM evidence,
interpreted decoder epochs, and approximate X68000 PCM samples. It does not
assign PCM tracks, pack PDX files or generate MDX PCM commands. The separate
`py/opna_pcm.py` adapter connects its transcoded samples to the existing
`pcm_mdx` P-track/PDX pipeline; `opna_opm` orchestrates FM and PCM together.

## API and artifacts

```python
from opna_adpcm import analyze, project_msm6258

native = analyze(raw_vgm_bytes, loop_count=1)
native.dump(outdir, stem)
target = project_msm6258(native)
target.dump(outdir, stem)
```

`loop_count` uses the shared `vgm_timing.command_times` traversal. Block and
control rows retain source command ID, byte address, expanded sample timestamp,
original sample timestamp and loop iteration. `source_event_id` is the expanded
traversal ID, while `original_source_event_id` identifies the command before
loop expansion; Segments retain both corresponding ID tuples. Native `segments` have a decoder
`playback_id` and `continuation_index`; a continuation is not a fresh ADPCM
decoder. Source sample identity includes addresses, immutable encoded bytes and
the contributing block IDs, including successive ROM revisions.

Native outputs are `<stem>.opna_adpcm.blocks.csv`, `controls.csv`,
`segments.csv`, `issues.csv`, `analysis.json`, whole block payload `.bin` files,
and immutable high-nibble-first Yamaha sample `.bin` files. The block payload
includes its original eight-byte ROM size/destination prefix. Analysis JSON
lists sample codecs, hashes, source ROM addresses, block provenance and current
files. A declared ROM region with unfilled bytes remains unknown, rather than
being assumed to contain zeros.

Target outputs are `<stem>.msm6258_ir.playbacks.csv`, `analysis.json` and
low-nibble-first MSM6258 sample `.bin` files. Target JSON records source and target
sample hashes, source IDs, sample counts, padding nibbles, level, pan, divider,
rate, codec and squared requantization error. Every source Segment produces a
target playback row, including an ineligible row with diagnostics. Both stages
write current manifests even for an empty PCM source; old sample files are not
current artifacts unless listed in the current JSON.

## Native interpretation

Supported initial independent spans require a single ordinary positive YM2608
clock, port-1 writes and external playback (`control 00` bits 7 and 5 set).
`control 01` selects ROM (bit 0), 8-bit RAM (bit 1) or 1-bit RAM (both clear).
ROM and RAM8 use 32-byte address units; RAM1 uses 4-byte units. Read-only RAM
initialized from a type-81 ROM block is an explicit source-memory assumption:
`ram8_seeded_from_vgm_rom_block_assumption` or
`ram1_seeded_from_vgm_rom_block_assumption`. Segment observations and target
diagnostics retain it; it does not establish hardware RAM contents.
Source reset initialization is explicitly assumed:
registers zero and limit registers `0c/0d` equal `ffff`. This is not presented as
observed source writes.

Start and end register pairs `02/03` and `04/05` use the selected address units.
End is inclusive of its whole address unit. RAM1 sample bytes remain ordinary
ADPCM-B bytes; no bit unpacking is applied. Data block `81` begins with little-endian
total ROM bytes and destination, followed by actual encoded bytes. Later
accepted blocks overlay the known ROM image without changing earlier samples.

The nominal nibble rate is a rational value supported only at the assumed
default FM/ADPCM prescaler divisor 6:

```text
rate = source_clock_hz * deltaN / (144 * 65536)
nominal end = start_vgmticks + encoded_bytes * 2 * 44100 / rate
```

Port-0 prescaler controls `2d/2e/2f` are retained in the controls CSV with their
port, expanded/original command IDs and effective divisor. `2d` selects 6,
`2e` changes 6 to 3, and `2f` selects 2. An effective divisor change while a
decoder epoch is active invalidates that entire epoch for independent target
transcoding. A new epoch under divisor 2 or 3 is retained with
`unsupported_adpcm_prescaler`; its rate and inferred end are unknown (rate
0 is an explicit unavailable sentinel), rather than applying the default
`/144` formula. Returning to divisor 6 permits subsequent independent epochs.

The inferred end remains a numerator/denominator in native Segments. An integer
Segment boundary is its ceiling. A repeat epoch records an inferred nominal cycle duration as a
rational value, while keeping its active span through reset/retrigger/EOF;
it does not invent loop boundaries or independently transcode a repeating decoder.
The model is not cycle-accurate YM2608 playback: fetch latency, end-prefetch behavior and output interpolation are not
emulated. Source reset, retrigger, parameter changes and EOF have distinct
termination labels. EOF retains an active epoch without inventing a reset.

Repeat, CPU supply, recording, address wrap/limit effects, second chips,
extra-clock headers, unexpanded DAC streams and PCM RAM transfers remain
unsupported. ROM changes during playback and any held-epoch parameter writes,
including writes at the start's timestamp, invalidate independent transcoding
for the entire decoder epoch. Repeated identical passive parameter writes
retain raw evidence without creating a decoder change. Register `10` controls
IRQ flags and does not split/invalidate playback. Bus-data writes remain
strobes even when their byte value repeats. Continuation slices remain inspectable. These
cases are not silently restarted or treated as independently encoded samples.
External record controls and recording bus writes conservatively taint the
analysis memory history. Clearing current control/data registers cannot
restore a trusted seed image; subsequent independent target transcoding stays
ineligible for this analysis. Native source blocks and controls are retained.
Ordinary unsupported modes produce issues and ineligible IR instead of stopping
the native dump. Resource bounds and malformed VGM command boundaries can raise
explicit errors.

Resource limits are 64 MiB source bytes, 2 MiB ROM, 64 MiB retained block bytes,
64 MiB native sample bytes, two million traversed commands, 10000 block rows and
100000 control/Segment rows. Target decoding/resampling is limited to one
million samples per span and eight million target samples in total.

## MSM6258 projection

The target reuses `pcm_mdx.RATES` to choose the nearest nominal MXDRV F0..F4
rate: `(4 MHz, 1024)`, `(4 MHz, 768)`, `(8 MHz, 1024)`, `(8 MHz, 768)` and
`(8 MHz, 512)`. The OPM target clock does not constrain the PCM clock. Target
samples/playbacks record the actual PCM clock, divider and MDX frequency index;
the projection-level `clock_hz=4000000` argument identifies the platform base
profile, not a fixed PCM sampling clock. Supported independent spans decode Yamaha ADPCM-B to signed
16-bit values, resample linearly to that rate, bake the linear OPNA level
(`level / 256`), and independently encode four-bit MSM6258 ADPCM. Yamaha encoded
bytes remain separate from the transcoded MSM6258 bytes.

The decoder begins with predictor 0 and step 127, clips to signed 16-bit range,
and updates the adaptive step using the Yamaha ADPCM-B scale law. The MSM6258
encoder is an independently written greedy search over sixteen candidate
nibbles. It uses a 10-bit predictor, reset value -2 and adaptive step index 0.
The target PCM scale maps full signed 16-bit range to this 10-bit predictor;
the reported error is in that normalized signed PCM domain, not a prediction
of analog output gain. Samples pack the first nibble in the low half byte.

An odd final sample count needs a padding nibble. Its presence and exact sample
count are recorded. PDX receives the full final byte, so its extra nibble and
nominal encoded-duration rounding are reported as losses. Source pan stays in
native/target IR; the adapter translates OPNA enable bits to MSM6258 disable
bits before the existing MDX pan mapping.

All transcoded rows report loss diagnostics: nominal decoding without YM2608
prefetch/DAC interpolation, resampling without an antialias filter, baked
volume, greedy ADPCM requantization and reduced dynamic range. Rate changes,
duration rounding and these approximations do not establish acoustic
equivalence. Playback has not been verified at this implementation stage.

## MDX/PDX connection

Normal conversion accepts `--pcm-policy best-effort` to allow the explicitly
recorded decoding/resampling/recompression losses. Default `strict` rejects
them. Ineligible decoder epochs remain blocking in both policies, including
short intervals that output normalization could otherwise omit.

`opna_pcm.adapt` keeps expanded original-VGM event IDs and sample timestamps,
checks source-byte identity, and deduplicates only byte-identical transcoded
payloads before the 96-slot PDX limit. Playback rate/pan stay separate from
sample identity. It records no fabricated native byte supplies or physical
decoder resets. Independent PDX-note reset is an unverified target assumption.
The bridge manifest `<stem>.opna_pcm_bridge.json` traces native Segments and
target sample IDs to PDX sample IDs; `<stem>.pcm.assessment.json` retains losses.

FM and PCM share existing clock selection and normalization. Reader song-loop
expansion occurs once; projected FM is parsed as an already expanded traversal,
and PCM uses that same source end and loop boundary. Decoder continuation
across the emitted loop remains unsupported. Root output contains final MML,
MDX, PDX and their assessment; `projected_opm/` contains FM source passes when
requested. Existing hash-based binary ownership controls regeneration.
The external helper receives transcoded target bytes unchanged and uses the
existing 16-track/E8 layout with one active P track. Focused source/adapter and
PDX-projection checks cover this connection. End-to-end song MDX/PDX generation
and playback remain the user's checks; PCM MUCOM roundtrip is deferred.

## Primary references

- [YMFM ADPCM-B engine](https://github.com/mamedev/mame/blob/master/3rdparty/ymfm/src/ymfm_adpcm.cpp)
  and [register definitions](https://github.com/mamedev/mame/blob/master/3rdparty/ymfm/src/ymfm_adpcm.h):
  mode bits, address units, limit precedence, reset state, nibble ordering,
  predictor/step law, prefetch and interpolation behavior.
- [MAME MSM6258 device](https://github.com/mamedev/mame/blob/master/src/devices/sound/okim6258.cpp):
  standard divider selection, step/index law, reset predictor and nibble order.
- [YMFM OPN engine](https://github.com/mamedev/mame/blob/master/3rdparty/ymfm/src/ymfm_opn.cpp):
  shared FM/ADPCM cadence and prescaler behavior.

These sources were checked to understand behavior. The module contains an
independent Python implementation of the codec arithmetic; it does not vendor
their source files or claim their runtime implementation is reproduced.
