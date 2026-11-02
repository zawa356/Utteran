# B9 Outbox retention / compaction（layout v5）

## v5 migration（`ManagedControlStore`）

- v4までの全表を維持し、`outbox_compaction`表を追加、`outbox_immutable_DELETE` triggerを
  `outbox_guarded_delete`（compaction transaction中のみDELETE可）へ置換。UPDATEは引き続き禁止。
- v1〜v4→v5は1 transaction。B6/B7/B8クラスはv5を`store_schema_unsupported`で拒否（テスト済み）。
- B8/B9の`JobRuntime(setup=...)`はv5を開く。jobs-onlyのRuntime（setupなし）はv3のままでcompactionしない
  （`/events`・`/sync`は読取のみなのでv3でも動作）。

## policy（`compact_events()`）

| 規則 | 値 |
|---|---|
| 最低保持期間 | 7日（それ未満の指定は422相当で拒否）。7日未満のeventは件数に関係なく削除しない |
| 件数目安 | 最新100,000件は常に残す（それを超えた古い部分だけが対象） |
| 1回の上限 | 50,000件（長いtransactionを避ける） |
| 保護 | final状態でないresource、`recovery_required`付きresourceの行は削除しない（floor未満に残し、証拠として保持） |
| floor | 削除範囲の上端を`installation.epoch_floor`へ同transactionで設定。それ未満のcursorは410/`resync_required` |

対象は「floor+1から、7日より新しい最初の行の直前・最新100,000件の手前・上限のうち最小」までの連続prefix。
`epoch:seq`は再利用しない（counterは減らない）。crash・disk full・readonlyでは全体がrollbackし、成功と
報告しない（Runtimeは失敗を握りつぶすが`last_compaction`を設定しない）。再実行は安全（idempotent）。

## 実行タイミング

背景ループ・外部schedulerなし。**明示的なv1 activate時に1回**（recovery後）だけ実行する。
接続中のstreamは、自分のcursorがfloor未満になった時点で`resync_required`を受けて切断される。
