# B7 検証記録

2026-11-02 JST、Linux（container、Python 3.12.13 / 3.11）。Windows・GPU・実モデル推論は未実施。

## 実測結果

| 検査 | baseline（main `3fd187d`） | B7 HEAD |
|---|---|---|
| `pytest -m "not requires_model"`（3.12） | 1024 passed / 2 failed / 8 skipped | **1100 passed / 2 failed / 8 skipped** |
| failed node集合 | `test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`、`...::test_profile_manifest_detects_venv_move_without_disabling_profile` | **同一2件（新規失敗0）** |
| skipped | 8（Windows専用5、git-filter-repo 1、PowerShell 2） | 同一8件（B7のLinux専用テストはLinuxで実行） |
| 契約matrix（CIと同じファイル群＋B7 2ファイル、3.11） | 430 passed | **506 passed**（=430+B7 76） |
| B7テスト（`test_b7_jobs.py` 49関数・`test_b7_api.py` 8関数、parametrize展開で76件） | — | 76 passed |
| B6 `test_b6_control.py`（store hook化後、無改変） | 80 passed | 80 passed |
| mypy | `processes.py`既知9件 | 同一9件（新規0） |
| Ruff check / format | — | 成功 |
| offline schema `--check`（B1/B3/B4/B5/B6/B7） | — | 成功（B6はreason `user_reorder`追加で再生成） |
| `uv lock --check` | — | 成功（root version行のみ0.1.36へ） |
| PowerShell BOM | `scripts/verify_uninstaller.ps1`既知失敗（#27） | 同一（B7はps1を変更しない） |
| public_history_scan worktree | blocking 3（既存の`site/src/content.config.ts`と2つの`.wav` fixture） | 同一3件（B7追加分の新規0） |
| wheel build | — | 成功。`utteran_contracts/jobs.py`・`job_schema.py`、`utteran_control/job_repository.py`、`utteran_gui/job_*`・`jobs_api.py`・`process_verifier.py`を同梱 |

Windows Actions・契約3.12 matrixはPRのCIで計測し、PR本文に追記する（ここではローカル結果のみ）。

## Phase A migrationテスト項目との対応

| ID | B7でカバーした内容 | 未カバー |
|---|---|---|
| T10 immutable submit | snapshot凍結・DBトリガー不変・digest改ざん検出、B5新revision後も凍結値、inventory/model/route変化で`resolution_stale`、pre-start失敗でblocked・再選択なし、1件不正で全件0件 | VM |
| T11 queue race | queue revision CAS、順列の重複/欠落/外部ID 422・active 409・412、no-op、reorder/hold/dispatch並行（3 thread）でCAS勝者1・active≤1、paused中は新規dispatchなし、held/blocked両立 | 2 client実ブラウザ |
| T12 idempotency | commit前crash（2点）で全表不変、commit後応答喪失→同JobBatch/ETag/Location、restart後・別process 2本・6 thread再送で重複なし、同key別body 409、Operation replayとscope非共有 | — |
| T13 cancel race | 実行中cancel→202 cancelling・lease保持→cancelled、cancel先行時は成功でもcancelled・result未記録、completion先行は200 succeeded、waiting即cancel | GenAI実機・force cancel |
| T16 recovery | 実Linux child/owner processで dead owner×(launch failed / intended / child gone / child alive)、PID reuse（start identity不一致）、unknown、exit未確認でslot保持、stale fencing拒否 | Windows実機・VM kill |
| T18 privacy | sentinel Prompt/path/model dir/ファイル名が公開表・全replay表・Outbox・Job/JobList・ログ・HTTP応答/エラーに0、401/403/CSRF、forged/base64 token、重複key/invalid UTF-8 | packaging |
| T19 compatibility | 既定`create_app`でDB・B7 route・core/heavy import無し、旧`/api/queue`、B5 API同居、旧テスト全通過、v1/v2→v3 migration、B6コードはv3拒否 | CLI golden（既存`test_application_golden.py`は通過、B7による変更なし）・Windows VM |

## 故障注入・実process

- `_fault("batch_before_replay"|"before_commit"|"after_commit")`、migration中`before_commit`。
- 実子process: FakeRunnerが`python -c`のchildを起動し、`on_spawn`でPID+`linux:<boot>:<starttime>`を記録。
  `keep_alive`で終了未観測→`termination_failed`のslot保持。OS verifierはalive / pid_reused / unknown / gone。
- Windows: `process_verifier`のctypes実装とreparse判定はWindows CIのpytestでのみ実行され、
  **実機（ACL・Job Object・portable・installer・GPU・長時間推論）は未検証**。

## 合成evidenceの注意

テストのready候補はB4 pure evaluator/resolverに**合成observation**を与えたもの。実推論・実smokeではない。
