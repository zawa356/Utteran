# データ・REST・IPC・イベント契約案

**全て将来仕様案、未実装**。型の必須/任意、request/response、全endpointは[OpenAPI](openapi.yaml)。以下はその意味・交差制約を定める。合成データのみの[JSON例](examples.json)も参照。

2026-11-02 B5追補: opt-in Preset/Policy/validationsの実装とpreview契約拡張は
[API差分](../b5/api.md)を参照。Provenance/ValidatedFile/ValidationResultと一覧5型は
OpenAPIへ同期した。その他endpointはこの設計案のまま未接続。

## 1. 独立した管理概念

| 概念 | 正本・内容 | 他の概念との関係 |
|---|---|---|
| Environment Profile | inventory、profile ID、venv generation、lock/extras fingerprint、native/model資源参照、probe/smoke evidence | 処理presetを含めない。cpu/cuda/intel/vulkanは環境種別であって品質presetではない |
| Processing Preset | ID/revision/schema_version、ASR/話者分離/Prompt/用語集/後処理/出力のsettings | profile IDやvenv pathを持たない。immutable revisionを保存 |
| Selection Policy | auto/fixed、目的、許可profile、memory条件、fallback許可、品質要件 | Presetと独立。fixedはcandidate IDを明示しsilent fallback禁止 |
| Resolved Execution Specification | 正規化settings、具体candidate/profile generation、model revision、要求route、根拠、許可attempt構成 | validation時に作成。実際に初期化できたrouteの証拠ではない |
| Job Specification Snapshot | job投入時の入力identity、preset/policy revision、common/file override、resolved、schema/adapter version | job IDと対応し不変。後日のpreset/auto再評価で変えない |
| Execution Attempt | attempt ID、実際のruntime fingerprint、route observation、fallback理由、core job ID、artifact link | snapshotを書き換えず追記。retry/rerunの出所を追跡 |

全DTOの`schema_version`は整数1、HTTP prefixは`/api/v1`。これらとworker protocol version、core manifest/result/policy versionは独立。未知majorは422 `schema_version_unsupported`、worker不一致は409 `protocol_incompatible`。追加optional fieldは後方互換として生成clientで許容できるが、request typo/未登録設定は422、silent ignoreしない。

## 2. 共通設定・個別上書き

### 2.1 編集と確定

EditingSessionはfrontendで保持可能な構造化要求（APIへ同じ形を送る）である。`preset_ref={id,revision}`、`policy_ref`、`common_overrides: SettingOverride[]`、`files:[{client_file_id,input_path,overrides: SettingOverride[]}]`を保持する。preset/policy参照はrevision必須。空filesは禁止、同一client_file_idは禁止。

順序は `preset.settings → common_overrides → file.overrides → core正規化・validation → resolved → snapshot`。1 fileを1 Jobへ変換し、batchは全件検証成功時のみ一括投入する。folderの展開・対象一覧確定は別の入力選択段階で行い、投入後にfolderを再列挙してjobを増やさない。初期PRのv1はfile入力のみ、旧folder CLI/APIは併存する。

Overrideは`{path: "/asr/beam_size", value: 8}`の配列。pathはschemaが公開する**leaf field限定JSON Pointer**、同じpathや親子重複は422。任意JSON objectをdeep mergeするAPIにはしない。配列（formats/terms/extra tokens）は項目全体を1 leafとして置換する。schemaがleafとして定義するmap（speaker_labels）のみmap全体置換。backend固有設定も登録済みleafのみ。

- override entryの**不在**: 下層から継承。
- entryを**削除**: 現在のcommon/preset値へ復帰。nullをinheritの印に使わない。
- **null**: nullable fieldの明示的な値。例`initial_prompt:null`はPromptを消す。beam_size:nullは422。
- **auto**: `language:"auto"`、`word_timestamps:"auto"`等の明示的なenum。nullと異なる。
- **無効化**: `diarization.enabled:false`、`vad.enabled:false`。null/省略と異なる。
- **未入力**: entryを作らない。空Prompt `""`は正規化後nullと同じだが、provenanceには入力状態を保存する。

共通値を更新した場合、上書きのないfileだけ追従。preset変更は新refと同じoverride集合で再validationし、backend変更で無効になったpathを`incompatible_override`で返す。勝手にoverrideを消さない。provenance（preset/common/file/normalization）とissuesのpathを返し、GUIで「共通値」か「個別値」か表示する。

### 2.2 backend設定

ProcessingSettingsは`asr`、`diarization`、`postprocessing`、`output`を持つ。ASRのbackend/modelには`auto`指定を許すが、Resolvedではcandidateから具体値へ解決する。Resolved内のbackend/model/compute/word_timestampsは具体化し、入力言語の実行時検出を要求するlanguage=autoだけは残せる。実認識言語は結果側へ記録する。backendがfixedならauto policyもその制約を守る。device/routeはPolicy/candidateから選ぶため、Presetへvenv pathやdriverを保存しない。

typed ASR設定: language、initial_prompt、glossary terms、beam_size、VAD、word timestamps、condition_on_previous_text。backend settingsは`faster_whisper` / `whisper_cpp` / `openvino_genai`の名前空間を持ち、選択backend以外に有効値を設定した場合は422。例: whisper_cpp.temperature/temperature_increment/no_context/dtw/threads、faster_whisper.compute_type、GenAI固有値はcapabilityで登録されたものだけ。

用語集は文字列配列をそのまま全backendへ押し込まない。backend別Prompt compiler versionをResolvedに記録する。initial_promptとglossaryが共存する場合、ユーザーPromptの後に改行と用語列を連結する提案とし、token budget超過は422（黙って切り捨てない）。未実証backendはglossary capabilityを`unverified`とし、通常モードのquality保証に使わない。既存no_contextの既定は維持し、Issue #1実測前にPrompt効果を保証しない。

### 2.3 Expert追加引数

`extra_arguments:{backend:"whisper-cpp",tokens:["--entropy-thold","2.6"],allow_unverified:false}`のような**token配列**。GUIがテキスト入力を提供する場合も配列editorか文法を限定したtokenizerを用い、shellへ渡さない。`shell=False`だけでbackend option悪用が防げるとは扱わない。

1. capabilityのoption registry（alias、型、arity、範囲、対象native version）で分類。
2. `known_supported`は型検証、`known_invalid`は422。`unknown`は通常422 `argument_unverified`、Expertが明示許可した場合のみwarningとtainted reproduction flag付きで許容する。
3. registryが未分類でも、executable/subcommand、入力/出力/path、model、device/route、protocol/stdin/stdout、progress、resume、force/unlock、token、plugin/load/response-file等の制御はreserved。短縮alias・`--key=value`・単一dash連結・`@file`・`--`・positional等も正規化して検査。安全に分類できないtokenは許可しない。shell演算子/NUL/改行/展開記法を拒否する。
4. typed fieldと同じ設定を指すtokenは**衝突として422**。後勝ち・暗黙優先順位なし。typed fieldを未入力にしてもresolved defaultが所有するreserved settingは上書き不可。
5. faster-whisper/GenAIはlibrary APIであり、任意CLI tokens非対応。registryに明示した拡張fieldだけ別typed adapterへ渡す。汎用kwargs passthroughは禁止。
6. 有効なnormalized token列、native/adapter version、警告をprivate snapshotへ保存。token/credential類はそもそも引数禁止、Prompt/用語集はtyped private fieldに限る。追加引数はすべて機密扱いで通常log/SSEへ出さない。

## 3. Capabilitiesと選択

### 3.1 直積を作らない

coreが登録したrecipe（backend/model family/route/OS制約）をinventory evidenceとjoinする。frontendがbackend×model×deviceを直積展開しない。recipe IDは安定、Candidate IDはrecipe+profile generation+model revision+routeを指すopaque ID、任意pathをcandidate IDから作らない。

| recipe | backend | 要求native variant | encoder要求 | decoder/GGML要求 |
|---|---|---|---|---|
| fw-cpu | faster-whisper | null | CPU | CPU |
| fw-cuda | faster-whisper | null | CUDA | CUDA |
| wc-cpu | whisper-cpp | cpu | CPU | CPU |
| wc-vulkan | whisper-cpp | vulkan | backend既定（観測なければunknown） | Vulkan |
| wc-ov | whisper-cpp | openvino | OpenVINO GPU（現行） | backend既定（観測なければunknown） |
| wc-ov-vk | whisper-cpp | openvino_vulkan | OpenVINO GPU（現行） | Vulkan |
| genai-ov | openvino-genai | null | 選択OpenVINO CPU/GPU/NPU | 選択OpenVINO device |

これはsoftware recipe例で、全PCの成功保証ではない。GenAIは別backend、OpenVINO+CUDA recipeは存在しない。pyannote CPU/CUDA/XPUは別の話者分離component候補としてASRとの言語・timestamp・memory制約をcoreが検証する。

CapabilityCandidateはsoftware_support、hardware、runtime、venv、model、native、smokeを個別Observationとして返す。各Observationに`state=passed/failed/unknown/not_applicable`、observed_at、fingerprint、reason_codeを持たせる。未観測はunknown、not_applicableをpassedへ変換しない。environment状態とcandidate runnableは別で、`runnable=ready/blocked/unknown`、blockersを持つ。native不要recipeはnot_applicable、smoke unknownの候補をreadyとしない。smokeは対象model/route/runtime fingerprintへ拘束し、driver/lock/native変更時に失効する。

### 3.2 Policyとresolve

Autoは**構築済みかつready**のみを候補にする。品質・language/diarization・model・memoryのhard条件でfilterし、比較可能なbenchmark、過去の成功/失敗、reliability、目的の順でrankする。benchmark比較keyはfixture hash、duration、language、model revision、precision、timestamp/diarization、threads、warmup/runtime version。異条件は別group、欠損値はunknown。smoke成功は実行性のみ、速度優位性の根拠にしない。実測なしは明示した静的heuristicで同順位ならcandidate ID順に決定しconfidence=low。

結果にselected candidate、alternatives（除外理由含む）、reason_codes、evidence kind（smoke/benchmark/history/static_rule）、confidence、policy_revision、inventory_revisionを付す。autoが新venv/model取得を開始することはない。候補なしは422 `no_runnable_candidate`とSetup Plan作成への案内情報を返す。fixed候補消失は409 `fixed_candidate_unavailable`、他へ切替禁止。

resolveはside-effect-freeな準備。必要なprobeは別の明示的refresh操作とし、HTTP timeoutを避ける。resolve tokenの有効期間は5分、session/入力identity・revision・inventoryへ束縛。job投入transactionで全file分を再確認し、変化時409 `resolution_stale`。queue待機中に環境が変化したらjobをblockedにしsnapshotは保持。新auto評価は次回投入にのみ反映。

### 3.3 fallback

fixed policyのfallbackは`none`のみ。autoは投入時に明示した`preapproved`候補列だけ。各代替はcandidate IDだけでなく、profile generation/model/runtime fingerprint・route・設定・adapter versionを`fallback_specs`として凍結する。実行時に可変catalogの最新値で補完しない。未承認download、model品質低下、出力意味の変更をしない。設定に適合する代替がなければfailed/blocked。開始後のfallbackは新attemptとして、from/to、reason code、evidence、時間、stage invalidationを追記。`requested_route`、`initialized_route`、encoder/decoderの`observed`を分離し、観測不能は文字列`unknown`を返す。要求variantだけで実route成功を作らない。

## 4. HTTP共通規約

- base `/api/v1`、JSON UTF-8。日時RFC3339 UTC、opaque ID、resource revisionは単調増加整数。major API/config/IPC versionを区別。
- successはOpenAPI記載のtyped body。GET 200、create 201、async accept 202、delete 204。非同期応答に`Location`、GET resourceに`ETag: "<revision>"`。
- 全endpointの共通失敗: 401 session、403 origin/host、404不明ID、409 semantic conflict、412 If-Match不一致、422 schema/意味検証、428 If-Match不足、503実行/保存不可。JSON parse不正は400。HTTP timeoutは操作失敗と同義ではない。
- error bodyは`{schema_version:1,error:{code,message_key,request_id,retryable,issues:[{path,code,message_key}],resource_id?}}`。messageに入力値/絶対path/Prompt/argv/tracebackを入れない。UIはmessage_keyを翻訳、未知codeはgeneric errorでrequest ID表示。
- preset/policy PUT・DELETE、queue PUT/PATCH、plan approve/cancel、operation retry/resume/cancel、job cancel/retry/rerunには`If-Match`必須。失敗時read→再編集。`If-Match:*`は受け付けない。
- 全POSTとQueue更新は`Idempotency-Key`必須。session単位でなくlocal installation ID+verb+canonical endpoint+keyでscope。body hashはinstallation secretでHMAC化（Prompt推測防止）、不一致409 `idempotency_conflict`。同keyは同resource/status/Locationを返す。replay判定をstale If-Match検査より先に行う。
- key/resultは少なくとも7日、active resourceの間は期限切れさせない。受理記録とresource作成/outboxを同transactionでcommit。期限切れ後の再送は新要求扱いになるため、clientはkeyとoperation IDを保持しGETで調停する。serverがexactly-onceの外部process効果を保証するとは言わない。
- list responseはitems、next_cursor nullable、revision。cursorはfilter付きsnapshot pagination、期限切れ409。本文をeventで丸ごと返さない。

### endpoint領域

全method・request/response型・statusをOpenAPIに定義。主な流れ:

1. GET capabilities/environments/policies/presets → snapshotとrevision。
2. POST environment-refreshes → RefreshOperation（cached inventoryを即readyと偽らない）。
3. POST validations → per-file effective settings、provenance、issues。POST execution-resolutions → per-file Resolved + token。
4. POST jobs → resolution token+同じEditingSession、immutable Job列、atomic queue登録。
5. POST jobs/{id}/revalidate はblockedの同一snapshotの復旧確認のみ。GET queue、PUT queue/order、PATCH queue/control、PATCH queue/items/{id}、POST jobs/{id}/cancel。
6. POST setup-plans →変更対象・容量・工程・license/credential要件。POST approve →承認plan hashへ拘束、POST setup-operations →実行。GET/retry/resume/cancelで復旧。
7. GET jobs/{id}/events相当は統一GET eventsのresource queryで取得。GET jobs/{id}/diagnosticsはsanitized paginated entries。結果本文/出力・履歴は当面旧APIを使い、新Jobのlegacy_core_job_id linkを通じて参照する。

## 5. セットアップ承認と資源競合

Setup Planはchanges、step DAG、download/diskのestimate（bytes nullならunknown）、対象profile generation、inventory revision、期限、content hash、required agreementsを持つ。初回CUDAも追加Intelも同じplan→approve→operationで表現。初回は軽量bootstrap inventoryを用い、profile coreが存在しなければbundled declarative recipeとOS probeで計画する。probeの詳細検証はprofile構築後coreへ委ねる。

approvalはユーザーのplan確認actionからのみ発行、hashに拘束し24時間有効。追加工程/対象/download増大、inventory変化で再plan/再承認。認証token値やagreement同意をAPIが捏造しない。運用上の認証済みsessionと、ユーザーが変更計画を承認する操作は別。setup operation投入でapprovalを消費する（同idempotency key再送は同operation）。

単一resource-heavy slotをsetup/job/model operationで共有。profile generationごとのread leaseとbuild/deleteのexclusive lease、共有native/model資源にも参照leaseを適用する。CUDA利用中にIntel構築の候補を提示しても同時GPU実行は初期仕様でしない。既存CUDA環境の保持はplanに明記し、承認していない既存環境更新を追加しない。

## 6. SSEと再同期

GET `/api/v1/events?after=<cursor>&resource_id=<id>` はcookie sessionで認証（EventSourceは任意header不可）。`Last-Event-ID`も受理し、afterと両方指定時は同じでなければ400。stream全体でinstallation epoch+単調seqのID、resourceごとrevision、attempt ID、event schema_version。DB transactionで状態とoutboxをcommitしてから配信する。

SSE `id: <epoch>:<seq>`、`event: state_changed|progress|inventory_changed|queue_changed|fallback|resync_required`、`data: EventEnvelope`。payloadはresource_kind/id/revision、status、stage、fraction(nullable)、reason_code、attempt IDだけ。raw log/path/Prompt/文字起こしは入れない。progressは0..1の工程内比率でnullは不明。terminal状態とheartbeat欠如を混同しない。

最低7日または100,000件を超えた古いイベントはcompaction可能、進行中resourceのterminal eventは保持期間内で消さない。cursorがretention外/別epochなら接続前410 `event_cursor_expired`、または接続済みstreamで`resync_required`を送り切断。clientはGET `/sync`でresource一覧/queue/inventoryの整合snapshotとcursorを取得し、そのcursor以降を購読する。snapshotとcursorは同read transactionのwatermark。大量jobはsnapshot token付きpaginationを使い、ページ間のrevisionを混在させない。

重複IDは破棄、同resourceの低revisionは無視、seq gapはfilter配信では正常なので欠損断定しない。revisionが飛ぶ/未知eventはresource GETで照合。pollingとSSEは同revision reducerへ適用し、GET responseも古ければ捨てる。通信再接続はprocessを再起動しない。POST timeout時は同key再送/GET、workerを二重起動しない。

## 7. Profile worker IPC

将来の例: `<profile python> -m utteran.worker --protocol 1`。実装は後続PR。固定commandのみ、設定をargvへ展開しない。

```json
{"protocol_version":1,"request_id":"req-synthetic-1","operation":"execute","job_id":"job-synthetic-1","snapshot_ref":"opaque-private-snapshot","payload":{"schema_version":1}}
```

実際のenvelope payloadは共有DTOのResolved/検証要求。上のsnapshot_refはpathではなくlauncherがopenした保護入力の識別子。stdin length prefixまたは1 JSON line（改行はJSON escape）でmax16MiB、stdout JSONLはmax1MiB/record、unknown field/versionは拒否。handshakeは10秒、read-only probe timeoutはoperation種別で宣言、native推論の「進捗なし」を自動failedにしない。実行deadlineはPolicyからsnapshot化し、超過はcancel要求→規定grace→検証済み境界のみkill。

stdoutはhello/accepted/progress/completed/errorのtyped envelope、request/job/attempt ID一致を検証。EOFやprocess exitだけでcompletedとせず、exit 0 + terminal record +成果物整合を必要とする。child eventsをそのままSSEへforwardせず、backendが許可fieldへ投影して永続化する。旧CLI progress-json adapterは別protocol capabilityとして識別。

## 8. ローカルAPI・機密保護

loopback限定bind、ランダムsession key、cookie HttpOnly/SameSite=strict、header認証、CSP self、no CORS許可、no-storeを保持。v1 mutationはHostをbound authorityへ制限し、Originありは完全same-originのみ、Originなしのnative/header clientは専用session header必須。Content-Type application/jsonとサイズ上限を検査。SSEも同session認証。query stringへsession tokenを増設しない。現行/launchはbootstrap専用で履歴・logからsessionを除外する。

OpenAPI公開endpointは無効のまま。後続buildで副作用のないDTO/route factoryから`app.openapi()`またはDTO schema exporterをoffline実行し、正規化schemaをartifact化→pinned generatorでTS型生成→CI差分検査。schema exporterはprofile/token/modelを不要とし、静的仕様に実データ・example秘密値を入れない。runtime docsを開かずに型を生成できる。

private snapshotはOSユーザー限定directory（Windows ACL / POSIX 0700、file 0600相当）、portableは可搬性による保護限界を明示する。Prompt/用語集/入力pathは再現性に必要なprivate値として保存、ログ・イベント・PR・通常診断には出さない。tokenはkeyring参照かsession memoryのみ。portable session tokenは終了で消え、resume時に再入力が必要。暗号化済みとは主張しない。機密snapshotのexportはexplicit opt-in、通常support exportはredacted projectionのみ。設定hash/argvも公開logへ出さない。

## 9. 実装時に適用する追加交差制約

- fixed policyはfixed_candidate_id必須・fallback=none、auto policyのfixed_candidate_idはnull。allowed_profile_ids空は全既存profileを許す意味とし、profileを新規作成する意味にしない。
- diarizationのnum_speakersとmin/max併用禁止、min<=max、無効化時はcount値をnullへ正規化する。
- Resolved settingsのbackendとcandidate.backend一致、modelとmodel revision一致、inventory/runtime fingerprint一致を検証。fallback_specsも同様に検証・凍結する。
- queue itemのIf-Matchはqueue revision。setup itemのholdはqueue metadata（setup statusはqueuedのまま）、jobはheld statusも同transactionで更新する。
- force cancel capability未検証の構成は409 cancel_mode_unsupported。CancelRequestのenumに存在することは全backendでサポートする意味ではない。
- preset/policy GETはrevision query省略で最新、指定でimmutable履歴を返す。削除はtombstoneとしsnapshotから参照中の履歴を消さない。
- v1 ProcessingSettingsにまだ独立fieldがないlegacy general/ffmpeg/path設定はinstallation runtime settings、input/outputはFileInputへ分離。未対応の旧設定をimportする場合はunsupported_legacy_settingで明示し、黙って捨てない。
