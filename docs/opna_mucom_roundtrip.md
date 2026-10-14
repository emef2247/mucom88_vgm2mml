# Diagnostic OPNA FM roundtrip

The validation path is `VGM -> native State/Segments -> diagnostic MUCOMVGM
register-MML -> VGM -> native State/Segments`. It is a development check,
separate from the X68000 converter's MDX output. It establishes preservation
of modeled FM controls, not acoustic equivalence or musical note/voice MML.
PCM, SSG and rhythm remain outside this comparison. Source ADPCM-B IR is
saved for inspection, but the returned diagnostic MUC contains FM only.
MSM6258 transcoding and MDX/PDX generation belong to the X68000 target path;
native PCM MUCOM roundtrip work is deferred.

```bash
python scripts/verify_opna_mucom_roundtrip.py tests/fixtures/public/opna \
  --outdir outputs/opna/mucom_roundtrip
python -m unittest discover -s tests/scripts -p 'test_opna*.py' -v
```

## Local MUCOM88 fixture preparation

```bash
python scripts/prepare_mucom88_fixtures.py \
  tests/fixtures/local_only/opna/mucom88/mucom88win_yk2mml191012
python scripts/verify_opna_mucom_roundtrip.py /path/to/selected.vgm \
  --outdir outputs/opna/selected_roundtrip \
  --fixture-manifest outputs/local_only/mucom88_fixture_preparation/manifest.json
```

Preparation removes loop-command `L` from main MUC music/macro bodies while
keeping comments, strings and titles. Backup directories such as `org/` are
excluded. It backs up local source files, preserves source encoding/newlines,
and compiles temporary UTF-8 inputs beside the original dependencies. VGM is
published beside each MUC only after compilation succeeds. MUB is not used or
created. Manifest JSON/CSV retain MUC, compiler-input, compiler and VGM hashes,
logs and success/failure status. Fixtures and generated songs stay local-only.

MUCOMVGM does not parse native `@%` raw voice blocks. Preparation overlays
their exact 25 register bytes into temporary DAT slots and points only the
temporary input at that DAT. Original MUC/DAT files remain intact. This is a
compiler adaptation, not substitution of another voice. MUCOMVGM's DAT loader
masks DR register AM bit 7; successful compilation does not establish original
MUCOM88 musical/voice equivalence. The roundtrip reference is the actual
generated VGM. The manifest option checks that VGM and its original edited MUC
still match the recorded compiler generation.

## FM comparison profile

FM checks retain compiler-control order, semantic State and operator edges.
The report explicitly marks PCM as `not_compared` and records the number of
source PCM Segments retained in the source passes. Source PCM evidence does
not contribute to the FM pass result. There is no PCM calibration prelude,
PCM playback injection or timeline rebasing in this runner.

The runner uses WING's `mucomvgm.exe` v0.0.5 under
`.tools/mucomvgm-0.0.5/mucomvgm/`; `--mucomvgm PATH` selects its location.
Windows Python and WSL interop are supported. The tool is a local dependency,
not bundled source or an executable redistributed by this repository. See
[source setup](opna_segments.md#mucomvgm-source-check) and
[license notice](../THIRD_PARTY_NOTICES.md#mucomvgm-005).

The current target accepts one YM2608 instance at 7987200 Hz. Different clocks,
dual instances and CSM are rejected. DAC streams and unknown/uninterpreted
register effects are also rejected: they might affect FM and must not be
silently classified as non-FM. Known SSG/rhythm/ADPCM registers and memory
blocks remain excluded evidence. `--loop-count N` checks a finite expansion
of declared VGM loops while preserving held state; it does not validate an
infinite playback loop or an audio loop seam.

## Target boundary

`py/opna_mucom.py` consumes the immutable integrated native Segment collection.
It derives operator parameters, committed FNUM/BLOCK pairs, key masks and
shared FM controls from typed State fields. Original register identifiers select
the field and retain event order/provenance; original data bytes are not simply
replayed. Shared snapshots are deduplicated by source event/instance. Target
write rows list contributing Segment IDs in `target_writes.csv`.

One MML control track carries both ports with MUCOMVGM's 0..511 `y` addresses.
It uses explicit key controls and rests rather than note events, avoiding
driver-generated voice, key and pitch changes. This is register-MML, not a
compact editable musical score. Its virtual clock `C192 T55125` maps `r%1`
to exactly one 44100 Hz VGM sample: four internal ticks at 1/4 sample per tick.
It is an accepted MUCOMVGM diagnostic extension, not an ordinary MUCOM88 tempo
or a claimed score compatible with the original PC-8801 driver.

The compiler buffers frequency-high writes per register, while native OPNA
uses shared latches. The projection emits a canonical high/low pair for every
committed pitch boundary. Standalone source high-latch writes remain in source
IR; the target need not reproduce the writer's latch bookkeeping. Reserved
operator bits are canonicalized without modifying original evidence. Prescaler
selectors use alternating unused data bytes so compiler deduplication cannot
erase a repeated address strobe.

## Comparison and artifacts

The runner independently compiles a one-sample rest to establish the compiler's
zero-time initialization prefix. It saves the initializer's VGM, passes, log
and executable SHA256 under `_compiler_profile`. The returned prefix must match
exactly, and its state remains present in subsequent comparisons. Known source
controls cannot be excused as initialization. No final key-off or wait is stripped.

For each input, `tracks/<relative input>/` contains:

- `source/`: Raw, State, integrated FM Segments, unmodeled/auxiliary evidence,
  native FM and ADPCM-B analysis manifests, and shared timing CSV.
- `target_writes.csv`, `generated.muc`, `generated.compile.log`: projection and
  exact external compiler input/output diagnostics.
- `returned.vgm`, `returned/`: external VGM and its reconstructed native passes.
- `comparison.json`: clock, sample boundaries/end, compiler-normalized control
  sequence, per-boundary known semantic State and ordered operator key edges.

`results.csv` / `results.json` summarize each input. `passed` requires all FM
checks to agree. `no_active_fm` marks sources without an observed FM attack and
is not evidence of a playable FM conversion. Non-FM domains and auxiliary
commands are reported separately. SSG, rhythm, ADPCM, timer expiry, acoustic
release and LFO/envelope phase are not compared. Unknown source fields remain
unknown expectations. Latch/raw-history fields and unused register bits are
excluded from semantic equality; committed pitches, known operator/control
values, edge order and the final held state remain checked.

## Repository boundaries and next branches

The shared source modules `vgm_io`, `vgm_timing`, `vgm_loop`, `vgm_reader` and
`opna` provide the native evidence boundary. `opna_mucom`,
`opna_mucom_roundtrip` and the diagnostic runner form the MUCOM-specific target
boundary. They can move into a future `mucom88_vgm2mml` without importing MDX
target conveniences into source State/Segments. A standalone repository will
need a small source-only reader adapter; do not duplicate the interpreter or
route it through OPM to accomplish that split.

The next MUCOM milestone includes SSG, whose musical behavior is equivalent to
PSG, alongside FM. Preserve SSG source evidence, construct inspectable Segments,
and include the13 currently `no_active_fm` patterns in SSG MML roundtrip coverage.
The current milestone intentionally validates FM only, as accepted by the user.

The X68000 branch projects native OPNA FM Segments to OPM at 4 MHz using the
vgm-conv register mapping and the existing nominal pitch helper. Source evidence, target projection and
loss/eligibility diagnostics remain separate. Normal chip usage detection will
select that route and output MDX MML; MUCOM MML is not a normal output format.
The implementation is `py/opna_opm.py`; method, provenance and notice are
recorded in README and THIRD_PARTY_NOTICES. The ADPCM-B source and MSM6258
target PCM IR are separate stages. `opna_pcm` now adapts transcoded MSM6258
samples into the existing PCM P-track/PDX path under explicit best-effort loss
diagnostics; this target connection does not alter the shared source boundary.
