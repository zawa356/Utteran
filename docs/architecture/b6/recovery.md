# lease・fencing・recovery

## owner leaseとattempt journal

`begin_attempt(operation, expected_revision, owner, lease_seconds)`は1 transactionで:

- 遷移表でqueued→runningを確認、queue dispatch=running、itemがheld/blockedでないことを確認
  （`queue_paused`/`not_dispatchable`）。
- slot（job/setup=`heavy`、refresh=`refresh`）のleaseを作成。占有中は`slot_occupied`。
- attempt（新番号、`launch=intended`）、fencing token、owner ID、owner nonce・PID・creation identity
  （private）、lease期限・最終確認時刻を記録。queue itemをactiveにする。
- 返却`LeaseGrant`（private）にfencing tokenを含める。

**B7/B8向け契約**: 子process起動前に`begin_attempt`をcommitする。起動後に
`record_launch(fencing, outcome=confirmed, child=ProcessIdentity)`、起動失敗は`outcome=failed`で
事後証拠を別記録する。完了は`transition(..., fencing_token=...)`で、検証済み証拠に基づき
succeeded/failedを記録する。`exit 0`だけで成功扱いしない（B6は判定ロジックを持たない）。

- `renew_lease`は期限を延長するだけで、public revisionを変えない。
- **lease期限切れは助言情報**であり、dispatch・interrupt・再起動の根拠にしない（clock jump耐性）。
- 古いfencing tokenの更新・完了報告は`fencing_token_stale`。

## Verifier

`ProcessVerifier.check(ProcessIdentity) -> Liveness`（alive/gone/pid_reused/unknown）。
観測のみで、signal・kill・spawnをしない。例外や不正戻り値はunknown。nonceはVerifierへ渡さない。
B6同梱は常にunknownを返す`UnknownVerifier`のみで、OS別実装（PID+start time等）はB7。

## `recover(verifier=None, current_owner=None)`

明示呼出しのみ（通常GUIで自動起動しない）。leaseの観測は書込みlockの外で行い、
書込みtransaction内でfencing tokenが変わっていないleaseだけに判定を適用する。

1. queue dispatchを`paused`へ（新規dispatch停止のみ、実行中processのsuspendではない）。
2. 待機item（held=0）を**順序を保ったまま**held=1へ。jobはqueued→held、blocked jobはblockedのまま
   held flag付与、setupはqueuedのままheld flag付与（再開は利用者の明示操作）。
3. active leaseごとに判定:

| owner | launch / child | 結果 |
|---|---|---|
| alive（または`current_owner`のnonce一致） | — | 変更なし（`owner_alive`） |
| pid_reused | — | `recovery_required=recovery_pid_reused` |
| unknown | — | `recovery_owner_unverified` |
| gone | failed（起動失敗証拠） | **interrupted**（`owner_lost`） |
| gone | intended（起動証拠なし） | `recovery_launch_unconfirmed` |
| gone | confirmed + child gone | **interrupted** |
| gone | confirmed + child alive | `recovery_child_alive` |
| gone | confirmed + child pid_reused/unknown | `recovery_pid_reused` / `recovery_owner_unverified` |

- interruptedはlease解放・attempt state=interrupted・active item削除。成功への合成はしない。
- recovery_requiredはstatus（running/cancelling）とleaseを保持し、**slotを占有し続けて二重起動を防ぐ**。
  解除・旧core lock/manifest/partial成果物の照合はB7。
- active statusでleaseがない不整合も`recovery_owner_unverified`。
- 全変更はrevision+Outboxと同transaction。同じ観測での再呼出しは変更・eventゼロ（`changed=false`）。
- 新worker起動、再download、成功化は一切行わない（attempt数不変を検証）。

## 検証範囲とpending

検証済み（実子process不要のfault injection）: transaction前後crash、owner loss、PID reuse、
不明owner、clock jump（±10年/20年）、stale fencing、Verifier例外、同時2 reader/2 writer、
cancel/complete race（revision直列化）。

pending（B7でなければ成立しない）: 実子processの生存確認・PID+start time取得、Windows Job Object
との整合、旧core job lockとの照合、cancel中の実exitとartifact reconciliation、GUI正常終了時の
終了intent journal、実processを使ったcrash/kill試験（Phase A T16のVM kill injection）。
