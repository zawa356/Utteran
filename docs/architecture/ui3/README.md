# UI-3-0 — 第5案で設計確定した隔離プロトタイプ

**2026-11-06 Design Freeze:** ownerがWindowsブラウザで第5案を概観し、細かなUI改修は後続として設計基準を承認。[PR #38](https://github.com/zawa356/Utteran/pull/38)をmainにマージ（`701a94e`）。開発版`0.1.43`、未リリース。**本番機能の完成・Windows WebView2実機受入ではない。**

現在のUI-3-0正本: **[設計確定・受入・S1〜S5引継ぎ](design-freeze.md)**。

**2026-11-09 UI-3 S1（本番、opt-in）:** `/next/`の設定画面を実APIとhost bridgeへ接続（テーマ・言語・既定プロファイル・既定入出力フォルダとOSのフォルダ選択・
Hugging Faceトークンの状態/登録/削除・ログ保存先と「ログフォルダを開く」・セットアップの状態表示と旧画面への移動）。正本は[s1-settings-and-host.md](s1-settings-and-host.md)。
開発版`0.1.44`、未リリース、Draft PR。**Windows WebView2実機は未受入。**既定GUIは旧画面のまま。
同日PR #40レビュー（P1×2・P2×1）を反映し、Windows CI（frontend lint/型/Vitest/build）を追加。**各PRはLinux＋Windows CIで判断し、Windows実機の手動確認は最終統合ゲートへ集約する**（[s1 §8](s1-settings-and-host.md#8-windows-webview2-実機受入未実施最終ゲートへ集約)）。

**2026-11-09 UI-3 S2（本番、opt-in、A案）:** 実行環境表示を既存`GET /api/environment`の観測結果で完成（旧環境stripの4項目・Expertの詳細カテゴリ・
要求/サーバー選択/設定の既定の区別・前回の結果・待機の中止と240秒の時間切れ）。**B4 readiness（G-01）は未解決の後続課題で、readyとは表示しない。**
正本は[s2-environment-visibility.md](s2-environment-visibility.md)。開発版`0.1.45`、未リリース、Draft PR。Windows WebView2実機は未受入（最終ゲートへ集約）。
同日PR #41レビュー（CIブロッカー1件・P2×2: 前回の結果の役割表示、モデル数/native variantの不明と0/なしの区別）を反映（[s2 §12](s2-environment-visibility.md#12-pr-41レビュー修正2026-11-09)）。

**2026-11-09 UI-3 S3-A（本番、opt-in）:** 通常モードで1ファイルの文字起こしを旧`/api/jobs`へ投入し、`GET /api/jobs/{id}`を正本に進捗・取消・終了を表示
（話者分離オフ、SSEは速報でevent idにより重複排除、非冪等POSTは自動再送せず結果不明を表示、legacy job/queueは非永続）。Expertは同じ1件を3ペインで表示。
正本は[s3a-legacy-transcription.md](s3a-legacy-transcription.md)。開発版`0.1.46`、未リリース（PR #42でmainへ統合）。Windows WebView2実機は未受入（最終ゲートへ集約）。

**2026-11-10 UI-3 S3-B1（本番、opt-in）:** 複数入力の一覧（picker追加・複数D&D・手入力の追加、上限100、重複は補助判定）、共通設定＋ファイル別override＋出力先、
旧`POST /api/jobs`を1件ずつ順に呼ぶ段階的な投入（全件preflight・投入時凍結・単件開始と共通の送信ゲート・拒否/結果不明で停止・停止は送信中をabortしない）。
受付サマリーは実行状態の正本ではない。正本は[s3b1-multi-input-and-admission.md](s3b1-multi-input-and-admission.md)。開発版`0.1.47`、未リリース、Draft PR。
S3-B2（複数ジョブの監視・取消・絞込み）とS5（B7・同一入力の独立再実行・出力命名）は後続。Windows WebView2実機は未受入。

## 設計基準

- 通常モード: 1ファイル中心、文字起こし・セットアップ・設定の短い導線。Expert: sidebar＋①ファイル②処理設定③キュー投入の可変3ペイン。
- 共通設定→ファイル個別設定→投入時の不変snapshot。通常/Expertは同じ作業状態を異なる画面で扱い、切替でデータを消さない。
- 標準/カスタムのモデル選択、話者分離のトークンと利用条件の独立確認。再承認で旧プランの完了工程を流用しない。初回成功時の主モデルが新規ジョブに反映される。
- キューからの片付けは表示だけ、履歴と出力情報は保持。同じ入力を設定違いで複数ジョブ化。既定＋ファイル個別出力先。用語集の複数行一括入力。
- 日英・青/緑・ライト/ダークと可読性・レスポンシブ。修復/初期化の破壊的操作は試作では行わない。

## 文書一覧

| 文書 | 役割 |
|---|---|
| [design-freeze.md](design-freeze.md) | **確定した設計判断・受入証拠・未実装とS1〜S5引継ぎ** |
| [s3b1-multi-input-and-admission.md](s3b1-multi-input-and-admission.md) | **S3-B1: 入力一覧・ファイル別override・出力先、段階的な受付（共通送信ゲート・preflight・凍結・部分受付）、旧APIの副作用、試験、S3-B2/S5引継ぎ** |
| [s3a-legacy-transcription.md](s3a-legacy-transcription.md) | **S3-A: 1ファイルの文字起こし（旧`/api/jobs`）、DTO、状態遷移、制約、試験、S3-Bへの未決ゲート** |
| [s2-environment-visibility.md](s2-environment-visibility.md) | **S2: 実行環境の可視化、API projection、旧strip対応、状態・中止・時間制約、試験、G-01と未受入** |
| [s1-settings-and-host.md](s1-settings-and-host.md) | **S1: 本番設定画面・OS連携の実装、API対応表、状態遷移、試験、Windows手順と未検証** |
| [iteration-5.md](iteration-5.md) | メモ#25〜#36、P1修正、最終レビューとCI |
| [iteration-4.md](iteration-4.md) | メモ#15〜#24、設定とWizard |
| [iteration-3.md](iteration-3.md) | メモ#6〜#14、操作導線・可読性 |
| [iteration-2.md](iteration-2.md) | メモ#1〜#5、画面配置・sidebar |
| [screen-design.md](screen-design.md) | 第1案A1/A2/C1/C2の比較履歴 |
| [interaction-and-component-map.md](interaction-and-component-map.md) | 初期状態図と第5案による追補 |
| [review-and-acceptance.md](review-and-acceptance.md) | 画面/試験再現手順、当時の結果、最終受入 |

## 起動と検証

`frontend/` で `npm ci`、`npm run prototype`。ブラウザで `http://127.0.0.1:5180/preview.html`。下端のレビュー帯で初回起動・同一入力2ジョブ・キュー整理後・保存先混在・用語集120語などを確認する。

**合成・開発専用**: 実音声処理、ファイル/モデル入出力、GPU検出、DB、実API・native pickerを使わない。`/next/`や既定旧GUIには含まれない。

最終HEAD `1f72f28` の検証はVitest **221**、プロトタイプE2E **96**、UI-2 E2E **10**成功。PR CI **14/14**とmain統合後CIも成功。UI-3 #21の本番移行とUI-4 #22のWindows実機受入は継続する。
