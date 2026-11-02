# 互換性・意図的差異・後続への拡張点

## 既存機能への影響

- 変更しない: `src/utteran_gui/{operation_queue,jobs,setup_wizard,api,settings_api,processes}.py`、
  UI/JS/CSS、B3 worker、B4 resolver、B5 store/API、旧core job lock/stage hash/PID扱い、CLI。
- 追加のみ: `utteran_control` package、`utteran_contracts.control{,_schema}`、
  `DataPaths.control_store`（property追加、既存propertyは不変）、wheel/mypy対象、CIのB6 schema check。
- `create_app()`は`utteran_control`をimportせずDBを作らない。`/api/v1/events`・`/sync`はなく404。
- 旧`OperationQueue`のin-memory辞書を読んで新DBへ偽装移行しない。旧queueが正本のまま。

## 指示書・Phase Aとの意図的差異

| 項目 | 指示書/Phase A | 実装 | 理由 |
|---|---|---|---|
| branch名 | `feat/b6-durable-operation-store-outbox` | `claude/loving-noether-jroqrc` | セッションのpush先が固定。基点B5 HEAD・base B5は同一 |
| DTO配置 | `utteran_control/contracts.py`も可 | `utteran_contracts/control.py` | 既存schema exporter・軽量契約packageと同居、循環なし |
| outbox module | `outbox.py`例示 | `store.py`内 | Outbox書込みはresource書込みと同transactionで分離しない方が安全 |
| EventPayload | stage/fraction含む | status/reason_code/attempt_idのみ | B6はprogressを生成しない。Phase Aでも全field任意 |
| EventEnvelope.resource_kind | inventoryを含む | inventoryなし | inventoryはB4/B9。B6 schemaのenumに含めない |
| SyncSnapshot | snapshot_token/next_cursor/inventory_revision | `ControlSnapshot`（全件、token/paginationなし） | 実HTTP syncはB9。名前を分けPhase A型の完成を装わない |
| SyncResource | id/kind/revision/statusのみ | `OperationView`（held/blocked/blockers/recovery_required/attempt追加） | 内部基盤のためrecovery状態を明示 |
| Queue/QueueItem | Phase A形 | 同形（`QueueView`/`QueueItemView`）、resource_kindはjob/setupのみ | refreshはqueueを使わず`refresh` slot |
| setup held | lifecycleにheld状態なし | statusはqueued、itemのheld flag | [contracts](../phase-a/contracts.md) §9の交差制約どおり（差異ではなく確認事項） |
| Idempotency-Key | 16〜128文字 | 加えて可視ASCIIのみ | header安全性。緩和はB9で再検討 |
| 冪等性保持 | 最低7日 | 無期限（GCなし） | 勝手なGCを作らない |
| Queue更新の冪等性 | 全POSTとQueue更新はIdempotency-Key必須 | `set_dispatch`は任意（B6内部）、`queue_replays`へ保存 | PR #28レビューP2。HTTP境界（B7）では必須化する |
| SQLite layout | — | `user_version=2`、v1→v2追加migration | Operation replay（`replays`）の書式・意味論は不変。旧v1 replayはmigration後も同一応答 |

## B7/B8/B9への拡張点

- B7: queue hold/reorder/dispatchのHTTP化ではIdempotency-Keyを必須にし、`set_dispatch`/`set_hold`の
  scope引数へ渡す。queue reorder APIを追加する場合も`queue_replays`（QueueView応答）を使い、`replays`へ混ぜない。

- B7: OS別`ProcessVerifier`、dispatcher（held/blockedをskipする最初のeligible選択）、
  Job DTO・private snapshot・stage hash統合、queue hold/reorder/cancel HTTP、recovery_required解除、
  旧queueからの切替とrollback、実process race試験。
- B8: SetupOperation DTO・step journal（Phase A §2の工程state）、plan承認との結合。
- B9: `/api/v1/events` SSE（`resync_required`）、`/api/v1/sync`（snapshot token pagination）、
  compaction/retention floor、互換API adapter、B5/B6 installation統合の要否。

## 既知の環境課題（本PRで修正しない）

- `uv.lock`のroot version変更で全体hashが変わり、既存profileが`dependency_lock_changed`となり得る
  既知問題（B4/B5から継続）。manifest/venvの再生成・互換判定無効化は行わない。
  本PRはversion行のみ変更（ローカルuvが古く`uv lock`がlock revisionを下げるため、手動で1行更新し
  `uv lock --check`成功を確認）。
- Linux mypy 9件（`processes.py`のWindows API）、Windows Inno Setup前提、PID-file競合は
  [Issue #27](https://github.com/zawa356/Utteran/issues/27)。
