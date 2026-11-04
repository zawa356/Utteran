# UI-2 → UI-3 移行計画・backend gap・rollback・owner判断

更新: 2026-11-04 JST（0.1.41）。段階の定義は[UI-1 frontend-architecture §4](../ui1/frontend-architecture.md#4-段階移行)。

## 1. 段階の現状

| 段階 | 内容 | UI-2の状態 |
|---|---|---|
| S0 | 雛形・host bridge・API client・domain store・i18n・theme・Vitest/Playwright・CI・配信route・CSP nonce・opt-in flag | **完了**（本PR） |
| S1 設定 | 旧`/api/settings` `/api/token` | **一部**: theme/languageのみ。既定profile・入出力フォルダ・token設定/削除・log表示・wizard再起動は未移行 |
| S2 環境 | 旧`/api/environment` | **一部**: 読み取り表示・profile選択・再検出・中止・retry。旧UIの環境stripの全項目（native/OpenVINO model等）は未移行 |
| S3 文字起こし・キュー | 旧`/api/jobs*` `/api/queue` | 未着手（`SingleFlightPoller`は用意済み） |
| S4 モデル・履歴・旧wizard | 旧API | 未着手 |
| S5 v1接続 | `/api/v1`（opt-in） | 未着手（client・reducer・coordinatorはcontract＋fake testのみ） |
| S6 既定切替 | — | 未着手。UI-4のWindows実機受入とowner承認が前提 |

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

## 3. 旧画面 → 新UI 対応（UI-2時点）

| 旧（`index.html`） | 新UI | 状態 |
|---|---|---|
| `view-settings` theme・language | 設定 | 移行済み（同じ`gui-settings.json`を読み書き＝相互互換） |
| `view-settings` 既定profile・既定入出力・token・ログ・wizard再起動 | 設定 | 未移行（画面で旧UIを案内） |
| `view-workspace` 環境strip・再検出 | 文字起こし（通常）／Expert中央 | 読み取り表示のみ移行。readyとは表示しない |
| `view-workspace` フォーム・投入・`progress-panel`・`result-panel` | 文字起こし | 未移行 |
| `view-queue` | キュー／Expert右 | 未移行 |
| `view-models` / `view-history` / `view-viewer` / `view-wizard` | モデル／結果と履歴／セットアップ | 未移行 |
| D&D・file dialog・`report_frontend_error` | `host/` | bridge実装済み。file dialog・D&Dを使う画面は未移行（D&D受け口は上記3.） |
| 共通dialog | Vuetify Dialog/Alert | Alert（領域内error＋retry）のみ使用。確認dialogはUI-3 |

## 4. backend gap（UI-1 G番号、UI-2での扱い）

| ID | UI-2での扱い | 次 |
|---|---|---|
| G-01 公開能力・ready証拠 | 旧`options`を「検出結果・未検証」として表示。readyと表示しない | backend Issue（公開read-only API） |
| G-02 frontendの互換表 | 移植していない | backendへ移す |
| G-03 progress | v1にprogressなし。架空の%を出さない | 契約設計 |
| G-04 SSE実ブラウザ受入 | `SyncCoordinator`はfake EventSourceでのみ検証 | WebView2受入（UI-4） |
| G-05/G-06 override・入力リストの保存 | 永続化しない（sessionのみ） | **owner判断**（UI-3設計時） |
| G-09 旧/新queueの併存 | 新UIはv1を有効化しない | S5前に設計 |
| G-10 旧SSE再接続 | 新UIは旧SSEを使っていない | S3で方針決定 |
| G-11/G-12 | **実装済み** | — |
| G-13 code→文言 | ja/en辞書＋未知codeのkind fallback | v1 code網羅表をS5で |
| G-15 private DB retention/ACL | 新UIはDBに触れない | **owner判断** |

UI-2で見つけた追加の論点:

- **B9 `EventReducer.apply_sync`は別epochのsyncで`queue_revision`をresetしない**（store counterのため）。backup復元等で
  queue revisionが下がると（例: 旧epoch rev=100 → 復元後rev=5）、以後のqueue event（rev 6等）が`stale`になる。
  frontend portはparityのため同じ挙動。PR #36レビューにより、**S5（v1実接続）前のblocking課題**として、
  B9本体の契約・testと同時に修正し、TS/Python parityを維持する（UI-2には混ぜない）。
- `EventSource`はstatus codeを見せないため、`open`前の失敗（400/410/503）を区別できない。現在は一律`/sync`へ戻る。
  heartbeat comment（`: keepalive`）もJSから見えないので、無通信の検出は未実装。WebView2での挙動確認が要る。

## 5. rollback

- 新UIは`--next-ui`を付けたときだけ表示される。**flagを外せば旧UI**（設定・DB・backendのデータ形式は不変）。
  新UI画面からも「旧画面を開く」で同じsessionのまま`/`へ戻れる。
- 新UIが書くのは旧UIと同じ`gui-settings.json`のtheme/languageだけで、旧UIはそのまま読める。
- 不具合時は本PRのrevertで、`create_app`の既定・旧CSP・旧`/launch`は元から不変。

## 6. owner判断が必要な事項

1. G-05/G-06: ファイル別overrideと入力リストを保存するか、保存先と継承規則。
2. G-15: private DBのretention・ACL・完全削除。
3. 旧UIのP1欠陥（D-01 profile表示巻戻り、D-04 後続ジョブ追跡停止）の短期hotfixを**別PR**で出すか
   （本PRには含めていない。既定GUIの変更になるため承認後のみ）。
4. Windows WebView2での実機受入をいつ・どのVM/実機で行うか（新UIの既定化と配布受入の必須ゲート）。
5. installer/portableへ新UIを同梱する時期（`build.ps1`に`npm ci && npm run build`を組み込むか）。現在は
   build済みのときだけ`gui.spec`が同梱し、`build.ps1`は変更していない。
6. B9 reducerの`queue_revision`のepoch跨ぎ（上記4.）。
