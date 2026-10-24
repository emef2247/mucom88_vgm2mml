# mucom88_vgm2mml

VGMに記録されたレジスタ操作から音符・音価・音量・音色などの演奏情報を抽出し、構造化されたMML（MUC）とPCM IRを生成するツールです。MUCOM88およびMUCOMSX向けのMUCを出力し、対応する実行環境ではMUBへのコンパイルも行えます。

元のVGMの情報を検査可能な中間表現（State／Segment／PCM IR）として保持し、音源の違いを踏まえて各ターゲットに投影します。音源エミュレーションそのものを目的とするツールではありません。

Python 3.10以降を使用します。MUC生成はPythonで行い、MUB生成にはターゲットに対応する外部コンパイラ／実行環境が必要です。

## 使い方

MUCOM88向け（既定）：

```bash
python scripts/export_mucom.py INDIR --outdir OUTDIR --force
```

MUCOMSX向け：

```bash
python scripts/export_mucom.py INDIR --outdir OUTDIR --target mucomsx --force
```

入力にはVGM／VGZファイル、またはそれらを含むディレクトリを指定できます。一括変換では曲ごとの成果物を `tracks/`、診断情報・コンパイルログを `_diagnostics/` に保存し、`results.csv` に各曲の処理結果をまとめます。

### 主なオプション

| オプション | 説明 |
|---|---|
| `--outdir OUTDIR` | 出力先ディレクトリを指定 |
| `--force` | 対応範囲内で未対応の機能や容量超過のPCMを診断付きで省略し、変換を継続 |
| `--timeout SECONDS` | 変換・コンパイルの各段階の制限時間を指定（既定1200秒） |
| `--target {mucom,mucomsx}` | 出力先を選択。`mucom`（MUCOM88向け）が既定、`mucomsx` はMUCOMSX向け |

`--force` はすべての入力の変換成功を保証するものではありません。省略・近似が発生した場合は、各曲のレポートで理由を確認してください。

その他の設定や検証用オプションは、`python scripts/export_mucom.py --help` または各詳細資料を参照してください。

## 対応する音源

| ターゲット | 主な入力と変換内容 |
|---|---|
| MUCOM88 (`mucom`) | OPNA（YM2608）のFM・SSG・リズム・ADPCM-BをMUCへ変換。対応範囲内でPSG、OPM、MSM6258、SegaPCMなどからの投影にも対応 |
| MUCOMSX (`mucomsx`) | PSGをMSX内蔵PSGへ、OPLLをMSX-MUSICへ、SCCをOPNA FMへ投影。DCSG（SN76489系）にも対応 |

SCCからOPNA FMへの変換など、異なる音源間の投影は近似です。音源固有の機能、同時発音数、入力データやコンパイラの容量により制限があります。

## 生成物と再生上の注意

MUCは編集可能なMMLです。MUBへのコンパイルには、MUCOM88向けには対応するmucomDotNETなど、MUCOMSX向けにはMUCOMSXの実行環境が必要です。外部ツールや個人所有のROM／DOSイメージは同梱していません。

VGMは音符そのものではなく、音源への書き込みと時刻を記録しています。このため、MMLへの変換には演奏の解釈や近似が伴う場合があります。

**MUC生成・MUBコンパイル・実際の演奏は、それぞれ別の検証段階です。** 生成やコンパイルに成功しても、元のVGMとの音声一致やすべての再生環境での動作を保証するものではありません。

## 構成

- `vgm2mml.py`：単体VGMの解析・MML生成
- `scripts/export_mucom.py`：MUC／MUBの一括生成とレポート出力
- `py/`：音源解析、中間表現、MUC出力
- `scripts/`：コンパイル、検証、比較、補助ツール

## ライセンス

Copyright (c) 2026 emef2247.

Licensed under the [MIT License](LICENSE).

本プロジェクトで参照・利用する第三者コンポーネントのライセンスおよび著作権表示については、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。本リポジトリのMITライセンスは、本リポジトリ自身にのみ適用されます。外部ツールや第三者の楽曲には適用されません。

## 謝辞

本プロジェクトの開発、検証、比較、およびテスト生成では、以下のツールやプロジェクトを利用または参考にしています。開発者・メンテナー・貢献者の皆様に感謝します。

| プロジェクト | 本リポジトリでの用途 |
|---|---|
| [OPEN MUCOM88／MUCOM88 Windows](https://github.com/onitama/mucom88) | MML仕様、Windowsの実行環境、音色・PCMバンク仕様の参照 |
| [MUCOMSX — MUCOM88 for MSX](https://toughkiddev.github.io/MUCOMSX/) | MSX向けMUCOMSXのMML仕様・音源構成の参照、MUC→MUBコンパイルおよび動作検証 |
| [mucomDotNET](https://github.com/kuma4649/mucomDotNET) | MUC→MUBコンパイル、MUB→VGMによる比較、PCM保持中制御の検証 |
| [MUCOMVGM](https://github.com/wingghost/MUCOMVGM) | テスト用MUC→VGM生成、およびOPNA制御状態の検証 |
| [vgm-conv](https://github.com/digital-sound-antiques/vgm-conv) | 継承した音源変換方式の参考 |

既定のMUCOM88出力ではOPMへの変換を行いません。vgm-convは継承経路で参照した方式であり、通常のMUCOM88変換の実行時依存ではありません。

第三者コンポーネントの詳細は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)に記録しています。
