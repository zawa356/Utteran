# ADR-003: 次期GUIのfrontend構成とhost境界

- Date: 2026-11-04
- Status: **Proposed — owner review required**（UI-1 [#19](https://github.com/zawa356/Utteran/issues/19)、Epic [#18](https://github.com/zawa356/Utteran/issues/18)）
- Examined main: `068e62fce1b35960703b534426adfbe373ff86d8`（2026-11-04の履歴書換え後）
- 番号: mainには`ADR-002`のみ存在。`ADR-001`はDraft [PR #23](https://github.com/zawa356/Utteran/pull/23)が使用中のため、
  競合を避けて`ADR-003`とした。PR #23のADR-001は参照のみで、本ADRはそれを正本化しない。

## Context

- 現行GUIはFastAPI＋pywebview（Windows WebView2）＋手書きHTML/JS（`app.js` 1,984行に状態・描画・API・i18nが同居）。
- 「ボタン無反応・更新漏れ」の報告がある。UI-1で現行コードを実行し、**7件の非同期欠陥を再現**した
  （stale応答の巻戻り、catchなしの無反応、終了処理の二重実行、先行ジョブによる後続ジョブの追跡停止、
  取消失敗の固着、polling多重化、旧SSEの再接続再送）。原因はUIキットの欠如ではなく、
  request世代・終了ガード・エラー表示・単一in-flightの欠如（[defects-and-repros](../architecture/ui1/defects-and-repros.md)）。
- 理想UI要件（2026-11-04合意）: GUI-first、通常モードA＋Expert 3ペインC、常設sidebar、共通設定＋ファイル別override、
  不成立構成の非表示（backend正本）、成熟UIキット優先、日英・keyboard・DPI・オフライン同梱。
- backendのv1（B5〜B9）はopt-inで既定GUI未接続。公開能力API・progressイベント等は未実装（[gap](../architecture/ui1/backend-gap-matrix.md)）。

## Decision (proposed)

1. **hostはpywebview＋WebView2を継続**し、frontendからのhost依存を`host bridge`（`pickPath`/`onDroppedPaths`/
   `reportError`/`openFolder`）に閉じ込める。Electronは採用しないが、bridge差替えで移行可能な構造にする。
2. **frontendは Vue 3 + TypeScript + Vite + Vuetify 4（MIT）を第一候補**とする。
3. **非同期の正しさはframework非依存のTypeScriptコア**（API client、transport、domain store、純粋reducer）で担保し、
   view（Vue SFC）はkit標準部品とコアのselector/actionのみを使う。Pinia等の追加state libraryは必須にしない。
4. **代替: React + TypeScript + Vite + MUI（Core＋MIT範囲）**。同じコアをそのまま使う。
5. **PrimeVueは採用しない**（4.xは新規開発停止・security fixのみ、5.x以降はlicense key必須の商用ライセンス）。
6. 旧`app.js`は画面単位で段階移行し、flagで旧UIへ戻せる状態を既定切替まで維持する。

## Evidence

- 同一縦切りスパイク（[framework-comparison](../architecture/ui1/framework-comparison.md)、
  [tools/ui1/spike](../../tools/ui1/spike/README.md)）: Vue+PrimeVue 4.5.5 / Vue+Vuetify 4.3.2 / React+MUI 9.5.0 とも
  同一Playwright 7シナリオ**7/7 pass**（Chromium 153 headless）。差はframeworkではなくkit・依存・CSPに出た。
- payload（JS gzip）: PrimeVue 94 KB、Vuetify 120 KB（＋CSS 45 KB）、MUI 143 KB。本番依存: 31 / 50 / 84。
- 現行CSP（`style-src 'self'`）での違反: PrimeVue 32、Vuetify 2、MUI 5。nonce対応で全て0。
- ライセンス: 実パッケージ`primevue@5.0.4`の`LICENSE.md`は商用PrimeUI License。`primevue@4.5.5`はMIT。
  Vuetify・MUI CoreはMIT。MUI Xは列リサイズ・複数ソート（DataGrid）、DnD並べ替え（Tree View）がPro（公式licensing）。
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

## Acceptance & Open questions

採択前にownerが判断・確認すること:

1. Vue+Vuetify（第一推奨）とReact+MUI（代替）のどちらを採るか。
2. Windows VMのWebView2で同じスパイクを実行し、表示・CSP nonce・`EventSource`・D&D bridge・DPI 125/150/200%・
   起動時間・プロセスRAMを計測する（[validation](../architecture/ui1/validation.md#windows-webview2-実機での次回試験手順)）。
3. ファイル別overrideの保存先と継承規則（G-05）、入力リストの永続化可否（G-06）。
4. CSP nonce導入（G-11）と新資産配信route（G-12）の小さなbackend変更をUI-2前提として認めるか。
5. build成果物をcommitするか、CIでbuildして同梱するか（installer/portableの作り方に影響）。
6. 旧UIの欠陥（D-01〜D-07）を新UI完成まで放置するか、小修正PRで延命するか。
