# OPMクロックとMDXの音程補正

MDXの再生先はX68000の4,000,000Hz YM2151です。入力VGMが異なる
YM2151クロックを宣言していても、単一インスタンスならTargetで補正します。
Raw、State、Segmentのクロック、時刻、KC/KFは変更しません。

## 補正の位置と計算

`opm_mdx.project_segments`が共通の入り口です。structured／legacyの音符、
registers（channel／conductor）のレジスタ、PCM併用のFM部も同じ投影を使います。
PSG／SCCのOPM投影は既に4MHzを生成するため追加の音程変更はありません。
`--no-normalize-lengths`でも音程補正は有効です。時間の正規化とは別の処理です。

```text
pitch offset in 1/64-semitone units = round(768 * log2(source clock / 4,000,000))
target pitch = source KC/KF pitch + offset
```

KCの穴・別名とKFの上位6bitを使って音程位置を計算します。入力クロックが
低い場合は出力KC/KFを下げ、再生先クロックによる音程上昇を相殺します。
3,579,580Hzでは補正は-123/64半音です。KCのみ／KFのみの変更にも対応し、
繰り上がりが必要なら同じsource eventから両レジスタを出力します。
各命令は`target_sequence`で順序を保持します。4MHz入力の命令値は変更しません。

範囲内の計算は公称の対数音程に基づき、丸め誤差は最大0.78125 centsです。
範囲外のclampは別の損失で、誤差がこの値を超える場合があります。
実チップの周波数ROMや演奏音を測定して同一性を確認した値ではありません。
参照したKC/KF索引とクロック比例の実装は
[mdxtoolsのYM2151実装](https://github.com/vampirefrog/mdxtools/blob/master/ym2151.c)
です。ROM表やエミュレータコードは取り込まず、対数式を独立に実装しています。

## 未知の初期値と範囲外

- 異なるクロックの入力に限り、未書き込みKC/KFを出力側のreset-zero仮定として
  扱います。元のStateの`None`は保持します。最初の発音に必要なKC/KFは両方とも
  Key-On前に設定し、MXDRVの既存値には依存しません。
- 通常KCの96音域と最上端KC `0x7f`の別名を扱います。
  KCのbit7、KFの下位2bitは音程に使用せず、元のbyteはCSVに残します。
- 既定の`--opm-pitch-policy clamp`は範囲外を最寄りのKC/KFへ寄せます。
  音程差、件数、source eventを損失として記録します。3,579,580HzのKC/KF=0では
  下限へのclampに約+192.252 centsの差があります。
- `--opm-pitch-policy error`は厳密な範囲検査を維持します。初回Key-On前の
  範囲外セットアップは保留できますが、発音までに解決しない場合、
  または初回発音後（リリースを含む）の更新はエラーです。
- この範囲はKC/KFレジスタの指定範囲です。Segmentに独自の許容枠を設定したもの
  ではなく、音色側の倍率なども変更するHW全体の実現可能性を判定していません。

## 検査できるデータ

`--dump-passes`で次を保存します。

- `.opm.segments.csv`：変更しない入力Segment。
- `.mdx.pitch.csv`：source event／Segment、入力クロックと元KC/KF、
  未書き込み値の仮定、出力KC/KF、公称音程誤差、clamp／保留した初期化。
- `.mdx.controls.csv`：`register,data`は出力命令、`source_register,source_data`は
  元の命令。追加のKC/KFも元のsource eventへたどれます。
- `.mdx.timing.json`と`.mdx.normalization.json`の`pitch_projection`：補正の概要。

TXTでは`OPM clock and pitch projection`を確認してください。
`Pitch range policy`のclamped eventsは音符数ではなく、音程更新／Key-On評価の件数です。
通常の丸め誤差とclampによる誤差を別々に表示します。
仮定の件数は影響を受けたイベント数であり、チャンネル数ではありません。
roundtrip比較は補正後の期待値との一致を確認します。
KC/KFの対は同一source eventの最後まで反映してから比較します。

## 補正の限界

DT1のHz単位のdetune、EGとkey scaling、LFO、ノイズ、チップタイマーは
再調整しません。音程レジスタが一致しても音色・変調・リリースまで
音響的に等価とは限りません。PCMのrate／符号化バイトは変更しません。
dual-chip／別チップ種は引き続き対象外です。
