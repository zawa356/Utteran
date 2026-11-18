# Persistence, migration, recovery and privacy

## 2026-11-17 追補（0.1.57、束C2c、Draft PR）

- **layout v8**（`DeletionControlStore`）= v7 ＋ `job_deletions`（削除journal、行は消さない）＋ `job_deletion_gate`（削除トランザクション内だけで開閉、開くときに空でなければ`store_corrupt`）＋ `deletion_replays`。
  `job_snapshots`/`job_results`の`BEFORE UPDATE`だけを墓標定数に限る条件付きトリガーへ置換。一方向、v7のpre-open backupは0.1.56で開ける（試験で復元ドリル）。
- **成果物削除（④）は全環境で無効**（2026-11-18、PR #58レビュー）。`artifacts_pending`行を新たに作る経路は無い。
- **削除の残留**: DB本体は`secure_delete`で対象Jobの値が消えることを実測。**backup・rollback journalの解放済み領域・retry連鎖の他Job・公開projectionのreplay・core作業キャッシュには残る**。
  WALは使っていない（`journal_mode=delete`を試験で固定）。詳細と表は[../program/c2c-deletion-design.md §1・§6](../program/c2c-deletion-design.md)。
- 下の「Privacy」表の「完全消去手段なし」は引き続き正しい（C2cは完全消去を提供しない）。保持期間・暗号化・ACL・backup保持方針はowner判断待ちのまま。

## 2026-11-17 追補（0.1.56、現行main）

束C2a/#52のlayout v7（v6＋`job_visibility`）はmainへ統合済み（最終main `c5af76726f913e4044f6e73492fee851c2acdd8c`）。Windows 11専用VMでは旧HEAD `1c76453` においてv6→v7通常移行、旧版の`store_schema_unsupported`拒否、backupからの復元を確認し、差分HEAD `ad8c38e` では非表示/再表示時のDB各表・Outbox全行ハッシュ不変を確認した。[PR #53 Windows受入レビュー](https://github.com/zawa356/Utteran/pull/53#pullrequestreview-5934044093)。**移行中の強制killはNOT_RUN**（[#55](https://github.com/zawa356/Utteran/issues/55)）。実ユーザーデータのrestore・Windows ACL・完全消去を保証するものではない。

履歴のみの墓標化・生成成果物の削除は未実装（[#56](https://github.com/zawa356/Utteran/issues/56)）。private DB/WAL/backup/replayに残る内容の検証と所有確認はC2cの必須条件であり、先行受入をもって削除可能としない。

## 保存先

`utteran_paths.resolve_data_paths()`が唯一の解決経路（`UTTERAN_DATA_ROOT`指定時はその配下）。

| DB | 既定パス（data root未指定時） | 所有package | 作成されるのは |
|---|---|---|---|
| Settings v1 | `user_data_dir("utteran")/settings-v1/resources.sqlite3` | `utteran_settings`（`user_version=1`） | `enable_v1_settings`使用時のみ |
| Control v1 | `user_data_dir("utteran")/control-v1/operations.sqlite3` | `utteran_control` | `enable_v1_jobs`で`JobRuntime.activate()`した時のみ |
| backup | 同ディレクトリ `operations-pre-v1-<UTC>.sqlite3`、`operations-rollback-<UTC>.sqlite3` | `JobRuntime` | activate時（既存DBがある場合）、deactivate時 |

**既定のGUI/CLIはどちらのDBも作らない**（`tests/test_backend_foundation_e2e.py::test_default_app_is_legacy_only_and_creates_no_control_store`）。
POSIXではディレクトリ0700・ファイル0600に揃える。**Windows ACLは未設定・実機未受入。**

## Control store layout（SQLite `PRAGMA user_version`）

> 追補（2026-11-16、0.1.55）: **layout v7**（v6＋`job_visibility`、追加のみ）。v1 runtime（B8構成あり）は`HistoryControlStore`でv7を開く。
> v7は0.1.54以前のbuildでは開けない（一方向）。起動時のpre-open backupはv6のまま残り、旧buildで開ける。手順と試験は
> [../b7/history-visibility.md §3](../b7/history-visibility.md)。以下のv6に関する記述は0.1.39〜0.1.54の状態。

| version | 開くclass（`TARGET_VERSION`） | 追加内容 | 導入 |
|---|---|---|---|
| 1 | `ControlStore`（B6初版） | `operations` `operation_private` `operation_history` `attempts` `attempt_private` `leases` `queue` `queue_items` `replays` `outbox` `counters` `installation` | B6 |
| 2 | `ControlStore` | `queue_replays`（Queue応答replay） | PR #28 review |
| 3 | `JobControlStore` | `job_snapshots` `job_state` `job_results` `job_replays` `batch_replays` | B7 |
| 4 | `SetupControlStore` | `setup_plans` `setup_approvals` `setup_operations` `setup_steps` `setup_step_journal` `setup_state` `setup_inventory` `setup_replays` | B8 |
| 5 | `EventsControlStore` | `outbox_compaction`、guarded outbox delete trigger | B9 |
| 6 | **`ManagedControlStore`**（`JobRuntime`が使う現行class） | `operation_history_attempts`（不変なdiagnostics attempt sidecar） | PR #33 review（B9-P2-2） |
| 7 | `HistoryControlStore` | `job_visibility`（非表示） | 0.1.55（C2a） |
| 8 | **`DeletionControlStore`**（0.1.57以降に`JobRuntime`が使う） | `job_deletions` `job_deletion_gate` `deletion_replays`、2つの条件付きUPDATEトリガー | 0.1.57（C2c） |

規則（`ControlStore._migrate`）:

- 各classは自分の`TARGET_VERSION`だけを開く。古いversion（1以上）は**1つの`BEGIN IMMEDIATE`内で**順に
  additive migrationし、途中で失敗すればDDLと`user_version`ごとrollbackされ旧layoutのまま残る。
- **一方向**: 新しいversion・0・未知の値は`store_schema_unsupported`で拒否し、ファイルに触れない。
  v6は`JobControlStore`/`SetupControlStore`/`EventsControlStore`のいずれでも開けない。
- 旧headerなのに新しい表がある等の不整合は`store_corrupt`。
- 検証: `tests/test_backend_foundation_e2e.py`（v3→v6、v4/v5→v6、migration中故障のrollback、旧classの拒否）、
  [B7](../b7/compatibility.md)・[B8](../b8/storage.md)・[B9](../b9/retention.md)の各テスト。

## Backup と restore

- `JobRuntime.activate()`は**既存DBを開く前に**`ControlStore.backup_before_open()`で
  `operations-pre-v1-*.sqlite3`を作る（SQLite online backup、排他作成0600、`quick_check`）。
  **0.1.38まではstore構築（=migration）の後にbackupしていたため、v3/v4/v5のDBもv6としてbackupされていた**。
  0.1.39で修正し、backupは元のlayoutのまま残る（`test_opt_in_migrates_v3_to_v6_with_restorable_pre_cutover_backup`）。
- restore手順（開発者向け・アプリ停止中に実施）: (1) 現行`operations.sqlite3`を退避、(2) 目的のbackupを
  `operations.sqlite3`へコピー、(3) そのlayoutを開けるversionのアプリで起動、(4) 同じ版で再び使う場合は
  `rotate_epoch()`相当で古いSSE cursorを失効させる（B6の規則）。
  tmp DBで(2)(3)はテスト済み。**実機・実ユーザーデータでの復元は未受入で、保証済みとは扱わない。**
- backupにはprivate payloadが含まれる。公開・添付・共有しない。

## 排他・fencing・復旧

- **単一heavy slot**: active leaseは同時に1つ。Job dispatchとSetup実行が共有。
- **fencing token**: lease取得ごとに増え、古いtokenでの書込みは拒否（`test_stale_fencing_token_is_rejected`）。
- **冪等性**: Idempotency-Keyとendpointのscopeでreplay表に初回応答を保存。lost response再送は同じ
  status/ETag/Location、異bodyは409。
- **起動時recovery**（`ControlStore.recover`、activate時に明示実行）: dispatchを`paused`、待機itemを`held`、
  ownerと子プロセスが**共に確認できて不在**のときだけ`interrupted`。確認できないものは`recovery_required`で
  slotを保持し、二重起動しない（`test_restart_with_unverifiable_owner_never_starts_a_second_child`）。
  OS identityは`utteran_gui.process_verifier`（observe-only）。実process kill・Windows reparse判定は実機未受入。
- **cutover/rollback**: 旧queue idle時のみ所有権tokenで閉じる。active attemptがあればrollback拒否。
- **GUIプロセス排他（G-09、0.1.49）**: 上記`close_for`は**同一プロセス内**の排他。別GUIプロセスについては、全起動モードが
  `<data root>/gui-instance.lock`のOSロック（POSIX `flock`、Windows `msvcrt.locking`、継承不可、unlinkしない）をAPI構築前に取得し、
  同じrootの2つ目のGUIを拒否する。OSがプロセス終了時（kill含む）に解放するため、次回起動は上記recoveryで安全性を確保する
  （Windowsでは終了から解放までわずかな遅延があり得る）。CLI・旧バイナリ・非協調プロセスは排除しない。
- **開発用root（`--next-ui-v1`）**: v1 DBは専用markerのある開発用data rootにだけ作る。既存データのあるディレクトリは採用・初期化しない。
- **停止時の結果**: `JobRuntime.shutdown()`は`rolled_back`/`not_active`/`active_attempt`/`ownership_lost`/`shutdown_failed`を返す。
  `rolled_back`以外では旧queueを再開せず、GUIロックも明示解放しない（プロセス終了時にOSが解放）。`create_app()`は`activate()`を最後の手順にする。
  試験は合成fixtureのみ（[ui3/g09 §5–6](../ui3/g09-next-ui-v1-startup.md#5-起動失敗停止rollback)）。実DB復元・Windows ACL・secure deleteはG-15等の別ゲート。

## Outbox compaction

7日（`RETENTION_SECONDS`）超かつ最新100,000件より古い、finalかつrecovery不要なresourceの行だけを、
明示activate時に最大50,000行削除し、同一transactionでepoch floorを上げる。floor未満のcursorは
`410 event_cursor_expired`、クライアントは`/sync`からやり直す。diagnosticsはv6 sidecarによりcompaction後も同一。

## Privacy

| 項目 | 現状 |
|---|---|
| 公開DTO | allowlist projection（`utteran_contracts`）。prompt・入力/出力path・token値を含めない |
| HTTP/SSE/sync/diagnostics | 上記を含まないことをE2Eで確認（`test_private_values_stay_in_the_private_db_and_limits_hold`） |
| ログ | 値を含まない固定code（`ControlError`はpath/SQL/payloadを持たない）。tokenは`security.py`でredact |
| private DB | **Prompt・入力path等が平文で保存される。暗号化なし、retention/自動削除なし、完全消去手段なし**（意図的・テストで明示） |
| Windows ACL | 未設定・未受入 |
| 未決定（owner判断） | private DBの保持期間・削除UI・secure delete・暗号化・ACL方針、backupの保持方針 |
