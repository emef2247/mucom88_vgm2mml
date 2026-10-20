# x68k_vgm2mmlとのコード同期

ソース解析・音楽的構造化・時刻割当は、意味が共通する範囲で同期します。
同名ファイルを維持することと、その処理がMUC経路で接続済みであることは別です。

## 今回同期した範囲

- 共有の `musical_structure.py`、`ordered_timing.py`、
  `opna_adpcm_playback.py` は既に一致。`opm_output_normalization.py` も一致。
- `opna_adpcm_waveform.py` を追加。保持中の音量・パン・速度変更に対し、
  一つのデコーダ区間を保つ派生波形を生成する。元のState・Segmentを保持する。
- MDX用の `pcm_waveform_mdx.py` を追加し、`segapcm_pcm.py` を共通エンジンの
  アダプタに変更。DELTAとSegaPCMのソース証跡は区別する。
- `opna_force.py`、`opna_force_pcm.py`、`psg_force.py`、
  `pcm_force_capacity.py` と、その呼び出し側を同期。
  対象は `opm_conversion.py`、`opna_opm.py`、`opna_ssg_opm.py`、
  `pcm_mdx.py`、`psg_scc_conversion.py`、`psg_scc_opm.py`。
  `opm_mdx_music.py` の共通プランナ参照も揃えた。
- `vgm2mml.py` の継承MDX分岐、`scripts/export_mdx.py`、
  `scripts/export_report.py` に省略一覧・容量診断・空PDX時の表示を反映。
- 関連する合成テストも同期。CLIテスト2件では、既定ターゲットが異なるため
  `--target mdx` を明示している。

## ターゲットの境界

| 項目 | 継承MDX経路 | MUC経路 |
|---|---|---|
| 通常CLI | `--target mdx` を明示 | 既定の `--target mucom` |
| バッチ | `scripts/export_mdx.py` | `scripts/export_mucom.py` |
| 部分変換 | 継承MDXの`--force` | 同期後に[MUC固有のforce](mucom_force.md)を追加 |
| PCMバンク | PDXの96枠・検証済みサンプルサイズ | MUCOMの32枠・256 KiBペイロード・4バイト整列 |
| SSGノイズ・HWエンベロープ | OPMで未対応の区間をforce時に消音 | 対応済みのネイティブSSG投影を維持 |

`opna_muc.py`、`pcm_mucom.py`、`mucom_aux.py`、`mucom_fm.py`、
`mucom_normalization.py`、`scripts/export_mucom.py` と、
通常CLIのMUCラッパーは、当初の共有コード同期では変更していない。
その後のMUC固有拡張は以下に区別する。MUCOM固有のPCMバンク診断も保持する。
詳細は [MDXのforce](opna_force.md) と [MUCのPCM相互変換](pcm_interoperability.md)。

PDXの省略規則をMUCへ直結すると、MUCで表現できる機能を失うおそれがある。
MUC向けのforce、チャンネル割当・省略、バンク容量回避は、ターゲットの条件に
合わせて実装・診断する。コードの存在だけでMUCの対応済み経路とは扱わない。

## 同期後のOPNAターゲット拡張

- OPM、OPM＋SegaPCM、OPM＋MSM6258 → OPNA/MUC:
  [基本FM投影を接続](opm_to_opna.md)。元のOPM State/Segmentsを保持する。
- PSG → OPNAのSSG/MUC: [直接投影を実装](psg_to_ssg.md)。
- MSM6258単独およびPSGとの併用: 既存のADPCM-B変換へ接続済み。
- SCC → OPNA/MUC: 未実装。force時の除外と対応済み変換は区別する。

PSG → OPNAはPSG → OPM投影の移植ではなく、ネイティブPSG情報をSSGへ
写像する。OPMの最大8chからOPNAのFM6枠への固定／動的割当・明示的な余剰発音省略は、
独立したターゲット投影であり、ソース解析のch数を変更しない。
