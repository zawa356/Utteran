# UI-3-0 レビュー資料・試験結果・ownerへの確認事項・引継ぎ

更新: 2026-11-06 JST（最終判定追補。旧試験記録は保存、0.1.43）。基点 `main` = `dd115085c08b550c4c8d8a202b25952e90b36bd9`（PR #36 merge、`git fetch`で確認、先行変更なし）。
CI（GitHub Actions）の結果はDraft PRのchecksを正本とする（この文書へ追記するcommitでCIを再起動し続けないため）。

> **第3イテレーション（2026-11-05）の画像一覧・試験結果・確認事項は[iteration-3 §9〜§11](iteration-3.md#9-レビュー用スクリーンショット)。**
> 第2イテレーションは[iteration-2 §5〜§8](iteration-2.md#5-レビュー用スクリーンショット生成方法と一覧)。
> 以下の§2〜§4は第1案（A1/C1）の記録として残す（画像名・件数は第1案時点）。実行方法（§1）は第2案でも同じ。

## 0. 最終受入（2026-11-06）

**UI-3-0の第5案をDesign Freezeとして承認。** ownerはWindowsのブラウザで主要画面を概観した。微細なUI改修は後続S1〜S5に繰り越す。[PR #38](https://github.com/zawa356/Utteran/pull/38)（最終`1f72f28`）はmerge commit `701a94e`でmainに統合済み。

- 最新の正本: [design-freeze.md](design-freeze.md)。第5案とレビューP1修正は[iteration-5.md](iteration-5.md)。
- Vitest **221 passed**、UI-3 E2E **96 passed**、UI-2 E2E **10 passed**。PR HEAD CI [42170336857](https://github.com/zawa356/Utteran/actions/runs/42170336857) = **14/14 success**、main [42171089712](https://github.com/zawa356/Utteran/actions/runs/42171089712) = **success**。
- Windowsブラウザでの概観と本番WebView2/DPI/IME/OS pickerの受入は区別する。B4〜B9実API、実モデル/推論、DB履歴/削除、installer/portableは未受入。Issue #21は継続。
- 画像`i5-01`〜`i5-16`は`frontend/test-results/ui3-screenshots/`の生成物でcommitしない。

**以下の§1〜§6は初期案時点の試験値・確認事項を残した歴史資料**（19 tests、owner承認前などは当時の表記）。最新状態は本節と設計確定文書を優先。

## 1. 実行方法

| 目的 | コマンド（`frontend/`で実行） |
|---|---|
| 依存の導入 | `npm ci` |
| プロトタイプを開く | `npm run prototype` → `http://127.0.0.1:5180/preview.html` |
| 単体試験（本番＋プロトタイプ） | `npm test` |
| プロトタイプE2E（操作・レビュー画像・DPI・隔離） | `npm run build && npx playwright install chromium && npm run e2e:prototype` |
| レビュー画像の生成 | `npm run build && UI3_SCREENSHOTS=1 npm run e2e:prototype` → `test-results/ui3-screenshots/*.png` |
| UI-2既存E2E（本番`/next/`の非退行） | `npm run build && npm run e2e`（`uv`が必要） |

`npm run e2e`（UI-2）は`test-results/`全体を作り直すため、画像が必要なら`e2e:prototype`をその後に実行する。
`isolation.spec.ts`はbuild出力を検査するので、`npm run build`を先に実行する（無ければ失敗する。skipしない）。

## 2. レビュー用スクリーンショット

画像は**commitしない**（`test-results/`はignore済み、privacy/media scanの許可を増やさない）。作業セッションでownerへ提供し、上記コマンドで同じ画像を
再生成できる。データはすべて合成（`sample-meeting-01.wav`等のファイル名のみ、実音声・実パス・トランスクリプトなし）。

| No. | ファイル | モード | 条件 | 見どころ |
|---|---|---|---|---|
| 1 | `1-normal-ja-light-1280.png` | 通常 | ja / light / 1280×820（全体） | 入力→構成→開始→進捗→結果。完了3・失敗1（同じ行に再試行）・準備中1、開始が無効な理由 |
| 2 | `2-normal-en-dark-1280.png` | 通常 | en / dark / 1280×820 | 英語の長い文言、詳細（モデル・話者数・実行経路の説明）展開、処理中の表示 |
| 3 | `3-expert-ja-light-1280.png` | Expert | ja / light / 1280×820 | 3ペイン、2件選択時の「選択ファイル」Inspector（継承・一部個別・複数の値）、失敗ジョブの詳細 |
| 4 | `4-expert-ja-dark-1100.png` | Expert | ja / dark / 1100×760 | 2列＋キュー下段、ジョブ詳細（投入時の設定・デモ操作の区切り） |
| 5 | `5-expert-en-light-900x660.png` | Expert | en / light / 900×660 | 1列＋ペイン切替、F6で移動した先のfocus ring、横overflowなし |
| 6a–6e | `6a-normal-empty` `6b-expert-loading` `6c-normal-error` `6d-expert-success` `6e-normal-success` | 通常/Expert | 空・読み込み中・エラー・成功 | 空の案内と開始不可の理由、skeleton、領域内エラー＋コード＋再試行、全件完了時の投入無効 |
| DPI | `dpi-100-expert` `dpi-125-expert` `dpi-150-expert` `dpi-200-expert` | Expert | 1920×1200画面の最大化窓を100/125/150/200%に相当するCSS px（1920×1160／1536×928／1280×773／960×580、DPR 1/1.25/1.5/2） | 3/3/3/1列、横overflowなし。**Chromiumのエミュレーションで、Windows OS DPIの実機判定ではない** |

各画像のテスト（`e2e-preview/review-set.spec.ts`）は、画像の有無にかかわらず列数と横overflowを**assert**する。

## 3. 試験結果（Linux cloud container、headless Chromium）

環境: Node 22.23.4、npm 10.9.9（`npm ci`のみ。lockfile再生成なし）、Playwright 1.63.0同梱Chromium、Python 3.11（`uv sync --extra dev --extra gui --locked`）。

| gate | コマンド | 結果 |
|---|---|---|
| 本番依存audit | `npm run audit:prod` | 0 vulnerabilities |
| frontend lint | `npm run lint`（`--max-warnings=0`） | ok（error 0 / warning 0） |
| 型 | `npm run typecheck` | ok |
| 単体 | `npm test` | **91 passed / 0 failed**（13 files。既存77＋プロトタイプ14） |
| build | `npm run build` | ok。JS 411.05 KB（gzip 141.70 KB）、CSS 312.92 KB（gzip 44.72 KB）＝UI-2と同一（プロトタイプを含まない） |
| UI-2 E2E | `npm run e2e` | **10 passed / 0 failed**（既存10件そのまま、実FastAPI `/launch`→`/next/`） |
| プロトタイプE2E | `npm run e2e:prototype` | **19 passed / 0 failed**（操作9、レビュー画像6＋DPI 4、隔離1） |
| lock | `uv lock --check` | ok（`uv.lock`の差分はutteranのversion行のみ） |
| Ruff | `ruff check` / `ruff format --check`（src tests tools） | ok / 212 files formatted |
| mypy | `mypy` / `mypy --platform win32` | 0 issues（121 files）/ 0 issues |
| schema | b1/b3/b4/b5/b6/b7/b8/b9 `--check` | 8/8 ok |
| BOM | `tools/check_powershell_bom.py` | ok（9 files、`.ps1`変更なし） |
| UI-2 pytest（build必須） | `UTTERAN_UI2_REQUIRE_BUILD=1 pytest tests/test_ui2_next_ui.py tests/test_ui2_reducer_parity.py` | 22 passed |
| pytest全体 | `pytest -m "not requires_model"` | **1292 passed, 9 skipped, 1 xfailed, 0 failed**（skip 9・xfail 1はUI-2時点と同じ既存分。Python変更なし） |
| privacy | `tools/public_history_scan.py --worktree --fail-on-findings` | tracked 614（新規file追加後）、**blocking 0**（非blockingは合成ファイル名`sample-*.wav`等の`media-path-in-content`と、名前に`acceptance`等を含む文書の分類のみ）。`tools/public_history_delta.py --candidate <HEAD> --trusted-base dd115085…`（履歴をunshallowして実行）: new_commits=1、**new_blocking=0**。PRのCIを正本とする |

### 試験の内容（`e2e-preview/`）

| spec | 内容 |
|---|---|
| `prototype.spec.ts` 1 | 「プロトタイプ／合成データ／処理しません」をja/enで表示、`<html lang>`切替。**全テスト共通**: dev server以外のorigin・`/api`・`/next`へのrequest、console error、pageerrorが1件でもあれば失敗 |
| 2 | 通常フロー: 追加→取り消し→開始（4件）→全件投入済みで開始無効＋理由→疑似進行→失敗→再試行→完了4件→出力例→出力フォルダ無効→結果と履歴へ移動 |
| 3 | 通常/Expert切替で選択（2件）・override（言語=英語）・共通の編集（自動判定）・Inspectorのタブが保持される。継承中は入力無効、「共通に戻す」で継承へ |
| 4 | 全選択で「複数の値」「一部が個別」、すべて共通に戻す、選択なしの案内 |
| 5 | 投入→ジョブ詳細（投入時の設定）→キュー画面→デモ進行・失敗（`demo_failure`）・再試行（試行2）。モデル/セットアップは説明のみでbutton 0個。設定画面でdark。文字起こしへ戻るとExpertのまま |
| 6 | エラー（role=alert、コード、開始無効の理由）→再試行で復帰、読み込み中（role=status）、空→フォルダ追加で開始可能、初期化 |
| 7 | 900×660: 1ペイン＋切替、F6/Shift+F6でペイン移動とfocus、ja/en×light/dark×通常/Expertで横overflowなし、1280へ戻すと3ペイン |
| 8 | keyboard: Tabで入力一覧へ、focus ring、ArrowDownで次行、Enterで選択、削除dialogをEscで閉じる（変更なし）、確認で削除 |
| `review-set.spec.ts` | §2の6条件＋DPI 4条件（列数・横overflowをassert） |
| `isolation.spec.ts` | build出力のHTMLは`index.html`のみ、全assetにプロトタイプの文言・fixture名・`src/preview`・注入key無し |

### ブラウザとWebView2の区別

ここでの合格はすべて**Linux上のheadless Chromium**（Vite dev server）での結果。プロトタイプはWebView2・pywebview・`--next-ui`では
配信されない（そもそも本番に含まれない）ため、Windows実機での表示確認は**行っていない**。WebView2・OS DPI・native D&D・IMEの受入は
本番画面へ移す各段階（S1/S3、UI-4）で行う。

## 4. ownerへの確認事項（レビュー時に判断してほしい点）

1. **配置案**: 通常A1（縦ステップ）とExpert C1（3ペイン）を採用してよいか。A2/C2のうち取り込みたい要素はあるか（[screen-design §2](screen-design.md#2-比較した配置案)）。
2. **Expertのサイドバー**: 1280–1439pxでExpert表示中だけrailにする調整を本番へ入れるか。代案はgridをcontainer幅基準にする（CSS container query）。
3. **ペイン比率**: 23/45/32のままでよいか（1280pxでは左が約270px）。左を固定最小幅＋中央可変にする案もある。手動resizerは作っていない。
4. **通常モードの「構成」**: 表に出す日常項目を言語・話者分離・出力形式の3つとし、モデル・話者数を「詳細」に入れる分け方でよいか。
   出力先フォルダ・batch glob・resumeをどちらに置くか（未設計）。
5. **再実行（rerun）**: 完了した入力を再度処理する操作を、通常モードに出すか、Expert/履歴だけにするか。
6. **G-05/G-06（入力一覧・ファイル別overrideの保存）**: 次の案から選択（プロトタイプは保存しない）。
   - 案1: 保存しない（現状維持）。投入時のsnapshotだけが残る。最も単純でprivacy上も安全だが、再起動で作業途中の一覧が消える。
   - 案2: 入力一覧とoverrideを`gui-settings.json`とは別の**ローカルの作業状態file**に保存（パスを含むため公開しない・期限付き）。
   - 案3: B6 control store（private DB）に保存。G-15（保持期間・ACL・消去）の決定が前提。
7. **G-15**: private DBの保持期間・ACL・消去方針（案3を選ぶ場合は先に必要）。
8. **用語**: 「共通デフォルト」「個別に設定」「共通に戻す」「投入時の設定」「未投入をすべて投入」の言い回し（ja/en）。

> **第4イテレーションの確認事項**は[iteration-4 §13](iteration-4.md#13-未検証残る判断)（通常の設定の適用先、Wizardの粒度、処理設定の範囲、プリセット形式、狭幅と最小サイズ）。
> 上の6（G-05/G-06）は未決のまま。第4案もプリセット・Wizardの途中状態を含め何も保存しない。

## 5. 未受入・既知の制約

- WebView2、OS DPI 125/150/200%実機、native D&D・file dialog、IME、実backend・SSE・ready判定、GPU/モデル/推論、起動時間・RAM、installer/portable。
- 1920×1200・200%で既定窓が画面外へはみ出す件（UI-4 #22）は未修正のまま（`app.py`は変更していない）。
- dev serverのコンソールにVuetify由来の`ResizeObserver loop completed with undelivered notifications`がViteのclient経由で出ることがある
  （ページのconsole errorではなくE2Eの失敗条件にも該当しない。ブラウザの既知の無害な通知）。
- 出力先・batch glob・resume・結果viewer・再出力・モデル管理・セットアップの画面は設計していない（S3/S4で別途レビュー）。

## 6. UI-3 S1–S5・UI-4への引継ぎ

**UI-3-0の画面設計は2026-11-06に承認済み。** 各段階は画面単位の小PRとし、
プロトタイプの部品（`src/preview/components/*`）は本番の`domain/`（framework非依存）へ状態を移してから`features/`へ移植する。
プロトタイプのコード自体は本番へimportしない（ESLintで禁止）。

| 段階 | 実装順序 | 受入条件 | 禁止範囲 |
|---|---|---|---|
| S1 設定の残り | profile、既定入出力フォルダ（native picker）、token（値を出さない・503 keyring）、ログ、wizard再起動 | 旧設定画面と機能表で同等、保存失敗が見える（D-05再発なし）、Linux＋Windows CIの自動試験。native pickerのWindows実機確認は最終ゲートへ集約（2026-11-09 owner方針、[s1 §8](s1-settings-and-host.md#8-windows-webview2-実機受入未実施最終ゲートへ集約)） | 新UIからv1・DBへ書かない |
| S2 環境の残り | 環境stripの全項目、`profile_warnings`、B4 ready証拠の表示 | ready判定はbackend正本、frontendに互換表なし、架空のreadyなし | 不成立の組合せを候補に出さない |
| S3 文字起こし・キュー（**S3-A実装済み 0.1.46**: 1ファイル・旧`/api/jobs`・D-03/D-04/D-06/D-07の再発防止・SSEはevent id重複排除。[s3a](s3a-legacy-transcription.md)。**S3-B1実装済み 0.1.47**: 入力一覧・ファイル別override・出力先・旧APIへの順次投入（共通single-flightゲート、部分受付・結果不明で停止）。[s3b1](s3b1-multi-input-and-admission.md)。残りはS3-B2（複数ジョブ監視・取消）とS5） | 通常A1の5段・Expert C1の3ペインを旧`/api/jobs*`・`/api/queue`（単一in-flight poll）で実装。新UIのnative D&D受け口、最初の自由入力欄（Prompt）とIME | D-03/D-04/D-06/D-07が再現しない、二重投入なし、%を推測しない、IMEのE2E＋Windows実機、旧機能（batch glob・resume・話者数・形式）欠落なし、本プロトタイプの操作E2Eを実API版に移す | v1接続しない、旧SSEは重複排除を決めてから |
| S4 モデル・結果と履歴・セットアップ | 旧API同等の操作、viewer、旧wizard | 旧→新対応表で欠落なし | — |
| S5 v1接続 | B9 #37（epoch跨ぎ`queue_revision`）をPython正本とTSで解消した後（**0.1.48で解消**）、Preset/Policy、Job/Queue CAS、Setup B8、events/sync | 旧/新queue排他（G-09）、一方向DB migrationのowner承認 | owner承認前のactivate |
| UI-4 #22 | DPI 200%の初期窓収容、installer/portable同梱、WebView2実機E2E、起動時間/RAM、既定切替判断 | 実機で受入 | 承認前の既定切替 |
