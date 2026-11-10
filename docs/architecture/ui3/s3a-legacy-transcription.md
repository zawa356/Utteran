# UI-3 S3-A — 本番文字起こしの基本フロー（旧`/api/jobs`、1ファイル、`/next/`、opt-in）

- **作成:** 2026-11-09 JST。対象 [Issue #21](https://github.com/zawa356/Utteran/issues/21)（親 [Epic #18](https://github.com/zawa356/Utteran/issues/18)）。Issue #21はS3-B/S4/S5が未完のためopenのまま。
- **基点:** `main` `79fa6924ec2d7e7e3b15a96dbe5c89e5b8379866`（PR #41 / S2のマージコミット。作業直前に`origin/main`と一致、作業ツリー無変更、open PRは無関係の#23のみ）。
- **版:** 開発版 `0.1.46`（未リリース、tag/releaseなし）。§11。
- **状態:** Linux（Vitest＋headless Chromium＋実FastAPI）とpytestの自動試験まで。WindowsはCIで判断し、**Windows WebView2実機（native picker・D&D・IME・DPI）の手動確認は最終統合ゲートへ集約**（§10）。
- **改訂:** 2026-11-10 PR #42レビュー（P2×2）を反映（§12）。§4.4・§9も更新。
- **正本の優先順位:** コード（`src/utteran_gui/api.py`・`jobs.py`・`operation_queue.py`・`environment.py`・`cli.py`、`frontend/src/**`）→ 本書 → [design-freeze](design-freeze.md)。

## 1. 目的と範囲

通常モードで**ローカル音声ファイル1件**を指定し、**現在の**環境検出結果からASR構成を選び、旧GUIと同じ実FastAPIジョブAPIへ投入して、進捗・取消・終了状態を確認できるようにした。モード切替やページ移動で実行状態を失わない。

| S3-Aで実装 | S3-Aで実装しない（S3-B以降） |
|---|---|
| 入力1ファイル（`HostBridge.pickPath("input_file")`・native D&D・手入力）、出力先（保存済み既定・`pickPath("output_folder")`・手入力） | フォルダ入力・複数入力・batch glob（include/exclude）・再帰 |
| 現在の`GET /api/environment`結果からbackend→model→device、言語（`auto`/`ja`/`en`）、出力形式（5種、1件以上） | 話者分離の選択・同意・トークン導線（S3-Aは常に`diarization_enabled=false`）、話者数、resume方式の選択、Prompt/用語集とIME |
| `POST /api/jobs`（1回だけ送信）、`GET /api/jobs/{id}`を状態の正本、SSEは速報、bounded polling、`POST /api/jobs/{id}/cancel` | ファイル別override、Expertの複数ジョブキュー、同一入力の再投入・retry・rerun・submitAgain、出力命名の変更 |
| 結果不明（POST応答喪失）の表示と`GET /api/queue`の参考表示、表示上の片付け | 結果viewer・履歴・再出力（S4）、B5〜B9のv1接続（S5） |

**変更しないもの:** Python製品コード（`src/**`）、API・DB・worker・CLI、旧`web/app.js`と既定旧GUI、既定CSP・`/launch`、`/api/v1`（呼ばない）、`frontend/src/preview/**`（importしない）。

## 2. 対象route（コードで確認した現行契約）

| route | 本書での扱い |
|---|---|
| `POST /api/jobs`（202） | `TranscriptionPayload`。サーバーは**最初に`EnvironmentService.snapshot(profile)`を同期実行**して`_validate_dynamic_selection()`でbackend/model/deviceを再検査（不一致は409）。`CliError`は400、pydanticは422、`JobBusyError`/v1が実行枠を保持中は409。受理時に`settings.remember_directories()`が**既定の入力/出力フォルダを書き換える**（旧APIの既存の副作用。新UIはPATCHしないが、受理後の設定値は変わる。画面に明記） |
| `GET /api/jobs/{id}` | `JobManager.snapshot()`: `id`（uuid4 hex）・`status`（`starting`/`running`/`completed`/`failed`/`cancelled`）・時刻・`exit_code`・`stalled`・`outputs`（パス）・`events`（全件）・`logs`（生stdout/stderr）・`guidance`。**状態の正本** |
| `POST /api/jobs/{id}/cancel`（202） | `OperationQueue.cancel()`→待機中なら即`cancelled`、実行中ならプロセスツリーをkill。**202は要求受理**。繰り返しても安全（終端なら現状を返す） |
| `GET /api/jobs/{id}/events`（SSE） | 0.5秒周期、`id: n`付きだが`Last-Event-ID`非対応で**再接続するとcursor 0から全件を再送**、終端かつ新規なしで切断。`done`は`JobManager._finish()`が合成することもある |
| `GET /api/queue` | 旧operation queue（プロセス内メモリ、直列実行、転写とモデル操作で共有）。`label`は入力ファイル名 |

**`starting`の意味:** キューで順番待ちの間もジョブは`starting`（`QueueItem.status=waiting`）。画面では「開始待ち（キューで順番を待っている場合を含みます）」と表示し、推測の順番は出さない。

## 3. DTO（`frontend/src/api/legacyJobs.ts`、送信用投影は`legacyEnvironment.ts`）

| 実レスポンス | DTO | 扱い |
|---|---|---|
| `id` | `id` | `^[0-9a-f]{32}$`以外・要求したIDと違う応答は全体`malformed`（URLへ入れない） |
| `status` | `status` | 5値以外は全体`malformed`（**適用しない**。直前の状態を維持し、ポーリング失敗として表示） |
| `created_at`/`started_at`/`finished_at`、`exit_code`、`stalled` | 同名 | ISO形式以外の時刻は`null` |
| `outputs[]` | `outputCount`、`outputNames`（**ファイル名だけ**、最大16件、制御文字除去） | フォルダは保持しない。HTML風の名前も文字として表示 |
| `events[]` | `progress {stage, ratio}`、`lastEventId` | 最新の`stage_start`/`progress`/`stage_done`だけ。stageは旧GUIの5種＋`other`。ratioはその工程の値だけ（**全体%を推測しない**）。`message`・`error_type`・`output_written.path`は保持しない |
| `guidance.key` | `guidance`（既知12種＋`other`） | 固定文言へ。`settings_anchor`は保持しない |
| `logs[]` | 保持しない | 生stdout/stderr（転写文・パスを含み得る） |
| `GET /api/queue` | `{id, transcription, status, position}` | `label`（ファイル名）は保持しない |

**送信用の環境投影（`LegacyEnvironment.jobOptions`）:** S2の表示用DTOは件数32件上限・短いID形式で絞っており、**whisper.cppのカタログは33件**、pyannoteのIDは`/`を含むため、送信に使うと有効なIDが欠落し得る。そこで`options.asr`から別投影を作った: 送信する値は**受信した文字列そのまま**（空・前後空白・制御/bidi文字・`TranscriptionPayload`の長さ上限超えは除外し、書き換えない）、上限256件、壊れた要素や上限超過は`incomplete`として注記。モデルまたはデバイスが空のbackendは出さない。言語と形式はサーバーの一覧と既知値の積。表示ラベルと送信IDは分離。

**送信body（`transcriptionBody()`）:** `input_path`・`output_dir`・`profile`・`asr_backend`・`asr_model`・`asr_device`・`diarization_enabled: false`・`language`・`formats`・`resume_mode: "resume"`・`recursive: false`。include/exclude・話者数・話者分離の各値は送らない（APIの既定のまま）。`profile`は**現在の環境結果の`active_profile`**（サーバーが候補を作ったprofile）。

## 4. 状態遷移

### 4.1 フォーム（`domain/transcription.ts` `TranscriptionFormController`）

- `AppCore`で1つだけ生成し、通常とExpertが同じ参照を読む。メモリ内だけ（`localStorage`/`sessionStorage`/URLに書かない、Vitest/E2Eで検査）。
- **候補は現在の環境結果だけ**: 環境が未取得／読み込み中／失敗／前回の結果（`isCurrentResult`が偽）／profile未決定の間は候補を出さず、理由を表示（静的な補完なし）。
- 新しい環境結果に無い選択は外す。**選択肢がちょうど1つの階層だけ**自動で埋める（唯一の適法値であり推奨ではない）。backendを変えるとmodel/deviceを外す。
- 出力先: S1の**保存済み**既定（draftではない）を、未編集の間に1度だけ初期値に使う。既定が無ければ空のまま（**入力と同じフォルダを自動で組み立てない**: Windowsのドライブ/UNC/区切りの差で曖昧なパスを作らないため）。
- パス検査（`domain/paths.ts`）: 空・長さ（32768）・制御文字・前後空白（自動で除去しない）・相対パス（サーバーの作業ディレクトリ基準になるため）・入力の末尾区切りを拒否。ドライブ／UNC／POSIXの絶対パスのみ。存在・権限は確約しない（サーバーとCLIが判断）。
- D&D: ちょうど1件の`file`だけ受理。複数は件数を示して**全体を拒否**（先頭だけを黙って使わない）、`folder`・`unknown`も拒否、同じ通知の重複は無変化。購読は入力欄が画面にある間だけ（`onMounted`/`onUnmounted`）。
- 実行中もフォームは編集できる（次の投入用）。実行中のジョブは§4.3の凍結コピーで動作中である旨を表示。

### 4.2 ジョブ（`domain/job.ts` `JobController`）

```
idle ──submit──▶ pending ──202──▶ accepted + tracking=active ──GET終端──▶ tracking=terminal
                    │                         │                      └─GET 404──▶ tracking=lost
                    ├─応答あり拒否─▶ rejected  └─(SSE: 速報・早めのGET要求のみ)
                    └─応答喪失────▶ unknown（自動再送なし）
```

- **二重送信抑止:** `pending`/`active`中は送らない（`busy`）。開始ボタンも無効。
- **非冪等POST:** 自動再送しない。timeout/network/応答読取り失敗は`outcomeUnknown`→`unknown`。「開始されたかどうか不明です」と表示し、確認（了解）するまで新規投入不可。**`GET /api/queue`は参考情報**（受付・実行中の文字起こし件数）で、特定のPOSTの成否は証明できない（要求の識別子が無く、別画面・旧GUIの投入と区別できない）。
- **受信した拒否（400/409/422/503等）:** ジョブは作成されていない。同じ内容を利用者が明示的に再送できる。409は「開始時の再確認で候補から外れた／別の実行系が実行枠を使用中」等、400/422は入力またはprofileの拒否として固定文言。サーバーの`detail`（パスを含み得る）は表示しない（コードのみ）。
- **状態の正本:** `GET /api/jobs/{id}`。`SingleFlightPoller`（同時1件、2秒間隔、失敗時2→30秒のbackoff、失敗回数を表示）。終端で停止。404は「サーバーがこのジョブを認識していない（再起動など）＝結果不明」。
- **SSE（速報）:** 許可したevent名だけ読み、`events[].id`で重複・再送（cursor 0からの再生）を捨てる。`done`/`error`は**早めのGETを要求するだけ**で結果を決めない。`onerror`で**自ら閉じる**（ブラウザの自動再接続に任せない: 終端後に切断→再接続→全件再送のループになるため）。その後のGET成功時に再接続、1ジョブ最大3回。EventSourceが無い環境はpollingのみ。
- **取消:** 202で「取消を要求しました」、GETが`cancelled`を返すまで「取消済み」と表示しない。失敗は表示しボタンを戻す（D-06）。応答喪失は「届いたか不明」、利用者は明示的に再要求できる（サーバー側は冪等）。取消要求後に`completed`/`failed`になったら「取消が間に合わず終了」。
- **古い応答の排除:** 追跡ごとの世代番号。前のジョブの遅いGET/取消応答/SSEは次のジョブの状態・poller・SSEに触れない（D-04）。終端遷移は1回だけ（D-03）。
- **片付け:** 終端・拒否・不明の表示を画面から外すだけ。サーバーのジョブ・成果物・履歴は削除しない。
- **cleanup:** `AppCore.dispose()`（`pagehide`）でpollerとSSEを止める。サーバーのジョブは継続する。

### 4.3 投入時の凍結コピー

送信したbody（と表示用の要求）を`Object.freeze`したコピーとして**メモリ内だけ**に保持し、「この実行は開始時の内容で動作します。後から設定を変えても反映されません」と説明する。**B7の不変・永続JobSnapshotではない**。ブラウザストレージ・永続stateへは書かない。

### 4.4 同じ入力の再投入（S3-Aでは提供しない）

このページでこれまでに受理（または結果不明）となった**すべての**入力と同じパス（Windows風のパスは大文字小文字・区切りを無視、POSIXは完全一致）は投入不可とし、旧画面への導線を出す。理由: coreの`src/utteran/jobs.py::job_id_from_input_hash()`は入力fingerprintからcore job IDを作るため、出力先が違っても保存履歴・stage cacheが独立した新しい実行になるとは限らない（S3-Bの設計ゲート）。表示の片付けでは解除しない（A→B→Aも拒否、§12）。メモリ内の判定でありページ再読込で消え、別window・別クライアント・旧GUIからの投入は対象外。シンボリックリンク・8.3形式・`..`は解決しない。受信した拒否の後は再送可能。

## 5. 画面

| 部品 | 内容 |
|---|---|
| `features/transcribe/TranscribeInput.vue` | 選択・外す・パス入力（「アップロードではなく同じPC上の絶対パス」）、指定方法、D&Dの説明と拒否理由、ブラウザでpickerが使えない説明 |
| `TranscribeOptions.vue` | 環境結果が使えない理由、使用profile、「候補は検出結果から。動くかは開始時にサーバーと処理が確認」、backend/model/device/言語（`VSelect`）、形式（`VCheckbox`）、話者分離オフの明示、resume既定、出力先と由来、存在・権限は未確認、`_1`連番、受理時のフォルダ記録 |
| `TranscribeJob.vue` | 未充足項目、投入不可の理由、開始、送信中の経過秒と上限240秒、拒否／不明（参考キュー・旧画面・了解）、状態・ジョブID・工程・停滞・取得失敗・速報の状態、取消、終端の案内・終了コード・出力ファイル名・保存先・「出力先フォルダを開く」（既存`POST /api/open-folder`）・旧画面・片付け、凍結コピーの説明、非永続の説明 |
| 通常 | 入力→処理設定→実行→S2実行環境 |
| Expert | ①入力（同じ1ファイル）、②処理設定＋S2詳細、③実行。複数入力・キューはS3-Bと明記 |

「ready」「推奨」「確実に動く」とは表示しない。出力ファイル名は**サーバーが報告した名前だけ**を表示し、捏造しない。

**native D&Dの受け口:** `utteran_gui/app.py`は`--next-ui`のとき文書の読込みごとに`#input-drop-zone`を1回だけ結合し、要素が無ければ何もしない。新UIは`frontend/index.html`に**静的な`#input-drop-zone`**（`#app`の外側）を置き、Vue描画前から存在して再描画で置き換わらないようにした（Python無変更）。ドロップは画面全体で受け、入力欄が表示されている間だけフォームへ渡す。ブラウザ表示では入力欄上のdrop既定動作（ファイルへの遷移）を抑止する。

## 6. 制約（文書化した契約上の限界）

- **非冪等POST:** サーバー側idempotency keyが無い。応答喪失時は結果不明として扱い、`/api/queue`でも証明できない。
- **POSTの待ち時間:** サーバーが受付前に環境を再取得するため、`GET /api/environment`と同じ240秒をclient上限とした（経過秒を表示）。
- **legacy job/queueは非永続:** GUIプロセス内メモリだけ。アプリ再起動で`GET /api/jobs/{id}`は404になり、再開は保証しない（画面に明記）。B7のqueue revision・durable stateと同一視しない。
- **core job IDは入力fingerprint由来:** 旧`JobManager`のIDは実行ごとに異なるが、coreの履歴・cacheは共有され得る（§4.4）。
- **結果と履歴の区別:** S3-Aは旧`JobManager`のジョブ状態だけを表示する。core履歴の閲覧・削除・再出力はS4。片付けは表示だけ。
- **受理時の既定フォルダ更新:** `remember_directories()`は旧APIの副作用（§2）。新UIは設定をPATCHしない。
- **出力命名:** 既存`src/utteran/exporters/__init__.py::_available_stem()`の連番回避（`basename_1.ext`）のまま。実行IDごとの予約・排他保証ではない。

## 7. 試験

**Vitest（`npm test`）: 39ファイル・398件成功**（S2の353件に45件追加。本番254、プロトタイプ144は不変）。

| ファイル | 内容 |
|---|---|
| `api/legacyJobs.test.ts`（16件） | snapshotの許可リスト投影（logs・message・パス・トークン風文字列を保持しない）、ID不一致・未知status・壊れたlistで`malformed`、未知guidance/stageは`other`、HTML風の名前は文字、全体%を作らない、SSE eventの許可リスト・bad JSON、eventsパスのID検証、queueのlabel破棄、送信body（話者分離オフ）、POST 1回・400/409/422/503の分類と非再送・応答喪失は結果不明・202の不正JSONは`malformed`、GET abort、実service fixtureからの送信投影・33件・`/`入りID・不正IDの除外と`incomplete` |
| `domain/paths.test.ts`（3件） | ドライブ/UNC/POSIX、空・相対・空白・制御文字・長さ・末尾区切り、同一パス判定 |
| `domain/transcription.test.ts`（11件） | 未取得/読み込み中/失敗/前回/profileなしで候補なし、単一候補だけ自動、完全な要求、環境変化で古いIDを外す、backend変更、形式1件以上、保存済み既定の1回だけ初期化・draft不使用、picker成功/取消/hostなし、D&D単数/複数/フォルダ/unknown/空/重複、購読解除、ブラウザストレージ不使用、dispose |
| `domain/job.test.ts`（14件） | ダブルクリックで1回、受信した拒否は作成なし・非再送、応答喪失は不明・新規投入不可・同一入力ブロック・参考キュー、SSE `done`だけでは成功にしない、再送/重複/未知eventの無視、SSE切断→pollingと再接続上限3、EventSourceなし、取得失敗の可視化とbackoff・未知statusを適用しない、404→lost、取消の要求≠取消済み・失敗表示・再要求、取消が間に合わない、**D-04**（前ジョブの遅いGETが次ジョブに触れない）、片付け・disposeでtimer/stream 0、即時終端 |
| `domain/job.core.test.ts`（1件） | `AppCore`経由: 保存済み既定の出力先、1回の投入、通常⇔Expert・設定画面への移動でフォームと追跡を保持、非GETは`POST /api/jobs`だけ（設定PATCHなし）、`/api/v1`なし、再検出なし、dispose後timer 0 |

**Playwright E2E（実FastAPI、`npm run e2e`）: 44件成功**（UI-2 10＋S1 13＋S2 11＋**S3-A 10**）。ハーネス`tools/ui2/serve_next_ui.py`に**実`JobManager`/`OperationQueue`**を注入し、CLIだけ合成プロセス`tools/ui2/synthetic_transcribe.py`（モデルDL・ASR・ファイル書込みなし、入力名でシナリオ）に置換。

1. 通常: 環境前は候補なし→cuda・入力・出力・model選択→ダブルクリックでも`POST /api/jobs`1回→実行中の編集で凍結コピー不変→完了・3ファイル名、bodyの話者分離オフ等、生ログ非表示、ブラウザストレージ・URLにパスなし、非GETは投入だけ・`/api/v1`なし、同一入力ブロックと片付け。
2. 保存済み既定の出力先が初期値、mdを外した形式で送信。
3. 失敗の固定guidanceと終了コード、`done`(exit 0)後にexit 3は「失敗」。
4. 取消: 実行中に通常→Expert→設定→戻るで追跡維持、取消要求は1回、GETで「取消済み」。
5. 400拒否: ジョブなし、サーバーのパス入りdetailを表示しない、再送可能。
6. POST応答喪失（実サーバーは受理）: 不明・自動再送なし（3秒待ってもPOST 1回）・参考キュー1件・了解後も同一入力ブロック。
7. SSE遮断: pollingだけで完了、`/events`要求は1〜3回。
8. native drop（pywebviewのイベント）: 複数・フォルダ拒否、1件受理、`#input-drop-zone`内に`#app`、設定画面でのdropは無視、ブラウザでpicker不可の説明。
9. 合成`window.pywebview.api`でpickerの選択・取消。
10. 相対パスは送信前に拒否、HTML風のファイル名は文字のまま（`img`要素0）。

S3-A specを3回反復して30/30成功。各試験の後に旧キューの未終了項目を取消す（直列キューの持越し防止）。

**既存E2Eの改訂（1件、削除・skipなし）:** `next-ui.spec.ts`「mode switch…」の「Expertのキューペインにボタンが0個」を、「ボタンは共有の開始ボタン1個だけで、入力未指定なら無効」へ（S3-Aで意図的に開始ボタンを置いたため）。

**pytest:** `tests/test_ui3_s3a_job_harness.py`（6件）— ハーネスが実ルートを通すこと（202は非終端・GETで完了・生logsはAPIにある＝UIで捨てる対象、`done`だけでは成功でない、guidance、取消は要求→GETで`cancelled`・再取消は無害、409/400/422/404でジョブなし、受理時の既定フォルダ記録）。

**プロトタイプE2E: 96件成功（不変）。** lint・typecheck・build・`audit:prod`（0件）成功。

## 8. 受入残件

- **Windows WebView2実機（最終統合ゲート、未実施）:** native file picker・`#input-drop-zone`へのExplorerからのD&D（文書読込み時の結合タイミングを含む）・日本語パス・長いパスの折返し・DPI 125/150/200%・実GPUでの実ジョブ（実モデル）と取消・アプリ終了時の子プロセス終了。
- **IME:** S3-Aには自由入力欄（Prompt/用語集）が無い。パス欄の日本語入力を含めWindows実機IMEは未検証。
- 実モデルでのASR実行・GPUは自動試験の対象外（合成プロセス）。

## 9. S3-Bへの引継ぎ（明示的な未決ゲート）

1. **出力命名:** 案`<入力stem>_<YYYYMMDD-HHMMSS>_<短い実行識別子>.<拡張子>`（例`meeting_20261109-143025_a8f2c1d4.srt`）。正式実装前にtimezone/時刻形式、既存ファイル・同時出力の原子的予約、backend exporterとcore manifest・履歴参照の整合、Windowsの禁止文字・長さ、再実行時の挙動を検証。**GUIが完了後にrenameする方式は禁止**。
2. **同一入力:** core job ID（入力hash）とhistories/stage cacheの共有。独立履歴を保証できるまで再投入を正式解放しない。S3-Aのfrontend判定はページ内メモリだけで、再読込・別window/別クライアント・symlink/8.3名/`..`・大文字小文字を区別するWindowsフォルダ等の完全な同一性を保証できない。**正式な同一入力ルールはbackendを権威（authority）とする**ことをS3-Bの要件とする。
3. **独立した再実行の不変snapshot:** retry（UI-3-0は同Job ID/attempt+1）とB7（新Job ID＋元snapshotのコピー）の意味の違いを隠さない。旧APIに同義のretry endpointは無い。
4. **出力衝突の排他**（同時ジョブ・同名出力）。
5. **キュー表示の片付けは表示のみ**（履歴・成果物を削除しない）。
6. **未解決のowner判断:** G-01（B4 readiness）、G-05/G-06（作業途中の入力・override・状態の保存先）、G-15（保持期間・ACL・消去・backup）。
7. **S5:** B7（Idempotency-Key・durable job/queue）とB9（epoch/cursor・`/sync`）の契約で、非冪等POSTと非永続queueの限界を置き換える。
8. 複数window・ページ再読込時の再接続（現在は追跡を失う。サーバーのジョブは継続）。
9. 話者分離（トークン・利用条件の同意）・話者数・resume方式・include/exclude・フォルダ入力・Prompt/用語集とIME。

## 10. Windows（自動CIで判断、手動実機は最終ゲート）

各PRはLinux自動試験＋Windows CI（`UI-3 frontend Windows (npm ci, lint, types, unit, build)`・`Windows pytest (no models)`・`Windows PowerShell 5.1 parse and headless startup`）の成功で判断する。`tests/test_ui3_s3a_job_harness.py`はWindows pytestでも実`JobManager`＋合成プロセス（`sys.executable`）の起動・取消（`taskkill /T`）を通す。自動CIの成功を実機受入とは書かない。

## 11. 版

`0.1.45`→`0.1.46`（`pyproject.toml`・`uv.lock`のutteran行・README日英・`変更履歴.md`）。`frontend/src/preview/`の合成版数は変更しない。tag・releaseなし。

## 12. PR #42レビュー修正（2026-11-10）

基点HEAD `cb91b9b`。Python製品コード・API・DB・CLI・旧GUI・`frontend/src/preview/**`は無変更。版は`0.1.46`据え置き（同じ未リリース作業単位のレビュー修正）。

| 指摘 | 原因 | 修正 | 回帰試験 |
|---|---|---|---|
| **P2-1:** 同一入力の再投入禁止が直近の1件しか守らない（A受理→終端→B受理→終端→Aを再POSTできる） | `JobController.lastInput`が単一値で、次の受理で上書きされていた | 受理済み・結果不明の入力を**ページの寿命の間すべて**保持（`sentInputs`、`samePath`で重複排除）。表示の片付けでは解除しない。受信した拒否は対象外のまま | Vitest 2件: A→終端→片付け→B→終端→片付け→Aは拒否（POST 2回だけ）／結果不明A→B→A、Windows表記の同等パス（大文字小文字・区切り）で拒否、受信拒否のCは再送可。**修正前のコードでは2件とも失敗**することを確認 |
| **P2-2:** 「lost POST answer」E2Eが1つのroute handler内で`route.fetch()`→`route.abort()`（PR #41で`Route is already handled!`の間欠失敗を起こした型） | S1で直した方式を再利用していなかった | S1と同じ方式へ: 傍受したPOSTのbodyを**別の`APIRequestContext`**で実FastAPIへ1回送り、ステータスとjob IDを記録してから、ブラウザのRouteは`abort("connectionreset")`だけで**1回だけ**完了。`route.fetch()`は使わない | サーバー側POSTが**202で1回**・job IDが実在し`starting/running`、ブラウザ側は結果不明・3秒待っても再送なし（ブラウザPOST 1回・handler 1回）、参考キュー1件、同一入力ブロックを検査。ローカルで20回反復20/20 |

**試験数（修正後、Linux）:** Vitest 39ファイル・**400件**（+2）、E2E 44件（S3-Aの1件を改修）、プロトタイプE2E 96件。§7の数値は修正前の記録として残す。
