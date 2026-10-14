# SegaPCM source evidence, playback reconstruction and PDX projection

The shared VGM reader captures C0 writes and type80 ROM blocks on the same
44100 Hz sample clock as OPM. Loop replay carries state and retains original
command IDs, occurrence IDs, command addresses and iteration/timing provenance.
No MDX clock normalization is applied to native evidence.

The collector emits immutable raw records, channel State snapshots and
write-observed control/configuration Segments for sixteen channels per instance.
Repeated writes and zero-duration observations remain inspectable. Enabled
configuration is not proof of an audible gate. Programmed address bytes remain
unknown until written; runtime address advancement and natural sample completion
are not emulated. EOF closes observations without a synthetic disable.

## Retained artifacts

- `<stem>_trace.segapcm_regs.csv`: original C0 operands, RAM aliases, role and provenance.
- `<stem>_trace.segapcm.csv`: state after each modeled write or ROM update.
- `<stem>.segapcm.segments.csv`: channel configuration intervals and endpoint IDs.
- `<stem>.segapcm.rom_blocks.csv`: ordered ROM size/start/header facts and payload paths.
- `<stem>.segapcm.rom_blocks/blockNNNN.bin`: verbatim sparse payload per block.
- `<stem>.segapcm.analysis.json`: current artifact list, counts, declarations, issues and limits.
- OPM State/Segment, shared timing and loop CSVs accompany this source route.

Only manifest `current_files` belong to the current analysis. Older unlisted
payloads in a reused output directory are not part of the current ROM history.
ROM blocks preserve overlaps, repeated loads and changing sizes chronologically;
each update changes the ROM generation in affected channel intervals. There is
no allocation proportional to a declared ROM size. A short ROM header is kept
opaque and diagnosed; physically truncated VGM framing is rejected.

Register interpretation follows the sixteen-channel VGMPlay SegaPCM model:
low-page channel offsets2/3 set volume,4/5 loop address,6 end page,7 delta;
high-page84/85 program address and86 control. Bit0 disables; bit1 disables loop.
Untouched bytes are nullable. Unmodeled RAM writes remain in the raw CSV.
Nominal byte advance rate, when declarations permit it, is clock * delta /32768;
it is not measured playback frequency. Bank parameters retain raw interface
values; a zero mask's compatibility default is explicitly labeled. Undeclared
or unmodeled second-instance/clock details do not manufacture a known rate.

## Separate runtime reconstruction

`py/segapcm_playback.py` reconstructs playback without modifying the observed
State or control Segments. The explicit VGMPlay-compatible profile assumes
reset RAM FF, zero fractional addresses and neutral80 in uncovered ROM regions.
At clock/128 frames per second it fetches unsigned8 PCM, applies left/right
7-bit volumes, and advances a16.8 address by delta. It checks end-page equality
before fetching, clears the fractional address on natural stop, models hardware loops, and applies live
register/ROM updates in source occurrence order. The reader's VGM replay
continues runtime state; it does not reset the chip at the song-loop boundary.

Additional artifacts are `<stem>.segapcm.native.s32le` (unclipped, interleaved
left/right signed32 PCM), `.segapcm.playback_gates.csv`, `.segapcm.runtime.csv`
and `.segapcm.playback.json`. Fractional source times remain exact rational
values. Runtime gates and natural-stop events are inferred, separately from
observed commands. Rendering has explicit frame/event resource bounds; sparse
ROM storage does not allocate the declared ROM size.

## Target boundary

`py/segapcm_pcm.py` mixes `(left + right)/2`, clips to signed16 with counts,
linearly resamples to F4/15625Hz and independently encodes MSM6258 ADPCM.
This is an explicit best-effort target transform; strict policy blocks its
reported losses. Native unsigned8 ROM bytes remain in the source artifacts.
Target ADPCM bytes, not ROM bytes, go into the existing single-P/16-track PDX
route. No observed MSM6258 supply or chip-reset command is fabricated.

The union of native active gates is packaged into at-most8-second chunks,
split at the MDX `L` boundary and EOF. Fractional gate bounds round outward by
less than one source sample. Chunks are packaging units, not source notes;
short tails cannot be omitted by whole-gate normalization. Exact identical
encoded payloads share a slot. The existing96-slot/65535-byte verified limits
still apply. After common FM/PCM clock selection, each original audio interval
is resampled to its final scheduled gate, using an even ADPCM sample count
within one F4 sample of its target duration. Inspect
`.segapcm_pcm_mapping.csv` and `.segapcm_pcm_bridge.json` for the mapping,
clipping, duration error and loss diagnostics.

Stereo folding, resampling without antialias filtering, requantization,
independent decoder reset at seams and recorded-loop phase freezing are
explicit limitations. Physical playback, decoder reset and acoustic equivalence
are unverified. A saved loop traversal becomes fixed PDX audio; later runtime
phase or nonperiodic source behavior cannot evolve as in the original chip.

`--fm-only` retains the previous diagnostic route through ordinary native OPM
projection, without rendering or binding SegaPCM audio; its omission remains
explicit in conversion JSON and TXT. Source validation and dumps still run.
MGS and other unsupported source combinations remain rejected.

## References

Hardware/VGM facts were checked against the
[VGM specification](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/vgmspec171.txt),
[VGMPlay command dispatch](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/VGMPlay.c)
and [SegaPCM model](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/chips/segapcm.c).
Runtime behavior was also checked against the independently referenced
[MAME SegaPCM model](https://github.com/mamedev/mame/blob/master/src/devices/sound/segapcm.cpp)
and [clock definition](https://github.com/mamedev/mame/blob/master/src/devices/sound/segapcm.h).
The code reconstructs these behaviors independently; it does not embed or copy
either emulator. These are explicit software-model assumptions, not proof of
physical hardware or target playback equivalence.
