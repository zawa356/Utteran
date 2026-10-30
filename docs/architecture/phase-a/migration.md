# 段階移行・実装PR・受入計画

設計承認前に下記実装へ進まない。各PRは小さな独立レビュー単位とし、共有schema変更・core adapter・GUI切替を一括PRへまとめない。

## 1. PR依存と推奨順

| PR案 | 依存 | 対象module/成果 | 必須検証とrollback |
|---|---|---|---|
| B1 contracts DTOとoffline schema exporter | Phase A承認 | 新utteran_contracts、schema snapshot、import境界 | OpenAPI差分、strict validation、GUIにcore/heavy importなし。未使用DTO追加のrevertで戻せる |
| B2 core application/Config adapter | B1 | cli/config/pipeline/registry、設定正規化 | 旧CLI/config/hash/出力golden比較。旧CLIを正本のままservice抽出 |
| B3 profile worker protocol | B2 | 新worker、gui/cli/processes | handshake/version、stdin secrets、JSONL framing/timeout/crash、旧adapterへflag rollback |
| B4 candidate inventory/resolve | B2、GUI接続はB3 | devices/native/profiles/asr、gui/environment/hardware | recipe非直積、unknown/freshness、GenAI制約、auto/fixed/benchmark条件のstub試験 |
| B5 preset/policy/編集継承API | B1,B4 | 新preset/policy store、api/settings境界 | absent/null/auto/false、解除、preset変更、provenance、ETag、protected private値 |
| B6 durable operation store/outbox | B1 | gui/jobs/operation_queue、new control store | commit前後crash、idempotency、sync watermark、DB busy/disk full。旧queueと同時dispatch禁止 |
| B7 immutable job submissionとqueue controls | B3,B5,B6 | jobs/api/queue、attempt↔core ID | atomic batch、snapshot不変、reorder/hold/dispatch pause、cancel/complete race、resume回帰 |
| B8 setup plan/approval/journal | B3,B4,B6 | setup_wizard/hardware/settings、新setup engine | 初回CUDA→追加Intel、失敗/retry/reapproval/復旧。既存wizard adapter併存 |
| B9 Events/diagnostics・互換API adapter | B6,B7,B8 | api/SSE、history projection、redaction | event replay/duplicates/410/sync、legacy payload/status、restart後状態、no token/body leak |
| U0 frontend技術比較spike/ADR | B1で型のみ参照可 | 文書・使い捨てspike、WebView2配布調査 | Vue/React等のCSP/サイズ/a11y/保守比較、ユーザー採択。PR #23は候補資料 |
| U1 shared typed client/UI状態 | B9,U0 | 選定後frontend基盤 | mock遅延/timeout/重複/順序逆転、POST調停、offline assets |
| U2 画面を1つずつ移行 | U1 | settings/environment→models→jobs/queue→setup→history | 各viewの旧機能parity、feature flag切替、旧view rollback |
| U3 VM/実機・配布受入 | U2 | tests/packaging/docs | Windows VM mock、WebView2/installer/portable/native連携、対象GPU実機を分離 |

B6はB2〜B5と独立に進め得るが、統合前に契約レビューを済ませる。B7のqueue controlをU1の基盤より先に安定化する。次フェーズの推奨開始点は**B1だけ**（軽量contractsとoffline schema、既存動作無変更）。

## 2. 正本と新旧併存

| 資産 | 併存中の正本・変換 | 切替・rollback条件 |
|---|---|---|
| CLI options | 従来Typer→Config→service。v1 workerは構造化Resolved→Config | 旧引数/default/exitをgolden維持。追加optionは別互換PR |
| config.toml/env/.env | CLIは現行優先順位維持。v1は読込時にlegacy-default presetへcopy/importする明示action | 起動で自動書換えなし、import元とversion記録。v1投入後にenvで上書きしない |
| GUI settings | theme/language/path等の現ファイルを正本として維持 | preset/policyは別store。default_profileはimport時fixed policy候補へ、unknownは確認 |
| legacy API | `/api/*` payload/statusをcompat adapterが保持 | v1へroutingするdomain単位をflag管理。new必須If-Match等を旧clientに突然要求しない |
| core job/history | 既存manifest、input hash ID、resultsを正本 | control job UUID→core job ID link。既存履歴を新queueへ勝手に投入しない |
| resume / intermediates | 現行stage hashとschema/policy version、artifact照合 | 新設定はhash adapterで対応。Prompt/args変更をhashへ反映、旧job書換え禁止 |
| output | SRT/VTT/JSON/TXT/MD、speaker labels/presentation、改行/BOM維持 | API snapshotと出力schemaのversion混同禁止。schema goldenで比較 |
| models | utteran_paths/catalog/manager配置を維持、複製しない | v1 DBは参照とfingerprintだけ。削除は承認とlease、rollbackでmodel消去しない |
| venv/native | 既存OS別profile layout、shared native manifest維持 | discovered generationとして登録。unknownはverify、全profile再sync禁止 |
| installer/portable | GUI軽量依存、同梱assets、portable data root、session-only token | contracts同梱/禁止module検査。DB新formatを旧版が読まない場合legacy UI rollbackを先行 |
| WebView2/dialog/D&D | 現app.py/native bridgeを継続 | frontend変更時もbridge protocolと絶対path受渡しは検証。Electron移行なし |
| secure token | keyring/env/.env/sessionの既存取得方針 | v1 snapshotに値なし。portable再起動は再入力、旧キー削除・移動をしない |

新domainをv1へ切替えた後は**そのdomainのwriterは1つ**。旧adapterは同application service/control storeへ変換し、旧queueと新queueを二重稼働させない。まだ切替えていないdomainは旧serviceを正本とし、v1はread projectionのみ。dual writeを正本2つで解決しない。旧APIには新feature非対応を明示し、hold等を独自推測させない。

B6切替前にlegacy待機/実行operationをdrainまたはユーザーcancelし、private backup・journal確認後にflag切替。旧memory operationの途中状態はDBへ捏造して移行しない。rollback時は新dispatch停止→子process退出確認→新DB/snapshotを保全→旧UI/APIへ。active processが残る・schema downgrade不可・旧版がsnapshotを解釈できない場合はrollbackをblockし、データを消して戻さない。新機能のjobは保持し、旧版に表示されないことを明示する。

## 3. 後続テスト設計（本Phaseでは新機能テスト未追加）

| ID / 対象 | 入力・障害 | 期待する観測可能結果 | 環境 |
|---|---|---|---|
| T01 setup初回 | NVIDIA detected、CUDA runtime可、承認前 | plan見積りのみ、download/venv変更0。approve後だけ開始 | VM stub |
| T02 profile追加 | CUDA ready、Intel plan | CUDA generation保持、Intelだけ構築/verify、両候補一覧、既存job不変 | VM stub、実機は別 |
| T03 unsupported | openvino+cuda、GenAI+ja+diarization | 422、候補選択不可、子process0 | unit/VM |
| T04 venv failure | uv非zero、disk full、crash | failed/interrupted、既存profile保持、retryはnew attemptと同承認範囲 | VM stub |
| T05 missing model | venvありmodelなし | blocked、auto download0、plan提示 | VM stub |
| T06 smoke fail/stale | import成功だがinfer失敗、driver変更 | readyにしない、error code/evidence、旧smoke失効 | VM stub＋実機 |
| T07 selection | 比較条件が異なるbenchmark、同順位、低VRAM | 比較group分離、deterministic choice、reasons/confidence、fixed失敗で切替0 | unit/VM |
| T08 fallback | auto許可候補/未承認候補、fixed | 許可候補だけ新attempt、unknown route維持、from/to/reason、snapshot不変 | VM stub＋実機 |
| T09 inheritance | common変更、file override解除、null/auto/false、preset backend変更 | 継承正確、非互換pathエラー、typed/args衝突422 | unit/schema |
| T10 immutable submit | enqueue後preset/policy/inventory変更 | snapshot同値、再解決なし、開始不能ならblocked。batch1件不正で全件未投入 | unit/VM |
| T11 queue race | reorderとdispatch同時、hold/release、2 clients | 単一revision勝者、412/409、order/slot一意、paused中active継続 | VM concurrency |
| T12 idempotency | double click/POST response喪失、同key異body | 1resource/1queue item、同Location、異body409、restart後も重複なし | VM crash tests |
| T13 cancel race | completion commit前/後、delayed cancel | 規定terminal、202後cancelling、停止確認までlease保持、成果物整合 | VM＋GenAI実機 |
| T14 event recovery | duplicate/reverse/out-of-retention cursor、restart | revision reducer、410→sync→replay、old GET破棄、二重dispatchなし | browser mock/VM |
| T15 UI failure | 遅延/401/422/503、view switch、timeout | 即pending、理由・再試行、accepted operationを失敗と誤認しない | browser/VM |
| T16 recovery | DB commit/child起動/terminal/outbox各点crash | interrupted/held/paused、nonce検査、stage再検証、process再開と偽らない | VM kill injection |
| T17 mode/view | normal↔Expert、setup途中再接続 | 同snapshot/override provenance、backend stateから復元、未対応pause非表示 | browser/VM |
| T18 privacy/security | 合成sentinel Prompt/token/terms/path、cross-origin、oversized input | logs/events/exception/argv検査で漏洩0、401/403/413、schema runtime非公開 | unit/VM/packaging |
| T19 compatibility | 旧TOML/job/result、export-only変更 | CLI/出力golden一致、hash依存だけinvalidate、結果検索/再出力維持 | Linux/Windows VM |
| T20 distribution | installer/portable/Python、WebView2、dialog/drop、125/150/200%DPI | local assets、keyring/session、起動/終了、禁止import、legacy view rollback | Windows VM＋対象実機 |
| T21 real inference | CPU/CUDA/Intel/Vulkan/native variants、Prompt A/B | 実route/精度/latency/VRAMを条件付き記録。smokeと性能評価を区別 | 対象実機のみ |

Windows VMでGPUや実モデルを必須にしない。T21、native route初期化・hard kill安全性・実Explorer/セキュリティポリシー等は#22/#15/#7へ分離する。mockで合格しても実機合格とは報告しない。

## 4. UI完成形との対応

通常モードはfile選択→resolveのhuman reason→job投入→state/result。venv/CUDA等はerror recovery/詳細画面にのみ表示可能。Expertは左file一覧、中央flow/ASR/diarization/output、右output/queue。共通設定とfile overrideを同EditingSessionで管理し、詳細argsは折り畳む。queue詳細、history/results、profiles、models/environments、settingsは同resource APIを使用。初回wizardと追加profile画面は同plan API、異なるpresentationだけを持つ。

検索/仮想scroll、speaker filters/rename、再出力、テーマ/ja-en、native picker/drop、models IR/verify/remove、batch、logs/support等のparityをviewごとに確認する。機能欠落があれば旧viewを残し、UI framework採用を理由に省略しない。
