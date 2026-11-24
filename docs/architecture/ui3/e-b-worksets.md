# 束 E-B — 名前付き作業セットの自動保存・復元と容量上限

開発専用 `--next-ui-v1` のみ。対象は [#80](https://github.com/zawa356/Utteran/issues/80)（親 [束 E #77](https://github.com/zawa356/Utteran/issues/77)）。開発版 `0.1.68`、未リリース。
本書は E-B の正本。挙動の正本はコード（`src/utteran_gui/worksets_*.py`、`frontend/src/domain/v1/worksets*.ts`、`frontend/src/api/worksets.ts`）と offline schema [`e-b-worksets.schema.json`](e-b-worksets.schema.json)。

既定の旧 GUI、旧 `--next-ui`、CLI、B5/B6/B7 の契約・DB は変更していない（§9）。

## 1. 確定した UX

| 項目 | 実装 |
|---|---|
| 初回 | 作業セット 0 件のとき、`POST /worksets/bootstrap` が保存先を作成し、既定名の作業セットを **1 件だけ**作る（保存済みの言語で「作業セット1」／「Workset 1」）。複数ウィンドウ・複数プロセスの同時初期化でも 1 件（`BEGIN IMMEDIATE` と O_EXCL 作成者プロトコル） |
| 操作 | Normal と Expert の文字起こし画面上部に同じ作業セット欄（`WorksetBar`）。選択（切替）、新規、複製、名前変更、以前の保存状態に戻す、削除 |
| 上限 | 作業セット **20 件**。上限時は新規・複製を明示拒否し、何も削除しない（`workset_limit` 409／画面の `limit`） |
| 起動時 | 最後にアクティブだった作業セットと表示モード（Normal／Expert）を復元する。復元はジョブ投入、キュー操作、再実行、モデル取得を一切行わない |
| 保存 | 自動保存。保存状態の表示はサーバーが確定した結果だけ（保存済み／未保存の変更あり／保存中／保存できていません／保存結果を確認中／競合）。見かけの「保存済み」は出さない |
| 切替 | 先に保存を確定する（debounce を取り消して即時保存）。保存失敗・結果不明・競合・v1 投入の確認／送信中（結果不明を含む）は切替を拒否する。入力途中の欄や未適用の一括変更がある場合は破棄の確認を出す |
| 削除 | 確認ダイアログ必須。削除するのは作業セットとその内部の復旧用保存状態（最大 2 件）だけ。元音声・出力・ジョブ・キュー・B7 履歴は削除しない（UI に明記）。最後の 1 件は削除できない（`workset_last`） |
| 欠損入力 | 復元時と「入力を再確認」で存在を照会し、見つからない／ファイルでない行は一覧に残したまま表示（`見つかりません`）。修正または一覧からの削除まで v1 の投入を止める |
| 欠損参照 | Preset／Policy の ID と revision をそのまま保持する。B5 は tombstone 後も過去 revision を GET できるので、削除済み Preset の旧 revision は凍結されたまま「一覧にない」と表示される。revision 自体が読めない場合も参照を残し、エラーを表示する。最新 revision への自動切替はしない。選び直すまで投入不可 |

## 2. 保存するもの・しないもの（明示プロジェクション）

`frontend/src/domain/v1/worksetProjection.ts` の `project()` が、実行時ストアから次の値だけを選んで直列化する。`JSON.stringify(core)` のような実行時状態の丸ごと保存はしない。

**保存する:** 入力行の順序、パス、追加元、選択、出力先（継承／個別）、適用済みの行別 override（ASR backend・model・device・language・formats・用語集）、primary 行、Preset／Policy の `{id, revision}`、共通の差分（language／formats。`null` は Preset 継承、`[]` は明示空）、共通の出力先。

- 行別の用語集は「継承（キーなし）」と「明示的な空配列」を区別する。同一の用語集は `glossaries` に 1 回だけ保存し、行は初出順の番号で参照する（正規形なので同じ状態は同じバイト列）。
- 保存済みの既定出力先に従っているだけの共通出力先は空として保存し、復元時にその時点の既定値へ追従する。
- 表示モードは作業セット本体ではなく、アクティブ参照（`active.view.mode`）に保存する。作業セットの切替では現在の表示モードを維持し、起動時だけ復元する（切替のたびに画面構成が変わらないようにするため）。

**保存しない:** 入力欄の入力途中、未適用の一括変更（stage）、用語集・Preset のダイアログ下書き、Preset の作成／改訂／削除の確認途中、validation／resolution の結果と token、Idempotency-Key と結果不明の送信 intent、AdmissionGate／QueueMutationGate の記録、ジョブ・キュー・SSE・lease・スーパーバイザの状態、ページ内の行 ID・ファイル ID、存在確認の結果。B7 のジョブ・snapshot・キューは B7 が正本。

**復元の境界:** `InputCollectionController.replaceAll()` は 1 回のストア更新で行を置き換え、新しいページ内行 ID を振る。そのため過去の受付記録・ファイル ID・ジョブが新しい行に結び付かない。受理済みジョブは B7 に残り、再送可能にはならない。送信中の行がある場合は置換を拒否する。Preset／Policy の参照は同期的に `ref` へ設定し、revision の読取りは後から完了する。読取り中は投入できない。

## 3. 永続 API（`/api/v1`、`--next-ui-v1` のみ）

既存の v1 サブアプリに登録する。session・Host・Origin の境界は B5 と同じ。GUI の既定起動では import もしない。

| メソッドとパス | 用途 | 成功 | 主なエラー |
|---|---|---|---|
| `GET /worksets` | 一覧と active、上限。**読取りだけで、保存先が無ければ作成せず `exists:false` を返す** | 200 | `store_*` |
| `POST /worksets/bootstrap` `{schema_version, name}` | 初回作成（0 件のときだけ 1 件を作成。冪等） | 200 一覧 | `name_invalid` 422 |
| `POST /worksets` + `Idempotency-Key` `{schema_version, name, state?}` | 新規／複製 | 201 + `ETag`・`Location` | `idempotency_conflict` 409、`workset_limit` 409、`store_not_initialized` 409、`state_too_large`／`store_quota_exceeded` 413 |
| `GET /worksets/{id}` | 1 件（state を含む） | 200 + `ETag` | `workset_not_found` 404 |
| `PUT /worksets/{id}` + `If-Match: "n"` `{schema_version, name, state}` | CAS の全置換。内容が同じなら revision を増やさない | 200 + `ETag` | `if_match_required` 428、`if_match_invalid` 400、`revision_conflict` 412、`state_invalid`／`name_invalid` 422、`state_too_large`／`store_quota_exceeded` 413 |
| `DELETE /worksets/{id}` + `If-Match` | 削除（復旧世代も削除）。active だった場合は後継を同じトランザクションで active にする | 200 一覧 | 412、`workset_last` 409 |
| `GET` / `PUT /worksets/active` `{schema_version, workset_id, view:{mode}}` | 最後の作業セットと表示モード。絶対値なので再送しても安全 | 200 | `mode_invalid` 422、404 |
| `GET /worksets/{id}/recovery` | 復旧用保存状態の一覧（state は返さない） | 200 | 404 |
| `POST /worksets/{id}/recovery/{revision}/restore` + `If-Match` | 復旧用保存状態を現在にする。置き換えた現在の状態も復旧用に残す | 200 + `ETag` | 412、`recovery_not_found` 404 |
| `POST /worksets/input-status` `{schema_version, paths[≤100]}` | 入力の存在を読取り専用で照会。応答は `file`／`missing`／`not_file`／`unchecked` を同順で返し、パスは返さない | 200 | 422 |

- エラー本文は B5 と同形の `{schema_version, error:{code, message_key:"worksets.<code>", request_id, retryable, issues:[]}}`。`retryable` は `store_busy`・`store_unavailable` だけ true。
- ID は `ws_` と UUID hex。パスへ展開しない。revision は 1 から始まる整数。
- 作成の Idempotency-Key は B5 の replay とは独立した、このストアの `replays` 表に保存する。同じキーと同じ本文なら同じ作業セットを返す。上限は最新 64 件で、超えた古い記録はこのストアから消す。
- `input-status` は UNC／`//`／相対パス／NUL を `unchecked` とし、触れない（SMB 認証などの副作用を避けるため）。全体の期限は 3 秒で、期限を過ぎたものも `unchecked`。
- state の検証は Pydantic（`worksets_model.py`）。未知キー、明示的な `null` による継承指定、範囲外の primary、未参照・重複・順序違いの用語集、`schema_version≠1` を拒否する。`job_id`・token などの実行時キーは定義が無いため保存できない。

## 4. 自動保存と競合モデル（クライアント）

`WorksetController`（`frontend/src/domain/v1/worksets.ts`）。Normal と Expert で同じインスタンス。

- **基準:** サーバー revision が唯一の CAS トークン。`baseline` はその revision でサーバーが保持している状態の正規文字列。プロジェクションが `baseline` と違えば未保存。
- **統合:** 変更から 800 ms 静止で 1 回保存。連続編集中も最初の変更から 4 秒以内に保存する。同時に送る要求は 1 本だけで、送るのは最新状態だけ（履歴は送らない）。保存中の追加変更は応答後に保存する。
- **412:** GET で最新を読む。内容と名前が送信内容と同じなら、以前の書込みの応答が失われただけとして採用する。違えば「競合」とし、「保存済みの内容を読み込む」か「この画面の内容で上書き」（最新 revision への明示的な CAS）を選ぶまで書き込まない。
- **応答なし・503:** 同じ `If-Match` のまま 2→5→10→30 秒の間隔で再試行する。適用済みなら 412 から GET で認識され、二重書込みにはならない。無条件の再送はしない。
- **400／413／422:** 固定コードを表示し、同じ内容は再送しない（変更があれば再度保存を試みる）。
- **404:** 別ウィンドウで削除されたものとして扱う。「この画面の内容を新しい作業セットとして保存」を提示する。
- **世代:** 現在の作業セットが替わるたびに世代番号を進める。前の作業セットへの遅い応答・読取りは新しい作業セットへ書き込まない。
- **読込み中の編集:** 起動時の読込み中に編集された場合、保存済みの内容が空でなければ置き換えずに競合として提示する。空の新規作業セットなら、入力内容をそのまま保存する。
- **他ウィンドウの検出:** フォーカス時と表示時に一覧と現在の作業セットを読み直す。未保存の変更が無いときに新しい revision があれば「別のウィンドウで更新された」と提示する（自動適用しない）。未保存の変更がある場合は次の保存の 412 で検出する。
- **ページ終了:** 非表示（`visibilitychange`）で即時保存を試みる。未保存なら `beforeunload` で離脱確認を出す。pywebview で確認ダイアログが出るかは [#22](https://github.com/zawa356/Utteran/issues/22) の実機確認項目。戻る／進むは SPA の履歴を使わないため、ページ離脱と同じ扱い。
- **保存先が読めない場合:** 画面はメモリ上でそのまま使える。「作業セットを読み込めません。この画面の変更は保存されません」と表示し、保存済みとは表示しない。再試行は明示操作。

## 5. ストア（`worksets-v1`）

- **パス:** `DataPaths.worksets_store`（`utteran_paths`）。通常は `user_data_dir("utteran")/worksets-v1/worksets.sqlite3`、明示 data root では `<UTTERAN_DATA_ROOT>/data/worksets-v1/worksets.sqlite3`。B5 `settings-v1/resources.sqlite3`、B6/B7 `control-v1/operations.sqlite3` とは別のファイル・ディレクトリで、それらを読み書き・prune しない。
- **作成:** 初回 bootstrap のときだけ作成する。ディレクトリ 0700、ファイル 0600（POSIX）。symlink は拒否する（`store_unsafe_path`）。O_EXCL で作成した者だけがスキーマを作る。待機側は作成者のコミットを最大タイムアウトまで待ち、空ファイルを初期化しない。以降の接続は `mode=rw` の URI で開くため、消えたファイルを黙って再作成しない。
- **スキーマ（`user_version=1`）:** `installation(id, secret)`、`worksets(id PK, revision, name, created_ms, updated_ms, ordinal, state, state_bytes)`、`recovery(workset_id, revision, name, saved_ms, state, state_bytes, PK(workset_id, revision))`、`active(singleton, workset_id, mode, revision)`、`replays(key PK, digest, status, body, created_ms)`。
- **トランザクション:** すべての書込みは `BEGIN IMMEDIATE`、`synchronous=FULL`、DELETE journal（WAL のサイドファイルを残さない）、`secure_delete=ON`、`auto_vacuum=INCREMENTAL`（作成時に設定）。CAS・容量判定・復旧世代の移動・更新・空きページの返却を 1 トランザクションで行う。
- **fail closed:** 未知の `user_version` は `store_schema_unsupported`、`quick_check` 失敗・不正な記録・active の不整合は `store_corrupt`。ファイルは変更しない。BUSY／LOCKED は `store_busy`（503、再試行可）、FULL は `store_full`（507）、READONLY は `store_readonly`、IO／OS は `store_unavailable`。どの失敗もロールバックし、自動の空初期化、データ破棄、確定済み状態の上書きはしない。
- **復旧世代:** 更新で上書きされる直前の状態を、最新の復旧世代が 10 分より古い場合だけ復旧世代として残す（自動保存のたびには残さない）。1 作業セットあたり最大 **2 世代**。復旧世代の合計は最大 **32 MiB** で、超える場合はこのストアの最も古い復旧世代から削除する。現在の状態は削除しない。明示的な復旧の前には必ず現在の状態を残す。作業セットの削除では、その作業セットの復旧世代も同時に削除する（`secure_delete` により空きページにも値を残さない）。
- **空きページの返却:** 自動保存は空きページを再利用する。空きページがファイルの 1/4 かつ 1 MiB を超えた場合と削除後に、同じトランザクションで `incremental_vacuum` を行う。Python の sqlite3 は PRAGMA を 1 回の execute で 1 step しか進めないため、1 ページずつの上限付きループで返却する。
- **バックアップ:** 稼働中の単純なファイルコピーは禁止。`WorksetStore.backup(dest)` が SQLite backup API で一貫したコピー（0600）を作る（試験済み、HTTP には公開しない）。手動の場合はアプリを終了してからコピーするか、`sqlite3 <db> ".backup <dest>"` を使う。
- **診断・復旧ガイド:** 読めない／未知の形式のストアは自動修復しない。アプリを終了し、上記の方法で退避してから調査する。B5／B7 のストアには影響しない。作業セットのストアを退避・削除すると、次回起動時に新しい「作業セット1」が作られる。

## 6. 容量上限と実測

上限値は `tools/ui3/measure_worksets.py`（合成値のみ）による最大構成の実測で決めた。Cloud Linux、SQLite 3.45.1、page size 4096。

| 構成（1 作業セット） | 正規 UTF-8 バイト | 判定 |
|---|---:|---|
| 入力 100 件、パス 260 文字（CJK）、全行に個別出力先・全 override、共有の用語集 1,000 語×256 コードポイント（CJK） | 935,956 | 受理 |
| 入力 100 件、ASCII パス 32,768 文字（入力・出力とも） | 6,611,093 | 受理 |
| 入力 100 件、CJK パス 32,768 文字 | 19,773,979 | 拒否（`state_too_large`） |
| 異なる最大 CJK 用語集 10 個 | 7,648,264 | 受理 |
| 異なる最大 CJK 用語集 11 個 | 8,410,986 | 拒否 |
| 異なる最大 ASCII 用語集 32 個 | 8,313,534 | 受理 |
| 全 100 行に異なる最大 CJK 用語集（重複排除なしの理論最大） | 76,293,244 | 拒否 |

| 設定値 | 値 | 根拠 |
|---|---:|---|
| 1 作業セット | 8 MiB（8,388,608） | B5／B7 の要求上限 8 MiB と同じ。この範囲の状態は同じ行の 1 編集セッションとして B5 に送れる。通常の最大構成（上表 1 行目）の約 9 倍 |
| 全作業セット合計 | 64 MiB | 20 件すべてが通常の最大構成でも 18.7 MB で、3 倍以上の余裕がある |
| 復旧世代 | 2 世代／作業セット、合計 32 MiB | Issue #80 の承認値（最大 2 世代）。容量は上限付き |
| 作業セット数 | 20 | Issue #80 の承認値 |

上限を超える保存は **明示的に拒否**する（413 と固定コード、画面に表示）。保存済みの状態は変わらず、何も切り捨てない。

| 物理サイズの実測 | ファイル | ページ数（空き） | 復旧世代 | 備考 |
|---|---:|---:|---:|---|
| 20 件すべてが通常の最大構成 | 18,829,312 B | 4,597（0） | 20 行 | コミット直前の journal 最大 33,344 B |
| 合計上限まで最大級の状態で埋め、各作業セットを 3 回更新（1 時間間隔、うち 26 回は合計上限で拒否） | 107,077,632 B | 26,142（2,594） | 11 行（32 MiB 内） | 現在の状態の合計 65,705,316 B。コミット直前の journal 最大 18,279,640 B。コミット後の journal は 0 |
| 通常の最大構成を 1 万回上書き | 1 回目 974,848 B → 10 回目以降 1,908,736 B で一定（10・100・1,000・5,000・10,000 回） | 238 → 466（228） | 1 行 | 回数に比例して増えない |

- ファイルの上限の目安は、現在の状態 64 MiB＋復旧世代 32 MiB＋空きページ（ファイルの 1/4 以下、または 1 MiB 以下）＋ SQLite の管理領域で、約 128 MiB。実測の最悪値は 107 MB。
- 更新中の rollback journal は、更新対象の旧ページ分（最大で約 2 状態分）だけ一時的に増え、コミット後に消える。
- pytest で、1,000 回の上書き後もページ数が 100 回目と同じで初回の 2 倍以下であること、小さい状態の 5,000 回上書きで 16 ページ・64 KiB 以下であること、削除後に空きページ 0 になることを毎回検証する。

## 7. ログと機密

- 新しいログファイル、jsonl、要求本文のログは追加していない。`worksets_*.py` は `logging` を import しない（試験で確認）。GUI ログは既存の `logging_runtime`（`app.log` は `RotatingFileHandler` 10 MiB×5、通常ログは既定 30 日／100 MiB、raw subprocess logs は既定 OFF）のまま。
- 入力パス、作業セット名、用語、Preset 設定などの private な値は、認証済みの要求本文・応答本文とローカル画面だけで扱う。エラー、ログ、URL、SSE、public 診断、ブラウザの localStorage／sessionStorage には出さない（pytest で、sentinel 値がエラー応答と caplog に出ないこと、Chromium で URL とブラウザ storage に出ないことを確認）。
- private DB は B5 と同様に平文（暗号化なし）。保持・削除方針は owner の未決事項を引き継ぐ。Windows の ACL はユーザーディレクトリの継承に依存し、実機受入が必要（[#22](https://github.com/zawa356/Utteran/issues/22)）。

## 8. 試験

| 層 | ファイル | 主な確認 |
|---|---|---|
| Backend／API（pytest） | `tests/test_eb_worksets.py` | 読取りで作成しない、既定 GUI にルート・import なし、session／Host／Origin、初回作成の同時性（8 スレッド・4 プロセス）、CRUD・CAS 412・428・同一 PUT で revision 不変、作成の冪等性と 20 件上限、最後の 1 件の削除拒否、active の移動、継承と明示空の往復、不正 state 15 種の拒否、8 MiB・合計上限の拒否と旧状態の保持、復旧 2 世代・間隔・復元・容量予算、削除時の secure_delete、コミット前の kill、FULL／READONLY／IOERR／BUSY／CORRUPT の注入、読取り専用ファイル、破損・未知 schema の保持、消えたファイルを再作成しない、権限・symlink、backup API、反復上書きの容量、input-status、機密の非漏洩、ログ設定の不変、schema 文書の同期 |
| Unit（Vitest） | `frontend/src/domain/v1/worksets.test.ts` | プロジェクションと復元（継承／明示空、用語集の重複排除、primary、実行時キーなし）、初回 1 件、debounce と統合、最大待ち、再読込での復元と B7 書込みなし、Preset revision の固定、欠損入力、412 競合と解決、応答喪失の照合、503 の再試行、413、切替の flush と遅延応答、保存失敗・送信中の切替拒否、名前変更と削除、20 件上限、読込み中の編集、読めないストア、新しい行 ID、100 件の境界 |
| E2E（Playwright、実 FastAPI） | `frontend/e2e/worksets-eb.v1.spec.ts` | 初回作成→Normal→Expert→自動保存→再読込で同じ状態と表示モード、B7 のジョブ数 0 のまま。名前変更（IME 変換中の Enter は確定しない）／新規／切替／削除と、削除前後でジョブ・snapshot 不変。欠損入力の表示と再確認。2 ウィンドウの競合と明示的な上書き。Preset の新 revision・削除後も rev 1 を固定。英語表示と URL・storage の非漏洩。アプリ再起動（同じ data root）後の復元 |

既存の v1 E2E は、B5/B7 の書込み列を厳密に比較する箇所で `/api/v1/worksets` を除外する。また試験間で共有するサーバーでは、テストの開始時に設定と同様、作業セットを実 API で空の「作業セット1」（Normal）に戻す（`e2e/fixtures.ts`）。作業セットは仕様上、前のページの状態を復元するため。

試験件数、最終 HEAD、push／PR CI の URL と結果は Draft PR の本文に記録する。

## 9. 互換性と変更しないもの

- 既定 GUI（`create_app(session_key, repo_root=...)`）は `/api/v1` も作業セットのモジュールも読み込まない（試験済み）。旧 `--next-ui` は legacy の API モードのままで、作業セット欄を表示しない。CLI は無関係。
- B5 の API・DB、B6／B7 の制御 DB、ジョブ snapshot、キューは変更しない。B5 のエラー応答関数は、作業セットのエラーにも名前空間と retryable を付けられるよう一般化した（B5 自身の応答は従来どおり）。
- v1 の投入判定に「入力がディスク上に無い」問題と、Preset／Policy の参照が読めない場合の `missing` 優先を追加した（投入可能な範囲は狭くなるだけ）。

## 10. 制約・残留課題

- Windows 11 実 WebView2 での自動保存、離脱確認ダイアログ、複数ウィンドウ、IME、DPI、ACL、native picker／D&D は未受入（[#22](https://github.com/zawa356/Utteran/issues/22)）。Chromium は WebView2 の受入ではない。
- 存在確認は読取り専用の stat。ネットワーク上の場所は確認しない（`unchecked` と表示）。シンボリックリンク先の差し替えや確認後の削除は投入時の B7／worker が最終判断する。
- 同一作業セットを 2 ウィンドウで編集した場合、後から保存した側に競合を提示する（自動マージはしない）。表示モードの記録は最後に操作した側が勝つ。
- 作成の結果不明は同じキーと本文での明示的な再確認だけ。自動で再送しない。
- 保存先の暗号化、保持期間、owner による一括削除・エクスポートは未実装（B5 と共通の owner 未決事項）。
- E-C（ASR なし再出力）、束 F、既定 GUI の切替は範囲外。
