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

## PR #33レビュー修正: 固定snapshot paging（B9-P2-3、2026-11-02追補）

提出時の`d1.<offset>.<limit>`は各ページで履歴を再取得・再ソートしたため、ページ間の追加・同一時刻で
重複/欠落・revision混在が起こり得た（修正前スクリプトで再現: 1ページ目revision 4、2ページ目revision 5）。
cursorはresource・kind・sessionに束縛されていなかった。

修正後（`DiagnosticsService`、activeなstoreごとに1つ）:

- cursorなしのGETで、対象resourceの公開`DiagnosticEntry`全件とrevisionを**1 read transaction**でmaterialize。
  順序は`(recorded_at, source, source内の単調ID)`で一度だけ決め、後続ページは再読込・再ソートしない。
  ページ取得中の新revision・step journal追加・compaction・時計の巻戻りは既存snapshotに影響しない。
- cursor `d2.<snapshot>.<offset>.<limit>.<hmac>`: snapshot・offset・page size・resource ID・kind・viewer（session
  binding）をinstallation秘密鍵のHMACで束縛。内容からprivate値は分からない。
- 上限: TTL 300秒・16 snapshot（B7 `GET /jobs`と同じ`LIST_TTL`/`LIST_CAPACITY`）、1 resource 20,000件
  （`DIAGNOSTIC_MAX`、超過は503 `list_snapshot_too_large`、黙って切り詰めない）、全体80,000件。複数ページになる
  場合だけsnapshotを保持する。
- エラー: 期限切れ・evict・再起動（別process）は**409 `cursor_expired`**（新しいcursorなしGETから取り直す）、
  署名不正・書式不正・旧`d1`・他protocol（`j2`/`s2`/`y1`）・別resource/kind/sessionへの流用は**400 `cursor_invalid`**。
  B7 `j2`・B9 `/sync`と同じ規約。
- **単一プロセス内snapshot**であり、再起動・別プロセスでは継続できない（継続できると偽装しない）。
- 応答形（`DiagnosticEntryList`）・公開field（timestamp/code/message_key/stage/attempt_id）は不変。
  `next_cursor`の最大長のみ64→200（署名付きcursorのため。schema再生成済み）。SSEの`epoch:seq`とは別protocol。
