# UI-3 束D1 — 共通シェル・画面遷移（Normal上部ナビ／Expert管理サイドバー）

更新: 2026-11-19 JST（開発版`0.1.58`・未リリース、Draft PR）。親: [UI-3 #21](https://github.com/zawa356/Utteran/issues/21)／[Epic #18](https://github.com/zawa356/Utteran/issues/18)。
基点: main `dd961fc26fcd37377c1ecc38c1266b970092bef7`（0.1.57、統合後CI [run 44433404772](https://github.com/zawa356/Utteran/actions/runs/44433404772) success）。
画面方針の正本は[design-freeze](design-freeze.md)（第5案）、実挙動の正本はコード。D2（キュー専用画面）・D3（結果と履歴）・D4（外観統合・3ペイン精密化・実機DPI）は別PR。

## 1. 着手時の現況と第5案との差分（新規に作ったものと既存の区別）

| 場所 | 着手時（main 0.1.57） | 第5案 | D1の扱い |
|---|---|---|---|
| `features/shell/AppShell.vue` | **既存**: Normal・Expertの**両方**に6項目の`v-navigation-drawer`、上部にモード切替と`LegacyLink` | Normal＝アプリバーに3項目・sidebarなし、Expert＝6項目の管理sidebar | 分離。上部（アプリ名・試験版chip・モード切替・旧画面リンク・APIモード別の常設案内）は維持 |
| `domain/shell.ts` | **既存**: `mode`・単一の`section`・`expertPane`・`navCollapsed` | 通常に無いページからの復帰は文字起こしへ | モード別の最後のページ（`normalSection`/`expertSection`）を追加。`section`は表示中ページ（常に現在モードの値）。セッション限定・永続化なし |
| sidebarの折りたたみ | **既存**: `navCollapsed`。**1024px未満では強制rail＋ボタン無効**（展開不能） | 折りたたみボタンはsidebar最上部に常設、狭い窓はrailで始まるが展開可能 | ボタンを最上部へ移し、全幅で有効。広い窓／狭い窓の選択を別々に保持（`navCollapsed`／`navExpandedNarrow`） |
| `features/expert/ExpertWorkspace.vue` | **既存**: ①②③の3ペイン、1024px未満は1ペイン＋切替 | 可変3ペイン（D4） | **変更なし**（機能・testid・CSSとも維持） |
| `features/workspace/NormalWorkspace.vue`・`SettingsPage.vue` | 既存 | — | 変更なし（外枠の変更でstateを失わないことを試験で確認） |
| `domain/core.ts` | AppCoreを1回生成、v1/legacy controllerを保持 | 同一AppCoreを異なる形で表示 | 変更なし（controllerの追加・二重生成なし） |

新規ファイルは`features/shell/NormalTopNav.vue`・`ExpertSidebar.vue`・`navIcons.ts`のみ（productの部品。`frontend/src/preview/`はimportしていない。設計参照は`preview/shell/NormalTopNav.vue`・`ExpertSidebar.vue`）。

## 2. 仕様（実装後）

### 2.1 Normal（アプリバー）

- 目的地は**文字起こし／セットアップ／設定**の3つだけ。キュー・結果と履歴・モデルは描画しない。`nav`ランドマーク（`aria-label`＝メインナビゲーション）、現在のボタンに`aria-current="page"`・primary色・下線。
- 窓幅が**1000px未満**（`TOPNAV_FOLD_WIDTH`）では、現在のページ名を表示する1つのメニューボタン（`top-nav-menu`、`v-menu`）へ折りたたむ。文字は縮小しない。
  閾値の根拠（headless Chromium 1280px実測）: アプリバー全体が必要とする幅は ja 762px／en 816px。Windowsのフォント差を見込み1000pxとした（900px＝pywebview最小では折りたたみ、1100/1280pxは展開）。
- セットアップは既存の`NotMigrated`で「未移行」と移行予定を表示する。ボタン・成功表示はない。

### 2.2 Expert（管理サイドバー）

- 6項目を第5案の順（文字起こし・キュー・結果と履歴・モデル・セットアップ・設定）で表示。展開時は項目名の下に「一部移行／未移行」。
- 折りたたみ／展開ボタン（`nav-toggle`、`aria-expanded`・`aria-controls`・`title`）は**最上部に常設**し、rail時も残る。窓幅1024px未満はrailで開始し、ボタンで展開できる。
- rail時は各項目に`aria-label`（例「キュー（未移行）」）と`v-tooltip`。幅のドラッグはD4。
- キュー（D2）・結果と履歴（D3）・モデル／セットアップは未移行表示のまま。v1のjob boardはExpert文字起こし③にあり、キュー専用ページではない。
  **追補（0.1.59、束D2-A）**: `--next-ui-v1`ではキューが読み取り専用の専用ページ（[d2a-queue-page](d2a-queue-page.md)）になり、sidebarは「一部移行」。legacyは未移行のまま。

### 2.3 遷移と状態

- `ShellController.setMode()`は相手モードの最後のページを表示する。Normalの初期値・Expert専用ページから戻った場合は文字起こし。
- `navigate()`は現在モードに無いページ（Normalでのキュー等）を**拒否して何も変えない**（戻り値`false`）。
- 通常の「複数件の投入・ファイル別の設定（Expertモード）」は`openExpertTranscribe()`でExpertの文字起こしを開く（Expertの最後のページがキュー等でも）。
- 入力一覧・選択・override・共通設定・設定ドラフト・ジョブ状態・v1の確認／受付／結果不明はAppCoreのcontrollerにあり、シェルは一切触れない。モード切替・ページ移動・rail切替で**書き込み要求は0件**。
- ページ遷移でViewがunmountされると既存どおりpolling（`V1JobBoard`の`watch(false)`等）が止まり、再表示で1つだけ再開する。
- URL・hash・query・storageは読まない。APIモードはサーバーのmetaのみ。シェル状態の永続化はG-05/G-06の将来判断として追加しない。

## 3. testid

維持: `mode-toggle`・`mode-normal`・`mode-expert`・`open-legacy`・`preview-chip`・`preview-notice`・`nav-toggle`・`nav-<section>`・`section-<id>`・`expert-*`・`pane-*`・`sidebar`。

| testid | 変更 |
|---|---|
| `nav-<section>` | Normalではアプリバーのボタン（3つ）、折りたたみ時はメニュー内の項目。Expertではsidebarの項目。同時に2つ描画されることはない |
| `sidebar` | `v-list`から`v-navigation-drawer`へ移動（`data-rail`付き）。リストは新`sidebar-list` |
| 新規 | `app-bar`、`top-nav`（`data-folded`）、`top-nav-menu`、`top-nav-list`、`content`（`v-main`、`data-section`） |

参照元の更新: `e2e/next-ui.spec.ts`（sidebar試験をExpertへ、Tabの到達先を`sidebar-list`へ、狭幅でのrail展開を追加）、`e2e/settings-s1.spec.ts`（900pxで`top-nav-menu`を開いてから設定へ）。いずれも理由をコメントし、削除・skip・timeout延長なし。

## 4. 試験（Red→Green）

Red試験を先にコミット（`3de110d`）。基点mainのコードに対して:

- Vitest `shell.test.ts`: **8 failed／3 passed**（状態分離・`isRail`等が無い）。
- Playwright（4 spec）: **22 failed／22 passed**。v1のpolling増殖ガード1件は旧コードでもpass（回帰ガードとして意図どおり）。

| ID | 試験 |
|---|---|
| D1-01 | `shell-d1.spec.ts` D1-01、Vitest D1-01 |
| D1-02 | `shell-d1.spec.ts` D1-02、Vitest D1-02×4（300手のランダム列で「表示中ページ∈現在モード」を検査） |
| D1-03 | `shell-d1.spec.ts` D1-03/D1-05（入力3件・選択2件・override・cuda・設定ドラフト・ペイン選択を全ページ／rail／両モード往復後に確認、書込0件・`/api/v1`0件）、Vitest D1-03（要求0件・store同一参照） |
| D1-04 | `transcription-s5d-shell.v1.spec.ts` 3件（確認済み・受付済みの保持と追加POST/取消/DELETE 0件、結果不明の保持と明示再照合で同key同body、board読取りの非増殖と離脱時停止）、Vitest D1-04 |
| D1-05 | `shell-d1.spec.ts` D1-05（hash/query/storageで昇格せず、再読込で既定へ）、既存`api-mode.spec.ts` |
| D1-06 | `shell-d1.spec.ts` D1-06（両モードで未移行表示・ボタン0・書込0） |
| D1-07 | `shell-d1.spec.ts` 900/1100/1280px × ja/en × ライト/ダーク（12件）＋キーボード1件 |
| D1-08 | 本書§3、スクリーンショット§5 |

全体の結果（件数・CI）はPR本文に記録する。

## 5. スクリーンショットとDesign Freezeとの差分（D4への残件）

採取: `UI2_SCREENSHOTS=1 npx playwright test e2e/shell-d1.spec.ts`（`test-results/screenshots/d1-*.png`、commitしないローカル成果物）。
条件: Normal/Expert × 900/1100/1280px × ja/en × ライト/ダーク、headless Chromium、deviceScaleFactor 1。比較対象はプロトタイプ`i5-*`（[design-freeze §6](design-freeze.md#6-再現手順)）と基点mainの同条件画面。

| 観点 | 第5案 | D1後 | 残件 |
|---|---|---|---|
| Normalのナビ | アプリバー内3導線、下線、狭幅でメニュー | 同じ構成（閾値1000px） | プロトタイプは760px。アプリメニュー（ファイル/表示/ツール/ヘルプ）は未実装（D4） |
| Expertのsidebar | 6項目、上部の折りたたみ、rail、幅ドラッグ168–360px | 6項目・上部の折りたたみ・rail・tooltip。幅は固定232px | ドラッグリサイズ・キュー件数chip（D4/D2） |
| 3ペイン | 可変幅、container幅で3→2→1、縦積みしない | 既存のまま（viewportのmedia query、1280px未満は③が下段） | D4 |
| 配色・密度 | 青/緑×ライト/ダーク | 既存テーマのまま | D4 |
| 実機 | WebView2・OS DPI 125/150/200% | 未実施（headless Chromiumのみ） | D4／[#22](https://github.com/zawa356/Utteran/issues/22) |

## 6. 範囲外（完了と扱わない）

- D2 キュー専用画面・表・詳細・検索/並べ替え・pause/hold/reorder。D3「結果」「履歴」タブ・rerun・Viewer。D4 外観・3ペイン精密化・実機DPI。
- [#60](https://github.com/zawa356/Utteran/issues/60) S5-Bのcancel/revision競合（独立PR）。[#59](https://github.com/zawa356/Utteran/issues/59) 成果物削除（全OSで`artifact_deletion_disabled`のまま）。
- 既定GUI/CLI・`--next-ui`のAPIモード・CSP・DB schema・API/IPC・依存パッケージは無変更。
