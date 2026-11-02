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
