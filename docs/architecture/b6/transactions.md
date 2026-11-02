# transaction・CAS・冪等性

## 単一transaction

`submit` / `transition` / `set_hold` / `begin_attempt` / `record_launch` / `set_dispatch` /
`recover`の各受理は、1つの`BEGIN IMMEDIATE` transaction内で次を同時に確定する。

1. resource行の状態・revision（+1）・reason・更新時刻
2. 不変`operation_history`行（対応outbox seqを持つ）
3. collection revision（`operations_revision`）
4. queue item（作成/flag/active/削除）とqueue revision（変化時のみ+1）
5. Outbox event（`state_changed`、queue変化時は続けて`queue_changed`）
6. 冪等性scopeがある場合はreplay記録

commit前の失敗はすべてrollback（部分行なし）。commit後に応答が失われても、同key再送は
同じstatus/headers/bodyを再現し、resource・event・replayは増えない（テストで検証）。

## revisionとsequence

- operation revision: operationごとの単調増加整数（1開始）。
- queue revision: queue行ごと。
- outbox seq: installation epochにおける全体単調増加（counter、再利用なし）。
- fencing token: 全体単調増加（counter）。

これらは別counterで、互いに代用しない。

## CAS

- `expected_revision != current` → `revision_conflict`（想定412）。
- 表にない遷移 → `invalid_transition`（想定409）。未登録ID → `resource_not_found`（404）。
- `BEGIN IMMEDIATE`で書込みを直列化するため、同じrevisionへの並行更新は1件だけ成功する
  （2 thread・2 process・2 connectionで検証）。

## 遷移表

`transitions.py`（[Phase A lifecycles](../phase-a/lifecycles.md) §2〜4準拠）:

| kind | from → to |
|---|---|
| job | queued→held/blocked/cancelled/running、held→queued/cancelled、blocked→queued/held/cancelled、running→cancelling/succeeded/failed/interrupted、cancelling→cancelled/failed/interrupted |
| setup_operation | queued→cancelled/running、running→waiting_user/cancelling/succeeded/failed/interrupted、waiting_user→queued、cancelling→cancelled/interrupted、interrupted→queued |
| refresh_operation | queued→cancelled/running、running→cancelling/succeeded/failed/interrupted、cancelling→cancelled/interrupted |

- terminal（succeeded/failed/cancelled）とjobのinterruptedからは遷移なし。retry/rerunは別resource（B7）。
- `running`へは`begin_attempt`（lease+journal）経由のみ。`transition(to=running)`は拒否。
- active（running/cancelling）からの離脱は**現在のfencing token必須**（`running→cancelling`の
  cancel要求のみ不要）。cancelling→succeededは表にないため、cancel受理後の完了報告は
  cancelledへ収束させる必要がある。completion先行時は後続cancelが412/409になる。
- setupのwaiting_user/interrupted→queuedは新queue itemを末尾に作る（次attemptは新番号）。
- `held`（利用者意図）と`blocked`（環境不足）は別flag。jobのstatusはflagを反映し、
  blocked中のhold解除はblockを解除せず、block解除は`held`へ（`queued`へは不可）。
  setupはqueued statusのままitemの`held`だけを持つ。
- store専用reason（`submitted`、`dispatch_started`、`owner_lost`、`recovery_*`等）は外部から指定不可。

## 冪等性

- scope: (installation ID, `POST`, canonical endpoint, key)。endpointは`/api/v1/<segment>...`の小文字
  pathのみ、keyは可視ASCII 16〜128文字（Phase Aの長さ制約に文字種制限を追加）。
- digest: canonical JSON（key順非依存、NaN拒否、256 KiB上限）のHMAC-SHA256（installation secret）。
  平文body・平文hashは保存しない。replay bodyはpublic `OperationView`のみ。
- **replay照合はDTO検証・意味検証・CASより先**。同key同bodyは元結果、異bodyは`idempotency_conflict`。
  未受理（4xx/503）は記録しない。transition/set_holdも任意でscopeを取れる。
- 同key並行送信は1件だけ受理され、他はreplayになる（6 threadで検証）。
- **外部processのexactly-once効果は保証しない**。B6はcommit後にsubprocess/network/downloadを起動しない。

## 故障注入（テスト）

`_fault`（`before_commit`/`after_commit`）と`_connect_hook`（`max_page_count`、`query_only`）で、
SQLITE_FULL、実disk full相当、readonly、busy、process crash（`os._exit`）をcommit前後に注入し、
「resourceはあるがreplay/eventがない」「eventだけある」「重複event」が起きないことを確認した。
