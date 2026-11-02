# B9 `GET /api/v1/events`（SSE）

## framing

```
id: <epoch>:<seq>
event: state_changed|queue_changed
data: <EventEnvelope 1行JSON>

```

最初に`retry: 3000`、待機中は`: keepalive`コメント（eventと区別できる）。`resync_required`は
`event: resync_required` + `data: {"schema_version":1,"type":"resync_required","reason_code":...}`で、
**`id:`を持たない**（Last-Event-IDを進めず、偽の連番を作らない）。

## cursor

| 入力 | 動作 |
|---|---|
| なし | 現在のwatermarkから（それ以前は`GET /sync`で取得する） |
| `?after=` と `Last-Event-ID` | どちらでも可。両方あり不一致は400 `cursor_conflict` |
| 書式不正・未来seq | 接続前に400 `cursor_invalid` |
| 別epoch・floor未満（compaction/backup復元後） | 接続前に410 `event_cursor_expired` |
| 接続中に失効（epoch rotation・compaction） | `resync_required`を送って切断 |
| `?resource_id=` | そのresourceの行だけ。seq gapは正常（欠落と扱わない） |

## 認証・header

- 既存v1 middleware: session（cookie可。EventSourceは任意headerを送れないためcookieで通る）、Host、
  Origin。query string・SSE本文・logにsession値を入れない。
- `Content-Type: text/event-stream`、`Cache-Control: no-store`、`X-Accel-Buffering: no`。

## 上限・backpressure（`EventStreamConfig`）

| 項目 | 既定 |
|---|---|
| poll間隔 | 0.5秒（各pollは独立した短いread transaction） |
| heartbeat | 15秒 |
| 1接続の最大時間 | 3600秒（終了後clientはLast-Event-IDで再接続） |
| 同時購読者 | 8（超過は503 `event_stream_limit`） |
| 1回の読取 | 100 events |

generatorはASGI sendが消費されるまで次を読まないため、遅いclientは自分のstreamだけが遅れ、DBの
write transactionは保持しない（テストで3 stream接続中に20件の書込みが完了）。切断は
`request.is_disconnected()`とgenerator終了で検出。SSEの再接続はjob/setupを開始・再試行しない。
