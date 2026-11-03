# Phase B9 — Events / Sync / Diagnostics / Legacy Compatibility

> **現在の状態（2026-11-03追補）:** 本書は**Draft提出時の記録**。B9は[PR #33](https://github.com/zawa356/Utteran/pull/33)で
> **main統合済み**（merge `e803ecc3`）・未リリース・opt-in。**現行のruntime storeは layout v6 の`ManagedControlStore`**
> （PR #33レビュー修正B9-P2-2で追加）で、layout v5のclassは`EventsControlStore`。下表の「layout v5 `ManagedControlStore`」
> は提出時点の記述。現行は[backend/persistence-recovery.md](../backend/persistence-recovery.md)。

2026-11-02 JST、開発版 **0.1.38（未リリース）**、**Draft・未マージ**。B8（0.1.37）の後続commit系列として
同一ブランチ・同一Draft PRに含める（[理由](../b8/compatibility.md)）。B8のroute・契約・v4表は書き換えない。

`create_app(..., enable_v1_jobs=True, enable_v1_events=True, ...)`を明示した場合のみ、同じ`/api/v1`
sub-app（B5のsession/Host/Origin middleware）に以下を追加する。既定起動・旧`/api/*`・旧
`/api/wizard/jobs/{id}/events`は不変（route一覧をgolden固定）。

| endpoint | 内容 |
|---|---|
| `GET /events` | SSE。commit済みOutboxだけを`EventEnvelope`（B6 allowlist）で配信 |
| `GET /sync` | resources・queue・inventory revision・event watermarkを**1 read transaction**で取得、snapshot paging |
| `GET /jobs/{id}/diagnostics`、`GET /setup-operations/{id}/diagnostics` | 固定codeとtimestamp/stage/attemptのみ |

| モジュール | 責務 |
|---|---|
| `utteran_contracts.events` / `events_schema` | SyncSnapshot・SyncResource・ResyncRequired・DiagnosticEntry(List)、純粋`EventReducer`、offline schema |
| `utteran_control.events_repository` | `read_sync`・`SyncService`・`diagnostics`・`watermark`、layout v5 `ManagedControlStore`（guarded compaction） |
| `utteran_gui.events_api` | SSE/sync/diagnostics route、`EventStreamConfig`（上限） |

詳細: [events-sse](events-sse.md) / [sync](sync.md) / [retention](retention.md) /
[diagnostics-legacy](diagnostics-legacy.md) / [validation](validation.md) / [docs-audit](docs-audit.md) /
offline schema [`events.schema.json`](events.schema.json)。

## 配信するevent type（証拠があるものだけ）

| type | 配信 | 根拠 |
|---|---|---|
| `state_changed` | する | Outbox行（状態変更と同一transaction）。setupのstep進行は`step_progress` reasonのstate_changed |
| `queue_changed` | する | 同上 |
| `resync_required` | する（制御message、`id:`なし） | cursorが失効した接続済みstreamで送ってから切断 |
| `progress`（stage/fraction） | **しない** | 子processの進捗は永続化していない（stdoutは保存しない）。step単位の進行は`state_changed`＋GETで取得 |
| `inventory_changed` / `fallback` | **しない** | 永続した証拠がない（B4 inventoryはOutboxを持たず、fallbackは実行しない） |

そのためv5 migrationでOutbox列は追加していない。v5はcompactionのためのdelete guardのみ。
