# Docs audit — Backend Foundation Hardening (2026-11-03)

方針: 過去の監査・検証の事実は書き換えない。現行と食い違う箇所は冒頭/末尾の**追補**と正本へのリンクで誘導する。
重複した仕様の再掲はしない。

| 文書 | 判定 | 理由・変更内容 |
|---|---|---|
| `AGENTS.md` | **new** | 既存の`AGENTS.md`/`CLAUDE.md`/`.claude/`は無かった。読む順・実行境界・品質ゲート・privacyのみ |
| `docs/architecture/README.md` | **new** | 現行正本と歴史資料の区別、優先順位 |
| `docs/architecture/backend/*.md` | **new** | README・components・interfaces・persistence-recovery・testing-and-operations・validation・本書 |
| `README.md` / `README.en.md` | updated | B8/B9見出しの「Draft・未マージ」→「main統合済み・未リリース」。B6節の「Setup/SSE未提供」→「B6単独では未提供、B8/B9で追加（opt-in）」。版0.1.39、正本へのリンク。日英で同内容 |
| `AISTATE.md` | updated | 冒頭に現在地を追加。以前のDraft記録は改変なし |
| `変更履歴.md` | updated | 0.1.39節 |
| `要件定義.md` | updated | B9節にmain統合済みの1行追補のみ |
| `docs/architecture/integration-2026-11-02.md` | updated | B7 PR #32 / B8+B9 PR #33のmerge commit、Pages公開成功済み、#27是正への追補 |
| `docs/architecture/b8/README.md` | updated | 冒頭に「Draft提出時の記録、現在はmain統合済み」 |
| `docs/architecture/b8/storage.md` | updated | **訂正追補**: pre-v1 backupは0.1.38まで実際にはv6のコピーだった（コード修正済み） |
| `docs/architecture/b9/README.md` | updated | 冒頭に統合済み・**現行=v6 `ManagedControlStore`、v5=`EventsControlStore`**の追補。本文表の旧記述は履歴として残す |
| `src/utteran_control/events_repository.py`, `src/utteran_gui/job_runtime.py`（docstring/comment） | updated | 「ManagedControlStore=v5」の古い記述をv6へ |
| `docs/UI_UX_刷新計画.md` | updated | 冒頭に2026-11-03現況（B7〜B9統合済み、U0〜U3未着手）1段落 |
| `docs/architecture/phase-a/migration.md` | updated | 同上の現況1段落 |
| `docs/architecture/phase-a/README.md`, `openapi.yaml` | reviewed-no-change | 提案時の歴史資料として保持。差異は[interfaces.md](interfaces.md#phase-a-openapiとの差異)に集約 |
| `docs/architecture/b1`〜`b7/*` | reviewed-no-change | 提出時の記録。B7は既に統合済み追補あり。現在地は[README.md](README.md) |
| `docs/architecture/b9/{events-sse,sync,retention,diagnostics-legacy,validation}.md` | reviewed-no-change | 仕様は現行と一致（v6はretention.mdに記載済み） |
| `docs/リリース手順.md` | reviewed-no-change | patch versionを作業単位で上げる規則に従い0.1.39とした。手順変更なし |
| `.github/workflows/ci.yml` | updated | 独立job化（[testing-and-operations.md](testing-and-operations.md)） |
| `tools/public_history_scan.py` / `tools/public_scan_reviewed_media.json` | updated / new | `.ts`内容判定、レビュー済み`(path, blob)`、diff記号の誤検出修正 |
| `tools/acceptance/*` | reviewed-no-change | 旧CLI経路の実機受入用。v1の根拠ではないことを[testing-and-operations.md](testing-and-operations.md#既存のtoolsacceptance)に明記 |

## リンク検証

`tests/test_docs_links.py`が`AGENTS.md`、`README.md`、`README.en.md`、`docs/architecture/README.md`、
`docs/architecture/backend/*.md`の相対リンク（ファイル存在と見出しanchor）をCIで検査する。
