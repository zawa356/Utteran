# UI-1 frontend framework・UIキット・host比較

更新: 2026-11-04 JST。**結論は推奨案であり採択ではない**（ADR: [ADR-003](../../adr/ADR-003-ui-frontend-stack-and-host-boundary.md)、Status: Proposed — owner review required）。

## 1. 比較条件（同条件スパイク）

同一の縦切りを3案で実装し、同一のPlaywrightスクリプトで駆動した（lockfile込みでcommitし、クリーンcheckoutとCI job `ui1-frontend-spike`で再実行できる）。ソースと再実行手順:
[`tools/ui1/spike/`](../../../tools/ui1/spike/README.md)。

- 共通コア（framework非依存TypeScript、`core/core.ts`を3案で同一ファイル共有）: 共通設定＋ファイル別override、
  latest-wins GET、POSTのtimeout/AbortController・エラー分類・同一Idempotency-Keyでの再試行、
  `epoch:seq` event reducer（重複・逆順・`resync_required`→`/sync`）、合成backend。
- 画面: sidebar（通常/Expert切替＋6機能）、Expert 3ペイン（入力一覧 / 共通設定・選択ファイルInspector・工程 /
  投入・キュー・イベント統計）、通常モード（入力→推奨→開始、根拠は`<details>`）。装飾は未調整。
- 3ペインは3案とも**同じCSS grid**（23/45/32、1100px未満でキュー列を下段へ）。キット固有のSplitterは使わない。
- 各frameworkのadapterは薄い購読のみ: Vueは`shallowRef`＋`subscribe`、Reactは`useSyncExternalStore`。
  Pinia・Zustand等は**比較条件を揃えるため不使用**（コアが状態を持つため不要だった）。
- 実行環境: Linux、Node v22.23.4、Vite 8.3.5、TypeScript 6.0.3、Chromium 153.0.8010.12（headless）。
  **Windows WebView2では未実行**。

| 項目 | Vue 3 + PrimeVue | Vue 3 + Vuetify | React + MUI |
|---|---|---|---|
| 主要版 | vue 3.5.44 / primevue **4.5.5** / @primeuix/themes 2.0.3 | vue 3.5.44 / vuetify 4.3.2 | react 19.3.1 / @mui/material 9.5.0 / @emotion 11.14 |
| 型検査 | `vue-tsc --noEmit` 0 error | `vue-tsc --noEmit` 0 error | `tsc --noEmit` 0 error |
| 同一7シナリオ（override / latest-wins / 409+連打+再試行 / timeout+再試行 / reducer / 通常↔Expert状態保持 / keyboard Enter） | **7/7 pass** | **7/7 pass** | **7/7 pass** |
| JS（raw / gzip -9） | 410.7 KB / 94.2 KB | 354.2 KB / 120.1 KB | 470.8 KB / 143.0 KB |
| CSS（raw / gzip -9） | 1.1 KB / 0.6 KB（themeはJSが実行時注入） | 314.0 KB / 44.8 KB | 1.1 KB / 0.6 KB（Emotionが実行時注入） |
| 本番依存パッケージ数（`npm ls --omit=dev --all`） | 31 | 50 | 84 |
| `node_modules`（dev込み） | 141 MB | 202 MB | 142 MB |
| `vite build`（3回、ms） | 1294 / 915 / 773 | 1645 / 985 / 1021 | 1344 / 767 / 806 |
| ready到達（5回の中央値、mock backend込み） | 230 ms | 254 ms | 224 ms |
| JS heap（GC後: 読込直後 → シナリオ後） | 5.5 → 6.8 MB | 4.7 → 6.2 MB | 4.7 → 5.9 MB |
| 現行CSPのまま（`style-src 'self'`）のCSP違反 | **32件**（themeが当たらない） | **2件**（theme変数が当たらない） | **5件**（Emotionのstyleが当たらない） |
| nonce付きCSP（`style-src 'self' 'nonce-…'`）の違反 | 0件（`csp.nonce`設定） | 0件（`theme.cspNonce`設定） | 0件（`createCache({nonce})`） |
| 本番依存のライセンス | MIT 27, BSD/ISC/Apache 各少数 | MIT 42, BSD/ISC/Apache 少数。**MPL-2.0の`lightningcss`はVite（build時のみ）由来でbundle外** | MIT 79, BSD-3 3, ISC 2 |

計測の注意: build時間・ready時間・heapは1台のcontainerでの小規模計測で、ばらつきの検定はしていない。
WebView2上の起動時間・RAM（プロセス全体）は**未計測**。

## 2. UIキットの標準部品とライセンス（作業時点の実パッケージと公式情報）

| 観点 | PrimeVue | Vuetify | MUI |
|---|---|---|---|
| ライセンス | 3区分（詳細は下の「PrimeVueのライセンス区分」）: **MIT版4.x**（最終4.5.5、2026-04-08）／5.0.0（2026-07-15）以降の**PrimeUI License**のうち**無料のCommunity License**（条件付き）と**有料のCommercial License**。公式GitHubは「security fixes only」と表示し、開発はPrimeUIへ移行 | MIT（実パッケージ・npm metadata） | `@mui/material` MIT。MUI XはCommunity（MIT）＋Pro/Premium（商用・license key、未設定時は透かし） |
| 保守状況 | 4.xは新機能停止・security fixのみ（公式GitHub） | v4系が活発（4.0.0 2026-02-23 → 4.3.2 2026-11-03） | v9系が活発（9.0.0 2026-04-07 → 9.5.0 2026-10-09） |
| データテーブル・仮想化 | DataTable（virtual scroller）、TreeTable | VDataTable / **VDataTableVirtual**（core） | 素の`Table`はMIT。DataGrid Community（MIT）は**列リサイズ・autosize・行/列仮想化を含む**（MUI X v7でCommunityへ移動。`@mui/x-data-grid@9.17.0`の型定義でも`disableColumnResize`/`autosizeOnMount`は通常prop）。Community版で強制されるのは複数列ソート/フィルタ不可・列の並べ替え不可・pagination有効（`DataGridForcedPropsKey`）。複数列ソート・列固定・row reorder・tree dataはPro以上 |
| Tree / Inspector | Tree, TreeTable | **VTreeview**（core） | Tree View Community（展開・選択）。**DnD並べ替え・lazy load・仮想化はPro** |
| Splitter / リサイズ可能ペイン | **Splitter**あり | **なし**（CSS grid＋自作resizer、または別ライブラリ） | **なし**（`react-resizable-panels` 4.17.0 MIT等が必要） |
| Dialog / Toast / Menu / Tabs / Form | あり | あり（VDialog、VSnackbarQueue、VMenu、VTabs、VForm、VNumberInput） | あり（Dialog、Snackbar、Menu、Tabs） |
| キュー順序のdrag操作 | OrderList / PickList / DataTable row reorder | 標準部品なし（VList＋別ライブラリ） | 標準部品なし（DataGridのrow reorderはPro以上） |
| i18n | locale設定 | locale adapter（ja/en同梱） | locale object（jaJP等） |
| 実行時style注入 | あり（styled mode） | theme変数のみ（本体CSSはファイル） | あり（Emotion） |

MUI Xの列リサイズについて: 初版（HEAD `f41ae3a`）で「列リサイズはPro」と書いたのは誤りで、レビュー指摘により訂正した。
MUI X v7（2024年）でcolumn resizing/autosizingはCommunityへ移動している（https://mui.com/blog/mui-x-v7/ 、
https://mui.com/x/react-data-grid/column-dimensions/ ）。採用候補版`@mui/x-data-grid@9.17.0`の型定義でも、
Community版の強制prop（`DataGridForcedPropsKey`）に列リサイズは含まれない。公式licensingページの概要文には
Pro機能として列リサイズを挙げる記述が残っていたため、実パッケージと機能ページを正とした。

## 3. framework（Vue vs React）

| 評価軸 | Vue 3 + TS + Vite | React + TS + Vite |
|---|---|---|
| 応答順序・SSE・状態遷移の正確性 | コア（framework非依存）で担保。今回3案とも同じコアで同じ結果 | 同左。差は出なかった |
| テスト | Vitest（コア）＋Playwright（画面）。今回は同一Playwrightで駆動できた | 同左 |
| 型検査ツール | `vue-tsc`が必要。**vue-tsc 3.3.14はTypeScript 7.0.2で起動不可**（`typescript/lib/tsc`が未export）。TS 6.0.3では動作 | `tsc`のみ（TS 7でも理論上可、今回は条件統一のため6.0.3） |
| テンプレート/責務分離 | SFCで画面単位に分けやすい。HTMLに近く既存`index.html`からの移植が直観的 | JSX。分割は容易だが、Hooks規約（依存配列等）の学習負担 |
| 依存規模 | 小（PrimeVue 31 / Vuetify 50） | 大（MUI 84） |
| 長期保守 | Vue本体は安定。キット側はVuetifyが活発、PrimeVueはMIT版4.x凍結/5.x以降は独自ライセンス | React・MUIとも活発。MUI Xの高機能部品は有償境界がある |
| Electron移行時の再利用 | コアとviewはhost非依存にできる（`window.pywebview`をhost bridgeへ隔離すれば同等） | 同左 |

**状態の正しさはframework選択では決まらない**: 3案とも同じ非同期コアで7/7を満たし、旧`app.js`の欠陥
（D-01〜D-07）はframeworkではなく「request世代・終了ガード・catch・単一in-flight」の欠如が原因だった。

## 4. 推奨（提案）

1. **第一推奨: Vue 3 + TypeScript + Vite + Vuetify 4（MIT）＋ framework非依存の非同期コア（TS）**。
   - 理由: MITで活発に保守、VDataTableVirtual・VTreeview・VSnackbarQueue等の業務部品が無償のcoreにある、
     本体CSSがファイル配信でCSP違反が最少（2件、nonceで0件）、依存数がMUIより少ない、SFCで画面単位の段階移行がしやすい。
   - 留保: Splitterが無い（CSS grid＋小さなresizerを自作するか、MIT小規模ライブラリを追加）。
     Material Designの見た目を業務用の密度へ寄せる調整（`density="compact"`、theme）が必要。キュー並べ替えの
     drag部品が無い（キーボード操作の「上へ/下へ」を正とし、dragは任意）。Vuetify 4.3.2は作業前日公開で、
     採択時は2週間以上経過した版へpinする。
2. **代替: React + TypeScript + Vite + MUI（Coreのみ、MUI X Pro/Premiumは使わない）＋同じコア**。
   - 選ぶ条件: owner/保守者がReactに慣れている、MUIの見た目を好む、Electron/他製品とのReact資産共有を優先する場合。
   - 留保: 業務テーブルの一部（複数列ソート・列並べ替え・列固定・row reorder・tree data）は有償版、`react-resizable-panels`等の追加依存、JS最大、
     Emotionのためnonce CSPが必須。
3. **不推奨: PrimeVue**。部品の充足（Splitter・OrderList・TreeTable）は最良だが、MIT版4.xは新規開発停止、
   5.x以降はMITではない独自ライセンス（無料Community版はあるが条件付き）で、MITのOSS配布物の依存として
   長期採用しにくい（理由は下の「PrimeVueのライセンス区分」）。

### PrimeVueのライセンス区分とOSS配布上の判断

| 区分 | 対象 | 費用・条件（実パッケージ`primevue@5.0.4`の`LICENSE.md`と公式 https://primeui.dev/licenses/community 、2026-11-04確認） |
|---|---|---|
| MIT | PrimeVue **4.x以前**（最終4.5.5） | 無料・MIT。公式は「既存のMIT版は永久にMIT」とする。新機能開発は停止、security fixのみ |
| PrimeUI **Community License** | PrimeVue **5.x以降** | **無料**。対象は年間売上100万USD未満・開発者5人未満・従業員10人未満・外部資金300万USD未満の組織、個人・学生・非営利・**非商用OSS**。license key必須、12か月ごとの更新（30日猶予）、Pro部品は含まない |
| PrimeUI Commercial License | PrimeVue 5.x以降 | 有料（開発者単位、Pro部品・サポートを含む） |

Utteranが5.xを依存に採らない理由（**法的解釈ではなく採用判断**。条文の解釈が必要ならownerが確認する）:
- UtteranはMITで配布し、第三者がforkしてbuildできることを前提にしている。5.xはcompiled packageの配布で、
  key・適格性・更新が**利用者（build者）ごと**に必要になり、MITの依存として同等に扱えない。
- 再配布で第三者が開発に使えるようにする場合は別途OEM Licenseが要るとの制限があり、OSSリポジトリでの扱いに確認事項が残る。
- keyが無い・期限切れの場合にlicense noticeが表示され得るため、配布物の表示が依存側の状態に左右される。
- MITの4.xは利用可能だが、新規開発が止まっているため長期の基盤には選ばない。

不採用の追加候補（比較を肥大化させないため要点のみ）:
- **Svelte 5 + キット**: bundleは最小になり得るが、業務向けUIキット（テーブル仮想化・Tree・Splitter）の
  成熟度がVue/Reactより低く、今回の要件（標準部品優先）に合わない。
- **Bootstrap + vanilla JS**: BootstrapはCSS/部品キットで、**非同期状態モデルの代替ではない**。現行欠陥は
  状態管理の欠如が原因であり、Bootstrap導入だけでは解消しない。テーブル仮想化・Treeも無い。

## 5. host（pywebview + WebView2継続 vs Electron）

| 観点 | pywebview 6.2.1 + WebView2（現行） | Electron |
|---|---|---|
| 現状資産 | 既存の起動・session・D&D full path・file dialog・process supervisor・installer/portable | すべて再実装（main process、IPC、配布） |
| 配布サイズ・RAM | WebView2はOS共有（Evergreen前提、実機未計測） | Chromium同梱で増加（未計測） |
| SAC/WDAC | 既存の評価経路（#7）を継続 | 新しい署名済みバイナリの評価が必要 |
| フロント資産の再利用 | — | host bridgeを差し替えればコア・viewは再利用可能（設計で担保） |

**推奨: pywebview + WebView2を継続**。Electronへ移る条件（いずれかを実機で確認した場合のみ再検討）:
WebView2で`EventSource`/SSEや必要なWeb APIが実用上機能しない、D&D・file dialog・複数windowの要件を
pywebviewで満たせない、配布先でWebView2 Runtimeを前提にできない、自動更新等Electron固有機能が製品要件になる。

## CSP

現行CSPは`style-src 'self'`のため、3キットとも実行時style注入がブロックされる（上表）。UI-2では
**index.htmlを配信するたびにnonceを生成し、`style-src 'self' 'nonce-…'`とキットのnonce設定を一致させる**
変更がbackend（`api.py`の`index()`とmiddleware）に必要。`'unsafe-inline'`での回避はしない。
これはUI-2の依存タスク（[backend-gap-matrix](backend-gap-matrix.md)のG-11）。
