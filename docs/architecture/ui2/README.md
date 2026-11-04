# UI-2 — 次期GUIのfrontend基盤（S0）とopt-in新UI

更新: 2026-11-04 JST。開発版 **0.1.41（未リリース）**。Issue [#20](https://github.com/zawa356/Utteran/issues/20)
（Epic [#18](https://github.com/zawa356/Utteran/issues/18)）。前段: [UI-1](../ui1/README.md)、
採択: [ADR-003](../../adr/ADR-003-ui-frontend-stack-and-host-boundary.md)（**Accepted**、2026-11-04 owner）。

> **既定のGUIは旧UIのまま。** 新UIは開発者向けの明示flag `utteran-gui --next-ui` のときだけ表示される試験版で、
> 移行済みなのは「実行環境の読み取り表示」と「テーマ・表示言語の設定」の2つの小さな縦切りだけ。
> 文字起こし・キュー・モデル・履歴・セットアップは**未移行**と明示し、操作できるように見せていない。

## 現在地

- 基点: `main` = `0e07e138aae742244ca387468de62701874a31c9`（PR #35 merge、2026-11-04の履歴書換え後の新履歴）。
- 実装: `frontend/`（Vue 3 + TypeScript + Vite + Vuetify 4）、`src/utteran_gui/next_ui.py`（opt-in配信・CSP nonce）、
  `create_app(..., enable_next_ui=False)`、`utteran-gui --next-ui`。build成果物`src/utteran_gui/web-next/`はcommitしない。
- 検証: Vitest（unit/contract）、Playwright（実FastAPI経由のE2E、Linux headless Chromium）、pytest、
  B9 reducerのPython/TypeScript共通fixture、隔離venvへのwheel install検査。CI job `ui2-frontend`。
- **未受入**: Windows WebView2 / pywebview実機（表示・CSP・D&D・file dialog・IME・DPI・起動時間・RAM）、
  installer/portableへの同梱、実GPU・実モデル。Linux Chromiumの成功はWebView2の合格として扱わない。

## 読む順番

| # | 文書 | 内容 |
|---|---|---|
| 1 | [design.md](design.md) | backend境界（flag・route・CSP nonce・session）、frontendの層（host/api/transport/domain/features）、asset packaging |
| 2 | [tests-and-acceptance.md](tests-and-acceptance.md) | 実測結果（SHA・pass/fail/skip・CI run）、受入基準の対応、未受入 |
| 3 | [migration-next.md](migration-next.md) | UI-3への移行順序・旧→新対応表、backend gap、rollback、owner判断の一覧 |
| — | [frontend/README.md](../../../frontend/README.md) | 開発者向け: install・build・test・新UIの起動手順 |

## 使い方（開発者）

```bash
cd frontend && npm ci && npm run build
```

```bash
uv run utteran-gui --next-ui
```

flagなしの`utteran-gui`（`gui.ps1`、installer、portable）は従来どおり旧UIを開く。新UI画面の右上「旧画面を開く」で
同じsessionのまま旧UI（`/`）へ戻れる。

## 正本と優先順位

実コード → offline schema（`docs/architecture/b*/**.schema.json`）→ [backend/](../backend/README.md) →
[UI-1](../ui1/README.md)のowner採択方針 → 歴史資料。`docs/architecture/phase-a/openapi.yaml`は現行APIではない。
