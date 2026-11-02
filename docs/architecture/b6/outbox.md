# Outbox・cursor・snapshot

## event

Outboxはcommit済み状態の**公開projection**。typed columnから`EventEnvelope`を組み立てる
（任意dictのcopyではない）。fieldはPhase A `EventEnvelope`/`EventPayload`の部分集合:

`schema_version, id(<epoch>:<seq>), resource_id, resource_kind(job|setup_operation|refresh_operation|queue),
resource_revision, timestamp(UTC), type(state_changed|queue_changed), payload{status, reason_code, attempt_id}`

private payload、path、Prompt、用語集、transcript、argv、例外、traceback、token、nonce、PID、
任意labelは**列として存在しない**。`progress`/`stage`/`fraction`/`inventory_changed`/`fallback`/
`resync_required`はB6では生成しない（B7/B9）。

## sequence・epoch

- seqはcounterから採番し、`BEGIN IMMEDIATE`の直列化によりcommit順とseq順が一致する。
- 未commit eventは他reader（別connection）から見えない（commit直前hookから読んで検証）。
- DB再open後も連番は巻き戻らない。
- epochはinstallation作成時のランダム32 hex。`rotate_epoch()`でepochを更新し、
  `epoch_floor`を現在seqに設定する。backup restore後は必須（[storage](storage.md)）。

## `read_events(after, resource_id=None, limit=100)`

- 読取専用transaction。`after=None`は現epochの先頭から。limitは1〜500。
- cursor形式不正・未来seq → `cursor_invalid`（想定400）。別epoch・floor未満 →
  `event_cursor_expired`（想定410）。B6は接続中streamがないため`resync_required`送信はB9。
- `next_cursor`: 続きがあれば最後に返したseq、なければ**read時点のhigh watermark**。
  resource filter中のseq gapは欠損ではなく、filter外eventを飛ばした結果として扱う。
- SSE endpoint、thread、stream managerは作らない。

## `snapshot()`

1つの`BEGIN`読取transactionで、全operationのpublic view、queue view、`operations_revision`、
境界`event_cursor`を返す。次の`read_events(after=event_cursor)`で中間変更を失わず、
snapshot以前のeventを重複して新状態と誤認しない（並行writer下で、snapshotの全revisionが
境界以下のevent再生結果と一致することを25回検証）。

ページングは未実装（全件返却）。大量化した場合のsnapshot token付きpaginationはB9で設計する。

## retention / compaction

compactionなし（outboxはUPDATE/DELETE禁止trigger）。Phase Aの最低7日を下回らない。
将来compactionを入れる場合は`epoch_floor`相当のretention floorと最新watermarkを同一transactionで
更新し、floor未満を`event_cursor_expired`にする。
