# OPM → OPNAの基本FM投影

OPMの基本4operator FMを既存の音楽的MUC生成へ接続する。OPM＋MSM6258／
SegaPCMでは、既存のADPCM-B投影と同じ終端・曲ループ時刻を使う。
ネイティブOPM State/Segments・PCM IRは保持し、OPNA表現をターゲット側に生成する。
vgm-convを実行せず、独立したレジスタ・音程投影を行う。

## 実行例

```sh
python vgm2mml.py input.vgm --outdir outputs/opm --force --opm-channel-policy omit
python scripts/export_mucom.py INPUT --outdir outputs/opm --force --opm-channel-policy omit --pcm-policy best-effort
```

基本FMが対応範囲内ならforceは不要。外部PCMには`--pcm-policy best-effort`が必要。
`--pcm-only`はFMを除外する明示抽出なので、FMも変換する場合は指定しない。
コンパイルせず生成物を確認する場合はバッチに`--muc-only`を付ける。

## 対応と損失

- primary YM2151の使用chを全曲で固定割当する。Key-On要求のあるchを使用chとする。
  既定は7ch以上で停止。`--opm-channel-policy omit`は使用chの先頭6chを元ch順に採用し、
  残りを省略する。時間区間ごとのch使い回しはしない。forceだけでは余剰chを省略しない。
- algorithm・feedback、operatorレジスタ、部分Key操作、保持中の音程・TL等を投影する。
  レジスタbank順と論理Key mask順を区別する。DT1・EGの物理速度や音響一致は未検証。
- KC/KFと元OPMクロックから公称周波数を求め、7,987,200 HzのOPNAのFNUM/BLOCKへ
  一度だけ投影する。各pitch書き込みはhigh→lowの順で共有latchを更新する。
  未観測KC/KFのreset 0仮定はターゲット側の仮定として別記し、ソースを上書きしない。
  表現範囲超過は通常停止し、force時は端点への近似とHz・cent誤差を記録する。
- DT2、実効的なハードウェアLFO、CSM、ノイズは通常停止。force時はDT2・LFO・CSMの
  効果を省略し、ノイズoperator（元ch7/C2）はTL 127で抑制する。他operatorは保持する。
  pan 0は通常停止し、force時は両pan＋全operator TL 127で消音を近似する。
  これらは位相・release・聴感の一致を保証する方式ではない。
- 元のtimer IRQを再現してKeyを生成しない。VGMのwaitを時刻の証拠として用い、
  timer書き込みの識別子を記録する。単なるtimer設定とCSMは区別する。
- OPNA FMも発音する混在入力は通常停止し、force時はネイティブOPNA FMを優先して
  OPM FMを省略する。未対応YM2151 instance／variantもforce時は明示省略となる。
- 未観測panでのKey-On、不完全な音色、入力破損、既存のPCM・timing・ループ検証、
  バンク／コンパイラ制限を一律に回避する機能ではない。

## 中間結果

ネイティブOPMの`<stem>_trace.opm_regs.csv`／`<stem>_trace.opm.csv`と
`<stem>.opm.segments.csv`を残す。
投影後はネイティブOPNAソースと混同しない名前を使う。

| ファイル | 内容 |
|---|---|
| `<stem>.opm_opna.target.writes.csv` | target write ID・順序、OPM occurrence/original event ID・元時刻・元レジスタ、投影先、理由、音程誤差 |
| `<stem>.opm_opna.target.state.csv` / `.segments.csv` | 明示的に投影したOPNA State/Segments |
| `<stem>.opm_opna.target.music.csv` | 投影されたFM発音全体と内部制御 |
| `<stem>.opm_opna.json` | クロック・ch割当・reset仮定・timer・省略内容 |
| `<stem>.mucom.force.csv` / `.json` | forceまたは明示ch省略の一覧（元の識別子・時刻・理由） |

以降は既存のMUC出力正規化・構造化とその中間結果へ接続する。ターゲット初期化も
イベントとして保存し、正規化の再解析で失われないようにする。各target writeは
独立したIDを持ち、元OPMイベントとの対応はwrites CSVで追跡する。

## 確認範囲

2026-10-16時点: 小さな自作テストでpitch・operator/key順序・保持中変更・共有pitch latch・
省略診断・MSM6258併用ループを確認。公開自作SEGAONEではCLIからFMを含むMUC、
音色バンク、PCMバンクを生成し、中間write/state/musicを確認した。
実曲リグレッション・コンパイル・視聴は別途利用者が行う。生成成功を再生の証明としない。

[FITOMのvoice_convert](https://github.com/madscient/FITOM_staging/tree/main/tools/voice_convert)と
[voice parameter reference](https://github.com/madscient/FITOM_staging/blob/main/docs/voice-parameter-reference.md)
は静的音色の形式・operator情報の確認に参照した。変換器コードは取り込んでいない。
