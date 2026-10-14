# Structured MML references from compiled MDX

MDX stores compiled finite-repeat starts/ends, last-iteration escapes and
performance-end loop jumps. `vampirefrog/mdxtools`' `mdx2mml` renders these as
`[...]N`, `/` and `L`, including nested finite repeats. This provides independent
evidence about structure that the corresponding VGM has expanded into register
events. It cannot recover original source formatting or macro names.

Primary implementation references:
- [Binary format and commands](https://github.com/vampirefrog/mdxtools/blob/9c8539fec2757fcf7c85d1986171b50ebe2ef1e5/docs/MDX.md)
- [Decompiler implementation](https://github.com/vampirefrog/mdxtools/blob/9c8539fec2757fcf7c85d1986171b50ebe2ef1e5/mdx_decompiler.c)
- [Actual command-line options](https://github.com/vampirefrog/mdxtools/blob/9c8539fec2757fcf7c85d1986171b50ebe2ef1e5/mdx2mml.c)

The upstream format document identifies itself as a work in progress. Keep source
bytes and measured behavior authoritative when documentation is ambiguous.

## Current scope

Reuse established MGSDRV/shared continuity and repetition processing where it
preserves OPM/MDX semantics. MDX macroization is excluded from the current work:
source macro syntax and preprocessing depend on the compiler. Finite loops and
song-loop markers remain in scope. Existing MGSDRV macro support is unchanged.

The canonical converter continues to take VGM only. Decompiled MML is a diagnostic
reference and a fixture-selection aid; it must not guide conversion by supplying
structure unavailable in the VGM. Matching decompiled text is not a correctness
criterion. Compare expanded performance and inspect source/target structure
separately; equally valid repeat factorizations may differ.

## Preparing references

Build only the external tools needed for inspection, in an ignored directory.
No global installation or third-party source inside the converter is required.
The recorded revision below is the one verified in this repository:

```bash
bash scripts/setup_tools.sh --with-mdxtools
python scripts/decompile_mdx_references.py INPUT_MDX_OR_DIRECTORY \
  --outdir outputs/mdx-references/run-001 \
  --mdx2mml .tools/mdxtools/mdx2mml \
  --mdxdump .tools/mdxtools/mdxdump \
  --tool-revision 9c8539fec2757fcf7c85d1986171b50ebe2ef1e5
```

Use a fresh empty output directory, outside the input tree. The script preserves
untouched MML, tool stdout/stderr, arguments, exit status, executable hashes,
declared upstream revision and MDX/MML hashes. Partial/failed runs remain evidence
and are never classified as successful references. Existing authored MML is kept.

The structure audit checks repeat nesting/counts, exit attachment and song-loop
marker counts against `mdxdump`. It does **not** validate jump destinations, exact
exit locations, notes, timing, control trajectories or playback equivalence.
`decompiled` means the tools completed and this limited audit found no issue;
it does not certify a recovered original score. `needs_review` also covers
commands the decompiler omits, including PCM8 enable and fade-out. Inspect the
original MDX and replay evidence before accepting a reference as an oracle.

## Evidence and pairing

Keep original MDX, paired source VGM, decompiled MML and generated VGM separate.
Same-stem MDX/VGM pairing is provisional until native timed OPM writes are
compared. Save input hashes and the comparison result. Playback length and loop
count are part of that provenance.

Public regression cases are in
`tests/fixtures/public/opm/mdx_decompiler/README.md`; they cover nested repeats,
repeat exits, song loops and tied-note continuity. Private references stay under
ignored `outputs/` or the existing `tests/fixtures/local_only/` layout. Do not
promote private derived MML into public fixtures or paste its contents into notes.

For the song-loop public case, track A's F1 command is at byte offset 23 and
contains displacement -16. Its target is `23 + 3 - 16 = 10`, relative to the end
of the three-byte command. `mdx2mml` restores `L` before the repeat at that target.
The decompiler's signed-offset expression includes this three-byte origin
adjustment; it is not evidence of an off-by-three bug in this case.

See `field_notes/2026-10-07_mdx_decompiler_references.md` for measured coverage
and limitations of the current external tool chain.
