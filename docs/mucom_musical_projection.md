# YM2608 VGMから音楽的MUCへの投影

現在の既定経路は `VGM → native State／Segments／PCM IR → musical units／source structure → MUCOM target projection → MUC／DAT／PCM bank` です。コピー基盤を記録した`integration_baseline.json`は同期時点の記録です。旧Segment単位のレンダラを復元せず、保持音の内部制御を発音全体へ関連付けます。

## 発音単位と構造

`opna_music.py`はFMの正の時間を持つSegmentを、ネイティブのcontinuity IDと隣接区間でKey-On→Key-Offのgateへまとめます。同じ意味のStateは内部で併合し、元Segment・イベントIDを保持します。元のゼロ時間イベントと解放中のStateはソースのCSVに残ります。全Key-Offまでのwhole gate内部では部分Keyによりcontinuity IDが変わっても内部軌跡として保持します。正のSegmentだけでは見えない一時的な部分Keyも順序付きcontrolとして保持します。正規化を無効にした基準経路では全キーオフを経由するゼロ時間の発音パルスを理由付きで拒否します。正規化時は、後述の明示した省略規則に一致する閉じた全operator発音だけを省略できます。解放中のパラメータ変更は独立したcontrol unitとしてMUCへ投影します。

`musical_structure.source_plan`は継承したMDXの既存構造処理を共有化したものです。元のgate／SSG／リズム／PCMの意味と相対時刻から反復候補を求め、MUC文字列だけで構造を推定しません。ネイティブ有限反復は`[body]N`、Nは2..255、深さは16までです。深さ超過時はターゲット表記を展開し、ソースの構造カタログを保持します。

現在の構造処理では、MDXと同じ`musical_structure.source_plan`／`SourceLoopPlan`を、発音全体の外側と、保持音内部の制御列・解放後の制御列の内側へ適用します。最初のgate entryと保持中の制御を区別し、曲loop位置で分割した後半を新しいattackとしません。SSG・リズム・PCMもネイティブの意味状態・順序付きactionから内部キーを作ります。

元のnative tree／全反復候補を保持したまま、同じnativeキーと投影後の相対時間幅が一致する候補を別に求めます。元の幅2の反復が投影時刻では一致せず、より広い幅6の反復なら一致する場合などを、時刻を変更せず選択できます。出力文字列だけで音楽的反復を捏造せず、各候補がnative列でも一致することと展開命令列の一致を確認します。native／時間適合の候補、外側のみ／内部も使う候補を比較し、同じ演奏列を保つ短い表記を選びます。同値長では従来のnative案を優先します。MGSDRV固有のマクロ構文やMDXの音長・opcode規則はMUCへ流用しません。

MUCOM専用のtarget compactionは、既知の同値`o`／`D`を省略し、C128で正確に一致する通常・付点音価を選びます。loop入口・戻り、音色／raw制御などで既知状態を無効化します。`@`・音量・pan・H・y・KD・triggerを一律に省略しません。長い保持音／休符は、既に出力された同じ時間片だけを有限反復にし、タイと各KD再設定を保ちます。内部・外側・時間片を合成した最終深さは16以下です。超過部だけ展開し、深さ制限前の選択数と最終MUCの反復数を区別します。

行分割はMDXと共通の`mml_format.wrap_track`を使用します。パート指定を各行へ付け、110文字を目安に、反復開始／終了でも改行します。文字数短縮は表記選択の基準であり、MUB容量の最小化や64KBエラー解消の保証ではありません。

タイは同じ発音の正の内部区間、または長いnoteの長さ表記の分割に使用します。Segment境界をそのまま新しい音符／タイの境界にしません。

## ネイティブ投影

検査したプロファイルはMUCOM88 Windows 260407同梱mucomDotNET、legacy MUB8、YM2608 7987200 Hzです。FMはABCHIJ、SSGはDEF、リズムはG、ADPCM-BはKへ割り当てます。MUCはCP932／CRLF、依存ファイルは同じフォルダの名前で記述します。

FMの通常noteと`D`でnative FNUM／BLOCKへ投影します。DAT音色と`v15`を設定し、`yTL`で元のoperator TLを復元します。保持中のoperator／algorithm変更は音色を再ロードせずparameter commandで表現します。`H`によるAMフラグの副作用は直後の`yDR`で復元し、ハードウェアLFOの無効化は`y$22,0`で明示します。部分Keyはinactive operatorをKD128で抑制し、111 clocks以下のnoteごとにKDを更新します。保持中のmask変更はKDとy28で元の順序を保ちます。初期ゼロ時間maskも直接Key命令と先頭tieで保持します。CH3特殊／CSMと不完全な音色は未対応です。インストール済みlegacy compilerはp0とyB4を拒否するため、pan 0を音色変更や近似muteへ黙って置き換えません。

SSGはtone／noise／volume／mixer／hardware envelopeを投影します。形状0〜15と単発triggerに対応します。installed legacy S命令は8〜15しか発音できず、SSG上のgeneric yはコンパイルしてもdriverが正しく消費しません。Gのglobal-control laneでDEFのnote処理後に元のshapeを復元します。追加された同時刻の形状リセットをJSONに診断し、波形・包絡線位相の一致は保証しません。prescale変更と共有hardware envelope channelの重なりは未対応です。

リズムは発音をserial held gateへ制限せず、trigger／dump mask・level・panの時系列actionとしてGへ投影します。@0のtiming carrierによりdriverの自動dumpをmask 0にし、元の非ゼロhit／部分dump／保持中controlを順序付きで出力します。ゼロ時間actionも保持します。これらのcontrol carrierを独立した短い発音gateとして長さ保護しません。

ADPCM-Bは32個のnative bank slotへ元の符号化データを格納します。4byte start-address単位とinclusive endを使用し、header lengthは`encoded length - 4`です。サンプルSHA256・slot・アドレスをJSONへ記録します。rateは元IRの有理値からtarget Delta-Nへ投影します。未解決ソース、保持中のアドレス／メモリモード変更、repeat、32slot／native address超過、不完全な4byte単位、nativeでmuteするlevel 250..255は拒否します。未再生のPCMデータ／初期診断だけでは他音源の変換を拒否しません。追加clockのないvolume-only拡張ヘッダーはtarget側で時計の事実と区別します。単一の正のdecoder区間と、その開始／終了時刻だけのゼロ時間制御はnative playbackへ束ねます。元IRのindependent／continuation診断は変更せず、payload、元Segment／イベント、解決根拠を保持します。外部プレーヤーのmixer音量はchip registerのdecoder設定と混同せず、音響balance補正は未検証です。ROMブロックをRAM初期値とみなす仮定は元IRに残ります。MSM6258／PDX制約はこの投影へ使用しません。

異なる元clockではpitch／rateを補正しますが、detune、envelope、LFO等の物理的な変化は補正していません。JSONに診断を残します。未対応のactive chip／commandは省略せず拒否します。

## 時刻とループ

全パート・内部制御・曲loopを一つの絶対時刻表へ投影します。
既定は`--normalize-lengths`有効、`--normalization-ms 8`です。
短い発音の省略、境界移動、Timer-B／C128、保護区間、原子的fallback、
正規化後の構造ビューの仕様は[output_normalization.md](output_normalization.md)にまとめています。
元State／Segments／PCM bytesを変更せず、レンダラーと比較器が同じ採用時刻表を使います。

VGMが宣言したloopへ参加する各パートに`L`を置き、共通のloop endへrestで揃えます。introだけのパートは有限で終わります。FM／SSGの保持音は、LとEOFで意味状態が一致する場合にtieで継続します。EOF-held FMは先頭の最初のキー書き込みが全Key-Offで、Lと同じ投影clockになる場合も接続できます。状態が異なる正の保持区間を跨ぐ境界は、まだ理由付きで拒否します。loopがない入力のEOFで切られた保持音は、投影EOFで終了させることを診断します。native startup位相、loop periodの補正、以後の同じperiodによる再生は別々に記録・比較します。`--no-loops`は有限フレーズの表記を展開するオプションで、VGMの曲loopを取り除きません。`--no-song-loop`はターゲットの曲loopを無効化し、最初の1回分を元VGMのEOFまで有限出力します。FM／SSGのloop seam解析・L／EOF接続制約と曲loop用の時間制約も適用しません。元のloop情報はsource CSVに残り、`*.mucom.json`の`song_loop`にsource startとtarget start（無効時はnull）を区別して記録します。有限フレーズの反復圧縮は独立して有効です。公開の手書きfixtureの「ファイル全体でL 1個」は素材の契約であり、production multi-part MUCの制約ではありません。

PSGを含む曲loop入力では、初回の演奏を保持してから、曲末状態を引き継いだ
反復分を共有readerで再解析し、初回EOF（2周目開始）へ`L`を置きます。
混在するFM／PCMも同じ展開後の時間軸に揃えます。元のloop開始・初回EOFと
反復開始・展開後EOFは別々に記録し、反復側の継ぎ目検査を省略しません。
PSGを含まない経路のループ動作は従来どおりです。
詳細と出現IDの対応は[PSG直接投影](psg_to_ssg.md#ノイズエンベロープループ)を参照してください。

## 中間成果物

- `*.opna.music.csv`：FM whole gate、内部trajectory、元Segment／イベント対応。
- `*.mucom.units.csv`：全パートの発音／rest／control unit、ソースとtarget時刻、native source key、commands。
- `*.mucom.<part>.loops*.csv`：元の反復構造、全memberと実際のtarget folding。
- `*.mucom.<part>.timing_loops*.csv`：元のnativeキーを維持し、選択済みclockにも適合する反復候補と木。native側と各木自身のprojection結果を分けて保持します。
- `*.mucom.<part>.selected_loops.csv`：実際に選択した外側の反復表記（深さ制限前）。
- `*.mucom.control_units.csv`：内部sliceごとの元／target時刻、nativeキー、展開命令と元Segment／イベント対応。
- `*.mucom.inner_repeat_candidates.csv`／`*.mucom.inner_loops.csv`：内部の全反復候補、native／時間適合の木・選択と元unit対応。
- `*.mucom.plain.muc`／`*.mucom.before_depth_limit.muc`／`*.mucom.uncompacted.muc`：反復前、選択後・深さ制限前、深さ制限後・target compaction前のMUC。バンクは通常の生成物を参照します。
- `*.mucom.compaction.csv`：target token位置、setter省略・音価表記・時間片反復の判断。`bytes_saved`は未検証のMUB短縮量として使いません。
- `*.mucom.json`：元VGMとMUCのSHA256、timing boundaries／error、collapsed interval、FM投影、PCM binding、対応範囲／拒否理由。
- `*.mucom.normalization.json`：省略したFM／PCM発音、元時刻、閾値、保護区間、時計の採否・fallback。
- `*.mucom.timing.csv`：採用したsource sample→target clockと移動量。JSONと同じ時刻表。
- `*.mucom.output.state.csv`／`*.mucom.output.segments.csv`／`*.mucom.output.music.csv`：元証拠と区別した出力専用ビュー。
- `*.mucom.<part>.normalized_loops.*`：元カタログとは別の正規化後の構造木、候補、投影。
- native sourceのraw／State／Segment CSV、SSG CSV、ADPCM-B／PCM IR：ターゲット補正前の証拠。

一括出力は各runの診断を残し、公開MUCは隣接bank名を使用します。コンパイラ入力だけを隔離して絶対パスへ解決します。exit zeroだけでは成功とせず、MUB8／非空track count／ERROR不在を確認します。

## ネイティブFM往復検証

`mucomDotNET2vgm.exe`でMUBをoffline replayします。投影JSONには実際に選択したclockが必要です。単一変換をコンパイルする例：

```sh
python vgm2mml.py tests/fixtures/public/opna/from_fm/legato_patch_mix/legato_patch_mix.vgm --outdir outputs/check/legato --dump-passes
python -c "import sys; sys.path.insert(0, 'scripts'); from mucom_compiler import compile_muc; compile_muc('outputs/check/legato/legato_patch_mix.muc', 'outputs/check/legato/legato_patch_mix.mub')"
python scripts/verify_mucom_native.py tests/fixtures/public/opna/from_fm/legato_patch_mix/legato_patch_mix.vgm outputs/check/legato/legato_patch_mix.mub --projection outputs/check/legato/legato_patch_mix.mucom.json --outdir outputs/check/legato_native
```

既定replay toolは`.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe`です。別配置は`--tool`で指定します。入力VGMのSHA256をJSONと照合します。MUBは対応するMUCのコンパイル結果を指定してください。

FMのgate count／onset／end、正のtarget幅を持つ各source controlのpitch・operator・algorithm・pan・LFOをnative Stateと独立に比較します。正規化した内部制御timelineの区間数・開始／終了境界も照合し、切替時刻のずれや余分な区間を検出します。元VGMに記録済みの明示的な短音省略だけを適用した期待値と、cropしたnative出力の順序付きoperator Keyエッジを比較します。採用時刻表の欠落は再丸めで補わずエラーにします。native startup 1clockを別に扱い、target時刻との許容差は2 VGM samplesです。loop曲は2周を比較し、native driverが終端に追加する第3周boundaryをcropします。`--no-song-loop`の投影では、元VGMがloopを宣言していても1回分を比較します。元VGMにFMがない場合は`not_applicable`とします。元FMの全発音を認められた規則で省略した場合は、native出力に予期しない発音がないことを確認します。これは省略ポリシー適用後の検査で、元の音響同一性の主張ではありません。

このFM gate checkerは極短いcollapsed control interval、解放中control、
SSG／リズム／PCM同等性、波形・聴取を認証しません。これらの未検査事項を
FM比較やコンパイル成功から推定しません。

参照：[MUCOM88 MMLリファレンス](https://github.com/onitama/mucom88/wiki/MML%E3%83%AA%E3%83%95%E3%82%A1%E3%83%AC%E3%83%B3%E3%82%B9)、[mucomDotNET](https://github.com/kuma4649/mucomDotNET)、[WING MUCOMVGM](https://github.com/wingghost/MUCOMVGM)。


## 診断と性能上の境界

同じYM2608間でFM operator／algorithm等を別音源用に近似する必要はありません。現在の拒否には、MUC note／loop／compilerの表記制限と未実装の投影が含まれます。周波数変換失敗と同一視しません。元のStateを制限へ合わせて変更せず、対応可能なnative commandを実際の合成MUC→MUB→VGMで確認します。

gate membershipは索引＋bisectで構築し、発音ごとの全Segment／event検索を除きました。元順序・ゼロ時間member・終端を含む区間は保持します。解析の進捗段階をJSON／conversion.logへ出力し、timeoutの段階を確認できます。CSV出力やsource解析自体に時間のかかる入力まで、すべて解決したとは主張しません。

実曲の再検証はユーザーのリグレッションで行います。実曲確認が必要になったら一旦停止し、対象と目的を通知します。

VGM拡張ヘッダーのclockとvolumeは別テーブルです：[一次仕様](https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/vgmspec171.txt)。


## 初期ピッチ・SSGループと処理時間の診断

FMの初期Key-On時点でFNUM／BLOCKが未観測でも、同じ発音内に観測済みの完全なピッチがあり、未観測prefix全体とその観測時刻が採用済みの同じtarget clockに重なる場合は、音符表記のためだけにそのピッチを使用できます。元State／Segment、意味キー、Key／operator制御の順序は保持します。出力用の仮の値とdonorを`fm_initial_pitch_resolution`／`*.mucom.initial_pitch.csv`へ記録します。正のtarget時間が残る未観測区間、他の発音からの借用、未観測reset値による補完は行いません。基準経路で採用済みclockがない場合も理由付きで拒否します。

初期の正のsource区間が0 target clockへ縮む場合にも、明示的な最初のKey-Onと続く部分Key操作を保存します。その初期Keyより前に投影ピッチを設定し、最初の正の音符をタイで接続して重複attackを避けます。ネイティブdriverはDでprevious note codeを0へ戻すため、同じ0を使う`o1c`のタイ付き音程更新を省略する場合があります。FM／SSGとも、同じregister pitchになる別の音符＋detune表記を選びます。この制約はMUCOM target adapterだけに置きます。[公式driverのFRQ_DF／SSSUBB等](https://github.com/kuma4649/mucomDotNET/blob/master/mucomDotNETDriver/music2.cs)と、実際のMUC→MUB→VGMで確認しました。

SSGは、元のloop先頭状態とEOFから引き継ぐ状態の二つをseedにして、実際のloop内register writeを採用clock順に再生します。EOF seedは末尾の正長区間の状態だけでなく、EOF同時刻のゼロ時間書込みまで適用した最終状態を用います。共有raw controlと元のprovenanceも保持します。各正時間区間の有効tone／noise／mixer／amplitude／envelope設定を比較します。使っていないnoise／envelope設定はその区間の音響状態から除外しますが、raw値とwriteは保持し、後で有効になる区間も比較します。初期状態が違っても、観測した先頭writeで正のtarget時間に入る前に復元できる固定音量SSGは、`observed_head_restore`としてLを跨ぐ保持音を分割・継続できます。L直後の制御を保持し、EOFの継続は観測した末尾の発音状態に従います。`*.mucom.ssg_loop_checks.csv`と`ssg_loop_analysis`に両seedの比較を保存します。正時間がある間に状態が違う場合、またはhardware-envelopeの状態復元に位相の判断が必要な場合は理由を記録して拒否します。全ケースを一律に先頭状態へresetしません。

SSGのDは第1octaveのperiodへ適用されてからoctave shiftされます。元periodを同じregister値へ投影するdetuneを使います。software envelopeのE設定は発音開始、真の無音後、hardware→software切替で初期化し、連続した保持中のピッチ／音量変更では再設定しません。hardware→softwareは別の出力単位にし、既存hardware phaseをタイ中のE変更で壊さないようにします。tone専用パートから共有noise periodを書き戻すことも避けます。hardware envelopeやnoiseの波形位相、複数noiseパートの同時刻順序の全面的な同等性は保証しません。

`stage_timings`は完了した各処理段階の秒数です。段階開始時に`current_stage_started_at_unix`、`current_stage_completed=false`を保存し、成功・例外時に完了時間を確定します。timeoutでprocessが停止しても最後に入った段階と完了済み時間が`*.mucom.json`へ残ります。一括出力のtimeout通知にも保存済みのMUC段階を表示します。Timer-B候補の最近傍計算と誤差検査は、同じ整数領域・丸めを保ったまま境界ごとのFraction生成を減らしています。整数化の丸め・許容領域・採否は独立に一致を検査します。算術部分の測定は実曲全体の高速化率やtimeout解消の証拠ではありません。

境界回復の検証対象は、初期FMの全／部分Key順序、SSGの2周のtone／mixer／volume、複数octave・detune両符号・period0／4095、保持中の全native状態区間、無音と再発音、hardware→software切替です。合成・公開素材による検証と、ユーザーによる実曲のリグレッションを区別します。

### 音色バンクのファイル名

生成する音色バンクは `<stem>.dat`、MUCの参照は `#voice <stem>.dat` とする。
入力stemが8文字以内の場合、MSX-DOSの8.3形式を保つため、音色バンクに追加の接尾辞は付けない。

### CH3特殊モードのMML表現と未対応範囲

[本家MMLリファレンス](https://github.com/onitama/mucom88/wiki/MMLリファレンス)には
FM3の効果音モード用 `Sn1,n2,n3,n4` がある。引数順はOP4, OP3, OP1, OP2、
本家仕様の範囲は0～255。共通音程に対するスロット別ディチューンであり、
任意の独立周波数・キー軌跡をそのまま4つの音符列へ置換できることを意味しない。
[mucomDotNETのMML仕様](https://github.com/kuma4649/mucomDotNET/blob/master/MML.txt)には
`EXON`/`EXOF` とスロット割当 `EX` があり、Cパートのページを使った別表現が可能。
.NET独自機能のMUBは本家との互換を保証しない。MUCOMMSX対応とも混同しない。
現在のコンパイラアダプタはmucomDotNETConsoleを利用するが、生成器は特殊モードを拒否する。

native State/Segmentsは各オペレータのFNUM/BLOCK、モード、原レジスタ証拠を保持する。
CSMのTimer-A起因の自動キーエッジは未再構成である。特殊モードの情報保持と、
CSMを含む発音再構成の完成を区別する。

## PCM保持中制御と相互運用の境界

OPNA ADPCM-Bの非repeat・読取専用ROM／seeded RAMのdecoder epochに対し、
保持中の音量（0B）、パン（01の上位2bit）、Delta-N（09/0A）を投影する。
同じsampleでも別のstartは別epochであり、内部control sliceは新しいsample noteにしない。
音量0・パン0でもdecoder位置は進み、Delta-N0ではnominal位置進行が停止する。
最初の音符は正規化後も正の長さを保護する。これを0 clockにすると、先行する
live controlを初回noteのdriver初期化が上書きするためである。

`opna_adpcm.py`のState／Segments／符号化sampleは変更しない。
`opna_adpcm_playback.py`が元の有理rateを積分し、同一epoch内のnominal nibble位置と
推定自然終了を別の解釈結果として記録する。推定自然終了を元Segmentの境界へ書き戻さず、
MUCのlive epochをそこで新たに切らない。観測されたepoch終端まで制御を配置し、
ターゲットのencoded sampleは自然に尽きる。元の自然終了推定と実測終了を混同しない。
元位置と、採用target clock／Delta-Nで進む位置・終了差も別々にJSONへ残す。
fetch、decoder位相、DAC interpolation、波形や聴取の同等性は認証していない。

MUCは最初の`@sample`音符を一度だけ出し、その後は同じ音符コードのタイで継続する。
保持中の`v`／`p`はnative driver内部値だけを書き換える場合があるため、
[mucomDotNET](https://github.com/kuma4649/mucomDotNET)の
multi-port `y0,1,register,data`でlive registerを更新する。rate更新時に`D`や異なる音符で
再開始しない。RAM8由来の元sampleもbankへ再配置するため、target pan書込みは
4byte address-unit設定を維持する。元メモリモードはsource IRに残る。

旧Windows260407同梱compiler（同梱CHANGEはTAG046/2020-04-04）は4引数yを拒否する。
この命令を含むMUCだけ、公式`bin.zip`固定版のNET6 compilerを自動選択する。
通常のMUCは既存compilerを使用する。`--mucom88`による明示指定は優先される。

```sh
python scripts/setup_mucom_pcm_tools.py
python scripts/export_mucom.py tests/fixtures/public/opna/pcm_controls --outdir outputs/pcm_controls --no-song-loop --timeout 600
```

固定配布参照はrevision `f0403762a37028c77afe23193bd674d7bf6efaf7`、archive SHA256は
`666bc13830a6a9d4748a8f6b9c7c0317f59f2732b7bc12b6c25209600138f2dd`。
.NET6のWindows runtimeが必要。工具はignored `.tools/mucomdotnet-pcm/output/NET6`へ
別配置し、既存工具を置き換えない。MUBからのFM検証もこのprofileへ追従する。
native replayの実測開始位相は旧profileがclock1、固定NET6 profileがclock0である。
これは工具profileとして記録し、曲ごとに誤差へ合わせた補正はしない。
拡張MUC/MUBの本家MUCOM88・MUCOMMSX互換は保証しない。

追加passは `*.opna_adpcm.playback.csv`／JSON（source nominal位置・epoch・元ID）と
`*.mucom.pcm_projection.json`（MUC制御、元時刻、target消費位置の監査）である。
従来のPCM IR、source CSV、構造カタログ、normalization CSVは保持する。
公開fixture `tests/fixtures/public/opna/pcm_controls/heldctrl.vgm` は自作の符号化データのみ。
合成MUBの検証では全ての正のnative制御区間、同時刻の書込み順、start数、次epochを比較する。

未対応: repeat、Lをまたぐheld PCM、再生中のアドレス／下位メモリモード変更、record/CPU supply、
ROM更新・未解決stream、非標準prescaler、初期level250..255など。
Segmentを消したりPCMを再スタートして回避せず、Projection診断を維持する。

x68kのSegaPCM実装は、source State/観測Segment、Sega固有runtime再生、MSM6258投影を
分けている。今回もこの境界とprovenanceの契約を踏襲する。Segaのunsigned8 ROM、16.8位置、
clock/128 rendererをDelta-T decoderへ流用せず、MSM6258/PDXを共通native IRにしない。
将来の相互変換は各source固有の復号／再生位置・control軌跡を保持した段階から、
出力先のcodec、gain/pan、bank、channel数、rate/loop制約へ別途投影する。
今回はSegaPCM↔OPNA変換、ADPCM-Bへの新規encodingや多声mixを実装していない。

## 再生環境との互換性

本家MUCOM88、MUCOM88 Windows、MSX／Makoto向けMUCOMSX／MUBPLAYは
compiler dialect、MUB形式、bank配置、memory、clock、runtimeを個別に確認します。
MSX再生は互換性の目標であり、すべての生成MUBが動くという検証済みの保証ではありません。
MGSDRVへの出力とは別の経路です。音色DATの一致だけではdriverによるTL／AM等の
上書き後の状態を証明できません。生成命令、実際のcompiled replay、聴取の結果を区別します。


## Short FM control states

With length normalization enabled, the FM output gate view uses the shared
MSX-derived selection policy using the independent `--fm-state-ms` duration
(default 1 ms / 44 original VGM samples). The existing `--normalization-ms`
(default 8 ms) still controls eligible whole-note omission and clock movement;
changing it does not change the internal FM-state threshold. Native State, Segments and source event order remain
unchanged. The same target path handles native OPNA and mapped OPM-to-OPNA FM.

The current conservative scope is pitch and uniform additive TL changes across
all four operators. Tone parameters, nonuniform operator levels, pan/modulation,
partial Key changes, zero-time controls and ordered global effects protect
adjacent state runs. Released-envelope controls retain their existing path.
This restriction avoids treating a short FM timbre change as an OPLL preset
intermediate write.

A retained initial state fills any removed prefix; the preceding retained state
fills an omitted middle run. The last positive state is protected as
`release_tail_state`: key-off does not restore full frequency/level registers,
so omitting this state could incorrectly change the subsequent released tail. Gate attack and release times are retained.
If every ordinary run is short, the final state is retained. Equivalent adjacent
retained states are coalesced without crossing protected controls.

The `.mucom.fm_states.input.csv`, `.output.csv` and `.decisions.csv` artifacts
record the source-derived gate view, selected output view and per-control
selection/protection reason. `--no-normalize-lengths` bypasses this stage.

The `--fm-state-ms` option is connected to native MUCOM OPNA projection and
mapped OPM-to-OPNA, including batch `export_mucom.py`. It does not currently
apply to the separate MUCOMSX MSX-source conversion path; nondefault values
are rejected there. No new time-threshold sweep or private conversion is
required by this interface update.
