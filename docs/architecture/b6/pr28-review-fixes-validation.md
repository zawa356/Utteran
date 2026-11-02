# PR #28 レビュー修正の検証記録

2026-11-02 JST。開発版0.1.35、Draft・未マージ・未リリース。外部レビュー判定は
「B6の主要実装は概ね妥当。P2完了後に最終承認」。本修正の完了はCI全体合格と同義ではない。

## 開始時の確認

- PR #28: OPEN / Draft、head `a03785704862080c727b623d961d59d326b54ad0`、base
  `feat/b5-preset-policy-editing-api` @ `5abb23d62f5fd061c8f5d88ea25d0c761fb133d2`（指示書と一致、他者の追加commitなし）。
- 同じブランチ`claude/loving-noether-jroqrc`へ追加push。rebase・force push・base変更・Ready化はしていない。

## 修正内容

| ID | 内容 |
|---|---|
| P2 | `set_dispatch`に`IdempotencyScope`を追加し`AcceptedQueue`を返す。Queue応答専用の`queue_replays`表（方式A）、SQLite `user_version` 1→2の追加migration。詳細は[transactions](transactions.md)、[storage](storage.md) |
| P3 | `pyproject.toml`の`description`をリポジトリ概要に合わせて更新（versionは0.1.35のまま、`uv.lock`は変更なし）。Issue #27へBOM検査の既知失敗を追補 |

## 実測（Linux container、Python 3.11.18、uv 0.11.32）

再現コマンドは[validation](validation.md)と同じ（`uv sync --extra dev --extra gui --locked`後に各コマンド）。

| 項目 | 修正前（`a037857`） | 修正後 |
|---|---|---|
| `tests/test_b6_control.py` | 61 passed | **80 passed**（+19、3回連続で安定） |
| `pytest -m "not requires_model"` | 1005 passed / 2 failed / 8 skipped | **1024 passed / 2 failed / 8 skipped** |
| failed/error/skipped node集合 | — | 修正前・B5基点（`5abb23d`）の両方と完全一致、新規0（[比較](pr28-review-test-comparison.json)） |
| mypy | 既知9件 | 同9件（`processes.py`のみ）、新規0 |
| ruff check / format --check | 成功 | 成功 |
| B1/B3/B4/B5/B6 schema `--check` | 成功 | 成功（B6 schemaもbytes不変。`AcceptedQueue`はDTOではない） |
| `uv lock --check` | 成功 | 成功（lock差分なし） |
| `uv build --wheel` | 成功 | 成功。METADATA `Version: 0.1.35`、`Summary:`が新descriptionと一致。テストfixtureはwheelに含まれない |
| 独立wheel venv 3.11.18 / 3.12.3 | 各200 passed | **各220 passed** |
| public_history_scan worktree | 82 findings / blocking 2 | 82 findings / blocking 2（既存音声fixtureのみ、新規blocking 0） |
| check_powershell_bom | rc=1（基点から既存） | rc=1（不変、Issue #27へ追補） |

## 追加テスト（19件）

- 同key同要求の再送で、status/headers/QueueViewが初回と完全一致し`replayed`で区別される。replayでは
  table行・outbox seq・queue revisionが不変。
- 別keyでpaused→runningへ進めた後に初回keyを再送すると、初回のpaused応答を返し、現在のrunningを書き換えない。
- commit後の応答喪失（`after_commit`故障）→別インスタンスで再open→同keyで初回結果が復元され、件数は不変。
- 同keyでexpected_revisionまたはdispatchを変えると409、DB byte不変。古いCASの新keyは412でreplayを作らず、
  後で同keyの正しい要求が受理される。不正dispatch値は422相当。
- no-op: revision・eventは増えずに応答を記録、同keyの再送はreplay。keyなしno-opは従来どおり。
- `replays`と`queue_replays`の間でscopeを共有できない（双方向で409）。
- `before_commit`故障、実`max_page_count`満杯、readonly、busyで孤立行ゼロ（状態だけ・eventだけ・replayだけが残らない）。
- 同key 6 thread・2 processの同時送信で1回だけ適用され、全員が同じ応答を得る。
- `queue_replays`のbodyにPrompt/path/nonce/PID identityが含まれず、QueueView schemaに適合する。
- v1→v2: 修正前実装（`tests/fixtures/b6/store_v1.py`、`a037857`のstoreを凍結したtest専用copy）で作成した
  本物のv1 storeをv2で開くと、全tableの行が不変で、旧Operation replayが同一応答を返し、epoch/eventも不変。
- migration中の`before_commit`故障・SQLITE_FULL・process crashでv1が完全に残る。同時6 openで1回だけmigration。
- v1 header＋v2 table、v1構造欠損は`store_corrupt`、`user_version=3`は`store_schema_unsupported`（byte不変）。
  v2 storeを旧v1コードで開くと`store_schema_unsupported`。

既存のテスト「既定GUI起動でcontrol storeを作らない」「Operation replayの応答喪失」は修正後も成功している。

## GitHub Actions

最終HEADの結果は[validation](validation.md)のCI節とPR本文に記録する。以前のWindows 1011 passed / 4 skipped、
契約3.11/3.12各411 passedは**修正前の記録値**。

## 未検証・残るゲート

- Issue #27（Linux mypy 9件、Inno Setup前提、Windows PID-file競合、PowerShell BOM、未到達のpublic scan step）。
- Windows実機ACL・配布・GPU、実processを使ったrecovery、private DBの保持・消去方針。
- B7（HTTP queue操作でのIdempotency-Key必須化を含む）・main統合は未着手。
