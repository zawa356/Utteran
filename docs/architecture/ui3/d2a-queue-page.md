# UI-3 束D2-A — Expertキュー専用画面（一覧・検索・詳細、読み取り専用）

更新: 2026-11-19 JST（開発版`0.1.59`・未リリース、Draft PR）。親: [UI-3 #21](https://github.com/zawa356/Utteran/issues/21)／[Epic #18](https://github.com/zawa356/Utteran/issues/18)。
基点: main `e9745dfb8ecd248319153eef8ca09c064538f8ed`（0.1.58、束D1統合、main push CI [run 44445796022](https://github.com/zawa356/Utteran/actions/runs/44445796022) success）。
画面方針の正本は[design-freeze](design-freeze.md)（第5案、§7 論理ジョブ）、キューの実挙動の正本は[S5-B §3](s5b-multi-job-board.md#3-キューと履歴domainv1jobboardtscorev1board)とコード。
D2全体ではない。D2-B（キュー操作）・D3（結果と履歴）・D4（外観統合・実機）は後続。

## 1. 着手時の現況（再利用したもの／新規）

| 場所 | 着手時（main 0.1.58） | D2-Aの扱い |
|---|---|---|
| `domain/v1/jobBoard.ts` `V1JobBoardController` | 全ページの`GET /api/v1/jobs`（最大100ページ）＋`GET /api/v1/queue`。queue/history/hiddenに分類、fail-closed。`watch(on)`は単一boolean | **唯一の読み取り正本として再利用**。追加は`retainWatch()`（polling所有のlease、§4）のみ。読み取り・分類・書き込みの挙動は無変更 |
| `domain/v1/logicalJobs.ts` | `retry_of`連鎖→1行、`rootId`が安定キー、未完了枝が1つでもあればqueue | 無変更で再利用 |
| `features/transcribe/V1JobBoard.vue`（Expert文字起こし③） | キュー＋履歴＋非表示、取消・再試行・非表示・削除確認 | **操作・表示とも残す**（D2-B/D3で段階移転）。`onMounted/onUnmounted`の`watch(true/false)`をleaseへ置換した2行のみ |
| `api/v1Resources.ts` `JobV1`/`QueueViewV1` | path・成果物名・工程・進捗%なし | 無変更。DTO拡張・新API clientなし |
| `features/shell/AppShell.vue` | Expertの`queue`は`NotMigrated` | `apiMode === "v1-dev"`のときだけ新`QueuePage`。legacyは`NotMigrated`のまま |
| `domain/shell.ts` `SECTION_STATUS` | `queue: not_migrated`（全モード） | 静的表は不変。`sectionStatus(section, apiMode)`を追加し、v1-devのキューだけ「一部移行」。sidebarはこれを使用 |
| `frontend/src/preview/*` | 第5案の試作（`QueueSection.vue`等） | **参照のみ**。importなし。合成状態モデルは移植していない |

新規: `features/queue/QueuePage.vue`（画面・表示状態）、`QueueTable.vue`（表）、`QueueDetail.vue`（詳細）、`queueRows.ts`（純関数）。

## 2. 仕様

### 2.1 表示条件

- `--next-ui-v1`（server metaの`v1-dev`）かつExpertの「キュー」だけ。legacy `--next-ui`は従来の未移行案内で、`/api/v1`を呼ばない（試験で0件）。
- Normalの上部3導線・Expertの6導線の並び、LegacyLink・常設案内・CSP・既定起動は不変。Normalにキュー導線は無い。

### 2.2 一覧（論理ジョブ1行）

- 行＝`board.store.queue`の論理ジョブ（キー`rootId`）。表示・照合の内部Jobは`board.current(group)`（B7が先に実行する未完了の内部Job。最新ではない）。
- 並びの既定は`board.store.queue`の順＝B7順（実行中→待機position順→位置不明は末尾）。`queueIndex`はこの順で、表示ソートでも変わらない。
- 列: **順序**（B7の`active`なら「実行中」、`position`があれば「待機 N」＝B7の値、無い／nullなら「順位不明」。画面の行番号は出さない・0を出さない）、
  **名前**（このセッションの入力行から得たbasenameのみ。無ければ「ファイル名不明」＋内部Job ID・モデル・profile）、**状態**（`status`のi18n、色付きchip）、
  **工程**（常に「未提供」。B7公開DTOに工程・進捗が無い。ページ上部に理由を明記）。保留（queue itemの`held`／Jobの`held`／`status=held`）はchip。
- 試行回数2以上・未完了枝2以上は名前列の補助行に表示。

### 2.3 選択と詳細（読み取り専用）

- 行のクリック／Enter／Spaceで選択（`aria-selected`）、↑↓・Home・Endキーでフォーカス移動（roving tabindex）。表は`role="grid"`・`aria-readonly`。
- 選択は`rootId`で保持。pollingで同じ行が更新されると詳細は**最新の取得値**で再構築（古い行オブジェクトを保持しない）。
- 完了した読み取り（`phase=done`）でキューから消えた行は選択を解除し「キューから外れました（終了なら履歴）」を表示。読み込み中・エラー中は判定せず、詳細も出さない（旧成功データを確定表示しない）。復旧後に行が残っていれば詳細が戻る。
- 詳細: 論理Job ID・現在の内部Job ID・状態・B7の位置/保留・モデル(revision)・profile/backend/device/compute type・作成/更新日時・reason/error/block/blockers/recovery（既存code→i18n、未知codeは「理由コード X」）、
  実行記録数、未完了枝の全ID・状態・B7位置、全試行の一覧、不完全連鎖・rerun由来。path・prompt・token・出力先・transcriptはDTOに無く、表示・ログしない。
- 状態は別表示: 読み込み中（`queue-loading`）／エラー（`queue-error`、kind・codeをdata属性、部分一覧なし）／キュー0件（`queue-empty`）／絞り込み0件（`queue-no-match`、キュー件数を併記）／未選択（`queue-detail-none`）。「再読込」は常設の明示ボタン（GETのみ）。
- 操作ボタンは置かない。取消等は文字起こし③にあると案内。`board.action`がpending/unknownなら「文字起こし画面で再照合」と案内し、ページ遷移ボタンのみ（再送しない）。dispatch停止中は表示のみ。

### 2.4 検索・絞り込み・並べ替え（表示のみ）

- 対象は完全に取得できた`board.store.queue`だけ（エラー時は何も無い）。サーバー検索APIなし。
- 検索: 既知のbasename・連鎖の全内部Job ID・モデルID・profile。NFKC＋小文字、空白のみは無検索。IME変換中は確定まで適用しない。Enterは何もしない。
- 絞り込み: すべて／実行中（running・cancelling・active）／待機中（queuedかつ非保留）／保留中／要確認（blocked・waiting_user・recovery・block reason・未完了枝2以上・不完全連鎖）。推論ステージ別は作らない。
- 並べ替え: キューの順（既定）／作成日時（連鎖の最初の内部Job）／状態（実行中→…→待機）／名前（既知名を先、Intl.Collator、不明は末尾）。同順位は常にB7順へfallback。
  「表示の並べ替えでは実行順は変わらない」を常設文言と並べ替え中の注記で示す。読み取り操作はPOST/PUT/PATCH/DELETEを送らない（試験で0件）。

### 2.5 レイアウト

- `.u-queue`はcontainer query。ページ幅900px以上で「表＋右詳細（280px〜24rem）」、未満は詳細を表の下に積む。表は`table-layout: fixed`、長いID・モデル名は省略（`title`で全体）または折り返し。ページ全体の横スクロールなし（900/1100/1280px × ja/en × light/darkで試験）。
- D4の可変3ペイン・ドラッグ幅・キュー件数chipは対象外。

## 3. polling（単一controllerの所有）

- 変更前: `V1JobBoard`が`watch(true)`／`watch(false)`を直接呼ぶ単一boolean。2画面が同時にmountされる場合、後から来た`watch(false)`が他方のpollingを止め得た。
- 確認: `AppShell`は`section`の`v-if`連鎖で`ExpertWorkspace`（③の`V1JobBoard`）と`QueuePage`を同時に描画しない。ただしVueのunmount/mount hookの実行順に依存しないよう、
  **lease**（`board.retainWatch()`が解放関数を返す）を導入。取得で即時`refresh()`、保持者が1人以上の間だけ従来の2秒polling（timerは1つ）、最後の解放で停止。二重解放は無効。
- 未完了が無い・エラー・画面離脱でpollingが止まる現仕様、世代番号による古い応答の破棄、全ページ取得のfail-closedは無変更。第二のpoller・別controllerなし。

## 4. 試験（Red→Green）

Red試験を先にコミット（`4542dbb`）。基点mainのコードに対して:

- Vitest: **5 failed**（`retainWatch`・`sectionStatus`が無い）＋`queueRows.test.ts`はmodule未存在で収集失敗。
- Playwright（新2 spec）: **18 failed／2 passed**（Expertキューが`NotMigrated`。通過2件はlegacyガードとキュー掃除のsetupで、旧コードでも通るのが正しい）。

| ID | 試験 |
|---|---|
| D2A-01 | `transcription-s5e-queue.v1.spec.ts`「real B7 data…」、`queue-d2a.spec.ts`（legacy: NotMigrated・`/api/v1` 0件・書込0件）、Vitest `shell.test.ts` D2A-01 |
| D2A-02 | 同「crafted B7 answers…」（1→2, 1→3の分岐が1行、currentは実行中枝、詳細に全枝）、Vitest queueRows D2A-02×2 |
| D2A-03 | 実データでactive/position（`GET /queue`と照合）、craftedで保留・位置null／itemなし→「順位不明」末尾、Vitest D2A-03×2 |
| D2A-04 | 実データ（クリック・↓・Enter、`aria-selected`、終了で選択解除）、crafted「a selected row keeps its root…」（polling更新で値置換、完了で解除）、Vitest D2A-04 |
| D2A-05 | 実データ（名前/ID/パス片の検索、状態フィルタ、名前ソート、queue revision・item順不変、書込0件）、Vitest D2A-05×6 |
| D2A-06 | 実データ（DOMにdata root・`/in/`なし、basenameのみ）、crafted（「ファイル名不明」＋ID）、Vitest D2A-06 |
| D2A-07 | crafted「multi-page…」（2ページ・cursor循環・ページ間revision不一致・重複ID・2ページ目500・queue 503・不正status→エラー、行0件、polling停止、再読込で復帰、選択維持）、「network loss…」 |
| D2A-08 | 「one board poller…」（③⇔キュー⇔Normal 10往復で読取頻度が増えない、両画面離脱で0件）、「an unknown board write…」（結果不明の取消をキュー画面は再送しない、明示再照合は同key）、Vitest `jobBoard.watch.test.ts`×4 |
| D2A-09 | 「D2A-09: {900,1100,1280}px {ja,en} {light,dark}」12件（横overflow 0、表と詳細が重ならない、全セルが表内、0件・エラー表示） |
| D2A-10 | 既存の全E2E（legacy・v1）・Vitest・prototype E2Eを無変更で実行（skip/xfail/timeout延長なし） |

全体の件数・CIはPR本文に記録する。

## 5. Design Freezeとの比較（D4・後続への残件）

採取: `UI2_SCREENSHOTS=1 npx playwright test --project=v1 e2e/transcription-s5e-queue.v1.spec.ts`（`test-results/screenshots/d2a-*.png`、commitしないローカル成果物）。
プロトタイプは`UI3_SCREENSHOTS=1 npx playwright test -c playwright.preview.config.ts`のキュー画面。headless Chromium、deviceScaleFactor 1。

| 観点 | 第5案 | D2-A後 | 残件 |
|---|---|---|---|
| ページ構成 | 管理sidebar＋キュー専用ページ、表＋右に詳細 | 同じ構成（狭い幅では詳細を下に積む） | 可変3ペイン・幅ドラッグ（D4） |
| 表の列 | #・名前・状態・工程・↑↓ | 順序（B7の位置）・名前・状態・工程（「未提供」） | ↑↓順序変更（D2-B）。工程・進捗%はB7公開DTOに無い（G-03等で別途設計） |
| 名前 | 常に入力名 | このセッションで追加した入力のbasenameのみ。再起動後は「ファイル名不明」＋ID | 公開DTOへのpath追加はしない方針（privacy）。表示名の永続化は別途owner判断 |
| ツールバー | 検索・絞り込み・並べ替え・一時停止・片付け・すべて次へ | 検索・絞り込み・並べ替え・再読込 | 一時停止/再開・保留・片付け（D2-B）。「すべて次へ」は試作専用 |
| 詳細 | 投入時設定・保存先・出力名・工程チップ | ID・状態・B7位置・モデル/実行環境・時刻・理由code・試行/枝 | 設定要約・保存先・出力名は公開DTOの拡張設計が要る（束E等） |
| 状態表現 | 色付きチップ＋アイコン | 色付きchip（文字） | アイコン・密度の仕上げ（D4） |
| 実機 | WebView2・OS DPI 125/150/200% | 未実施（headless Chromiumのみ） | D4／[#22](https://github.com/zawa356/Utteran/issues/22) |

目視所見（`d2a-02-real-selected`と試作`i5-06-queue-before-cleanup`、1280px ja light）: sidebar＋表＋右詳細の骨格、表の4列、選択行の強調は一致。
製品は上部に常設案内と説明3行（並び・工程未提供・操作場所）があるため表の開始位置が低い。状態chipにアイコンが無い。詳細は試作の「投入時の設定・保存先・出力ファイル・工程チップ・再実行」に代わりID・B7位置・実行環境・理由codeを示す。
900pxでは詳細が表の下に積まれ、長いIDとモデル名は省略表示（`title`で全体）、状態chipが長い場合も省略（`title`付き）。

DOM試験の合格をDesign Freezeの完全再現とはしない。

## 6. D2-Bへの引継ぎ

- キュー操作の実接続（1操作ずつ別試験）: dispatch一時停止/再開（`PATCH /queue/control`）、保留/解除（`PATCH /queue/items/{id}`）、順序変更（`PUT /queue/order`）、cancel、retry、終了行の片付け（現行の履歴visibilityとの契約整合）。
  いずれもqueue revision/Job revisionのCAS、412の再読込、凍結したidempotency-key/bodyによる結果不明の明示再照合、一度に1 mutation。既存の`board.action`（③と共有）との排他を設計する。
- 操作の移転先は本ページ。③の`V1JobBoard`の操作をいつ外すかはD2-B/D3で決める（二重実装の期間を最小にする）。
- 表示ソート中は順序変更を出さない（第5案と同じ）。`queueIndex`と`position`の区別を維持する。
- [#60](https://github.com/zawa356/Utteran/issues/60)（retry/cancel・revision競合の間欠問題）はD2-Bのreviewで扱う。本PRでは変更していない。

## 7. 範囲外（完了と扱わない）

- D2-B キュー操作、D3 結果と履歴ページ・rerun・Viewer、D4 外観・3ペイン・実機DPI。[#59](https://github.com/zawa356/Utteran/issues/59) 成果物削除（全OSで`artifact_deletion_disabled`のまま）。
- 既定GUI/CLI・`--next-ui`のAPIモード・CSP・DB schema・API/IPC・公開DTO・依存パッケージは無変更。
