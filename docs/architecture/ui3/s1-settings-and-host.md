# UI-3 S1 — 本番設定画面とOS連携（`/next/`、opt-in）

- **作成:** 2026-11-09 JST。対象 [Issue #21](https://github.com/zawa356/Utteran/issues/21)（親 [Epic #18](https://github.com/zawa356/Utteran/issues/18)）。
- **基点:** `main` `a1cb1b0aee4d8cb2c290581521559c1853211c26`（作業直前に`origin/main`と一致を確認。先行コミットなし）。
- **版:** 開発版 `0.1.44`（未リリース、tag/releaseなし）。根拠は§9。
- **状態:** Linux（headless Chromium＋実FastAPI）とWindows CI（frontend lint/型/Vitest/build、既存のWindows pytest/PowerShell）の自動試験まで。**Windows WebView2実機は未受入**で、最終の手動受入ゲートへ集約する（§8）。UI-3全体の完了ではない。
- **改訂:** 2026-11-09 PR #40レビュー（P1×2・P2×1）を反映（§4.1・§4.2・§11）。
- **正本の優先順位:** コード（`src/utteran_gui/api.py`・`settings.py`、`frontend/src/**`）→ 本書 → [design-freeze](design-freeze.md)（承認済みUI方針）。

## 1. 目的と範囲

UI-3-0で承認した設定画面のうち、**既存の旧API（v0）とhost bridgeで実現できる部分**を本番Vue/Vuetifyアプリ（`/next/`）へ移した。
既定GUIは旧画面のまま、`utteran-gui --next-ui`のときだけ表示される（`/api/v1`は有効化しない）。

| S1で実装 | S1で実装しない（後続） |
|---|---|
| テーマ・表示言語・既定プロファイル・既定入力フォルダ・既定出力フォルダの取得/編集/保存（`GET`/`PATCH /api/settings`） | ジョブ投入・キュー・結果・モデル管理（S3/S4） |
| OSネイティブのフォルダ選択（`HostBridge.pickPath("input_folder"/"output_folder")`） | 入力ファイルpicker・D&D投入（S3） |
| Hugging Faceトークンの状態表示・登録・削除（`GET`/`PUT`/`DELETE /api/token`） | 新UIのセットアップウィザード（S4）・B8接続（S5） |
| ログ保存先・代替保存先・生ログ設定の表示、ログフォルダを開く（`POST /api/open-folder`） | 生ログの本文表示・ダウンロード・診断zip、rawログ設定の変更 |
| セットアップ状態の読み取り表示（`GET /api/wizard/status`）と旧画面への安全な移動 | B4互換判定・B5 Preset/Policy・v1 activate・DB migration |

`frontend/src/preview/**`は変更・importしていない（ESLintの`no-restricted-imports`で引き続き禁止）。合成fixture・`runPlanKey`・固定のモデル可用性表は持ち込んでいない。

## 2. API・画面の対応表（実装前 → 実装後）

| 旧GUI（`web/index.html` `view-settings`） | API（既存、変更なし） | UI-2時点の`/next/` | S1の`/next/` |
|---|---|---|---|
| テーマ・表示言語 | `GET`/`PATCH /api/settings` | 移行済み | 維持（「表示」カード。保存確定後にのみ適用） |
| 既定プロファイル | `PATCH /api/settings` `default_profile: cpu/cuda/intel/vulkan/null` | 未移行 | 「既定値」カード。`null`＝「指定なし」 |
| 既定入力先・既定出力先（テキスト） | `PATCH /api/settings` `default_input_dir`/`default_output_dir`（≤4096、`""`可） | 未移行 | テキスト入力＋「選択…」（ネイティブ）＋「クリア」 |
| ― | `NativeDialogApi.choose_path`（`app.py`、FOLDER dialog） | bridgeのみ | 「選択…」から使用 |
| Hugging Face token（保存/削除） | `GET`/`PUT`/`DELETE /api/token` | 存在表示の文字列のみ | 状態5種＋登録＋確認付き削除 |
| ログ保存先・rawログ警告・「ログフォルダーを開く」 | `GET /api/settings`の`log_dir`/`log_dir_fallback`/`raw_subprocess_logs`、`POST /api/open-folder` | 未移行 | 「ログ」カード（読み取り＋開く） |
| ポータブル版の注記 | `portable_distribution`、`token_session_only` | 未移行 | 注記を表示（トークンは「このセッションのみ」） |
| 「ウィザードを開く」 | `GET /api/wizard/status`（`POST /api/wizard/start`は旧画面のみ） | 未移行 | 状態の読み取り＋「セットアップを開く（旧画面）」 |
| プライバシー注記（入力履歴は保存しない） | ― | ― | 変更なし（S1は入力ファイル名を扱わない） |

旧GUIとの差異: 既定プロファイルの選択肢は旧GUIが「構築済みprofileのみ」、新UIは**サーバー契約の4種＋指定なし**（構築状況の独自判定をfrontendに持たない。構築済みかは文字起こし画面の実行環境で確認する旨を表示）。旧GUIの1つの「設定を保存」は5項目＋表示をまとめて送るが、新UIは**変更した項目だけ**を`PATCH`する。トークン/ログ/セットアップは設定保存と別操作。

**新規API・backend変更: なし。** E2Eハーネス`tools/ui2/serve_next_ui.py`だけを変更（§7）。

## 3. 実装（再利用した部品）

| 層 | ファイル | 内容 |
|---|---|---|
| API | `frontend/src/api/legacy.ts` | `parseSettings`を5項目＋`token`/`portable`/`log`へ拡張（未知・wizardフィールドは無視）。`parseTokenStatus`（`backend`名・`error_message`は保持しない、`"session"`だけをsession-only判定に使う）、`parseWizardStatus`（3フラグ）。`settingsPatchBody`は変更項目だけをsnake_caseで、`null`は`null`のまま送る。`getToken`/`putToken`/`deleteToken`/`getWizardStatus`を追加。既存`openFolder`を流用 |
| domain | `domain/settings.ts` | 既存`SettingsController`を拡張: `saved`/`draft`分離、5項目の`diff`/`isDirty`、`rebase`（編集した項目は保持・未編集はサーバー値に追従）、二重PATCH防止、`reconcile()`（結果不明時のGET再照合）、`pickFolder()`（キャンセルno-op、host不在を区別）、4096文字超は送らない |
| domain | `domain/token.ts`（新） | `TokenController`。値は`set(value)`の引数だけ。状態は`loading/load_error/configured/missing/unavailable/unknown` |
| domain | `domain/logs.ts`・`domain/wizard.ts`（新） | `LogFolderController`（サーバーの`log_dir`だけを開く）、`WizardStatusController`（GETのみ） |
| domain | `domain/environment.ts`・`domain/core.ts` | `followsDefault`/`selectDefault()`を追加。起動時は保存済み既定profileで環境を表示（旧GUIのbootと同じ、`refresh=false`）。既定profileを保存したとき、環境パネルが既定に追従中の場合だけ`selectDefault`で切替（パネルで選んだprofileは維持）。再検出は暗黙に開始しない |
| host | `host/index.ts`・`pywebview.ts`・`fake.ts` | **変更なし**。`pickPath`の既存契約（キャンセル＝`null`、bridgeなし＝`HostUnavailableError`）を使用 |
| view | `features/settings/SettingsPage.vue`・`FolderField.vue`・`TokenCard.vue`・`LogCard.vue`・`SetupCard.vue`、`components/LegacyLink.vue` | Vuetify標準部品（`VCard`/`VSelect`/`VTextField`/`VBtn`/`VAlert`/`VDialog`）のみ。短いセクションカード |
| i18n | `i18n/ja.ts`・`en.ts` | 日英同数（キー一致はVitestで検査）。旧`settings.token*`3キーを削除 |
| style | `styles/app.css` | `.u-card`/`.u-grid`/`.u-folder`/`.u-path`（長いWindowsパスを折り返す） |

## 4. 状態遷移

### 4.1 設定（5項目）

```
load: idle → pending → success | error(再試行)
draft編集 → dirty ─保存→ save: pending ─200→ success（saved=応答、draft=rebase）
                              ├─4xx/5xx→ error（draft保持、再試行は明示操作のみ）
                              └─timeout/network→ error(outcomeUnknown)、unconfirmedSave={送信したPATCHの凍結コピー}
                                   └─「最新の状態を確認」(GET) → reconciled={matched:[…], mismatched:[…]}（送信した項目ごと）
picking中・save/reconcile中・unconfirmedSaveがある間は保存不可（未確認の保存を新しい保存で上書き・隠蔽しない）。
revertはdraftをsavedへ戻すが、unconfirmedSaveは消さない。GET失敗時はunconfirmedSaveを維持。
```

- 再照合は**送信したPATCHの項目と値だけ**をGET結果と比べる（現在のdraft全体のdirty判定とは別）。`matched`は「サーバーの現在値が送信値と一致」という観測で、そのPATCHが原因とは断定しない（別画面が同じ値を保存した可能性）。`null`の既定プロファイル・空のフォルダも値として比較する。
- 結果不明の後に編集した項目は`rebase`で保持され、照合結果（「送信した変更は一致」）と「未保存の変更があります」が同時に表示され得る。一部だけ一致した場合は一致しない項目名を示し、draftに送信値が残るので明示的に保存し直せる。

- テーマ・言語の画面適用は`saved`（サーバー確定値）だけが駆動する。保存失敗・結果不明では見た目が変わらない。
- 保存中に追加入力した項目、遅いGETの間に入力した項目は保持（`rebase`）。旧GUIで別項目が変わっていた場合、未編集の項目は**古い値を再送しない**。

### 4.2 トークン

状態を2つに分ける（PR #40レビューP1-1）。`GET /api/token`は**存在の有無だけ**を返すので、直前のPUTで値が置き換わったか、`configured=false`が直前のDELETEによるものかは証明できない。

**(a) 現在の状態（状態行）** — 最後にサーバーから読んだ存在状態:

| 表示 | 条件 |
|---|---|
| 確認しています… | 初回GET中 |
| 登録済み（値は表示しません）／このセッションで使用中 | `configured && available`（session-onlyなら後者） |
| 未設定 | `!configured && available` |
| OSの資格情報保存機能を利用できません | `available=false`（**未設定と混同しない**、未確認の操作より優先） |
| 現在の状態は未確認です | 結果未確認の操作の後、GETが成功するまで（`statusCurrent=false`）。GETが失敗しても維持 |

**(b) 直前の操作が未確認（注記、`unconfirmed={kind: set|clear, reason}`）**:

- `reason=no_response`: timeout/network（要求が届いたか不明）。
- `reason=store_error`: 5xx（例: 503）。`TokenStore.set()`は書き込み後に読み戻しを行うため、**書き込み成功→読み戻し失敗でも503**になる（P1-2）。よって503で「変更されていません」とは表示しない。
- GET成功後も注記は残り、「上の表示は現在保存されているかどうかだけ。入力した値に置き換わったかは確認できない（必要なら登録し直す）」／「直前の削除によるものかは確認できない」と案内する。消えるのは、次のPUT/DELETEが2xxで確認されたときか、GET後に利用者が「閉じる」を押したときだけ。
- 422/401等の明確な拒否は通常のエラー表示（未確認扱いにしない）。
- 「登録しました」「削除しました」は、そのPUT/DELETE自身の2xx応答のときだけ表示し、後続のGETで消える（再照合で完了メッセージを出さない）。GET成功時は前回の操作エラーも消し、状態行と矛盾する表示を残さない。
- 古いGET応答はPUT/DELETEの応答を上書きしない。操作中の2回目の登録・削除は拒否。自動再送なし。

**入力欄:** 送信を開始した時点で（成否に関係なく）password欄を空にする。再送は常に利用者の再入力による。入力前の検証（空白のみ・4096超）で送らない場合は消さない。値はコンポーネントのローカルrefと`putToken`の引数以外に複製しない。サーバーの`detail`・`error_message`・backend名は表示しない。削除は確認ダイアログ必須（Escで取消、要求なし）。

### 4.3 フォルダ選択

`選択…` → `pickPath(kind)` → 文字列ならdraftだけ更新／`null`（キャンセル）は変更なし／`HostUnavailableError`は「Windowsデスクトップ版のUtteranが必要」と案内（ボタンは有効のまま、パスの直接入力も可）／その他の失敗は「画面を開けませんでした」。pywebviewが無いブラウザでは`createPywebviewHost`の既存の待機（最大5秒）の後に案内が出る。

## 5. 設計判断

1. **PATCHのみ・変更分のみ。** `PUT /api/settings`（全置換、wizard進捗を既定値で上書きし得る）は使わない。`SettingsStore.update()`のmergeにより`setup_wizard_*`と未知フィールドは保持される（E2Eで`/api/wizard/status`の`started_at`/`step`不変を確認）。
2. **`default_profile=null`は値。** TSの`undefined`（＝送らない）と区別し、JSON `null`として送る。Vuetifyの`VSelect`は`null`をitem値にできないため画面内だけ`"__none__"`へ写像。
3. **選んだフォルダ＝実行時の権限保証ではない。** 既存APIは存在検証をしないため、保存時に「存在や書き込み権限は確認しません」と表示。検証はS3のジョブ投入契約に委ねる。
4. **トークン値はフォームのローカルrefだけ。** `SettingsController`・`TokenController`・URL・`localStorage`/`sessionStorage`・console・`HostBridge.reportError`・`ApiError`へ入らない（Vitest/E2Eで検査）。422のpydantic詳細（入力値を含み得る）は既存`parseErrorBody`が`request_invalid`へ畳む。クライアントでも空白のみ・4096超を送らない。
5. **ログは状態とフォルダを開くだけ。** パスは`GET /api/settings`の`log_dir`をそのまま`POST /api/open-folder`へ渡す（UIで組み立てない）。rawログ設定はcore config（`config.toml`）由来のため変更UIを置かない。
6. **ウィザードはワンクリック開始にしない。** 新UIから`POST /api/wizard/start`を呼ぶと、完了済みでも`started_at`の再設定・工程リセットという副作用があり、旧画面に遷移する前に失敗した場合に利用者が取り残される。安全性の証明はS4の課題とし、S1は**読み取り表示＋同一セッションの`/`へ移動**＋「旧画面の［設定］→［ウィザードを開く］」の案内に留めた。初回未完了なら旧画面は起動時に自動でウィザードを表示する（旧`boot()`の既存挙動）。
7. **離脱確認。** 設定画面の「セットアップを開く（旧画面）」とapp barの「旧画面を開く」は、未保存のdraftがあると「保存して移動／保存せずに移動／キャンセル」を出す。保存失敗時は移動しない。新UI内のナビ・通常/Expert切替・窓幅変更ではdraftもAPI処理状態もcore（Vue外）に残る。ウィンドウ自体を閉じる場合の確認は実装していない（pywebviewの終了処理と合わせてUI-4で検討）。
8. **既定プロファイルと環境表示は別操作。** 保存成功は「保存しました」、環境の再取得は文字起こし画面の環境パネル側の読み込み/エラー表示で示す。

## 6. 失敗時の復旧

| 事象 | 表示 | 復旧 |
|---|---|---|
| 設定GET失敗 | 読み込みエラー＋再試行 | 再試行（GET） |
| 設定PATCH 4xx/5xx（例: 503） | 「保存できませんでした。変更内容は残っています。」 | 再試行（明示）／元に戻す |
| 設定PATCH 無応答 | 「保存されたか確認できません」＋送信した項目名 | 「最新の状態を確認」→ 送信項目ごとの一致/不一致。照合までは保存不可 |
| トークンPUT/DELETE 503・5xx | 「資格情報ストアのエラーが返されました。保存（削除）されたかどうかは確認できません」 | 「状態を確認」で現在の存在状態だけ確定。必要なら再入力して登録 |
| トークンPUT/DELETE 無応答 | 「応答がなかったため結果を確認できていません」 | 同上。GET失敗中は「現在の状態は未確認」を維持 |
| ログフォルダ 400/503 | 「フォルダが見つかりません」／「ファイルマネージャーを起動できません」 | もう一度押す |
| pickerのhost不在 | 「Windowsデスクトップ版のUtteranが必要」 | パスを直接入力 |
| wizard status失敗 | 読み込みエラー＋再試行 | 旧画面への移動は可能 |

## 7. 試験

**Vitest（`npm test`）: 31ファイル・272件成功**（UI-2/S1本番分128、プロトタイプ144は不変。初版255件にPR #40レビュー分17件を追加、§11）。S1で追加・変更した主な試験:

- `api/legacy.test.ts`（新）: `parseSettings`の正常・欠損・未知フィールド・`default_profile=null`・空フォルダ・`token_store_available=false`・portable/session-only・fallback/raw、token/wizardのparse、PATCH本文（null/空文字）、`PUT /api/settings`を使わないこと。
- `domain/settings.test.ts`: 5項目のdiff・部分PATCH・theme/language非退行・二重送信1回・保存中の追加入力保持・失敗時draft不変・revert・旧GUIでの別項目変更を再送しない・遅いGETが保存結果を上書きしない・結果不明→GET再照合（applied/not_applied）・4096超を送らない、fake hostでのpicker（種類・draftのみ・キャンセルno-op・host不在・選択中の保存/二重picker拒否）。
- `domain/token.test.ts`（新）: GET/PUT/DELETE、unavailable≠missing、503、timeout→unknown→再読込、操作中の追加操作拒否、空白/超過は送らない、portable、**timeout時もstore/error/host reportに値が無い**。
- `domain/logs.test.ts`（新）: サーバー`log_dir`だけを開く、400/503、wizardはGETのみ（POST・`/api/v1`なし）、起動時の既定profile表示と保存後の追従/非追従。
- `host/host.test.ts`: `input_folder`/`output_folder`の転送、dialog失敗はキャンセル扱いしない。

**Playwright E2E（実FastAPI、`npm run e2e`）: 23件成功**（既存`next-ui.spec.ts` 10件＋`settings-s1.spec.ts` 13件。レビュー修正でトークン2件・設定1件を改訂/追加）。既存10件はトークン表示のtestid変更（`settings-token`→`token-status`）のみ調整。

- 実`GET/PATCH /api/settings`でprofile/フォルダ保存→再読込→旧GUI（`/`）の`#default-output`等と一致、PATCHは変更分だけ（`null`/`""`の消去を含む）、wizard進捗不変、`PUT /api/settings`なし。
- 実`GET/PUT/DELETE /api/token`（ハーネスの合成in-memory keyring、実`TokenStore`経由）で登録/確認付き削除。DOM・console・storage・URL・`/api/settings`応答に合成値が出ないこと。keyring失敗（合成値に拒否マーカー→実`TokenStoreUnavailable`→503）、無応答（`route.fetch`後に切断）→unknown→再確認、`available=false`、portable session-only。
- picker: 実`createPywebviewHost`に台本付き`window.pywebview.api`を注入して種類・キャンセル・revertを検証。bridgeの無いページで案内が出てボタンが無反応にならないこと。**OSのダイアログは開かない。**
- 設定PATCHの無応答（未適用・適用済みの2種）→GET再照合、PATCH 503（既存）、ログの表示・raw/fallback警告・open 204/503/400（要求本文がサーバー値と一致）、`log_dir`なしで無効化。
- セットアップ: 状態表示、未保存時の離脱確認（キャンセル/保存して移動/保存せずに移動）、`/next/`からの非GETは明示保存の`PATCH /api/settings`だけ（`/api/wizard/start`・B8・`/api/v1`なし）、`/api/v1`は404。
- 通常/Expert往復・ナビ移動・900×660でdraftとpicker結果を保持、横スクロールなし。英語表示、Tabでpickerへ到達、Escでダイアログ取消。全テストでCSP違反0・JS例外0（fixtureに`pageerror`検査を追加）・外部origin要求0。

**プロトタイプE2E（`npm run e2e:prototype`）: 96件成功（不変）。** `npm run lint`・`typecheck`・`build`・`audit:prod`（0件）成功。

**Python:** `pytest -m "not requires_model"` 1292 passed / 9 skipped / 1 xfailed、ruff check/format、mypy（linux/win32）、8 schema `--check`、`uv lock --check`、PowerShell BOM、`public_history_scan --worktree`（blocking 0）。backend/hostのPythonコードは無変更。

**E2Eハーネスの変更（`tools/ui2/serve_next_ui.py`、製品からimportされない）:** read-onlyだった`SyntheticKeyring`を**プロセス内のin-memory**に変更（拒否マーカー付きの値だけ失敗）、ログ保存先を一時ディレクトリに固定（実`config.toml`やユーザーのログ先を読まない）、旧画面の`/logo`用にリポジトリのアイコンを一時ディレクトリへ複製。各テストはfixtureでAPI経由で設定5項目とトークンをリセットする（設定ファイルは起動ごとの一時ディレクトリ、実keyring・実ユーザー設定は使わない）。

## 8. Windows WebView2 実機受入（未実施、最終ゲートへ集約）

**受入の運用（owner方針、2026-11-09〜継続適用）:**

- **各フェーズ／修正PR（S1〜S5）:** Linux自動試験＋**Windows CI自動試験**（`UI-3 frontend Windows`＝lint・vue-tsc・Vitest・build、既存の`Windows pytest (no models)`・`Windows PowerShell 5.1 parse and headless startup`）の全必須ジョブ成功を確認してmerge可否を判断する。各PRでownerに実機確認を求めない。
- **最終統合（UI-3/UI-4の最終ゲート）:** WebView2の実起動、OSネイティブpicker、Explorer、DPI 125/150/200%、IME、installer/portableの**手動実機確認をまとめて行う**。それまで既定GUIの切替・正式リリースはしない。
- 自動CIの成功を実機受入完了とは書かない。S1の実機未確認項目は下記のとおり記録し、最終ゲートで消化する。
- Windows CIジョブは合成試験のみ（fake host/fetch）で、実トークン・OSキーチェーン・ユーザープロファイル・OSダイアログに触れない。新ジョブを必須チェックにするにはリポジトリ管理者がbranch protectionのrequired checksへ`UI-3 frontend Windows (npm ci, lint, types, unit, build)`を追加する必要がある（本作業では保護ルールを変更していない）。

この作業環境（Linuxコンテナ）ではWindows WebView2・OSダイアログ・IME・OS DPIを試験できない。**以下はすべて未受入（最終実機受入待ち）**。UI-4 #22の全画面受入と合わせて実施する。

1. `uv sync --extra gui`、`cd frontend && npm ci && npm run build`、`uv run utteran-gui --next-ui`で起動（引数なしの起動が旧GUIのままであることも確認）。
2. 設定 → 「既定の入力フォルダ」の「選択…」でOSのフォルダ選択が開くこと。1回選択、出力側も1回選択、もう一度開いてキャンセル（値が変わらないこと）。
3. 既定プロファイルとフォルダを保存 → アプリを終了・再起動 → 値が保持されていること。旧GUI（`--next-ui`なし）の設定画面でも同じ値が表示されること。テーマ/言語の保存と表示が壊れていないこと。
4. 合成トークン（本物のHF tokenは不要。例: `hf_`で始まる任意の文字列）を登録→「登録済み」、削除→確認→「未設定」。値が再表示されず、送信後に入力欄が空になること。keyringの503（書き込み後の読み戻し失敗を含む）は自動試験で代替済みだが実機は未確認。
5. ログ保存先の表示と「ログフォルダを開く」でExplorerが開くこと。rawログを有効にした場合の警告（`config.toml`の`[general] raw_subprocess_logs = true`）。
6. 「セットアップを開く（旧画面）」で旧画面へ移動し、［設定］→［ウィザードを開く］が使えること。未保存の変更がある場合の確認ダイアログ。
7. DevToolsでCSP違反0。DPI 125/150/200%とIME（日本語のフォルダ名を直接入力）をS1の入力欄で最低限確認。

## 9. 版

`docs/リリース手順.md`の「作業単位ごとにパッチ版を1つ上げる」とAGENTS.mdに従い、`0.1.43`→`0.1.44`（`pyproject.toml`・`uv.lock`のutteran行・README日英・`変更履歴.md`）。`utteran.__version__`はpackage metadata由来で`tests/test_version.py`が一致を検査。`uv lock`をローカルの旧いuvで実行すると`revision`行が変わったため、その行は元に戻し**version行だけ**の差分にした（`uv lock --check`成功）。プロトタイプ内の`0.1.43`（`frontend/src/preview/domain/about.ts`等）は合成表示データで、preview不変の指示に従い変更していない。tag・releaseは作らない。

## 10. 後続へ残したもの

- **S2:** 環境stripの全項目、既定プロファイル選択肢へのB4由来の構築/ready状態の表示（frontend独自の互換表は作らない）。
- **S3:** 入力ファイルpicker・新UI自身のnative D&D受け口・ジョブ投入、出力先の実パス検証（存在・権限・衝突）、自由入力フォームのIME composition E2E（UI-2からの引継ぎ）。
- **S4:** 新UIのセットアップウィザード（ワンクリック開始を入れるなら、明示確認→wizard/queue状態照会→`POST /api/wizard/start`の副作用検証→遷移の安全性を試験で実証）、Expertの設定カテゴリ（ログ・高度・プリセット）。
- **S5:** B8 setup・v1 settings（B5）への接続。
- **UI-4:** 窓を閉じる際の未保存確認、Windows実機・DPI・IME・installer/portableの受入。

## 11. PR #40レビュー修正（2026-11-09）

レビュー [#5820580886](https://github.com/zawa356/Utteran/pull/40#pullrequestreview-5820580886) の3件。Python製品コード・API契約・host・CLI・DB・B4〜B9・`frontend/src/preview/**`は無変更。版は`0.1.44`のまま（同じ未リリース作業単位のレビュー修正。UI-3-0の各イテレーションと同じ扱い）。

| 指摘 | 原因 | 修正 | 観測できること／断定しないこと |
|---|---|---|---|
| P1-1 トークンの結果不明とGET再確認の混同 | 結果不明後のGET成功で`outcomeUnknown`だけ消え`action.error`が残り、「登録済み」と旧エラーが同時表示。入力欄は`set()`が`true`のときだけ消去 | `TokenController`に`statusCurrent`と`unconfirmed{kind,reason}`を分離。GET成功は現在の存在状態だけを更新し、前回操作の表示（成功・エラー）を消す。未確認の記録はGETでは消さない。`TokenCard`は送信開始時に入力欄を空にする | 観測: 現在の存在／利用不可。断定しない: PUTによる置換、DELETEによる未設定 |
| P1-2 503で「変更されていません」と断定 | `TokenStore.set()`は書き込み後の読み戻し失敗でも`TokenStoreUnavailable`→503 | 5xxを`store_error`の未確認として扱い、文言を「保存（削除）されたかどうかは確認できません」に変更（日英）。固定文言のまま例外本文は出さない。E2Eハーネスに「書き込み成功・読み戻しだけ失敗」の合成keyringを追加し、実APIの503で検証 | 観測: 503が返った事実。断定しない: 保存の有無 |
| P2 設定の再照合がdraft全体で判定 | `reconcile()`が`isDirty`（現在のdraft全体）で判定し、後続編集があると適用済みでも「保存されていませんでした」 | 保存時に送信PATCHを凍結して`unconfirmedSave`に保持し、GET後に送信項目ごとに`matched`/`mismatched`を返す。照合前の新しい保存は拒否、revertでも記録は消さない。後続編集は`rebase`で保持し「未保存の変更があります」を別に表示 | 観測: サーバーの現在値と送信値の一致。断定しない: 一致がそのPATCHによること |

**回帰試験（先行コードで失敗→修正後に成功を確認）:** `domain/token.review.test.ts` 11件（修正前は9件失敗。残る2件＝古いGETとPUTの競合・GET失敗時のunknown維持は修正前から成立）、`domain/settings.review.test.ts` 6件（修正前は6件すべて失敗）。既存`token.test.ts`の「503で状態不変」は誤った期待だったため「未確認」へ改訂、`settings.test.ts`の照合試験を新しい戻り値へ改訂。E2E: 503（書き込み前の拒否／書き込み後の読み戻し失敗）、応答喪失（未設定→PUT適用、登録済み→別値PUT未適用、DELETE適用、再確認GETの失敗）、設定PATCH喪失後の後続編集と送信項目ごとの照合。skip/xfailの追加なし。

**CI:** `.github/workflows/ci.yml`にjob `ui3-frontend-windows`（`UI-3 frontend Windows (npm ci, lint, types, unit, build)`、`windows-latest`、`frontend/.nvmrc`、npm 11.21.1固定、既存と同じactionのSHA pin）を追加。既存のWindows pytest・PowerShellジョブは変更なし。
