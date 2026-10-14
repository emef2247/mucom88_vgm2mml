# OPM / MDX baseline

Normal conversion is `python vgm2mml.py input.vgm --outdir outputs/input --dump-passes`.
The default target is native OPM/YM2151 to MDX. See the root README for notation and track options.

`py/opm_conversion.py` orchestrates the inherited reader, native Segment analysis,
clock inference and target renderers. `py/opm_mdx_music.py` remains the current
structured renderer. It already groups a real attack with its held-control
trajectory and reuses the shared MGSDRV loop planner. Control-time slices may
still produce tied note fragments; that is a target-notation choice to inspect,
not evidence that every Segment is mechanically serialized or that the entire
converter must be replaced. Future MDX work should reuse proven shared analysis
and address concrete musical/target representation problems. This migration
changed orchestration, not those rendering decisions.

Inspect `*_trace.opm_regs.csv`, `*_trace.opm.csv`, `*.opm.segments.csv`,
`*.mdx.controls.csv`, `*.mdx.structure.*` and `*.mdx.timing.json` in order.
The native integrated Segment CSV and source timing must remain inspectable.

Use `scripts/verify_opm_mdx_roundtrip.py INPUT --outdir OUTPUT` for an external
compiler/player check. It accepts `--generator PATH`, `--notation`, `--track-layout`,
`--no-loops`, `--normalize-lengths` and `--reference-mml-only`; run `--help` for details.
Build instructions are in `scripts/mdx_fixture_generator/README.md`.
Compiler roundtrip agreement is separate from musical or structural correctness.

Current MDX work excludes macroization while retaining loop reconstruction and
reuse of established MGSDRV/shared processing. Compiled MDX can supply independent
structured MML references through mdxtools; see [reference preparation](mdx_reference_mml.md)
for commands, public cases and the limits of that evidence.

Native tracks begin with `/* Track A */` through `/* Track H */`. Note duration
formatting prefers exact MDX values, including triplet divisors and dots, with
step notation for the remainder. Optional `--normalize-lengths` corrects only
the structured target projection before shared loop planning. Source evidence
remains unchanged, and the correction has its own JSON/CSV diagnostics. See
[MDX note lengths](opm_note_lengths.md) for the clock, fallback and replay rules.
