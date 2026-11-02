# Phase B6 文書同期監査

2026-11-02 JST。Draft・未マージ・未リリース。updated / reviewed-no-change / blockedで点検する。
テスト成功だけでPhase完了としない。このゲートはPR提出直前とレビュー修正後の両方で再実施し、
今後の各Phaseへ継承する。

## PR提出直前の監査

| 対象 | 状態 | 確認・理由 |
|---|---|---|
| README.md | updated | 0.1.35、B6節（既定未使用、B7〜B9未実装、exactly-once非保証、非暗号化） |
| README.en.md | updated | 日本語と同じversion・機能・制限・未merge/未release |
| 変更履歴.md | updated | 0.1.35節（追加/互換性・制限）。「開発版・未リリース」と明記 |
| AISTATE.md | updated | 基点/branch/PR、実装、検証、既知失敗、復旧未検証、次工程 |
| 要件定義.md | reviewed-no-change | 31.4「キューはGUI process内のみ・永続化しない」は現行確定仕様のまま有効。B6は既定未使用の基盤でGUI挙動を変えないため改変しない。切替時（B7）に改訂要 |
| docs/UI_UX_刷新計画.md | updated | 2026-10-30本文維持、B6 Draftの現在地とU0〜U3未着手を追記 |
| docs/リリース手順.md | updated | 手順不変、現在開発version 0.1.35の事実のみ同期 |
| Phase A contracts.md | updated | B6追補（内部実装のみ、events/sync/jobs endpoint未実装） |
| Phase A lifecycles.md | updated | B6追補（保存側のみ実装、実dispatch等未実装） |
| Phase A migration.md | updated | B6追補（旧queue不変、T14/T16はunit範囲のみ、切替手順未着手） |
| Phase A openapi.yaml | reviewed-no-change | B6はHTTPを追加しない。EventEnvelope/SyncSnapshotはB9仕様のまま。差分はB6 compatibilityに記録 |
| Phase A examples.json | reviewed-no-change | HTTP例は不変。B6内部例は`b6/examples.json`へ分離 |
| B1 README/schema/DTO | reviewed-no-change | Contract/VersionedContract規則を再利用、`contracts.schema.json` bytes不変 |
| B2資料 | reviewed-no-change | core adapter・hash互換に影響なし |
| B3 worker schema/資料 | reviewed-no-change | worker不変、`worker.schema.json` bytes不変 |
| B4資料/capability schema | reviewed-no-change | resolver不変、`capabilities.schema.json` bytes不変 |
| B5 README/storage/security/api/docs-audit/pr26-review-validation | reviewed-no-change | 必読として確認。B5 storeとの分離（別DB/secret）をB6 storageに記録、B5本文は変更不要 |
| B5 preview schema | reviewed-no-change | bytes不変 |
| B6 README/storage/transactions/outbox/recovery/security/compatibility/validation | updated | 新規作成 |
| B6 control.schema.json / examples.json / exporter | updated | 新規、`--check`とschema適合testあり |
| B6 test-comparison.json | updated | baselineと最終のfailed/error/skipped集合 |
| pyproject.toml / uv.lock | updated | version 0.1.35、wheel/mypy対象に`utteran_control`。lockはroot version行のみ |
| src/utteran/__init__.py / src/utteran_gui/__init__.py | reviewed-no-change | `importlib.metadata`方式。数字の重複記載を追加しない |
| .github/workflows/ci.yml | updated | B6 schema checkと3.11/3.12契約jobへB6 test追加。既知CI問題は修正しない |
| GitHub Issue #17 / #18 | updated | 本文は変更せずコメントで追記（#17: issuecomment-6569356401、#18: issuecomment-6569357125）。B6 Draft・検証・レビュー待ち、B7/B8/B9未実装。完了チェック・closeはしない |
| GitHub Issue #27 | reviewed-no-change | リンク記録のみ。本PRでCI改善を行ったとは主張しない |
| Draft PR #28 / Actions | updated | base B5、Draft。run 41390499258の実測をvalidation.mdとPR本文へ記録（Windows 1011/4skip、3.11/3.12各411、workflowは既知課題で赤） |
| 全体品質gate（Linux mypy9/pytest2、Windows Inno Setup、PID競合、BOM、既存fixture scan） | blocked | baselineから存在する既知課題、#27の範囲。B6新規回帰なし |
| Windows ACL/実配布/実process recovery | blocked | 未検証。B7（実process）・U3（配布/VM）で確認 |
| 保持期限・完全消去・暗号化 | blocked | 方針未決（レビュー事項）。B6はGCせず既存private履歴を削除しない |

## PR #28レビュー修正時の再監査（2026-11-02）

修正前HEAD `a037857`。P2（Queue dispatchの永続冪等性、`user_version` 1→2）とP3（description、Issue #27追補）を反映。

| 対象 | 状態 | 確認・理由 |
|---|---|---|
| README.md / README.en.md | reviewed-no-change | 冒頭の製品説明（desktop app/CLI）は新descriptionと一致。B6節の「既定未使用・B7〜B9未実装・exactly-once非保証・非暗号化」は修正後も正確。version 0.1.35不変 |
| 変更履歴.md | updated | 0.1.35開発版内に「修正（PR #28レビュー）」を追記。リリース済みとは書かない |
| AISTATE.md | updated | 修正HEAD、結果、残るゲート、次の統合判断 |
| 要件定義.md | reviewed-no-change | 31.4（キュー非永続）は既定動作として現行のまま。B6は既定未使用 |
| docs/リリース手順.md / docs/UI_UX_刷新計画.md | reviewed-no-change | version・手順・現在地に変化なし |
| Phase A contracts.md | updated | B6追補にQueue dispatch replayを含むことを追記 |
| Phase A lifecycles.md / migration.md / openapi.yaml / examples.json | reviewed-no-change | 状態遷移・移行計画・HTTP仕様に変更なし |
| B6 transactions.md | updated | Queue replayのscope・判定順・原子性・no-op・scope非共有・B7でのkey必須 |
| B6 storage.md | updated | `queue_replays`、`user_version=2`、方式Aの選定理由、migration/失敗/rollback/退避条件 |
| B6 compatibility.md | updated | Operation replay互換、Queue更新の冪等性、SQLite layout、B7拡張点 |
| B6 validation.md | updated | 修正後実測への参照（上表は修正前の記録値と明記） |
| B6 pr28-review-fixes-validation.md / pr28-review-test-comparison.json | updated | 新規。実測・追加テスト・未検証 |
| B6 README.md | updated | 資料一覧と冪等性の範囲 |
| B6 outbox.md | updated | 書込み飽和時に読取が`store_busy`（再試行可能）になり得ること（Windows CIで観測） |
| B6 control.schema.json / examples.json | reviewed-no-change | `AcceptedQueue`はDTOではなくschemaのbytes不変（`--check`成功）。例は引き続き適合 |
| B1〜B5 schema/資料 | reviewed-no-change | bytes不変、変更なし |
| pyproject.toml | updated | descriptionのみ。version 0.1.35維持 |
| uv.lock | reviewed-no-change | descriptionはlockに含まれず差分なし。`uv lock --check`成功 |
| tests/fixtures/b6/store_v1.py | updated | 新規。v1 store作成用に`a037857`のstoreを凍結したtest専用copy。wheelには含まれない |
| PR #28本文 | updated | 「Review fixes」節 |
| Issue #27 | updated | BOM検査の既知失敗と未到達stepをコメントで追補（issuecomment-6569607027）。本文・チェック・closeは変更なし |
| Issue #17 / #18 | updated | 修正内容とレビュー待ちをコメントで追記（差分のみ） |
| 全体品質gate（#27の各項目） | blocked | 基点から存在する既知課題。本修正で新規失敗なし |
| Windows ACL/実配布/実process recovery/保持・消去方針 | blocked | 未検証・未決のまま |
