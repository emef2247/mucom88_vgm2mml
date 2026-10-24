# Batch export package layout

`scripts/export_mucom.py` uses the same package boundary as the MSX and MDX
exporters. The original input parent path is preserved below `--outdir`.
For `INDIR/vgmrips.net/NEMESIS/song.vgm`, the package is:

```text
OUTDIR/vgmrips.net/NEMESIS/
  tracks/
    song.muc
    song.mub
    <required PCM and voice banks>
  _diagnostics/
    song/
      <attempt logs, source passes and isolated compiler jobs>
  _source_inputs/
    song.vgm
  listening_manifest.json
  results.csv
```

There is no song directory below `tracks/`. Final editable scores, compiled
binaries and their required banks stay together. Replay VGM, reports, pass
CSV/JSON and compiler work files stay in diagnostics. A single-file input uses
`OUTDIR` directly as its package. Without `--outdir`, each package is written
beside the original source files. Recursive discovery excludes `tracks`,
`_source_inputs` and `_diagnostics`, so adjacent reruns do not reconvert copies.
An explicitly supplied output tree must remain separate from a directory input.

The staged VGM/VGZ keeps its original basename and bytes. The package manifest
records its original path, source and staged SHA-256 values, and publication
ownership. Package `results.csv` uses package-relative paths; a partial rerun
keeps the latest rows for other known songs. Whole-invocation CSV/JSON summaries
are under `OUTDIR/_diagnostics/results.*`; API rows use output-root-relative
paths. `latest_attempt_status` distinguishes a failed rerun from a retained
successful bundle. `source_sha256` identifies the published generation when
products are retained; `staged_sha256` identifies the latest staged input.

Both MUCOM88 and MUCOMSX compile in isolated diagnostic workspaces before
publishing. First-time compilation failures may still publish a valid MUC and
its banks; incomplete MUBs remain unpublished. A failed rerun preserves an
existing successful bundle and records the failed attempt separately.

Publication and its ownership commit are rolled back together on a write
failure; temporary copies are cleaned up. Only recorded, hash-matching products
can be replaced. Modified staged input,
modified or foreign products, case-insensitive source-name collisions, and
conflicting dependency basenames are rejected. Dependency files are never
renamed after compilation; their references remain intact. Separate packages
may reuse the same song stem. Legacy output directories are not automatically
migrated or removed; use a fresh output directory when changing layout.

Conversion, musical interpretation, timing defaults and compiler selection are
unchanged by this packaging layer.
