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

### epochとrevisionの有効範囲（Issue #37、0.1.48）

- `epoch:seq`のseqとresource/queueの**revisionは別の指標**で、値の一致は仮定しない。revision・status・cursorの比較は**同じepoch内だけ**有効。
- **別epochの`/sync`**（例: backup復元後の`rotate_epoch()`）は新しい正本。reducerはcursor・既知のresource revision/statusに加え
  **queue revisionも新epochの値で再初期化**する（旧epoch rev 100 → 新epoch rev 5を採用し、続くrev 6の`queue_changed`は`applied`）。
  0.1.47まではqueue revisionだけ`max()`で旧値を保持し、新epochのqueue eventを`stale`として捨てていた。
- **同じepoch内**は単調: 古い`/sync`・遅れたresource GET・既知以下のrevisionのevent（queueを含む）は状態もcursorも巻き戻さない。
- 旧epochのSSE frameは`resync`。resource GET（`apply_resource`）はepochを持たないため、旧epochの応答を新epochへ適用しないのは
  呼出し側の世代管理（新UIの`SyncCoordinator`は`AbortController`＋現在の要求参照で後着応答を破棄）の責務。
- `/sync`の全ページは同じsnapshot token・`event_cursor`を返す。reducerへは**全ページを結合した1つのsnapshot**として適用する。
- 再同期・再接続はジョブ/Setupの開始・再投入・再試行ではない。wire形式・schema・DBは不変。

> PR #33レビュー修正追補: `/sync`の実装・cursor（`y1`）は不変。diagnosticsの固定snapshot paging（`d2`）は
> 同じ設計（[diagnostics-legacy](diagnostics-legacy.md)）で、protocolは別。
