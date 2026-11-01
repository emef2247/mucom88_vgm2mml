# MSX musical-layer integration

Implemented on 2026-10-29 against MSX checkpoint `b78d675`.
The existing MUCOM88/MUCOMSX target converters, compiler/runtime profiles and
bank packaging remain the target adapters. README remains user-maintained.

## Source and output boundaries

The namespaced `musical_hal` package reconstructs source-native devices, sounding
units, ordered controls, voices, percussion and PCM dependencies. Timing uses
integer VGM samples at 44100 Hz, not a rounded video frame clock. Original
State/Segments and source controls remain inspectable. Package provenance and
explicit host adaptations are recorded in `musical_hal_provenance.json`.

Native imports cover PSG, SCC, OPLL, HuC6280, OPM, OPNA, DCSG, MSM6258,
MSM6295, SegaPCM and C140. Native import/reference support does not imply that
all these sources can be projected into production MUC. Unsupported target
routes remain diagnosed. Native SCC5 is now a separate production mapping in
`--target mucomsx`; see `mucomsx_conversion.md` for its pending MSX runtime support.

The canonical converter prepares independent source products before attempting
production MUC. The batch exporter publishes:

- `tracks/<stem>.muc`, MUB and required banks when the target route succeeds;
- `tracks/<stem>.vgm`: source-native reference reconstructed from musical-layer
  controls, retaining the original chips and PCM dependencies;
- `blueprint/<stem>.muc`, `.midi` and a neutral `.dat` bank where needed.

Overflow score parts are preserved in `.partNN` blueprint files. The blueprint
uses MUCOM88 notation, with fixed audition levels and a neutral sine FM voice;
source timbres, SCC waves and device/channel identities are documentary comments.
It excludes PCM. It is not a MUCOMSX source-routing score. MIDI uses an in-project
SMF writer with no external MIDI library. Successful compilation is not listening
validation.

Source analysis, reference generation, blueprint generation, production MUC and
MUB compilation report separate statuses. Failure in one source-product group
retains the last owned files in the other. Modified or unowned files are protected
even with `--force`. Durable recovery copies are retained if rollback cannot
restore every file. Evidence and reports remain under `_diagnostics`.

## Export controls

`export_mucom.py` forwards the following shared controls for both targets:

| Option | Default | Scope |
| --- | --- | --- |
| `--fm-state-ms` | 24 | Internal target FM states: OPNA, projected OPM, MUCOMSX OPLL and SCC-to-OPNA FM |
| `--normalization-ms` | 8 | Existing target timing-correction bound |
| `--timeout` | 1200 | Each conversion/compilation stage |
| `--score-normalize` | off | Optional source-device score retiming before production conversion and reference output |
| `--score-tempo` | 225 | Score normalization and blueprint reference tempo |
| `--score-guided-loops` | off | Additional score-based repeat proposals, with exact target-command expansion checks |
| `--legacy-loops` | off | Retained loop planner; disables score guidance |
| `--legacy-macros` | off | Baseline MUCOM-native macro search instead of enhanced search |
| `--no-software-volume-curves` | absent | Software volume grouping enabled unless this flag is present |
| `--force` | off | Existing documented target approximation/omission rules only |
| `--dump-passes` | off | Additional inspectable target passes |

The canonical converter retains its native 1 ms FM default; the batch exporter
explicitly sends 24 ms, matching the MSX exporter. PSG/SCC-specific state thresholds
are not silently set from this value. Core source evidence is not edited by the
FM target filter. MGSC-only allocation, JavaScript runtime and key-on comparison
options are not copied into the MUCOM compiler interface. Existing MUCOM runtime,
PCM, channel and target-specific options remain available in `--help`.

Blueprint durations are note-value based at all times. Whole source gates are
projected once onto a 64th-note grid using exact rational VGM-sample arithmetic;
internal pitch changes settling at one grid point cannot invent extra voices.
Original-device quiet anchors use the shared score-timing report; complete
durations are truncated independently of their grid phase. Sub-grid gates and
unknown-pitch intervals are omitted with decisions in the
blueprint report. Native evidence and reference timing remain unchanged unless
`--score-normalize` is requested. MUCOM C128/Timer-B projection reports its effective
tempo; unsupported very slow tempos are diagnosed rather than silently clamped.

When a requested normalized production input retains a source song loop, the
shared timing engine abstains until loop-boundary retiming is validated. The
prepared conversion VGM preserves that loop; `--no-song-loop` removes the request.
Reference output remains a finite native timeline.

## Validation

Authored checks cover source control identity/order, PCM dependencies, unchanged
native evidence, score quantization, FM route forwarding, loop/macro expansion,
independent output publication, ownership and recoverable rollback. Small public
PSG/OPNA/OPLL inputs are used for integration. Private `local_only` regression and
listening remain with the user. No broad emulator sweep is required here.

Validation checkpoint: 75 focused authored/public tests passed; the final shared
anchor adapter then passed 16 blueprint/service tests. Public PSG, OPNA and OPLL
source products were published and their MUCOM88 blueprint MUC compiled. The PSG
blueprint replay produced positive SSG level writes with tone enabled. An authored
DCSG input unsupported by the default production target still published its native
reference and compilable blueprint. Listening and full MUCOMSX runtime regression
are not claimed.
