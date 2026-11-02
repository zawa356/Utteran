# Batch原子性・Queue CAS・冪等性

## SQLite layout v3（`JobControlStore`）

| version | 内容 | 開ける実装 |
|---|---|---|
| 1 | B6初版 | B6 `ControlStore`/`JobControlStore`が明示migration |
| 2 | +`queue_replays` | B6 `ControlStore`の既定。`JobControlStore`が→3へmigration |
| 3 | +`job_snapshots`、`job_state`、`job_results`、`batch_replays`、`job_replays`（snapshot/results/replay表はUPDATE/DELETE禁止trigger） | `JobControlStore`のみ。**B6 `ControlStore`はv3を`store_schema_unsupported`で拒否**（B6-only codeがB7 Jobを誤読しない） |

migrationは1 `BEGIN IMMEDIATE`で段階適用（1→2→3）。失敗・crash時は旧versionのまま。
旧headerなのに新表がある・未知version・壊れたschemaはfail closed（ファイル不変）。

## Replay表（scopeは共有、型は分離）

| 表 | 応答型 | 用途 |
|---|---|---|
| `replays` | OperationView | B6 operation（不変） |
| `queue_replays` | QueueView | queue control/order/item hold |
| `batch_replays` | JobBatch | `POST /jobs`、`/rerun` |
| `job_replays` | Job | cancel / retry / revalidate |

同一(installation, method, endpoint, key)は1つの応答種別のみ。他表で使用済みなら409
（fake operation IDは作らない）。digestはinstallation秘密鍵のHMAC。

## Batch受理（`accept_batch`）

1回の`BEGIN IMMEDIATE`で: replay再照合 → 全Jobの`operations`・`operation_private`marker・
`job_snapshots`・`job_state`・history・state_changed outbox・queue item → queue revision +1と
queue_changed 1件 → `JobBatch`構築 → `batch_replays`。B6単件`submit()`の反復で擬似batchを作らない。
commit前のどの失敗（故障注入`batch_before_replay`/`before_commit`、STORE_FULL等）でも全表が不変。

## Queue操作（すべてIdempotency-Key必須・If-Match必須・`*`拒否）

| 操作 | CAS | 規則 |
|---|---|---|
| `PUT /queue/order` | queue revision | active/terminalを除く**全waiting item（held含む）の完全順列**。重複・欠落・外部ID 422、active混入409、revision 412。既存ord値を再利用し負値経由で更新（UNIQUE衝突なし）。同順はno-op（revision/eventなし） |
| `PATCH /queue/items/{id}` | **queue revision**（B6 `set_hold`のoperation revisionは使わない） | waiting itemのみ。jobはblockerがなければheld/queuedへ、setupはqueuedのまま。held/blockedは独立フラグ。no-opはrevision不変 |
| `PATCH /queue/control` | queue revision | B6 `set_dispatch`（`AcceptedQueue`）を再利用。pausedは**新規dispatchのみ停止** |

replay照合は常にCASより先。再送は初回のstatus/ETag/QueueViewを完全再現。

## Dispatch直列化

`begin_attempt`内の`_dispatch_guard`で、対象が**B7 Job（snapshotあり）かつ先頭のeligible item**
（held=0、blocker空、ord最小）であることを同じwrite transactionで検査。reorder/hold/dispatchが並行しても
DB上で直列化される。先頭がB7以外（B6 operation等）の場合はdispatchしない（追い越さない）。
単一slotはB6の部分UNIQUE index + lease PKで保証。

## Job操作

- cancel: queued/held/blocked→即`cancelled`（queue itemとlease解放、202）。running→`cancelling`（202、
  **停止完了ではない**、leaseは保持）。terminal→200現状。
- retry: failed/cancelled/interruptedのみ。入力identityが変わっていれば409 `input_changed`。
  succeededは409（rerunを使う）。
- rerun: terminal/interruptedのみ、単一file、新token・新snapshot。
- revalidate: blockedのみ。同じsnapshotを再検査し、成功ならqueued（hold中ならheld）、失敗は409
  `revalidation_failed`（状態不変、replay記録なし）。

## 同一keyの並行送信（PR #32 P2-2）

`POST /jobs`・`/rerun`はscope（endpoint+key）＋HMAC digestで識別し、永続replay → プロセス内in-flight表
（leader 1件）の順に処理する。同key・同bodyのfollowerはleaderの完了を有界待機（既定10秒）し、
永続replayを再照会して同一のJobBatch/status/ETag/Locationを返す。期限を過ぎた場合は再試行可能な
`503 submission_in_progress`を返す（先行結果は偽装しない。後の再送ではreplayが返る）。leaderが
rollbackした場合はfollowerが1回だけcommitする。同key・別bodyは409。tokenのconsumeはcommit成功後に限る。
schema変更はない。

## 一覧pagination（PR #32 P2-3）

`GET /jobs`の1ページ目で、公開Job DTO・順序・revisionを1 read transactionからmaterializeする
（プロセス内、TTL 300秒・16件・1件あたり10,000 Job・合計50,000 Job）。後続ページはそのsnapshotだけを返す。
cursor `j2.*`はsnapshot・位置・page size・閲覧session・filter（現行は全Jobのみ）にHMACで束縛する。
期限切れ・再起動・evictは409 `cursor_expired`、改ざん・転用は400 `cursor_invalid`、上限超過は
503 `list_snapshot_too_large`（末尾を黙って欠落させない）。DB layoutは不変。
