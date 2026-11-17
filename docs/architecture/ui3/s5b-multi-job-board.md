# UI-3 S5-B（束C2b）— 複数ジョブの一括投入・論理ジョブのキュー/履歴・成果物ビュー

更新: 2026-11-17 JST（開発版`0.1.56`・未リリース。Windows統合受入の結果と所見3件の修正を反映）。対象は`--next-ui-v1`（開発専用）だけ。既定GUI・`--next-ui`は不変。
前提: [s5a-v1-single-job](s5a-v1-single-job.md)、[../b7/history-visibility](../b7/history-visibility.md)、[design-freeze §7](design-freeze.md)（内部Job IDと論理ジョブ）。

> **機能の接続であって、デザインの再現ではない。** §5の比較のとおり、画面構成と見た目はDesign Freezeにまだ届いていない（束D）。
> Windows 11 VM（実WebView2・CPU実モデル、合成データ）の統合受入は **CONDITIONAL**（HEAD `1c76453`、14項目中12 PASS／W-01 FAIL／W-12 BLOCKED。§8）。
> 一括投入・キュー順・retry分岐・非表示/再表示・成果物照合・v6→v7移行の経路は実機で受入済み。残件あり（§8.2）。**この束に削除機能は無い。**

## 1. 画面

| 場所 | 追加・変更 |
|---|---|
| Expert ③ | 「一括投入」パネル（`V1Batch.vue`。無効表示だった`V1BatchDisabled.vue`を置換）、「キューと履歴」（`V1JobBoard.vue`）、従来の1件投入（`V1Job.vue`） |
| Normal | 完了したジョブに成果物ビュー（`V1Artifacts.vue`）。投入は従来どおり1件 |
| 共通 | ファイル別設定の注記を「一括投入で各ファイルの差分として送る」へ改訂 |

## 2. 一括投入（`domain/v1/batchJobs.ts`、`core.v1.batch`）

1. 「① まとめて確認」: 選択した行を1つのEditingSessionへ凍結（行ごとに1 file。行のoverrideはleaf override、行の出力先は`output_dir`）。
   `POST /validations` → 全fileが有効で、**有効設定が画面の計算と完全一致**したときだけ `POST /execution-resolutions`。
2. 「② N件を投入」: 凍結したsessionとtokenで`POST /jobs`を**1回**。B7は全件受理か全件拒否（1トランザクション）なので、画面に部分受理は無い。
3. 4xx＝拒否（ジョブなし）。応答なし・timeout・5xx・読めない2xx・「1 file＝1 job」でない応答＝結果不明 → **同じkey・同じbodyの明示再照合だけ**。
4. 確認後の編集（行・選択・override・出力先・Preset/Policy）は古い確認として投入を止める。tokenの期限切れも同じ。
5. 行ごとのデバイス指定は表現できない（デバイスはPolicyとB4の証拠で決まる）ので、**要求を送る前に拒否**して理由を出す。黙って落とさない。
6. 旧`POST /api/jobs`は呼ばない。受付後は下のボードとキュー表示を読み直す（キューrevisionが進むため）。

## 3. キューと履歴（`domain/v1/jobBoard.ts`、`core.v1.board`）

- **論理ジョブ**（`domain/v1/logicalJobs.ts`）: `retry_of`の連鎖を1行に。行の状態・成果物は連鎖の最新の内部Job。`rerun`は別の行。
  元の試行が一覧に無い連鎖は「不完全」と表示して独立行にする。循環・重複IDでも止まらず欠落させない。
- **キュー**＝未完了の内部Jobが1つでもある行、**履歴**＝全ての内部Jobが終了した行（新しい順）、**非表示の履歴**＝スイッチを入れたときだけ読む。
- **キューの並びはB7の順**（PR #53レビュー P2-1）: `GET /queue`の`items`を内部Job IDで行へ紐づけ、実行中（active）→ 待機の`position`順に並べ、
  行に「実行中の枠を使用中／待機 N番目／保留中」を表示する。キューが位置を返さなかった行（`waiting_user`等、読み取りの間に状態が変わった行）は末尾に置き、
  **順番を表示しない**（推測しない）。行の作成順はキューの順ではない: A失敗→B待機→A再試行では、Aの行はBの後ろに出る。
- **分岐した連鎖**（PR #53レビュー P2-2）: B7は同じJobへの再試行を複数回受け付けるので、連鎖は枝分かれし得る。行が表示・操作する内部Job（`currentJob`）は
  「未完了のうちB7が先に実行するもの」で、最新の内部Jobではない。最新の枝が終了していても、未完了の枝が残る行はキューに残り、pollingも続く。
  未完了の枝が複数あるときは全てを列挙し、**枝ごとの内部Job IDで個別に取消**する。未完了の枝がある間は再試行・非表示を出さない（枝をさらに増やさない）。
- 一覧は`GET /api/v1/jobs`の全ページ（上限100ページ）と`GET /api/v1/queue`が正本。途中のページ失敗・revision不一致・重複IDは**エラー**で、部分的な一覧を出さない。
- 表示中かつ未完了の行があるときだけ2秒間隔で読み直す。全部終わるか、エラーになるか、画面を離れると止まる。
- 書き込みは明示操作だけ、一度に1つ:

| 操作 | API | 凍結するもの | 結果不明のとき |
|---|---|---|---|
| 取消 | `POST /jobs/{id}/cancel`（協調的のみ） | 行の現在の内部Job（または指定した枝）のID・revision・intent | 同じkey・bodyで再照合 |
| 再試行 | `POST /jobs/{id}/retry`（B7契約: 新しい内部Job、`retry_of`） | 全て終了した行の最新の内部Job。応答が「そのJobを指す新しいJob」でなければ不正 | 同上（replayで同じJobが返り、二重に作られない） |
| 非表示/再表示 | 行の各内部Jobへ `GET` → `PUT /jobs/{id}/visibility` | PUT直前に読んだvisibility revision | 同じ操作をやり直す（冪等。既に同じ状態なら何も書かない） |

- 412は「状態が先に変わっていた」と表示して一覧を読み直す。未解決の操作がある間、他の書き込みは始めない。
- 非表示にできるのは、行の**全ての内部Jobが終了**しているときだけ。非表示は表示の属性で、Job・snapshot・成果物を変えない・消さない。
- layout v7より前のstoreでは非表示が無いことを表示する（filterの422をエラーにしない）。
- ファイル名は**このページで追加した行**にだけ出る（公開APIはpathを返さない）。再起動後は内部Job ID・モデル・profile・時刻で識別する。

## 4. 成果物ビュー

`GET /api/v1/jobs/{id}/artifacts`を「成果物を確認」で読む（自動では読まない）。形式・サイズ・照合結果
（完了時の記録と一致／完了後に変化／記録なし）と、Utteranが書いていないファイルの数を表示する。**ファイル名・場所は表示しない。**
本文の表示（Viewer）、フォルダを開く、再出力/exportは未実装（束E）。

## 5. Design Freezeとの比較（2026-11-16、合成データ、headless Chromium 1280×820）

比較した画像（どちらもcommitしないローカル成果物）: 製品 `frontend/test-results/screenshots/s5b-batch-board.png`・`s5b-hidden-history.png`
（`UI2_SCREENSHOTS=1 npx playwright test --project=v1`）、プロトタイプ `frontend/test-results/ui3-screenshots/i5-06`〜`i5-09`
（`UI3_SCREENSHOTS=1 npx playwright test -c playwright.preview.config.ts e2e-preview/iteration5.spec.ts`）。

| 観点 | Design Freeze（第5案） | 製品（0.1.56） | 判定 |
|---|---|---|---|
| Expertの骨格 | 管理sidebar（文字起こし/キュー/結果と履歴/モデル/セットアップ/設定）＋ページ | sidebarなし。3ペイン内に縦積み | **未達**（束D） |
| キュー | 専用ページ。表（#・名前・状態・工程）、行選択で右に詳細 | ③ペイン内のリスト。詳細ペインなし | **未達**（束D） |
| キューの操作 | 検索・絞り込み・並べ替え・一時停止・順序変更（↑↓）・「終了したジョブを片付け」 | 取消・再試行・読み直しのみ。一時停止/再開は1件投入パネル側 | **未達**（順序変更・保留はB7にAPIあり、UI未接続） |
| 履歴 | 「結果と履歴」として別ページ。キューと分離 | 同じカード内で「キュー」「履歴」「非表示の履歴」に分離 | 概念は一致、配置は未達 |
| 再試行/再実行 | 「再試行＝同じジョブ」「現在の設定で再実行＝新しいジョブ」 | 再試行＝同じ行（論理ジョブ、§7追補どおり）。**再実行（rerun）は未接続** | 一部達成 |
| ジョブ詳細 | 投入時の設定・保存先・出力ファイル名・工程チップ | 試行回数・モデル/profile・時刻・内部Job ID・固定codeの理由。設定の要約・保存先・ファイル名・工程なし | **未達**（公開DTOにpath/進捗が無い。束E・G-03） |
| 成果物 | ファイル名を並べ、開く/コピー | 形式・サイズ・照合結果のみ（名前・場所は出さない） | 意図的な差（privacy方針）。開く/Viewerは束E |
| 状態の表現 | 色付きチップ＋アイコン | 文字のみ | **未達**（束D） |
| ボタン・密度 | 標準サイズ、表形式で一覧性が高い | リスト内の小さいボタン、行が縦に長い（内部IDが折り返す） | **未達**（束D） |
| 複数投入 | ③「キュー投入」で件数と対象を確認して投入 | 「一括投入」パネル（確認→投入の2段階、対象一覧、拒否理由） | 機能は達成、見た目は未達 |
| 非表示 | 第5案に明示の画面なし（履歴削除と成果物削除を分ける方針のみ） | 「非表示」「再表示」、非表示の履歴のスイッチ | 方針に沿う新規要素（束Dで配置を決める） |

### 5.1 束Dの受入条件（未完了のまま引き継ぐ）

次は**本束では未達**であり、束Dの完了条件とする（DOM試験の合格では満たしたことにしない。Windows VMの画面確認を受ける）。

1. Expertの管理sidebar（文字起こし／キュー／結果と履歴／モデル／セットアップ／設定）とページ切替。
2. キュー専用ページ: 表（#・名前・状態・工程）、行選択で右に詳細ペイン、検索・絞り込み・並べ替え。
3. キュー操作の統合: 一時停止/再開、順序変更、保留、「終了したジョブを片付け」（B7のAPIは既存。CAS・冪等性を崩さない）。
4. 「結果と履歴」ページの分離と、再実行（rerun）の接続。
5. 状態チップ（色＋アイコン）、標準サイズのボタン、狭幅・高DPIでの縮退。
6. ジョブ詳細: 投入時設定の要約・保存先・出力名（公開DTOの拡張が要る場合は別途設計）。
7. 製品画面とDesign Freezeのスクリーンショット比較を、主要画面ごとに更新する。

**結論**: S5-Bは「実API→成果物」までの機能縦断を通したが、Design Freezeの画面構成（sidebar・専用ページ・表・詳細ペイン・状態チップ）は未実装である。
PlaywrightのDOM試験の合格をデザイン再現の証拠としない。画面の再構成は束Dで行い、その際は本表を出発点にし、Windows VMの画面確認を受ける。

## 6. 試験（Cloud Linux）

| 種別 | 内容 | 件数 |
|---|---|---|
| Vitest | `logicalJobs.test.ts` 11、`batchJobs.test.ts` 7、`jobBoard.test.ts` 15（fake v1 serverをfile別override・1 file＝1 job・visibility・artifacts・retry・B7のキュー順/保留へ拡張） | 全体 520 passed |
| Playwright `v1` | `transcription-s5b.v1.spec.ts` 5（準備1＋一括投入・成果物、非表示/再表示、再試行＝同じ行、再試行した行のキュー位置）。実FastAPI・layout v7・実dispatcher・合成runner | v1 project 15 passed |
| Playwright 全体 | legacy 53＋v1 15 | 68 passed |
| pytest | `test_history_visibility.py`に`JobRuntime(store_type=)`の試験 | 全体はPR本文 |

### 6.1 既存試験の改訂（削除・skipなし）

- `transcription-s5a.v1.spec.ts`「Expert uses the same v1 controller…」: 無効表示の`v1-batch-start`を確認していた1行を、
  実パネルの「確認・投入とも、確認前は押せない」へ改訂（placeholderを削除したため）。それ以外の検証は同じ。

### 6.2 Windows受入の所見への対応（2026-11-17、版据置）

| 所見 | 対応 | 回帰試験 |
|---|---|---|
| W-01: 長いパスのpytestが治具の`mkdir`で停止（70文字×3階層はWindowsで作成不能） | 治具だけを修正。`job_output_name`が拒否する最短の親長を契約から求め（現行168）、実際の基点長に合わせて短い階層で親を作る（拒否境界＋16、Windowsのディレクトリ上限248未満、拡張パスなし）。製品の`PATH_BUDGET`・拒否動作は無変更 | `tests/test_job_output_dir.py`: 既存の422／`/files/0/output_dir`／`output_path_too_long`／生成0件に加え、親の実在・長さ・契約の拒否を事前確認。境界の1文字手前は受理される試験を追加（36件） |
| P3: 常設案内がv1の実装と不一致 | サーバー由来の`apiMode === "v1-dev"`のときだけ`app.previewNoticeV1`を表示（`previewNoticeKey`）。`v1.mode.notice`を「通常＝1ファイル、Expert＝一括投入・キュー/履歴・成果物照合」へ。旧`--next-ui`の`app.previewNotice`と`tx.batch.*`は無変更 | Vitest `shell.test.ts`（mode別のkey、ja/en文言）、Playwright `transcription-s5a.v1.spec.ts`（v1の通常/Expert）・`api-mode.spec.ts`（legacyは従来文言） |
| P3: 非表示直後に補助一覧「サーバー上のジョブ（v1）」へ内部Jobが残る | 非表示/再表示が`done`になりボードを読み直した後に、**取得済みの**補助一覧だけを`GET /jobs`から読み直す（`BoardOptions.onVisibilitySettled` → `refreshRecentIfLoaded`）。pending・拒否・412・結果不明では読み直さず、手元での先行削除もしない。pollingなし | Vitest `boardRecentSync.test.ts` 4件（retry連鎖2件が消える→再表示で戻る、未取得は読まない、結果不明は再送なし・明示再照合後に反映、412/拒否）、Playwright `transcription-s5b.v1.spec.ts`（1件の行、2件の連鎖、DELETE 0件） |

上表の修正後の件数: Vitest 525 passed、Playwright 68 passed（legacy 53＋v1 15）、pytest 1647 passed／9 skipped／1 xfailed（Cloud Linux）。
**修正後のHEADでのWindows実機再試験（W-01のpytest、実WebView2での案内文と補助一覧）は未実施**（Codexの別受入）。

## 7. 未実装・後続

- 画面の再構成とDesign Freezeの再現（束D）。rerun・キューの順序変更/保留/一時停止のボードへの統合。
- Viewer・フォルダを開く・再出力（束E）。作業セット・自動復元（束E）: 現状、再起動後はファイル名が出ない。
- 履歴レコードの削除（墓標化）・成果物削除（束C2c、別PR）。
- B9 SSE（`/events`）による更新（現状は有界polling）。進捗%（G-03）。
- Windows受入: [../program/windows-acceptance-w-c2.md](../program/windows-acceptance-w-c2.md)。

## 8. Windows統合受入（2026-11-17、HEAD `1c76453`）

出典: [実機報告](https://github.com/zawa356/Utteran/pull/53#issuecomment-6934835816)、[オーナーレビュー](https://github.com/zawa356/Utteran/pull/53#pullrequestreview-5931054818)。Windows 11 Pro（Hyper-V VM）、実WebView2、CPU実モデル`faster-whisper:large-v3-turbo`、合成データのみ。
**判定はCONDITIONAL。以下は`1c76453`での実測であり、その後の修正HEADで再実行したものではない。**

### 8.1 実機で受入済みの経路

- W-02〜W-05: CPU smoke・inventory、Expertからの1 POST・3件の原子的一括投入（全件完走）、ファイル別の出力先と形式override、不正1件を含むbatchは生成0件。
- W-06〜W-10: 確認の失効、実行順・保留・並べ替えがB7と一致、協調取消、retry（新ID・同一snapshot）、兄弟retryの個別取消、再起動後の復元、
  非表示/再表示でジョブ・snapshot・結果・Outboxが不変、成果物の形式別検証・差し替え検知・未知ファイル検知。旧投入・DELETEは0件。
- W-11: C1（layout v6）からv7への移行で同じジョブ・成果物を保持。旧版は`store_schema_unsupported`で拒否。バックアップからの復元。
- W-13: 既定GUIでv1 APIは404・v1 DB生成0件、CLIの出力と元ファイルの不変。

### 8.2 残件（合格扱いにしない）

- **W-01 FAIL**: 上記§6.2の治具問題。Cloudで修正済みだが、Windows実機でのPASSは未確認。
- **W-12 BLOCKED**: 100%の狭幅/最大化・日本語IME・picker起動は確認済み。DPI 125/150/200%、pickerでの選択確定、Explorerからの複数WAVのD&Dは未実施。
- 未実施: 実CPU稼働中の接続断（W-06の一部）、強制kill（W-11の一部）。
- 対象外のまま: CUDA/NPU、Design Freezeの画面構成（束D、§5）、履歴・成果物の削除（束C2c）。
