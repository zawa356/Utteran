# B9 文書監査（2026-11-02、B9 commit系列の最後）

| ファイル | 判定 | 理由 |
|---|---|---|
| README.md / README.en.md | updated | 0.1.38、B9節（opt-in・未配信type・UI未接続） |
| 変更履歴.md | updated | 0.1.38追加・互換性/制限 |
| AISTATE.md | updated | B9節 |
| 要件定義.md | updated | Phase B9節 |
| docs/UI_UX_刷新計画.md | updated | B9 Draft・frontend未接続 |
| docs/リリース手順.md | updated | 現在の開発version 0.1.38のみ |
| docs/architecture/phase-a/contracts.md / migration.md | updated | B9追補（差異へのリンク） |
| docs/architecture/phase-a/openapi.yaml / examples.json / lifecycles.md | reviewed-no-change | 歴史的仕様案。EventPayload stage/fraction・SyncSnapshot inventory_revision（必須int）との差異はb9/README・sync.mdに記載。wire正本は`b9/events.schema.json` |
| docs/architecture/b6/* | reviewed-no-change | Outbox/`read_events`の意味論は不変（v5でDELETE guardのみ追加、B9 retentionに記載） |
| docs/architecture/b7/* | reviewed-no-change | B8で追加した統合済み追補のまま |
| docs/architecture/b8/storage.md | updated | Runtimeがv5を開く旨の追補 |
| docs/architecture/b8/その他 | reviewed-no-change | B8の内容は不変 |
| docs/architecture/b9/* | updated（新規） | README、events-sse、sync、retention、diagnostics-legacy、validation、docs-audit、events.schema.json |
| pyproject.toml / uv.lock | updated | 0.1.38、root version行のみ。依存追加なし |
| .github/workflows/ci.yml | updated | B9 schema `--check`、契約matrixへB9テスト |
| tests/fixtures/b9_default_routes.json | updated（新規） | 既定app route golden（main時点から生成） |
| site/src | reviewed-no-change | 未リリース機能を宣伝しない |

## 2回目（PR #33レビュー修正後、2026-11-02）

| ファイル | 判定 | 理由 |
|---|---|---|
| b9/retention.md | updated | layout v6・sidecar・backfill（lostは推測しない）・旧クラス拒否 |
| b9/diagnostics-legacy.md | updated | 固定snapshot paging、`d2` cursor、409/400/503、単一プロセス制約 |
| b9/sync.md | updated | `/sync`不変、`d2`との関係のみ追補 |
| b9/validation.md | updated | 修正前再現・追加テスト・実測（追補節） |
| b9/events.schema.json | updated | `DiagnosticEntryList.next_cursor` maxLength 64→200 |
| b9/README.md / events-sse.md | reviewed-no-change | SSE・event type・cursorの意味は不変 |
| phase-a/contracts.md | updated | diagnostics cursor/errorの追補 |
| phase-a/openapi.yaml | reviewed-no-change | 歴史的仕様案（`DiagnosticEntryList.next_cursor`は任意文字列・長さ制約なし）で今回の挙動と矛盾しない。wire正本は`events.schema.json` |
| 変更履歴.md / AISTATE.md / 要件定義.md | updated | 0.1.38内のレビュー修正、diagnostics pagingの仕様 |
| README.md / README.en.md | reviewed-no-change | 機能範囲・opt-in・versionは不変。DB layout番号はREADMEに記載していない |
| docs/UI_UX_刷新計画.md / docs/リリース手順.md | reviewed-no-change | UI接続状況・versionは不変 |
| .github/workflows/ci.yml | updated | 契約matrixに`test_b8_review_fixes.py`・`test_b9_review_fixes.py`を追加 |

## 3回目（Issue #37 epoch跨ぎ`queue_revision`、2026-11-11、0.1.48）

| ファイル | 判定 | 理由 |
|---|---|---|
| src/utteran_contracts/events.py | updated | `apply_sync()`の別epoch分岐でqueue revisionも再初期化、docstringにepochごとの有効範囲 |
| frontend/src/transport/reducer.ts | updated | 同等の修正、旧コメント（「resetしない」）を訂正 |
| frontend/src/test/fixtures/b9-reducer-cases.json | updated | Issue #37の2ケースを追加（既存ケース無変更） |
| tests/test_b9_events.py / frontend/src/transport/sync.test.ts | updated | 直接回帰・SyncCoordinator復旧の回帰 |
| b9/sync.md / validation.md / docs-audit.md | updated | epochとrevisionの有効範囲、Red/Green記録 |
| b9/README.md / events-sse.md / retention.md / diagnostics-legacy.md | reviewed-no-change | SSE・cursor・retention・wire形式は不変 |
| b9/events.schema.json | reviewed-no-change | 型・schemaは不変（`--check`成功） |
| frontend/src/transport/sync.ts | reviewed-no-change | 既存の世代ガード（`AbortController`＋要求参照・`source`一致）で十分なことを回帰試験で確認 |
| src/utteran_control/*（store・events_repository） | reviewed-no-change | `/sync`は1 read transaction・ページ間固定・`rotate_epoch()`は不変 |
| ui2/migration-next.md / ui3/design-freeze.md / ui3/README.md / ui3/review-and-acceptance.md | updated | #37解消と最新のowner方針（新UIはv1が正規の接続先）を追補。歴史記録は削除しない |
| AISTATE.md / 変更履歴.md / README.md / README.en.md / pyproject.toml / uv.lock | updated | 0.1.48（root version行のみ） |
| docs/architecture/README.md | updated | B9・UI-3の現在地への1行追補 |
| docs/リリース手順.md | reviewed-no-change | 版の規則は不変（版番号を記載していない） |
