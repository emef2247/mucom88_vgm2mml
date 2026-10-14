# External MDX fixture generator

This Rust utility compiles newly authored UTF-8 MDX MML
with mmlx 0.2.0 and plays its MDX into VGM with soundlog 0.15.0. It is separate
from the Python VGM/Segment engine. No third-party compiler source is copied
here. Dependencies are pinned by Cargo.lock.
The native PCM converter also uses typed PCM assembly and PDX packing; OPM-only
VGM-to-MML conversion does not require this executable.

For listening exports, `../export_mdx.py` defaults to native MXC for FM-only
MML and calls this utility's `--from-mdx` mode to replay the compiled file.
The OPM roundtrip verifier also defaults to MXC and uses this replay mode.
This utility's own MML compilation modes continue to use mmlx. Typed PCM
generation also retains mmlx for its FM portion.

```bash
cargo build --release --locked --manifest-path scripts/mdx_fixture_generator/Cargo.toml
scripts/mdx_fixture_generator/target/release/mdx-fixture-generator input.mml output.mdx output.vgm
```

The MML compilation mode requires three positional paths. Playback modes load
the PDX named by the MDX header from beside the input MML/MDX, write a
4 MHz OPM VGM, and emit the intro plus one song traversal. Finite nested
repeat counts remain effective. Errors are printed to stderr with a nonzero
exit status; the Python batch regeneration script reports them. Generated
outputs are explicitly overwritten, so use dedicated output paths.

Public cases and regeneration commands are documented in
`tests/fixtures/public/opm/from_mdx/README.md`.

For fine-tick register replay, append `--max-ticks N` to set a positive playback
tick budget. The roundtrip verifier derives this budget from the projected
source end; the default 100000 ticks covers only 25.6 seconds at @t255.
See [OPM MDX target](../../docs/opm_mdx.md) for conversion and comparison.

## Existing MDX playback and inspection

Play an existing MDX directly, preserving its compiled commands and tone bank:

```bash
mdx-fixture-generator --from-mdx input.mdx output.vgm --max-ticks 1000000
```

This reads the original MDX without rewriting or recompiling it. The same
4 MHz clock, PDX lookup, and single traversal policy apply. The VGM
may be saved beside the MDX when preparing private input fixtures.

Inspect the compiled tone bank without running playback:

```bash
mdx-fixture-generator --inspect-mdx input.mdx tones.csv
```

The CSV contains each local voice ID, algorithm, feedback, enabled operator
mask, and operator parameters in `m1, m2, c1, c2` order. IDs are local to each
MDX bank: compare complete parameters when comparing different files.
The inspector uses soundlog's typed MDX parser; it does not infer SCC-to-FM
conversion rules or simulate the resulting sound.

## Compile standard PCM without replay

Canonical Python conversion invokes direct PCM assembly:

```bash
mdx-fixture-generator --compile-pcm FM_ONLY.mml PLAN.tsv OUTPUT.mdx
```

PCM MML is not compiled in this mode. FM tracks/tones/title are compiled by
mmlx and retained; a strict typed PCM plan is added using soundlog's existing
MdxBuilder. Serialization/reparse verifies standard nine-track layout and
preservation of FM commands. PDX is resolved beside OUTPUT, not beside the
FM-only input. Missing/empty references, native sample lengths above 65535,
inconsistent tempo/duration and combined MDX above 65535 bytes are errors.
Finite FM repeats and escapes are counted; infinite jumps and sync waits are
outside this mode. Structural success does not certify PCM runtime playback.

The plan has exactly three tab-separated columns, `kind`, `value`, `ticks`.
First come metadata rows `pdx_name`, `tempo` (raw operand), `end_tick`.
Then bank (0), frequency (0..4), pan (PCM raw 0..3), gate (8), volume (raw 128),
hold (empty), note (slot 0..95, 1..256 ticks), rest (empty value, 1..128 ticks),
and a final end (empty). All controls precede the first note; a hold immediately
precedes its note. Python's target projection owns quantization and chunking.

For manually authored or readable PCM MML, the existing mode remains:

```bash
mdx-fixture-generator --compile-only input.mml output.mdx --pcm-mode standard
```

Generated PCM MML targets one standard PCM track `P`. mmlx 0.2.0 normally
compiles any `P..W` track into the extended sixteen-track layout. The explicit
`--pcm-mode standard` option verifies that `Q..W` contain no commands except
track endings, removes the automatically inserted PCM8 marker, and selects
the standard nine-track layout. It preserves the encoded PDX bytes and does
not introduce a replacement compiler. Active `Q..W` tracks are rejected.

PCM replay is currently unavailable: pinned soundlog 0.15.0 loses raw sample
continuation at held-note/control boundaries and does not reproduce native
per-note stop/reset behavior. Normal compile-and-replay still writes the
validated MDX, then reports this limitation and removes any stale requested
VGM. `--from-mdx` reports the same limitation for a package containing PDX.
Use the compiled MDX+PDX pair in an X68000 player for playback. FM-only replay
continues to work. No resampling or decode/re-encode workaround is applied.

## Build a PDX from encoded samples

```bash
mdx-fixture-generator --build-pdx manifest.tsv output.pdx
```

The UTF-8 TSV manifest has exactly this header and four fields per row:

```text
sample_id	bank	slot	file
sample_000	0	0	samples/000.adpcm
sample_001	0	95	samples/001.adpcm
```

Files must be within the manifest directory, using relative paths. This
initial backend supports bank 0 and slots 0–95. Each sample ID and slot must
be unique. Empty files, missing files, and samples larger than `0x00ffffff`
bytes fail. The helper validates the total PDX address range before using
soundlog's `PdxBuilder`; it performs no decode, resampling, encoding or
compression. Odd lengths are retained. Unassigned slots are empty.

The previous output is invalidated on failure, and a successful PDX is
written through a temporary file and atomic rename. MML and PDX assignments
must come from the same conversion binding table.

For replay, the PDX header name must be a single filename. Exact spelling is
preferred, followed by a unique ASCII case-insensitive match. Missing or
ambiguous names, directory traversal, files resolving outside the input
directory, and PCM notes referencing empty slots produce explicit errors.
