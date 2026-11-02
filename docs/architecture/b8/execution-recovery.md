# B8 実行・cancel・復旧

## dispatch（B7と同じthread・同じslot）

`SetupControlStore.next_dispatchable()`は先頭のeligible item（held=0・blocker空・ord最小）がB7 JobまたはB8
Setupの場合に返し、それ以外（B6単体operation等）が先頭なら何も返さない（追い越さない）。`begin_attempt`内の
`_dispatch_guard`が同じ条件をwrite transactionで再検査する。`JobDispatcher.run_once()`は`setup_*`を
`SetupExecutor.dispatch()`へ委譲するだけで、独立したworker threadは作らない。単一slotはB6の
`queue_one_active`部分UNIQUE indexと`leases.slot`PKで保証され、別connection・別Runtimeでも2つ目は
`slot_occupied`/`not_dispatchable`。

## 1 attempt

1. 観測（read-only）。fingerprintがplan時/直前attempt終了時のどちらとも違えば、attemptを開始して即
   `waiting_user(inventory_changed)`（子processなし）。古いplanを実行しない。
2. `begin_attempt`（lease・fencing token・attempt journal）。全stepを`pending`へ戻す（前attemptの成功を
   信用せず、各stepのverify-firstで再確認）。
3. stepごと（宣言順＝DAG順を検証）: `step_started`（running, revision+Outbox）→ 子ごとに
   `child_intent`（`launch=intended`, child identity消去）→ spawn → `step_launched`（PID+OS start identity）
   → 終了観測 → `step_exited`（exit code）→ 検証 → `step_finished`（evidence付きsucceeded/skipped、
   またはfailed）。
4. 全step充足でのみ`finish_setup(succeeded)`。storeも全step `succeeded|skipped`かつevidence有りを再検査。
5. 終了時に再観測したfingerprintを`last_fingerprint`へ保存（retry/resumeの照合用）。

heartbeatはlease/3間隔で`renew_lease`。fencing staleを検出したら以後一切書かない。

## cancel（cooperativeのみ）

- queued/waiting_user/interrupted: 即`cancelled`（queue item・lease解放、202）。
- running: `cancelling`（202、停止完了ではない）。**leaseは保持**。実行中の子（uv sync・download・smoke）は
  killせず、**次の子process境界で停止**し、子の終了を確認してから`cancelled(exit_reconciled)`。
  uv/compiler/downloadを途中killする安全性は未実証のためforceは409。
- `JobRuntime.shutdown()`も同様にcancelling→境界待ち。30秒内に終わらなければslotを残し、次回activateの
  recoveryに委ねる。

## 復旧（B6 `recover()`をそのまま使用）

| 状況 | 記録 | 復旧時の判定 |
|---|---|---|
| intent commit後・spawn前にcrash | `launch=intended`、child NULL | `recovery_required(recovery_launch_unconfirmed)`、slot保持 |
| 子が記録済み・生存 | `launch=confirmed`＋identity | `recovery_child_alive`、slot保持 |
| 子がgone（PID不在・identity不一致は別扱い） | 同上 | `interrupted`（slot解放）。step表示は`interrupted` |
| 子の終了を観測できない（実行中に例外） | `flag_setup_unconfirmed` | `termination_failed`でslot保持 |

起動時はqueue paused・待機setupはheld（statusはqueuedのまま）。自動resume・自動download・自動dispatchなし。
interruptedからは明示`resume`（新attempt、各stepはverify-firstで再確認）。venv sync・download・buildを
命令途中から再開できるとは扱わない（download再開はModelManagerの既存挙動のみ、partialは再verify）。

## 資源の保護

- 実行するcommandは計画の`profile_kind`と承認済みmodel refのみを固定slotに入れる。他profile・
  モデル削除（`models remove`）・`--force`系は生成しない（テストで全argvを検査）。
- 既存一致のvenv/モデルはverify-firstで`skipped/validated_reuse`となり、setup.ps1/downloadを実行しない。
- 既存venvの再構築・generation切替・Windows venv renameは提供しない（`profile_rebuild_unsupported`）。
- read/write leaseはheavy slotの排他で代替（setup実行中はJobも別setupも動かない）。profile単位の
  共有read leaseは未実装（slotが1つのため現時点で競合は起きないが、並列化時は別ADRが必要）。
