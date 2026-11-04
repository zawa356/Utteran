# UI-1 欠陥・再現・推定リスク

更新: 2026-11-04 JST。対象: 現行既定GUI（`src/utteran_gui/web/app.js` 1,984行、`index.html`、`api.py`）。
基点 `main` = `068e62fce1b35960703b534426adfbe373ff86d8`。

## 分類の定義

| 区分 | 意味 |
|---|---|
| **実証済み欠陥** | 現行mainの実コードを実行し、決定的な条件で誤った結果を観測した |
| **コードからの推定リスク** | 実コードの読解で成立条件を特定したが、今回は実行再現していない |
| **実機未確認** | Windows WebView2 / pywebview / 実Explorer D&D等、この環境では検証できない |

**ユーザー報告の「ボタン無反応・画面更新漏れ」の原因を、以下のどれかに断定しない。** D-01/D-02/D-04/D-05は
同じ症状を生み得る有力候補だが、配布版での実際の発生条件は未確認である（Windows実機での観測手順は
[validation.md](validation.md#windows-webview2-実機での次回試験手順)）。

## 再現環境

- ハーネス: [`tools/ui1/legacy_gui_repro.mjs`](../../../tools/ui1/legacy_gui_repro.mjs)。実物の`index.html`・`i18n.js`・
  `app.js`・`styles.css`を、本番と同じCSP（`api.py`の`secure_api`）でChromiumに読み込み、`/api/*`だけを
  決定的なmockに置き換える。操作はPlaywrightの実click・`<select>`変更で行い、DOMを読む。
- 実行: Chromium 153.0.8010.12（Playwright同梱、headless）、Node v22.23.4、Linux。2回連続実行で同一結果。
- 合成値のみ（`synthetic.wav`等）。実音声・実モデル・GPU・外部通信・個人パスは使用しない。
- これは**Chromium headlessの結果であり、WebView2実機合格ではない**。

## 実証済み欠陥

| ID | 優先度 | シナリオ（ハーネス名） | 期待 | 実測 |
|---|---|---|---|---|
| D-01 | P1 | `env-stale-profile` | 最後に選んだ`cpu`を維持 | 遅い`cuda`応答が後着し、`profile-summary`と`#profile-select`が**`cuda`に巻き戻る** |
| D-02 | P1 | `wizard-step-no-feedback` | 失敗を表示し再試行できる | `PUT /api/wizard/state`が500でも**画面は変わらず、通知なし**（unhandledrejection 1件のみ） |
| D-03 | P2 | `job-finish-twice` | 終了遷移は1回 | SSE `done`と1秒pollerの両方が`finishJob()`を実行し、**保持ログが2回追記**（GET 3回） |
| D-04 | P1 | `stale-finish-orphans-next-job` | ジョブBの追跡を継続 | ジョブAの遅い終了GETがB投入後に完了し、**BのEventSourceとpollerを閉じる**（以後Bのpoll 0回）。Aの結果が表示される |
| D-05 | P2 | `settings-save-no-feedback` | 保存失敗を表示 | `PATCH /api/settings`が500でも**`#settings-status`は空、dialogなし** |
| D-06 | P2 | `cancel-failure-stuck` | 取消失敗を表示し、ボタンを戻す | POST前に「キャンセルを要求しました」を表示し、POST失敗後も**ボタンはdisabledのまま、失敗表示なし** |
| D-07 | P2 | `queue-poll-overlap` | in-flightは最大1件、失敗は可視 | 3.5秒かかる`GET /api/queue`に対し**最大4件が同時in-flight**。503は`.catch(() => {})`で**不可視** |
| D-08 | P3 | `tests/test_ui1_legacy_sse.py` | `Last-Event-ID: 1`なら`id 2`から再開 | 旧`/api/jobs/{id}/events`は**常に`id 0`から再送**（実`create_app`で確認、strict xfail） |

### 原因（ソース行）

- **D-01** `loadEnvironment()`（`app.js:301-363`）が`state.environment = await api(...)`をrequest世代やAbortなしに代入。
  呼出し元は`#profile-select` change（`:1847`）、`#refresh-environment`（`:1844`）、`loadModels()`（`:1637`）、
  `wizardFinishSetup()`（`:951`）、起動時の非await呼出し（`:1970`）。起動時のdevice probeは最大約95秒
  （コメント`:1962-1969`）なので、起動直後のprofile変更でも同じ順序入替が起こり得る（未計測）。
  影響: 利用者が選んでいないprofileが選択状態になり、そのまま投入できる（POST時の`_validate_dynamic_selection`は
  **成立しない組合せ**は拒否するが、成立するが意図しないprofileは通す）。
- **D-02** wizardのstep handler群が`async`で、local catchがない: `wizardProfileNext`（`:722`）、
  `wizardDiarizationNext`（`:729`）、`wizardModelChoiceNext`（`:788`）、`wizardConfirmStart`（`:835`）、
  `wizardFinishSetup`（`:948`）、`openWizardFromSettings`（`:954`）、`#wizard-token-skip`（`:975`）、
  `#wizard-cancel`（`:983`）。例外は`unhandledrejection`→`report_frontend_error`（native log）へ行くだけで
  画面には出ない。pending表示も二重click防止もない。
- **D-03** `handleEvent()`の`done`（`:1025`）とpoller（`:1164-1169`）が両方`finishJob()`を呼ぶ。
  `finishJob()`（`:1046`）の入口ガードは`if (!state.job)`のみで、`state.job`は終了後も非null（`:1051`）。
- **D-04** `startJob()`はPOST受理直後に`#start-button`を再有効化（`:1149`）し、`state.job`/`state.source`/
  `state.poller`を単一slotで上書きする。先行ジョブの`finishJob()`は`await`後にjob IDを照合せず
  `state.job = result`、`state.source?.close()`、`clearInterval(state.poller)`（`:1051-1053`）を実行する。
- **D-05** `saveSettings()`（`:1817`）にcatchなし。同型: `#clear-token`（`:1900`）、キューcancel（`:1809`）、
  `openFolder()`（`:1621`、履歴行・結果画面から）、`#cancel-model-action`（`:1865`）。
- **D-06** `#cancel-button` handler（`:1870-1877`）がPOST前にdisabled化・文言表示し、失敗時の復帰がない。
- **D-07** `setInterval(() => loadQueue().catch(() => {}), 1000)`（`:1976`）。前回完了を待たない。
  `loadQueue()`（`:1784`）は`#start-button`の文言も毎秒上書きする（日英2値の直書き、`:1791-1794`）。
- **D-08** `api.py`の`stream_events()`／`stream_wizard_events()`はgenerator内で`cursor = 0`から開始し、
  `Last-Event-ID`を読まない。`EventSource`は切断時に自動再接続するため、再接続ごとに全イベントを再送する。
  画面への影響（ログの重複、`done`再受信による`finishJob()`再実行）は**コードからの推定**で、ブラウザ上の
  切断再接続は今回再現していない。opt-inのB9 `/api/v1/events`は`after=`/`Last-Event-ID`で再開する（別経路）。

### 再現コマンドと出力（抜粋）

```bash
node tools/ui1/legacy_gui_repro.mjs
```

```text
UI-1 legacy GUI repro — chromium 153.0.8010.12, node v22.23.4, linux
[REPRODUCED] env-stale-profile        actual: before release='cpu', after stale response: profile-summary='cuda', select='cuda'
[REPRODUCED] wizard-step-no-feedback  actual: step unchanged=true, dialog=false, message visible=false, unhandledrejection=1
[REPRODUCED] job-finish-twice         actual: log line appears 2 time(s); GET /api/jobs/job-a issued 3 time(s)
[REPRODUCED] stale-finish-orphans-next-job  actual: job B polls after A's stale completion: 0; result panel visible=true
[REPRODUCED] settings-save-no-feedback      actual: settings-status='', dialog=false, unhandledrejection=1
[REPRODUCED] cancel-failure-stuck     actual: cancel disabled=true, pending text='キャンセルを要求しました…', failure visible=false
[REPRODUCED] queue-poll-overlap       actual: max concurrent GET /api/queue=4; failure visible=false; total calls=6
```

```bash
uv run pytest tests/test_ui1_legacy_sse.py -rxX
```

```text
1 passed, 1 xfailed  (XFAIL ... UI-1 D-08: legacy job SSE ignores Last-Event-ID and replays from 0 on reconnect)
```

UI-2で修正した場合は`node tools/ui1/legacy_gui_repro.mjs --expect-fixed`（再現が残ればexit 1）と、
D-08のstrict xfail解除で回帰確認する。

## コードからの推定リスク（未再現）

| ID | 優先度 | 内容 | 根拠（`app.js`行） | 次の試験案 |
|---|---|---|---|---|
| R-01 | P1 | `api()`にtimeout・AbortSignal・応答相関がない。`GET /api/environment`は最大約95秒 | `:167-178`, `:1962-1969` | 応答を返さないmockで、pending表示・中止・再試行の有無を観測 |
| R-02 | P1 | `runModelAction()`は単一実行の保証がなく、2件目は同じprogress panelを共有。poll失敗でpanelを閉じるがbackend操作は継続、`state.modelJob`も残る | `:1719-1764` | 2モデル連続click、poll 500 1回のmock |
| R-03 | P2 | wizard pollerの`await api()`拒否が毎秒unhandled。backend停止時にpollerが止まらない | `:696-716` | wizard job GETを500固定 |
| R-04 | P2 | `loadHistory()`/`openViewer()`にstale guardなし（profile切替・連続open） | `:1178`, `:1256` | 2件の履歴を連続open、先行応答を遅延 |
| R-05 | P2 | `showDialog()`は別dialog表示中に即`false`。確認操作が無言で却下され「無反応」に見える | `:68` | エラーdialog表示中に削除clickを発火 |
| R-06 | P2 | キュー待機中のジョブも「処理しています」と経過時間・ETAを表示（`GuiJob.status`初期値`starting`） | `jobs.py:37`, `app.js:1147`, `:1004-1007` | 先行ジョブ実行中に2件目を投入 |
| R-07 | P2 | 全体進捗を`(stage index + ratio) / 5`で推測（工程の重みをbackendは確約しない） | `:1000-1003` | 仕様確認（backend契約に進捗重みがない） |
| R-08 | P3 | 再出力で形式未選択なら無言でreturn | `:1557` | 全形式を外してsubmit |
| R-09 | P2 | navigation click handlerがcatchなしで`loadModels()`/`loadQueue()`をawait | `:1836-1842` | env GETを500にしてモデル管理へ移動 |
| R-10 | P3 | `handleEvent()`の`JSON.parse`失敗がglobal errorになる | `:993` | 不正data行のSSE |
| R-11 | P2 | 起動時`boot()`失敗は`#environment-alert`の1行だけ。queue初回失敗でwizard判定も止まる | `:1954-1983` | `/api/queue`初回500 |

## 実機未確認

- Windows WebView2での`EventSource`再接続挙動、`report_frontend_error`ブリッジ到達、`pywebviewready`の順序。
- 実Explorer D&D（`app.py:_bind_input_drop` → `utteran-input-dropped`）、ネイティブfile dialog（`choose_path`）。
- DPI 125/150/200%、Windows表示言語、IME。
- 配布版（installer/portable）でのCSP・cookie・`/launch`経由のsession確立。

## PR #23との関係

[PR #23](https://github.com/zawa356/Utteran/pull/23)（Open/Draft、旧履歴基点`1097aef`）は`loadEnvironment`を
**関数抽出＋mock DOM**で順序入替させて巻き戻りを報告した。本書のD-01は**現行main（新履歴`068e62f`）の
実ファイルを実DOM・実CSP・実`<select>`操作**で再検証したもので、同じ結論（巻き戻り）を得た。
PR #23のコード・commitは取り込んでいない。差分の一覧は[README.md](README.md#pr-23との差分)。
