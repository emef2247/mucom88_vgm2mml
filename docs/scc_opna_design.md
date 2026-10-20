# SCC → OPNA: FM+additive 検討

2026-10-18時点の調査・提案です。SCC→MUCの実装完了や再生確認を示すものではありません。
既存のPSG→SSGを維持し、SCCをFM+additiveで近似する方針を対象とします。

## ソース解釈と構造化

SCCのnative State/Segments、32-byte波形、音量・音程・enable・波形更新の証拠を保持します。
MSXで採用した発音単位、音量run、phrase/loopの処理を共有し、各Segmentを新しい音符にしません。
x68k側の `scc_note_units.py` / `musical_structure.py` の移植差分を最初に確認します。
SWエンベロープの曲線はその解釈層に残し、MUCの命令選択とは分離します。

ターゲットではSCC一声に主FM音色と残差additive音色を候補として割り当てます。
OPNAはFM6ch（MUC ABCHIJ）、SSG3ch（DEF）です。PSGをSSGへ出す場合でも、
SCC5声それぞれへ常にFM2ch、計10chを割り当てることはできません。
まず各声の主音色を確保し、残差レーンは空きと改善量に応じて選びます。
不足時の品質低下と実際の声の省略を区別し、割当・省略理由を記録します。
既存native FMとの混在は別途容量競合を扱う必要があります。

## FMのSWエンベロープ

[本家MUCOM88のMML仕様](https://github.com/onitama/mucom88/wiki/MMLリファレンス)で、
`E` はSSG用の6パラメータSWエンベロープです。MGSDRVの任意長 `@e` テーブルと同じ機能ではありません。
調査対象のmucomDotNETコンパイラ `muc88.cs` の `SETSEV` もFMでの `E` を拒否します。
同コードにDotNetのADPCM用分岐はありますが、FM対応を意味しません。

FMでも、発音を維持したまま時刻付き `yTL,slot,value` を出すことでSCCの音量runを表現できます。
既存 `py/mucom_fm.py:project_state` は保持中のoperator TL変更を同形式で投影します。
FM+additiveでは主音色のcarrierと残差音色の出力を同じ音量曲線で調節し、
modulator TLは音色として保持します。両レーンのgateと音量runの対応をdumpします。
内部の音量更新ごとにKey-Onや音色ロードを追加しないことが重要です。

これは組み込みエンベロープテーブルへの圧縮ではなく、制御列による表現です。
MUC文字数・MUB命令量の削減は別途検証が必要で、MDXの肥大問題が自動で消えるとは言えません。
FMのhardware EGに曲線を近似させる案は音色探索・誤差方針を伴う将来案です。
公式の本家仕様ではTL用SW-LFOの `MT` は正しく実装されていないとされるため、
現在の互換プロファイルで利用可能とは仮定しません。

## CH3特殊モード

OPNAのCH3特殊モードはoperatorごとの周波数設定に利用できますが、
FM3（MUC C）の4operatorを使います。通常FM6chに独立した4chが追加される機能ではありません。
algorithm 7と組み合わせた一声の倍音配置候補としては価値があります。
一方で他のFM3用途、slot別Key/周波数の順序、共通音量や音色制御との整合が必要です。

本家の `S` は共通の音符に対するslot detune（OP4, OP3, OP1, OP2の順、0..255）です。
任意の独立音符列や全倍音を自由に割り当てられる保証はありません。
[mucomDotNET MML仕様](https://github.com/kuma4649/mucomDotNET/blob/master/MML.txt)の
`EXON` / `EXOF` / `EX` はCパートのpageにslotを割り当てる拡張です。
現在のコンパイラアダプタがmucomDotNETを使うことと、この拡張を生成・検証済みであることは別です。
現行 `py/mucom_fm.py` はCH3特殊/CSMを拒否します。

初版は通常FMのFM+additiveを優先します。特殊モードは将来の明示的なDotNet専用候補とし、
compiler/driver/MUB形式を固定した独立検証が済むまで既定で使いません。
CSMのTimer-A自動KeyとCH3の独立周波数も区別します。

## 音色評価と実装順序の提案

1. 共通SCC発音単位・波形・音量runを移植し、既存PSG→SSGと並行する入力経路を設計する。
2. YM2608用の音色候補とnative DAT/FNUM/BLOCK投影を用意する。
   x68kのYM2151/4MHz評価bankは探索の初期候補に限り、YM2608での精度保証として流用しない。
   採用clock、音源core、音量校正、波形hash、音程別誤差を保存する。
3. 6ch内の主/残差レーン割当、carrier音量run、gate/loopを既存MUC構造処理へ接続する。
4. 最小限の公開・自作データで中間結果とMUC/MUBを確認し、実曲リグレッションはユーザーが担当する。

本調査ではコンパイル実験・実曲変換・聴取は行っていません。
特殊モードの音色改善量、TL更新の聴覚誤差、実行負荷、容量への効果は未測定です。
