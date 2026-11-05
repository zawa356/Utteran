# UI-3-0 第2イテレーション — 通常の圧縮・Expertの高密度化とリサイズ・緑配色・環境mock

更新: 2026-11-05 JST（0.1.43のまま、[§9](#9-versionの判断)）。Draft PR [#38](https://github.com/zawa356/Utteran/pull/38)（`claude/blissful-planck-84e59y`）への追加コミット。
前提: 第1案の[screen-design](screen-design.md)（A1/C1）と[review-and-acceptance](review-and-acceptance.md)。

> **これは「触って判断する」ための第2案で、画面の採択ではない。** 合成・架空のデータだけで動き、S1〜S5の本番移行は行っていない。
> 変更は開発専用の`frontend/src/preview/`、`frontend/e2e-preview/`と文書だけ。本番`/next/`・既定の旧GUI・`src/styles/theme.ts`・
> backend/API/CSP・DB・推論・CLI・配布経路は**変更していない**（`vite build`の出力はUI-2と同一サイズ、§6）。

## 1. ownerメモの反映先

| メモ | 要件（要旨） | 反映（第2案） | 主なファイル |
|---|---|---|---|
| #1 通常 | 5段縦積みは長い。1画面に主要操作と状態 | 左＝ファイル＋設定、右＝開始→工程→結果の**2カラム1画面**（<880pxは1列）。詳細は折りたたみ | `normal/NormalHome.vue` ほか |
| #1 通常 | 入力は原則1ファイル。正常・実行中・失敗・未選択を切替可 | 通常は**1件だけ表示**。レビュー帯で「1ファイル・未開始／未選択／実行中／失敗／完了」を切替 | `domain/demo.ts`（`normalInputId`・`SCENARIOS`） |
| #1 通常 | 独立topbar、3導線、左サイドバーなし | app bar内に「文字起こし／セットアップ／設定」（`aria-current`＋下線）。サイドバーは描画しない | `shell/NormalTopNav.vue` |
| #1 全体 | モード切替＝アプリ全体の情報設計切替 | 通常＝topbar 3導線、Expert＝管理sidebar 6導線。通常に無いページ（キュー等）から戻ると文字起こしへ | `domain/layout.ts`・`PreviewShell.vue` |
| #1 状態 | 切替で削除・暗黙投入・上書き禁止 | 状態はview外のstore。Expertの複数件は**そのまま**、通常は代表1件（最後に触れた入力）＋「ほかにn件。Expertで管理」案内のみ | `normal/NormalFileCard.vue` |
| #2 Expert | 3ペイン境界をdragで幅調整、比率を固定しない | 2本の区切り（`role=separator`）をdrag／←→（Shift×4）／Home・End／Enter・ダブルクリックでリセット。比率の例「標準 30/42/28」「入力狭め・設定広め 22/53/25」をレビュー帯で比較 | `expert/ExpertLayout.vue`・`components/ResizeHandle.vue` |
| #2 Expert | サイドバー幅も可変。折りたたみを上部へ、rail時も再展開ボタン | 右端をdrag／キーで168–360px（窓幅の40%上限）。**折りたたみ/展開ボタンはサイドバー最上部**に常時表示（rail時もそこに残る）。狭い窓（<1024px）はrailで始まるが展開可能 | `shell/ExpertSidebar.vue` |
| #2 Expert | 入力追加を大きく明確に。投入ボタンと取り違えない | 左ペイン最上部に**高さ40pxの「ファイルを追加…」「フォルダを追加…」**（tonal＋アイコン）。右は「選択をキューへ投入」（filled＋送信アイコン）と文言・形を分けた | `expert/InputsPane.vue`・`expert/QueuePane.vue` |
| #2 Expert | Explorer詳細表示風の高密度一覧 | 1行28pxの表（☑・名前・形式・長さ・状態・個別）。長い名前はellipsis＋tooltip＋Inspector見出しに全文。**ペイン幅に応じて形式→長さの列を隠す**（container query）。クリック＝単一選択、Ctrl+クリック/チェック＝追加 | `components/InputTable.vue` |
| #2 Expert | 横はみ出しなし、3→2→1列、キーボード | 列数を**viewport幅ではなく作業領域の実幅**で決定（≥936px:3列、≥688px:2列＋右ペイン下段、未満:1列＋ペイン切替）。ペイン最小幅で必ず収まる（§3） | `domain/layout.ts`（`resolvePanes`） |
| #3 重複 | 通常は1件の状態と結果だけ | 通常にキュー・履歴の画面・一覧は無い。結果は出力例と「過去の結果はExpertへ」リンクのみ | `normal/NormalRunCard.vue` |
| #3 重複 | 作業画面右は要約と操作、キュー＝全件管理、結果と履歴＝完了物 | 作業画面右: 投入ボタン・件数（待機/実行中/要対応/完了）・「いま実行中・要対応」最大6行・一時停止・各画面へのリンク。**キュー画面**: 検索・絞り込み・並べ替え・順序変更・詳細。**結果と履歴**: 完了のみ・検索・出力と投入時設定。viewer・再出力は無効＋「S4で設計」 | `expert/QueuePane.vue`・`sections/QueueSection.vue`・`sections/ResultsSection.vue` |
| 色 | 深緑プライマリ、背景ニュートラル、意味色は区別、WCAG 2.2 AA、本番theme不変 | プロトタイプ限定テーマ`greenLight/greenDark`。レビュー帯で「緑（第2案）／青（UI-2）」を切替比較。状態はアイコン＋文言（色は第3の手掛かり）。計測は§4 | `preview/theme.ts`・`theme.test.ts` |
| #5 環境 | 実在しそうな属性の合成情報、常時「架空」明示、通常は概要、Expertは階層 | GPU例（faster-whisper / large-v3-turbo / CUDA / float16 / pyannote）、CPUのみ例、未構成例、読み込み失敗例。通常＝推奨プロファイル1件＋詳細展開、Expert＝全プロファイル＋表示密度・すべて展開。再検出は表示状態だけ（何も検出しない） | `domain/envExamples.ts`・`environment/*`・`sections/SetupSection.vue` |

## 2. 情報設計

```text
通常（topbar・1ファイル）                                       Expert（管理sidebar・3ペイン）
┌ Utteran [試作] 文字起こし|セットアップ|設定 ……… [通常|Expert] ┐  ┌ Utteran [試作] ……………………………………… [通常|Expert] ┐
│ ⚠ プロトタイプ／合成データ／処理しません …                    │  │管理メニュー[⇤]│ ⚠ プロトタイプ…                             │
│ 文字起こし  実行環境: GPU… [利用可能(例)][デモ用の架空情報] 詳細│  │🎤 文字起こし ┃│入力(5)  ‖ 処理設定        ‖ 投入と実行状況│
│ ┌ファイル ─────────────┐ ┌実行と結果 ────────────┐           │  │≡ キュー    3┃│[+ファイルを追加…]‖[共通|選択(2)]‖[選択をキューへ投入]│
│ │ sample-meeting-01.wav ×│ │[▶ 文字起こしを開始]   │           │  │⟲ 結果と履歴 ┃│[+フォルダを追加…]‖ モデル/言語…  ‖ 待機1 実行中2 要対応1│
│ ├設定 ─────────────────┤ │ ✓ASR ▶話者分離 ○出力  │           │  │▣ モデル     ┃│☑ 名前 長さ 状態 個別‖ 工程          ‖ いま実行中・要対応 │
│ │ 言語 話者分離 出力形式 │ │ 結果: .srt .json      │           │  │🔧 セットアップ┃│☑ sample… 12:34 ▶実行中‖ 実行環境(架空)‖ → キューで全件管理│
│ │ ▸詳細（モデル・話者数）│ └───────────────────────┘           │  │⚙ 設定       ┃│☐ interv… 21:52 準備中 2‖              ‖ → 結果と履歴     │
│ └──────────────────────┘                                     │  │接続: なし    ┃  ‖＝ドラッグ／←→で幅変更、┃＝サイドバー幅           │
└ ┄┄ レビュー用操作（製品UIではありません）▾ 表示状態・環境例・接続例・配色・テーマ・言語・比率例・初期化 ┄┄┘
```

- レビュー用操作は**画面下端の破線・斜線の帯**に移した（折りたたみ可）。製品の操作と同じ領域に置かない。
- 試作表示: app barのchip「プロトタイプ（UI-3-0 第2案）」、本文上端の1行バナー、環境情報の横の「デモ用の架空情報」chip、
  デモ操作の破線区切りと「デモ操作」chip。
- Expertの接続表示はサイドバー下端（rail時はアイコン＋tooltip）。既定は「なし（試作）」。

## 3. 幅・リサイズの仕様と縮退

| 項目 | 値・動作 |
|---|---|
| サイドバー | 既定200px、168–360px（かつ窓幅の40%以下）。rail 56px。<1024pxの窓はrailで開始（ボタンで展開可） |
| ペイン最小幅 | 入力220px・処理設定340px・投入と実行状況240px。左右ペインは作業領域の45%以下 |
| 列数（作業領域の実幅） | ≥936px: 3列（各ペイン独立scroll）／688–935px: 2列（入力｜処理設定、投入と実行状況は下段全幅、領域ごとscroll）／<688px: 1列＋ペイン切替・F6 |
| 代表的な結果 | 1280×820（sidebar 200）: 3列・入力314px／900×660（rail）: 2列／900×660でsidebar展開: 1列／1920: 3列 |
| ドラッグ | Pointer Events＋pointer capture。drag中だけ`user-select:none`とcol-resize cursor、drawer/mainのtransition停止。pointerup/cancel/lostpointercapture/unmountで全listenerを解除 |
| キーボード | 区切りは`role="separator"`・`aria-orientation=vertical`・`aria-valuenow/min/max`・`aria-controls`・`tabindex=0`。←→16px（Shift 64px）、Home/End＝最小/最大、Enter＝リセット。focus時はグリップが4pxの緑線になる |
| リセット | 区切りのダブルクリック／Enter、作業画面見出しの「ペイン幅をリセット」、サイドバー区切りのダブルクリック／Enter |
| 範囲外 | 値は保存時と描画時の両方でclampし、合計が作業領域を超えない（`resolvePanes`のVitestで任意比率を検査、E2Eで横overflow 0） |
| 保存 | しない（G-05/G-06未決）。再読み込みで既定に戻る |

## 4. 配色とコントラスト（WCAG 2.2 AA）

緑案: ライト `primary #1f6b4f`／背景`#f5f7f6`・面`#ffffff`、ダーク `primary #7fd1ad`（文字`#0b2a1d`）／背景`#131715`・面`#1b201e`。
意味色はブランド緑と色相を分けた（success＝黄緑〜オリーブ`#4f6400`/`#c8d98a`、info＝青灰、warning＝琥珀、error＝赤）。
計算方法はWCAG 2.xの相対輝度。Vuetifyの描き方（文字の不透明度・tonalの12%下地・枠線の不透明度）を合成して測った（`src/preview/theme.test.ts`がassert）。

| 指標（基準） | 緑ライト | 緑ダーク | 参考: UI-2青ライト | 参考: UI-2青ダーク |
|---|---|---|---|---|
| 本文（4.5:1） | 10.60 | 13.79 | 16.10 | 16.14 |
| 補助文（medium emphasis、4.5:1） | 6.31（面）/6.07（背景）※1 | 7.40 | 5.74 | 8.51 |
| primary文字（4.5:1）面/背景 | 6.41 / 5.96 | 9.14 / 10.01 | 6.88 / 6.36 | 7.45 / 8.21 |
| filled primaryボタンの文字（4.5:1） | 6.41 | 8.53 | 6.88 | 9.69 |
| tonal chip/alert 文字 primary/success/warning/error/info（4.5:1） | 5.38/5.57/5.00/5.37/5.65 | 7.10/8.14/7.53/7.45/7.04 | 5.77/5.36/5.00/5.37/5.77 | 5.91/7.31/7.35/7.27/5.91 |
| 選択行の文字（12%緑下地、4.5:1） | 9.23 | 10.72 | — | — |
| 入力欄の枠（非テキスト3:1） | **2.29→4.25**※2 | 3.17→5.78 | **2.68**（0.38のまま） | 3.50 |
| focus ring＝primary（3:1） | 6.41 | 9.14 | 6.88 | 7.45 |
| disabled文字（WCAG適用外、記録のみ） | 2.29 | 4.46 | 2.68 | 5.06 |

※1 緑ライトは文字色を純黒でなく`#1c2420`にしたため、Vuetify既定のmedium emphasis 0.6では4.2:1で不合格。テーマ変数で0.72にした。
※2 Vuetifyのoutlined入力欄の枠は不透明度0.38で3:1未満。プロトタイプだけ`preview.css`で0.6へ上げた（hover/focus時は既定どおり濃くなる）。

- 色だけで状態を区別しない: ジョブ状態chip・工程（✓/▶/○/–＋文言）・入力一覧の状態列・環境の判定（アイコン＋「利用可能（例）」等）・
  選択行（チェック＋左端の緑線＋下地）・topnavの現在地（下線＋太字＋`aria-current`）。
- ブランド緑の使用箇所: 主ボタン・選択・タブ下線・リンク・現在地・スイッチ・focus ring。背景・面・app barは中立色。
- 本番`src/styles/theme.ts`は**不変**。採択後に、本番へ入れる場合の差分（`on-surface`、medium emphasis、入力欄枠）を別PRで判断する。

## 5. レビュー用スクリーンショット（生成方法と一覧）

`cd frontend && npm run build && UI3_SCREENSHOTS=1 npm run e2e:prototype` → `frontend/test-results/ui3-screenshots/`（**commitしない**）。
snackbarは画像から隠す（`shot()`のstyle指定）。データはすべて合成・架空。

| 指示書の番号 | ファイル | 条件 |
|---|---|---|
| ① 通常ホーム light/green | `01-normal-light-green-1280.png`、`01b-normal-light-done-1280.png` | 1280×820、未開始／完了 |
| ② 通常 dark | `02-normal-dark-running-1280.png` | 実行中、詳細展開 |
| ③ Expert 1280 | `03-expert-1280.png` | 複数・処理中、2件選択、選択タブ |
| ④ Expert広幅・入力縮小＋Inspector拡大 | `04-expert-1920-wide-inspector.png` | 1920×1080、比率例「入力狭め・設定広め」、sidebarを240pxへdrag |
| ⑤ rail折りたたみ後 | `05-expert-rail-1280.png` | 再展開ボタンが上部に残る |
| ⑥ 900×660 | `06a-normal-900x660.png`、`06b-expert-900x660.png` | 通常（2カラム→1列）、Expert 2列 |
| ⑦ 環境GPU/CPU/未構成/失敗 | `07a-env-normal-gpu.png`、`07b-env-expert-cpu.png`、`07c-env-unconfigured-normal-home.png`、`07d-env-load-failed.png` | 通常は概要＋根拠展開、Expertは全プロファイル展開、未構成で開始不可の理由、読み込み失敗 |
| 追加 | `08a-queue-page.png`、`08b-results-page.png`、`dpi-{100,125,150,200}-expert.png` | 画面ごとの責務、DPI相当（Chromiumのエミュレーション） |

## 6. 試験結果（Linux cloud container、Node 22.23.4、headless Chromium）

結果はコミット時点の実測。PRのCI runを正本とし、run番号はPRコメントに記録する。

| gate | 結果 |
|---|---|
| `npm ci` / `npm run audit:prod` | ok / 0 vulnerabilities |
| `npm run lint` / `npm run typecheck` | ok（error 0・warning 0）/ ok |
| `npm test`（Vitest） | **116 passed**（16 files。プロトタイプ39＝demo 16・layout 5・envExamples 2・theme 12・i18n 3、ほか本番77） |
| `npm run build` | ok。JS 411.05 KB（gzip 141.70）・CSS 312.92 KB（gzip 44.72）＝UI-2・第1案と同一（試作を含まない） |
| `npm run e2e`（UI-2、実FastAPI） | **10 passed** |
| `npm run e2e:prototype` | **26 passed**（操作13・レビュー画像8・DPI 4・隔離1） |
| Python gates | §6.1 |

`e2e:prototype`の内訳（指示書§4の必須項目）: モード別nav（topbar 3／sidebar 6、通常に無いページからの復帰）、1件のみ表示（未選択・置換・
未対応・環境不可の理由）、状態保持（Expert⇄通常で入力5件・選択・override・共通編集が残り、投入されない）、Expert再展開（上部ボタン、
rail→展開）、resize&bounds（sidebar 168/360px、ペイン最小220px・最大、右ペインはInspector最小幅を割らない、preset・reset）、
focus/keyboard（separatorの←→/End/Enter、一覧のTab1回・↑↓・Space・End・Enter、dialogのEsc、F6）、3→2→1列、ライト/ダーク×ja/enで
横overflow 0、緑/青のprimary値、環境mockのデモ表示（全chipが「デモ用の架空情報」、再検出→検出中→表示）、build出力に試作の文言・
fixture・`greenLight`・`demo-profile-gpu`が無いこと、全テストで外部・`/api`・`/next`通信とconsole errorが0。

### 6.1 Python側（変更なし。非退行の確認）

`uv lock --check`・ruff・mypy（linux/win32）・`check_powershell_bom.py`・`pytest -m "not requires_model"`・privacy scanの結果は
PR本文に記録する（このcommitではPythonファイルを変更していない）。

## 7. 発見事項（本番への申し送り。今回は本番を変更していない）

1. **Vuetify 4ではMD2のtypography class（`text-caption`・`text-body-2`等）が廃止されている。** 本番UI-2の`ErrorAlert.vue`・
   `NotMigrated.vue`・`EnvironmentPanel.vue`と第1案プロトタイプの注記は既定サイズのまま表示されていた（第1案の注記が大きく見えた原因）。
   プロトタイプは`text-body-small/medium/large`へ置換し、これらが既定でRobotoを指定するため`--v-font-body`をシステムUIフォントへ戻した。
   本番の修正はS1以降の小PRで扱う。
2. UI-2青ライトの入力欄枠は2.68:1で、WCAG 1.4.11（3:1）を満たさない（Vuetify既定の0.38）。本番へ緑案を入れるかどうかに関係なく要修正候補。
3. dev serverのログにVuetify由来の`ResizeObserver loop completed with undelivered notifications`が出ることがある（第1案から既知、
   ページのconsole errorではない）。プロトタイプ自身のResizeObserverは次フレームで反映するようにした。

## 8. 未実装・保留・次回レビュー論点

**未実装・未検証**（隠さない）: Windows WebView2での表示・操作・ドラッグ、OS DPI実機（ChromiumのDPRエミュレーションは合格扱いにしない、UI-4 #22）、
native file picker・D&D（S1/S3）、IME（自由入力欄なし、S3）、実backend・SSE・B4 ready判定（環境はすべて架空）、タッチ操作でのresize、
画面読み上げソフトでの確認、幅・比率の永続化（G-05/G-06）、出力先・batch glob・resume、結果viewer・再出力、rerun、モデル画面、セットアップ実行。

**ownerに判断してほしい点**

1. 通常: 2カラム1画面（左＝ファイル＋設定、右＝実行と結果）でよいか。結果を右下に出す位置、完了後の「続けて別のファイル」の要否。
2. 通常で複数件を持つ場合の代表（最後に触れた入力）と案内文の言い回し。通常で「別のファイルにする」が未投入1件を置き換える挙動の是非。
3. Expert: 既定比率（30/42/28）と最小幅（220/340/240）、2列へ落ちる閾値（作業領域936px）。比率例2種のどちらを既定にするか。
4. サイドバー: 上部の折りたたみボタン位置、既定幅200px、<1024pxでrail開始。幅・比率を保存するか（G-05/G-06と同じ扱いか）。
5. 入力一覧: 列（名前・形式・長さ・状態・個別）と列の隠し順、クリック＝単一選択・Ctrl＝追加の操作系。
6. 作業画面右の要約の量（件数4つ＋最大6行）と、キュー画面・結果と履歴の責務分担。
7. 緑案: 色味（`#1f6b4f`/`#7fd1ad`）、successを黄緑に分けた判断、本番`theme.ts`へ入れるか（入れる場合は§7-2も同時に）。
8. 環境mock: 情報の粒度（ASR・モデル・device・精度・話者分離・メモリ・速度の目安）、根拠/警告/必要な操作の3区分、通常で推奨1件だけを出す方針。
9. レビュー帯を下端に置いた位置（第1案は本文上部）。

## 9. versionの判断

**0.1.43のまま上げない。** [リリース手順](../../リリース手順.md)の規則は「変更を加えた作業単位ごとにpatchを1つ上げる」。
今回は同じ作業単位（UI-3-0設計ゲート、未merge・未releaseのDraft PR #38）のレビュー反復で、変更は開発専用のプロトタイプ・試験・文書に限られ、
配布物・本番bundle・Pythonパッケージに差分が無い（build出力サイズ同一、§6）。新しいversion番号を付けても利用者に見える差が無いため、
`変更履歴.md`の0.1.43節に「第2イテレーション」として追記した。ownerが別作業単位として扱う判断をした場合は0.1.44へ上げる。
