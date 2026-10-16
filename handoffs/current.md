# Active checkpoint: PSG carried-state song loops (2026-10-16)

## Current implementation

- Direct PSG -> OPNA SSG and MSM6258 -> ADPCM-B/DELTA, scoped --force and
  capacity omissions are saved in the working tree. See docs/psg_to_ssg.md,
  docs/mucom_force.md and docs/pcm_interoperability.md for exact scope.
- PSG musical notes reuse MSX mml_envelopes.extract_notes on an original-sample
  view; native Segments remain intact. Software entry resets E, internal decay
  stays held; HW envelope continuity/global shape actions remain separate.
- Normalization retains all actual PSG controls (redundant/inactive/EOF), with
  shared bounded clocks and positive shape-trigger protection.
- User's last PSG regression18/20:05/14 failed because original loop-head state
  differs from EOF-carried state, not missing timestamps. User now authorized
  retaining the initial pass and replaying loop source commands from EOF state.

## New carried-state implementation

PSG-containing MUC routes with enabled song looping request2 traversals using
shared command_times/parse_vgm. Interim EOF is suppressed, chip/stream/wait state
carried, L placed at replay_start_samples (first EOF). NonPSG routes retain
previous loop policy. --no-song-loop remains finite1pass. Mixed native FM/PCM,
MSM6258 and SegaPCM use the same replay count/end/boundary; external PCM equality
is asserted. Existing eligibility, phase, resource and capacity guards remain.

MUC SourceOccurrence and PSG native rows retain unique occurrence source_event_id,
original_event_id, loop_iteration and original_vgmticks. Reader timing CSV maps
both IDs. Short FM omission now uses occurrence IDs, so one short replay cannot
remove its long original gate. Original EOF and expanded EOF remain separate in
song_loop report. No source resets or forced seam bypass introduced.

## Validation

Saved implementation before tests.12 distinct focused authored cases pass:
8existingPSG cases,3new replay cases (delayed state/EOF/provenance/finite mode,
carried wait override, mixedPSG+MSM timeline),1new mixedFM omission case.
No private reconversion, broad regression, compiler execution or listening.
Persistent authored intermediate/MUC inspection:
outputs/diagnostics/psg_carried_loop_20261016. Original loop2000/EOF5000;
replayL5000/expandedEOF8000. Original head tone288, carried head352;
stable_carry proof, original/occurrence source mapping, L/ties inspected.
Focused read-only architecture review found no blocker; Sega diagnostic wording
now refers to rendered loop-body freezing rather than falsely claiming1pass.

## Next allowed action

User owns DSLAYER4/other private regression and native listening. Inspect actual
reported failures before further changes. Do not run private failed-song checks,
relax phase/decoder/capacity guards, disable looping implicitly or change timeout.
OPM -> OPNA and SCC -> YM2608 remain later work; surplusFM channels still need the
agreed explicit omission option. No stage/commit/push/branch changes by agent.
External notes:
field_notes/2026-10-16_psg_carried_loop_replay.md
field_notes/2026-10-16_psg_remaining_loop_state_mismatch.md
