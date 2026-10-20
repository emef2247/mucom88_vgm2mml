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
Mixed OPM inputs can instead retain FM through the [basic OPM-to-OPNA
projector](opm_to_opna.md); extraction is optional. Pure supported PCM inputs
do not require extraction. Existing OPNA FM may share
an external PCM input when extraction is not selected, but simultaneous native
ADPCM-B and external PCM, or MSM6258 plus SegaPCM, are rejected. Unsupported
active commands remain errors. Use `--muc-only` on the batch exporter to omit
MUB compilation. The batch conversion/compilation timeout defaults to600 seconds per stage.

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
同期後に[MUC固有のforce](mucom_force.md)と[OPM基本FM投影](opm_to_opna.md)を追加した。
[同期範囲と次の経路](x68k_code_sync.md)を参照。

## MUC forceとPSG併用

`--force`を直接CLI／バッチに追加できる。PSGとの併用はSSGとKを同じ時刻に
投影し、MSM6258単独の入力には`--pcm-only`は不要。OPM混在入力でも基本FM投影を
利用できる。その他の未対応エンジンは、明示抽出またはforceによる除外と一覧表示を選べる。
容量省略は収まらない新規サンプルだけを対象とし、採用済み音色の再利用を残す。
元のPCM IR、終端・ループ時刻は保持する。詳細は[MUC force](mucom_force.md)。

## PCMサイズを抑える設定

MSM6258／SegaPCMからMUC用のADPCM-Bへ変換するときは、
`--pcm-profile low`で標準の約半分、`--pcm-profile ultra_low`で約1/4の
サンプルレートを選択できる。
直接CLIとバッチの両方で使用でき、既定値`standard`の音声生成は従来どおり。

```sh
python scripts/export_mucom.py INPUT --outdir outputs/pcm_low \
  --pcm-policy best-effort --pcm-profile low
```

`standard`は目標15625 Hz、`low`は目標7812.5 Hz、`ultra_low`は目標3906.25 Hz。
実際にはターゲットOPNAの
クロック7987200 Hzで最も近い整数Delta-Nを選び、その実レートで再サンプリング・
符号化し、MUCの音階／detuneにも同じDelta-Nを使用する。元の音程・再生速度・
発音時刻を保つための設定であり、音声を遅く再生する設定ではない。

長い波形の符号化ペイロードはlowでおおむね半分、ultra_lowで約1/4になる。4バイト整列・短い音・重複排除・
容量省略・サンプル分割の違いにより、バンク全体が必ずその比率になるわけではない。
低レートでは同じサンプルbyte上限に長い時間を収めるため、包装境界も変わり得る。
32枠・256 KiBペイロード上限・既存のforce省略方針は変更しない。
バンクが小さくなってもコンパイラや再生環境のPCMバッファへ収まる保証はない。

Source State／Segment／PCM IRは変更しない。既存の線形補間を用い、専用の
アンチエイリアスフィルタは追加していないため、高域の損失や折り返しがあり得る。
PCM保持中制御を含め、ネイティブOPNA ADPCM-Bは符号化byteとレートを保持する
別経路であり、この設定の対象外。`.mucom.json`の`pcm_profile.applicable`で区別する。

`.pcm_mucom_projection.json`、`.pcm_mucom_capacity.json`とmapping CSVに、
選択profile・目標／実レート・Delta-N・encoded byte数を保存する。source時刻・
valid/padding samples・duration誤差も併せて確認できる。バンク容量は1024 byteの
ヘッダーと音声ペイロードを区別する。設定による音質の選択は実際の再生で確認する。
