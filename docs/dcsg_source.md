# DCSG source analysis and AY projection views

`py/dcsg.py` decodes SN76489-family VGM writes independently of MUC rendering.
`analyze(raw, loop_count=1)` returns immutable configuration, ordered writes,
source State snapshots and positive-duration channel control Segments. A chip
has three tone channels and a fourth noise channel; a declared second chip
retains its own latch, registers, stereo mask and noise-reset history.

## Source evidence

Commands `0x50`/`0x30` address the first/second chip. Game Gear stereo commands
`0x4f`/`0x3f` are retained even when the configured mono variant ignores them.
Latch and data writes retain raw bytes, source addresses, source-event IDs,
loop traversal and original/replayed sample timestamps. Shared VGM waits use
`vgm_timing.command_times`, including continuous chip state on loop replay.

Tone low-nibble latches and six-bit high data are decoded separately. Known-bit
masks distinguish observed values from unknown capture/reset state. A data byte
before an observed latch is retained and diagnosed instead of guessing its
register. Unobserved attenuation is unknown, not an invented source KeyOff.
This module does not simulate oscillator phase, DAC output or arbitrary PCM
played by rapid register modulation.

Raw clock, dual-chip/T6W28 bits, feedback mask, shift width and variant flags
remain inspectable. The nominal tone frequency is `clock / (4 * divider * N)`,
where the ordinary input divider is 8; the no-divider flag selects 1. A TI-style
period zero represents 1024. Sega-style zero/DC is preserved without inventing
a pitched frequency. Tone-2-coupled noise retains its source tone period and
nominal shift clock. Repeated noise-control writes and their reset requests
remain visible even when the register value is unchanged. T6W28 cross-chip
routing is inventoried but is not implemented by the AY adapter.

`dump_analysis` writes `.dcsg.writes.csv`, `.dcsg.states.csv`,
`.dcsg.segments.csv` and `.dcsg.analysis.json`. State indexes connect snapshots
and Segments to raw writes. Segment boundaries are control changes, not
invented note attacks: DCSG has no KeyOn register.

## Separate musical projection

`py/dcsg_music.py` derives sample-time `PsgSegment` views for the inherited
`mml_envelopes.extract_notes` and `pre_envelope_loops.prepare` routines. The
native DCSG Segments remain under `SourceBundle.segments['dcsg']`; they are not
replaced by three-channel target data. Target views retain source Segment IDs
and have explicit `target_period` and `target_noise_period` fields in the
2 MHz MUCOM virtual-SSG convention. The renderer must not scale those periods
again from the DCSG source clock.

One target AY engine is assigned per DCSG instance: the first uses internal
MSX PSG and the second uses OPNA SSG. All three source tones are retained.
An audible noise voice first uses a silent tone slot. If all three tones sound,
noise shares the slot whose common amplitude minimizes nominal squared tone
and noise gain error, with stable allocation on ties. This preserves a noise
contribution but cannot recreate four independently controlled DAC levels on
three AY channels. The shared-gain intervals are explicitly diagnosed.

The initial amplitude model approximates DCSG's 2 dB attenuation steps with
nominal 3 dB AY levels. It is not a measured DAC-equivalence claim. Noise is
also approximate: AY and DCSG have different LFSRs, and DCSG periodic noise is
not AY white noise. Noise shift clocks are rounded/clamped to the AY register
range with interval diagnostics. Stereo and source phase are not reproduced
by these mono musical views.

Inspect `.dcsg.ay_views.csv`, `.dcsg.ay_allocation.csv`,
`.dcsg.ay_assessments.csv`, and `.msx.notes.csv` before comparing final output.
The native source-analysis issues remain in bundle metadata. Unmapped active
source commands fail rather than being silently discarded.

## References and validation scope

Header/variant interpretation was checked against the primary
[libvgm VGM player](https://github.com/ValleyBell/libvgm/blob/master/player/vgmplayer.cpp)
and register/clock behavior against its
[SN76496 core](https://github.com/ValleyBell/libvgm/blob/master/emu/cores/sn76496.c).
The implementation is an independent register decoder, not copied emulator
code. Authored tests cover single/dual latches, exact waits, unknown state,
stereo, period-zero variants, coupled/repeated noise, source-loop replay,
source retention and derived AY voice allocation. Passing these tests alone
does not establish acoustic equivalence or a successful MUC/VGM round trip.
