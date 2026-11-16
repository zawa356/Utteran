# UI-3 S5-B（束C2b）— 複数ジョブの一括投入・論理ジョブのキュー/履歴・成果物ビュー

更新: 2026-11-16 JST（開発版`0.1.56`・未リリース）。対象は`--next-ui-v1`（開発専用）だけ。既定GUI・`--next-ui`は不変。
前提: [s5a-v1-single-job](s5a-v1-single-job.md)、[../b7/history-visibility](../b7/history-visibility.md)、[design-freeze §7](design-freeze.md)（内部Job IDと論理ジョブ）。

> **機能の接続であって、デザインの再現ではない。** §5の比較のとおり、画面構成と見た目はDesign Freezeにまだ届いていない（束D）。
> Cloudの合成試験（実FastAPI＋headless Chromium）までで、Windows WebView2実機・実モデルは未受入。**この束に削除機能は無い。**

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
- **キュー**＝終了していない行（古い順）、**履歴**＝終了した行（新しい順）、**非表示の履歴**＝スイッチを入れたときだけ読む。
- 一覧は`GET /api/v1/jobs`の全ページ（上限100ページ）と`GET /api/v1/queue`が正本。途中のページ失敗・revision不一致・重複IDは**エラー**で、部分的な一覧を出さない。
- 表示中かつ未完了の行があるときだけ2秒間隔で読み直す。全部終わるか、エラーになるか、画面を離れると止まる。
- 書き込みは明示操作だけ、一度に1つ:

| 操作 | API | 凍結するもの | 結果不明のとき |
|---|---|---|---|
| 取消 | `POST /jobs/{id}/cancel`（協調的のみ） | 最新の内部Job ID・revision・intent | 同じkey・bodyで再照合 |
| 再試行 | `POST /jobs/{id}/retry`（B7契約: 新しい内部Job、`retry_of`） | 同上。応答が「そのJobを指す新しいJob」でなければ不正 | 同上（replayで同じJobが返り、二重に作られない） |
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

**結論**: S5-Bは「実API→成果物」までの機能縦断を通したが、Design Freezeの画面構成（sidebar・専用ページ・表・詳細ペイン・状態チップ）は未実装である。
PlaywrightのDOM試験の合格をデザイン再現の証拠としない。画面の再構成は束Dで行い、その際は本表を出発点にし、Windows VMの画面確認を受ける。

## 6. 試験（Cloud Linux）

| 種別 | 内容 | 件数 |
|---|---|---|
| Vitest | `logicalJobs.test.ts` 10、`batchJobs.test.ts` 7、`jobBoard.test.ts` 10（fake v1 serverをfile別override・1 file＝1 job・visibility・artifacts・retryへ拡張） | 全体 514 passed |
| Playwright `v1` | `transcription-s5b.v1.spec.ts` 4（準備1＋一括投入・成果物、非表示/再表示、再試行＝同じ行）。実FastAPI・layout v7・実dispatcher・合成runner | v1 project 14 passed |
| Playwright 全体 | legacy 53＋v1 14 | 67 passed |
| pytest | `test_history_visibility.py`に`JobRuntime(store_type=)`の試験 | 全体はPR本文 |

### 6.1 既存試験の改訂（削除・skipなし）

- `transcription-s5a.v1.spec.ts`「Expert uses the same v1 controller…」: 無効表示の`v1-batch-start`を確認していた1行を、
  実パネルの「確認・投入とも、確認前は押せない」へ改訂（placeholderを削除したため）。それ以外の検証は同じ。

## 7. 未実装・後続

- 画面の再構成とDesign Freezeの再現（束D）。rerun・キューの順序変更/保留/一時停止のボードへの統合。
- Viewer・フォルダを開く・再出力（束E）。作業セット・自動復元（束E）: 現状、再起動後はファイル名が出ない。
- 履歴レコードの削除（墓標化）・成果物削除（束C2c、別PR）。
- B9 SSE（`/events`）による更新（現状は有界polling）。進捗%（G-03）。
- Windows受入: [../program/windows-acceptance-w-c2.md](../program/windows-acceptance-w-c2.md)。
