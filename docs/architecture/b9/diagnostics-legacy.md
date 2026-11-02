# B9 diagnostics / legacy互換

## diagnostics

`GET /api/v1/jobs/{id}/diagnostics`、`GET /api/v1/setup-operations/{id}/diagnostics` →
`DiagnosticEntryList{revision, items[{timestamp, code, message_key:"diagnostics.<code>", stage, attempt_id}], next_cursor}`。

- 情報源: `operation_history`（固定ReasonCode、Outbox行が残っていればattempt ID）と、setupでは
  private step journalの`result`/`launch_failed`行（step id、固定StepReason → `step_<reason>`）。
- **読まないもの**: worker stdout/stderr、traceback、Prompt、path、argv、token、モデルdir、exit code、
  自由文字列。worker/CLIのログは非構造化テキストしかないため公開しない（diagnosticsに含めない）。
- 両情報源とも追記専用なので、offset cursor `d1.<offset>.<limit>`で安定してpagingできる（1 operation最大20,000件）。
- `GET /jobs/{id}`・`GET /setup-operations/{id}`の応答は変更しない。kind違い・未知IDは404。

## legacy互換

- 既定`create_app()`のroute一覧を`tests/fixtures/b9_default_routes.json`でgolden固定（main時点の一覧から生成）。
  `/api/v1/*`なし、旧`/api/wizard/jobs/{job_id}/events`あり、B6〜B9 moduleのimportなし。
- 旧CLI・出力形式・旧Queue/JobManager/SetupWizardServiceは変更しない（B9はこれらを置き換えない）。
- 新旧Queueの同時dispatchなし: v1 activate時に旧OperationQueueを閉じる（B7）。B7 Job→legacy core job
  IDのlinkは`legacy_core_job_id`のみで、旧historyへ二重writeしない。
