# Architecture documents — where to start

更新: 2026-11-12 JST（UI-3 S5-A 新UIからv1単一ジョブ、開発版0.1.51・未リリース）

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

- **UI-3 S5-A（2026-11-12、`0.1.51`未リリース、Draft PR）**: [ui3/s5a](ui3/s5a-v1-single-job.md)。`--next-ui-v1`に限り、Vueの単一入力を保存済みB5 Preset/Policy（revision凍結、初回は明示作成）→ B5 validation（画面と同じ有効設定のときだけ）→ B7 resolution → B7 durable job → `GET`の状態表示へ接続。サーバーが`/next/`に書く`utteran-api-mode` metaだけでモードを決め、v1では旧投入を呼ばない。結果不明は同key・同bodyの明示再照合のみ、queue `paused`は明示再開のみ、取消は協調的のみ。B5 storeの初回並行openの競合を修正。`--next-ui`・既定は不変。Windows WebView2実機・実モデルは未受入。
- **B4 ready/smoke（2026-11-12、`0.1.50`未リリース、Draft PR）**: [b4/smoke](b4/smoke.md)。開発専用の明示smoke入口（`python -m utteran_gui.capability_smoke`、専用markerのrootのみ・GUI稼働中は拒否）、profile内の単発子process、version付きprivate evidence（digestと固定codeのみ、24時間TTL、fail-closed読取）、`refresh/resolve`とB7 prestartでの読取専用統合。fw-cpuだけがready到達可、fw-cudaは`unknown`。実機手順は[smoke-local-acceptance](b4/smoke-local-acceptance.md)（**未実施**）。
- **G-09（2026-11-11、`0.1.49`未リリース、Draft PR）**: [ui3/g09](ui3/g09-next-ui-v1-startup.md)。暫定・開発専用`--next-ui-v1`（専用markerのdata rootのみ、ポート先取り後に実authorityでB5〜B9）、同一data rootのGUIプロセス排他（全モード、OSロック。CLI・旧バイナリは対象外）、停止順序と`shutdown()`結果code。既定・`--next-ui`・API・schema・DBは不変。新UIのv1製品接続は未完了（旧投入は409）。
- B9 **Issue #37（2026-11-11、`0.1.48`未リリース、Draft PR）**: 別epochの`/sync`でqueue revisionも再初期化（Python `EventReducer`・TS port・共通fixture。[b9/sync](b9/sync.md#epochとrevisionの有効範囲issue-370148)）。API・schema・DB・旧GUI・v1の既定は不変。新UIの正規の接続先は`/api/v1`（owner方針、[ui2/migration-next](ui2/migration-next.md)冒頭）。
- [ui3/](ui3/README.md): **UI-3 S3-B1（2026-11-10、`0.1.47`未リリース、Draft PR）**: [s3b1-multi-input-and-admission](ui3/s3b1-multi-input-and-admission.md)。`--next-ui`で複数入力の一覧・ファイル別override・出力先、旧`POST /api/jobs`を1件ずつ順に呼ぶ段階的な投入（トランザクションではない。全件preflight・投入時凍結・単件と共通のsingle-flightゲート・4xx=拒否/応答なし・5xx=結果不明で停止）。複数ジョブの監視はS3-B2、B7/同一入力の再実行/出力命名はS5。backend変更なし、Windows実機未受入。
- 同上 **UI-3 S3-A（2026-11-09、`0.1.46`）**: [s3a-legacy-transcription](ui3/s3a-legacy-transcription.md)。`--next-ui`で1ファイルの文字起こしを旧`/api/jobs`へ投入・進捗・取消（話者分離なし、GETが状態の正本、非冪等POSTは自動再送しない、legacy queueは非永続。backend変更なし、Windows実機未受入）。S2は[s2-environment-visibility](ui3/s2-environment-visibility.md)（`0.1.45`）。
- 同上 **UI-3 S2（2026-11-09、`0.1.45`未リリース、Draft PR）**: [s2-environment-visibility](ui3/s2-environment-visibility.md)。`--next-ui`の実行環境表示を旧`GET /api/environment`の観測結果で完成（旧環境stripの4項目・Expert詳細、readyとは表示しない。B4 readiness＝G-01は後続、backend変更なし、Windows実機未受入）。
  **UI-3 S1（2026-11-09、`0.1.44`）**: [s1-settings-and-host](ui3/s1-settings-and-host.md)。`--next-ui`の設定画面を旧`/api/settings`・`/api/token`・`/api/open-folder`・`/api/wizard/status`とhost `pickPath`へ接続（backend変更なし、Windows実機未受入）。
  **UI-3-0設計確定（2026-11-06、`0.1.43`）**。[design-freeze](ui3/design-freeze.md)が採用仕様・証拠・未実装とS1〜S5引継ぎの正本。[PR #38](https://github.com/zawa356/Utteran/pull/38)をmainに統合（`701a94e`、CI成功）。通常1ファイル中心/Expert可変3ペイン、モデル選択、キューと履歴の分離、個別出力先・用語集一括入力は**合成プロトタイプの設計**。実APIとWindows配布は未受入、UI-3 #21は継続。

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
