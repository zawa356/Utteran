# Phase B7 — Immutable Job Submission / Durable Queue Controls / Safe Dispatch

2026-11-02 JST、開発版 **0.1.36（未リリース）**、**Draft・未マージ**。
基点: `main` `3fd187dff64e83291253bd0d0bf4ba74f043664a`（着手時にremote再確認、以後のcommitなし）。

B1〜B6の契約・Worker・Resolver・Preset/Policy・SQLite control storeを、**実行前に完全検証された
Jobの不変snapshot、原子的なbatch受理、Queueの安全な制御、単一dispatch、停止・復旧**として接続する。
**既定のGUI/CLIは変えない**。v1 Job APIは`create_app(..., enable_v1_settings=True,
enable_v1_jobs=True, v1_authority=...)`を明示した場合のみ有効（`app.py`の通常起動は有効化しない）。

## 責務とモジュール

| モジュール | 責務 |
|---|---|
| `utteran_contracts.jobs` / `job_schema` | private `JobSnapshot`・`ResolvedSpec`・`InputIdentity`、typed request、allowlist公開型、offline schema |
| `utteran_control.store`（変更） | version/replay/dispatch guard用のhook（B6既定はv2のまま） |
| `utteran_control.job_repository` | layout v3、v1/v2→v3 migration、all-or-nothing batch、queue CAS、cancel/retry/revalidate、fenced終端 |
| `utteran.capabilities.bridge`（追加action） | profile内で`refresh`+`resolve`、ready候補のローカルモデルdirをprivate返却 |
| `utteran_gui.capability_inventory`（追加） | `resolve()`、scope再検査、固定reason |
| `utteran_gui.job_admission` | B5継承・入力identity・B4解決・短命token・提出時freeze proof・pre-start再検査 |
| `utteran_gui.job_dispatcher` | 1 heavy slot、launch journal、B3 worker、成果物/入力照合、fenced終端 |
| `utteran_gui.process_verifier` | 観測専用OS verifier（Linux `/proc`、Windows creation time） |
| `utteran_gui.job_runtime` | 明示cutover/rollback、旧Queueとの単一writer排他、backup、recovery |
| `utteran_gui.jobs_api` | 既存B5 v1 sub-appへのroute追加（mountは1個）、固定エラー |

詳細: [baseline-and-scope](baseline-and-scope.md) / [job-snapshot](job-snapshot.md) /
[admission-resolution](admission-resolution.md) / [queue-and-transaction](queue-and-transaction.md) /
[dispatch-recovery](dispatch-recovery.md) / [compatibility](compatibility.md) /
[security](security.md) / [validation](validation.md) / [docs-audit](docs-audit.md) /
offline schema [`jobs.schema.json`](jobs.schema.json)。

## 実装したもの / 意図的に実装しないもの

- 実装: `POST /execution-resolutions`、`GET/POST /jobs`、`GET /jobs/{id}`、`POST /jobs/{id}/
  {cancel,retry,rerun,revalidate}`、`GET /queue`、`PUT /queue/order`、`PATCH /queue/control`、
  `PATCH /queue/items/{id}`（すべて`/api/v1`配下、opt-in時のみ）。
- **実行可能構成はB3 workerの限定構成のみ**: recipe `fw-cpu`/`fw-cuda`、execution path `worker`、
  明示ローカル完全モデル、話者分離なし、glossary/実験引数なし、単一file。B4で`ready`かつ上記を満たす
  場合だけ受理する。whisper.cpp / OpenVINO GenAI / pyannote / glossary / 実験引数は
  **受理時に宣言的に拒否**し、旧CLIへ自動転送しない。
- 標準collectorは実機smokeを生成しないため、**実環境では通常ready候補が0件となり、受理は409
  `execution_unavailable`**になる。これは正常な fail closed であり、smoke providerは別Phase。
- fallback: preapproved候補は`fallback_specs`として**凍結記録のみ**。B7は実行しない。
- 未実装: `/jobs/{id}/diagnostics`、B8 Setup、B9 `/events`・`/sync`・Outbox compaction、UI刷新、
  force cancel（409 `cancel_mode_unsupported`）、旧Queueデータ移行、本番自動有効化。

## 3つの誤推論を避けるための前提

1. Job APIがある ≠ 全backendで安全にdispatchできる（上記限定構成のみ）。
2. SQLiteに保存できた ≠ クラッシュ後に必ず再開できる（不明は`recovery_required`で占有保持、自動resumeなし）。
3. worker exit 0 ≠ 成果物が正しい（B3 `verify_result`＋入力identity再照合後のみ`succeeded`）。
