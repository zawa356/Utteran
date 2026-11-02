# B8 HTTP API（opt-in、`/api/v1` sub-appは1つ）

認証・Host・Origin・CSRF（Originなしのmutationはsession header必須）・Content-Type・8 MiB上限・重複key拒否は
B5の既存middleware/body parserをそのまま使う。エラーbodyは`{schema_version, error:{code, message_key:"setup.<code>",
request_id, retryable, issues}}`、`Cache-Control: no-store`。

| method | path | 必須header | 成功 | 主な失敗 |
|---|---|---|---|---|
| POST | `/setup-plans` | Idempotency-Key | 201 SetupPlan（ETag, Location） | 400 key/JSON、409 plan拒否・idempotency_conflict、422 model_unknown等 |
| GET | `/setup-plans/{id}` | — | 200（ETag） | 404（discard済み含む） |
| DELETE | `/setup-plans/{id}` | If-Match | 204 | 412、409 plan_consumed |
| POST | `/setup-plans/{id}/approve` | Idempotency-Key, If-Match | 201 Approval（ETag=plan revision, Location=plan） | 412、409 plan_expired/plan_already_approved/plan_consumed/plan_hash_mismatch/inventory_stale/agreement_required/credential_required、422 agreement_unknown |
| POST | `/setup-operations` | Idempotency-Key | 202 SetupOperation（ETag, Location） | 409 plan_expired/plan_not_approved/plan_consumed/plan_revision_mismatch/approval_invalid/approval_consumed/approval_expired/plan_hash_mismatch/inventory_stale |
| GET | `/setup-operations` | — | 200 SetupOperationList（`s2` cursor） | 400 cursor_invalid、409 cursor_expired、503 list_snapshot_too_large |
| GET | `/setup-operations/{id}` | — | 200（ETag） | 404 |
| POST | `/setup-operations/{id}/retry` | Idempotency-Key, If-Match | 202 新operation（retry_of） | 409 invalid_transition/reapproval_required/credential_required、412 |
| POST | `/setup-operations/{id}/resume` | Idempotency-Key, If-Match | 202（queued、次のdispatchで新attempt） | 同上 |
| POST | `/setup-operations/{id}/cancel` | Idempotency-Key, If-Match | 202 cancelled/cancelling、200 terminal | 409 cancel_mode_unsupported（force） |

## 冪等性

- POSTは全てIdempotency-Key必須。scope = installation + method + canonical endpoint + key、bodyは
  installation秘密鍵のHMAC。**replay判定は期限・CAS・入力検証・観測より先**（plan作成/開始はread
  transactionで先行lookup、さらにwrite transaction内でも再照合）。
- 同key・同body → 初回と同じstatus/body/ETag/Location。同key・別body → 409 `idempotency_conflict`。
  同scopeで別種別（plan/approval/operation、B6/B7の各replay表）との混用は409。
- 本PRのsetup POSTは同key並行時もDB上で1件のみcommitされる（`BEGIN IMMEDIATE`内の再照合、6並列テスト）。
  B7の`submission_in_progress`のような有界待機は不要（観測は短く、トークン消費もない）。

## retry / resume / reapproval

- retry: failed/cancelledのみ。同じ承認済みplanへの新operation（`retry_of`）。元operationは不変。
- resume: waiting_user/interruptedのみ。同じoperationをqueue末尾へ戻し、次回dispatchで新attempt。
- どちらも承認期限内かつ現在のfingerprintが「plan時」または「直前attempt終了時」のどちらかと一致し、
  必要なcredentialが存在する場合のみ。それ以外は409 `reapproval_required` / `credential_required`
  （新しいplanの作成と承認が必要）。追加step・容量増加は新planでしか表現できない。

## 旧API

`/api/wizard/*`・`/api/models/actions`は維持。v1 runtime有効中は旧OperationQueueが閉じるため、旧wizardの
実行系は409（既存`V1_OWNS_SLOT`）。旧wizardの`completed_stages`等はv1へ移行・信用しない。
