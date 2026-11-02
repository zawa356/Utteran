# B9 `GET /api/v1/sync`

`read_sync()`が**1つのSQLite read transaction**（rollback journalのSHARED snapshot）で、全operation
（job/setup_operation/refresh_operation、id・kind・revision・status）、QueueView、setup observation
counter、Outbox watermark（`epoch:seq`）を読む（テストでtransaction 1回を確認）。

`SyncSnapshot`: `snapshot_token`、`event_cursor`、`resources`、`next_cursor`、`queue`、`inventory_revision`。

- `inventory_revision`はB8の**setup観測counter**。setup観測がない（v3 store含む）場合は`null`。
  B4のinventory revisionは同時点で取得できないため使わず、数値を作らない（Phase A差異）。
- paging: 1ページ目でsnapshot全体をプロセス内に保持（TTL 300秒・16件・最大50,000 resource）。
  後続ページは同じtoken・cursor・queue・inventoryを返し、途中の追加・完了は混ざらない（テスト済み）。
  cursor `y1.*`はsnapshot・位置・page size・閲覧sessionにHMAC束縛、`snapshot_token`指定時は一致必須。
  B7 `j2`・B8 `s2`とは互換なし（400）。再起動・evictは409 `cursor_expired`→**最初から新しいsync**。
  上限超過は503（末尾を黙って欠落させない）。`limit` 1〜500。

## 正しい利用順序

1. `GET /sync`（全ページ）でstateと`event_cursor`を得る。
2. `GET /events?after=<event_cursor>`を購読し差分を適用。
3. 重複event IDは無視、resource revisionが既知以下のeventや遅れて届いたGETは破棄、filterによるseq gapは
   正常、別epochや`resync_required`/410なら1へ戻る。必要な場合だけresource GETで調停。

純粋reducer `utteran_contracts.events.EventReducer`がこの規則を実装する（UIへの接続はU1以降）。
