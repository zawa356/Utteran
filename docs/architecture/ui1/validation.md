# UI-1 検証記録

更新: 2026-11-04 JST。**記録したpass/failは下記の環境・SHAで実行した結果だけ**。過去のCI成功は流用していない。

## 開始時の確認

| 項目 | 結果 |
|---|---|
| GitHub main | `068e62fce1b35960703b534426adfbe373ff86d8`（PR #34 merge）。`git fetch origin main`で確認 |
| 作業ブランチ | `claude/sharp-pasteur-ret4gy`（実行環境が指定。開始時点で`origin/main`と同一SHA）。指示書例の`docs/ui1-…`名は環境制約で使用せず |
| 新履歴であること | 作業ブランチは書換え後の新履歴（reviewed cutover `e0960bd`がancestor）。PR #23の基点`1097aef`（旧履歴）はHEADのancestorではない |
| 旧履歴の残存 | container内のローカル`main` refだけが旧履歴`1097aef`を指していた（以前のfetch由来）。**ローカルrefのみ`origin/main`へ付け替え**（remote・他branchは不変）。旧履歴は作業基点・merge・cherry-pickに使っていない |
| clone | shallow（depth 50）。履歴系検査は到達範囲で実行 |
| PR #23 | Open / Draft / 未merge、head `94a7e3f`、1ファイル（ADR-001候補）。読み取りのみ |
| ツール | Linux（container）、system Python 3.13.18 / uv venv Python 3.11.18、uv 0.11.32、Node v22.23.4、npm 10.9.9、Playwright同梱Chromium 153.0.8010.12 |
| Windows / WebView2 / pywebview GUI | **この環境に無い**。実行していない |

## UI-1の再現・スパイク

| 検証 | 環境 | 結果 |
|---|---|---|
| `node tools/ui1/legacy_gui_repro.mjs`（2回） | Chromium 153 headless、現行実asset・実CSP、mock `/api/*` | 7シナリオ全て**再現**（2回とも同一）。exit 0 |
| `pytest tests/test_ui1_legacy_sse.py` | 実`create_app`＋stub JobManager | 1 passed, 1 xfailed（strict） |
| スパイク型検査 | TypeScript 6.0.3（vue-tscは3.3.14） | 3案とも0 error |
| スパイク`vite build` | Vite 8.3.5 | 3案とも成功（サイズは[framework-comparison](framework-comparison.md)） |
| スパイクE2E（`tools/ui1/spike/e2e/run.mjs`） | Chromium 153 headless | 3案とも**7/7 pass**。repo内コピーから再install→build→E2Eでも同結果 |
| vue-tsc × TypeScript 7.0.2 | — | 起動失敗（`ERR_PACKAGE_PATH_NOT_EXPORTED: ./lib/tsc`）。TS 6.0.3へ統一 |

## 品質ゲート（作業ブランチ、コミット前のworking tree）

<!-- GATES -->

## CI（push / PR）

<!-- CI -->

## 未検証（実施していない・合格扱いしない）

- Windows WebView2 / pywebviewでの旧GUI再現・スパイク表示・CSP nonce・`EventSource`再接続・D&D・file dialog・DPI。
- 配布版（installer/portable）、SAC/WDAC、GPU・実モデル・実音声。
- スパイクのWebView2上の起動時間・プロセス全体RAM・payload読込時間。
- 依存のSBOM・脆弱性監査（`npm audit`）は未実施（スパイクは本番に入れないため）。本番採用時にUI-2で実施する。

## Windows WebView2 実機での次回試験手順

前提: Windows 10/11、WebView2 Evergreen Runtime、Node 22、Python環境（`uv sync --extra dev --extra gui`）。

1. 旧GUIの観測（再現ではなく発生確認）: GUIを通常起動し、起動直後（環境検出中）に`#profile-select`を変更して
   表示が巻き戻るか（D-01）、ログフォルダの`report_frontend_error`記録に`unhandledrejection`が出るか（D-02/D-05）を確認する。
   個人ファイル・実音声は使わず、合成WAV（`tools/generate_diarization_fixture.py`等）を使う。
2. スパイクのWebView2表示: 各スパイクの`dist/`を、現行と同じCSP（nonce版）でローカル配信する小さなFastAPI app、または
   pywebviewの`webview.create_window(url=...)`で開き、`e2e/run.mjs`と同じ7操作を手動またはWebView2 CDP
   （`--remote-debugging-port`）経由のPlaywright `connectOverCDP`で実行する。
3. 計測: 起動からready表示まで、タスクマネージャのプロセス合計メモリ（msedgewebview2群＋python）、DPI 125/150/200%の
   表示崩れ、日英切替、Tab/Enter/Esc操作。
4. 記録: OS build、WebView2 Runtime版、結果、スクリーンショット（合成データのみ）をこの節に追記する。
