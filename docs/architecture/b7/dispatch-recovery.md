# Dispatcher・Worker接続・クラッシュ復旧

## 1 attemptの順序（各段のcommit後に次へ）

1. `next_dispatchable()`（paused/slot占有/先頭非B7なら何もしない）。
2. **pre-start再検査**（同じsnapshotのみ）: 入力identity、出力dir、環境再観測で凍結candidateの
   ready・fingerprint・モデルdir。失敗→`blocked`（`block_reason`: input_changed / input_unavailable /
   output_unavailable / candidate_changed / candidate_unavailable / resolver_unavailable /
   worker_plan_unsupported）。新候補へ切り替えない。
3. `plan_request`の事前検証（B3限定構成以外は`worker_plan_unsupported`でblocked）。
4. `begin_attempt`: lease・fencing token・attempt(`launch=intended`)を**commit**。
5. `WorkerClient.execute(..., on_spawn=...)`: Popen直後に`on_spawn`で子PIDと**OS start identity**を
   `record_launch(confirmed)`。commit→spawnの間にcrashすれば`intended`が残る。spawn失敗は
   `record_launch(failed)`→`failed(start_failed)`。`record_launch`自体が失敗した場合、
   WorkerClientのfinallyで子をtree killしてから失敗扱い。
6. heartbeat（lease/3間隔）: `renew_lease`、status=cancellingならcancel要求。fencing staleなら以後
   一切書かない。
7. 終了: B3 `verify_result`（manifest、stage、artifact存在/size/sha256、出力dir、formats）を通った
   `WorkerCompleted`だけを候補とし、さらに**入力identityを再取得**して一致しなければ`failed(input_changed)`。
8. 子の終了を`poll()`で確認できない場合は`flag_unconfirmed`（`recovery_required=termination_failed`）で
   **slotを保持**し、終端を書かない。
9. `finish_attempt`（fenced）: cancel要求が先にcommitされていれば、exit 0でも`cancelled`
   （`exit_reconciled`、resultは記録しない）。completion先行なら`succeeded`を保持し後のcancelは200。
   worker `cancelled`だがcancel要求なし→`failed`。store busyは指数backoffで5回再試行、それでも失敗なら
   leaseを残してrecoveryへ委ねる。

`core_job_id`はJob UUIDと別で、`job_results`にartifact/manifest digestと共にprivate保存、公開は
`legacy_core_job_id`とartifact数のみ。旧core job lockはB2/B3の`application.execute_single`が使う同じ
`job_dir`（`DataPaths.jobs`）を共有するため、旧経路と同一入力の競合はcore lockで排他される。

## Cancel

cooperativeのみ。WorkerClientがIPC `cancel`を送り、core cleanupを待つ。forceは
backend能力・Windows実機検証がないため409。停止未確認ならleaseを保持（`recovery_required`）。

## Process verifier（`OsProcessVerifier`、観測のみ）

| 状況 | Linux | Windows（CIのみ、実機未検証） |
|---|---|---|
| start identity | `linux:<boot_id>:<starttime>`（`/proc/<pid>/stat`） | `win:<creation FILETIME>`（`OpenProcess(QUERY_LIMITED)`+`GetProcessTimes`） |
| PID不在 | gone | `ERROR_INVALID_PARAMETER`→gone |
| identity一致・稼働 | alive（zombieはgone） | `STILL_ACTIVE`→alive |
| identity不一致 | pid_reused | pid_reused |
| 記録identityなし・権限不足・例外 | unknown | unknown |

PIDだけを生存証拠にしない。macOS等はPID不在のみgone、それ以外unknown。
Windowsは`OpenProcess`/`GetProcessTimes`/`GetExitCodeProcess`/`CloseHandle`の`argtypes`/`restype`を
明示し（HANDLEをpointer幅で扱う）、判定は`observe_windows()`の純関数として64-bit handleのfakeで検証する。
OpenProcess後のAPI失敗はUNKNOWN、handleは`finally`でclose、close失敗は判定に影響しない（PR #32 P3）。

## Recovery（`recover()`はv1 activate時のみ）

B6のルールをそのまま使い、verifierだけをOS実装にする: dispatchをpaused、waiting itemをheld、
ownerと記録済みchildが**共にgoneと立証**できた場合のみ`interrupted`（`launch=failed`はchildなしの証拠）。
`intended`のまま・child alive・pid_reused・unknownは`recovery_required`でslot占有。自動resume/download/dispatchなし。

`recovery_required`の解除手順（B7時点）: 利用者が該当child/旧GUIを終了 → v1を再activate
（recoverがgoneを確認して`interrupted`へ）→ 必要ならretry。強制解除APIは提供しない。
疑わしいcore lock・partial outputは旧core規則（resume/force、lock）に委ね、人工的な`succeeded`は作らない。

## 正常終了

`JobRuntime.shutdown()` → dispatcher停止（新規dispatchなし）→ 実行中Jobを`cancelling`へ記録→
cooperative cancel → 終了待ち（30秒）→ fenced終端。時間内に終わらなければslotを残し、
旧queueも閉じたまま（次回activateのrecoveryで判定）。
