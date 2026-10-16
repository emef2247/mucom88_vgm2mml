# MUCの部分変換（--force）

`vgm2mml.py`の既定MUC経路と`scripts/export_mucom.py`で指定できる。
扱える部分の試聴候補を生成するための明示的な損失許容であり、既定は無効。
元のState・Segment・PCM IRは保持し、省略はターゲット側で行う。

```bash
python scripts/export_mucom.py INPUT_DIRECTORY --outdir outputs/mucom88/forced \
  --pcm-policy best-effort --force
```

## 今回の適用範囲

- 対応外のSCC/OPLLのMUC投影を省略し、対応するFM・PSG・PCM等を続行する。
- [OPMの基本FM投影](opm_to_opna.md)では、DT2・ハードウェアLFO・CSMの効果を省略し、
  ノイズoperatorの抑制、pan 0のTL消音近似、範囲外音程の端点近似を記録する。
  ネイティブOPNA FMも発音する入力はOPNAを優先し、OPM FMを省略する。
  余剰ch省略はforceだけでは許可せず、`--opm-channel-policy omit`を必要とする。
- 外部PSGの未対応種類・構成はPSG部全体を省略する。ネイティブOPNA SSGと
  DEFが競合する場合も、外部PSGを省略してネイティブSSGを優先する。
- SSGの未対応共有エンベロープ競合は競合区間を消音する。
  使用中の周期が範囲外の場合や未対応プリスケーラ区間も消音し、復帰後は
  対応する区間を出力する。使用可能なノイズ・エンベロープは保持する。
- MSM6258/SegaPCM由来のADPCM-Bバンクは、32枠・サンプル長・256 KiB
  ペイロードを超える新しいサンプルを丸ごと省略する。収まる後続サンプルと
  採用済みバイト列の再利用は継続する。省略区間は休符となり終端・ループ時刻を保持。
- 分類済みのMSM6258未対応HWモードや保持中パンなどは、必要に応じてPCM部
  または発音全体を省略する。ただし同時に供給周期不整合、リセット・サンプル欠落、
  不正クロックなどがあれば停止する。すべての速度変更を省略できるわけではない。

全発音を省略した場合は、その事実を記録した休符のMUCを生成する。
空のPCMバンクを要求せず、不要な`#pcm`参照を出さない。

入力破損、未知のストリーム、未解決のソース証拠、アラインメント不整合、
リソース上限、タイミング・ループ検証やコンパイラ制限は回避しない。
ネイティブOPNA FMのCH3/CSMや、ネイティブADPCM-Bの既存制限をすべて
省略可能にした機能ではない。これらのガードは引き続きエラーになり得る。
PCM再符号化の既知損失には、別途`--pcm-policy best-effort`が必要。

## 表示と証跡

処理後にパート・音源・ポート・レジスタ・処理・件数・理由を一覧表示する。
容量省略にはactual/limitと単位を表示する。件数は省略判断の数であり、
元の書き込み数や失われた曲の秒数とは限らない。

- `<stem>.mucom.force.csv/.json`: 全省略判断、元時刻・イベント/発音ID、容量値。
- `<stem>.mucom.json`の`force_projection`: 同じ判断の参照。
- `<stem>.pcm_mucom_capacity.json`: PCM容量判定、採用数と省略数。
- `<stem>.pcm_mucom_omissions.csv`: 外部PCM由来の省略詳細。

バッチでは各runの`_diagnostics/.../report.txt`にも一覧を付ける。
コンパイル失敗でも既知の省略一覧を残すが、成功には変更しない。
forceなしの再実行では古いforce一覧を現在の結果として表示しない。
ただし`--opm-channel-policy omit`による余剰chの明示省略は、forceなしでも
同じCSV／JSONとコンソールに保存・表示する。`force_requested=false`とし、
表示名を`MUCOM explicit channel omission`としてforce指定と区別する。

PDXの96枠・検証済み65,535バイト規則とは異なるターゲット方針。
継承MDXのforceは[別文書](opna_force.md)を参照。
