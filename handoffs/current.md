# Active OPM -> OPNA regression checkpoint (2026-10-16)

User authorized roadmap step1; user owns private regression/listening. Basic
OPM->OPNA implementation is saved in working tree; no stage/commit/push/branch
change. Canonical MUC and export_mucom accept --opm-channel-policy error|omit.
Default rejects >6 active primary source channels; omit is explicit, fixed
whole-song first6 active channels. --force alone does not authorize that loss.

py/opm_opna.py retains native OPM and emits .opm_opna.target.writes/state/
segments/music.csv and .opm_opna.json. Replayable target initialization and
unique target event IDs survive normalization. Original OPM occurrence and
raw command identities are separate mapping fields. Pitch correction occurs
once at7987200Hz; shared high latch always rewritten; operator banks and
logical Key masks use their distinct orders. Force diagnoses DT2/LFO/CSM,
noise operator/pan0 TL approximations and out-of-range pitch clamping. Explicit
channel omission is displayed/saved even without force, distinguished by
force_requested=false. Native OPNA FM has priority under force if both use FM.

OPM enabled song loops use shared2pass reader and carried source state. External
MSM/SegaPCM use same replay count/end/L. Native OPNA SSG/PCM retain their own
source clock even when FM uses projected target clock. No fake OPNA raw source.

Checked: test_opm_opna6 + test_mucom_force3 + test_mucom_cli3 =12 pass. Public
SEGAONE batch --muc-only --force --opm-channel-policy omit --pcm-policy best-effort
succeeded; one FM gate, PCM/voice bank,41 target writes, maximum nominal pitch
error0.139698cent. Inspected mappings and MUC A/K tracks. Artifacts under
outputs/diagnostics/opm_opna_check_20261016. No compiler/private regression/audio.
Architect read-only completion review found no basic-route blocker; both audit
cautions (channel omission visibility and range loss) were addressed.

User regression is next. Do not silently tighten/loosen timing or remove loops
for compiler capacity. Future roadmap: x68k PSG/SSG musical-unit reuse, scoped
noise support, measured FM structure improvements, eventual MSX backport.
Details: docs/opm_to_opna.md and external
field_notes/2026-10-16_opm_opna_initial_projection.md.
