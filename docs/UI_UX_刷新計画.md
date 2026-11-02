# UI/UX刷新計画・次期開発引継ぎ

> ステータス: **計画・引継ぎ確定、実装未着手**（2026-10-30）。
> GitHub Issuesを残タスクの正本とする。現行の確定仕様は [要件定義.md](../要件定義.md)、開発履歴は [AISTATE.md](../AISTATE.md) を参照。
> 本文書は次期UI刷新の**作業方針**であり、以下の未実装機能がすでに動くという意味ではない。

## 1. ユーザー要望・優先順位

- ボタンを押しても応答がない、画面が更新されないなど、現行GUIの操作信頼性に不満がある。再現条件・根本原因は**まだ未確定**。
- 次期開発ではGUI/UXを大幅改修し、独自の手作り部品だけに依存しない**成熟したUIキット・フレームワーク**を検討する（Bootstrapは候補、決定事項ではない）。
- **CLI/coreは引き続き実行・仕様の基盤**としつつ、通常のセットアップ、文字起こし、進捗管理、結果確認、モデル管理は**GUIを主導線（GUI-first）**とする。
- デザイン刷新そのものより先に、**操作の即時フィードバック、非同期状態の整合性、画面更新、エラー可視化**を保証する。
- UI/UX作業はWindows VMを主環境にして進める。重いASRモデル・GPU実推論の検証は後段の対象実機で行う。
- 既存機能の劣化や過去の不具合の再発を避け、段階移行する。大規模な一括書き直しを最初から選ばない。

## 2. 技術的な現在地（2026-10-30、リポジトリ確認）

| 領域 | 現行構成 |
|---|---|
| GUI実行 | FastAPI（localhost）+ pywebview / Windows Edge WebView2 |
| frontend | `src/utteran_gui/web/index.html`, `app.js`, `styles.css`, i18n assets |
| frontend規模 | `app.js` 約1,985行。イベント登録・表示・状態管理・API通信が同一ファイルに存在 |
| backend API | `src/utteran_gui/api.py`。ジョブ、モデル、キュー、ウィザード、履歴、設定等のAPI、SSE |
| GUI起動・OS連携 | `src/utteran_gui/app.py`。ネイティブwindow、ダイアログ、D&D等 |
| CLI接続 | `src/utteran_gui/cli.py` ほか。profileのCLIを子processとして使用 |
| 配布 | installer / portable / Python直起動。SAC/WDACは別Issueで追跡 |

**Electronは現状未採用**。pywebviewはWebView2上でWeb技術を使えるため、BootstrapなどのUIキットを選ぶためだけにElectronへ移る必要はない。

観測事実は「フロントエンドが大きな単一JSファイルである」「ユーザーが無反応・更新漏れを経験している」の2点。両者の因果関係は未検証であり、UI-1で実際に切り分ける。

## 3. アーキテクチャ原則

1. **core/CLIの処理仕様を正本**とする。GUIでbackend制約、推論判定、モデル互換性を重複実装しない。
2. GUIはprofile CLIを利用する現行分離の利点を維持。新フレームワークが推論coreへ直接依存しない。
3. 操作の各段階を明示的に状態化する。少なくとも `idle`、`submitting`、`running`、`success`、`error`、`cancelled` と、再試行・再接続・処理不能状態を表現する。
4. API / SSE / pollingには stale response、重複イベント、接続断、再接続、キャンセルと完了の競合に対する契約を設ける。
5. ボタン押下から結果表示までの経路を自動検証可能にする。単にCSS部品を入れ替えるだけで終わらせない。
6. GUIは完全ローカル動作を維持。**外部CDN必須にしない**。依存物・フォント・スクリプトは適切に同梱・ライセンス管理する。
7. ログやIssue/PRに機密会議の音声・本文・ファイル名・用語集・認証tokenを載せない。公開testは合成・匿名データを使う。
8. 既存のjob/resume、出力schema、ジョブ履歴、settings、native drop、keyring、installer/portableの互換性を壊さない。
9. 既存リリース手順に従う。実装変更時のversion/changelog/CIは `docs/リリース手順.md` を参照。**引継ぎの文書整理だけでは機能が実装されたと扱わない**。

## 4. 採用技術の判断方針（未決定）

UIキットとfrontend frameworkは別々に評価する。

| 判断対象 | 比較ポイント |
|---|---|
| Bootstrap等のUI kit | components、一貫した表示、accessibility、カスタマイズ、依存/ライセンス |
| React / Vue / Svelte / その他 | 状態更新モデル、型安全性、テスト、学習/保守負担、build chain |
| 現行pywebview/WebView2継続 | ネイティブダイアログ、D&D、配布サイズ、起動、既存API資産 |
| Electronへの移行 | 本当に必要な機能差、SAC/WDAC、配布サイズ、起動時間、メモリ、保守負担 |

UI-1では技術を**先に決め打ちしない**。現行課題の再現、依存の比較、最小スパイク、移行コストの推定を基にADRを残す。単一JSファイルの分割や型導入だけで解決できる領域と、ライブラリに任せた方がよい領域を区別する。

## 5. 開発フェーズとIssue

親Epic: [#18 次期GUI全面刷新](https://github.com/zawa356/Utteran/issues/18)。全体ロードマップ: [#17](https://github.com/zawa356/Utteran/issues/17)。

### UI-1: 現行GUI監査・再現・ADR → [#19](https://github.com/zawa356/Utteran/issues/19)

- 画面・操作一覧、`app.js`イベント、backend API、SSE、状態遷移を棚卸し。
- 無反応/更新漏れの原因を再現・分類。未再現は観測不足と明記。
- UI kit + framework + WebView hostの選定、移行方式、rollback、テスト方式をADR化。
- **次の専用チャットではここから開始する。**

### UI-2: 共通UI基盤・非同期状態管理 → [#20](https://github.com/zawa356/Utteran/issues/20)

- UI-1で選定した技術で、button、form、dialog、notification、loading、errorsを共通化。
- API / SSE / pollingの更新順序、エラー処理、タイムアウト、再接続、連打を統一。
- VM上のモックで正常系・異常系を検証。

### UI-3: GUI-first画面の段階移行 → [#21](https://github.com/zawa356/Utteran/issues/21)

- 入力 → 構成 → 実行 → 進捗 → 結果の導線。
- workspace、history、models、queue、settings、setup wizardなどを機能表で欠落管理。
- [#6 非技術者向け最適構成](https://github.com/zawa356/Utteran/issues/6)、[#1 用語集](https://github.com/zawa356/Utteran/issues/1)と統合（backendが未対応の機能をGUIだけ先行実装しない）。

### UI-4: E2E・VM/実機受入 → [#22](https://github.com/zawa356/Utteran/issues/22)

- VMではAPI mock・Web frontendテスト・起動/ウィンドウ/DPI等を主に検証。
- 対象実機ではWindows WebView2・installer/portable・GPU実推論・SAC/WDAC等の環境固有条件を分離して検証。
- [#15 実機受入追跡](https://github.com/zawa356/Utteran/issues/15)、[#7 SAC](https://github.com/zawa356/Utteran/issues/7)と連携。

## 6. VM主体の受入・品質基準

### VMで完了可能
- 独立frontendのbuild、lint、type check、component tests
- ブラウザ/WebView2相当でのボタン→API→画面更新テスト
- mockの成功/失敗/timeout、遅延、競合、SSE切断・復旧
- 各画面のlayout、theme、ja/en、keyboard、focus、modal、accessibility
- CLIを実推論させないstubによるモデル管理・キュー・ジョブ進捗テスト

### 対象実機に保留するもの
- 実モデル/ASR/diarization/GPU機種ごとの実推論結果
- Windows SAC/WDACやmanaged-device security policy
- 実Explorer D&D、file dialog、installer/portableの全組合せ
- 長時間処理・GPU負荷時のタイミング/中断挙動

**VMで通ったことを実機E2E合格と混同しない**。保留項目は対応Issueへ未完了として残す。

## 7. 既存機能回帰チェック対象

- 文字起こし（単体/フォルダ/batch、profile/backend/model/device/language、話者数、出力形式）
- モデル（一覧、取得・削除・verify、IR生成、progress、cancel）
- キュー（待機、実行、cancel、終了）、job resume / 中間成果物
- 結果/履歴（検索、filter、speaker label変更、再出力、仮想スクロール）
- セットアップ（hardware検出、HF token、profile、モデル、再開）
- テーマ/言語、設定、ダイアログ、OS picker / drag&drop
- GUI終了と子process cleanup、ログのredaction、installer / portableの既存挙動

## 8. 次チャット開始時の進め方

1. リポジトリ `zawa356/Utteran` の**最新main**とIssue #18, #19〜#22を確認（本書より新しい実装を優先）。
2. `README.md`、`要件定義.md`、`AISTATE.md`、`docs/リリース手順.md`、本書を読む。
3. GUIの現行src/testを調べ、無反応/更新漏れの診断可能性を把握する。実際の再現ができない場合は原因を断定しない。
4. **UI-1 / #19を実行**：構成・イベント/状態設計の監査、UI技術の比較、ADR、次のUI-2実装指示を整理。利用できるVM/ブラウザ/テスト環境を確認して着手。
5. 実装へ移るときはIssue/PRを紐付け、CLI/coreの互換性を維持。ユーザーの明示方針があるまではElectron移行やBootstrap採用を確定事項にしない。
6. 終了時はIssue状態・決定事項・実機未検証事項をGitHubへ反映し、次回も追えるようにする。

## 9. 現時点の完了/未完了

- **完了**: Issue分割、刷新方針のドキュメント化、次チャットへの引継ぎ整備。
- **未実施**: UI kit/framework選定、原因再現、UI変更、VM test、GPU実機検証、releaseビルド。
- 旧版のGUI bugfix履歴は完了済みのものもあるため、古いAISTATEの記述をすべて現在の不具合とみなさない。

## Phase A設計レビューへの参照（2026-10-30）

共通実行設定・profile隔離・API/イベント契約・永続queue・setup管理の次期設計案は
[Phase A成果物](architecture/phase-a/README.md) と [ADR-002](adr/ADR-002-structured-execution-contract.md) を参照。
**提案・レビュー待ちであり、実装済み仕様ではない。** frontend framework/UI kitは未決定。
Draft PR #23のADR-001候補とは比較のみを行い、無断取り込みや技術採択はしていない。
後続順序は契約/worker/backend基盤を先行する案を追加したもので、本計画の既存Issueを完了にしない。
