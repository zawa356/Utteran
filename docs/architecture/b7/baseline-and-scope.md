# B7 事前監査: baseline・既存機能・実装可能範囲

## GitHub確認ゲート（2026-11-02）

| 項目 | 結果 |
|---|---|
| `main` HEAD | `3fd187dff64e83291253bd0d0bf4ba74f043664a`（fetch後も同一、新commitなし） |
| PR #30 / #24 / #25 / #26 / #28 / #29 / #31 | mainにmerge commit（`7318a2f`/`cf84f33`/`417e3be`/`8a3f175`/`c636555`/`c79de4a`/`3fd187d`）を確認 |
| Issue #27 | open維持。既知失敗はB7の新規失敗と分離して比較 |
| UI監査Draft PR #23 | open・未統合。内容は取り込んでいない |
| Pages | run [41394884715](https://github.com/zawa356/Utteran/actions/runs/41394884715) conclusion=success。`/Utteran/`・`/Utteran/en/` HTTP 200、`/Utteran/favicon.svg` HTTP 404（独立の軽微不具合、B7差分外） |

## Baseline（main実測、Linux、Python 3.12.13、`pytest -m "not requires_model"`）

`1024 passed / 2 failed / 8 skipped`。failed集合:
`tests/test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`、
`...::test_profile_manifest_detects_venv_move_without_disabling_profile`（B6記録の既知2件と一致）。
mypy: `src/utteran_gui/processes.py`の既知9件のみ。Windows/契約matrixはCIで再測定（validation.md）。

## 既存機能の監査

| 対象 | 監査結果 | B7での扱い |
|---|---|---|
| `utteran_gui/api.py` 旧`/api/*` | session cookie/header、`JobManager`/`OperationQueue`はin-memory、SSEあり | 不変。v1有効時のみ旧queueを閉じ、旧投入routeは409 |
| `operation_queue.py` | 単一workerで直列実行、プロセス寿命のみ | `close_for/reopen`を追加（既定は閉じない） |
| `processes.py` / `worker_client.py` | ProcessSupervisor（Windows Job Object）、tree kill、B3 IPC・`verify_result` | 再利用。`on_spawn` hookのみ追加（既定None） |
| `settings_api.py` | B5 v1 sub-app、session/Host/Origin、body上限・重複key・UTF-8検査 | **同じsub-app**にB7 routeを登録。PATCHもCSRF境界へ追加 |
| `utteran_control.store` | schema v2、`submit/transition/set_hold/set_dispatch/begin_attempt/record_launch/recover`、`replays`(OperationView)と`queue_replays`(QueueView)は別型 | hook化のみ。B6既定はv2のまま、B7は`JobControlStore`(v3) |
| B6 `set_hold` | **operation revision** CAS | HTTP `PATCH queue/items`には使わず、queue revision CASの新操作を実装 |
| B6 `_check_private` | `token`等のkeyを拒否 | snapshotにtokenを入れない（resolution tokenはDBへ保存しない） |
| B5 `preview()` | `worker_support`は`unverified/unsupported`のみ | readyの証拠にしない。`unsupported`なら拒否 |
| B4 `resolve()` | 純粋関数、core内、tokenなし | GUIはcoreをimportできないためbridge `resolve` actionを追加 |
| B3 worker | 単一絶対path、FW、明示ローカルモデル、確定device/compute、話者分離なし | この範囲のみdispatch可能 |

## Phase A OpenAPIとの意図的差異

| Phase A | B7 | 理由 |
|---|---|---|
| `Job.snapshot: JobSnapshot`（file path・overrides・settingsを含む） | `JobSnapshotSummary`（created_at、client_file_id、refs、resume_mode、opaque `input_identity`、`ResolvedSpecView`） | private値・絶対pathを公開しない |
| `Attempt.status/route/...` | `JobAttempt`（id/number/state/launch/時刻）、`Job.result.legacy_core_job_id` | B6 attempt journalの事実のみ |
| `ResolvedSpec.settings`/`profile_ref` | private `ResolvedSpec`に保持、公開viewはsettingsなし | Prompt・vad_model path等の非公開 |
| `Resolution.files[].input_identity` | `in_<32hex>`（installation鍵HMAC） | path非公開 |
| `Job.error: ErrorDetail` | `JobErrorView{code}`（固定語彙） | raw error禁止 |
| `JobBatch` 202 | 同じ。`Location: /api/v1/jobs`、`ETag`=queue revision | — |
| 追加 | `Job.held/block_reason/recovery_required/reason_code` | queue状態とblock理由の明示 |
| `/jobs/{id}/diagnostics` | 未実装（404） | B7範囲外 |
| `CancelRequest.mode=force` | 409 `cancel_mode_unsupported` | 未検証の強制停止を許可しない |
