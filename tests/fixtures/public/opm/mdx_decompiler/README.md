# MDX decompiler structure references

Four original synthetic MIT fixtures verify that a compiled MDX can provide an
independent structured MML reference. No commercial music, extracted voices or
private fixture data is included. The nested phrase score reuses this repository's
original `from_mdx/nested_phrase_loops` fixture; the other scores are newly authored.

| Case | Encoded structure / expected single-traversal attacks |
| --- | --- |
| `nested_phrase_loops` | Two inner traversals within three outer traversals; 24 attacks |
| `repeat_exit` | `[c d / e f]3`: two complete traversals and a shortened third; 10 attacks |
| `song_loop` | Two-note intro then `L [e f]3 g r8`; 9 attacks |
| `tie_controls` | One held note across a tie, then pan change and a new note; 2 attacks |

Each case keeps its hand-written `*.source.mml` separate from `reference/*.mdxtools.mml`.
The reference directory also retains the original compiled MDX and untouched
`mdxdump` output. The manifest records hashes, compiled MDX note numbers, observed
OPM KC bytes, authored attack ticks and the song-loop displacement. Target note
spelling and native KC labels are distinct representations; these fixtures do not
establish a new pitch interpretation policy.

Generation used mmlx 0.2.0 and soundlog 0.15.0 via the existing Rust fixture helper,
then unmodified vampirefrog/mdxtools revision
`9c8539fec2757fcf7c85d1986171b50ebe2ef1e5`. All four decompiled references recompile
to byte-identical MDX and VGM with that helper. This proves the checked examples
within this tool chain; it does not prove arbitrary MDX decompilation or hardware
playback equivalence.

For each case, from the repository root in WSL:

```bash
case=repeat_exit
folder=tests/fixtures/public/opm/mdx_decompiler/$case
scripts/mdx_fixture_generator/target/release/mdx-fixture-generator \
  "$folder/$case.source.mml" "$folder/reference/$case.mdx" "$folder/$case.vgm"
outputs/mdx-reference-tools/mdxtools/mdx2mml -u "$folder/reference/$case.mdx" \
  > "$folder/reference/$case.mdxtools.mml"
outputs/mdx-reference-tools/mdxtools/mdxdump "$folder/reference/$case.mdx" \
  > "$folder/reference/$case.mdxdump.txt"
```

Regeneration overwrites artifacts. Review their musical intent, bytes and timing
before updating manifest hashes. Automated tests require no external binaries;
external compilation/replay is a separate development check. Reference text is
never input to the VGM converter and never an exact-text expectation for its output.
