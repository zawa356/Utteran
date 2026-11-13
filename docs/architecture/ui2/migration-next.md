# UI-2 → UI-3 移行計画・backend gap・rollback・owner判断

更新: 2026-11-12 JST（0.1.51、UI-3 S5-A v1単一ジョブ）。初版2026-11-04（0.1.42）。

> **現在の計画（2026-11-11 owner方針、以下の本文より優先）:** 旧UIは旧API、**新UIは`/api/v1`を正規の接続先とする**。
> 進行順は **Issue #37修正（0.1.48で解消）→ v1起動/旧・新queue排他（G-09）の設計・実装 → B4 ready/smoke → 新UIのv1投入接続 → v1上のS3-B2**。
> 本文の「S3-B2は旧API、S5で初めてv1へ接続」は当時の計画の記録であり、今後の指示ではない。
>
> **G-09の実装状況（0.1.49、未リリース）:** v1起動基盤だけを実装した（[ui3/g09](../ui3/g09-next-ui-v1-startup.md)）。起動モードは3つ:
> 引数なし＝旧UI＋旧API（完全互換）、`--next-ui`＝Vue新UI＋旧API（従来どおり、v1は有効にしない）、`--next-ui-v1`＝**暫定・開発専用**のVue新UI＋B5/B7/B8/B9 v1
> （開発用markerのある専用data rootでのみ起動、同一rootのGUI二重起動は全モードで拒否）。`--next-ui-v1`では現行S3-A/B1の旧投入は**409**（fallbackなし）で、
> 実ジョブのUI受入モードではない。暫定フラグは新UIのv1投入接続が完成し`--next-ui`（または既定切替S6）へ統合された時点で廃止する。
> 残るゲート: **B4 ready/smoke → 新UIのv1投入接続（409の解消） → v1上のS3-B2** → 最終統合ゲート（Windows WebView2実機）。
>
> **B4 ready/smokeの実装状況（0.1.50、未リリース）:** 開発専用の明示smoke入口とprivate evidence、refresh/resolve・B7 prestartでの読取専用統合を実装した
> （[b4/smoke](../b4/smoke.md)）。GUI・HTTP APIにsmokeの操作は無い。Cloudでは合成試験のみで、**Windows実機でのfw-cpu受入（[手順](../b4/smoke-local-acceptance.md)、未実施）が次の前提**。
> fw-cudaは本版ではready不可（identity/VRAM未観測）。次は Windows受入 → 新UIのv1投入接続。v1のactivate・既定切替・実ユーザーDBのmigrationはowner承認後のみ。段階の定義は[UI-1 frontend-architecture §4](../ui1/frontend-architecture.md#4-段階移行)。

> **S5-Aの実装状況（0.1.51、未リリース）:** `--next-ui-v1`に限り、新UIの単一入力をB5/B7へ接続した（[ui3/s5a](../ui3/s5a-v1-single-job.md)）。
> 保存済みPreset/Policy（revision凍結・明示作成）→ B5 validation → B7 resolution → durable job → `GET /api/v1/jobs/{id}`。v1では旧`POST /api/jobs`を呼ばず、
> 複数ファイルの一括投入は無効表示。`--next-ui`は従来どおり旧API。残り: **S5-B（原子的batch）→ v1上のS3-B2** → 最終統合ゲート（Windows WebView2実機）。

## 1. 段階の現状

| 段階 | 内容 | UI-2の状態 |
|---|---|---|
| S0 | 雛形・host bridge・API client・domain store・i18n・theme・Vitest/Playwright・CI・配信route・CSP nonce・opt-in flag | **完了**（本PR） |
| S1 設定 | 旧`/api/settings` `/api/token` | **実装（0.1.44、Windows実機未受入）**: theme/language・既定profile・既定入出力フォルダ（native picker）・token状態/登録/削除・log表示/フォルダを開く・wizard状態表示と旧画面への移動。新UIのwizardはS4（[ui3/s1](../ui3/s1-settings-and-host.md)） |
| S2 環境 | 旧`/api/environment` | **実装（0.1.45、Windows実機未受入）**: 旧環境stripの4項目・Expert詳細（profile存在/互換性・デバイス・検出候補・導入済みモデル・native/OpenVINO・注意）・profile選択（既定に従う/手動）・再検出・待機の中止・retry・前回の結果。readyとは表示しない（G-01は後続）（[ui3/s2](../ui3/s2-environment-visibility.md)） |
| S3 文字起こし・キュー | 旧`/api/jobs*` `/api/queue` | **S3-A実装（0.1.46、Windows実機未受入）**: 1ファイル（picker・native D&D・手入力）、現在の環境結果からASR構成、話者分離オフで`POST /api/jobs`1回（自動再送なし・結果不明の表示）、`GET /api/jobs/{id}`を正本にSSE速報（event idで重複排除）＋`SingleFlightPoller`、取消は要求→GETで確定（[ui3/s3a](../ui3/s3a-legacy-transcription.md)）。**S3-B1実装（0.1.47、Windows実機未受入）**: 入力一覧（複数D&D・picker追加・手入力の追加）、ファイル別override・出力先、旧`POST /api/jobs`を1件ずつ順に投入（全件preflight・凍結・単件と共通の送信ゲート、拒否/結果不明で停止）（[ui3/s3b1](../ui3/s3b1-multi-input-and-admission.md)）。**未着手**: S3-B2（複数ジョブの監視・取消・絞込み）、フォルダ・同一入力の再投入・出力命名（S5）・話者分離 |
| S4 モデル・履歴・旧wizard | 旧API | 未着手 |
| S5 v1接続 | `/api/v1`（opt-in） | **S5-A実装（0.1.51、`--next-ui-v1`のみ、Windows実機未受入）**: 単一入力のPreset/Policy選択・明示作成、validation→resolution→job、GET polling、queue明示再開、協調取消（[ui3/s5a](../ui3/s5a-v1-single-job.md)）。S5-B（原子的batch・ファイル別override）は未着手 |
| S6 既定切替 | — | 未着手。UI-4のWindows実機受入とowner承認が前提。**DPI 200%での初期窓の画面収容**（0.1.42で既定1180×820 DIPがはみ出すことを実測）を解消すること |

## 2. UI-3で移す順序（提案）

1. **S1残り**: 既定profile・既定入出力フォルダ（host `pickPath`）・token設定/削除（値は表示しない、503 keyring不可の表示）・
   log状態。旧UI D-05（保存失敗の無表示）の再発防止を同じ`SettingsController`方式で担保。
2. **S2残り**: 環境stripの全項目、`profile_warnings`の導線、起動時の長いprobeの進捗表示。
3. **S3**: 通常モードの入力→構成→開始→状態→結果、Expertの入力一覧/Inspector/投入。旧`/api/jobs`は1 jobずつ、
   キューは`SingleFlightPoller`で`/api/queue`。D-03/D-04/D-06/D-07をE2Eで再発防止（UI-1ハーネス相当）。
   旧SSE（`/api/jobs/{id}/events`）は再接続時に全再送するため（D-08）、使うならevent idで重複排除するか、
   pollingのみにする。**native D&Dの受け口**（`app.py`の`#input-drop-zone`）を新UIに用意する。
4. **S4**: モデル管理（download/verify/delete・取消）、結果・履歴・viewer、旧wizard。
5. **S5**: v1（Preset/Policy、Job/Queue CAS、Setup B8、`/sync`＋`/events`）。activateは一方向migration（v1→v6）を伴うため
   owner判断後のみ。旧queueとの排他（G-09）を画面で明示。

各段階で[UI-1 ux-structure §8](../ui1/ux-structure.md#8-旧画面--新画面-対応表機能欠落の管理)の旧→新対応表を更新し、
機能欠落が無いことを確認してから`SECTION_STATUS`を変える。

### UI-2から引き継ぐ受入条件（0.1.42 Windows受入より）

- **UI-3 #21**: 最初の自由入力フォームでIME compositionをE2E化する（開始/更新/確定/取消、未確定入力が実行・送信を誘発しない、
  確定文字列が重複しない）。新UI自身のnative D&D受け口とfile dialogを実装し、Windows実機で受入する。
- **UI-4 #22**: 既定切替の前に、DPI 200%・1920×1200でも初期窓が画面内に収まること（適応的な初期サイズ）。

## 3. 旧画面 → 新UI 対応（UI-2時点）

| 旧（`index.html`） | 新UI | 状態 |
|---|---|---|
| `view-settings` theme・language | 設定 | 移行済み（同じ`gui-settings.json`を読み書き＝相互互換） |
| `view-settings` 既定profile・既定入出力・token・ログ | 設定 | **移行済み（UI-3 S1、0.1.44）**。変更分だけ`PATCH`、tokenは値を保持しない。詳細は[ui3/s1 §2](../ui3/s1-settings-and-host.md#2-api画面の対応表実装前--実装後) |
| `view-settings` ウィザードを開く | 設定 → 旧画面 | 状態の読み取りと旧画面への移動のみ（新UIのwizardはS4） |
| `view-workspace` 環境strip・再検出 | 文字起こし（通常）／Expert中央 | **移行済み（UI-3 S2、0.1.45）**: 4項目＋詳細。旧`form-status`の「ready」は移さない。詳細は[ui3/s2 §4.1](../ui3/s2-environment-visibility.md#41-旧gui環境strip--新ui) |
| `view-workspace` フォーム・投入・`progress-panel`・`result-panel` | 文字起こし | **一部移行（UI-3 S3-A 0.1.46・S3-B1 0.1.47）**: 1ファイル・ASR・言語・形式・出力先・投入・工程表示・取消・終端の案内と出力ファイル名、複数ファイルの順次投入とファイル別設定（受付結果のみ、複数ジョブの進行表示は旧画面/S3-B2）。フォルダ/batch glob・話者分離・話者数・resume選択・結果viewer/履歴は旧画面（S3-B/S4） |
| `view-queue` | キュー／Expert右 | 未移行（S3-B）。S3-AのExpert右は現在の1件だけ。結果不明時の`GET /api/queue`参考表示のみ |
| `view-models` / `view-history` / `view-viewer` / `view-wizard` | モデル／結果と履歴／セットアップ | 未移行 |
| D&D・file dialog・`report_frontend_error` | `host/` | bridge実装済み。**フォルダ選択は設定画面で使用（S1）**。**入力ファイルdialog・native D&D（`frontend/index.html`の静的`#input-drop-zone`）はS3-Aで使用** |
| 共通dialog | Vuetify Dialog/Alert | Alert（領域内error＋retry）と確認Dialog（token削除・未保存での離脱、S1） |

## 4. backend gap（UI-1 G番号、UI-2での扱い）

| ID | UI-2での扱い | 次 |
|---|---|---|
| G-01 公開能力・ready証拠 | 旧`options`を「検出結果・未検証」として表示。readyと表示しない。**UI-3 S2もowner判断（A案）で同じ扱いに限定**（[ui3/s2 §2](../ui3/s2-environment-visibility.md#2-ソースの意味g-01は未解決)） | backend Issue（公開read-only API）。未解決 |
| G-02 frontendの互換表 | 移植していない | backendへ移す |
| G-03 progress | v1にprogressなし。架空の%を出さない | 契約設計 |
| G-04 SSE実ブラウザ受入 | `SyncCoordinator`はfake EventSourceでのみ検証 | WebView2受入（UI-4） |
| G-05/G-06 override・入力リストの保存 | 永続化しない（sessionのみ） | **owner判断**（UI-3設計時） |
| G-09 旧/新queueの併存 | `--next-ui`はv1を有効化しない。**0.1.49:** 開発専用`--next-ui-v1`・開発用root・同一rootのGUIプロセス排他・停止/復旧境界を実装（[ui3/g09](../ui3/g09-next-ui-v1-startup.md)） | 起動基盤は解消。CLI・旧バイナリとの排他、新UIのv1投入接続は後続 |
| G-10 旧SSE再接続 | 新UIは旧SSEを使っていない | S3で方針決定 |
| G-11/G-12 | **実装済み** | — |
| G-13 code→文言 | ja/en辞書＋未知codeのkind fallback | v1 code網羅表をS5で |
| G-15 private DB retention/ACL | 新UIはDBに触れない | **owner判断** |

UI-2で見つけた追加の論点:

- **【0.1.48で解消、Issue #37】** 別epochの`/sync`でqueue revisionも再初期化するようPython正本・TS portを修正し、共通fixtureで一致を確認
  （[b9/sync.md](../b9/sync.md#epochとrevisionの有効範囲issue-370148)）。以下は当時の記録:
  **B9 `EventReducer.apply_sync`は別epochのsyncで`queue_revision`をresetしない**（store counterのため）。backup復元等で
  queue revisionが下がると（例: 旧epoch rev=100 → 復元後rev=5）、以後のqueue event（rev 6等）が`stale`になる。
  frontend portはparityのため同じ挙動。PR #36レビューにより、**S5（v1実接続）前のblocking課題**として、
  B9本体の契約・testと同時に修正し、TS/Python parityを維持する（UI-2には混ぜない）。
- `EventSource`はstatus codeを見せないため、`open`前の失敗（400/410/503）を区別できない。現在は一律`/sync`へ戻る。
  heartbeat comment（`: keepalive`）もJSから見えないので、無通信の検出は未実装。WebView2での挙動確認が要る。

## 5. rollback

- 新UIは`--next-ui`を付けたときだけ表示される。**flagを外せば旧UI**（設定・DB・backendのデータ形式は不変）。
  新UI画面からも「旧画面を開く」で同じsessionのまま`/`へ戻れる。
- 新UIが書くのは旧UIと同じ`gui-settings.json`のtheme/language・既定profile・既定入出力フォルダ（UI-3 S1から）で、
  変更分だけの`PATCH`のためwizard進捗等の他フィールドは保持され、旧UIはそのまま読める。tokenは旧UIと同じOS keyringの同じslot。
- 不具合時は本PRのrevertで、`create_app`の既定・旧CSP・旧`/launch`は元から不変。

## 6. owner判断が必要な事項

1. G-05/G-06: ファイル別overrideと入力リストを保存するか、保存先と継承規則。
2. G-15: private DBのretention・ACL・完全削除。
3. 旧UIのP1欠陥（D-01 profile表示巻戻り、D-04 後続ジョブ追跡停止）の短期hotfixを**別PR**で出すか
   （本PRには含めていない。既定GUIの変更になるため承認後のみ）。
4. Windows WebView2: UI-2の範囲は0.1.42で条件付き受入済み（[§7](tests-and-acceptance.md#7-windows実機受入0142head-862870deccbc2fc9cd1041b96d8c6ea64800fbfe)）。
   既定化（UI-4 #22）では200%での初期窓の画面収容、installer/portable、起動時間・RAMの受入が残る。
5. installer/portableへ新UIを同梱する時期（`build.ps1`に`npm ci && npm run build`を組み込むか）。現在は
   build済みのときだけ`gui.spec`が同梱し、`build.ps1`は変更していない。
6. ~~B9 reducerの`queue_revision`のepoch跨ぎ（上記4.）。~~ 0.1.48で解消（Issue #37）。
