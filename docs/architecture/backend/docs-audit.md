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

## PR #34レビュー修正での追補（2026-11-03）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `tools/public_history_delta.py` / `tools/public_history_baseline.json` / `tests/test_public_history_delta.py` | **new** | P2: 新規commit全系列のprivacy gate、固定cutover、17件の回帰テスト |
| `.github/workflows/ci.yml` | updated | `privacy-history-delta` jobを追加（12 job）。他jobは不変 |
| `testing-and-operations.md` | updated | 3つのprivacy検査の役割分担、範囲・cutover・将来PRの基準、fail-closedコード、信頼の限界（branch protection / required check / CODEOWNERS） |
| `validation.md` | updated | P3: 68（`b83669c`）と69（`51dc90f`）を別時点の実測として記録し、差の1件を特定。P2の結果。過去の表は注記の追加のみ |
| `AGENTS.md` | updated | 品質ゲートに新規履歴gateを追加 |
| `変更履歴.md` | updated | 0.1.39のCI節に新規履歴gateを1行追加（versionは0.1.39のまま） |
| `README.md` / `README.en.md` / `AISTATE.md` / `要件定義.md` / Phase A・B8・B9文書 | reviewed-no-change | 利用者向け機能・API・version（0.1.39）・DBに変更はない。CI/privacyの正本はbackend文書に集約している |

## PR #34追加レビュー修正（placeholder判定、2026-11-03）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `tools/public_history_scan.py` / `tools/public_history_delta.py` | updated | `_is_placeholder(detector, value)`をdetector別の値全体判定にした。delta gateも同じ関数を使う |
| `tests/test_public_history_scan.py` / `tests/test_public_history_delta.py` | updated | T01〜T11等12件を追加 |
| `tests/test_b6_control.py`、`test_models.py`、`test_private_history_match.py`、`test_acceptance_scenarios.py`、`tools/acceptance/scenarios.py` | updated | 合成値を実行時に組み立てる方式に変えた（値は不変） |
| `AISTATE.md` | updated | 実在のユーザーパス1件を伏せ字にした（現行treeのみ。過去の記録の他の記述は不変） |
| `ユーザー確認事項.md` | updated | `<名前>`の後ろにpath区切りを1文字追加 |
| `testing-and-operations.md` / `validation.md` / `AGENTS.md` / `変更履歴.md` | updated | placeholder基準、計測結果、fixture規則、0.1.39への追補 |
| README日英・要件定義・Phase A/B8/B9文書 | reviewed-no-change | 利用者向け機能・API・version・DBに変更はない |

## リンク検証

`tests/test_docs_links.py`が`AGENTS.md`、`README.md`、`README.en.md`、`docs/architecture/README.md`、
`docs/architecture/backend/*.md`の相対リンク（ファイル存在と見出しanchor）をCIで検査する。
