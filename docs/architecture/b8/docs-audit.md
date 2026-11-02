# B8 文書監査（2026-11-02、B8 commit系列の最後）

| ファイル | 判定 | 理由 |
|---|---|---|
| README.md / README.en.md | updated | 0.1.37、B8節（opt-in・実行subset・B4 readyへ非昇格・未検証）、B7節を「main統合済み」へ |
| 変更履歴.md | updated | 0.1.37追加・互換性/制限、0.1.36見出しに統合済み追補（当時の本文は保持） |
| AISTATE.md | updated | B8節（単一PRの理由、実行可能範囲）、B7統合済み追補 |
| 要件定義.md | updated | Phase B8節、B7見出しに統合済み |
| docs/UI_UX_刷新計画.md | updated | B7統合済み・B8 Draft・frontend未接続・U0〜U3未着手 |
| docs/リリース手順.md | updated | 現在の開発version 0.1.37のみ。手順不変 |
| docs/architecture/integration-2026-11-02.md | updated | B7統合（`4fbb4d1`）とB8着手 |
| docs/architecture/phase-a/contracts.md / lifecycles.md / migration.md | updated | B8追補（差異へのリンク、setup cancel遷移追加） |
| docs/architecture/phase-a/openapi.yaml / examples.json | reviewed-no-change | 歴史的仕様案として維持。wire正本は`b8/setup.schema.json`、差分は`b8/contracts.md` |
| docs/architecture/b6/control.schema.json / b7/jobs.schema.json | updated | `ReasonCode` enum追加の再生成のみ |
| docs/architecture/b6/README.md・その他 | reviewed-no-change | 当時の記録。v4はB8資料に記載 |
| docs/architecture/b7/README.md | updated | **マージ済み現況追補**のみ追加（当時の証拠記録は改変しない） |
| docs/architecture/b7/その他 | reviewed-no-change | 提出時の記録 |
| docs/architecture/b8/* | updated（新規） | README、contracts、storage、api、execution-recovery、security、compatibility、validation、docs-audit、setup.schema.json |
| pyproject.toml / uv.lock | updated | 0.1.37、lockはroot version行のみ。依存追加なし（ADR不要） |
| .github/workflows/ci.yml | updated | B8 schema `--check`、契約matrixへB8テスト |
| site/src | reviewed-no-change | 未リリースの実験機能のため製品サイトで宣伝しない。favicon 404は別件 |
