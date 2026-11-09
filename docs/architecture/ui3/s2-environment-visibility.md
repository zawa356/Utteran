# UI-3 S2 — 実行環境の可視化（既存`GET /api/environment`、`/next/`、opt-in）

- **作成:** 2026-11-09 JST。対象 [Issue #21](https://github.com/zawa356/Utteran/issues/21)（親 [Epic #18](https://github.com/zawa356/Utteran/issues/18)）。
- **採用方針:** **A — 既存`GET /api/environment`の観測結果を表示する。** B4 readinessの公開API・`/api/v1`接続・ready判定は実装しない（2026-11-09 owner判断）。
- **基点:** `main` `f3ad48b3e22aded301995c0c08508ab784982f70`（PR #40 / S1のマージコミット。作業直前に`origin/main`と一致、作業ツリーは無変更）。
- **版:** 開発版 `0.1.45`（未リリース、tag/releaseなし）。§10。
- **状態:** Linux（Vitest＋headless Chromium＋実FastAPI）の自動試験まで。WindowsはCI（frontend lint/型/Vitest/build、Windows pytest、PowerShell）で判断し、**Windows WebView2実機の手動確認は最終統合ゲートへ集約**（§9）。UI-3全体の完了ではない。
- **正本の優先順位:** コード（`src/utteran_gui/environment.py`・`api.py`、`frontend/src/**`）→ 本書 → [design-freeze](design-freeze.md)。

## 1. 目的と範囲

`--next-ui`の本番Vue/Vuetify画面に、実サーバーが返す**環境の観測情報**を表示し、プロファイル選択・再検出・長い待ち時間・失敗からの復帰を完成させた。S1の既定プロファイル設定と矛盾させない。

| S2で実装 | S2で実装しない（厳守） |
|---|---|
| 旧GUI環境stripの4項目（ハードウェア／使用プロファイル／導入済みモデル数／ネイティブ構成）、未取得・不明・なしの区別 | B4 Capability InventoryのHTTP公開、`/api/v1`接続、recipe candidateの`ready`判定、推奨順位、smoke test、互換マトリクス、推論の試行 |
| Expertの詳細（profile存在/互換性、デバイス、ASR・話者分離の検出候補、導入済みモデル、Native/OpenVINO、注意・失敗） | モデルのdownload/verify/delete、profile構築/修復、セットアップ実行、ジョブ投入・キュー・履歴、B5〜B9 |
| profile選択（既定に従う／手動）、再検出、待機の中止、再試行、時間切れの明示、前回の結果の明示 | `frontend/src/preview/**`のimport、固定の推奨GPU/モデル表、静的な「この構成は動く」判定 |
| 型を確認した最小DTO、パス・生エラー文の非保持 | Python製品コード・API・DB・worker・旧GUI/CLIの変更、既定GUI切替 |

## 2. ソースの意味（G-01は未解決）

`GET /api/environment`は`EnvironmentService.snapshot()`がプロファイルCLIから集めた**その時点の観測値**で、B4の`InventorySnapshot`ではない。`options.asr`があっても、B4が求める実測smoke・identity・freshnessを満たした`ready`の証明ではない。

**G-01（B4公開read-only API／ready証拠）の扱い:** 以前の設計（[design-freeze §4](design-freeze.md#4-次段階ui-3-21はオープン)の「S2 環境・モデル: B4正本の候補/ready理由」）ではS2にB4 readinessまで含めていたが、owner判断で**S2は旧APIの観測表示に限定し、G-01は未解決の後続課題**とした。readyの判定は真実の条件を満たしたbackendだけが行う。UI全体の設計思想（frontendに互換表を持たない、readyを捏造しない）は変更していない。

## 3. API projection（`frontend/src/api/legacyEnvironment.ts`、`legacy.ts`から再export）

| 実レスポンス | DTO | 扱い |
|---|---|---|
| `profiles[]` `name`/`exists`/`compatible`/`compatibility_reason` | `profiles[]` `name`/`exists`/`compatible: true\|false\|null`/`compatibilityReason`（既知6種＋`other`） | **必須**（形が違えば全体`malformed`）。`path`・`updated_at`は捨てる |
| `active_profile` | `activeProfile` | **必須**。文字列か`null`。構築済みprofile以外を名乗る値は`malformed`（「稼働中」と誤表示しない） |
| `options.asr`/`options.diarization` | `asr`/`diarization`: `id`・`label`・`modelCount`（候補内）・`models`・`devices` | リスト自体は**必須**。壊れた要素だけ落とし`issues`に`malformed/options`を記録（全画面は落とさない） |
| `errors[]` | `issues[]` `{kind: timeout\|failed\|malformed, probe, seconds}` | **必須**。**文字列は保持しない**（`Profile Python is not available: <path>`・stderr等を含み得る）。`CliAdapter`の`CLI timed out after Ns: <args>`から既知の6種のCLI呼出しだけを特定 |
| `devices`（`cpu`・`ctranslate2.cuda_devices`・`pytorch.cuda_devices`/`xpu_devices`・`openvino.values`・`native`・`probes`・`probe_cache_hit`） | `devices`（`null`＝未取得） | 任意。型が違う値は`null`/空に。GPUは`runtime`別に`usable`をそのプローブの観測として保持。probeの`detail`・`error`、`cuda_libraries`・`ffmpeg.path`は捨てる |
| `models[]` | `installedModels`（`installed===true`の行だけ）・`installedModelCount` | 任意。`active_profile=null`ならサーバーは問い合わせないので`null`（未取得）。`path`・`execution_label`・`recommendation_reason`は捨てる |
| `native`（`native status`） | `nativeRunnable`（`runnable===true`のvariant名） | 任意。`manifest`（パスを含み得る）は捨てる |
| `openvino_models[]` | `openvinoIr: {installed,total}` | 任意。**導入済みモデル数に加算しない**。`xml_path`は捨てる |
| `options.guidance` | `guidance: native_not_built\|ggml_model_missing\|other` | 既知の意味だけ日英文言へ。「auto選択に従い既定にしました」は旧フォームの既定の説明なので表示しない |
| `profile_warnings` | `profileWarningCount` | 文言は`profiles`の`compatible=false`＋理由から日英で再構成（サーバー文は日本語固定のため）。件数差は「その他の注意」 |
| `model_path`、`options.defaults`、`options.languages`/`formats` | 保持しない（`languageCount`のみ） | S2では不要。パス・推奨確定に見える値を持たない |

**文字列の扱い:** 表示する文字列は型を確認し、制御文字・bidi制御を除去して最大120文字、リストは最大32件。識別子（variant・probe名・device id）は`^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$`のみ。描画はVueのテキストバインディングだけ（`v-html`なし）。HTML風の名前（合成fixtureの`<b>XPU</b>`）は文字として表示されることをE2Eで確認。

**fixture:** `frontend/src/test/fixtures/environment-snapshots.json`は、**実`EnvironmentService.snapshot()`**を合成プロファイルCLI（`tools/ui2/synthetic_environment.py`）の上で実行した出力（`--write`で生成）。`tests/test_ui2_synthetic_environment.py`が生成結果との一致を検査するため、旧API契約が変わるとLinux/Windows pytestで失敗し、frontendのparser試験の再確認を強制する。パスは固定の相対placeholder（`synthetic-root/...`、POSIX表記でOS非依存）。

## 4. 画面（Vuetify標準部品）

| 部品 | 内容 |
|---|---|
| `features/environment/EnvironmentPanel.vue` | profile選択・再検出・待機の中止・状態（読み込み中／中止／失敗／時間切れ／前回の結果）・4項目・選択の注記。`detail`属性でExpert表示 |
| `EnvironmentSummary.vue` | 4項目（`VCard`内の`dl`グリッド、狭幅で折返し） |
| `EnvironmentDetails.vue` | `VExpansionPanels`で5カテゴリ（プロファイル／デバイス／検出候補（未検証）／導入済みモデル・ネイティブ構成／注意・メッセージ）。読み取り専用 |
| `EnvironmentNotices.vue` | 失敗（固定文言）・要再構築・案内 |
| `domain/environmentSummary.ts` | 4項目・profile状態・要求とサーバー選択の関係・注意件数（Vue非依存、Vitest対象） |

### 4.1 旧GUI環境strip → 新UI

| 旧（`web/app.js` `loadEnvironment()`） | 新UI（通常の4項目） | 違い |
|---|---|---|
| ハードウェア: `ctranslate2.cuda_devices`＋`pytorch.xpu_devices`の`usable`名、なければ`CPU · N threads` | 同じ規則＋`pytorch.cuda_devices`も対象、名前の重複を除く（同じGPUを2つのruntimeが報告するため）。最大2件＋「ほかN件」。`devices`なし＝**未取得** | 旧GUIは`devices`なしでも`CPU · — threads`と表示していた |
| プロファイル: `active_profile`または`—` | サーバーの`active_profile`。`null`＝**なし**、未取得＝**未取得**。要求profileとは別に注記 | |
| 導入済みモデル: `models.filter(installed).length` | 同じ。`active_profile=null`（問い合わせなし）は**未取得**、0件は「0件」 | 旧GUIは問い合わせていなくても0と表示 |
| ネイティブ構成: `devices.native.variants`の`true`のみ、なければ`—` | 同じ。`devices`なし＝**未取得**、`native`欄なし＝**不明**、該当なし＝**なし** | |
| `environment-alert`（profileなし／候補なし／errors原文／profile_warnings原文／guidance原文のいずれか1つ） | 注記（§4.3）＋部分失敗の要約＋Expert/詳細の注意一覧（固定文言） | 生の`errors`文字列は表示しない |
| `form-status`の「ready」（候補が1件以上のとき） | **表示しない**。常に「検出情報・実行可否は未検証」 | G-01 |

### 4.2 通常とExpert

- **通常（文字起こし画面）:** 4項目、未検証ラベル、サーバーが使用中のprofile、注記・部分失敗・注意件数、折りたたんだ「詳細を表示」。
- **Expert（②処理設定ペイン）:** 同じパネルに詳細カテゴリを展開。profile行は「構築」「互換性」を別欄にし、「サーバーが使用」「この画面で選択」「設定の既定」をチップで区別。待機をやめた要求の件数（§5）。候補は一覧のみで、入力・選択・実行の部品を置かない（S3/S4）。
- どちらも単一の`AppCore.environment`のsnapshotを読む。**モード切替・ナビ移動で要求は増えない**（E2Eで要求数を検査）。

### 4.3 3つのprofileと状態

`SettingsController.saved.defaultProfile`（設定の既定）・`EnvironmentController.requestedProfile`（この画面の要求、`null`＝profile引数なし）・`data.activeProfile`（サーバーが使用）を別々に保持する。UI-2では要求`null`の応答でサーバーの選択を`requestedProfile`へ書き戻していたが、S2で廃止した（既存Vitest 1件の期待値を改訂）。

| 注記（`selectionNote`、現在の選択に対する結果のときだけ） | 条件 |
|---|---|
| fallback「選んだ「x」は使用できないため、サーバーは「y」を使用」 | 要求あり・`active`が別。サーバーは構築済みが1つだけのとき黙ってそれを使う（`snapshot()`の規則） |
| requested_unavailable | 要求あり・`active=null` |
| server_choice | 要求なし・`active`あり（構築済みが1つ） |
| choose「どれも選びませんでした。上の一覧から選んで」 | 要求なし・構築済みが複数で`active=null` |
| none_usable「使用できるプロファイルがありません」 | 構築済みなし |

`environmentView`: `idle`/`pending`/`success`/`partial`（応答はあるが`issues`あり）/`empty`（`active=null`。エラーではない）/`error`/`cancelled`。`partial`でも取得できた項目は表示し、何が取れなかったかを固定文言で示す。

**前回の結果:** `dataFor`（その応答を得た要求と受信時刻）を保持し、`isCurrentResult`が偽（読み込み中・失敗・中止、または要求が変わった）なら「前回の結果」チップと「「x」について hh:mm:ss に取得した前回の結果」を表示し、薄く描画する。このとき「サーバーが使用中」・注記・部分失敗の要約は出さない。

## 5. 非同期処理・時間制約・中止

- **起動:** `App.vue`は先にmountし`core.start()`（設定→保存済み既定profileの環境）を開始する現設計のまま。環境取得中も設定・ナビ・通常⇔Expertは操作可能（E2E）。
- **進捗:** APIは段階や割合を返さないため捏造しない。不定のprogress bar、「初回やドライバー変更後は1〜2分以上かかることがある」、待機の上限、**この画面の時計による経過秒（進み具合ではないと明記）**を表示。
- **再検出:** `refresh=true`は「デバイスを再検出」だけ。起動・設定保存の追従・profile選択・画面移動・モード切替は`refresh=false`（Vitest/E2Eで要求URLを検査）。
- **競合:** `LatestOnly`で古い応答を捨て、最後の選択が勝つ（UI-1 D-01）。**同じ要求（profile・refresh・既定追従）が進行中なら新しい要求を出さない**（連打・ダブルクリックで1回、E2E）。再検出ボタンは読み込み中は無効。
- **中止:** 「待機を中止」はfetchをabortし表示の更新を止める。**サーバー側のCLI子プロセスは止まらない**（FastAPIの同期ハンドラはクライアント切断後も完了まで走る）ので、文言は「表示の更新を中止しました。サーバー側の検出処理は止まらず、終わるまで続くことがあります」。中止後の遅い成功・失敗は表示も選択も上書きしない。「もう一度確認」は明示操作のみ。
- **重複プローブの抑止の範囲（backend無変更の制約下）:** 同一要求の重複送信なし、再検出の多重起動なし、自動再試行なし、時間切れの誤判定による再試行を減らす（次項）。**別のprofileへ切り替えた／中止した要求のサーバー処理は続く**ため、待機をやめた件数を`abandoned`として数え、Expertに表示する。サーバー側で同一要求を1本にまとめる（single-flight）か中止可能にするのはbackendの変更で、S2の範囲外（後続のbackend課題として提案）。
- **時間切れ:** `ENVIRONMENT_TIMEOUT_MS`を**120秒→240秒**に変更。根拠: `snapshot()`は最大6回のCLI呼出しを順に行う（`profiles list`、`devices --json`＝自前の上限`_DEVICES_PROBE_TIMEOUT_SECONDS`=200秒、models・native・model path・OpenVINO一覧）。実測は未キャッシュ初回94.7秒（7プローブ中4つが時間切れ）で、20秒×7プローブの時間切れだけでも120秒を超える。120秒では**サーバーがまだ正常に返し得る間に時間切れと表示し、再試行が2本目のプローブを起動**していた。240秒＝devicesの上限＋他の呼出しに40秒。全呼出しがそれぞれ60秒上限に達する理論上の最悪（約500秒）は待たない（利用者はいつでも中止できる）。時間切れは「240秒以内に応答がありませんでした。サーバー側の検出は続いている可能性があります。少し待ってから再試行してください」と明示。Python本体・probeの終了条件は変更していない。
- **機密:** 遅延・失敗時もパス・profile固有の詳細・トークンをconsole/host error reportへ流さない（エラーは分類済み`ApiError`のみ、`errors`原文はDTOに入らない）。

## 6. S1との整合

- 既定profileの保存は**S1の`PATCH /api/settings`だけ**。S2のprofile選択・再検出・既定に従うはPATCHを出さない（Vitest/E2E）。
- 「既定に従う（x）」は**保存済み**の既定値を読む（未保存のdraftではない）。既定に従っている間だけ、S1の保存成功後に表示対象が追従する。手動選択中は設定保存で巻き戻らない（読み込み中の保存を含む）。
- 構築されていない既定profile（例: vulkan）でも設定値は書き換えず、サーバーの扱い（requested_unavailable／fallback）と「設定の既定値は変更していません」を表示する。
- 環境取得が失敗しても設定画面は操作・保存可能。トークン・ログ・wizardとは独立。
- 実APIは旧`/api/environment`とS1の`/api/settings`だけ。`/api/v1`・`CapabilityInventoryAdapter.refresh()`・workerを起動するAPIは使わない（E2Eで非GET・`/api/v1`要求0件を検査）。`frontend/src/preview/**`はimportしない（ESLintで禁止済み）。

## 7. 試験

**Vitest（`npm test`）: 34ファイル・334件成功**（本番190、プロトタイプ144は不変。S1時点272件に62件追加）。

| ファイル | 内容 |
|---|---|
| `api/legacyEnvironment.test.ts`（新、29件） | 実service fixture 5種（profile引数なし／cpu／cuda／intel／vulkan未構築）の投影、パス・manifest・サーバーの可否文・生エラーを保持しない、必須項目の欠落8種で`malformed`、任意項目の破損は不明値、壊れた候補だけ除外、文字列・件数の上限と制御文字除去、パス・トークン風の値をissueに残さない、`classifyIssue`12種 |
| `domain/environment.test.ts`（11件、既存3件を含む） | D-01、late success/failureと中止、中止後の明示再試行、同一要求の重複抑止、前回の結果と`dataFor`、既定に従う／手動の区別、`empty`/`partial`、**fake clockで120秒時点は待機中・240秒で時間切れ**（実時間を待たない）、中止はfetchをabortするだけ |
| `domain/environmentSummary.test.ts`（新、17件） | 4項目の未取得/なし/不明/値、同一GPUの重複除去、usableでないGPUを出さない、OpenVINO IRを加算しない、profile状態4種、注記5種、注意件数、**日英の`env.*`文言に「ready/推奨/動作します」等がない**、最も揃った応答でも未検証 |
| `domain/environment.core.test.ts`（新、7件） | 起動順と`refresh=false`、手動選択・再検出でPATCHなし、未構築の既定を書き換えない、既定に従うは保存済み値、読み込み中の設定保存で巻き戻らない、既定追従は1回、環境失敗中も設定保存可 |
| `i18n/i18n.test.ts` | `env.active`の英文変更に合わせ期待値を改訂（日英キー一致は従来どおり） |

**Playwright E2E（実FastAPI、`npm run e2e`）: 31件成功**（既存UI-2 10件＋S1 13件＋**S2 8件**）。ハーネスは**実`EnvironmentService`**＋合成CLI（§3）。

1. 通常の4項目が応答と一致（未取得→cpu→cuda）、詳細を開くと読み取り専用の候補。
2. Expertの5カテゴリ（profileの構築/互換性/役割、XPU・OpenVINO・probe時間切れ、導入済みモデル、OpenVINO IR、native、部分失敗の固定文言、要再構築）、HTML風の名前は文字、候補に入力部品なし、DOMに`synthetic-root`・`CLI exited`・ready/推奨なし、モード切替・ナビで要求数不変、非GET・`/api/v1`なし。
3. `refresh=true`は再検出だけ、ダブルクリックで1回、書き込みなし。
4. 遅いCUDA→速いCPU→遅れたCUDAでもCPU維持、中止後に遅い応答が来ても「前回の結果」のまま、「もう一度確認」で復帰。
5. 失敗（`timedout`）中に設定の保存・モード切替が可能、再試行中もモード切替可、復帰。
6. 構築済みなし（応答の局所置換）＝状態として表示、未構築profileは選択不可。
7. 未構築の既定（vulkan）→requested_unavailable、設定値は不変、手動選択は既定の再保存で維持、「既定に従う」で保存済み値へ。
8. 900×660・英語・長いデバイス名で横スクロールなし、Tabでprofile選択へ到達。

既存E2Eの改訂: D-01の初期条件（UI-2の合成ハーネスは既定なしでもcpuを返していたが、実serviceの規則では「どれも選ばない」）と未検証チップの文言の2点のみ。skip/xfailの追加・試験の削除なし。

**プロトタイプE2E: 96件成功（不変）。** `npm run lint`・`typecheck`・`build`・`audit:prod`（0件）成功。

**Python:** `pytest -m "not requires_model"` 1294 passed / 9 skipped / 1 xfailed、ruff check/format、mypy（linux/win32）、8 schema `--check`、`uv lock --check`、PowerShell BOM、`public_history_scan --worktree`（blocking 0）すべて成功。CI結果はPRに記録する。追加は`tests/test_ui2_synthetic_environment.py`（2件）のみ。Python製品コードは無変更。

## 8. 失敗時の復旧

| 事象 | 表示 | 復旧 |
|---|---|---|
| HTTP失敗（500等） | 読み込みエラー＋（あれば）前回の結果 | 再試行（明示） |
| 240秒で無応答 | 時間切れ＋「サーバー側は続いている可能性」 | 少し待って再試行 |
| 中止 | 「表示の更新を中止…サーバー側は止まらず」＋前回の結果 | 「もう一度確認」 |
| 応答はあるが一部の呼出しが失敗 | 部分成功（固定文言、件数）＋取得できた項目 | 再検出／旧画面で詳細 |
| 応答の形が不正（必須項目） | `malformed`のエラー | 再試行 |
| 構築済みprofileなし／選ばれない | 注記（状態でありエラーではない） | 旧画面のセットアップ／一覧から選ぶ |

## 9. Windows（自動CIで判断、手動実機は最終ゲート）

S1の方針を継続する。各PRはLinux自動試験＋Windows CI（`UI-3 frontend Windows (npm ci, lint, types, unit, build)`・`Windows pytest (no models)`・`Windows PowerShell 5.1 parse and headless startup`）の全必須ジョブ成功でmerge可否を判断し、ownerに実機確認を求めない。自動CIの成功を実機受入とは書かない。

**最終統合ゲートへ残すS2の手動確認（未実施）:** WebView2で`--next-ui`起動→実機の`GET /api/environment`（実CUDA/XPU/OpenVINO・未キャッシュ初回の待ち時間と経過表示、時間切れに至らないこと）、再検出中の中止と旧GUIとの4項目の一致、DPI 125/150/200%と長いデバイス名の折返し、日本語表示。実GPUでの`usable`はそのプローブの観測で、実行可能性の受入ではない。

## 10. 版

`docs/リリース手順.md`とAGENTS.mdの「作業単位ごとにパッチ版を1つ上げる」に従い`0.1.44`→`0.1.45`（`pyproject.toml`・`uv.lock`のutteran行だけ・README日英・`変更履歴.md`）。`uv lock --check`成功。`frontend/src/preview/`の合成版数は変更しない。tag・releaseなし。

## 11. 後続へ残したもの

- **G-01（未解決）:** B4の公開read-only API（inventory・ready理由・freshness）とready表示。設計・backend実装・契約試験の後、S2の「未検証」表示をbackendの判定へ置き換える。
- **backend課題（提案、S2では未起票）:** `/api/environment`の同一要求single-flightまたは中止可能化（frontendのabortではCLI子プロセスが止まらない）、全呼出しの合計上限（理論上約500秒）。
- **S3:** 検出候補を処理設定・ジョブ投入へ配線（候補≠ready、投入時の検証はbackend）、入力picker/D&D、IME。
- **S4:** モデル管理・セットアップ（要再構築・GGML未取得の案内から新UIの操作へ）。
- **S5:** v1接続（B4〜B9）。
- **UI-4:** Windows実機・DPI・IME・installer/portableの受入（§9の項目を含む）。
