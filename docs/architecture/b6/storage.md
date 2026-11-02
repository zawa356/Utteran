# 保存・schema・backup

## 場所

`DataPaths.control_store`（解決のみでI/Oなし）:

- 通常: `platformdirs.user_data_dir("utteran")/control-v1/operations.sqlite3`
- `UTTERAN_DATA_ROOT`指定時: `<root>/data/control-v1/operations.sqlite3`

B5の`settings-v1/resources.sqlite3`とは別DB・別schema version・別writer・別installation ID/secret。
B6はB5 DBを読まず、installation ID/HMAC secretも共有しない。統合する場合はB7/B9で明示ADR/migration。

`ControlStore(path)`は明示的に呼んだ場合だけ作成する。既定GUI/CLIの起動経路には存在しない。

## 作成とopen

- pathは絶対・`.sqlite3`拡張子・`..`なしのみ受理（`store_unsafe_path`）。
- POSIX: 親directoryを0700で作成し、symlink・非directory・他uid所有を拒否。DB fileも
  symlink・非regular・他uid所有を拒否し0600へ。SQLiteはjournalをDBと同じ権限で作る。
- 新規作成は**一時file（O_EXCL 0600）上でschema・installation・counterを1 transactionで作成**し、
  fsync後にPOSIXは`os.link`、Windowsは`os.rename`で**上書きなしに公開**する。
  同時作成で負けた側は勝者の完成DBを開く。最終pathに未完成DBが現れることはない。
  crash時に残る`.<name>.init-<random>`は無害で、最終DBとして扱わない（手動削除可）。
  hard link非対応filesystem（POSIX上のFAT等）では`store_unavailable`となり、無理に作成しない。
- 各接続は`file:...?mode=rw` URIで開く。open後にDBが削除されても**新しい空DBを作らない**。
- open時検証: `user_version==2`（0=空/外部file、3以上=未知はともに`store_schema_unsupported`。
  1は下記の明示migrationの対象）、
  `quick_check`、必須table、installation 1行、counter集合、queue 1行。失敗時は**変更・再初期化・
  downgrade・削除をしない**（テストでbyte不変を確認）。

## SQLite設定

`PRAGMA user_version=2`（storage layoutの版。wire DTOの`schema_version=1`とは別物）、`synchronous=FULL`、`foreign_keys=ON`、既定DELETE journal（WAL sidecarを
常駐させない）。書込みは`BEGIN IMMEDIATE`、読取は`BEGIN`（同一SHARED snapshot）。
busy timeout既定2秒。

| 例外 | code |
|---|---|
| BUSY/LOCKED | `store_busy` |
| FULL / ENOSPC | `store_full` |
| READONLY | `store_readonly` |
| CORRUPT/NOTADB/quick_check失敗/必須table欠落 | `store_corrupt` |
| CANTOPEN/その他 | `store_unavailable` |

例外はすべてrollbackし、raw SQLite messageやpathを持たない`ControlError`へ変換する。

## テーブル

| table | 内容 | 公開性 |
|---|---|---|
| installation | installation ID、32-byte HMAC secret、cursor epoch、epoch floor | private |
| counters | `outbox_seq`、`fencing`、`operations_revision`、`enqueue_order`（それぞれ独立） | 内部 |
| operations | id/kind/status/revision/reason_code/recovery_required/時刻 | public projectionの元 |
| operation_history | (operation, revision)ごとの不変記録、対応outbox seq | 内部（UPDATE/DELETE禁止trigger） |
| operation_private | private payload（canonical JSON） | **private** |
| queue | queue revision、dispatch（running/paused） | public projectionの元 |
| queue_items | item id、operation（UNIQUE）、enqueue順`ord`（UNIQUE）、active、held、blockers | public projectionの元 |
| attempts | attempt id、番号、fencing token（UNIQUE）、owner id、state、launch evidence | public（fencing除く） |
| attempt_private | owner nonce、owner/child PIDとcreation identity | **private** |
| leases | slot（PK: `heavy`/`refresh`）、operation/attempt/fencing（各UNIQUE）、期限、最終確認 | 内部 |
| outbox | seq、epoch、type、resource kind/id/revision、status、reason_code、attempt_id、時刻 | 公開projectionそのもの（UPDATE/DELETE禁止） |
| replays | installation/method/endpoint/key、HMAC digest、status、headers、public `OperationView` body、operation FK | 内部（UPDATE/DELETE禁止）。Operation専用、v1から書式不変 |
| queue_replays（v2） | installation/method/endpoint/key、HMAC digest、status、headers、public `QueueView` body、queue revision | 内部（UPDATE/DELETE禁止）。Queue応答専用、operation FKなし |

単一active slotは`leases.slot` PKと`queue_items(active) WHERE active=1`の部分UNIQUE indexで二重に拒否する。

## 保持

GC/compactionは実装しない（no-prune）。Phase Aの最低7日・active resource中保持を満たす。
history・outbox・replayは無期限に増える。保持期限・完全消去・private payloadの消去方針は
レビュー待ちの未決事項で、B6は既存private履歴（B5含む）を削除しない。

## backup / restore

- `ControlStore.backup(dest)`: SQLite online backup APIで新規0600 file（既存なら`store_exists`）へ複製し、
  複製後に同じopen検証を行う。失敗時は自分が作成した複製先だけを削除する。稼働中の`cp`は使わない。
- restore（運用手順、B6はCLIを提供しない）: アプリ停止 → 現DBを退避 → backupを`operations.sqlite3`
  として配置 → `ControlStore(path).rotate_epoch()`。backup時点以降のseqは再利用され得るため、
  **restore後は必ずepochを更新**し、旧cursorを`event_cursor_expired`として再同期させる。
  restore後の実行中recordは`recover()`の保守的判定に従う。
- 自動restore、DBの再生成による「回復」は行わない。

## migration / rollback

### v1 → v2（PR #28レビュー修正、方式A）

`set_dispatch`の永続replayのため、Queue応答専用表`queue_replays`を追加した（方式A）。
既存`replays`はOperation専用のまま（`operation_id NOT NULL REFERENCES operations(id)`、OperationView body）で、
架空のoperation IDやQueueViewを入れない。方式B（replay表の一般化）は既存行の移行とFK再設計が必要で、
Operation replayの書式・意味論を変えるため採らなかった。

- 新規作成: 一時file上でv1 DDL＋v2 DDLを作成し`user_version=2`で公開（既存の一時file→上書きなし公開は不変）。
- 既存v1: open時に`BEGIN IMMEDIATE`内で再度versionを確認し、v1構造（quick_check、必須table、installation、
  counter、queue行）を検証してから`queue_replays`と不変triggerを追加し`user_version=2`へ更新する。
  **追加のみ**で既存のoperation/history/replay/outbox/installation secretは変更しない。
- v1 headerなのに`queue_replays`が存在する等の不整合は`store_corrupt`、v1構造の欠損は`store_corrupt`、
  0/3以上は`store_schema_unsupported`。いずれもfileを変更しない（byte不変をテスト）。
- 途中失敗（commit前の故障注入、SQLITE_FULL、process crash）はDDLと`user_version`ごとrollbackされ、
  完全なv1 storeが残る。次の通常openで再migrationされる。同時openは`BEGIN IMMEDIATE`で直列化し1回だけ適用。
- `create=False`でも既存v1はmigrationする（明示的に開いたstoreのみ）。読取専用環境ではv1のまま`store_readonly`。
- 運用上の推奨: migrationは単一writerの状態（アプリ停止中または当該storeを開く唯一のprocess）で行い、
  事前に退避を取る。v1 storeを`ControlStore`で開くと先にmigrationされるため、migration前の退避は
  アプリ停止後（writerなし）のfile copy、または旧コードの`backup()`で行う。
  B6は既定未使用のため、現時点で既存の利用者DBは想定していない。
- v2→v1のdowngradeは提供しない。v2 storeを旧（a037857）コードで開くと`store_schema_unsupported`で拒否される。
- 本PRのrollbackはcommit revertのみで成立する（既定経路から未参照、既存DBに触れないため）。
  作成済み`control-v1/`は旧コードから参照されず、必要なら利用者が手動で退避・削除する。
