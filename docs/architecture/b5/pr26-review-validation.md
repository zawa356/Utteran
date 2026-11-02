# PR #26 B5レビュー結果・未解決ゲート記録

2026-11-02 JST。[Draft PR #26](https://github.com/zawa356/Utteran/pull/26)がOPEN/Draft、
base `feat/b4-capability-inventory-resolver` @ `f6c2a9546e1ee9b780f8c1a098f7bcdfc7096314`、
head `feat/b5-preset-policy-editing-api` @ `410e39ad4e37876c20f630ef7036cc4ca3cff052`であることを
GitHubで再確認してから記録した。version 0.1.34、未マージ・未リリース。

## レビュー判定

外部レビュー（ChatGPT）がB4 `f6c2a95`との差分としてHEAD `410e39a`を静的に確認した。

- 判定: **未解決ゲートを明示した上でB5コードレビュー受理**。
- 検査したB5変更に新たなrelease-blockingコード欠陥の指摘なし。レビュースレッド/修正要求もなし。
- そのため本記録は**文書のみ**。実装・テスト・schema・CI設定・version/lockは変更しない。
- 静的コード/GitHub Actionsレビューであり、レビュー側の新規ローカル/実機試験ではない。
- CI緑、Windows配布試験済み、merge済み、release済みとは**記録しない**。

受理された範囲: 不変SQLite Preset/Policy revision履歴、tombstone、CAS/ETag、永続idempotency、
snapshot pagination、純粋なEditingSession継承とprovenance、明示opt-inの`/api/v1`設定/検証API。
既存CLI/GUI/API既定経路、B3 Worker、B4 Resolver、既存job/hashは従来経路のまま。

## 確認済みCI・試験の証拠

最終HEAD `410e39a`の[Actions run 41380461146](https://github.com/zawa356/Utteran/actions/runs/41380461146)
をjob/step単位で確認した（最終追補は文書のみで実装は`f7f0f11`と同一）。

| job | 結論 | 内容 |
|---|---|---|
| windows-tests | failure | モデル不要pytest成功（**951 passed / 3 skipped**、失敗0）、PowerShell構文成功、汎用起動段で既知`verify_uninstaller.ps1`のInno Setup前提不足により失敗 |
| contracts-python-compatibility (3.11) | success | **350 passed**、B1/B4/B5 schema check成功 |
| contracts-python-compatibility (3.12) | success | **350 passed**、B1/B4/B5 schema check成功 |
| linux-quality | failure | lock/4 schema/Ruff/format成功、mypyの既知9件（`src/utteran_gui/processes.py`のWindows ctypes属性）で停止、後続pytestはskip |

作成者のローカル基準報告（Linux 943 passed / 既知3 failed / 8 skipped、3.11独立wheel 470 passed）は
[validation.md](validation.md)と[test-comparison.json](test-comparison.json)のとおり。レビュー側は再実行していない。

### 本記録作成時のローカル再実行（補助証拠）

本記録の作成時に、Linux上の`uv sync --extra dev --extra gui --locked`環境（Python 3.11.18）で
HEAD `410e39a`を再実行した。作成者の3.12環境と同一条件ではないため、基準値の置換えには使わない。

| 検査 | 結果 |
|---|---|
| tests/test_b5_settings.py | **73 passed** |
| モデル不要pytest全体 | **944 passed / 2 failed / 8 skipped** |
| failed node | test_gui_processes.pyのprofile manifest依存判定・venv移動判定（既知3件の部分集合） |
| Ruff lint / format | 成功、165 files |
| strict mypy | 93 source files、既知Windows ctypes 9件のみ |
| B1/B3/B4/B5 schema --check | 成功 |
| uv lock --check | 成功 |

既知3件のうち`test_devices.py::test_timeout_kills_probe_process_tree`はこの環境では成功した。
環境依存の既知失敗であり、修正済みとは扱わない。新規失敗はない。

## 未解決のリスク / release gate

レビューで明示された以下は、B5受理後も**未解決のまま保持**する。いずれも本PRで隠さない。

1. **CI未緑**: Linux mypy既知9件、Windows PowerShell起動段のInno Setup前提不足。
   `tests/test_gui_processes.py`のWindows PID-file生成競合は間欠的で、B5とは別に追跡する。
   チェックを無効化・緩和しない。別の是正PRで対応するか、本記録と[validation.md](validation.md)の
   既知失敗記録を維持する。専用IssueはGitHub上に未作成（作成はユーザー指示待ち）。
2. **実機未検証**: Windows実機のACL継承、installer/portable/PyInstaller/WebView2、GPU/native推論は
   B5テストの対象外。mock/CI上のSQLite成功と実受入を区別する（#15、#22で後続確認）。
3. **privacy**: `resources.sqlite3`はprivateなPrompt/設定値を**非暗号化**で保持し、削除revisionと
   replay応答は無期限保持。tombstoneは履歴を安全消去しない。保持期限/消去/backup方針は
   security/privacy release gate前に決定する。決定までデータを無断pruneしない（[security](security.md)、
   [storage](storage.md)）。
4. **opt-in境界**: B5 APIは明示opt-inのみ。通常GUIやjob実行へ暗黙接続しない。
   resolve/実行token、durable queue、job投入は後続Phaseの範囲。
5. **stacked Draft**: B1〜B5はmain未merge。編集・rebase前に毎回GitHub上のSHA、PR base、依存状態を確認する。

## 次のマイルストーン（未着手）

次は**Phase B6 — durable operation store / outbox**
（[migration](../phase-a/migration.md)、[lifecycles](../phase-a/lifecycles.md)）を専用の小さなDraft PRで行う。
B6はB1のみを前提とし、B5とは独立に設計できる。B7はB3/B5/B6を結合するため統合レビューが必要。
ユーザー指示があるまでB6着手、rebase、merge、Issue close、PR状態変更は行わない。

## 文書ゲート

本レビュー記録で再点検した結果は[docs-audit.md](docs-audit.md)の「PR #26レビュー受理時の再点検」に記録。
