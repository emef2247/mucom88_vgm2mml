# mucom88_vgm2mml

VGMを検査可能な中間表現へ変換し、その中間表現からMMLとPCM IRを生成するツールです。現在の主な入力はYM2608（OPNA）、MML出力はMUCOM88形式（MUC）です。

このプロジェクトの主目的は、VGMのレジスタ操作から導出される情報を、編集可能なMUCおよびPCM IRとして表現することです。生成したMUCは、対応するmucomDotNETコンパイラでMUBへコンパイルできます。

出力の時間精度は `--normalization-ms`（既定8ms）で調整できます。値を小さくすると細かなタイミング変化を保持し、大きくすると対応範囲内で短い発音の省略と時刻の近似を広く許容します。元のSegment・PCM IRは保持します。

MUCは発音内の制御とフレーズの反復を構造化し、音価表記と行分割で出力します。変換の詳細は[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md)を参照してください。

Python 3.10以降を使用します。主な実行環境はWSLです。MUC生成はPythonのみで行い、MUB生成にはWindows上の外部コンパイラを使用します。

## 通常変換

```bash
python vgm2mml.py input.vgm --outdir OUTPUT
```

ターゲットの既定値は `mucom` で、`OUTPUT/input.muc` と、必要に応じて音色DATおよびADPCM-Bバンク、解析用CSV／JSONを生成します。`--outdir` を省略した場合の出力先は `outputs/` です。fixtureの検証には入力と異なる出力先を指定してください。

VGM／VGZの実際のコマンドから使用音源を判定します。MUCOM88出力では、対応範囲内のOPNAのFM・SSG・リズム・ADPCM-Bを処理します。未対応の音源・組み合わせ・ストリームは診断情報として報告します。

## おまけ機能

既定のMUCOM88出力とは別に、MMLの生成に関しては以下オプションでMDX　MML/PCM IR, 及び MGS MMLを出力可能です。

```bash
python vgm2mml.py input.vgm --target mdx --outdir OUTPUT
python vgm2mml.py input.vgm --target mgs --outdir OUTPUT
```

### 入力と出力

| 入力 | 主な出力 |
|------|------|
| OPNA（YM2608）の通常FM | 状態・Segmentと、FMパートのMUC・音色DAT |
| OPNAのSSG | 状態・Segmentと、SSGパートのMUC |
| OPNAのリズム | 発音・停止・制御情報と、リズムパートのMUC |
| OPNAのADPCM-B（Delta-T） | 元のPCM IR、PCMパートのMUCと必要なADPCM-Bバンク |

これらの併用入力も対応範囲内で処理します。CH3特殊モード／CSMなどには未対応条件が残ります。詳細は[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md)を参照してください。

音色ファイルは `<stem>.dat`、MUC内の参照は `#voice <stem>.dat` です。

### MUC・MUBの一括出力

単一のVGM／VGZ、またはディレクトリ配下のファイルを再帰的に検索して、MUCとMUBを生成します。

```bash
python scripts/export_mucom.py INPUT_DIRECTORY --outdir outputs/mucom88
```

`tracks/` 以下に曲ごとのMUC・MUB・必要なバンク、`_diagnostics/` 以下に中間CSV／JSON・コンパイラログ・レポートを保存します。各曲の結果は `results.csv` で確認できます。MUCだけを一括生成する場合は `--muc-only` を指定してください。

MUB生成にはMUCOM88 Windows 260407に同梱された `mucomDotNETConsole.exe` を使用します。PCM保持中制御の拡張命令が必要なMUCには、別配置した対応版mucomDotNETを自動選択します。外部工具はリポジトリに同梱しません。配置・導入方法は[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md#pcm保持中制御と相互運用の境界)を参照してください。別のコンパイラ配置は `--mucom88 <実行ファイル>` で指定できます。

生成したMUCや関連ファイルを手作業で編集した場合、一括出力ツールは上書きを拒否します。元の入力素材とは別の出力先を使用してください。

## VGMの曲全体ループ

VGMヘッダーに指定されたループは、対応範囲内で既定のMUCへ引き継ぎます。

MUCでは `L` で曲ループを表現し、参加する各パートの時刻を揃えます。保持音がループ境界をまたぐ場合などには接続条件があります。詳細は[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md#時刻とループ)を参照してください。

有限1周で終了させたい場合は `--no-song-loop` を指定してください。これは、MUC内の有限反復圧縮を無効にする `--no-loops` とは別の設定です。

```bash
python scripts/export_mucom.py input.vgm --outdir outputs/mucom88/input

python scripts/export_mucom.py input.vgm \
  --outdir outputs/mucom88/finite \
  --no-song-loop --timeout 600
```

`--no-loops` は通常変換の `vgm2mml.py` で使用します。

## 生成されるMMLの確からしさ

VGMには音符そのものではなく、音源への書き込みとその時刻が記録されています。したがって、VGMからMUCを生成する際には、発音・停止・音程・音量・音色の変化を解釈し、MUCで表現可能な形式へ変換しています。

MUCの生成やMUBへのコンパイルが成功したことは、元VGMとの音声一致を保証するものではありません。近似や省略が発生する場合があり、その内容は診断情報やレポートに記録されます。

FMの発音や内部制御を比較する手順は[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md#ネイティブfm往復検証)を参照してください。

## PCMを含むVGMからMML・PCM IRを生成する

対応範囲内のOPNA ADPCM-B入力から、MUC・元のPCM IR・必要なADPCM-Bバンクを生成します。元の符号化データをバンクに保持し、PCM IRとMUBコンパイル用のバンクを区別して保存します。

再生中の音量・パン・Delta-N変更に対応しています。再生中のPCMを維持したまま制御を更新し、Delta-Nによる停止・再開も扱います。このMUCはmucomDotNET拡張を利用し、本家MUCOM88／MUCOMMSXとの互換性は保証しません。

対応版工具は次のコマンドで `.tools/` 以下へ導入できます。Windowsの.NET 6ランタイムが必要です。

```bash
python scripts/setup_mucom_pcm_tools.py

python scripts/export_mucom.py tests/fixtures/public/opna/pcm_controls \
  --outdir outputs/mucom88/pcm_controls \
  --no-song-loop --timeout 600
```

推定再生位置、出力時刻、再生速度の差は中間結果に保存します。repeat、曲ループをまたぐPCM保持音、再生中のアドレス／メモリモード変更などは未対応です。詳しくは[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md#pcm保持中制御と相互運用の境界)を参照してください。

SegaPCMなど他形式PCMとの相互変換は未実装です。入力元のPCM情報と、出力先のcodec・バンク・再生制約を分けて扱う方針は、同じ資料に記録しています。

## 主なオプション

通常のMUCOM88変換の主なオプションは次のとおりです。

| オプション | 内容 |
|------------|------|
| `--target mucom` | MUCOM88形式のMUCを生成（既定） |
| `--outdir DIRECTORY` | 出力先（既定 `outputs/`） |
| `--normalize-lengths` / `--no-normalize-lengths` | 出力用正規化の有効／無効（既定は有効） |
| `--normalization-ms MS` | 短い発音の省略閾値と出力時刻の最大移動量（既定8ms、適用条件あり） |
| `--timing-bound-ms MS` | 正規化を無効にした基準経路の時刻移動上限（既定8ms） |
| `--no-loops` | 有限反復の生成を無効化 |
| `--no-song-loop` | VGMヘッダーの曲ループを出力せず、最初の1周で終了 |
| `--dump-passes` | 中間結果保存の明示指定。MUCOM88経路では指定の有無によらず保存 |
| `--diagnostics-outdir DIRECTORY` | 中間結果・診断情報の保存先を指定 |

ここでいう **有限反復** は、VGMヘッダーに記録された **曲ループ** とは別です。有限反復は、同一パターンを構造化してMUCを短く表現する機能です。

一括出力ツールの主な追加オプションは次のとおりです。

| オプション | 内容 |
|------------|------|
| `--outdir DIRECTORY` | 一括出力先（必須） |
| `--muc-only` | MUBコンパイルを行わずMUC・必要なバンクを出力 |
| `--mucom88 PATH` | MUB生成に使用するコンパイラを指定 |
| `--timeout SECONDS` | 変換・コンパイルの各段階の上限（既定180秒） |

一括出力でも `--no-song-loop`、正規化の有効／無効と `--normalization-ms` を指定できます。その他のオプションは各ツールのヘルプを参照してください。

```bash
python vgm2mml.py --help
python scripts/export_mucom.py --help
```

## トラブルシューティング

エラーの詳細は、出力先の `results.csv` と各runの `report.txt`、変換・コンパイルログで確認できます。

- `conversion_failed`：解析・MUC投影・時間割当の未対応条件や、変換のtimeoutです。音源間の周波数変換失敗だけを意味しません。
- `compilation_failed`：MUC生成後にコンパイラが失敗しています。演奏データの容量制限も含まれます。MUCの文字数とMUBの演奏データ容量は別です。
- CH3特殊モード／CSM：現在のMUC投影では未対応です。
- 外部工具が見つからない場合：工具の配置または `--mucom88` 指定を確認してください。PCM保持中制御には対応版工具が必要です。

曲ループ接続のエラーには、`--no-song-loop` を追加して有限1周の出力を試してください。長い曲のtimeoutには、各段階の上限を `--timeout 600` などで延長できます。

```bash
python scripts/export_mucom.py INPUT_DIRECTORY \
  --outdir outputs/mucom88/finite \
  --no-song-loop --timeout 600
```

この指定でもMUC内の有限反復による構造化は維持します。詳細は[docs/mucom_musical_projection.md](docs/mucom_musical_projection.md)を参照してください。

## 構成

- `vgm2mml.py`：通常変換の入口
- `py/vgm_reader.py`、`py/opna.py`：VGMの読込とOPNAの状態・Segment解析
- `py/opna_music.py`、`py/musical_structure.py`：発音単位の再構成と反復構造の処理
- `py/opna_adpcm.py`、`py/opna_adpcm_playback.py`：PCMの元情報と再生位置の解釈
- `py/opna_muc.py`、`py/mucom_fm.py`、`py/mucom_aux.py`：MUCOM88向け投影とMUC出力
- `scripts/`：一括出力、コンパイル、診断、往復検証、fixture生成
- `tests/fixtures/public/`：公開可能な自作テスト素材
- [docs/project_knowledge.md](docs/project_knowledge.md)：変換の原則と制約


MUCOM88関連の自動チェックは次で実行できます。外部ツールを使用するチェックには、そのツールの準備が必要です。

```bash
python -m unittest discover -s tests/scripts -p 'test_mucom*.py' -v
python -m unittest discover -s tests/scripts -p 'test_export_mucom.py' -v
```

## ライセンス

Copyright (c) 2026 emef2247.

Licensed under the [MIT License](LICENSE).

本プロジェクトで参照・利用する第三者コンポーネントのライセンスおよび著作権表示については、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。本リポジトリのMITライセンスは、本リポジトリ自身にのみ適用されます。外部ツールや第三者の楽曲には適用されません。

## 謝辞

本プロジェクトの開発、検証、比較、およびテスト生成では、以下のツールやプロジェクトを利用または参考にしています。開発者・メンテナー・貢献者の皆様に感謝します。

| プロジェクト | 本リポジトリでの用途 |
|---|---|
| [OPEN MUCOM88／MUCOM88 Windows](https://github.com/onitama/mucom88) | MML仕様、Windowsの実行環境、音色・PCMバンク仕様の参照 |
| [mucomDotNET](https://github.com/kuma4649/mucomDotNET) | MUC→MUBコンパイル、MUB→VGMによる比較、PCM保持中制御の検証 |
| [MUCOMVGM](https://github.com/wingghost/MUCOMVGM) | `mucomvgm.exe`を使ったテスト用MUC→VGM生成、およびOPNA制御状態の検証 |
| [x68k_vgm2mml](docs/integration_baseline.json) | VGM解析、Segment処理、MML構造化などの開発基盤 |
| [vgm-conv](https://github.com/digital-sound-antiques/vgm-conv) | [x68k_vgm2mml](docs/integration_baseline.json)から継承したコードに含まれるOPNA FM→OPM、PSG／SSG→OPMの対応方式の参照 |

既定のMUCOM88出力ではOPMへの変換を行いません。vgm-convは継承経路で参照した方式であり、通常のMUCOM88変換の実行時依存ではありません。

使用した第三者コンポーネントのバージョン、参照元、著作権表示、ライセンス条件、および再配布時の注意事項は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)に記録しています。

解析およびMUC・PCM IR生成はPythonで実装しています。外部ツールは主にコンパイル、再生確認、比較、およびテスト生成に使用します。