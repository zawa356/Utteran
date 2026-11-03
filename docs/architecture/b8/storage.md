# B8 永続化（SQLite layout v4、`SetupControlStore`）

## version

| version | 内容 | 開ける実装 |
|---|---|---|
| 1/2 | B6 | B6 `ControlStore`（v2） |
| 3 | B7 Job | B7 `JobControlStore` |
| 4 | +setup 8表 | `SetupControlStore`のみ。**B6/B7コードはv4を`store_schema_unsupported`で拒否**（テスト済み） |

v1/v2/v3→v4は既存の`_migrate`で**1つの`BEGIN IMMEDIATE`内で段階適用**。commit前の故障・disk full・
readonlyでは旧versionのまま（`PRAGMA user_version`はDDLと同じtransactionで更新、推測で書き換えない）。
旧headerなのにv4表がある・未知version（5等）はfail closed。

**後退不可**: v4へmigrationしたDBはB7単体（`enable_v1_setup=False`）でも開けない。PRをrevertした場合は
activate時のbackup（`operations-pre-v1-*.sqlite3`、v3のまま）から戻す必要がある。setupを一度有効にした
環境ではjobs-only運用へ自動で戻らない（fail closedで誤読しないことを優先）。

> B9追補: `JobRuntime(setup=...)`はB9からlayout v5（[retention](../b9/retention.md)、v4＋outbox compaction guard）を開く。
> v4ファイルは1 transactionでv5へ移行し、v4のクラス（`SetupControlStore`）はv5を拒否する。

> PR #33レビュー修正追補: Runtimeが開くlayoutは**v6**（v5＋診断用attempt sidecar、[B9 retention](../b9/retention.md)）。
> v1〜v5は1 transactionでv6へ移行し、v5を開いていた旧クラス（`EventsControlStore`）・B6/B7/B8クラスはv6を拒否する。
> 移行前のDBはactivate時のbackup（`operations-pre-v1-*.sqlite3`）で保全され、DBの削除・初期化・downgradeはしない。

> **2026-11-03訂正（Backend Foundation Hardening、0.1.39）:** 0.1.38までの実装はstoreを構築（=migration）した**後**に
> backupしていたため、上記の`operations-pre-v1-*.sqlite3`は実際には**v6のコピー**で、「v3のまま戻す」ことはできなかった。
> 0.1.39で`ControlStore.backup_before_open()`によりmigration前のlayoutのままbackupするよう修正した。現行の正本は
> [backend/persistence-recovery.md](../backend/persistence-recovery.md#backup-と-restore)。上の記述は提出時の設計意図として残す。

## 表（追加分）

| 表 | 用途 | 不変性 |
|---|---|---|
| `setup_inventory` | store内observation counter（revision、最新fingerprint） | mutable singleton |
| `setup_plans` | hash済み本文・plan_hash・state/revision・期限 | 本文/hash/期限はUPDATE禁止trigger、DELETE禁止（discardはtombstone） |
| `setup_approvals` | plan_revision/hash束縛・同意ID・期限・消費marker | DELETE禁止。consumed_at/operation_idは同時に設定（CHECK） |
| `setup_operations` | operation↔plan link、approval、retry_of | UPDATE/DELETE禁止、初回operationはapproval一意（部分UNIQUE） |
| `setup_state` | error_code/error_step/waiting_reason/last_fingerprint（固定語彙） | mutable |
| `setup_steps` | 公開StepProgress（status/reason/evidence kind/時刻） | mutable（固定語彙） |
| `setup_step_journal` | private: intent/launched/launch_failed/exited/result、child PID・start identity・exit code | UPDATE/DELETE禁止 |
| `setup_replays` | Plan/Approval/SetupOperation応答（kind列） | UPDATE/DELETE禁止 |

既存の`operations / queue_items / attempts / attempt_private / leases / outbox / operation_history`を
そのまま正本として使い、setup用の二重queue・二重leaseは作らない。`operation_private`はplan hashの
markerのみ。**秘密キー・credential値は保存しない**（tokenは保存経路がなく、sentinel testでDB dumpに不在）。

## 1 transactionの単位

- plan作成: replay再照合 → observation counter → plan → replay。
- 承認: replay → CAS（plan revision）→ 期限 → state → hash → fingerprint → 同意 → credential有無 → approval・plan revision → replay。
- 開始: replay → plan期限/state/revision → approval（plan/revision一致、未消費、期限、hash）→ fingerprint →
  operation・history・Outbox（state_changed）・queue item・queue revision・queue_changed・step行・approval消費・plan consumed → replay。
- step進行・終端・waitingはfenced（lease fencing token一致）で、状態・history・Outboxを同transaction。

## 一覧

`GET /setup-operations`はB7 `GET /jobs`と同じ**プロセス内snapshot paging**（1ページ目を1 read transactionで
materialize、TTL 300秒・16件・上限あり）。cursorは`s2.*`でHMAC束縛し、B7の`j2`とは互換なし（相互転用は400）。
再起動・evictは409 `cursor_expired`、上限超過は503（黙って欠落させない）。
