# UI-3 S3-B1 — 複数入力・ファイル別設定・段階的な受付（旧`/api/jobs`、`/next/`、opt-in）

- **作成:** 2026-11-10 JST。対象 [Issue #21](https://github.com/zawa356/Utteran/issues/21)（親 [Epic #18](https://github.com/zawa356/Utteran/issues/18)）。Issue #21はS3-B2/S4/S5が未完のためopenのまま。
- **基点:** `main` `c56d37060997428275e11826be5b6b2aa2bac630`（PR #42 / S3-Aのマージコミット。作業直前に`origin/main`と一致、作業ツリー無変更）。
- **版:** 開発版 `0.1.47`（未リリース、tag/releaseなし）。§11。
- **状態:** Linux（Vitest＋headless Chromium＋実FastAPI）とpytestの自動試験まで。Windows WebView2実機は最終統合ゲート（§10）。
- **分割（owner承認）:** S3-B1＝複数ファイル・個別設定・段階的な一括投入（本書）。S3-B2＝Expertの実キュー管理（複数ジョブのGET/SSE監視・取消・絞込み）。S5＝B7永続化・同一入力の独立再実行・日時＋実行ID出力名。
- **正本の優先順位:** コード（`src/utteran_gui/api.py`・`jobs.py`・`operation_queue.py`・`settings.py`、`frontend/src/**`）→ 本書 → [s3a](s3a-legacy-transcription.md) → [design-freeze](design-freeze.md)。

## 1. 目的と範囲

`--next-ui`の文字起こし画面で、**複数の入力ファイルを1つの一覧**に登録し、共通設定にファイル別の差分（override）と出力先を加え、
**旧`POST /api/jobs`を1件ずつ順番に呼ぶ**段階的な投入を行えるようにした。S3-Aの「1ファイルを投入→進捗→取消→結果」は同じ一覧の**現在のファイル（primary）**に対して従来どおり動く。

| 区分 | 内容 |
|---|---|
| **実装済み** | 入力一覧（picker＝1回1件の追加、native D&D＝複数件の追加、手入力＝明示の「追加」）、重複検知（クライアント内の補助判定）、上限100件、複数選択・すべて選択・解除・一覧から外す（表示のみ）、共通設定＋leaf override＋出力先（inherit/custom）、混在表示と明示の「適用」、全件preflight、投入時の凍結、single-flightの共通送信ゲート、受付/拒否/結果不明の区別と停止、受付サマリー、日英 |
| **後続（S3-B2）** | 複数ジョブの状態追跡（job ID別GET/SSE）、キューの絞込み・並べ替え・取消、表示整理と履歴の区別、結果Viewer |
| **後続（S5）** | B7の原子的batch・Idempotency-Key・durable snapshot、同一入力の独立再実行・retry/rerun、日時＋実行ID出力名と原子的予約、B9同期、既定GUI切替 |
| **設計変更（S3-A比）** | 入力はフォームから一覧へ移動（`TranscriptionFormController`は共通設定だけを持つ）。手入力は「追加」操作が必要。複数ファイルのドロップは拒否せず追加。5xx応答は「拒否」ではなく「結果不明」に分類（§4.1） |

**変更していないもの:** Python製品コード・API契約・DB・worker・CLI・旧`web/app.js`・既定GUI・CSP/`/launch`・`frontend/src/preview/**`。`/api/v1`は呼ばない。ホストのmulti-pickerは追加せず、既存の単一ファイルpickerを繰り返し呼ぶ。

## 2. 旧API契約（コードで確認、変更なし）

| route | B1での扱い |
|---|---|
| `POST /api/jobs`（202） | **1ファイル＝1回**。`TranscriptionPayload`は1ジョブ分だけ。受理前に`EnvironmentService.snapshot(profile)`で選択肢を再検査（409）。`JobManager.start()`がuuid4のjob IDを発行し`OperationQueue.submit()`（GUIプロセス内・直列・非永続）。**batch endpoint・idempotency keyは無い**（pytestで`/api/batch`が404/405であることも確認）。 |
| 受理時の副作用 | `settings.remember_directories(input_path, output_dir)`で既定入出力フォルダを書き換える。順次投入では**最後に受理されたファイルのフォルダ**が残る（pytest・E2Eで固定）。UIは隠さず、自動PATCHで戻さない。 |
| `GET /api/jobs/{id}` | S3-Aの1件詳細追跡だけが使う（状態の正本）。B1の受付一覧はjobごとのGETをしない（E2Eで0件を検査）。 |
| `GET /api/queue` | 参考情報。特定POSTの受付の証拠にしない。 |

## 3. データモデル

### 3.1 全体（1つの`AppCore`に1つずつ）

```
            ┌──────────────── AdmissionGate（domain/admission.ts）────────────────┐
            │ single-flight（同時POST≤1） / batch lease / ページ存続中の受付ledger │
            │  records[]: {seq, clientFileId, origin, snapshot(凍結), phase, jobId} │
            └──────────▲───────────────────────────────▲──────────────────────────┘
                       │ admit(request)                 │ admit(request, lease)
   JobController（S3-A、1件の詳細追跡）          BatchSubmitter（S3-B1、順次投入）
   GET/SSE/取消はこの1件だけ                     受付サマリーだけ（GETしない）
                       ▲                                ▲
                       └──── InputCollectionController ─┘
                             rows[]（clientFileId・inputPath・source・selected・
                             overrides・output・attemptFloor）、primaryId、staged
                             ＋ TranscriptionFormController（共通設定）
```

- **共通送信ゲート:** `POST /api/jobs`を出せるのは`AdmissionGate.admit()`だけ。S3-Aの`JobController.submit()`もここを通る（既存の`api.startJob`直接呼び出しを置換）。二重配線は無い。
- **行の受付状態はledgerから導出**し、行には保存しない（二重の正本を作らない）。`attemptFloor`は「拒否を未投入に戻す」明示操作のためだけの境界。
- S3-Aの`JobController`は従来どおり**1件だけ**を監視する。B1の順次投入で受理されたジョブはIDと受付時の要約を一覧に示すだけで、監視しない（S3-B2で実キューペインへ置換する前提で`BatchPanel`を分離）。

### 3.2 入力行（`domain/inputCollection.ts`、memoryのみ）

| 属性 | 意味 |
|---|---|
| `clientFileId` | ページ内で一意の安定ID（パスを含まない）。 |
| `inputPath` | 指定どおりの絶対パス（trim・正規化・結合をしない）。 |
| `source` | `picker` / `drop` / `typed`（表示用）。 |
| `selected` | Expertの選択集合（追加時は選択状態）。通常モードは`primaryId`の1件。 |
| `overrides` | 共通設定から変えたleafだけ（`asrBackend`/`asrModel`/`asrDevice`/`language`/`formats`）。 |
| `output` | `inherit`（共通の出力先＝S1の**保存済み**既定が初期値）/ `custom(path)`。処理設定leafとは別（B5 `FileInput.output_dir`と`SettingOverride`の分離に対応）。 |
| 受付状態 | `not_submitted` / `pending` / `accepted`（job ID）/ `rejected` / `unknown`。ledgerの最新試行から導出。 |

- **ロック:** `pending`/`accepted`/`unknown`の行は、一覧から外せず、overrideも編集されない（一括編集の対象から除外し件数を表示）。外す操作でガードを回避できない。
- **上限:** `MAX_INPUTS = 100`（B5 `FileInput`の最大1000未満。順次POSTの所要時間と一覧描画を抑えるため。定数で変更可能）。超える操作は**1件も追加せず**件数を示す（黙った切捨てなし）。
- **重複:** `samePath()`（Windows形式は大文字小文字・区切りを同一視）。シンボリックリンク・8.3名・別window・再読込後は判定できない（画面にも明記）。重複は新規行を作らず通知。
- **パス検証:** 既存`checkPath()`（空・長さ・制御文字・前後空白・相対・末尾区切り）。存在・形式・読取り・書込み可否はサーバーとCLIに委ねる（「検証済み」とは表示しない）。

### 3.3 設定の解決（`effectiveDraft`）

`共通設定 ← 行のoverride ← 行の出力先`。backendをoverrideした行は**ASR群を切り離す**: 行のbackendが共通backendと同じときだけmodel/deviceを継承し、異なるときに未指定のmodel/deviceは`missing`（推測で埋めない）。
backendのoverride適用時は旧backendのmodel/device overrideを消し、候補が1つだけの階層だけ埋める（S3-Aの`reconcile`と同じ規則）。
現在の環境結果に無い値は自動で置換せず`unavailable`として表示・preflightで停止。`formats`は1つ以上（空集合の適用は拒否、「共通に戻す」とは別操作）。

## 4. 段階的な一括投入（`domain/batchSubmission.ts`）

**トランザクションではない。** 「一括」＝この画面がN件を1件ずつ順番にPOSTする動作。B7のatomic batch・durable snapshot・replayは実装していない。

1. **対象の固定:** 選択中かつ`not_submitted`の行を一覧の順序で（並べ替えなし）。受理済み・結果不明は対象外。拒否行は明示の「未投入に戻す」後だけ。
2. **全件preflight:** 各行を`evaluateRow()`（S3-Aの`evaluate()`を再利用）で検査。現在の`GET /api/environment`の`jobOptions`だけを候補とし、未取得・読込み中・失敗・前回の結果では不可。ページ内ガード（受理/結果不明/送信中の入力）も検査。**1件でも問題があればPOST 0回**で、ファイルごとの理由を表示。
3. **凍結:** 各行の`TranscriptionRequest`を防御的コピーでfreezeしてからループ開始（出力先も解決済み）。ゲートは送信直前にもう一度freezeし、ledgerに記録。開始後に共通設定・行のoverride/出力先・サーバー側の既定フォルダ（`remember_directories`）が変わっても、残りのbodyは変わらない（Vitest・E2Eで検査）。
4. **送信:** ゲートのleaseを取得（他のPOSTが応答待ちなら開始しない＝`busy`）。1件ずつ`await`し、前件が確定するまで次を送らない。lease保持中は単独開始（S3-A）も拒否（`batch`）。

### 4.1 応答の分類（`classifyStartFailure`）

| 応答 | 行の状態 | 一括 |
|---|---|---|
| 202＋妥当なjob ID | `accepted`（**受付≠実行成功**） | 次へ |
| 受信した4xx（400/401/403/409/422/429等） | `rejected`（ジョブ未作成） | **既定で停止**。受理済み行はそのまま。拒否行は自動再送しない |
| 応答なし（network/timeout）、読めない2xx、**5xx**、クライアント例外 | `unknown`（開始された可能性） | **直ちに停止**。自動再送・自動照合・残りの自動投入なし。行はロック |

5xxを`unknown`にした根拠: `start_job`は`JobManager.start()`の後にも処理があり（`remember_directories`等）、想定外の例外は受理後の500になり得る。S3-Aの単件開始も同じ分類になった（従来の`rejected`より安全側）。

### 4.2 停止・再開・終了

- **停止:** 送信中のPOSTを**abortしない**。その応答の確定後、未送信分を`not_sent`にして止める。受理済みジョブは取り消さない（取消はS3-Aの1件詳細、またはS3-B2の別操作と表示）。
- **再開:** 改めて「投入」を押すと、その時点の選択中・`not_submitted`の行だけが対象。受理済み・結果不明は含まれない。
- **画面終了（`dispose`）:** 応答待ちの1件の結果は記録し、以降は送らない。
- **終了後:** 受理が1件以上なら`GET /api/settings`で設定を読み直す（S1の`rebase`で未保存の設定編集は保持）。PATCHはしない。

### 4.3 部分受付の例（Vitest・E2Eで固定）

| ケース | 受付済み | 受信拒否 | 結果不明 | 未投入 | POST |
|---|---|---|---|---|---|
| 3件すべて202 | 3 | 0 | 0 | 0 | 3（同時1） |
| 2件目が400 | 1 | 1 | 0 | 1 | 2（再開で+1） |
| 2件目の応答喪失（サーバーは受理） | 1 | 0 | 1 | 1 | 2（再開で+1、喪失行は再送なし） |
| 1件目送信中に停止 | 1 | 0 | 0 | 2 | 1 |
| preflightで1件不正 | 0 | 0 | 0 | — | 0 |

### 4.4 送信body（`transcriptionBody`、S3-Aと同一）

`input_path`・`output_dir`（解決済みの絶対パス）・`profile`（現在の環境結果の`active_profile`）・`asr_backend`/`asr_model`/`asr_device`（受信値のまま）・`language`・`formats`・`diarization_enabled=false`・`resume_mode="resume"`・`recursive=false`。

## 5. 画面

- **通常:** 入力（ファイル追加・パス入力＋追加・ドロップ案内・上限）→ 現在のファイル（複数あれば選択、状態チップ、個別設定ありの注記、一覧から外す）→ 件数の要約とExpertへの導線 → 共通設定 → S3-Aの実行（primaryの1件）→ 実行環境。順次投入中は要約に受付件数。
- **Expert（3ペイン、モード切替で状態保持）:** ①入力一覧（チェックボックスで複数選択、すべて選択/解除、選択を一覧から外す＝確認文で「ジョブ取消・成果物/履歴削除ではない」と明示、状態チップ・個別設定あり・設定に問題あり・job ID、拒否行の「未投入に戻す」）②選択中のファイルの設定（混在は「混在」表示、未適用の変更は「適用」まで反映しない、共通設定に戻す、出力先inherit/custom）＋共通設定＋実行環境 ③順次投入（受付）＋S3-Aの1件詳細。
- 部品はVuetify標準（`v-list`・`v-checkbox-btn`・`v-select`・`v-radio-group`・`v-alert`・`v-chip`・`v-btn`・`v-card`）。`InputAdd`/`InputList`/`InputOverrides`/`BatchPanel`/`RowStatusChip`に分割（各200行前後以下）。
- 文言は「受付済み」「受信拒否」「結果不明」「未投入」「送信中」を使い、「実行中」「完了」「成功」と表示しない。受付サマリーには「実行状態の正本ではない（S3-B2で対応、それまでは旧画面のキュー）」と常時表示。

## 6. 機密保持

- パス・設定・ledgerはJSメモリだけ（localStorage/sessionStorage/URL/host error reportへ出さない。Vitest・E2Eで検査）。
- 拒否の生`detail`（パスを含み得る）は`ApiError`内に留め、表示は固定コード/メッセージ（`ErrorAlert`、`HTTP 400`等）。E2Eで`synthetic-profile`が本文に出ないことを検査。
- E2E・Vitestのパスは合成値を実行時に組み立てる。

## 7. 試験（Linux、ローカル）

**Vitest: 42ファイル 432件成功**（S3-A時点400、+34新規、-2移設統合）。

| ファイル | 内容 |
|---|---|
| `domain/admission.test.ts`（7） | single-flight（誰が呼んでも同時1）、batch leaseで単独開始を拒否、4xx=拒否/5xx・読めない2xx・応答なし・クライアント例外=不明、送信中・受理・不明のガードと拒否の非ガード・Windows表記の同一視、凍結コピー（呼び出し側の変更が送信・記録に影響しない）、`JobController`が同じゲートを通る、単件の5xxは不明 |
| `domain/inputCollection.test.ts`（17） | picker 2回→2行・取消・host無し、複数drop・mixed（追加1/重複1/フォルダ1/不明1/不正1）・空drop、手入力の拒否理由と値の保持・Windows重複、上限超過は0件追加、fake hostのdrop購読（S3-Aから移設）、ID安定性と選択、受理/不明は外せない・再追加は重複・ガード維持、拒否→明示リセット、継承/override/解除、backend切替でmodel/device整合、環境から消えた値は置換せずunavailable、混在表示と選択だけへの一括適用、空formats拒否・選択変更で未適用破棄、出力先inherit/custom・不正パス拒否・設定PATCHなし、既定なしはブロック、ロック行は一括編集対象外、storage書込みなし |
| `domain/batchSubmission.test.ts`（10） | preflight失敗はPOST 0・ファイル別理由、未選択、3件受理（同時1、途中の共通設定/行/サーバー既定の変更でもbody不変、job ID別、afterRun）、2件目400（1/1/0/1・再開は未投入だけ）、2件目喪失（停止・再開で再送なし・ガード）、送信中の停止（abortなし・以降0件）、dispose、単件⇔一括の排他、A→B→A（行に紐付かない単件送信もパスでガード）、storage |
| 改訂: `transcription.test.ts`・`job.core.test.ts` | 入力を一覧へ移したためAPIを置換（§7.1） |

**Playwright E2E（実FastAPI、`npm run e2e`）: 50件成功**（UI-2 10＋S1 13＋S2 11＋S3-A 10＋**S3-B1 6**）。ハーネスは`tools/ui2/serve_next_ui.py`の**実`JobManager`/`OperationQueue`**＋合成文字起こしプロセス（モデル・ASR・ファイル書込みなし）。

1. 3件drop→共通設定→bだけmodel、cだけ出力先→混在表示→ダブルクリックでも順次3件、同時POST≤1、bodyは各ファイルの凍結値、job ID 3件が相異なり実キューにある、サーバー既定の出力先が最後の受理（c）のフォルダになる副作用、設定の再読込・PATCHなし、job別GETなし、受理行は外せず再投入不可、モード切替でPOSTなし、storage/URLにパスなし。
2. 2件目が400: 1/1/0/1、サーバー文言非表示、再開で未投入1件だけ送信、拒否行の除外（確認文）。
3. 2件目の応答喪失: S1/S3-Aと同じ方式（`route.fetch()`→`abort()`は使わない。別`APIRequestContext`で実FastAPIへ1回送り202・job IDを確認してからブラウザ側を1回abort）。不明で停止、3秒待っても再送なし、再開で未投入だけ、不明行は外せない、サーバーのジョブは実在。
4. 1件目のPOSTを保留中に停止: 保留中に通常モードで単独開始が無効、停止はabortせず応答後に以降0件、取消要求なし。次の投入は変更後の言語で凍結。
5. 1件目保留中に共通の言語・形式と2件目の出力先を変更しても、両bodyは開始時の値。
6. 追加: drop（ファイル1・フォルダ1・不明1）の内訳表示、Windows表記の重複、上限表示、相対パスは追加不可、Enterで追加。

**反復:** S3-A＋S3-B1 specを3回反復、S3-B1の応答喪失・停止の2件を10回反復（結果は§12・PRに記録）。

**pytest:** `tests/test_ui3_s3a_job_harness.py`に1件追加（順次POSTは別ジョブ・拒否はジョブを作らずフォルダも記録しない・既定フォルダは最後の受理のもの・batch routeは無い）。

**プロトタイプE2E: 96件成功（不変）。** lint・typecheck・build・`audit:prod`（0件）成功。

### 7.1 既存試験の改訂（削除・skipなし）

| 試験 | before | after | 根拠 |
|---|---|---|---|
| `transcription.test.ts`（S3-A form） | `form.edit({inputPath})`・`pick("input_file")`・`acceptDrop`（複数は全件拒否） | 共通draftに入力を与えて`evaluate`、出力先pickerだけを検査。入力系はinputCollection.test.tsへ移設（複数dropは追加） | 指示書§3.2: pickerは追加、D&Dは複数件を1操作で追加 |
| `job.core.test.ts` | `form.pick("input_file")`→`form.evaluation().request` | `core.inputs.pick()`→`evaluateRow(primary)`、一覧storeもモード切替で同一 | 入力の正本を一覧へ統合（§1.1） |
| `transcription-s3a.spec.ts` | パス欄に入力するだけで主入力、複数dropは「どれも入力にしません」 | パス入力＋「追加」で現在のファイル、複数dropは2件追加・フォルダは件数表示、同一入力は一覧の選択で再現 | 指示書§3.2（手入力は別の追加操作、D&D複数追加） |
| `next-ui.spec.ts`「mode switch」 | ③のボタン1個（単独開始） | ③のボタン2個（順次投入・単独開始、入力なしでは両方無効） | ③に受付サマリーを配置（指示書§6） |
| `job.test.ts` | 変更なし（503を含むケースはstatus 409で構成されており分類変更の影響なし） | — | — |

## 8. 受入残件

- **Windows WebView2実機（最終統合ゲート、未実施）:** native pickerの連続追加、Explorerから複数ファイル・フォルダ混在のドロップ、日本語・長いパスの一覧表示、DPI 125/150/200%、実GPU・実モデルでの順次投入と取消、IME。
- 実モデルでのASR・GPUは自動試験の対象外（合成プロセス）。**合成CLIでの成功は実モデル動作の保証ではない。**

## 9. 後続への引継ぎ

**S3-B2（Expert実キュー）:** 受理済み各job IDのGET/SSE監視（S3-Aの`JobController`を複数化するのではなく、ledgerの`accepted`行を入力に追跡storeを別に設計）、並び・絞込み、取消、表示整理と履歴の区別、通常/Expert整合、ページ再読込・複数windowでの再接続。旧APIが対応しない保留・並べ替え・永続化を捏造しない。`BatchPanel`の受付サマリーを実キューペインへ置換。

**S5:** B7公開API/ready、永続キュー、atomic batch/Idempotency-Key（応答不明の照合）、同一入力の独立ジョブ履歴（backend権威の同一性判定。現在のページ内ガードを置換）、原子的な日時＋実行ID命名（案`<入力stem>_<YYYYMMDD-HHMMSS>_<短い実行識別子>.<拡張子>`、**文書の案のみ**。現行`_available_stem()`の連番を維持、完了後renameは禁止）、snapshot保持/削除、B9のepoch/cursor、旧Queueとの排他切替、G-01/G-05/G-06/G-15。

**その他:** フォルダ入力・再帰・glob、話者分離（トークン・同意）、Prompt/用語集とIME、B5保存プリセット/Policy、永続下書き。

## 10. Windows

各PRはLinux自動試験＋Windows CI（`UI-3 frontend Windows`・`Windows pytest (no models)`・`Windows PowerShell 5.1 parse and headless startup`）で判断する。追加したpytestはWindowsでも実`JobManager`＋合成プロセスを通す。自動CIの成功を実機受入とは書かない。

## 11. 版

`0.1.46`→`0.1.47`（`pyproject.toml`・`uv.lock`のutteran行・README日英・`変更履歴.md`）。`frontend/src/preview/`の合成版数は変更しない。

## 12. 検証記録

PR本文とAISTATEに、CI run URL・HEAD・ジョブ数・反復試験の結果を記録する。
