# Output normalization: semantic contract and target allocation

## Scope and ownership

This contract separates shared musical meaning from chip evidence and target
clock/notation constraints. It does not claim that the three repositories
have identical implementations or that copying their modules connects a new
frontend. The current MUCOM adapter is specified separately below.

Native State, Segments, source timestamps/IDs and encoded PCM stay immutable.
An output-only musical view owns accepted omissions and boundary movement.
A complete short sounding gate is different from a short held control slice.
Retain released controls as well as non-key writes inside omitted gates;
filtering only by a later omitted gate must not drop edits after an earlier
retained gate. All-omitted output still represents finite elapsed song time.

Eligibility and source diagnostics precede target omission. An unsupported
PCM epoch does not become valid merely because it is short. Keep bank bytes
and source bindings separately from emitted playback.

## Clock allocation and validation contract

Attack-clock inference nominates candidates; it does not set the permissible
output error. Each target supplies exact integer domains, origin and required
ordering/positive intervals. Source sample time, musical subdivisions and
native timer units are different quantities.

Use one accepted map across engines, internal controls, loop boundaries,
renderers, pass dumps and validators. Missing map entries are an error, not
permission for independent nearest rounding. Protect required positive gates
and ordered key actions. Report collapsed internal controls separately from
omitted whole gates; preserve their commands even if their width collapses.
Validate final boundary errors, intervals and cumulative song/loop timing.

Adopt omission policy and timing atomically, or restore the entire structured
baseline. Failed normalization does not select a Segment-by-Segment renderer.
Retain original source event IDs across loop traversals so omissions and replay
expectations refer to the same evidence. Startup phase belongs to a versioned
runtime profile, separate from source origin and loop-period correction.

Keep native structure catalogs alongside accepted-clock semantic views.
Equality distinguishes attack/held actions, voice/pitch/control order and
membership. Each emitted repeat expands to the full projected command stream.
Chip-specific evidence adapters may differ; do not replace source planning
with unrelated target-text repetition heuristics.

Integer optimizations require independent rounding/domain/acceptance parity,
including signed/tied/large boundaries and rejected candidates. Stage starts
must be persisted before work so timeout diagnostics survive termination.
A microbenchmark or faster membership lookup is not end-to-end throughput
proof and does not authorize private regression.

## Current MUCOM adapter

This section is the current implementation policy, not chip specifications
or a claim about inherited MGS/MDX defaults. Shared helpers currently used
include `normalization_samples`, `note_normalization.infer_timing`,
`ordered_timing.allocate_tick_domains` and `musical_structure.source_plan`.
MUCOM supplies native domains, C128 adaptation, evidence adapters, MUC syntax,
banks and compiler limits. The inherited [MGS normalization document](note_normalization.md)
describes a different clock/profile; [OPM note lengths](opm_note_lengths.md)
describe MDX. Their tolerances and timer rules are not MUCOM budgets.

全パート・内部制御・loop start／endを同じ絶対timelineへ投影します。source sample 0をtarget clock 0へ固定します。native runtimeのstartup位相は別に扱い、source originをずらしません。C128を使用し、Timer-Bを0..255から選びます。1clockのVGM sample数は`(256 - timer) * 2304 * 44100 / 7987200`です。元の時刻を保持し、ターゲットへ整数clockを割り当てます。レンダラーと比較器は、採用した一つの時刻表を共用します。

`--normalize-lengths`は既定で有効、`--normalization-ms`は既定8msです。
この値は二つの出力ポリシーを兼ねます。元VGMの時刻で閉じた短い発音を省略する閾値と、ターゲットの境界時刻を元から動かせる上限です。VGM sample単位へ切り捨てるため、8msでは352 samples以下が対象で、353 samplesは超過です。チップの仕様ではありません。

FMの省略対象は、元の順序付きKeyイベントで確認できる、全operatorの単独Key-On→全Key-Offです。部分Key、途中の繰返しKey操作、EOFで閉じていない発音、曲loop境界を跨ぐ発音を短音扱いしません。保持音の内部にある短い制御sliceも短音ではありません。非Keyの音色・周波数・音量・LFO変更は省略音内と解放中の双方で保持します。元State／Segmentを変更せず、別の出力用状態ビューを作ります。すべての発音を省略しても、経過時間を持つ有限の無音パートを生成します。

PCMのソース適格性とバンクを先に検証・構成します。独立した単一decoder epochで、観測したresetで閉じた短いPCM発音のみ省略できます。継続区間、repeat、未解決ソース、推定した自然終端、EOF終端はこの省略の対象外です。元PCM IRとバンクの符号化bytes・bindingsは残します。SSGとリズムの発音は省略せず、同じ時刻表で投影します。

時計の候補推定には共有の`note_normalization.infer_timing`を使い、採否はMUCOM88のTimer-Bで独立に判定します。推定器のconfidence toleranceを出力の移動許容値へ流用しません。推定できない素材では、宣言した移動上限に合わせたnative Timer-B候補を使用します。MDXのperiodやMGSDRVの音長規則は流用しません。各候補の有理数periodから厳密な整数clockの許容範囲を求め、MDXと共有した`ordered_timing.allocate_tick_domains`で順序を保持します。保持する正の発音、正の部分Key操作、長い休符、trigger間隔、曲とloopの時間幅を保護します。短い休符は同一clockへ縮む場合がありますが、Off→Onの命令順は保持します。短い内部制御の幅が同じclockへ縮む場合も、commandsを保持し`collapsed_control_intervals`で明記します。

候補が成立しなければ省略と時刻変更を一括で取り消し、元の発音を保持する基準経路へ戻ります。`--no-normalize-lengths`もこの基準経路を選びます。基準経路の`--timing-bound-ms`は既定8msで、従来の最近傍丸めと通常音価の優先を使用します。基準経路でも正の発音を保持できなければ、理由付きで拒否します。

構造化には、元のnativeカタログと時間適合カタログを残した上で、採用した出力長による別の正規化済みビューを追加しています。FMと休符は微小な元時刻の揺れが異なっても同じ出力長・意味的制御列なら反復候補にできます。発音開始と保持操作、音色・ピッチ・制御順序を区別し、展開命令列の一致を確認します。SSG／リズム／PCMの内部timing identityは現時点では保守的に元のキーを使用します。

## Inspectable output and validation limits

The source/native dumps are retained beside `*.mucom.output.state.csv`,
`*.mucom.output.segments.csv` and `*.mucom.output.music.csv`.
`*.mucom.normalization.json` records eligibility, omissions, original times,
thresholds, protected intervals, clock acceptance and fallback.
`*.mucom.timing.csv` and projection JSON expose the same chosen map.
Native, timing-feasible and normalized repeat catalogs stay distinguishable;
see [MUC pass inventory](mucom_musical_projection.md#中間成果物).

Native comparison must apply only recorded omissions and the selected timing
map. Compiler success, encoded-byte identity and FM gate checks do not certify
released-envelope controls, auxiliary waveform identity or audible equivalence.
Inspect source-linked released/control rows independently. Song-loop validation
uses the explicit [target policy](mucom_musical_projection.md#時刻とループ).
