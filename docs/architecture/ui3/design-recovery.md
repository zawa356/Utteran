# UI-3 #96 — Design Freeze第5案とのUI整合性回復（先行実装）

- **状態:** 先行実装（Intel実機受入 #95/#22 と並行）。stacked Draft PR、owner review待ち。[#96](https://github.com/zawa356/Utteran/issues/96)は完了扱いにしない。
- **基点:** #95 `codex/windows-integration-acceptance` の公開HEAD `4d07f075b4983c1cbf114a40dd6c73368cd4e287`（作業開始時にremoteで一致を確認、同名branchなし）。
  作業branch `claude/ui-design-recovery`。Draft PRのbaseは `codex/windows-integration-acceptance`。
- **設計基準:** [design-freeze.md](design-freeze.md)、第5案 [#38](https://github.com/zawa356/Utteran/pull/38) 最終HEAD `1f72f28f6fac51cb76e0b5e8aaeda0f2911b2abd`（`frontend/src/preview/`は基点HEADでも同一内容）、
  [iteration-5.md](iteration-5.md)、後続owner決定（内蔵Viewerなし、retry＝新しい内部Job、成果物物理削除の無効、§7）。
- **版:** 0.1.72 据え置き（`pyproject.toml`/`uv.lock`/`変更履歴.md`/共有AISTATEは最終同期時に更新）。
- **対象:** `--next-ui-v1`（`v1-dev`）の表示層だけ。旧`--next-ui`（legacy形）、既定GUI、CLI、Python、API schema/client、B4〜B9、domain/store、
  Setup（`SetupWizard.vue`/`SetupCard.vue`/`api/setup.ts`/`domain/v1/setup.ts`/Setupのi18nキー）は変更していない。

## 1. 差分表（承認設計・変更前・変更後）

区分: **回復**＝今回Freezeに合わせた／**維持**＝製品の機能・安全のため意図的に残した差／**未対応**＝既存契約で表現できず明示表示にした／**後続**＝Intel受入後。

| # | 領域 | 承認設計（第5案） | 変更前（#95 `4d07f07`） | 変更後 | 区分・理由 |
|---|---|---|---|---|---|
| N1 | Normal 全体 | 左＝ファイル＋設定、右＝実行・進捗・結果の2列（880px未満で1列）。見出しは画面上sr-only | 作業セット説明・入力・「共通の処理設定」（Preset/Policy作成・差分・出力先）・実行環境・ジョブ実行（v1キュー・サーバー上のジョブ）が全展開の縦長 | 左「ファイル」「設定」、右「文字起こし」の2カード（カード幅のcontainer query、880px以上で2列） | 回復 |
| N2 | ファイル | 1ファイル（名前を大きく＋形式）、別のファイル、保存先 | 入力パス（monospace）のみ、出力先は設定カードの下 | ファイル名＋フルパスのfile box、同じカードに「出力先フォルダ」（入力欄＋選択ボタン） | 回復。ネイティブpicker・手入力・D&Dは既存のまま |
| N3 | 設定（基本） | 言語・話者分離・出力形式（チップ、最小幅4.75rem/gap 8px）、2列⇔1列（520px） | 言語select・形式checkbox（Presetとの差分欄） | 言語・話者分離・出力形式チップ。値は従来どおりB5 `common_overrides`（`/asr/language`, `/output/formats`） | 回復 |
| N4 | 話者分離 | ON/OFFスイッチ | 表示なし（バナーで「話者分離なしで実行」） | 「この実行経路では未対応（話者分離なしで実行）」と表示。スイッチは置かない | 未対応（v1 worker/B3は話者分離なし。ダミーのスイッチを作らない） |
| N5 | 詳細 | 「詳細（モデル・話者数）」折りたたみ | Preset/Policy・実行環境が常時展開 | 「詳細（モデル・Preset・実行環境）」にモデル（Presetの値）・Preset/Policyの選択と作成・実行環境を収容。**開始に必要な時（Preset/Policy未選択、版の欠落/新版、一覧失敗）は自動で開き「確認が必要」**を表示。自動では閉じない | 回復＋維持（機能は削除しない。暗黙の作成・採用・保存なし） |
| N6 | モデル | 詳細でモデル選択 | Preset作成時のみ指定 | Presetのモデルを読み取り表示し、変更方法（Preset作成／Expertのファイル別）を案内 | 未対応（共通overrideに`/asr/model`がない。新しい操作を作らない） |
| N7 | 実行 | 大きな「文字起こしを開始」1つ、理由表示、進捗、結果 | ①確認→②投入（小ボタン）、v1キュー・サーバー上のジョブが常時展開 | ①確認→②投入を大きなボタンで横並び。v1キュー・サーバー上のジョブは「キューとサーバー上の記録」に格納し、**追跡中ジョブがあるのにキュー停止中・キュー操作の失敗/不明・送信結果不明・追跡喪失の時は自動で開く** | 維持（validate/resolve/submit・期限・CAS・冪等性・unknown時の停止を保つため2段のまま）＋回復（密度） |
| E1 | Expert ペイン | sidebar＋可変3/2/1ペイン、各ペインは1枚の面に「①ファイル 選択中: n件」等の見出し | 見出しが面の外、中に複数のカード | 各ペインを1枚の面にし、番号丸＋見出し＋件数（選択中/対象）を面の上端に。3/2/1切替・resize・F6は不変 | 回復 |
| E2 | ② 対象切替 | 「共通の既定値／選択した{n}件」 | なし（共通の処理設定と選択中の個別設定を縦に並置） | 同じ切替を追加。①で選択が0→1件以上になると「選択」へ、0件になると「共通」へ（値は変えない） | 回復 |
| E3 | ② カテゴリ | 音声認識／プロンプト・用語集／話者分離／出力／詳細（折り返すボタン列、下線で現在地） | なし | 5カテゴリを同じ見た目で追加（下表§2）。切替は表示だけで、未適用の個別変更は共有storeに残る | 回復。Issue本文の「基本/ASR/…」はFreezeの正式名に合わせた |
| E4 | ③ キュー投入 | 「選択した{n}件をキューへ投入」、簡易キュー、キューを開く | 一括投入・簡易キュー・単件ツールが別カード | 1枚の面に一括投入→簡易キュー→単件ツール（折りたたみ） | 回復（面）＋維持（2段投入、B7 retry/rerun・照合・回復は不変） |
| E5 | 作業セット | （束Eで追加。Freezeに対応物なし） | 説明文付きのカード | 1行の帯（選択・状態・操作）、説明はiボタンのtooltipへ。Normal/Expert共通 | 自然な配置（機能・自動保存・競合表示は不変） |
| S1 | アプリバー | アイコン＋Utteran、メニュー（ファイル/表示/ツール/ヘルプ）、Normalは3行先、モード切替（primary色）、高さ52px | アイコン・メニューなし | v1形のみ: 既存アイコン（`icon/utteran.svg`のバイト同一コピー、Vitestで照合）、メニュー（ファイルを追加…/通常・Expert/ペイン幅を戻す/セットアップ/設定）、primary色のモード切替、高さ52px。1000px未満は1つのメニューボタン | 回復。ヘルプ（About/リポジトリ）・ログ/診断ダイアログ・修復と初期化は製品実装がないため出さない（後続） |
| S2 | テーマ | 青/緑×ライト/ダーク | 実装済み（D4） | 変更なし（新しい色・tokenは追加していない） | 維持 |
| S3 | 余白・密度 | カードpadding 16px・カード間16px・見出し1rem/600 | `pa-3`（12px）、見出し大小混在 | v1形の`.u-fcard`（16px）、カード間16px、カード見出し1rem/600、折りたたみ枠（`.u-fold`）。値はpreview.cssから採用 | 回復 |
| W1 | Setup | ようこそ→実行環境とモデル→使用する機能→確認→実行→完了の段階式（4手順＋実行・完了、戻る/次へ） | 1ページに 1.環境の確認／2.内容／3.計画の確認／4.実行状況／5.実行の可否 を縦積み | **変更なし**（§4に対応表と移植手順） | 後続（Intel受入後） |

意図的に残す差（Design Freezeから変える箇所）: N4/N6（契約にない操作を作らない）、N7/E4の2段投入（S5-Aの安全条件）、開発版バナー（`--next-ui-v1`の開発専用表示。試作のレビュー帯・「試作版」帯とは別物）。

## 2. Expert ② カテゴリの割当（既存部品の再配置のみ）

| カテゴリ | 共通の既定値 | 選択した{n}件 |
|---|---|---|
| 音声認識 | 言語（共通差分）、モデル（Presetの値）、エンジン/デバイスの決まり方 | エンジン・モデル・デバイス・言語の個別設定（`InputOverrides`、適用/破棄/共通に戻す） |
| プロンプト・用語集 | 初期プロンプトは未対応と表示、共通用語集＝Presetの語数と「Presetを管理」（新しい版として保存、E-A） | 初期プロンプト未対応、ファイル別用語集（`RowGlossary`、E-A） |
| 話者分離 | 未対応（話者分離なしで実行）と表示 | 同左 |
| 出力 | 出力形式チップ（共通差分）、出力先フォルダ | 出力形式・出力先の個別設定（`InputOverrides`） |
| 詳細 | Preset/Policyの選択・作成、実行環境（detail） | ファイル別の詳細項目はない旨、実行デバイスの決まり方 |

`InputOverrides`はカテゴリごとに表示する欄だけを絞る（`fields`）。staged変更は既存の入力controllerにあるため、カテゴリや対象を切り替えても失われず、
どのカテゴリの「適用」も全staged項目を同じ規則（ロック済み行は除外、混在表示、空の形式は拒否）で反映する。

## 3. 変更ファイル

| 種別 | ファイル |
|---|---|
| 新規 | `features/expert/ExpertInspector.vue`（②の対象切替＋5カテゴリ）、`features/workspace/NormalSettingsCard.vue`、`features/transcribe/V1BasicSettings.vue`、`V1LibrarySettings.vue`、`V1OutputFolder.vue`、`features/shell/AppMenu.vue`、`assets/utteran.svg`（既存アイコンのコピー）＋`features/shell/appIcon.test.ts` |
| 削除 | `features/transcribe/V1Settings.vue`（内容を`V1LibrarySettings`/`V1BasicSettings`/`V1OutputFolder`へ分割。testid・処理は同一） |
| 変更 | `NormalWorkspace.vue`（v1形のみ再構成、legacy形は同一）、`ExpertWorkspace.vue`（v1形のペイン面）、`AppShell.vue`（v1形のアイコン・メニュー・52px）、`V1Job.vue`（`bare`、記録の折りたたみ）、`WorksetBar.vue`（`compact`）、`TranscribeInput.vue`/`InputList.vue`（`bare`）、`InputOverrides.vue`（`fields`/`bare`）、`styles/v1.css`（v1スコープのFreeze値）、`i18n/ja.ts`/`en.ts`（`ui3.*`のまとまった追加のみ。既存キーは不変） |
| 試験 | `e2e/fixtures.ts`（折りたたみを開く補助）、既存E2Eの操作手順（§5）、比較撮影 `e2e/ui3-recovery-capture.v1.spec.ts`・`e2e-preview/recovery-capture.spec.ts` |

## 4. Setup：第5案の段階式と現実APIの対応（後続の移植準備）

| 第5案の手順 | 試作の内容 | 製品の現在（`SetupWizard.vue`、束F） | 実API・状態 | 移植時の扱い |
|---|---|---|---|---|
| 1 ようこそ | 標準／カスタムの選択 | 「2. セットアップの内容」のトグル | — | 段階1に方式の選択を移す。方式切替でplanを変えない点は製品も同じ |
| 2 実行環境とモデル | GPU/ディスクの事実、デバイス、推奨モデル（標準）／エンジン・複数モデル・主モデル（カスタム） | 「1. 環境の確認」（診断の読取・再検出）＋「2. 内容」 | `GET/POST /api/v1/setup-diagnostics(/refresh)`、推奨はB8/B4 | 試作の`setupCatalog.ts`（合成表）は持ち込まない。候補・推奨・不可理由はserver応答だけ |
| 3 使用する機能 | 話者分離ON時のみ同画面でトークン状態・利用条件の同意（独立判定） | 「2. 内容」の話者分離スイッチと注記 | token状態・同意はB8 planの`consents`/credential preflight | 同意とトークンを独立に表示・判定（P1-1）。値は表示しない |
| 4 確認 | 差分の表（DL/容量/不明分）＋「インストールを開始」 | 「3. 計画の確認」（手順列挙・容量・同意） | `POST /api/v1/setup-plans`→`.../approve`→`POST /setup-operations` | 表形式へ。plan digest・approvalは製品の正本のまま |
| 5 実行 | 工程・失敗時の3手段 | 「4. 実行状況」（工程・再試行/再開・キュー再開） | `GET /setup-operations/{id}`、retry/resume、journal | 同一承認planのretry/resumeのみ工程保持（製品の既存判定を使う） |
| 6 完了 | 実行環境/モデル/話者分離の要約 | 既定値作成の結果＋「5. 実行の可否」 | `POST .../defaults`（create-if-empty）、`POST /readiness-checks` | Setup成功とready（B4判定）を分けて表示する現行方針を維持 |

移植手順（Intel受入後、同じbranch）: ① #95最新HEADへ同期し`SetupWizard.vue`の差分を確認 → ② 既存の`domain/v1/setup.ts` controllerの状態から「現在の段階」を導く薄いview adapterを作る（storeは変えない）
→ ③ 各段階をFreezeの見た目（ヘッダ「手順 n/4」、戻る/次へ、あとで）で表示し、`fsetup-*`のtestid・F-01〜F-06 E2Eを維持 → ④ 実WebView2で受入。
未接続の表示部品は今回作っていない（製品bundleに未使用部品を入れない）。

## 5. 試験・比較

### 5.1 比較画像の再現手順（同一条件: 1280×820、ja、ライト、緑、合成データ。英語ダーク・狭幅は別名）

```bash
cd frontend && npm ci && npm run build
UI3_RECOVERY_SHOTS=after npx playwright test e2e/ui3-recovery-capture.v1.spec.ts --project v1 --trace off
UI3_RECOVERY_SHOTS=after npx playwright test -c playwright.preview.config.ts e2e-preview/recovery-capture.spec.ts --trace off
```

画像は `frontend/ui3-recovery/<before|after>/`（git-ignored）。`freeze-*`＝第5案試作（レビュー帯は非表示、試作バナーは表示）、`product-*`＝製品v1。
変更前は基点`4d07f07`で同じspecを実行した。環境変数なしの通常実行ではこの2つのspecはskipされる。PRには代表画像だけを添付する。

### 5.2 独立レビュー（コード・設計差分）と対応

| 指摘 | 対応 |
|---|---|
| P1: 既存E2Eが折りたたみ・カテゴリで要素に届かない | `e2e/fixtures.ts`に表示だけを行う補助（`revealLibrary`/`fillOutput`/`inspectorCategory`/`openRunRecords`、`openSingleJobTools`は記録も開く）を追加し、操作手順だけを更新。安全・機能のassertionは変更なし |
| P1: 追跡中ジョブがない時にキュー停止が見えない | 「キューとサーバー上の記録」は、待機項目がある停止中キュー・切替不可の表示中も自動で開く |
| P2: 「詳細」が起動直後に常に開く | 一覧と復元選択の応答後にだけ判定（読込前の未選択では開かない） |
| P2: カテゴリ表示中の「共通に戻す」が他カテゴリも戻す／他カテゴリの未適用が見えない | 表示中の欄だけを戻す。他カテゴリに未適用の変更がある時は「適用」がまとめて反映する旨を表示 |
| P2: Preset未選択の案内の重複 | 言語/形式がある時だけ表示 |
| nit: メニューの選択状態の読み上げ、件数を含む見出し名、sticky位置、重複CSS | `menuitemradio`/`aria-checked`、件数は`aria-hidden`、44px、重複削除 |
| 残置: ②の「選択した{n}件」は選択数（送信済み行を含む）。個別設定の編集対象数は既存の「変更対象」「ロック中」表示で示す | Freezeの表記を優先 |

## 6. 受入結果・未実施

実施環境: Linux cloud、headless Chromium、合成store/runner（2026-11-27）。

| ゲート | 結果 |
|---|---|
| `npm run lint` / `npm run typecheck` | 合格 |
| Vitest | 60 files / 683 passed |
| `npm run build` | 合格 |
| 製品E2E legacy project | 73 passed（最終build） |
| 製品E2E v1 project | 105 passed・1 failed・4 skipped（skip＝比較撮影spec）。failedの`worksets-eb`（ExpertでPreset表示を開く手順の不足）を修正後、同spec 9/9 passed |
| 試作E2E（`playwright.preview.config.ts`） | 96 passed・4 skipped（比較撮影spec） |
| `tests/test_ui2_next_ui.py` | 20 passed |
| `tools/public_history_scan.py --worktree --fail-on-findings` | blocking 0 |
| `tools/check_powershell_bom.py` | 合格（.ps1変更なし） |
| 比較撮影 | before/after 各 product 12・freeze 20枚（未コミット、§5.1） |

途中の全体実行で`setup-f` F-03が1回失敗（Intel variant表示の待機）。基点`4d07f07`と本branchの単独実行ではどちらも合格し、v1全体の再実行でも合格したため、並列負荷時の一過性と判断（Setupは未変更）。
Python/core/schemaは変更していないため、uv lock・ruff・mypy・pytest全体・schema `--check`はCIに委ねる（stacked DraftではCIが起動しない場合があり、その場合は未実行）。
**実WebView2・Windows DPI/IME・native picker/D&D・実モデル：NOT_RUN。**

## 7. 保留事項（Intel受入後・契約変更が必要なもの）

| 項目 | 必要なもの |
|---|---|
| Normalのモデル選択 | 共通overrideに`/asr/model`（B5 registered leaf）を許すか、Preset切替を「モデル選択」として見せる設計判断 |
| 話者分離・初期プロンプト | v1 worker/B3の対応とB5 leafの登録。現状は未対応表示 |
| 単一の「開始」ボタン | resolve tokenの期限・CAS・unknown時停止を保ったまま確認と投入を1操作にするには、server側で「確認して投入」を原子的に扱う契約が要る |
| メニューのヘルプ・ログ/診断ダイアログ・修復と初期化 | 製品側の実装（About情報源、ログ/診断の安全なexport、修復ランチャー）。現在は設定画面のログ欄だけ |
| Setupの段階式 | §4の手順（#95同期後） |
| 最終統合 | #95最新同期、意味的差分の確認、最終CI、実WebView2受入、#94の撮影 |
