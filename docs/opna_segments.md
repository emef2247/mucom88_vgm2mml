# Native OPNA source analysis

`py/vgm_reader.py` captures YM2608 (OPNA) writes. `py/opna.py`
reconstructs immutable native FM State events and Segment
intervals. This is a source-analysis stage;
The diagnostic MUCOMVGM roundtrip is described separately in
[opna_mucom_roundtrip.md](opna_mucom_roundtrip.md). MDX/PCM target projection
and acoustic simulation are not implemented here.
OPN and OPN2 are outside the requested scope.

## Reader and API

```bash
python py/vgm_reader.py INPUT.vgm outputs/opna-analysis
python -m unittest discover -s tests/scripts -p 'test_opna*.py' -v
```

The reader CLI emits the existing chip artifacts and the new native FM dumps.
The production `vgm2mml.py` detects OPNA commands and projects native FM
Segments to OPM4MHz through `py/opna_opm.py`, then reuses the existing MDX
converter. This target stage does not change the native analysis. OPNA is
not an MGSDRV compatibility route.

`parse_vgm` keeps its existing eight-path return tuple. Its independent new
keyword arguments are `opna_metadata` and
`dump_opna_segments`. Raw traces are emitted for declared chips or observed
writes. State/Segment reconstruction is opt-in for Python callers. Each call
replaces the metadata fields owned by this analysis, including absent paths,
counts, issues and analysis; it constructs fresh state for each source.

```python
facts = {}
parse_vgm(source, output_dir, opna_metadata=facts, dump_opna_segments=True)
analysis = facts['analysis']
```

Each chip module also provides `build_segments(trace_csv, *, end_vgmticks)`,
`dump_analysis(analysis, *, state_csv, segments_csv)`, `key_counts(analysis)`
and `dump_unmodeled_writes(analysis, *, csv_path)`.

## Inspectable artifacts

| Artifact | Meaning |
|---|---|
| `<stem>_trace.opna_regs.csv` | All ordered native register writes, including unsupported domains |
| `<stem>_trace.opna.csv` | FM State events, including unchanged writes and shared-control snapshots |
| `<stem>.opna.segments.csv` | Integrated FM control-state intervals for all channels and chip instances |
| `<stem>.opna.unmodeled.csv` | Source writes without a modeled FM effect, with domain and provenance |
| `<stem>.opna.auxiliary.csv` | Related DAC stream commands and DELTA-T block commands; operands are losslessly retained as hex |
| `<stem>.opna.analysis.json` | Current analysis status, issues and the files emitted by this invocation |

The auxiliary CSV is emitted only when such commands occur. It preserves the
whole block operands, including length/instance flags and memory-image header;
it does not extract playable PCM samples or execute DAC streams. Combine
`metadata['issues']` with the FM analysis when assessing a complete input;
`analysis.issues` alone describes only the register trace consumed by the module.
On a repeated run, the manifest's `current_files` identifies current pass output.
Old pass CSVs are preserved as evidence and may remain when a chip or dump flag
is removed; their existence alone does not make them current results. A prior
manifest is updated even if the new source has no writes for that chip.

Raw rows retain occurrence event ID, byte address, command, instance, chip,
clock, port, register/data, integer `vgmticks`, and original event/time plus
loop iteration. One `vgmtick` is one VGM sample at 44100 Hz. All chips share
the stream origin and the last wait's end; a last write is not the source end.

YM2608 header clock is at 0x48, defined from VGM 1.51.
Fields overlapping command data are absent. OPNA uses
0x56/57 and A6/A7 for ports 0/1 and instances 0/1. The second instance requires
clock bit 30. Undeclared writes remain in Raw, but Segment dumping rejects
them. Second-instance writes with an extra VGM header are conservatively
diagnosed instead of assuming that the second chip has the primary clock.

## FM state and interpretation

OPNA has six FM channels. Preserve all four operator parameters,
raw key mask, algorithm/feedback, FNUM/BLOCK, SSG-EG, timers, mode and prescaler.
Preserve pan, AMS/PMS, LFO and six-channel output enable.
There is no inferred scalar FM volume.

Operator register banks are `op1, op3, op2, op4`; key-mask bits are
`op1=1, op2=2, op3=4, op4=8`. CSV columns identify each operator explicitly.
Real rising/falling mask edges and same-sample off/on boundaries remain visible.
Parameter changes during held gates do not create attacks. `continuity_id`
increments on any rising operator edge, without claiming a full-channel attack.

Unwritten parameters and frequency latches remain unknown. Cleared initial
key gates and the initial prescaler of 6 are explicitly marked reset assumptions.
A key-off interval describes released gates, not acoustic silence. EOF does not
invent a key-off. Replayed source loops carry register/latch/key state, preserving
occurrence and original provenance without a fabricated attack at the jump.

Frequency reconstruction follows the [ymfm OPN register handling profile](https://github.com/aaronsgiles/ymfm/blob/main/src/ymfm_opn.cpp):
normal upper writes A4-A6 stage a shared latch, including writes across OPNA
ports; a lower A0-A2 write commits that latch and its own low byte. Upper writes
alone do not change committed FNUM/BLOCK. Special AC-AE / A8-AA use a separate
latch. Raw register writes and committed frequency pairs are retained separately;
decoded FNUM/BLOCK mask the meaningful bits. In CH3 special mode, A9/AA/A8
map to op1/op2/op3 and A2 to op4. This records per-operator pitch, without
converting the four operators into independent musical channels.

Prescaler address 2D selects 6, 2F selects 2, and 2E selects 3 only when the
prior divisor is 6. OPNA register 29 bit 7 controls six-channel **output**;
it does not delete key or register evidence from channels 3-5. These are native
control-state fields, without target tempo or frequency quantization.

## Limits and validation

This initial implementation models **FM control-state Segments only**. SSG,
OPNA rhythm and ADPCM-B remain named, ordered source evidence with diagnostics.
PCM sample/Segment construction is a later stage. Reserved/unknown writes and
invalid key-channel requests remain explicit. Timer expiry, CSM-generated key
edges, envelope output, acoustic release duration and LFO phase are not simulated;
CSM observation is reported separately and `csm_edges_modeled=False` is exported.
OPNA port 1 register 10 is retained as IRQ flag control, separately from the
ADPCM-B registers 00-0F.

Independent synthetic tests cover partial keys, unchanged writes, same-sample
retrigger, held controls, operator order, unknown frequency latches, cross-port
commits, CH3 special mode, prescaler selection, dual-instance isolation, source
loops, final waits, gzip and the existing eight CSV outputs. Public converted
OPNA fixtures are checked against their actual Raw VGM commands, not their
original OPLL score. The fixture tests use this repo's migrated public OPNA
directories. No fixtures
are rewritten. Passing these tests is source-state evidence, not MUCOM88 or
audio roundtrip evidence.

Format references: [VGM specification](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/vgmspec171.txt),
[ymfm OPN implementation](https://github.com/aaronsgiles/ymfm/blob/main/src/ymfm_opn.cpp).
The executable ymfm profile is an implementation reference, not a new Yamaha
hardware specification.

## MUCOMVGM source check

[MUCOMVGM-Studio](https://github.com/wingghost/MUCOMVGM-Studio) is a GUI
wrapper. Its compiler is separately distributed as
[MUCOMVGM](https://github.com/wingghost/MUCOMVGM). The requested
`mucomvgm.exe` is not the official MUCOM88 Windows `mucom88.exe` CLI.
MUCOMVGM targets YM2608; its README explicitly does not promise complete
MUCOM88 compatibility. Use actual compiler output as observable source
evidence, without treating its behavior as the Yamaha specification.

Local setup uses the portable [v0.0.5 release](https://github.com/wingghost/MUCOMVGM/releases/tag/Ver.0.0.5)
under ignored `.tools/mucomvgm-0.0.5/mucomvgm/`. Downloaded `mucomvgm.zip`
SHA256: `6e5fc2e5fa845b77487f9755fe855bb7a3544df070fbd7518e988028dc312021`.
The compiler and its MIT license are kept locally, without vendoring binaries.
Studio is unnecessary for automated checks.

```powershell
& .\.tools\mucomvgm-0.0.5\mucomvgm\mucomvgm.exe song.muc song.vgm
python py/vgm_reader.py song.vgm outputs/opna-analysis
```

The compiler reads UTF-8 MUC. `T120` is BPM, while `t120` is a Timer-B value;
do not interchange them when checking durations. A self-authored embedded
algorithm-7 voice with `A T120 @1 o4 v15 q0 c4 r4 d4 r4` produced two keyed
intervals of 22050 samples each, at samples 0 and 44100, with total stream
duration 88200. Reader evidence contains 50 writes, 67 State events and 67
Segments; two attacks and eight operator rising/falling edges are retained.
The initial rhythm-stop write remains unmodeled evidence. Local artifacts:
`outputs/mucomvgm_source_smoke_2026-10-12/` (MUC, VGM, compiler log, passes,
summary). This initial smoke check covers MUC -> VGM -> State/Segment.
The subsequent Segment-to-MUCOMVGM projection and FM roundtrip are documented
in `docs/opna_mucom_roundtrip.md`.
