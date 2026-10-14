# PCM往復検証と独立基準

2026-10-09の検討結果。これは検証の設計であり、PCM往復検証の実装・成功報告ではない。

2026-10-10追記：通常のPCM付きMDX生成は、試聴で確認できた16トラック＋E8のPCM拡張経路へ変更した。
下記の標準9トラック／PCM1基準は独立検証候補として残すが、現在の出力を検証するには
PCM拡張の版と実行経路も固定した別profileが必要になる。6件の演奏・停止成功は個別の試聴結果であり、
A/B/C往復成功やreset・消費ニブル数・途中panの保証へは拡大しない。
現在の生成条件とCLIは [pcm_pdx.md](pcm_pdx.md)、調査根拠は
[field note](../field_notes/2026-10-09_pcm_mxdrv_roundtrip_review.md) を参照する。

## 目的と問題の分類

目標は `VGM -> PCM IR -> MDX+PDX -> VGM` の意味論的な往復である。
OPMは既存の音楽的な生成経路を利用する。PCMは符号化sampleと再生情報をIRに保持し、
MDXのPCM命令とPDXへ投影する。PCMのMMLテキストは任意の可読出力であり、必須の中間形式ではない。
PCM直接MDX出力とstrict／best-effortの投影評価は実装済み。独立再生基準とA/B/C比較は未実装である。

| 層 | 問題 | PCM MMLを省略した場合 |
|---|---|---|
| MML/コンパイラ | 記法、数値sample音符、tieの配置、Pだけでも16trackを選ぶmmlxの挙動 | テキスト変換・コンパイラ由来の問題を回避できる |
| MDX実行環境の表現可能性 | F7の保持、FC pan、ED rateが標準MXDRVでいつ何に作用するか | 残る。直接MDXを書いても命令の意味は変わらない |
| MDX+PDX再生変換器 | カーソル消失、停止未出力、固定rate/pan、decoder状態の誤再構成 | 残る。MMLを通さない元MDXでも検証する |

報告済みのtie問題をすべてMML由来とは分類しない。soundlogのnon-Note命令でのraw cursor消失は、
MDX実行時の問題である。F7/KeyOffDisable、FC/pan、ED/FはMDXバイナリ命令でもある。
標準環境で途中panが実効出力に反映されない場合、再生変換器を即時反映へ変更しても正解にはならない。
その差は対象への投影で既知の意味損失として診断する。常に変換を拒否するとは限らず、
strictとbest-effortで成果物の生成方針を分ける。

今回確認したMXDRV 2.06+17 Rel.X5-Sの通常PCM1経路では、FCとEDはtrack状態への保存であり、
次の新規ADPCMOUTで適用される。保持中にFCを挿入してもIOCSへ即時pan変更を発行しない。
以前のtie+途中pan出力は、この版で元VGMの即時pan変更を再現する根拠にならない。
現行投影はstrictで拒否し、best-effortで開始panを保持して損失区間を記録する。

有限bank `0x04`のstream展開は、固定したlibvgm profileに基づく転送列として実装・検証した。
これはdecoderの消費や標準MXDRVの実行を検証したことではなく、`consumed_nibbles=None`は維持する。
未対応の圧縮bank／stream modeも明示的に診断する。詳細は [PCM/PDX](pcm_pdx.md) を参照する。

## strict / best-effortと四種類の判定

ユーザーの追加方針として、targetで完全に表現できないことを一律の変換拒否にしない。
loss/eligibilityはMDX target projectionがsource IR、target profile、確認済みの規則から判定する。
PCM IRの元pan、rate、reset/continuation、時刻、bytesは変更しない。
source段階の観測不能・解釈未確定はsource evidenceとして残し、target仕様上の損失と混同しない。

| 項目の判定 | 意味 |
|---|---|
| `pass` | 指定scope/profileで本来一致すべき意味が、必要な根拠・検証を含め一致した |
| `lossy` (`known loss`) | target制約と選択した近似により、どの意味がどう失われるかを特定できる |
| `unverified` | IOCS、source解釈、観測不足、未実行の検証等により、挙動や一致をまだ判断できない |
| `fail` | 本来一致すべき箇所の不一致、選択した近似の実装不良、無効な成果物等が確認された |

判定scopeを必ず記録する。target eligibilityの`pass`は、PCM runtime往復の`pass`ではない。
生成・parse・static reference検査の成功も、runtime判定へ昇格させない。
strictの方針によって生成を止めた既知lossは`lossy`の診断であり、再生実装の`fail`ではない。
この場合の`lossy`は予定された投影のassessmentで、生成物の実行検証結果ではない。
`artifact_status=blocked`、`validation_run=not_run`を併記し、runtime判定は未検証として残す。

| 変換policy | 成果物の扱い |
|---|---|
| `strict` | 既知の意味損失を伴う投影を出力しない。source evidenceと理由を保存する |
| `best-effort` | ユーザーが明示的に選択した場合、定義済みの近似とloss診断を付けて、生成可能なMDX+PDXを出力する |

strictは「既知lossを許容しない」というpolicyであり、独立環境で認証済みという意味ではない。
具体的で内部整合したtarget planを作れるがIOCS等が未確認なら、成果物を生成しても`unverified`とする。
不明なdecoder消費等のためtarget plan自体を定められない場合は、best-effortでも生成を保留できる。
best-effortは未知の挙動、壊れたPDX、欠損参照、未実装のsource解析を無条件に無視するモードではない。
近似を定義できない場合や出力不能の場合は、その理由とscopeを記録する。

現行CLIは `--pcm-policy strict|best-effort`、既定strictである。
変換できた場合は終了0、生成を止めた場合は非0とし、`*.pcm.assessment.json`／CSVに四種類の判定を保存する。
batchの処理statusと意味的な評価は別の列とする。PCM replay unavailableは引き続き残る。

成果物の生成状態と意味論的な判定は別に保存する。
reportの設計項目は`policy`、`target_profile`、`artifact_status` (generated/blocked/error)、
項目別assessment、`known_losses`、`unverified_items`、`unexpected_mismatches`、
判定scopeと検証の実行状態。生成できたことだけで`pass`にしない。
総合validation statusは、実際の失敗があれば`fail`、必要項目に未確認があれば`unverified`、
残りが確認済みで既知lossがあれば`lossy`、必要項目が全て一致すれば`pass`とする。
未確認のため総合が`unverified`でも、既知loss一覧を隠さない。未実行の比較を`pass`にしない。

### 途中panのbest-effort投影

指定した通常PCM1 profileで途中panが適用されないことが確認された場合、
新規発音時のpanを保持し、保持中の変更をtargetへ適用しない近似を候補にする。
次の新規発音では、その時点のsource panを明示する。
pan変更を模倣するための人工的な再発音やdecoder resetは追加しない。
source側のpan trajectoryはIRにそのまま残し、target planと診断を別に出す。

診断には少なくとも次を含める。

- loss code/cause (`target_constraint`)、確認したprofileと証拠、選択したfallback規則。
- source playback/event ID、元の時刻と影響区間、source値とprojected値。
- 失われたpan変更・mute区間、およびそれによる可聴性の違い。
- 保持するsample/cursor/timing等の項目と、別途未確認のreset/消費等の項目。

「pan命令を落とした」という説明だけでなく、どの区間がどう再生される予定かを記録する。
known lossの診断はtarget planに基づく。runtime確認が未了なら、その確認不足も併記する。
sample length等の他の制約には別のfallback規則と検証が必要で、無条件の切捨てはしない。

## 再生状態を分ける

次の状態は同じものではない。

1. MDXのnote、gate/keyoff要求、F7、休符、pan/rate/bank指定。
2. MXDRVのsample選択、保持状態、IOCS呼出し、ドライバtick。
3. IOCS/DMAの転送範囲、カーソル、終了・中断・再開、rate/pan設定。
4. チップのPLAY/STOP、データ入力、ニブル位相、decoder状態、実効rate/pan。
5. VGMのdata bank、stream開始・停止・位置・書込み頻度。

VGM `0x94`はstream停止であり、チップのSTOPを直接意味しない。`0x95`もチップPLAYやresetではない。
`0x92`は書込み頻度であり、4-bit ADPCMのニブル消費rateとは別である。
sample先頭へのカーソル移動、noteの再要求、decoder resetも別々に記録する。
STOP/PLAYに伴うresetは選んだチップモデルで確認し、noteごとのresetを推測で追加しない。

## 不足しているもの

| 対象 | 必要な実装・根拠 | 現在の不足 |
|---|---|---|
| 開始・保持 | F7、gate、key-on delay、同一/異なるsample、休符の実行規則 | raw cursorと論理holdの寿命がsoundlogで一致しない |
| sample/cursor | PDX範囲、転送位置、ニブル位相、EOF、途中打切り、次sampleへの切替 | 供給bytesは保存済みだが実消費数は未確定 |
| PDX length/bank | containerの格納能力と選んだnative版の読み出し能力を別に検証 | 調べたPCM1経路はlength末尾16-bitを使用。packerの24-bit上限はnative再生保証ではない |
| 終了・中断・再開 | MXDRVからIOCSへの要求とDMA/チップの実効動作 | stream停止、DMA終了、チップSTOP、keyoffの対応を独立確認していない |
| reset/continuation | 予測器とstep、カーソル、PLAY状態を分けた状態遷移 | 元VGMのreset関係を再現できず、標準IOCS基準も未確定 |
| rate | ED設定が反映される時点、OPM CT出力/PPI/divider、正確な有理数rate | F番号だけの比較では不足。書込み頻度と消費rateを分ける必要がある |
| pan | FCの要求値と、IOCS/PPIによる実効反映時刻、muteとdecoder継続 | 現行tie+panの途中変更は実効再生未検証 |
| 時間 | 全trackのtempo/timer、同期・gate・delay、DMAとdecoder clock、同時刻順序 | MDX境界の量子化検査だけでは転送・消費を保証しない |
| VGM出力 | block IDとPDX slotの対応、stream/直接write、チップ制御の整合 | Leonardo版は固定rate/panと停止stub。soundlogも基準未認証 |
| VGM入力 | streamの実データ書込み展開、直接writeとの順序、供給と消費の状態 | 現在stream/bankは生保存と拒否のみ |

## 独立した実行基準R

第一候補は、特定版のネイティブMXDRVをX68000実機または独立した全体エミュレータで実行する環境R。
PCM8なしの標準9track/PCM1に範囲を限定し、少なくとも以下を固定して記録する。

- MXDRVの版、実行バイナリhash、資料/逆アセンブルの出所。
- 機種、ROM IOCSの版/hash、PCM8・XAPNEL等の差替え有無。
- OPM/ADPCM clock、divider、初期化・開始手順、loop回数/終了条件。
- emulatorの版/commit、chip/DMAモデル、traceの観測地点と時計。

ROM IOCSの動作まで含むため、単に「標準MXDRV」とだけ名付けた基準にしない。
資料としてMXDRV 2.06+17 Rel.X5-Sのネイティブ逆アセンブルを確認したが、
そのバイナリとROMを用いる実行基準Rはまだ用意・実行していない。
実機/独立エミュレータの取得可能なtraceも確認が必要である。

この版の逆アセンブルからは、F7がgate keyoffを抑制すること、activeなPCMの次note要求が
sample lookup前にreturnすること、FC/EDが次ADPCMOUTへ持ち越されること、
新規PCM1開始がADPCMMOD(0)→ADPCMOUT、gate keyoffが条件付きADPCMMOD(1)→(0)となることを追える。
これはC1のdriver/IOCS要求層に使える根拠であり、IOCS内部の物理resetを保証するものではない。

IOCS呼出しだけでは物理PLAY/STOP/resetや実消費を証明できない。
Rでは、時刻付きのOPM/PPI/ADPCM書込みとDMAデータ転送を観測する。
ニブル消費数まで判定する場合はdecoder入力位置・状態の観測または別途認証したchip modelが必要。
取得できない項目は未検証とする。音声比較と実機の聴き比べは補助証拠として別に残す。

NanoDrive8は物理音源を駆動する別のプレイヤーとして状態設計・差分調査に使う。
ネイティブMXDRV+ROM IOCSと異なる制御を含むので、標準のoracleにはしない。
portable_mdx/MXDRVgはhost上の補助基準候補だが、元ドライバとの対応、IOCS相当処理、
X68Sound側の状態とタイミングを確認してから採用する。
同じ由来のコードを複数使うことを、完全に独立した根拠の数として数えない。
soundlogと未修正Leonardo版の出力は、期待値生成に使わない。

## A/B/Cと失敗箇所の切り分け

```text
original MDX+PDX --R--> VGM0 --vgm2mml native analysis--> A
        |
        +--independent direct analysis--> C0 --validated semantics/profile--> C1

A --target projection--> generated MDX+PDX --R--> VGM_ref --analysis--> B_ref
                                     |
                                     +--player under test P--> VGM_P --analysis--> B
```

`analysis`はOPM/PCMのsource解釈まで。target量子化・正規化した出力をsource Segmentと比較しない。
Rはconverter/Pと別の実行経路にする。最初の小さいfixtureからB_refも取得する。

| 比較 | 判定するもの |
|---|---|
| C1 と A | 原MDX実行のVGM化とVGM source解析が、直接解析/独立基準と合うか |
| A と B_ref | source IRからgenerated MDX+PDXへの投影が標準環境の再生を保つか |
| B_ref と B | 同一generated MDX+PDXについてPが標準環境の動作を再現するか |
| A と B、C1 と B_ref/B | 最終的な往復と、複数経路の誤りの相殺がないか |

A/B一致だけは内部一貫性の証拠であり、標準再生の正しさを認証しない。
初段のR→VGM0についても、VGM formatterを通す前のtraceと照合する。
C1とRで同じ実装を流用する場合は異なる観測方法による照合と明記し、
独立したsequencer/IOCS根拠として数えない。

`C ≈ A ≈ B`は目的の略記である。許容差付き一致は推移的ではないので、
各経路/各項目を個別判定し、上の必要な対をそれぞれ比較する。

best-effort成果物でもA/B/Cの比較を省略しない。
AとB_refの既知lossは`lossy`として報告し、厳密な往復成功には数えない。
別途、選択したtarget planの近似が実際に再現され、変更対象外が一致するかを検証する。
fallback適用後の期待target planは、比較対象の実出力を見る前に規則とsource evidenceから確定する。
sourceと期待target planの差をknown loss、期待target planと実出力の差を検証上の不一致として分ける。
loss区間を単に比較から除外することで、余分な再発音・reset・timing不良を隠さない。
同じ生成MDX+PDXを再生するB_refとBでは、source→targetのlossは差を許容する理由にならない。
再生変換器がtarget plan/標準環境から外れた場合は`fail`、根拠不足なら`unverified`とする。

## 元MDX+PDXの直接解析C

C0はMMLへ逆変換せず、MDX命令とPDXから直接作る。
PDXの全slot・元offset/length・符号化bytesと、MDXのtrack/byte offsetを保存する。
note/rest/F7/F8/FC/ED/FD等の命令、repeat/end/escape、tempo、同期、delayを保持する。
command列を読むだけでは再生時刻は分からない。実行制御を解釈してtrack間の時刻を決める必要がある。
OPMも音色・software LFO・shared controlを含む実行解釈が必要である。

C1は、選択したMXDRVの確定した規則とIOCS profileに従ってC0を実効再生情報へ写像する。
要求pan/rateと実効pan/rate、note要求とsample開始、gate要求と物理停止を分ける。
不明な項目にはunknownと出所を残す。新しい推測で完全なCを作らない。

VGMから元のF7、q、Fコマンド配置を一意に逆算する必要はない。
それらの命令列そのものをA/Bの必須一致条件にせず、C1への写像後の観測可能な挙動を比べる。
同じ再生を表す別のMDX構造、slot割当て、loop表現も許容できる。

## 意味論的な比較項目

source ID、slot番号、VGM block ID、Segment行数の一致は要求しない。
Segment境界が異なっても、共通の時間境界で状態区間を比較する。
発音・停止・reset等のedgeは別のordered eventとして比較し、区間の併合で消さない。

| PCM項目 | 比較方法 |
|---|---|
| sample | codec/bit order/output precisionと実bytes。hashは索引、最終的にはbytesを照合 |
| 全assetと再生範囲 | 元PDXの全assetと、VGMで供給/消費されたprefixやsliceを区別。打切りで未再生のsuffixを欠落扱いしない |
| 開始/終了 | 論理trigger、データ供給開始/終了、物理PLAY/STOP、audible開始/終了を区別して時刻・順序を比較 |
| 継続/reset | playback間の継続関係、cursor rewind、decoder reset、reset根拠を個別比較 |
| cursor/消費 | byte offset・ニブル位置・消費範囲。供給byte数を消費数として代用しない |
| rate | clock/dividerと有理数ニブルrate、実効変更時点。streamのwrite cadenceは別に比較 |
| pan | 実効L/R/muteの時間軌跡。mute中もdecoder/cursorが進むかを保持 |
| 終了理由 | gate、rest、sample EOF、explicit stop、song end、loopによる終了/継続 |
| timing/loop | 共通の時間原点、同時刻順序、tempo境界、loop入口/出口のdecoder/cursor状態 |

IOCSが前置/後置する安定化データがある場合、そのprofileで確認した出所を記録する。
`0x80`等の固定byte列を一般的な無音として除去しない。
VGMだけでは復元できないPDX全assetや未再生suffixは、Cから得たcontainer情報と再生一致を別判定する。
全asset復元を一般VGMの往復の必須条件にすると、観測されなかった情報を要求してしまう。

OPMは各operatorのkey edge/mask、KC/KF、voice/operator parameters、volume、pan、
LFO/noise/shared controlの実効状態と時刻を比べる。
KeyOff後の状態・releaseに影響する設定も含める。未知のsource registerを架空の既知値で埋めない。
同じ最終状態でも意味のあるKeyOff/KeyOnやresetの違いを同一視しない。
raw列の一致は診断用として残すが、意味論的な合格条件の代わりにしない。

## 時間と合格条件

- VGM0→A、VGM_ref/P→B_ref/Bは整数sample時刻と同時刻順序を保存する。
- R/P→VGMの時間丸めは元のtrace時計と変換式で計測する。
  44.1kHzへの丸めだけなら1sample以内が候補だが、取得方式を確認する前に固定保証しない。
- A→MDX投影の境界誤差はsource/target時刻を別列で保存する。
  現行6sampleの上限はこの投影のnote/control境界の制約である。
- decoder消費、reset回数、sample bytes、同時刻順序、供給間隔の違いには6sample許容を流用しない。
  長い再生のrate丸めによる累積driftやunderrunも独立に調べる。
- 開始時のwarmup/初期化offsetを除く場合はprofileで確認した一つのoffsetのみとし、
  元の時間も保持する。trackごとのrebase、任意のtime warpで差を隠さない。
- loopは規定回数/時間で打ち切り、その時点の状態と継続関係も比較する。

結果は項目別のpass/lossy(known loss)/unverified/failを出す。
厳密な往復ではlossyを成功扱いせず、必須項目がunknownの場合もPCM往復成功とはしない。
MML生成、MDX parse、PDX bytes/static refs、意味論比較、音声/実機比較の結果は分ける。
CSVには値とともにoriginal/derived time、source offset、observation/inference/assumption、
profile、未検証理由を残す。schemaの追加は既存raw/state/sample dumpsを置き換えずに行う。

## 次の実装・検証順

1. ネイティブMXDRVの版とROM IOCS profileを固定し、独立環境Rの観測手段を確認する。
2. 自作の短いMDX+PDXで、単発、same/different sample、長い保持、F7前後、rest、gate、
   EOF、途中pan/F、delay/tempo/同期の実効規則を調べる。sampleと手書き期待値はsoundlogで生成しない。
3. C0の直接解析と根拠付きC1の限定schema、R trace→比較用情報を用意する。
   target projection側へ四種類のassessmentとloss reportを追加し、strict/best-effortを分ける。
   途中pan等の表現可能性と定義済みfallbackを判定し、現在の生成説明/target eligibilityに反映する。
4. 現行raw/state IRを保持してVGM streamと消費モデルを追加し、独立traceでsource解釈を検証する。
5. 同じsample bindingからPCM MDX命令を構築する出力を追加する。
   OPMの既存経路とtyped MDX builderを使い、PCM MMLや新しいMML compilerを必須にしない。
6. 小さなfixtureからA/C1/B_refを照合し、投影とsource解釈を認証する。
   Pの修正は確認済みの標準規則に限定し、同じ生成MDXについてB_ref/Bを照合する。
7. public fixtureを拡張してから、ローカルの元MDX+PDXと一般VGMへ広げる。
   ROM/楽曲/sample bytesはgitへ入れない。Z_MUSICは別profile/backendから同じPCM IRを消費する。

現在の`verify_opm_mdx_roundtrip.py`はOPM検証であり、このPCMのA/B/C検証は実装されていない。
利用可能なCLIがあるかのように、新しい検証コマンドをREADMEへ記載しない。
