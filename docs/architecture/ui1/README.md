# UI-1 — 現行GUI監査・理想UI設計・技術選定（提案）

更新: 2026-11-04 JST。開発版 **0.1.40（未リリース）**。Issue [#19](https://github.com/zawa356/Utteran/issues/19)
（Epic [#18](https://github.com/zawa356/Utteran/issues/18)）。後続: [#20 UI-2](https://github.com/zawa356/Utteran/issues/20) →
[#21 UI-3](https://github.com/zawa356/Utteran/issues/21) → [#22 UI-4](https://github.com/zawa356/Utteran/issues/22)。

> **これは調査・設計・提案であり、新GUIは実装していない。** 既定GUIの挙動、backend、v1 opt-in境界は変更していない。
> 技術選定は**ownerの採択待ち**（[ADR-003](../../adr/ADR-003-ui-frontend-stack-and-host-boundary.md): Proposed）。

## 現在地（要約）

- 基点: `main` = `068e62fce1b35960703b534426adfbe373ff86d8`（2026-11-04の履歴書換え後の新履歴。旧履歴は混入させていない）。
- 現行GUIの操作・状態・API/SSE経路を棚卸しし、**非同期欠陥7件を現行コードの実行で再現**、旧SSEの再接続再送1件を
  実backendのtestで固定した。ユーザー報告の「無反応」の原因は断定していない。
- Vue+PrimeVue / Vue+Vuetify / React+MUI を同条件スパイクで比較。**第一推奨: Vue 3 + TS + Vite + Vuetify 4 ＋
  framework非依存の非同期コア**、代替: React + MUI。PrimeVueはライセンス（5.x以降は独自のPrimeUI License化（無料Community版は条件付き））と保守（MITの4.xは凍結）で不推奨。
  host は **pywebview + WebView2継続**（Electronは保留、bridgeで隔離）。
- 通常モードA / Expert 3ペインC、共通設定＋ファイル別override、不成立構成の非表示（backend正本）を設計。
- backendギャップ15件（G-01〜G-15）を列挙。UI-2の前提となる小変更はCSP nonce（G-11）と新資産配信route（G-12）。

## 読む順番

| # | 文書 | 内容 |
|---|---|---|
| 1 | [interaction-inventory.md](interaction-inventory.md) | 画面/DOM/handler/API/状態/エラー/テストの一覧と依存図 |
| 2 | [defects-and-repros.md](defects-and-repros.md) | 実証済み欠陥D-01〜D-08、推定リスクR-01〜R-11、実機未確認 |
| 3 | [framework-comparison.md](framework-comparison.md) | 同条件スパイクの実測、UIキット部品・ライセンス、Vue/React、WebView2/Electron、CSP |
| 4 | [ux-structure.md](ux-structure.md) | 通常A / Expert C、状態表示、ready判断、キュー操作の粒度、旧→新対応表 |
| 5 | [frontend-architecture.md](frontend-architecture.md) | 層の責務、データフロー、競合対策、段階移行S0〜S6、rollback、E2E、UI-2の分割 |
| 6 | [backend-gap-matrix.md](backend-gap-matrix.md) | UI要求と現行B4〜B9/旧APIの差分、Issue候補 |
| 7 | [validation.md](validation.md) | 環境・コマンド・結果・CI・未検証事項・WebView2次回手順 |
| — | [ADR-003](../../adr/ADR-003-ui-frontend-stack-and-host-boundary.md) | 技術選定の提案ADR |

再現・スパイクのツール: [tools/ui1/](../../../tools/ui1/README.md)、testは`tests/test_ui1_legacy_sse.py`。

## 未受入ゲート（UI-1では確認していない）

- Windows WebView2 / pywebview上の実行（旧GUIの再現、スパイク、`EventSource`、D&D、file dialog、DPI 125/150/200%）。
- 配布版（installer/portable）、SAC/WDAC、実モデル・GPU・実音声。
- B9 SSEの実ブラウザ受入、B4 ready証拠・B8実setup（backend側の未受入、[backend/README](../backend/README.md)）。

## ownerが選択・承認すべき論点（UI-2着手前）

1. frontend案の採択（Vue+Vuetify / React+MUI）。ADR-003の Open questions 参照。
2. Windows VMのWebView2で同じスパイクと再現ハーネスを実行して判断するか。
3. ファイル別overrideと入力リストの保存方針（G-05、G-06）、private DB retention（G-15）。
4. UI-2前提のbackend小変更（G-11 CSP nonce、G-12 新資産route＋flag）を認めるか。
5. 旧`app.js`の欠陥を小修正PRで延命するか、新UIで解消するまで据え置くか。

## PR #23との差分

[PR #23](https://github.com/zawa356/Utteran/pull/23)（Open/Draft、`docs/ui-1-audit-adr`、旧履歴の`1097aef`を基点に監査、
ADR-001候補1ファイル）は**読み取りのみ**。編集・Ready化・merge・closeしていない。コードとcommitは取り込んでいない。

| 項目 | PR #23 | 本UI-1 | 扱い |
|---|---|---|---|
| 基点 | 旧履歴`1097aef`（書換え前、B5〜B9統合前） | 新履歴`068e62f`（B7〜B9統合後） | 行番号・backend前提を現行で再確認 |
| `loadEnvironment`競合 | 関数抽出＋mock DOMで巻戻り | **実ファイル・実DOM・実CSP・実`<select>`**で巻戻り（D-01） | 結論一致。知見のみ再利用 |
| その他の欠陥 | 静的リスクとして列挙（wizard polling、queue catch、settings、cancel、finishJob重複の可能性） | D-02〜D-07を**実行で再現**、D-08をbackend testで固定、R-01〜R-11を区別 | 重複箇所は再検証済み |
| 技術比較 | Vue+PrimeVueを暫定推奨（PrimeVueをMITと記載） | 3案の同条件スパイク。**PrimeVue 5.xの独自ライセンス化・MIT版4.xの凍結を確認し不推奨**、Vuetify推奨 | PR #23の採択は前提にしない |
| backend前提 | B5〜B9以前 | v1 opt-in・409 `execution_unavailable`・progressなし等を反映 | gap表へ |
| UI要件 | 理想UI（通常A/Expert C）以前 | 2026-11-04合意の要件を反映 | 新規 |
| ADR番号 | ADR-001 | ADR-003（競合回避） | 両者は別ファイル |
