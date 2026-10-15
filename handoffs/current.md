# x68k code synchronization checkpoint (2026-10-16)

User requested shared-code synchronization before reviewing new OPNA target
routes. Common native dependencies and musical_structure/ordered_timing/
opna_adpcm_playback already matched. Synced the new DELTA waveform, common
MDX PCM packaging, force omission/capacity helpers and inherited MDX callers,
exporter and TXT diagnostics. See docs/x68k_code_sync.md for the module list.

Default MUC main(argv) wrapper and native opna_muc/pcm_mucom/mucom_aux/mucom_fm/
mucom_normalization/export_mucom are unchanged. --force is available only
on inherited --target mdx and export_mdx, not default MUC/export_mucom.
Preserve MUC native SSG noise/EG and32-slot/256KiB bank policy. No new OPM,
PSG or SCC -> MUC route was added. Future PSG maps directly to OPNA SSG;
OPM8->FM6 excess-channel omission requires the previously agreed explicit option.

Working-tree changes are complete. Nine minimal authored checks passed:
OPNA/PSG force CLI and retry cleanup, PDX slot omission/all-omitted output,
continuous DELTA pause and live controls, default MUC CLI forwarding, existing
MUCOM PCM packaging and native held PCM controls. Tests assert source/pass
provenance and generated MML; no private regression/listening or compiler run.
26 synchronized source/test files were compared by content; two CLI tests
explicitly select MDX. The MUC wrapper and native target files are retained.
Audit: outputs/diagnostics/x68k_force_sync_20261016/{sync_manifest,validation}.json.

Next: user owns regression and will select the next OPNA projection work.
No stage/commit/push/branch operations. Do not start OPM/PSG/SCC target
implementation, private conversions or compiler optimization implicitly.
External note: field_notes/2026-10-16_x68k_shared_code_sync.md.

# Capacity diagnostic checkpoint (2026-10-15)

Improved pcm_mucom capacity errors only; projection limits and audio behavior
unchanged. Slot count, sample length, alignment and aggregate payload now have
separate codes and actual/limit numbers. Per-run pcm_mucom_capacity.json persists
failed sample/source identifiers and exact candidate/existing bank byte counts.
Existing GFII08 report has only the old generic error, so exact historical failed
sizes are unavailable. No private reconversion performed. Two authored boundary
checks passed; persistent JSON inspected under outputs/diagnostics/pcm_capacity_authored.
OPM-to-OPNA implementation and private OPNA failure investigation remain paused.
Next: user rerun a failed song to obtain numeric diagnostics. No commit/push.

# PCM interoperability checkpoint (2026-10-15)

Canonical MUCOM conversion and batch export now accept supported MSM6258 and
SegaPCM through explicit best-effort ADPCM-B transcoding. Mixed OPM extraction
requires --pcm-only; other source engines are recorded as excluded. OPM-to-OPNA
was considered separately and remains unimplemented. Source native IR is kept
separate from derived target samples/banks; no native OPNA state is fabricated.
See docs/pcm_interoperability.md for commands, assumptions and capacity guards.

Working-tree only; no staging/commit/push. User owns private regression and
listening. Only tiny authored/public checks and canonical MUC generation were
performed. Existing OPNA route must retain its source-byte bank behavior.
Next action: user regression; do not start OPM projection, private validation,
compiler optimizations or timeout-default changes without further direction.

# Current restart state

## Documentation organization completed (2026-10-15)

User requested slimming docs/project_knowledge.md without design changes.
Core principles remain; concrete specs merge into existing focused docs.
New docs/output_normalization.md separates shared semantic contract from the
current MUCOM Timer-B/C128 adapter. Dated results remain in existing external
field notes; the chapter destination audit is in
field_notes/2026-10-15_project_knowledge_slimming.md (external directory per AGENTS).
No code/tests/README/AGENTS changes, private conversion/replay, staging, commit
or push. Protected-file hashes and documentation links are checked in ignored
outputs/diagnostics/project_knowledge_slimming/validation.json.

## Implementation boundaries that remain

- Default YM2608 musical MUC/DAT/PCM bank output and batch MUB compilation;
  inherited explicit MDX/MGS routes remain separate. See focused MUCOM docs.
- PCM live level/pan/Delta-N uses one decoder epoch and newer multiport-y tool
  profile; preserved source IR, bank bytes and separate position/timing audits.
  Address/memory/data/repeat/held-loop guards and waveform/runtime unknowns stay.
- CH3 special projection is not implemented; per-operator source frequencies
  are retained. CSM Timer-A automatic edges are not reconstructed.
- Unknown FM pan is currently misclassified by the projection guard; diagnosis
  recorded in external2026-10-15_fm_pan_unknown_diagnostic.md. No fix authorized
  or implemented by this documentation task.
- User owns private failed-song regression and listening. No64KB/timeout,
  hardware phase or MUCOMMSX compatibility resolution is claimed.

## Next allowed action

Report the documentation changes and stop for the user's next direction.
Do not resume CH3/pan/PCM implementation or private song validation implicitly.
If private validation becomes necessary, stop and notify target/purpose first.
Preserve user edits and unrelated staged/working changes; no commit/push.
