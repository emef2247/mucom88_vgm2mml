# Output profiles and independent blueprint MUC

Profiles describe the production MUC dialect and compiler adapter. They do not
select a source chip or rewrite native analysis. The registry is
`py/mucom_profiles.py`; the canonical CLI and batch exporter use the same names.

| Profile (`--target`) | Production MUC | Batch MUB compilation |
| --- | --- | --- |
| `mucom` (default) | Existing MUCOM88 / mucomDotNET route | Existing native compiler adapter |
| `mucomsx` | Existing MUCOMSX route | Existing local DOS/openMSX runtime profile |
| `mucomsx-opn` | Existing MUCOMSX dialect under OPN-board assumptions | Generation only; board/runtime validation pending |
| `md` | Existing mucomMD2vgm dialect | Generation only; external compiler adapter not configured |

For generation-only production profiles, batch requires `--muc-only` before
creating outputs. It never silently compiles them with the ordinary MUCOM88
compiler or reports MUC generation as successful MUB compilation. `--mucom88`
belongs to `mucom`; `--runtime-profile` belongs to compiled `mucomsx` output.
This capability table is about adapters, not a guarantee that every source can
be mapped or that every MUCOMSX extension is supported by an installed runtime.

```sh
python scripts/export_mucom.py INPUT --outdir OUTPUT --target md --muc-only
python scripts/export_mucom.py INPUT --outdir OUTPUT --target mucomsx-opn --muc-only
```

## Blueprint without production conversion

`--blueprint-only` runs existing source interpretation and score projection,
then publishes the native-source reference VGM and blueprint products without
calling a production mapper or compiler. It works with any registered profile
and needs neither a MUCOMSX boot disk nor a native compiler.

```sh
python scripts/export_mucom.py INPUT --outdir OUTPUT --blueprint-only
python scripts/export_mucom.py INPUT --outdir OUTPUT --target mucomsx --blueprint-only
python vgm2mml.py INPUT.vgm --outdir OUTPUT --target md --blueprint-only
```

The blueprint is an **editable musical-score draft in MUCOM88-compatible MUC**.
It is intended for arrangement and instrument work, not exact VGM replay or
audition fidelity. The existing mucomDotNET declaration, neutral FM bank and
fixed initialization provide a compilable scaffold only.
It is not translated to the requested production dialect. Musical score, rests,
repetition and source chip/channel/voice comments remain; source timbres and
volume/envelope trajectories are not executable blueprint controls. MIDI is
written by the existing in-project SMF writer without an external MIDI library.

Batch layout remains package-local, preserving input-parent paths:

```text
OUTPUT/<relative input parent>/
  tracks/<stem>.vgm
  blueprint/<stem>.muc
  blueprint/<stem>.midi
  blueprint/<stem>.dat       # where FM audition parts require it
  _diagnostics/<stem>/...
  _source_inputs/...
  listening_manifest.json
  results.csv
```

Additional score voices use `.partNN.muc` / `.partNN.midi` files. PCM has no
blueprint score; PCM-only sources can publish a reference VGM and explicitly
report `blueprint_status=not_applicable`. Unknown-pitch and sub-grid score units
retain the existing documented decisions; short notes are not stretched simply
to make them audible. If no notatable non-PCM notes remain, an explicitly marked
rest-only MUC/MIDI draft is still emitted, with the decision report retained.
This is not a claim that the missing notes have been reconstructed. Source
analysis failures remain errors, not normal hardware-profile exclusions.

Existing production MUC/MUB files, their ownership hashes and successful
production status are preserved during source-only runs, including user-edited
production files. A successfully confirmed PCM-only score retires the prior owned blueprint group;
a failed/skipped blueprint stage preserves its prior output.
Modified/unowned reference or blueprint files remain protected
by the existing publication rules, even under `--force`. A source-only generation
or publication failure produces a failed status and a nonzero CLI result.

Source-only mode cannot be combined with `--muc-only`, `--pcm-only` or compiler
selection. Existing shared score options are available. Native source time and
synthesis controls are not changed by choosing a production profile.

## Reports and validation

`results.csv` and text reports identify `target_profile`, `blueprint_dialect`,
`reference_status` and `blueprint_status`. The musical-layer JSON also records
the selected profile and compiler capability. `blueprint_only` means source
products were generated and published; it never means a MUB was compiled.
A successful prepare stage is not enough if final publication failed.

Defaults remain FM state 24 ms in batch (1 ms canonical), normalization 8 ms,
timeout 1200 seconds and software-volume curves enabled. This update does not
modify production chip conversion laws, target clocks, LFO, presets or vgm_variants.
Existing OPN/OPN2 native analyses are connected to generic source products.
The native OPN-family Hz decoder uses the already established physical
`2**(21-block)` denominator, correcting score/blueprint pitch; source register
controls and production MUC mapping remain unchanged.
