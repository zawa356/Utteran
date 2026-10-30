# Phase A — アーキテクチャ・API契約案

- 日付: 2026-10-30（日本時間）
- 状態: **Proposed / ユーザーレビュー待ち**。本番機能・UI・データ移行は未実装。
- 調査 main: `1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`（作業開始時に fetch で確認）。
- 指示書: 「Utteran 開発指示書 — Phase A：アーキテクチャ再設計・API契約策定」。
- 既存仕様の正本は [要件定義](../../../要件定義.md)、未完了作業は GitHub Issues。本ディレクトリは将来契約の提案であり、現行実装の仕様を置き換えない。

## 読む順序と成果物

| 成果物 | 内容 |
|---|---|
| [現行監査](audit.md) | コード上の根拠、責務、設定経路、Issue・PR #23との差分 |
| [ADR-002](../../adr/ADR-002-structured-execution-contract.md) | 依存方向、共有契約、プロセス境界、代替案 |
| [データ・API契約](contracts.md) | 継承・解決・保護・REST・SSE・IPCの規約 |
| [OpenAPI 3.1案](openapi.yaml) | 全提案エンドポイントと型。実装済みAPIからの生成物ではない |
| [合成例](examples.json) | 上書き、解決、投入、エラー、イベントの機械可読例 |
| [状態遷移](lifecycles.md) | setup、environment、selection、job、queue、fallback、復旧 |
| [移行・受入計画](migration.md) | 小規模PR、依存関係、互換性、ロールバック、テスト |
| [判断待ち・検証記録](review.md) | リスク、未検証事項、実行結果、完了条件 |

新契約の規範は contracts / lifecycles / OpenAPI を合わせたもの。HTTP上の形・必須項目は OpenAPI、実行時の意味・交差制約・原子性は本文に定義する。不一致は実装時に解釈で補わず、契約PRで解消する。

## 要約

軽量な `utteran_contracts`（提案名、未作成）へ通信DTOと純粋な形の検証だけを分離し、GUIもCLIもこれに依存する。推論・対応構成判定の正本はprofile processのcoreに残す。GUI backendは永続的な投入・キュー・セットアップ計画を管理し、profile childへversion付きJSON要求を標準入力で渡す。独立CLIの既存オプションは同じcore application serviceへ変換する。常駐推論serverは要求しない。

Profile、Preset、Selection Policy、Resolved Spec、Job Snapshotは独立。投入済み設定は不変で、実際のfallbackは別のattempt記録に追記する。初回・追加環境は承認済みSetup Planを同じbackend orchestrationで実行する。frontend技術は選定しない。

## スコープと追跡

Refs [#17](https://github.com/zawa356/Utteran/issues/17)、[#18](https://github.com/zawa356/Utteran/issues/18)、[#19](https://github.com/zawa356/Utteran/issues/19)、[#20](https://github.com/zawa356/Utteran/issues/20)、[#21](https://github.com/zawa356/Utteran/issues/21)、[#22](https://github.com/zawa356/Utteran/issues/22)、[#1](https://github.com/zawa356/Utteran/issues/1)、[#6](https://github.com/zawa356/Utteran/issues/6)、[#12](https://github.com/zawa356/Utteran/issues/12)。どのIssueも本設計だけで完了・closeしない。

[Draft PR #23](https://github.com/zawa356/Utteran/pull/23) のhead `810c5c9fda118f34fb0a011c7be221b7f8bfe15b` にあるADR-001をGitから参照した。取り込み・編集・mergeはしていない。ADR-002の番号はその参照との衝突を避けたもので、ADR-001の採択やmainへの存在を意味しない。

今回は文書のみという明示指示を優先し、リリース手順の日常patch version更新は行わない。`pyproject.toml`・lockfile・Python・JS・HTML・CSSは変更しない。Ready化・merge・次フェーズ実装へ進まない。
