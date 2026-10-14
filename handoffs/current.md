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
