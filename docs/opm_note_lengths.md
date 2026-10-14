# MDX note lengths and safe target-clock correction

```sh
python vgm2mml.py input.vgm --outdir outputs/input --dump-passes
```

Each native track begins with `/* Track A */` through `/* Track H */`.
Structured and legacy note rendering use exact MDX duration values without
requiring normalization. The whole-note basis is 192 ticks. Supported values
include `1`, `2`, `3`, `4`, `6`, `8`, `12`, `16`, `24`, `32`, `48`, `64`, `96`,
`128`, `192` and representable one/two-dot values. A duration of 13 ticks uses
`%13`. Long notes use whole-note chunks joined by ties; a held slice also ends
with a tie so that a control change does not create a new attack.

This formatting preserves tick lengths. Target-clock correction is separate
and defaults ON for `--notation structured`. Use `--no-normalize-lengths` to
disable it; explicit `--normalize-lengths` is still accepted. Other MDX notations
default OFF, and MGSDRV retains its separate opt-in correction.
It runs after native analysis and before note/trajectory keys and loop planning.
Reference MML is never an input. MDX macroization remains outside the task.
Rejected or abstained correction retains the same structured renderer and
baseline clock/timing projection. It never changes notation to legacy/registers.
PCM-only and OPM+PCM inputs participate in the same candidate correction.
PCM playback starts contribute attack anchors; playback start/end and all PCM
controls constrain the shared clock. Encoded byte supply timing is not treated
as a musical attack grid. Enabling correction is not an input rejection reason.
PSG/SCC projected output checks original source boundaries and cumulative timing
through the intermediate OPM lattice before accepting a nominated clock.

## Reuse and target boundary

`py/opm_note_normalization.py` feeds native OPM rising Key edges and PCM playback
starts into the shared
MGSDRV estimator in `py/note_normalization.py`. The current estimator uses
clustered attacks, at least 16 anchors, candidate tempos 80..200, and 12/6-step
grids. It requires at least 95% anchor coverage. Its correction tolerance is
the smaller of 735 samples and a quarter of the smallest usable inter-onset
interval. These are estimator heuristics, not YM2151 timing specifications.

The fitted tempo/grid nominates one actual MDX timer multiplier, rounded from
the fitted sample period per score step. Every target boundary then uses the
existing nearest absolute MDX tick conversion on that timer. The source origin
remains sample zero. The fitted phase and grid are recorded but are not used to
snap individual attacks. MGSDRV gate inference, `q` generation and short-state
pruning are specific to that compatibility target and are not applied to OPM.

The 12/6 values are score-grid subdivisions, not a rule that discards or snaps
source intervals of six ticks or fewer. The 735-sample cap is a maximum timing
correction (one nominal 60 Hz frame), not a minimum MDX interrupt period.
Neither the attack IOI nor that cap alone selects the timer: intervening control
and PCM boundaries also constrain it. Since the 2026-10-11 output policy,
sparse or irregular material also tries bounded target quantization; a missing
musical fit alone no longer prevents correction.

Before clock normalization, complete source Key-On/Key-Off gates of at most
352 samples (8 ms at 44100 Hz, rounded down) may be omitted from the target.
`--normalization-ms N` changes both this original whole-gate omission threshold
and the maximum absolute target boundary movement. It defaults to 8, accepts
positive finite values of at least one source sample (1/44.1 ms), and is inactive
with `--no-normalize-lengths`. `vgm2mml.py` and `scripts/export_mdx.py` pass it
through OPM, PSG/SCC and PCM paths. For example:

```sh
python scripts/export_mdx.py tests/fixtures/public/opm/from_fm \
  --outdir outputs/listen/opm/from_fm_16ms --no-vgm --normalization-ms 16
```

The decision covers the whole sounding gate, never a short control slice in
a longer note. Partial changes, repeated Key-On requests, unclosed gates and
gates crossing a loop boundary are retained conservatively. PSG/SCC uses the
original source-map timestamps before intermediate OPM rounding. Source
Segments, State and PCM IR remain unchanged.

`py/opm_output_normalization.py` tries the fitted clock first, then multipliers
65 down to 1. Multiplier 65 (16.640 ms) is a preferred fallback based on the
user's approximate 60 Hz goal, neither a chip limit nor a guaranteed minimum
period. Projected boundaries stay within 352 samples by default.
With a configured value, the preferred fallback multiplier scales from 65
in proportion to the integer sample threshold, up to 255. This permits trying
slower clocks with larger tolerances. The musical fit is tried first, so a
larger parameter does not guarantee a longer period. Both tolerance and
omission use the configured threshold rather than a fixed 352 samples.
Surviving sounding gates, longer rests, side-effect pulses and loop spans remain positive. Short rests
and ordinary setter intervals may coalesce; both ordered Key-Off/Key-On
requests remain present. Lost rest duration is reported separately from
omitted notes. Absolute time is rounded without accumulating interval errors.
An invalid loop or failed bound/protected interval check keeps the structured
baseline. `--no-normalize-lengths` disables this policy. The earlier conservative
fitted-clock helper remains internally available for regression checks.
For PCM, the candidate PCM projection and structured score are checked before
writing binaries. A candidate-specific failure keeps the baseline clock for
both OPM and PCM; source eligibility failures remain ordinary diagnosed errors.
The selected multiplier drives FM tempo and PCM target commands together.
Source PCM IR, encoded samples and their PDX payloads remain unchanged.
Short PCM playback spans may be omitted only after source eligibility checks;
omission cannot make unsupported PCM representable. Rest-only PCM target
plans are allowed after all short spans are omitted. Normalization JSON/CSV
and `*.pcm_omitted_playbacks.csv` preserve the loss evidence.

Raw/state traces and native Segment fields retain observed values. Corrected
target ticks and source identifiers remain available for inspection. Normalized
note and control-trajectory keys go into the existing shared loop planner;
loop expansion must equal the uncompressed corrected target stream. A more
regular duration pattern can enable repeated units, but no loop-count increase
is promised for an individual song.

## Short relative setters

Target compaction after loop planning also chooses short relative octave and
volume commands. From a known value, a one/two-step change uses `<`/`>` or
`(`/`)` if its length is no greater than the absolute token. Equal values keep
the existing redundant-setter omission. Larger changes and unknown initial
values retain absolute commands. This runs with or without normalization.

The inspected MSX implementation has two rules: the current
`compact_state_token` selects only a one-step relative change, while older
MGS helpers allow up to three steps. MDX uses the user's requested maximum of
two characters. The coarse `v0..15` and fine `@v0..127` modes are tracked
separately; changing modes retains the first absolute command. In the tested
mmlx 0.2.0 compiler, `@vN` encodes `255-N`, and each relative volume step changes
that encoded value by one with the appropriate direction. This is target
volume state, not an inferred change to source operator levels.

Every finite-loop entry clears the compactor's known state so its first setter
is absolute for both initial and repeated entry. Voice loading and raw tone/TL
writes invalidate cached volume/pan state. Raw pitch writes retain their
explicit register spelling and do not alter the compiler's octave setting.
Before/after replacements and byte-saving estimates are recorded with action
`relative_setter` in `*.mdx.structure.compaction.csv`; the uncompacted MML
retains absolute setters. Two volume steps save text against `@vN` but can have
the same compiled byte cost as an absolute volume command. Octave syntax is
resolved by the compiler, so shorter octave text does not itself save MDX bytes.

Independent compiler/player comparisons cover octave changes, both volume
scales and their limits, mixed modes, nested repeats with different entry/end
values, held controls and raw writes followed by voice loading. Effective
state, Key timestamps/masks and the end sample must match the absolute version;
loop-token equality alone cannot establish relative-state equivalence.

## Reports

`<stem>.mdx.normalization.json` records requested/enabled/adopted, selected clock,
applied/unchanged status (also for disabled/non-applicable correction),
the reason, original and nominated MDX timing, fitted estimator parameters,
actual worst correction, boundary kinds and event IDs. Rejected candidates
retain collision count and up to ten collision examples where available.

With `--dump-passes`:

- `<stem>.mdx.normalization.csv` records source controls and end/loop boundaries,
  before/candidate ticks and sample times, errors and the acceptance status.
  PCM playback/control boundaries participate in the same table; `source_chip`
  distinguishes OPM controls, PCM controls and shared boundary evidence.
  Candidate columns remain candidate evidence if correction is rejected.
- `<stem>.mdx.before.normalize.mml` records the conventional target when
  correction is applied; the JSON then includes before/after structure counts.
  With PCM, this MML includes the baseline-clock P track and PDX reference.
- Existing trace, Segment, control, structure and timing dumps remain available.
  Integrated Segment-to-target membership may change while native fields do not.
  `score_clock_inference` is explicitly the conventional, pre-normalization fit;
  the chosen target clock is recorded in the main timing fields.

A new run clears old normalization artifacts first. A rejected candidate does
not leave an old successful before-MML behind. If fitting abstains before a
candidate exists, only the normalization JSON is written. No output is written
into source fixtures when a separate `--outdir` is supplied.

## Verification

```sh
python scripts/verify_opm_mdx_roundtrip.py input.vgm \
  --outdir outputs/input-check
```

The external check still requires exact projected Key edges, effective state
and target end time. With accepted normalization it uses the recorded source
correction bound for the explicit source-timing comparison. Conventional output
and rejected correction retain the existing six-sample source tolerance.
Normalization therefore proves a bounded timing change, rather than exact
source-time equivalence. The exact-formatting replay tests separately compare
old step chunks and named durations, including held controls and nested ties.
See [validation notes](../field_notes/2026-10-07_mdx_note_lengths.md).
