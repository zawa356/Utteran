# Architecture documents — where to start

更新: 2026-11-05 JST（UI-3-0 画面設計・隔離プロトタイプ、開発版 0.1.43・未リリース）

このディレクトリには **「現在の実装の正本」** と **「各Phaseの提出・検証記録（歴史資料）」** が混在する。
UI刷新（U0〜U3）やバックエンド変更を始める前に、まず次の正本を読むこと。

## 現在の実装の正本（current）

| 読む順 | 文書 | 内容 |
|---|---|---|
| 1 | [backend/README.md](backend/README.md) | 現在地、機能ごとの提供状況（実装済み/限定/未実装/実機未検証）、既知の制約 |
| 2 | [backend/components.md](backend/components.md) | 実コードのモジュール責務・依存境界・起動経路（Mermaid図） |
| 3 | [backend/interfaces.md](backend/interfaces.md) | `create_app()`の有効化条件、現行route一覧、DTO正本、状態遷移、Phase A OpenAPIとの差異 |
| 4 | [backend/persistence-recovery.md](backend/persistence-recovery.md) | SQLite layout v1〜v6、migration、backup/restore、排他・fencing・復旧、privacy |
| 5 | [backend/testing-and-operations.md](backend/testing-and-operations.md) | CIジョブの守備範囲、E2E、実機受入ゲート、障害時の確認手順 |
| — | [backend/validation.md](backend/validation.md) | 作業開始時と完了時の実測証拠（CI run・commit・件数） |
| — | [backend/docs-audit.md](backend/docs-audit.md) | 既存文書の監査結果（updated / reviewed-no-change と理由） |

DTOとAPIの機械可読な正本は、コード（`src/utteran_contracts/`）と、CIで`--check`されるoffline schema
（[b1](b1/contracts.schema.json)、[b3](b3/worker.schema.json)、[b4](b4/capabilities.schema.json)、
[b5](b5/preview.schema.json)、[b6](b6/control.schema.json)、[b7](b7/jobs.schema.json)、
[b8](b8/setup.schema.json)、[b9](b9/events.schema.json)）である。

## UI刷新（UI-1 設計 → UI-2 基盤 → UI-3-0 画面設計）

- [ui3/](ui3/README.md): **UI-3-0 画面設計と隔離型プロトタイプ**（2026-11-05、0.1.43）。通常A1（縦ステップ）／Expert C1（3ペイン）の推奨案、
  合成データだけで操作できるVite dev server専用のプロトタイプ（production buildに含まれない）、レビュー画像の再現手順、S1–S5への引継ぎ。
  第2イテレーション（[ui3/iteration-2](ui3/iteration-2.md)）で通常＝topbar＋1ファイル、Expert＝リサイズ可能なsidebar＋3ペイン、緑配色案、架空の環境mockへ改めた。
  第3イテレーション（[ui3/iteration-3](ui3/iteration-3.md)）で可読性の下限、説明文の削減、Expertの①→②→③と簡易キュー、共通メニューとログ・診断・Aboutのmock、
  設定の通常/Expert分離、工程内進捗・推定残りのmock（G-03の提案）を加えた。
  第4イテレーション（[ui3/iteration-4](ui3/iteration-4.md)）でExpertの処理設定5カテゴリ（Prompt・用語集・エンジン固有項目）、初回セットアップウィザード（B8のplan→承認→工程に対応）、
  セットアップ画面の「セットアップ…」、狭幅の縦積み廃止、ログ・プリセットの入出力確認画面、診断の簡素化、既存アイコン、緑/青テーマを加えた。
  **owner承認前の設計案で、機能移行ではない。**
- [ui2/](ui2/README.md): **次期GUIのfrontend基盤（S0）とopt-in新UI**（2026-11-04、0.1.41→0.1.42。Windows WebView2で条件付き受入、DPI 200%の初期窓に制約）。Vue 3 + Vuetify 4、
  `utteran-gui --next-ui`のときだけ表示。既定GUIは旧UIのまま。設計・実測・UI-3への移行計画。
- [ui1/](ui1/README.md): 現行GUI監査・再現・技術比較・理想UI情報設計・次期frontendアーキテクチャ・backendギャップ（2026-11-04）。
  技術選定は[ADR-003](../adr/ADR-003-ui-frontend-stack-and-host-boundary.md)（**Accepted**、2026-11-04 owner採択）。

## 歴史資料（historical, 書き換えない）

- [phase-a/](phase-a/README.md): **2026-10-30時点の提案**。`phase-a/openapi.yaml`はB5〜B9の現行実装を
  自動的に表す正本ではない（差異は[interfaces.md](backend/interfaces.md#phase-a-openapiとの差異)）。
- [b1/](b1/README.md)〜[b9/](b9/README.md): 各Phaseの設計・提出・レビュー修正・検証の記録。
  文中の「Draft・未マージ」等は**提出時点**の状態。現在の統合状態は[backend/README.md](backend/README.md)を見る。
- [integration-2026-11-02.md](integration-2026-11-02.md): Phase A〜B6のmain統合記録（B7〜B9は追補）。

過去の監査・検証の事実は改変せず、現行情報へのリンクで誘導する方針とする。
食い違いを見つけた場合は、コード → offline schema → backend/*.md → 各Phase資料 の順に優先する。
