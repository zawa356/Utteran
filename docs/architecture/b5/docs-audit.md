# Phase B5 文書同期監査

2026-11-02 JST。未マージ・未リリース。updated / reviewed-no-change / blockedで点検する。
テスト成功だけでPhase完了としない。このゲートは今後の各Phase・レビュー修正でも再掲する。

| 対象 | 状態 | 確認・理由 |
|---|---|---|
| README.md | updated | 0.1.34、opt-in/API範囲、非暗号化private保存、未接続機能 |
| README.en.md | updated | 日本語と同じversion/機能/制限 |
| 変更履歴.md | updated | 0.1.34、実装/互換性/制限/未検証 |
| AISTATE.md | updated | B4基点/B5 branch、テスト、既知失敗、引継ぎ |
| 要件定義.md | reviewed-no-change | 24.1/25章以降の設定・security/portable/queue要件確認済。実験的opt-in APIのみで確定GUI/CLI仕様を改変しない |
| docs/UI_UX_刷新計画.md | updated | 2026-10-30本文を維持しB5 Draftの現在地を追記 |
| docs/リリース手順.md | updated | 手順変更なし、現在開発versionの事実のみ0.1.34へ同期 |
| pyproject.toml / uv.lock | updated | patch versionのみ、dependency versionは不変、新軽量packageをwheel/mypy対象へ |
| src/utteran/__init__.py | reviewed-no-change | importlib.metadata方式。0.1.34配布metadataで一致を検証 |
| src/utteran_gui/__init__.py | reviewed-no-change | 同上。version literalの重複は追加しない |
| Phase A contracts.md | updated | B5契約差分への追補、過去の本文を維持 |
| Phase A migration.md | updated | B5/T09/T18/T19と未実装T17を区別、設定正本は維持 |
| Phase A openapi.yaml | updated | preview/listの5型をB5 DTO生成物と同期 |
| Phase A examples.json | updated | 既存例を維持し合成B5 preview例を追記 |
| B1 README/schema/DTO | reviewed-no-change | B1契約を正本として再利用。offline artifact bytes一致 |
| B2 README/mapping/compatibility | reviewed-no-change | 空Prompt保持・hash互換は変更せずB5内の差分を記録 |
| B3 worker schema | reviewed-no-change | offline artifact bytes一致、worker実装は不変 |
| B4 README/resolution/observations/pr25-review-validation | reviewed-no-change | 必読・基準/制約確認、resolver/証拠判定/bridgeは不変 |
| B4 capability schema | reviewed-no-change | offline artifact bytes一致 |
| B5 README.md | updated | 対象/責務/DoD/文書ゲートと未実装 |
| B5 storage.md | updated | schema/history/CAS/replay/backup/migration/rollback/保持未解決 |
| B5 inheritance.md | updated | leaf/null/auto/provenance/cross-field/workerと品質の区別 |
| B5 api.md | updated | endpoint/security/HTTP/Phase A差分と移行 |
| B5 compatibility.md | updated | 旧正本/import計画/未接続/lock問題 |
| B5 security.md | updated | private応答・DB・OS権限・未検証 |
| B5 test-comparison.json | updated | baselineと最終JUnitのfailed/error/skipped集合一致・件数を記録 |
| B5 validation.md | updated | baseline/増分/実行コマンド/環境別限界/CI |
| B5 preview.schema.json / exporter | updated | 生成元と生成物を更新、--checkとPhase A一致test |
| .github/workflows/ci.yml | updated | B5 schema checkと3.11/3.12関連テストを追加、既知CI問題は修正しない |
| GitHub #17 / #18 | blocked | Draft PR URL確定後に進捗追記。未mergeのため完了チェックを付けない |
| Draft PR / Actions | blocked | 作成・CI観測後にURL/実測を追記、Ready化しない |

Windows実配布/ACL/GPUの未検証と既知CI障害はvalidation.mdに明示する。
保持期限/完全消去は未解決で、無断pruneを行わない。B6以降には着手しない。
