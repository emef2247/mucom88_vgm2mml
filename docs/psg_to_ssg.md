# PSGからOPNA SSGへの直接投影

AY8910/8912/8913、YM2149のVGMレジスタ書き込みを、OPMを経由せず
MUCOM88のSSGパートD/E/Fへ投影する。音楽単位・内部制御・正規化・反復・
曲ループの出力は既存MUC経路を共有する。

```bash
python vgm2mml.py input.vgm --outdir outputs/psg --dump-passes
python scripts/export_mucom.py INPUT_DIRECTORY --outdir outputs/mucom88/psg --force
```

## ソースと投影

`psg_mucom.py`は実際の0xA0書き込みから、元レジスタのスナップショット、
音量・ミキサー・トーン・ノイズ・エンベロープ、時刻、イベントIDとアドレスを
持つPSG Segmentを構築する。OPNAソースやOPMイベントを合成しない。
初期レジスタ値0は解析モデルの仮定として記録する。

ソースクロックと実効AYクロックを保持し、ターゲットSSGクロックとの比で
使用中のトーン・ノイズ・エンベロープ周期を補正する。YM2149のPin26フラグは
追加の1/2分周として扱う。ノイズ専用区間の未使用トーン周期は投影制限にしない。
音量コードは対応するSSG音量へ渡すが、AY/YMのDAC特性、アナログ出力、
エンベロープ位相や音声一致は保証しない。

[VGM仕様のAYクロック・種類・フラグ](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/vgmspec171.txt)
を参照。既定の出力形式・アナログ特性フラグは証跡に保持する。
AY8930拡張など今回の対応外の種類、複数PSG、未対応設定は通常エラーにし、
force時にはPSG部全体の省略を明示する。不正・欠落クロックは停止する。

## MSXと共通の発音構築

`msx_vgm2mml`で使用している`mml_envelopes.extract_notes`を呼び出し、
PSG Segmentから発音と内部音量推移をまとめる。元のSegmentは変更せず、
元の44100 Hz sample時刻を使う明示的な一時viewで解析する。
ゼロ長の音程・形状書き込みも残し、次の正の区間への境界情報を失わない。

音量の下降・維持は同じ発音内のvolume runsとして保持し、音量の上昇、
音程書き込み、トーン/ノイズ/エンベロープ設定の変化などで分ける。
これはMSX由来の音楽的推定であり、PSGがKey-Onを通知したという意味ではない。
HW envelopeが無効でも形状書き込みを推定境界にする既存の抽出規則を含む。

この発音IDをMUCのソフトウェア音量のUnit構築に使う。各発音の先頭でflatな
`E`を初期化し、内部の音量変化はtieで保持する。HW envelopeにはこのIDによる
再発音を適用せず、既存の連続性・形状トリガー処理を保つ。forceで省略した区間の
後は発音開始状態を初期化する。MGSDRVの60 Hzグリッド、`@e`バンク制限、
MGS記法は持ち込まない。有限反復は既存の共通SourceLoopPlan経路に渡し、
raw control/context比較キーも保持する。VGMの全曲ループはこれと別に検査する。

正規化には、まとめたUnitだけでなく、冗長・非活動・EOFの書き込みを含む
元のPSG制御時刻を渡す。通常の制御間隔は同tickへまとまることがあるが、
正の形状再設定間隔は保護し、同時刻の命令順序も残す。
MUCとループ状態検査は同じ採用済み対応表を使用する。
不足した座標を後から無検査の丸めで補う処理は行わない。

## ノイズ、エンベロープ、ループ

対応するノイズとHWエンベロープはSSGで出力する。PSG→OPMの消音規則を
流用しない。同時刻の形状レジスタ書き込みを順番どおり保持する。
既存MUCのGパートを共有制御レーンとして使用し、DEFの後に元の形状を書き戻す。
これはドラム発音の合成ではない。legacy S命令による余分なリセットを補正する
既存方式であり、実機のエンベロープ位相同一性は未検証。

曲ループのあるPSG入力は、共有readerで初回と反復分の2周を解析する。
初回の曲末から音源状態とwait上書きを保持して元のloop addressへ戻り、
リセットやKey-On/Offを挿入しない。初回にしかない立ち上がり・音程・音量を残し、
反復用の`L`は2周目の開始（初回EOF）へ置く。初回のループ先頭と曲末で状態が
異なっても、初回を反復用の音で上書きしない。

反復分を含むSegmentを、共通の発音抽出・正規化・構造化へ渡す。
PSGとOPNA FM、MSM6258、SegaPCMが混在するときも同じ2周の時間軸を使い、
外部PCMの終端・loop位置の一致を検査する。解析量やPCMバンク使用量は増えるため、
既存のframe・容量・未対応動作の制限は残る。PSGを含まないMUC経路は従来の
ループ処理を維持する。`--no-song-loop`は展開せず元VGMの有限1周を出力する。

曲ループの判定も元の0xA0書き込みを用いる。2周目の先頭と展開後EOFの
実効レジスタ状態の接続を検査し、未確認の復帰やHW envelope・PCMの位相／
デコーダ継続を成功扱いにしない。2周解析自体は音響的な定常性の証明ではない。

ネイティブOPNA SSGと外部PSGが同時に必要な場合、通常はDEFの競合で停止する。
forceではネイティブOPNA SSGを優先し、外部PSGの省略を記録する。
未対応の共有エンベロープ競合、周期範囲超過、プリスケーラの区間省略は
[MUCのforce](mucom_force.md)を参照。

## 中間結果

- `.psg_ssg.source.segments.csv`: 元のPSG区間・レジスタ値・イベントID。
  `source_event_id`は出現ごとのID、`original_event_id`／`original_vgmticks`は
  元ファイルのID／時刻、`loop_iteration`は初回0・反復1。状態が書き換えられず
  持ち越される間は、直前の観測イベントが引き続きその区間の根拠になる。
- `.psg_ssg.source.json`: クロック、種類、フラグ、実際の全書き込み。
- `.psg_ssg.music.csv`: 推定発音の元sample区間、音量推移とSegment/event対応。
  `source_event_ids`は正の区間の所属IDであり、原因になったゼロ長書き込みは
  `.psg_ssg.source.segments.csv`の同時刻の順序付き行から確認する。
- `.mucom.units.csv`、`.mucom.control_units.csv`: PSGの`psg:ch:row`への対応。
- `.vgm.timing.csv`／`.vgm.loop.csv`: 全命令の元／出現対応、元loopと初回EOF、
  実際の反復開始・展開後EOF・wait上書きを反映した反復長。
- `.mucom.ssg_loop_checks.csv`: 元書き込みによるループ状態検査。
- `.mucom.normalization.json`、`.mucom.timing.csv`: 別レイヤーの出力時刻。
  正規化採用時の`auxiliary_control_timing`は元のPSG制御ID・時刻・レジスタと
  割り当てたclock・誤差を保持する。

通常のVGM readerのログも保持する。Segment生成・MUC生成・MUBコンパイル・
実機視聴は別々の確認であり、この実装の合成チェックは音声の証明ではない。

`.mucom.json`の`song_loop`は、元の`source_start_samples`／`source_end_samples`と、
`target_start_samples`／`playback_end_samples`、`source_traversals`を分けて記録する。
短音省略も出現IDで判定する。同じ元イベントでも初回と反復時で長さが異なる場合、
片方の省略を他方へ波及させない。
