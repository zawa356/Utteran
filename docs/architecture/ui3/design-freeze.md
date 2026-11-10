# UI-3-0 設計確定・受入記録・S1〜S5への引継ぎ

- **状態:** 2026-11-06、UI-3-0の第5イテレーションを**Design Freeze（設計基準承認）**。ownerはWindowsのブラウザで概観し、細かなUI改善は後続に繰り越すと決定した。
- **対象:** [Issue #21](https://github.com/zawa356/Utteran/issues/21)、[Epic #18](https://github.com/zawa356/Utteran/issues/18)。UI-3全体の完了・本番実装の承認を意味しない。
- **正本:** 第5案＋P1修正の[PR #38](https://github.com/zawa356/Utteran/pull/38)。最終コード `1f72f28f6fac51cb76e0b5e8aaeda0f2911b2abd`、mainマージ `701a94e1d7630bb58e6810b69064926fc02f01e1`。開発版 `0.1.43`（未リリース、tagなし）。
- **優先順位:** 本書はUI-3-0でownerが採用した設計判断の正本。実コードとB4〜B9契約は実挙動の正本。[iteration-5.md](iteration-5.md)は最終実装の詳細、[iteration-2](iteration-2.md)〜[iteration-4](iteration-4.md)と[screen-design](screen-design.md)はその時点の履歴。

## 1. 確定したUI方針

| 領域 | 採用する設計 |
|---|---|
| 技術基盤 | Vue 3 + TypeScript + Vite + Vuetify 4。標準部品を優先し独自実装を増やさない。[ADR-003](../../adr/ADR-003-ui-frontend-stack-and-host-boundary.md)に従い、WebView2/pywebviewを基本とする。Electron移行は可能性だけ残す |
| 通常モード | 文字起こし・セットアップ・設定の短い導線。表示中の1ファイルを中心に入力→設定→実行→進捗→結果。モデル選択は詳細から可能。専門用語や重複注記を減らす |
| Expertモード | 管理sidebar＋①ファイル ②処理設定 ③キュー投入の3ペイン。幅調整と画面幅による切替。キュー全件管理・結果と履歴を分離する |
| モード間の整合 | 同じ入力一覧・共通設定・ファイル別override・ジョブを異なる表示で扱う。モード切替で破棄/暗黙投入をしない |
| セットアップ | 標準＝推奨モデルを表示して変更可能、カスタム＝エンジン/デバイス/複数モデル/主モデル選択。話者分離のトークン有無と利用条件の同意は独立して確認 |
| 承認・復旧 | 新しく承認したプランは前回の同名工程を流用せず先頭から開始。同一承認プランのretry/resumeのみ完了工程を保持。未承認・失敗・中断では環境や設定を確定しない |
| 初回モデル | 初回セットアップの成功時に主モデル/エンジン/経路を処理設定の共通既定値に反映し、新規ジョブのsnapshotと一致させる。追加/修復は既存設定を変更しない |
| Expertの再投入 | 同じ入力を設定変更後に別Job IDで投入できる。`retry`＝既存snapshotの同一Job、`rerun`＝現在設定から別Job。既存ジョブのsnapshotは不変 |
| キュー・履歴 | 「終了したジョブを片付ける」は**キュー表示だけから外す**。元のジョブ・snapshot・出力名・履歴は残る。履歴削除と成果物削除は別操作 |
| 出力先 | 既定の出力先＋入力ファイル別の個別指定、複数選択時の一括指定。投入したジョブは解決済みの出力先を保持し、後の変更に追従しない |
| 用語集 | 1行1語の複数行エディタが標準。大量貼付、空行/重複/制限の検査、TXT取込/書出導線。入力IMEの挙動を妨げない |
| 表示と文言 | 日本語・英語、青/緑テーマ・ライト/ダーク、読みやすい文字/ボタンサイズ、レスポンシブな入力・形式チップ。繰り返し説明を抑える |
| 修復/初期化 | 実行環境の修復・再セットアップ・アプリの初期状態復帰を区別し、破壊的操作の内容を確認させる |

## 2. 反復・レビュー証拠

| 段階 | 内容 | 詳細 |
|---|---|---|
| 初期案 | 通常A1/A2、Expert C1/C2の比較、配置の第一候補 | [screen-design](screen-design.md)、[review-and-acceptance](review-and-acceptance.md) |
| 第2案、メモ#1〜#5 | 通常1ファイル・Expert可変3ペイン・sidebar・配色 | [iteration-2](iteration-2.md) |
| 第3案、メモ#6〜#14 | 可読性、①→②→③、キュー要約、メニュー/ログ/進捗 | [iteration-3](iteration-3.md) |
| 第4案、メモ#15〜#24 | 処理設定5カテゴリ、セットアップ、話者分離同意、狭幅、プリセット | [iteration-4](iteration-4.md) |
| 第5案、メモ#25〜#36 | 標準/カスタム、履歴分離、複数ジョブ、保存先、用語集、文言/修復 | [iteration-5](iteration-5.md) |
| 第5案レビューP1の2件 | プラン再承認で工程再利用しない／初回モデルとジョブsnapshotを一致させる | [iteration-5 §18](iteration-5.md#18-追補-レビューp1修正2026-11-06) |

**検証済み（最終HEAD `1f72f28`）:** Vitest **221 passed**（プロトタイプ144・本番77）、UI-3プロトタイプE2E **96 passed**、UI-2 E2E **10 passed**。PR CI [42170336857](https://github.com/zawa356/Utteran/actions/runs/42170336857)は14/14成功、main統合後CI [42171089712](https://github.com/zawa356/Utteran/actions/runs/42171089712)も成功。第5案の本番 `/next/` bundleはレビュー基点とバイト同一。旧GUI、opt-in `--next-ui`、CLI、Python、DB、API、IPC、推論を本試作の変更で置き換えていない。

**受入範囲:** ownerのWindowsブラウザでの概観と合成画面の設計判断。正式なWindows WebView2、125/150/200% DPI、IME、native D&D/picker、実モデル・ASR、B4〜B9実API、永続DB・保存/消去、installer/portableは**未受入**。「セットアップ完了」「保存」「履歴削除」等の試作は実行結果ではない。

## 3. 製品実装の責務分担

| 領域 | 正本と接続条件 |
|---|---|
| B4 実行環境・候補 | inventory/ready/互換性・推奨をバックエンドで判定。`setupCatalog.ts`等の固定の合成表は製品に持ち込まない |
| B5 設定 | Preset/Policy/`FileInput.output_dir`とCAS。入力別保存先は`ProcessingSettings`のleafではない。個別overrideと投入時snapshotを分離 |
| B6/B7 ジョブ | 不変snapshot、admission、lease/fencing、queueと履歴を分離。`retry`/`rerun`/同じ入力の同時投入を区別。出力先の実パス検査と既存成果物への上書き防止 |
| B8 セットアップ | plan/approval/journal/同意/復旧が正本。フロントの`runPlanKey`は試作の再利用防止モデルであり、本番はプランdigest・永続工程証拠で安全判定 |
| B9 同期・診断 | event epoch/cursor・queue revision・遅延/失敗回復はバックエンドに従う。Issue #37と旧/v1接続の競合をS5前に解決 |
| OS/ホスト | ネイティブpicker・D&D・IME・DPI・修復ランチャー。WebView2/installerに入れた後に実機受入 |
| 共通 | 既存CLI/coreと既定旧GUIの動作、rollback、opt-in v1境界を維持。開発専用 `frontend/src/preview/` を本番へ直接importしない |

## 4. 次段階（UI-3 #21はオープン）

1. **S1 設定とOS導線:** 既定入出力先/native picker、token状態・ログ・保存失敗表示を本番へ。新UI⇔旧UIの機能表とrollbackを維持。
2. **S2 環境・モデル:** B4正本の候補/ready理由・profile切替。成立しない構成の静的な表示はしない。
   **追補（2026-11-09 owner判断・A案）:** S2は旧`GET /api/environment`の観測表示に限定して実装した（[s2-environment-visibility](s2-environment-visibility.md)）。B4の候補/ready理由（G-01: 公開read-only API）は未解決の後続課題として残す。設計思想（frontendに互換表を持たない、readyを捏造しない）は不変。
3. **S3 文字起こし・キュー:** 通常/Expertから実APIへ段階接続。重複admission・idempotency・ファイル衝突・immutable snapshot・IME・D&Dを検証。旧APIとv1 APIを無条件に混在させない。
   **追補（2026-11-09）:** S3をS3-A（1ファイルの基本フロー、旧`/api/jobs`、[s3a-legacy-transcription](s3a-legacy-transcription.md)、`0.1.46`）とS3-B（複数入力・ファイル別override・Expertキュー・同一入力の再投入・出力命名）に分割。S3-Aの凍結コピーはメモリ内だけでB7の不変snapshotではない。idempotency・同一入力のcore履歴共有・出力衝突はS3-B/S5の設計ゲートとして[s3a §9](s3a-legacy-transcription.md#9-s3-bへの引継ぎ明示的な未決ゲート)に記録。
   **追補（2026-11-10 owner承認の分割）:** S3-B1＝複数入力・ファイル別override・段階的な一括投入（実装済み、[s3b1](s3b1-multi-input-and-admission.md)、`0.1.47`）、S3-B2＝Expertの実キュー管理、S5＝B7永続化・同一入力の独立再実行・日時＋実行ID出力名。B1の「一括」は旧APIへの順次POSTでありatomic batchではない。同一入力の再投入はS5まで提供しない（ページ内ガードは補助判定）。
4. **S4 結果・履歴・モデル・セットアップ:** viewer/再出力/管理を実接続し、履歴保持と成果物削除、実用語集TXT取込を安全に設計する。
5. **S5 v1接続:** B9 Issue #37、G-09排他とDB migration/復旧を先に解決。owner承認なしにactivate・既定切替しない。
6. **UI-4 #22:** Windows WebView2/IME/OS DPI 200%初期窓、installer/portable、起動/RAMと実機回帰の受入。

各段階は小PRで実装し、旧GUI/CLIの非退行と実APIの自動テストを必須とする。

**受入の運用（2026-11-09 owner方針で追補）:** 各段階のPRはLinux自動試験とWindows CI自動試験（frontend lint/型/Vitest/build、Windows pytest/PowerShell）の全必須ジョブ成功でmerge可否を判断する。WebView2実起動・native picker・Explorer・DPI・IME・installer/portableの手動実機確認はUI-3/UI-4の最終統合ゲートへ集約し、各PRでは求めない。自動CIの成功を実機受入とは扱わない（[s1 §8](s1-settings-and-host.md#8-windows-webview2-実機受入未実施最終ゲートへ集約)）。

## 5. 未決事項と変更管理

- G-05/G-06: 作業途中の入力/override/セットアップ状態をどこに保存するか。G-15: 保持期間・ACL・消去・backup/restore。
- 同一ファイルの同時admission・出力名衝突・履歴recordと出力ファイルの削除範囲/権限。
- 標準セットアップの推奨規則、デバイスの実互換性、同意のversion・tokenの安全な保存。
- GUIの最小サイズ・狭幅/高DPI、IME/長文/スクリーンリーダー、製品配色、既定GUI切替の時期。
- 軽微な文言/余白は後続PRで調整可能。状態モデル・履歴保全・同意/消去・API契約を変更する場合は理由と受入基準をIssueへ記録する。

## 6. 再現手順

`frontend/` で `npm ci`、`npm run prototype`。ブラウザで `http://127.0.0.1:5180/preview.html` を開く。下端のレビュー操作帯から初回起動、モデル変更、同一ファイル2ジョブ、キュー片付け後、出力先混在、用語集120語を再現できる。データはすべてin-memoryで、ページを再読込すると戻る。

画像 `i5-01`〜`i5-16` は `frontend/test-results/ui3-screenshots/` に再生成する未コミットのローカル成果物。詳細は[iteration-5 §17](iteration-5.md#17-動作確認の手順windowsでの目視レビュー用)および[review-and-acceptance](review-and-acceptance.md)を参照。
