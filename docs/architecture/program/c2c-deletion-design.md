# 束C2c 設計追補 — 履歴レコードの墓標化（③）と所有確認付き成果物削除（④）

更新: 2026-11-17 JST（開発版`0.1.57`・未リリース）。正本の前提は[c2-history-and-artifacts-design §0](c2-history-and-artifacts-design.md)（owner確定事項・必須条件5つ）。
追跡: [Issue #56](https://github.com/zawa356/Utteran/issues/56)。

> **成功の定義は「完全消去」ではない。** 本文書は「どこから何を消すか」と同じ重みで「どこに何が残るか」を記録する。
> 実装・試験は合成データと`tmp_path`だけで行った。Windows実機は未受入（§8）。

## 0. 結論（要約）

| 項目 | 決定 |
|---|---|
| ③の方式 | **墓標化**（③-a）。`operations`と状態履歴・Outbox・attempt骨格・可視性は残し、snapshot本文・完了証拠・private付帯行を固定値へ置換/削除する |
| layout | **v8 = v7 ＋ `job_deletions` ＋ `job_deletion_gate` ＋ 2つの不変トリガーの狭い置換**。一方向、追加migration。v7のpre-open backupは従来どおり残る |
| 不変トリガー | `job_snapshots`・`job_results`の`BEFORE UPDATE`だけを「ゲート行があり、かつ新しい値が**固定の墓標定数**、他の列は不変、Jobは終了状態」のときに限り通す条件付きトリガーへ置換。`BEFORE DELETE`・他の表の不変トリガーは無変更 |
| 一時表案（§3.1旧案） | **不成立**（実測、§3.2）。mainスキーマのトリガーはTEMP表を参照できない。v5の`outbox_compaction`と同型の**永続ゲート表**を採用 |
| 削除後の公開表現 | `GET /jobs/{id}`・`/visibility`・`/artifacts`・`retry`・`rerun`・`cancel`・`revalidate`・削除済みJobを含む旧replayは **410 `job_deleted`**（値なし）。`GET /jobs`の全filterから除外。B9 `/sync`・`/events`・diagnosticsは骨格（ID・kind・revision・status・固定code・時刻）だけを従来どおり返す |
| ④の対象 | 0.1.54以降の**成功したJob**の、所有markerが一致する**唯一の**専用ディレクトリだけ。形式別証拠（`evidence.files`）とサイズ・SHA-256が完全一致する既知形式ファイル＋marker以外が1つでもあれば全体拒否 |
| ④の手順 | preflight（読み取り）→ 短命plan token → 実行直前に同じ検証 → DBへ削除意図（journal）をcommit → ファイルごとに「隔離名へrename → inode再確認 → unlink」→ marker → 空なら`rmdir` → 全成功時だけ③（同一DBトランザクション） |
| ④のOS | POSIXで`dir_fd`/`O_NOFOLLOW`/`O_DIRECTORY`が使える環境だけ。**Windowsは`platform_unverified`で拒否（fail closed）**。理由と後続は§8 |
| 消去の強さ | 削除トランザクションの接続で`PRAGMA secure_delete=ON`。合成試験でDB本体からsentinelが消えることを実測。backup・rollback journalの解放済み領域・retry連鎖の他Job・core作業キャッシュには残る（§6） |

## 1. private情報の保存箇所（棚卸し、0.1.56 main `6532174`）

「private」= 入力/出力の絶対path、ファイル名、Prompt・用語集を含む処理設定、入力ファイルのSHA-256・inode等の同一性、成果物の内容digest。

| # | 保存箇所 | 内容 | 不変性 | ③後 | ④後 |
|---|---|---|---|---|---|
| 1 | `job_snapshots.body` | `JobSnapshot`全体（入力path・出力dir・Prompt/用語集を含む設定・入力SHA-256/inode・モデルdir） | トリガーで不変、読み込み時にdigest照合 | **固定の墓標JSONへ置換**（ゲート経由） | 同左 |
| 2 | `job_snapshots.digest` | body全体のSHA-256（推測照合に使える） | 同上 | **墓標JSONのdigestへ置換** | 同左 |
| 3 | `job_snapshots` その他列 | `batch_id`・`retry_of`・`rerun_of`・`created_at` | 同上 | **残す**（参照の骨格） | 同左 |
| 4 | `operation_private.body` | `{"b7_snapshot_digest": <#2>}` | 可変（トリガーなし） | 墓標digestへ置換 | 同左 |
| 5 | `job_results.evidence` | 成果物ごとのSHA-256・サイズ、manifest digest | トリガーで不変 | **固定の墓標JSONへ置換**（ゲート経由） | 同左 |
| 6 | `job_results` その他列 | `attempt_id`・`core_job_id`（16 hex）・`artifact_count`・時刻 | 同上 | 残す（core作業キャッシュの後続整理の手掛かり。§6） | 同左 |
| 7 | `attempt_private` | owner nonce・PID・プロセス開始時刻・子PID | 可変 | 当該Jobのattempt分を**削除** | 同左 |
| 8 | `attempts` | ID・番号・fencing token・owner ID・状態・時刻 | 可変 | 残す（骨格） | 同左 |
| 9 | `operations`／`operation_history`／`operation_history_attempts`／`outbox` | ID・kind・status・revision・固定reason code・attempt ID・時刻 | 履歴とOutboxは不変 | 残す（値なし。B9整合のため） | 同左 |
| 10 | `job_state` | 固定語彙の`block_reason`/`error_code` | 可変 | 残す | 同左 |
| 11 | `job_visibility` | hidden・revision・時刻 | 削除不可 | 残す | 同左 |
| 12 | `replays`（OperationView） | 公開projectionのみ | 不変 | 残す。配信時に削除済みJobを含めば410 | 同左 |
| 13 | `job_replays`／`batch_replays` | 公開`Job`/`JobBatch`（`client_file_id`、keyed HMACの`input_identity`、spec概要） | 不変 | **DBには残る**（不変・他Jobの応答を含む）。配信時に削除済みJobを含めば410 | 同左 |
| 14 | `queue_replays` | 公開`QueueView`（item ID・resource ID） | 不変 | 残す（終了Jobはqueueに居ないので通常は含まない） | 同左 |
| 15 | 一覧・sync・diagnosticsのプロセス内snapshot | 公開view（TTL 300秒） | — | **一覧snapshotを全破棄**（発行済み`GET /jobs` cursorは409 `cursor_expired`）。sync/diagnosticsは値なしなので保持 | 同左 |
| 16 | 同じsnapshotを持つ他Job（retry子・同batch） | retryは**同じbody**を自分の行にコピーしている | 不変 | **残る**（別Job。一括削除しない）。planで件数を示す | 同左 |
| 17 | rollback journal（`operations.sqlite3-journal`） | 書込みトランザクション中の元ページ | 一時ファイル | 削除トランザクション中は元ページ（sentinel含む）を保持、commitでunlink。**ファイルシステム上の解放済みブロックは対象外** | 同左 |
| 18 | WAL | — | — | **使用していない**（既定の`journal_mode=DELETE`、実測） | — |
| 19 | `operations-pre-v1-*.sqlite3`／`operations-rollback-*.sqlite3` | DB全体のbackup | 削除しない | **残る**（downgrade・復旧の安全網。自動削除しない） | 同左 |
| 20 | Settings v1 DB（`resources.sqlite3`） | Preset/Policy・editing state | — | 対象外（触らない） | 同左 |
| 21 | core作業キャッシュ（`<data root>/jobs/<core_job_id>`） | 段階cache（音声の中間・文字起こし結果）。retryで再利用 | — | **対象外・残る**（C2cの許可範囲外。後続Issue） | 同左 |
| 22 | 専用出力dir（`<出力先>/<stem>_<時刻>_<ID>/`） | 成果物5形式＋marker | ユーザーのファイル | **残る**（③は実ファイルを消さない）。markerは残るので他Jobが採用することは無い。**snapshotが消えるので、以後アプリから④は不可** | 検証済みファイルとmarkerを削除、空なら`rmdir` |
| 23 | 元の音声/動画・作業セット・他Job出力・C1以前の成果物 | ユーザーのファイル | — | 対象外 | **対象外（絶対に触らない）** |
| 24 | ログ・SSE・diagnostics出力 | 固定codeのみ（既存方針） | — | 変化なし（削除処理も値を書かない） | 同左 |

### 1.1 墓標の定数

```
job_snapshots.body   = {"b7_snapshot":"deleted","schema_version":1}
job_snapshots.digest = sha256(上の文字列)
job_results.evidence = {"b7_evidence":"deleted","schema_version":1}
operation_private.body = {"b7_snapshot_digest": <墓標digest>}
```

定数はトリガーにリテラルで埋め込む（アプリ側の値を信用しない）。読み込み経路は`job_deletions`の`completed`行を先に見て410にするので、
墓標JSONを`JobSnapshot`として解釈することは無い（`store_corrupt`の500にならない）。

## 2. ③後の参照・API（定義）

| 経路 | ③後の扱い | 理由 |
|---|---|---|
| `GET /api/v1/jobs`（visible/hidden/all） | **含めない** | 履歴レコードを削除した。`retry_of`の親が一覧に居ないことは非表示で既にあり得る（frontendは対応済み） |
| `GET /jobs/{id}` | 410 `job_deleted` | 墓標から`Job`を作るとsnapshot概要を捏造することになる |
| `GET/PUT /jobs/{id}/visibility` | 410 | 同上 |
| `GET /jobs/{id}/artifacts` | 410 | 出力先pathが消えたので位置を証明できない |
| `POST /jobs/{id}/retry`・`rerun`・`cancel`・`revalidate` | 410（副作用0） | snapshot本文が無い。既存の子Job・他Jobは不変 |
| 既存子Jobの`retry_of`/`rerun_of` | 削除済みIDを指したまま | FKと履歴を壊さない。子は自分のsnapshotを持つ |
| 子Jobの`retry`・`rerun` | 従来どおり可能 | 子のsnapshotは子の行（#16） |
| 同key・同bodyの再送（replay） | 応答が削除済みJobを含むなら410。含まなければ従来どおり | 古いreplayで削除済みJobの公開viewを再出現させない。DB行は不変のまま |
| 削除要求そのもののreplay | 初回の削除結果をそのまま返す（専用表`deletion_replays`） | lost responseの安全な再送 |
| B9 `/sync`・`/events` | 骨格（ID・kind・revision・status）を従来どおり。**イベントは出さない** | 削除は状態遷移ではない（非表示と同じ定義）。`EventReducer`・共通fixtureを変えない |
| diagnostics | 従来どおり（固定code・時刻・attempt ID） | 値を含まない |
| 起動時recovery・dispatcher | 影響なし（削除できるのは終了Jobだけで、queue/leaseに居ない） | — |
| 失効したIdempotency-Keyの再送 | replay行が無ければ通常処理 → 410 | 復元しない |

**ETag**: 削除は`operations.revision`を進めない（状態遷移ではない）。削除済みJobには表現が無い（410）ので、古いETagで成立する書込みは無い。

## 3. layout v8 と不変トリガー

### 3.1 追加DDL

```sql
CREATE TABLE job_deletions (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id),
  mode TEXT NOT NULL CHECK (mode IN ('history','history_and_artifacts')),
  state TEXT NOT NULL CHECK (state IN ('artifacts_pending','completed')),
  targets TEXT,            -- ④のjournal（形式・サイズ・sha256）。completedでNULLに戻す
  requested_at TEXT NOT NULL, completed_at TEXT,
  CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
  CHECK (state = 'artifacts_pending' OR targets IS NULL),
  CHECK (mode = 'history_and_artifacts' OR state = 'completed'));
-- 行は消さない。mode不変、completed → 他は不可
CREATE TABLE job_deletion_gate (operation_id TEXT PRIMARY KEY REFERENCES job_deletions(operation_id));
CREATE TABLE deletion_replays (... replays と同じ形 ..., operation_id TEXT NOT NULL REFERENCES operations(id));
DROP TRIGGER job_snapshots_immutable_UPDATE;  -- 下の条件付きトリガーへ置換（DELETE側は無変更）
CREATE TRIGGER job_snapshots_guarded_update BEFORE UPDATE ON job_snapshots WHEN NOT (
  OLD.operation_id IN (SELECT operation_id FROM job_deletion_gate)
  AND (SELECT status FROM operations WHERE id = OLD.operation_id) IN ('succeeded','failed','cancelled','interrupted')
  AND NEW.operation_id IS OLD.operation_id AND NEW.batch_id IS OLD.batch_id
  AND NEW.retry_of IS OLD.retry_of AND NEW.rerun_of IS OLD.rerun_of AND NEW.created_at IS OLD.created_at
  AND NEW.body = '<墓標body>' AND NEW.digest = '<墓標digest>')
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
-- job_results も同型（evidenceだけを墓標へ、他列不変）
```

- ゲート行は削除トランザクションの中で入れて、commit前に必ず消す。**開くたびに`job_deletion_gate`が空であることを検証**し、空でなければ`store_corrupt`（commitされたゲートは不正な状態）。
- ゲートへのINSERTは、`job_deletions`の行があり、Jobが終了状態のときだけ（トリガー）。
- 直接の`UPDATE`（ゲート無し）、ゲート有りでも墓標以外の値、他列の変更、実行中Jobは全て`immutable`で拒否（試験§7）。
- 既存トリガーを**全体的にDROPして無制限更新を許す方式は採らない**。置換するのは上記2つの`BEFORE UPDATE`だけで、v5の`outbox_guarded_delete`と同じ「専用状態のときだけ通す」形。

### 3.2 一時表案の検証（実測、2026-11-17）

設計§3.1の「削除手続き用の一時表にIDがあるときだけ通す」をSQLite（Python `sqlite3`、Linux）で試した。
mainスキーマの表に作ったトリガーから`CREATE TEMP TABLE`の表を参照すると、**トリガー作成は通るが、実行時に`no such table: main.tgate`で失敗**した
（非TEMPトリガーの本体はmainスキーマで名前解決される）。よって一時表案は採用できない。永続のゲート表＋開くときの空検証で代替した。

### 3.3 移行・downgrade

- v7→v8は1つの`BEGIN IMMEDIATE`内の追加migration（既存行は不変、試験で全行比較）。失敗すればv7のまま。
- v1 runtimeの起動は従来どおり開く**前**にbackupを作るので、初回のv8起動前のbackupはv7のまま残り、0.1.56のbuildで開ける（試験で復元ドリル）。
- **一方向**: v8は0.1.56以前のbuildでは開けない（`store_schema_unsupported`、ファイル無変更）。
- **downgradeで削除が戻ることはない**が、**削除前のbackupへ戻すと削除済みJobのprivate内容も戻る**。backupは削除しない（§6）。

## 4. ③の手順（DB 1トランザクション）

1. replay検索（`deletion_replays`、同key同body → 初回結果、異body → 409 `idempotency_conflict`）。
2. Jobが存在・B7 Job・未削除（削除済み → 410）、`If-Match`＝Job revision（不一致 → 412）。
3. 状態が`succeeded/failed/cancelled/interrupted`、`recovery_required`なし、queue item・lease・active attemptなし（それ以外 → 409 `invalid_transition`、副作用0）。
4. ④の削除が途中（`artifacts_pending`）なら、履歴のみの削除は409 `deletion_in_progress`（所有が追えなくなる状態を作らない）。
5. plan tokenの再計算（§5.3）が一致しなければ409 `deletion_plan_stale`。
6. `PRAGMA secure_delete=ON` → `job_deletions`へ記録 → ゲート → snapshot/evidence/private付帯行の置換・削除 → ゲート削除 → replay記録 → commit。
7. commit後に一覧のプロセス内snapshotを破棄。

## 5. ④の手順と状態

### 5.1 対象の検証（preflightと実行直前に同じ関数）

全て満たすときだけ許可。1つでも外れたら**何も消さず**、固定codeで拒否する。

| 検査 | 拒否code |
|---|---|
| Jobが`succeeded` | `not_succeeded` |
| snapshotに`output_stamp`（0.1.54以降） | `legacy_output` |
| 形式別の証拠`evidence.files`がある（旧証拠・記録なしは`unverified`） | `evidence_unverified` |
| POSIXで`os.open`/`os.unlink`/`os.rename`/`os.rmdir`/`os.stat`が`dir_fd`を受け、`O_NOFOLLOW`/`O_DIRECTORY`がある | `platform_unverified` |
| ID長8→12→16→24→32の候補のうち、markerがこのJob IDのものが**ちょうど1つ** | `directory_missing`／`directory_ambiguous` |
| 親（`file.output_dir`）とdir名がsnapshotから再計算した値と一致し、dirがsymlinkでない通常のディレクトリ（`O_NOFOLLOW|O_DIRECTORY`で開いたfdの`fstat`と`lstat`の(dev, ino)一致） | `unsafe_entry` |
| marker: 通常ファイル・nlink 1・symlinkでない・内容が1行のJob ID | `marker_mismatch` |
| 中身がmarkerと`<dir名>.<srt|vtt|json|txt|md>`**だけ**（サブディレクトリ・他の名前・隔離名の残骸があれば拒否） | `unexpected_entries` |
| 各ファイル: 通常ファイル・nlink 1・symlinkでない・`O_NOFOLLOW`で開ける・64 MiB以下 | `unsafe_entry` |
| 各ファイル: **自分の形式の**記録とサイズ・SHA-256が一致（別形式のdigestと一致しても不可） | `artifact_changed` |
| 証拠にある形式のファイルが無い（初回） | `artifact_missing` |
| 証拠に無い既知形式のファイル | `unexpected_entries` |
| 読めない・権限不明 | `filesystem_error` |

`job_artifacts()`／`owned_directory()`（C2aの読み取りビュー）は削除の根拠に**使わない**。削除専用の検証（`job_deletion.verify_artifacts`）はfdを開いたまま中身を検証する。

### 5.2 実行（TOCTOU対策）

1. **再検証**（§5.1と同じ関数、fdを保持）→ fingerprint（dirとmarkerの(dev, ino)、各ファイルの形式・サイズ・SHA-256・(dev, ino)・mtime）がplan tokenと一致しなければ409 `deletion_plan_stale`。
2. **意図をcommit**: `job_deletions`へ`artifacts_pending`と対象（形式・サイズ・SHA-256。名前やpathは書かない）を記録。
3. ファイルごと: 同じdir fd内で隔離名`.utteran-deleting-<乱数>`へ`rename` → 隔離名を`O_NOFOLLOW`で開き、(dev, ino)・サイズ・SHA-256が検証時と一致するか再確認 → 一致すれば`unlink`。
   不一致なら`link`で元の名前へ戻して（上書きしない）隔離名を外し、その時点で中止（結果は`incomplete`）。戻せなければ隔離名のまま残し中止（次のpreflightは`unexpected_entries`で拒否 → 利用者が確認）。
4. markerも同じ手順（内容がJob IDであることを再確認）。
5. dirが空なら親のfdから`rmdir`（(dev, ino)一致を確認）。空でない（同時に誰かが置いた）なら**残す**。
6. 全ての対象とmarkerが無くなったら、③と同じトランザクションで墓標化し`completed`、`targets`をNULLへ。
7. 途中で失敗したら墓標化しない。応答は200 `outcome: "incomplete"`（消した数・残った数）。replayは記録しない（同じ要求は再計画から）。

`shutil.rmtree`は使わない。ディレクトリを辿って消すことはしない（対象は検証済みの名前だけ）。

### 5.3 plan token

`dp1.<有効期限ms>.<HMAC>`。HMACはinstallation secretで、(Job ID・mode・Job revision・fingerprint・期限・session binding)に対して計算する。
token自体は状態を持たず、実行時にserverが**新しく検証してfingerprintを計算し直し**、MACが一致したときだけ進む。
期限切れ（既定300秒）は409 `deletion_plan_expired`、それ以外の不一致（改ざん・状態変化・別session）は409 `deletion_plan_stale`。**serverが新しいplanを勝手に発行して削除することはない。**

### 5.4 中断・再起動・部分削除

| 中断した時点 | DB | ディスク | 次の操作 |
|---|---|---|---|
| 意図commit前 | 変化なし | 変化なし | 最初からやり直し |
| 意図commit後・ファイル操作の途中 | `artifacts_pending`＋対象journal | 一部の対象・markerが無い／隔離名が1つ残り得る | preflightはjournalを使い「journalにあって無いもの」を削除済みとして扱う。隔離名が残っていれば`unexpected_entries`で拒否（利用者が確認） |
| ファイル完了後・墓標化commit前 | `artifacts_pending` | dir無し（または空でないdirが残る） | preflightは「残り0件」で許可 → 実行で墓標化のみ |
| 墓標化commit後・応答前 | `completed` | — | 同key同bodyの再送 → 初回結果（`deletion_replays`） |

- `artifacts_pending`中は**履歴のみの削除を拒否**（409 `deletion_in_progress`）。表示・非表示・retry/rerun・成果物ビューは従来どおり。
- 二重実行（連続クリック・2タブ）は、GUIプロセス内の削除ロックで直列化し、DB側でも状態を確認する（OSロックでGUIは1プロセス）。

## 6. 消せない・残る情報（実測と文書の一致が必須）

| 残る場所 | 何が | 対応 |
|---|---|---|
| backup（`operations-pre-v1-*`、`operations-rollback-*`） | 削除前のDB全体（snapshot本文） | 自動では消さない。画面と文書で明記 |
| retry連鎖の他Job・同じbatchの他Job | 同じsnapshot本文のコピー | 別のJobとして残る。planが件数（`related_jobs`）を返し、画面で明記。一括削除しない |
| `job_replays`/`batch_replays`/`replays` | 公開projection（`client_file_id`、keyed HMACの入力ID、モデル名等） | DB行は不変。配信は410 |
| Outbox・状態履歴・diagnostics | ID・状態・固定code・時刻 | 骨格として残す |
| `job_results.core_job_id`と core作業キャッシュ | 段階cache（中間音声・文字起こし） | **C2cの範囲外**。後続Issue |
| rollback journalのファイルシステム上の解放済みブロック、SSD/ファイルシステムのスナップショット、OSのごみ箱・検索index・クラウド同期 | 任意 | アプリから制御できない |
| ③後の出力ディレクトリ | 成果物とmarker | ③は実ファイルを消さない。以後アプリから④は不可 |

## 7. 試験（`tests/test_c2c_deletion.py`）

区分は指示書§7に合わせる。Redを先に確認したもの・Greenの数はPR本文に記録する。
1. migration/recovery: v7→v8の追加移行（全行同一）、失敗時rollback、旧classの拒否、pre-open backupと復元、ゲート残存時のfail closed。
2. 墓標化: 状態境界、CAS/同key replay/異body、非公開値の消去、retry/rerun・B9 `/sync`・cursor・Outbox・snapshot read・二重削除、DB本体・journal・replay・backupのsentinel走査。
3. 成果物削除: marker、各ID長、形式別SHA/size、旧形式、異形式のバイト置換、編集・余計なファイル・他Job・元音声、symlink、TOCTOU（検証後の差替え）、途中失敗、二重実行、再開。
4. API: session/Host/Origin、If-Match、planの失効/改ざん、JSON schema、409/412/404/410、値の非漏えい。
5. frontend: Vitest（2種類の確認、既定フォーカス、二重送信、結果不明）とPlaywright v1（合成runner）。

## 8. Windows（未受入、fail closed）

- Windowsの`os`は`dir_fd`を受けないため、§5.2の「同じdir fdの中で検証して消す」手順が成立しない。パスで消す実装はreparse point・junctionの差替えに対して安全を証明できない。
  よって**Windowsでは④を`platform_unverified`で拒否**し、③（DBだけ）は動作する。
- 後続案: ハンドルベース（`FILE_FLAG_OPEN_REPARSE_POINT`で開き、`GetFileInformationByHandle`で同一性・属性を確認し、`SetFileInformationByHandle(FileDispositionInfoEx)`で**ハンドルに対して**削除）。
  Codex実機受入でreparse point・junction・ACL・開いているファイル・長いpath・OneDrive配下を確認してから有効化を判断する（後続Issue）。
- Windows受入で今回確認できること: ④がWindowsで拒否され副作用0、③のmigration・墓標化・backup残存。
