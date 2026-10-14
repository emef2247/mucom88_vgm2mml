# Declared VGM loops and reader replay

The shared command cursor follows the VGM header's declared loop address.
It does not discover a source loop by matching musical phrases.

## Reader and Segment boundary

command_times(raw, loop_count=1) and parse_vgm(..., loop_count=1) retain
single-pass behavior. loop_count is the total number of traversals of the
declared loop body, not the number of additional repetitions. A file without
a declared loop is still read once.

At an intermediate 0x66, the cursor jumps to 0x1C + loop_offset. It retains
wait overrides and every consumer's chip/stream state, suppresses the
intermediate EOF, and continues the shared sample clock. No Key-On, Key-Off,
chip reset, or PCM decoder reset is inserted. Invalid command addresses and
zero-duration loops are rejected when replay is requested. Data-bank blocks
are loaded once, preserving stream block IDs on replay.

The ordinary State/Segment builders interpret these ordered occurrences.
Finite repetition discovery and target compaction reuse existing routines.

## Default MDX output

Native OPM and musical PSG/SCC conversion request two source traversals.
The first pass retains initialization and the first traversal. MDX's L
marker begins the second traversal, which is repeated indefinitely. This
warm-up pass matters when a write unchanged on the first traversal becomes
a state change after returning from the source EOF.

The projected PSG/SCC OPM VGM carries the already-expanded loop boundary.
The native backend reads that derived VGM once, avoiding double expansion.

All emitted FM tracks and the PCM track share the same projected loop start
and end. These boundaries constrain output normalization. Finite MML repeats
cannot span L, and target setter compaction forgets its incoming cache at
L so the entry retains absolute settings.

A gate crossing L remains explicit OPM controls and time advances. It is
not converted into a newly attacked note at the boundary. An unfinished FM
gate at the expanded end also remains raw controls; looping does not insert
a terminal release.

PCM requires independently playable PDX samples. A playback crossing the
repeat boundary, or continuing through the expanded EOF, is rejected: the
decoder continuation has no verified independent-note representation.
This also applies to best-effort; there is no defined fallback for it.

--no-song-loop selects a finite single source pass. --no-loops controls
finite repetition compression only and does not disable the declared song loop.
The legacy MDX renderer and PSG/SCC held-register compatibility route currently
require --no-song-loop for declared-loop inputs. MGSDRV behavior is unchanged.

## Inspectable metadata and passes

With --dump-passes, <stem>.vgm.loop.csv retains:

- loop_offset, loop_address: original header-relative and absolute address;
  offset zero means no declared loop.
- header_loop_samples: the header's declared sample count.
- status: validated command boundary, no loop, or an inspection failure.
- loop_start_samples, decoded_end_samples, decoded_loop_samples: original
  first-pass observations, independent of header duration claims.
- loop_start_event_id: original occurrence at the source loop entry.
- loop_count, playback_end_samples: actual bounded replay scope.
- replay_start_samples, replay_start_event_id, replay_loop_samples:
  the second traversal's boundary and measured duration.

Native OPM Raw/State/Segment tables retain original_event_id,
original_vgmticks, loop_iteration, original byte address, and ordered
occurrence event_id/source_event_id. Iteration zero is the original pass.
The reader's timing CSV includes occurrence_event_id explicitly. PCM timing
provenance is generated on demand from retained raw bytes and replay count,
avoiding an additional object per byte-supply command during analysis.

The normalization JSON and TXT report identify loop emission and the expanded
playback scope. Source statistics still describe one original stream pass;
compiled encoded-command counts are not equivalent to that source count.

## Validation scope

Public/synthetic checks cover wait-override carry, shared PCM/FM timing,
held gates, same-sample command order, source state on subsequent traversals,
finite opt-out, and real compiled F1 destinations. Compiler/replay acceptance
does not prove native MMDSP behavior or audio equivalence. Native listening is
performed separately by the user.
