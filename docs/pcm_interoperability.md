# MSM6258 / SegaPCM to MUCOM PCM

Native source IR is retained. Derived ADPCM-B samples, packaging intervals and
MUCOM bank bindings are target representations, not reconstructed OPNA source
registers or observed source resets. The original native ADPCM-B input route
still preserves its source encoded bytes; it does not use this transcode path.

## Commands

```bash
python vgm2mml.py input.vgm --outdir outputs/pcm_interop \
  --pcm-policy best-effort --pcm-only --no-song-loop --dump-passes

python scripts/export_mucom.py INPUT_DIRECTORY --outdir outputs/mucom88/pcm \
  --pcm-policy best-effort --pcm-only --no-song-loop --timeout 600
```

`--pcm-only` explicitly excludes other source engines and records exclusions.
It is required for mixed OPM inputs while OPM-to-OPNA projection is unavailable.
Pure supported PCM inputs do not require extraction. Existing OPNA FM may share
an external PCM input when extraction is not selected, but simultaneous native
ADPCM-B and external PCM, or MSM6258 plus SegaPCM, are rejected. Unsupported
active commands remain errors. Use `--muc-only` on the batch exporter to omit
MUB compilation. The timeout default remains180 seconds.

## Supported source scope

- MSM6258: known reset, independently playable, cadence-compatible 4-bit,
  low-nibble-first, 10-bit output. Unknown consumption is not promoted to
  measured consumption: the decoder uses an explicit nominal delivery model.
  Unresolved continuation, held pan/rate changes, unsupported output width,
  underfed/irregular delivery and other unresolved controls are rejected.
- SegaPCM: retained native control/ROM evidence and the existing playback model.
  Native stereo is folded to mono with explicit loss diagnostics. Missing-ROM
  substitutions and reset/model assumptions remain visible.

## Target and limits

The shared adapter renders source intervals to nominal PCM, then encodes
high-nibble-first Yamaha ADPCM-B. Target rate is represented by an explicit
Delta-N near15625Hz, and source pan/absolute onset is retained where supported.
The target driver level249 avoids its mute range250..255; its249/255 attenuation
is reported. Independent target predictors at packaging boundaries, linear
resampling, clipping and codec requantization are explicit losses.

Chunk bounds derive from MUCOM sample byte/address limits, not PDX's eight-second
policy. Exact payloads share bank slots. Maximum32 slots, four-byte alignment,
per-sample length and total bank address capacity are checked. Padding encodes
zero-valued target PCM through the continuing predictor; raw zero bytes are not
assumed silent. Valid audio and padding lengths remain distinct. Large mixed
waveforms can still exceed the bank capacity.

Source gate boundaries are kept separate from target packaging boundaries.
Packaging fragments are not eligible for observed-reset short-note omission.
The existing MUCOM normalization and source structure pipeline accepts a
separate target timing view. Inspect final gate timing versus valid/padded
sample duration; clock rounding may truncate or leave an exhaustion tail.
No waveform/runtime equivalence is claimed.

Declared loops split target samples at the selected loop point; the recorded
SegaPCM waveform traversal is fixed and new target decoder resets are explicit
losses. Stateful loop-phase equivalence is not guaranteed. Start finite user
validation with `--no-song-loop`.

## Inspection

Native source dumps remain alongside `.pcm_mucom_projection.json` and
`.pcm_mucom_mapping.csv`. These describe source IDs, target slots/hashes,
valid/padding sample counts, clipping and nominal duration errors. The final
`.mucom.pcm_projection.json` adds accepted-clock gate-versus-audio timing.
The normal MUCOM unit/control CSVs trace target timing and structural membership.
MUC/bank generation and native MUB playback are separate checks.

## Capacity failure diagnostics

A capacity failure reports an explicit code: `sample_slot_count`,
`sample_length_field`, `sample_alignment`, or `bank_payload_bytes`. The message
contains the actual value, limit, attempted one-based slot, source interval,
existing payload bytes and candidate sample bytes. The fixed1024-byte bank
header is excluded from the payload capacity. These are separate constraints;
a long source song does not identify which one was exceeded.

`<stem>.pcm_mucom_capacity.json` is saved even when no MUC/bank can be completed.
It records source identity, active limits, failed constraint, padding and encoded
length, source gate/event IDs and completed target chunks. An interrupted run
may retain `in_progress`; successful packaging changes it to `accepted`.
The reported attempted total is the size at the first rejected sample, not an
estimate of the entire remaining song. Batch diagnostics live under that run's
`_diagnostics` directory; direct conversion uses its output/diagnostics directory.
Old failures without this report require rerunning conversion to recover the
exact failed value; a generic historical error cannot supply the missing sizes.

## x68kとの共有コード

MDX側の共有PCM波形処理・部分変換診断を同期しているが、この文書の
MUC向け `pcm_mucom.py` とその32枠・256 KiB・整列制約は別である。
今回の同期でMUCの `--force` やOPMのFM投影は追加していない。
[同期範囲と次の経路](x68k_code_sync.md)を参照。
