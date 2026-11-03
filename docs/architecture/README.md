# Architecture documents — where to start

更新: 2026-11-03 JST（Backend Foundation Hardening、開発版 0.1.39・未リリース）

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

## 歴史資料（historical, 書き換えない）

- [phase-a/](phase-a/README.md): **2026-10-30時点の提案**。`phase-a/openapi.yaml`はB5〜B9の現行実装を
  自動的に表す正本ではない（差異は[interfaces.md](backend/interfaces.md#phase-a-openapiとの差異)）。
- [b1/](b1/README.md)〜[b9/](b9/README.md): 各Phaseの設計・提出・レビュー修正・検証の記録。
  文中の「Draft・未マージ」等は**提出時点**の状態。現在の統合状態は[backend/README.md](backend/README.md)を見る。
- [integration-2026-11-02.md](integration-2026-11-02.md): Phase A〜B6のmain統合記録（B7〜B9は追補）。

過去の監査・検証の事実は改変せず、現行情報へのリンクで誘導する方針とする。
食い違いを見つけた場合は、コード → offline schema → backend/*.md → 各Phase資料 の順に優先する。
