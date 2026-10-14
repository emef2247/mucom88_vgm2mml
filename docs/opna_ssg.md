# OPNA SSG to OPM and whole-song allocation

The automatic OPNA MDX route now handles native FM, SSG tone and supported
ADPCM-B together. `opna_ssg` interprets SSG source evidence independently;
`opna_ssg_opm` reuses the existing PSG FM synthesis/performance projection.
PCM remains on its separate MSM6258 P-track/PDX path.

```sh
python scripts/export_mdx.py input.vgm --outdir outputs/listen/opna \
  --no-vgm --pcm-policy best-effort
```

Use a separate output directory for fixtures. `--dump-passes` on `vgm2mml.py`
keeps source and target passes. Actual MDX/PDX playback remains a separate check.

## Source evidence

`opna_ssg.build_segments` consumes the existing native OPNA raw-register CSV,
not the FM projection. Port-zero registers `00..0f` reconstruct three immutable
PSG-compatible SSG parts. Raw 16-register snapshots remain separate from masked
tone period, amplitude, noise period and envelope shape. Initial registers zero
and prescale6 are explicit reset assumptions, with zero initial volume.

Segments retain integer 44100-Hz start/end samples, zero-duration changes,
expanded/original event IDs, source command addresses, original samples and loop
iterations. Repeated register13 envelope strobes and I/O writes remain evidence.
No fake terminal source mute, envelope tail or quantized boundary is added.

The effective AY-compatible clock is master/4 at prescale6, master/2 at
prescale3 and master at prescale2. Held SSG pitch updates at prescaler changes
without an inferred key restart. Source periods remain unchanged.
This follows [YMFM's YM2608 effective SSG clock](https://github.com/mamedev/mame/blob/master/3rdparty/ymfm/src/ymfm_opn.h)
and [prescaler selection](https://github.com/mamedev/mame/blob/master/3rdparty/ymfm/src/ymfm_opn.cpp).

## Target channel allocation

Allocation is fixed for the whole song, before target writes are generated:

1. Any native FM key activity reserves its part, including zero-duration key
   operations, temporarily disabled outputs and later release tails. Register
   initialization alone does not reserve an OPM channel.
2. Reserved FM parts keep the vgm-conv mapping: source0..5 -> OPM0,1,2,4,5,6.
3. Used SSG parts take vacant OPM channels in ascending source/target order.
   A part is used if its mixer allows output and fixed volume is nonzero or
   hardware envelope mode is selected. This is conservative source usage,
   independent of later MDX short-note omission.
4. A used SSG part beyond eight total OPM channels is dropped with its reason.
   All source Segments remain available. There is no dynamic channel stealing.

Six used FM parts leave OPM3 and7 for SSG0 and1; SSG2 is dropped when all three
SSG parts are used. Five FM parts can accommodate all three SSG parts. PCM
does not consume one of these eight FM channels. Unallocated FM setup/key-off,
output-enable and prescaler pitch writes are filtered from the target so they
cannot overwrite a newly assigned SSG voice. Native evidence remains intact.

Only retained SSG parts are projected/validated. Unsupported target modes on a
capacity-dropped part do not reject retained FM/SSG, but the complete source
state and the capacity omission remain inspectable.

## PSG synthesis reuse and limits

SSG uses `psg_scc_opm.project(..., psg_model='fm')` and `build_performance`:
the existing AL4/FB7 voice, multiplier profile, fixed-level TL curve, nominal
OPM pitch helper and audibility-edge gate inference. Pitch/level changes during
an audible span keep the inferred gate. This is the same square-tone
approximation as ordinary PSG import, with unverified phase/gain equivalence.
Noise and hardware envelopes on allocated parts retain the same explicit
rejection as the existing PSG target; their source state is not discarded.

FM and SSG writes merge by exact source samples, expanded source event IDs and
within-event write order. The generic PSG quantized schedule is not serialized
into the mixed source stream. Downstream OPM/PCM then share the existing MDX
clock and normalization. For song loops, synthetic SSG EOF release/mute is
disabled, preserving held gates across the replay boundary.

## Artifacts

- `.opna_ssg.segments.csv` / `.analysis.json`: native SSG fields and assumptions.
- `.opna_channel_allocation.csv` / `.json`: all six FM and three SSG parts,
  assigned channels, unused parts and capacity exclusions; retained by default.
- `.ssg.opm_target.*` / `.ssg.opm_performance.*`: the reused canonical PSG
  synthesis/performance before final channel remapping, when passes are requested.
- `projected_opm/<stem>.source_map.csv`: final mixed writes with `opna_fm` or
  `opna_ssg` provenance and original samples, IDs, source channel/row and final
  target channel. Synthetic voice setup/release is explicitly marked.
- `.opna-opm.json`: channel allocation, SSG model/inferred attacks and losses.

Native FM analysis still marks SSG writes as outside its FM interpretation;
the separate SSG source module consumes them. That source boundary is preserved
for future MUCOM FM/SSG validation, which this X68000 target change does not run.
