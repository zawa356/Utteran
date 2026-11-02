# B7 文書監査

## 1回目（PR提出直前、2026-11-02）

| ファイル | 判定 | 理由 |
|---|---|---|
| README.md / README.en.md | updated | 0.1.36、B7節（opt-in・限定構成・fail closed・非暗号化）、B6節のB7参照を訂正 |
| 変更履歴.md | updated | 0.1.36（開発版・未リリース、B7 Draft）の追加/互換性・制限 |
| AISTATE.md | updated | B7節を先頭に追加、統合記録の「最新0.1.35」を時点表記へ |
| 要件定義.md | updated | Phase B7節（既定仕様不変、opt-in時の仕様要約） |
| docs/UI_UX_刷新計画.md | updated | 冒頭現況: B7はDraft・frontend未接続、U0〜U3/B8〜B9未着手 |
| docs/リリース手順.md | updated | 現在の開発version 0.1.36の事実のみ。手順不変 |
| docs/architecture/integration-2026-11-02.md | updated | Pages公開成功（run 41394884715、HTTP 200）を現在地として追補、favicon 404は別件、B7着手を追記。旧記述は履歴として保持 |
| site/README.md | updated | Deployment statusを公開成功へ。旧404記録は履歴として保持 |
| docs/architecture/phase-a/contracts.md | updated | B7追補（実装endpoint、公開Jobの差異へのリンク） |
| docs/architecture/phase-a/migration.md | updated | 冒頭現況にB7 Draftと対応テスト範囲 |
| docs/architecture/phase-a/openapi.yaml / examples.json / lifecycles.md / README.md / review.md / audit.md | reviewed-no-change | Phase A提案の正本として維持。B7の意図的差異は`b7/baseline-and-scope.md`に一覧化（OpenAPIを実装へ合わせて書換えない） |
| docs/architecture/b3/* | reviewed-no-change | worker契約・対応範囲は不変（`on_spawn`は既定Noneの追加引数、B7資料に記載） |
| docs/architecture/b4/* | reviewed-no-change | 型・recipe・resolverは不変。bridge `resolve` actionはB7資料に記載 |
| docs/architecture/b5/* | reviewed-no-change | API・保存・継承は不変（PATCHのOrigin判定追加はB5 routeに影響なし、B7 security記載） |
| docs/architecture/b6/README.md | updated | B7がv3へ拡張しB6はv2のままである旨の追補 |
| docs/architecture/b6/control.schema.json | updated | `ReasonCode`に`user_reorder`追加の再生成 |
| docs/architecture/b6/その他 | reviewed-no-change | 当時の検証記録（履歴） |
| docs/architecture/b7/* | updated（新規） | README、baseline-and-scope、job-snapshot、admission-resolution、queue-and-transaction、dispatch-recovery、compatibility、security、validation、docs-audit、jobs.schema.json |
| pyproject.toml / uv.lock | updated | version 0.1.36、lockはroot version行のみ。依存追加なし |
| .github/workflows/ci.yml | updated | B7 schema `--check`、契約matrixへB7テスト追加 |
| site/src（ページ本文） | reviewed-no-change | B7は未リリースの実験機能のため製品サイトに掲載しない。README/Websiteリンク追加も見送り（公開成功は確認済み） |
| GitHub Issue #17 / #18 | updated（コメント） | B7進捗・未検証事項を事実ベースで追記。closeしない |
| GitHub Issue #27 | reviewed-no-change | CI改善トラックとして維持。B7で新規失敗なし |

## 2回目（レビュー修正後）

未実施（レビュー待ち）。修正時に同表を再監査して追記する。
