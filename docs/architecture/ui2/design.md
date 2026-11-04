# UI-2 設計 — backend境界・frontend層・asset packaging

更新: 2026-11-04 JST（0.1.41）。正本は実コード。ここは要点と理由の記録。

## 1. backendの変更（G-11 CSP nonce / G-12 新資産route＋flag）

変更は`src/utteran_gui/next_ui.py`（新規）、`api.py`の`create_app()`、`app.py`の起動flagに限る。

| 項目 | 既定（`enable_next_ui=False`） | opt-in（`enable_next_ui=True`） |
|---|---|---|
| `/`・`/assets/*` | 旧UI（不変） | 旧UI（不変。新UIから「旧画面を開く」で到達） |
| `/launch?session=` | cookie発行→`303 /` | cookie発行→`303 /next/`。cookie属性（HttpOnly・SameSite=Strict・path=/）不変 |
| `/next`・`/next/`・`/next/assets/*` | **routeなし（404）** | `/next`→`307 /next/`、`/next/`=index（nonce注入）、`/next/assets/*`=hashed asset |
| CSP | 旧policy（byte一致） | `/next/`のHTML応答だけ`style-src 'self' 'nonce-<毎要求>'`。他の応答は旧policy |
| `/api/*` session | 不変 | 不変（新UIも同じcookieで旧APIを使う） |
| `/api/v1/*` | なし | **なし**。新UIの有効化は`enable_v1_*`を一切有効にしない |

- **新UIの選択は明示flagのみ**: `utteran_gui.app.next_ui_requested(argv)`が`argv[1:] == ["--next-ui"]`の
  ときだけ真。設定ファイル・環境変数・自動migrationでは選ばれない（`gui.ps1`・installer・portableは引数なし＝旧UI）。
- **nonce**: `secrets.token_urlsafe(16)`（128 bit）。同じ値を応答本文の`<meta name="csp-nonce">`とCSP headerに入れる。
  Viteの`index.html`はplaceholder `__UTTERAN_CSP_NONCE__`を**ちょうど1個**持つ（起動時に検証）。置換後に
  placeholderが残れば配信しない。`script-src`は`'self'`のまま、`'unsafe-inline'`・`'unsafe-eval'`は使わない。
  Vuetifyのtheme `<style>`は`createVuetify({ theme: { cspNonce } })`で同じnonceを付ける。
- **静的HTMLを`FileResponse`で返さない理由**: 応答本文とheaderのnonceを一致させるため。index templateは
  起動時に1回読み、要求ごとに文字列置換して`HTMLResponse`で返す（`Cache-Control: no-store`）。
- **header**: `/next/`は`text/html`・`no-store`・`nosniff`。`/next/assets/*`（content-hash付きfile名）は
  `public, max-age=31536000, immutable`、404には付けない。`/api/*`の`no-store`は不変。
- **fail closed**: flag有効でbuildが無い・placeholderが0個/2個以上・UTF-8でない・読めない場合、`/next/`は
  `503 text/plain`（`next_ui_assets_missing`等の固定code、`no-store`）、`/launch`は`/next/`へ送ったまま。
  **旧UIへ黙ってfallbackしない**。errorはcodeだけlogに出す（pathは出さない）。
- **import境界**: `next_ui.py`は標準libraryのみ。flag有効でも`utteran_control`・`utteran_settings`・推論libraryを
  importしない（`tests/test_ui2_next_ui.py`の別process検査）。
- **host window**: `app.py`のpywebview起動・`NativeDialogApi`・D&D bindingは不変。D&Dの受け口
  `#input-drop-zone`は旧UIのDOMにしかないため、**新UIではnative D&Dはまだ届かない**（起動logに
  `Input drop target was not found`が出る）。入力画面を移すUI-3でtarget要素を用意するか、bindingを両UI対応にする。

## 2. frontendの層

```text
frontend/src/
  host/        HostBridge（pickPath / onDroppedPaths / reportError）。pywebview実装と明示注入のfake
  api/         http（timeout・abort・分類・再送なし）、legacy（/api/*の型）、v1（/api/v1/*の型）、idempotency、guards
  transport/   LatestOnly（latest-wins）、SingleFlightPoller、EventReducer（B9 port）、SyncCoordinator（/sync→SSE）
  domain/      Store（framework非依存）、Operation phase、EnvironmentController、SettingsController、ShellController、core
  composables/ useStore（Vue薄adapter）、useCore / useI18n
  components/  ErrorAlert（分類済みerror＋retry）、NotMigrated
  features/    shell（sidebar・mode）、workspace（通常）、expert（3ペイン構造）、environment、settings
  i18n/        ja / en（型でkey一致を強制）、code→文言（未知codeはkindでfallback）
  styles/      Vuetify theme（light/dark/system）、compact既定値、layout token、3ペインの縮退
  test/        fake fetch、合成DTO、B9 reducer共通fixture
```

ESLintで`features/`・`components/`からの`window.pywebview`・`window.fetch`参照と、`api/http.ts`以外の`fetch`直呼びを禁止。

### api — legacyとv1を混ぜない

| | legacy `/api/*`（UI-2で実使用） | v1 `/api/v1/*`（contract＋fakeのみ） |
|---|---|---|
| 認証 | session cookie（`/launch`で発行、HttpOnly） | 同じcookie＋Host/Origin検査（B5 middleware） |
| error | `{"detail": str}`／422はpydantic配列／401は平文 | `{"error":{code,message_key,request_id,retryable}}` |
| 冪等 | なし（PATCH settingsは同値なら冪等） | `Idempotency-Key`（`[A-Za-z0-9_.-]{16,128}`）、`If-Match: "<rev>"` |

- **自動再送しない**: http clientは1回だけ送る。`retryable`は「人が再試行してよいか」の表示用。
  POST/PATCH等のunsafe methodが応答なし（timeout・network）で終わると`outcomeUnknown=true`
  （「処理されたか不明」）とし、失敗と断定しない。
- v1の`MutationIntent`は意図ごとに1 keyを持ち、明示再試行で同じkeyを再利用する（B7 replay）。202の`JobBatch`は
  `accepted`でありterminalではない。
- timeout: 既定15秒、`GET /api/environment`は120秒（device probe最大約95秒、UI-1 R-01）、PATCH 30秒。環境読込は
  画面から中止できる（`AbortController`）。

### transport

- **LatestOnly**: 新しい読込が前のrequestをabortし、古い世代の応答（成功でも）は破棄して数える（D-01対策）。
- **SingleFlightPoller**: 完了後に次回をscheduleするので同時in-flightは常に1以下。失敗はstateに残りbackoff、
  `pause`/`resume`/`stop`は明示（D-07対策）。UI-2の画面はまだpollingを使わない（キュー移行時に使用）。
- **EventReducer**: `utteran_contracts.events.EventReducer`のport。`gapPolicy:"tolerate"`では全事例でPythonと同じ
  outcome（共通fixtureを両言語で再生）。追加: seq gapを必ず数え、**resource filterなしのstreamでは`"resync"`**
  （outboxのseqは書込transaction内の単一counterで採番され、filterなしの`read_events`は`seq > after`を全件返すため、
  欠番は取りこぼしを意味する）。filter付きstreamはB9どおりgapを正常とする。seqは`BigInt`で19桁まで正確に扱う。
- **SyncCoordinator**: 必ず`/sync`（全page、同じ`snapshot_token`）を先に読み、`/events?after=<cursor>`を購読。
  native `EventSource`の自動再接続は使わない（元URLの`?after=`と新しい`Last-Event-ID`が食い違い
  `400 cursor_conflict`になるため）。切断は新しい`EventSource`を`?after=<現在cursor>`で開き直す。`open`前の失敗
  （400/410/503はEventSourceから区別できない）・`resync_required`・別epoch・gap・解析不能frameは`/sync`へ戻る。
  再接続・再同期はjob/setupを開始・再試行しない。heartbeatのcommentはJSから見えないため、無通信監視は未実装。

### domain / view

- 操作単位の`Operation`（`idle/pending/accepted/success/error/cancelled`＋`ApiError`＋attempt）。
- 環境: 選択は同期的に記録し巻き戻さない。`readiness`は常に`"unverified"`（旧`options`はB4 ready証拠ではない、G-01）。
  frontendに互換表・ready判定は持たない。
- 設定: theme/languageのみ。変更fieldだけPATCH、pending中の二重送信は拒否、失敗時はdraftを保持し適用中のthemeは
  変えない、保存中の編集は保持。tokenは有無だけ表示（値はAPIも返さない）。他の設定項目は旧UIの案内のみ。
- shell: mode（通常/Expert）・section・Expertペインは**sessionのみ**（G-05/G-06方針で永続化しない）。stateはview外の
  storeにあり、mode切替でも消えない。各sectionに`partial`/`not_migrated`を明示。
- Expert 3ペイン: 23/45/32の構造、1280px未満で2列＋下段、1024px未満で1ペイン＋切替。入力・キューのペインは
  「未移行」の説明だけで、ボタンを置かない。
- i18n: ja/en。UI言語は**保存済み**の旧設定`language`に従う（未読込時はja）。`<html lang>`も更新。
- theme: Vuetify `light`/`dark`/`system`、`density: compact`、system font stack（外部font・CDNなし）、
  Alertの見出しを本文サイズに縮小。

## 3. asset packaging

| 経路 | 方式 | 状態 |
|---|---|---|
| Git | `src/utteran_gui/web-next/`は`.gitignore`。`frontend/**/*.ts`は`frontend/.gitignore`の`!*.ts`で追跡 | 確認済み（`git check-ignore -v`） |
| 開発checkout | `cd frontend && npm ci && npm run build`（`vite.config.ts`の`outDir`） | 確認済み |
| wheel | hatch `[tool.hatch.build.targets.wheel] artifacts = ["src/utteran_gui/web-next/**"]`（ignore対象を明示同梱） | CIで隔離venvにinstallして`/next/`配信を検査 |
| PyInstaller（installer/portable） | `packaging/gui.spec`が`web-next/index.html`があるときだけ`utteran_gui/web-next`として同梱 | **未検証**（Windows build・実機はUI-4）。`build.ps1`は変更していない＝build前に`npm run build`が必要 |
| buildが無い配布物 | 新UIは`503 next_ui_assets_missing`（fail closed）。旧UIは影響なし | test済み |

Node: `.nvmrc` = 22.23.4（`engines` `>=22.12 <23`）。npm: `packageManager` `npm@11.21.1`（npm 10.9.9はvitest 5の
optional peer解決で`Cannot read properties of null (reading 'edgesOut')`により`npm install`が落ちるため。
生成したlockfileはnpm 10の`npm ci`でも再現可能なことを確認）。
