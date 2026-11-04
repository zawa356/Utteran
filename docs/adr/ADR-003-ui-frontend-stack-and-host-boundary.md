# ADR-003: 次期GUIのfrontend構成とhost境界

- Date: 2026-11-04（提案）／**2026-11-04 owner採択**
- Status: **Accepted**（owner採択 2026-11-04。UI-1 [#19](https://github.com/zawa356/Utteran/issues/19)、
  UI-2 [#20](https://github.com/zawa356/Utteran/issues/20)、Epic [#18](https://github.com/zawa356/Utteran/issues/18)）
- 採択の範囲: 技術構成（下記Decision）。**Windows WebView2実機での受入は未実施**であり、採択とは別のゲート
  （新UIの既定化・配布受入の前提、UI-4 [#22](https://github.com/zawa356/Utteran/issues/22)）として残る。
- Examined main: `068e62fce1b35960703b534426adfbe373ff86d8`（提案時）、採択反映は`0e07e138aae742244ca387468de62701874a31c9`
  （PR #35 merge）を基点とするUI-2で実施。
- 番号: mainには`ADR-002`のみ存在。`ADR-001`はDraft [PR #23](https://github.com/zawa356/Utteran/pull/23)が使用中のため、
  競合を避けて`ADR-003`とした。PR #23のADR-001は参照のみで、本ADRはそれを正本化しない。

## 採択内容（2026-11-04 owner）

| 項目 | 決定 | UI-2で固定した版（`frontend/package-lock.json`） |
|---|---|---|
| framework | Vue 3（SFC） | `vue@3.5.44` |
| 言語・型検査 | TypeScript ＋ `vue-tsc` | `typescript@6.0.3`（`vue-tsc@3.3.14`がTS 7で起動しないため6系）、`vue-tsc@3.3.14` |
| build | Vite | `vite@8.3.5`、`@vitejs/plugin-vue@6.0.10`、`vite-plugin-vuetify@2.1.3` |
| UI kit | Vuetify 4（MIT） | `vuetify@4.3.1`（4.3.2は採択前日2026-11-03公開のため見送り、4.3系の1つ前のpatch） |
| icon | Material Design Icons（SVG path、外部font不使用） | `@mdi/js@7.4.47`（Apache-2.0） |
| 状態 | framework非依存TypeScriptのdomain/store/api/transport。Pinia等は不採用 | — |
| host | 現行 FastAPI ＋ pywebview / Windows Edge WebView2 を継続。native依存は`frontend/src/host/`に隔離 | — |
| Electron | **今は採用しない**。host adapterの差替えで将来選択可能な構造を維持 | — |
| PrimeVue / React+MUI | 再選定しない（下記Alternativesは提案時の記録） | — |

UI-2の実装・検証は[docs/architecture/ui2/](../architecture/ui2/README.md)。

## Context

- 現行GUIはFastAPI＋pywebview（Windows WebView2）＋手書きHTML/JS（`app.js` 1,984行に状態・描画・API・i18nが同居）。
- 「ボタン無反応・更新漏れ」の報告がある。UI-1で現行コードを実行し、**7件の非同期欠陥を再現**した
  （stale応答の巻戻り、catchなしの無反応、終了処理の二重実行、先行ジョブによる後続ジョブの追跡停止、
  取消失敗の固着、polling多重化、旧SSEの再接続再送）。原因はUIキットの欠如ではなく、
  request世代・終了ガード・エラー表示・単一in-flightの欠如（[defects-and-repros](../architecture/ui1/defects-and-repros.md)）。
- 理想UI要件（2026-11-04合意）: GUI-first、通常モードA＋Expert 3ペインC、常設sidebar、共通設定＋ファイル別override、
  不成立構成の非表示（backend正本）、成熟UIキット優先、日英・keyboard・DPI・オフライン同梱。
- backendのv1（B5〜B9）はopt-inで既定GUI未接続。公開能力API・progressイベント等は未実装（[gap](../architecture/ui1/backend-gap-matrix.md)）。

## Decision

1. **hostはpywebview＋WebView2を継続**し、frontendからのhost依存を`host bridge`（`pickPath`/`onDroppedPaths`/
   `reportError`/`openFolder`）に閉じ込める。Electronは採用しないが、bridge差替えで移行可能な構造にする。
2. **frontendは Vue 3 + TypeScript + Vite + Vuetify 4（MIT）を採用する**（提案時は第一候補、2026-11-04採択）。
3. **非同期の正しさはframework非依存のTypeScriptコア**（API client、transport、domain store、純粋reducer）で担保し、
   view（Vue SFC）はkit標準部品とコアのselector/actionのみを使う。Pinia等の追加state libraryは必須にしない。
4. 提案時の代替案React + TypeScript + Vite + MUIは**採用しない**（再選定不要とownerが判断）。コアがframework非依存である点は維持する。
5. **PrimeVueは採用しない**（MIT版4.xは新規開発停止・security fixのみ。5.x以降はMITではない独自のPrimeUI License: 無料のCommunity版は条件付き・license key必須・年次更新、ほかに有料のCommercial版）。
6. 旧`app.js`は画面単位で段階移行し、flagで旧UIへ戻せる状態を既定切替まで維持する。

## Evidence

- 同一縦切りスパイク（[framework-comparison](../architecture/ui1/framework-comparison.md)、
  [tools/ui1/spike](../../tools/ui1/spike/README.md)）: Vue+PrimeVue 4.5.5 / Vue+Vuetify 4.3.2 / React+MUI 9.5.0 とも
  同一Playwright 7シナリオ**7/7 pass**（Chromium 153 headless）。差はframeworkではなくkit・依存・CSPに出た。
- payload（JS gzip）: PrimeVue 94 KB、Vuetify 120 KB（＋CSS 45 KB）、MUI 143 KB。本番依存: 31 / 50 / 84。
- 現行CSP（`style-src 'self'`）での違反: PrimeVue 32、Vuetify 2、MUI 5。nonce対応で全て0。
- ライセンス: 実パッケージ`primevue@5.0.4`の`LICENSE.md`はPrimeUI License（無料Community版＝条件付き、有料Commercial版）。`primevue@4.5.5`はMIT。
  Vuetify・MUI CoreはMIT。MUI X DataGrid Community（MIT）は列リサイズ・autosize・仮想化を含む。複数列ソート・列並べ替え・列固定・row reorder・tree data（DataGrid）、DnD並べ替え（Tree View）はPro以上。
- tooling: `vue-tsc@3.3.14`は`typescript@7.0.2`で起動不可、`6.0.3`で動作。

## Alternatives

| 案 | 判断 |
|---|---|
| React + MUI | 有力な代替。依存とJSが最大、業務テーブル機能の一部が有償、Emotionのためnonce必須 |
| Vue + PrimeVue | 部品は最も充実（Splitter等）だが、ライセンス・保守の理由で不採用 |
| Svelte + kit | 業務部品キットの成熟度不足 |
| Bootstrap + vanilla | 状態モデルの代替にならない。欠陥の原因を解消しない |
| Electron | 現時点で必要性を示す実測なし。再検討条件はframework-comparison §5 |
| 旧`app.js`の修正のみ | 短期的には可能（ハーネスで回帰確認可）だが、要件のExpert 3ペイン・標準部品・保守性を満たさない |

## Trade-offs

- Vuetify: Splitter・drag並べ替えの標準部品がなく、小さな自作または追加MITライブラリが必要。Material系の
  見た目を業務用の密度へ寄せる調整が必要。本体CSSが大きい（raw 314 KB、ローカル配信なので許容）。
- Node/Viteのbuild chainとnpm依存のライセンス・供給元管理がリポジトリに加わる。
- framework非依存コアは薄い購読adapterを要するが、Vue/React/Electronの選択を後から変えやすくする。

## Migration

[frontend-architecture](../architecture/ui1/frontend-architecture.md#4-段階移行): S0（基盤・flag・CSP nonce・配信route）→
S1設定 → S2環境 → S3文字起こし/キュー → S4モデル/履歴/旧wizard → S5 v1接続 → S6既定切替。

## Rollback

S0〜S4はflagで旧UIへ戻すだけ（backendデータ形式不変）。S5のv1 activateは一方向migrationを伴うため、既存の
backup復元手順に従いowner判断の後に行う。S6は設定1か所の切替で戻せるようにする。

## Acceptance & Open questions（2026-11-04更新）

| # | 提案時の論点 | 状態 |
|---|---|---|
| 1 | Vue+Vuetify / React+MUI | **既決**: Vue+Vuetify（owner採択） |
| 2 | Windows VMのWebView2で表示・CSP nonce・`EventSource`・D&D・DPI・起動時間・RAMを計測 | **未実施**（UI-2もLinux headless Chromiumのみ）。新UI既定化・配布受入の必須ゲートとして保持 |
| 3 | ファイル別overrideの保存先・継承規則（G-05）、入力リスト永続化（G-06） | **未決**（UI-2では永続化しない。UI-3設計時にowner判断） |
| 4 | CSP nonce（G-11）・新資産route＋flag（G-12）のbackend小変更 | **既決・実装済み**（UI-2、既定off。[design](../architecture/ui2/design.md)） |
| 5 | build成果物のcommit有無 | **既決**: commitしない。CIでbuildしwheelへ同梱（hatch `artifacts`）、PyInstallerはbuild済みなら同梱 |
| 6 | 旧UI欠陥D-01〜D-07の延命修正 | **当面据置**: UI-2では旧GUIを修正しない。P1（D-01、D-04）の短期hotfixは別PRの提案のみ（owner承認待ち） |

追加の未決: private DB retention/ACL/完全削除（G-15）はowner判断待ちのまま。新UIはDBに触れない。
