# UI-1 backendギャップ表（UI要求 × 現行実装）

更新: 2026-11-04 JST。基点 `main` = `068e62f`。正本は実コードと[backend/](../backend/README.md)。
**ここに挙げた未提供機能を、UI-2/UI-3で「実装済み」の前提にしない。** 各Gは独立のbackend Issue候補である。

## 前提の実査結果

| 確認事項 | 結果 | 根拠 |
|---|---|---|
| 既定GUI起動はv1を有効化しない | 確認。`create_app(session_key, repo_root=project_root())`のみ | `src/utteran_gui/app.py:191` |
| B5〜B9はopt-in | 確認。`enable_v1_*`引数でのみmount | `src/utteran_gui/api.py:134-228` |
| 実機Jobは通常`409 execution_unavailable` | コード上の経路を確認（B4 ready証拠がないとadmission拒否）。標準collectorにsmoke providerなし | `job_admission.py:644`、[backend/README](../backend/README.md) |
| 公開能力一覧`GET /capabilities`・`/environments` | **未実装**（routeなし。B4は`capability_inventory.py`のbridgeでB7内部から使用） | `grep`でroute不在、[interfaces.md](../backend/interfaces.md#phase-a-openapiとの差異) |
| B9 SSEのイベント種別 | `state_changed`、`queue_changed`、`resync_required`のみ。progress / inventory_changed / fallbackは配信しない | `utteran_control/store.py:602`、`utteran_contracts/events.py:57` |
| B9 SSEの実ブラウザ検証 | TestClientのみ。**実`EventSource`/WebView2は未受入** | [backend/README](../backend/README.md) |
| force cancel | `cancel_mode_unsupported`（409） | `jobs_api.py:155`、`setup_api.py:248` |
| B8実setup/model DL/GPU | fake child runnerのみ。実機未受入。whisper.cpp native build・OpenVINO・既存venv rebuildは拒否 | [backend/README](../backend/README.md) |
| migration v6 / private DB retention・ACL | v1→v6一方向。retention・削除・暗号化・ACLは**owner判断待ち** | [persistence-recovery](../backend/persistence-recovery.md) |
| 旧queueとv1 queue | v1 activate時は旧queueが閉じ、旧jobs/wizard投入は409 | [interfaces.md](../backend/interfaces.md) |

## ギャップ一覧

| ID | UI要求 | 現行 | ギャップ | 種別 | UI側の暫定扱い |
|---|---|---|---|---|---|
| G-01 | 成立する構成だけを表示し、採用backend/device/根拠を確認できる | B4 resolverはあるが公開v1 APIなし。旧`/api/environment`の`options`はready証拠を持たない | `GET /capabilities`（または同等のread-only bridge）とready証拠の公開DTO | backend API | 旧`options`表示のまま「未検証」を明示。readyを偽装しない |
| G-02 | frontendに互換表を持たない | 旧`app.js`の`genaiIsBlocked()`がGenAI×言語×話者分離をfrontendで判定 | 判定をbackendのoptions/resolverへ移す | backend＋UI | 新UIへ移植しない |
| G-03 | 進捗表示 | v1 SSEにprogressなし。旧SSEは工程比率をfrontendが推測 | 永続化しない一時progress channel（またはv1 progressイベント）の契約 | backend契約 | v1では工程・状態のみ。%を推測しない |
| G-04 | キュー件数・状態の即時反映 | B9 `/sync`＋`/events`はあり（opt-in、実ブラウザ未受入） | WebView2での`EventSource`再接続・410・keepaliveの実機受入 | 受入 | 受入までpolling fallback（単一in-flight）を残す |
| G-05 | 共通default＋ファイル別override | B5は共通Preset/Policy。ファイル別overrideの保存先・継承規則なし | override DTO（B7 batch内の差分か、B5の派生Presetか）の決定 | 設計判断（owner） | 未投入のoverrideは非永続と明示 |
| G-06 | 入力一覧（複数ファイル）の永続化 | 旧GUIは1入力（フォルダ＋glob）。B7 batch投入はあるが「投入前の作業リスト」は無い | ワークスペース（入力リスト）を保存するか否か。private DB retention方針に依存 | owner判断 | セッション内のみ保持 |
| G-07 | 実機Jobを新UIから実行 | 標準collectorにsmoke ready証拠なし→409 | 実smoke provider・B4 ready昇格の受入 | backend＋実機 | 409を「未検証環境」と説明し、旧経路を案内 |
| G-08 | 新UIのSetup（B8） | API・実行器はあるがfake runnerのみ受入 | 実setup.ps1・実DL・GPUの受入、B8成功→B4 readyの関係定義 | 実機受入 | 旧wizardを維持（S4）、B8画面はS5以降 |
| G-09 | 旧/新UI併存時の正本 | 旧queue（in-memory）とv1 queue（DB）は同一プロセス内で排他。**0.1.49で一部解消**: 開発専用`--next-ui-v1`（専用markerのdata rootのみ）、同一data rootの協調GUIプロセス間のOSロック（3モード共通、kill後にOSが回収）、ポート先取り後のv1構築、旧投入の早期409、shutdownの結果code（[ui3/g09](../ui3/g09-next-ui-v1-startup.md)） | **残り:** CLI・旧バイナリ・非協調プロセスとの排他（ガード対象外）、新UIのv1投入接続、製品としての切替単位とrollback手順のowner判断 | 設計＋運用 | `--next-ui`はv1を有効化しない。v1は開発用rootでのみ。旧画面へ戻ってもv1を自動deactivateしない |
| G-10 | 旧SSEの再接続 | `Last-Event-ID`無視・cursor 0から再送（D-08） | 旧経路を延命するなら再開対応、しないならUI-2で旧SSEを使わない | backend小修正 or 方針 | UI-2のtransportでidによる重複排除 |
| G-11 | UIキットの実行時style | CSPが`style-src 'self'`のみ（nonceなし） | index配信ごとのnonce生成とCSPヘッダの一致 | backend小修正 | `'unsafe-inline'`は使わない |
| G-12 | 新frontend資産の配信 | `/assets`は`web/`固定 | build成果物（例`web-next/`）の配信route、旧UIとの切替flag | backend小修正 | — |
| G-13 | エラーの利用者向け説明 | 旧APIは`detail`文字列、v1は固定code | v1 code→日英文言の対応表（frontend i18n）。未知codeの汎用表示 | UI＋契約 | — |
| G-14 | ファイル追加（複数・D&D） | D&Dは1件のみ受理（`handleDroppedInput`）、bridgeは複数pathを渡せる | 複数入力の受理はUI側で可。backendのbatch上限・検証はB7に従う | UI | — |
| G-15 | private DB retention・ACL | 未決定 | owner判断 | owner | 新UIは保存範囲を明示 |

## UI-2 / 別Issueへの振り分け案

- **UI-2（frontend基盤）で扱う**: G-10（新transportで重複排除）、G-13（code→文言）、D-01〜D-07の再発防止をコアで担保。
- **UI-2の前提となる小さなbackend変更（別PR）**: G-11（CSP nonce）、G-12（新資産の配信と切替flag）。
- **別backend Issue**: G-01、G-02、G-03、G-07、G-08（実機）、G-04（WebView2受入、UI-4と連携）。
- **owner判断**: G-05、G-06、G-09の製品としてのrollback方針（起動基盤は0.1.49で実装）、G-15。
