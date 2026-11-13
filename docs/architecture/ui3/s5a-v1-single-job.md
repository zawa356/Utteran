# UI-3 S5-A — 新UIからv1単一ジョブ投入・状態確認（`--next-ui-v1`限定）

更新: 2026-11-12 JST（開発版`0.1.51`、未リリース、Draft PR）。UI-3 [#21](https://github.com/zawa356/Utteran/issues/21) / Epic [#18](https://github.com/zawa356/Utteran/issues/18)。
基点: `main` `397ef75181b61798434c51b35323b845a613fe1d`（PR #47統合、0.1.50）。

> **開発専用モードの接続であり、製品の既定経路ではない。** 引数なし（旧UI）と`--next-ui`（Vue＋旧API）は不変。
> `--next-ui-v1`のときだけ、Vueの単一入力を保存済みB5 Preset/Policy → B5 validation → B7 resolution → B7 durable job → `GET`の状態表示へ接続する。
> Cloudで確認したのは合成fixture・in-process/実loopback FastAPI・headless Chromiumまで。**Windows WebView2実機・実モデル推論は未受入**（後続のWindowsローカル工程）。

## 1. 差分調査表（UI操作 → 現行legacy → v1 → 本件の動作 → 繰越）

| UI操作 | 現行legacy call（`--next-ui`） | v1 endpoint / DTO | `--next-ui-v1`での新動作 | 後続へ繰越 |
|---|---|---|---|---|
| 画面起動 | `GET /api/settings`・`/api/environment`・`/api/wizard/status` | `GET /api/v1/presets`・`/execution-policies`・`/queue`・`/jobs`（読取のみ） | 一覧・キュー状態・サーバーのジョブ一覧を読む。**書込み0件**（Preset/Policy作成・queue変更なし） | — |
| モード判定 | なし（常にlegacy） | `/next/`のHTMLの`<meta name="utteran-api-mode">`（サーバーが`legacy`/`v1-dev`を書込） | 厳密に`v1-dev`の1個だけでv1。欠落・placeholder残り・大小違い・重複は`legacy` | 既定化（S6）はowner判断 |
| Preset/Policy選択 | なし（旧`/api/settings`の既定profile） | `GET /api/v1/{presets,execution-policies}`（cursor全page）→ `GET /{id}?revision=N` | 選んだrevisionを凍結。新revisionは**表示のみ**（明示操作でのみ切替）。一覧から消えたら送信不可 | 本格的な編集・削除UIはS5後続 |
| 初回Preset/Policyなし | — | `POST /api/v1/presets`（`PresetWrite`）・`POST /api/v1/execution-policies`（`PolicyWrite`） | 「作成→内容を確認→保存」の明示操作だけ。意図ごとに新しいIdempotency-Key。結果不明は同key・同bodyの再照合のみ | — |
| 言語・出力形式の変更 | `TranscriptionPayload`へ直接 | `EditingSession.common_overrides`（`/asr/language`・`/output/formats`） | Presetとの**差分だけ**をoverride化（同値は継承のまま） | 他leafの編集UI |
| ファイル別設定（Expert） | 行ごとの`asrBackend/model/device/language/formats` | `files[].overrides` | v1では未対応。設定されていれば**開始前に拒否**（黙って捨てない）。`overrides: []`で送る | S5-B |
| 入力・出力先 | `input_path`・`output_dir` | `files[0].input_path`・`output_dir` | primary 1件のみ。`client_file_id`はページ内ランダム（`f-`＋24hex、pathなし）。UNC/link等はB7が拒否 | — |
| 開始ボタン（Normal/Expert共通） | `POST /api/jobs`（非冪等、1回だけ） | ①`POST /api/v1/validations` ②`POST /api/v1/execution-resolutions` ③`POST /api/v1/jobs`（`JobSubmit`） | 2段階（確認→投入）。v1モードでは旧`startJob`/`cancelJob`をclientで閉鎖（通信しない） | — |
| 複数ファイルの一括開始 | S3-B1 `BatchSubmitter`（旧POSTを順に） | 原子的batchは未接続 | **明示的に無効表示**（`v1-batch-start` disabled）。代替の複数送信経路なし | S5-B |
| 状態表示 | `GET /api/jobs/{id}`＋SSE | `GET /api/v1/jobs/{id}`（ETag=revision） | GETだけを正本にbounded polling（2秒、失敗はbackoff、単一flight、古いrevisionへ戻さない）。進捗率は表示しない | SSE併用・複数ジョブ監視はS3-B2 |
| キュー | `GET /api/queue`（参照のみ） | `GET /api/v1/queue`・`PATCH /api/v1/queue/control`（If-Match＋key） | `paused`を明示。「キュー実行を再開」の明示操作だけ。412は最新GETを読み直し、繰り返さない | 順序変更・hold・自動再開はS3-B2 |
| 取消 | `POST /api/jobs/{id}/cancel` | `POST /api/v1/jobs/{id}/cancel`（`{mode:"cooperative"}`、If-Match、key） | 協調的取消のみ。「要求済み」と「取消済み」（GET）を区別。forceは出さない | — |
| 再起動後 | legacy jobは消える | `GET /api/v1/jobs` | サーバーの永続ジョブ一覧（新しい順20件）を表示し、明示的に「表示する」で追跡。不明だった送信の確定とはみなさない | — |

## 2. モード分離と認証

- サーバー（`create_app`）は、**v1 settingsとv1 jobsのrouteを自分でmountしたときだけ**`v1-dev`を書く（`--next-ui-v1`の形）。`--next-ui`・既定は`legacy`。
  `NextUiBundle.render(nonce, api_mode)`が許可値以外を拒否（`next_ui_api_mode_invalid`）し、`load_bundle`はplaceholder（`__UTTERAN_API_MODE__`）がちょうど1個のbuildだけ受理する。
  metaは固定語のみ（session・path・設定なし）。nonce/CSP・asset cacheは不変。
- frontend（`readApiMode`）はURL・localStorage・入力を読まない。`createCore({apiMode})`で一度だけ決まり、v1でなければ`core.v1 = null`。
- v1の書込みはブラウザの通常`fetch(credentials: "same-origin")`。ページはsession headerを付けない（cookie＋ブラウザが付けるOrigin）。
  Cookieだけで`Origin`なしのPOSTは既存middlewareが`403 origin_required`（E2E・Pythonで確認、緩和なし）。

## 3. Preset/Policyの明示作成

- Preset雛形は`frontend/src/domain/v1/presetTemplate.json`。旧coreの既定値（`Config()`）をB5形式にしたもので、利用者が選ぶのは名前・モデル・言語・出力形式だけ。
  `tests/test_ui3_s5a_v1_single_job.py`が、`PresetWrite`として正しくB5 semantic validationを通ること、および`adapt_settings`後の旧Configが既定値と
  **宣言した差分だけ**で異なること（話者分離オフ、backend明示、prompt `""`↔`null`、実行時に決まるdevice/compute type）を検査する。
- Policyは指示書どおり`mode=auto, allowed_profile_ids=["cpu"], objective=balanced, fallback=none`等（作成前に内容を表示）。
- 保存は実行readyを意味しない（画面に明示）。B5は同名を別IDとして受理するため、同名作成は可能（2件目は別の意図＝別key）。

## 4. 二段階送信・凍結・Idempotency

1. **確認**: 現在の選択・入力・出力・差分から`EditingSession`を作り、deep freeze。`POST /validations`の`effective_settings`が画面側の計算（Preset→common→file、leaf置換）と
   **完全一致**しなければ何も送らない（`v1-mismatch`）。B5の`worker_support`は`unverified`であり、readyとは表示しない。
2. 続けて`POST /execution-resolutions`（別key）。公開`ResolvedSpecView`（profile/recipe/model/device/compute type/確度/根拠/警告/代替候補数）と残り時間を表示。tokenはDOM・log・storageに出さない（E2Eで`page.content()`に含まれないことを確認）。
3. **投入**: `JobSubmit(schema_version=1, resolution_token, editing_session=凍結したもの, resume_mode="resume")`を別keyで1回。確認後の編集はstale扱いで投入不可、期限切れも不可（自動で再確認しない）。
4. 4xxは確定拒否（ジョブ0件、別候補・旧APIへの切替なし）。network/timeout/5xx/読めない2xx/1件に対応しない応答は**結果不明**。
   不明時はkey＋凍結bodyをページ内メモリだけに保持し、自動再送0回。「同じ要求で再照合」でのみ同key・同bodyを1回送る。B7はtoken検査より先に永続replayを照合するので、
   受理済みなら同じ`JobBatch`（初回と同じ**202**を再現。指示書の「200 replay」は既存B7契約に合わせて読み替え）が返る。結果が出るまで成功とは表示しない。
5. 受理後は作成された1件のjob ID・revisionだけを「受付済み（受付≠開始・成功）」として表示し、状態は`GET /jobs/{id}`の値だけ。

## 5. キュー停止・取消・再表示

- 初回起動・復旧後のqueueは`paused`。画面は「停止中（待機中のジョブは開始されません）」と表示し、ジョブ受付でも再開しない。
  「キュー実行を再開」は直前GETのrevisionを`If-Match`、新しいkeyで`PATCH /queue/control {schema_version:1, dispatch:"running"}`。412は最新を読み直して利用者に委ねる（自動再試行なし）。
- 取消は`POST /jobs/{id}/cancel {schema_version:1, mode:"cooperative"}`（If-Match=最新GETのjob revision）。202→「要求済み」、終端はGETで確認。412は読み直し。
- 画面離脱（`pagehide`）はpollingだけを止め、サーバーのジョブは止めない。再読込後は「サーバー上のジョブ（v1）」に永続ジョブが表示される。不明状態はページ内のみなので消える（その旨を表示）。

### 5.1 PR #48レビュー修正（2026-11-13、版据置0.1.51）

- **P1 取消対象の凍結**: 取消1回ごとに`CancelContext`（job ID・`If-Match`のrevision・`MutationIntent`のkey/body・対象epoch）を作成時に固定する。
  最初のPOSTも結果不明時の「同じ要求で再照合」も、この固定値だけで送る（表示中のjobや最新revisionから組み立て直さない）。修正前は、Aの取消pending中にBへ表示を切り替えると、
  Aの不明応答後の再照合がAのkeyのまま`/jobs/B/cancel`へ送られ、同revisionならBが取消されていた（修正前コードで再現を確認）。
- **追跡切替の方針**: 取消がpendingの間は表示jobの切替（一覧からの「表示する」・新しい確認・閉じる）を受け付けない。unknownの間は、同じjobへの再照合か、
  明示操作「未確定の取消要求を破棄」の後にだけ切り替えられる（破棄した要求は再照合不可、画面に理由を表示）。新しい取消は常に新しいkey。同じ意図の同時再照合は1回だけ送る。
- **P2 遅延応答のガード**: 取消応答は「その文脈が現在の未決着の要求であり、job IDと対象epochが一致し、破棄されていない」ときだけ反映する（成功・4xx・412・不明すべて）。
  `applyJob`も別IDの応答と古いrevisionを受け付けない。`dispose()`後は何も書き込まない。
- **P2 一覧の全ページ取得**: `GET /api/v1/jobs`はB7で古い順・1ページ100件・snapshot固定（最大10,000件）。`next_cursor`を逐次GETして末尾20件だけを保持し、新しい順に表示する。
  上限100ページ、ページ間のrevision不一致・cursorの循環・ID重複・途中の409（`cursor_expired`）/503/通信失敗はerrorとし、途中までの一覧を最新一覧として表示しない。
  自動再試行なし（「読み直す」で再取得）。新しい読込や画面破棄で古い読込は無効（世代番号＋abort）。一覧はunknown送信の確定判定に使わない（従来どおり）。

### 5.2 PR #48追加P2修正（2026-11-13、版据置0.1.51）

- 取消がpending/unknownの間は**新しい投入も不可**。ドメインの`canSubmit()`（唯一の投入許可判定）に`switchBlocked() === null`を加え、`submit()`は`canSubmit()`で防御的に拒否する。
  画面の「投入」ボタンは同じ`canSubmit()`を読み（状態・期限タイマー・入力の変化で再評価）、理由は既存の`v1-block`（取消要求が未確定）で表示する。Normal/Expertは同じcontroller。
- 修正前は`prepare()`→一覧からAを表示→Aの取消pending中に投入でき、新job Bの受理で表示がBへ切り替わって取消表示が初期化され、Aの未確定取消は再照合の導線を失った（修正前コードで再現）。
- 解除は既存どおり、同じ対象への再照合の成功か、明示操作「未確定の取消要求を破棄」だけ。`requested`・`done`・`rejected`・`conflict`は投入を妨げない。stale・期限切れは従来どおり拒否。

## 6. 対応範囲と拒否

- 実行できるのはB7/B3の`fw-cpu`・`fw-cuda`、ローカル完全モデル、話者分離なし、用語集・追加引数なし。Presetにworker非対応設定があれば事前に警告し、サーバーの422/409を理由付きで表示する。
- B4 evidenceは有効設定全体に束縛されるため、差分（言語・形式）の変更でready証拠が一致しなくなれば、resolutionが`409 execution_unavailable`等で拒否される。UIはこれを隠さない。
- model/profile準備・smokeをUI起動・入稿の副作用にしない。CUDA readyの推測・CPU fallbackはしない（判定はB4/B7だけ）。

## 7. 互換性

- 既定GUI・`--next-ui`: API・画面・S3-A/B1のテストは不変（Playwright `legacy` projectで既存52件＋mode試験）。`--next-ui`は`legacy`のmetaだけを受け取り、`/api/v1`呼出し0件（E2E）。
- API DTO・JSON Schema・DB layout・worker protocol・CLIは**無変更**。backendの変更は`next_ui.py`のmode placeholderと`create_app`のmode決定だけ。依存追加なし。
- G-09の暫定marker・OSロック・旧投入409・CSP・セッション境界は不変。

## 8. 試験

| 区分 | 試験 |
|---|---|
| Python | `tests/test_ui3_s5a_v1_single_job.py`（雛形の契約・core既定との差分、cookie＋Originでの作成→選択→validation→resolution→submit→replay→GET、Origin欠如403等）、`test_ui2_next_ui.py`（mode placeholder、render許可値、v1 route有無とmode、query非依存）、`test_g09_startup_guard.py`（実`--next-ui-v1`起動の`/next/`が`v1-dev`） |
| Vitest | `src/domain/v1/singleJob.test.ts`（起動時書込み0、二段階の呼出順・key分離、stale、effective不一致、409、5xx/network/壊れた202→unknown・自動再送0・同key/body再照合で同一job、二重クリック、期限切れ、queue CAS 412、協調取消、古いrevisionへ戻らない、dispose、行override拒否、一覧からの追跡、legacyモードでv1呼出し0・v1モードで旧start閉鎖）、`library.test.ts`（mode meta、override/effective、Preset/Policy body、revision凍結・新revision非自動採用・消失、作成unknown→同key再照合、422 issue、ETag不一致、paging上限） |
| PR #48追加P2 | `singleJob.review.test.ts`に5件（取消pending/unknown中は`canSubmit()`がfalseで、`submit()`直呼びでも`POST /api/v1/jobs`の増分0・job数不変・表示/epoch/intent不変、unknown後の再照合はAのみ、破棄または再照合成功で解除、未確定でない取消状態は妨げない・期限切れは拒否）。E2E 1件（Normal/Expertで「投入」が無効、POST 0件、破棄で解除） |
| PR #48修正 | `src/domain/v1/singleJob.review.test.ts`（16件: A取消pending中の切替拒否・不明後の再照合はAへ同key/If-Match/bodyのみでBへのPOST 0件・同時再照合1回、同revisionのA/Bで破棄後のB取消は新key、取消中の新規取消拒否、応答ガード、別IDの応答で表示不変、4xx/dispose後無害、0/20/100/101/201/450件で真の最新20件と全cursor取得、後続ページ409/503/通信失敗・循環cursor・revision変化・重複・ページ上限、古い読込の無効化、一覧からの追跡ID）。E2E: 105件で後続cursorのGETと最新jobの先頭表示 |
| E2E（実FastAPI） | `e2e/transcription-s5a.v1.spec.ts`（`v1` project: 空ストア→明示作成、Origin/cookie実経路、paused→明示再開→完了、stale、409/422拒否、500で応答喪失→同key/body再照合で同一job、協調取消、Expert共通・一括無効、再読込で永続一覧、401/403）、`e2e/api-mode.spec.ts`（`legacy` project: meta=legacy、URL/storageで昇格しない、`/api/v1`呼出し0） |

**非対象（明記）:** Windows WebView2実機（IME・D&D・DPI・CSP）、実GPU/実モデル推論、実B4 smoke証拠でのready、S5-B（原子的batch）、S3-B2（複数ジョブ監視・キュー管理）。CI成功を実機受入とは呼ばない。

## 9. 後続

S5-A（本書）→ **S5-B**（原子的複数batch、ファイル別override）→ **v1上のS3-B2**（複数ジョブ監視・キュー管理・SSE）→ 最終統合ゲート（Windows WebView2実機）。
