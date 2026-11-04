# UI-2 試験・受入記録

更新: 2026-11-04 JST（0.1.42、HEAD `862870d`）。§1〜§5.1は**0.1.41時点のLinux（headless Chromium）実測**（当時の記録として残す）。
**Windows WebView2実機の受入結果は§7**（0.1.42、条件付き受入）。
CI（GitHub Actions）の結果はDraft PRと Issue [#20](https://github.com/zawa356/Utteran/issues/20)のコメントに
run URLを正本として記録する（この文書へ追記するcommitでCIを再起動し続けないため）。

## 1. 環境

| 項目 | 値 |
|---|---|
| 基点 | `main` = `0e07e138aae742244ca387468de62701874a31c9`（`git ls-remote`で確認） |
| OS | Linux（cloud container） |
| Python / uv | CPython 3.11（`uv sync --extra dev --extra gui --locked`）。CIは3.12 |
| Node / npm | Node 22.23.4 / npm 11.21.1 |
| browser | Playwright 1.63.0 同梱 Chromium（headless） |
| データ | すべて合成（`tools/ui2/serve_next_ui.py`の合成環境、一時dirの設定file、読み取り専用の合成keyring）。実音声・実モデル・GPU・実パス・tokenなし |

## 2. 実測結果（ローカル）

| gate | コマンド | 結果 |
|---|---|---|
| lock | `uv lock --check` | ok（lock差分はutteranのversion行のみ） |
| lint/format | `ruff check src tests tools` / `ruff format --check src tests tools` | ok / 212 files formatted |
| 型 | `mypy` / `mypy --platform win32` | 0 issues（121 files）/ 0 issues |
| pytest | `pytest -m "not requires_model"` | 下記§2.1 |
| schema | b1/b3/b4/b5/b6/b7/b8/b9 `--check` | 8/8 ok |
| BOM | `tools/check_powershell_bom.py` | ok（9 files、`.ps1`の追加・変更なし） |
| privacy | `tools/public_history_scan.py --worktree --fail-on-findings` | tracked 565、**blocking 0**（新規`.ts`/`.vue`も走査対象、非blockingの分類のみ） |
| frontend lint | `npm run lint`（ESLint 10、`.ts`/`.vue`/`.js` 52 files） | ok（warning 0） |
| frontend型 | `npm run typecheck`（vue-tsc） | ok |
| Vitest | `npm test` | **77 passed / 0 failed**（11 files） |
| build | `npm run build` | ok。JS 411 KB（gzip 142 KB）、CSS 313 KB（gzip 45 KB）、外部URL参照0、CSS内`url()` 0 |
| E2E | `npm run e2e` | **10 passed / 0 failed**、3回連続で同結果 |
| wheel | `uv build --wheel` → `tools/ui2/check_wheel_assets.py` | ok（隔離venv、`utteran_gui`がvenvから読まれ、`/next/`とhashed asset 2件を配信） |
| 本番依存audit | `npm run audit:prod`（`--omit=dev --audit-level=high`） | 0 vulnerabilities |
| dev依存audit（参考、非gate） | `npm audit` | moderate 12件（すべてESLint系→`minimatch`のReDoS 1系統）。bundleに入らない。CIのgateは本番依存のみ（方針は§4） |

### 2.1 pytest

- 追加: `tests/test_ui2_next_ui.py`（12件。うち実build必須の1件は、buildが無い環境ではskip、`UTTERAN_UI2_REQUIRE_BUILD=1`の
  CI `ui2-frontend`では必須）、`tests/test_ui2_reducer_parity.py`（6件、Python `EventReducer`で共通fixtureを再生）。
- 全体（build済みcheckout、version 0.1.41でsync後）: **1288 passed, 9 skipped, 1 xfailed, 0 failed**。skip 9件はすべて既存
  （Windows専用のprocess・Win32回帰6件、Windows PowerShell起動2件、`git-filter-repo`不在1件）でUI-2による追加skipはない（buildが無い環境では
  実build test 1件が加わる）。xfail 1件は既存のD-08（strict）。version bump直後・`uv sync`前の初回実行では、installed metadataが
  0.1.40のままのため`tests/test_version.py`の2件が失敗し、README更新と`uv sync`の後に解消した。

### 2.2 GitHub Actions

| commit | run | 結果 |
|---|---|---|
| `e9db9f285f771db317a1ada7b35837a841ea98ce`（実装完了時のHEAD） | PR [41774436232](https://github.com/zawa356/Utteran/actions/runs/41774436232)、push [41774393328](https://github.com/zawa356/Utteran/actions/runs/41774393328) | PR runは**14/14 job success**（`ui2-frontend`、Linux/Windows pytest、mypy、schema、privacy 3種、UI-1 spike等） |

この記録を追加したcommit以降のrunは、PRのchecksとIssue #20のコメントを正本とする。

## 3. 何を試験したか

### unit / contract（Vitest、`frontend/src/**/*.test.ts`）

| 対象 | 件数 | 内容 |
|---|---|---|
| `api/http` | 18 | 200、401（平文）、403/410/428（v1 envelope）、409（legacy detail）、422（pydantic配列）、429、500、503、破損JSON、shape不一致、GET timeout、**POST応答喪失＝outcomeUnknown**、network、caller abort、**送信は常に1回（自動再送なし）**、`/api/`外のpath拒否 |
| `api/v1` | 8 | `/sync`全page（同じ`snapshot_token`）、別snapshot混入の拒否、`?after=`のみのURL、cursor/kind/schema_version検証、Idempotency-Key書式・意図ごとに一意、**応答喪失後の明示再試行で同じkey**、202は`accepted`、`If-Match: "<rev>"`・`waiting_item_ids`・409 conflict |
| `transport/latest` | 3 | 古い世代の成功応答も破棄、古い失敗は握りつぶし最新は通す、cancel |
| `transport/poller` | 5 | 3.5秒応答・1秒間隔でも**同時in-flight 1**、失敗の保持とbackoff（2s→4s）、`pollNow`の合流、pause/resume/stop、request中のpause→resumeで並行実行しない |
| `transport/reducer` | 16 | **共通fixture 6事例×2方式**（`tolerate`=Pythonと完全一致、`resync`=gapで再同期）、gap計数、19桁seq（`BigInt`）、不正ID |
| `transport/sync` | 8 | `/sync`→購読の順序、cursor以前のframe破棄、切断→新EventSourceで`?after=`再接続（`/sync`なし）・再送は重複扱い、`open`前の失敗（410等）→`/sync`、`resync_required`、gap/別epoch→`/sync`、解析不能frame→`/sync`、sync失敗→offline＋backoff、stop後は何もしない |
| `domain/environment` | 3 | **D-01**（cuda遅着が後からcpu選択を巻き戻さない・前requestをabort）、error→retry→success、`readiness`は常に`unverified`、再検出の中止 |
| `domain/settings` | 5 | 変更fieldだけPATCH、**pending中の二重送信拒否**、失敗でdraft保持・適用中theme不変・自動再送なし→明示retry、no-op、保存中の編集保持、再読込でdraft保持 |
| `domain/shell` | 2 | mode切替でsection・pane・設定draftが残る、全sectionが移行状態を宣言 |
| `host` | 6 | drop payloadのsanitize、error reportの上限、pywebview ready待ち・cancelのnull化、bridge不在で明示失敗、drop購読解除、fake host |
| `i18n` | 3 | ja/en key一致・空文字なし、引数展開、既知code・未知codeのkind fallback・outcomeUnknown文言 |

### E2E（Playwright、`frontend/e2e/next-ui.spec.ts`、実`create_app(enable_next_ui=True)`）

全シナリオで`securitypolicyviolation` 0件、外部originへのrequest 0件を確認する（fixtureで常時検査）。

1. `/launch` cookie → `/next/`。meta nonceとVuetify theme `<style nonce>`が一致、CSP headerは`style-src 'self' 'nonce-…'`・
   `script-src 'self'`・`unsafe-*`なし・`no-store`、要求ごとにnonceが変わる、`/`は旧policyのまま。
2. session無しの`/api/settings`と誤った`/launch`は401。
3. 未移行4領域（結果と履歴・モデル・キュー・セットアップ）は説明のみで**button 0個**、選択中の表示。
4. **D-01相当**: cuda（1.5秒遅延）→cpuの順に選択、2秒待っても表示・選択はcpu、「未検証」表示。
5. 環境読込500→領域内error（`HTTP 500`）→再試行で回復。
6. 設定: 言語をEnglishにして保存（dblclickでも**PATCHは1回**、body `{"language":"en"}`）、UI英語化・`<html lang="en">`、
   APIで永続化を確認、テーマDark適用。
7. 設定保存503→error表示、draft（ダーク）保持、適用中テーマは変わらない→再試行で保存・適用。
8. 環境profile選択と未保存の設定draftが、Expert切替・画面移動・通常へ戻しても残る。Expert右ペインにbutton 0個。
9. 900×660（pywebviewの最小window）で横scrollなし、Expertは1ペイン＋切替、1100幅で2列＋下段。
10. keyboard: TabでsidebarのVuetify listへ到達、矢印で移動、Enterで設定画面、focus outline表示。

旧UIの非退行は別途、同じserverで`/`を開き、CSP違反0・JS例外0で旧画面（セットアップウィザード初期画面）が
表示されることを確認した（一時scriptで実施、commitしていない）。旧UIの静的asset・`app.js`は変更していない。

### スクリーンショット

`UI2_SCREENSHOTS=1 npm run e2e`で`frontend/test-results/screenshots/`に出力（合成データのみ、commitしない）。
画像（通常モード・Expert 3ペイン・英語/dark・保存失敗・狭い窓）は作業セッションでownerへ提供した。PRには添付していない
（必要なら上記コマンドで同じ画像を再生成できる）。

## 4. 依存監査のpolicy

- **blocking**: 本番依存（`npm audit --omit=dev`）にhigh/critical。registry到達不可などでauditが実行できない場合も
  CI stepは失敗する（「安全」とは記録しない）。
- **非blocking（記録のみ）**: devDependencies（ESLint・Vitest・Playwright・Vite等はbundleに入らない）。2026-11-04時点で
  moderate 12件（ESLint系のtransitive `minimatch` ReDoS 1系統）。修正版の取り込みはlint toolchainの更新時に行う。

## 5. 受入基準との対応（UI-2 Done）

| 基準 | 状態 | 根拠 |
|---|---|---|
| ADR-003 Accepted、WebView2未受入を保持 | ✓ | [ADR-003](../../adr/ADR-003-ui-frontend-stack-and-host-boundary.md) |
| 既定`create_app`・`/launch`は旧GUI、v1/DB/CLI/ジョブ/推論不変 | ✓ | `test_default_app_does_not_expose_next_ui`、既存`test_default_app_is_legacy_only_*`、import境界test |
| opt-inでローカルassetのみ配信・表示、nonce一致、unsafe-inline不要 | ✓（Chromium） | pytest・E2E 1、外部request 0、CSP違反0 |
| 通常/Expert・sidebar・ja/en・compact・狭幅・keyboard、未移行を誤認させない | ✓（Chromium） | E2E 3, 6, 8, 9, 10 |
| 実API縦切り（環境・設定）でpending/success/error/retry/競合 | ✓ | E2E 4〜7、domain unit |
| host/api/transport/domainがviewから分離、契約がtest可能 | ✓ | ESLint制約、unit 77件 |
| `epoch:seq` gap/別epoch/重複/逆順/再接続/410→sync、旧pollerは単一in-flight | ✓（fake） | reducer・sync・poller unit、Python parity |
| wheel/asset配信・clean cloneのbuild・型検査・E2EをCIで再現 | ✓（ローカル）／CIはPR参照 | `ui2-frontend` job、wheel検査 |
| `frontend/`の`.ts`が全て追跡、lock・NOTICEあり、dist/node_modules非追跡 | ✓ | `git check-ignore -v`、`THIRD_PARTY_NOTICES.md` |
| 文書/version/Issue一致、Draft PR | PR・Issueコメントで記録 | — |

## 5.1 PR #36レビュー指摘への対応

| 指摘 | 対応 | 検証 |
|---|---|---|
| P2: `--next-ui`起動後に「旧画面を開く」で旧UIへ移るとnative D&Dが結ばれない | `--next-ui`時のみ`events.loaded`で読込ごとに再バインド、documentごとに1回（[design](design.md)） | `tests/test_ui2_next_ui.py`のfake window試験（遷移・重複loaded・往復で各documentに1回）。marker判定を外すと失敗することを確認。Windows実機では§7で3経路とも合格（0.1.42） |
| B9 epoch跨ぎの`queue_revision` | このPRでは変更しない（B9本体の契約・testと同時に直し、TS/Python parityを維持する）。S5（v1実接続）前のblocking課題 | — |

### Windows実機での最小smoke（手順。0.1.42で実施済み、結果は§7）

1. `utteran-gui --next-ui`でWebView2が起動し、新UIが表示される。DevToolsのConsoleにCSP違反が出ず、
   `style[nonce]`が`<meta name="csp-nonce">`と一致する。
2. テーマ（ライト/ダーク/システム）・日英切替・設定保存、通常/Expert、狭い窓、DPI 125/150/200%。
3. 「旧画面を開く」→ Explorerから合成WAVを入力欄へD&D → 入力欄に反映される。新UIへ戻って再度旧画面へ移り、
   D&Dが1回だけ反映される（二重に反映されない）。
4. flagなしの`utteran-gui`で既定の旧GUIが従来どおり起動し、D&Dが効く。

native file dialogは新UIでは未接続のため、UI-2では受入対象にしない（後続で受入）。installer/portableも未受入。

## 6. 未受入（0.1.42時点でも確認していない）

Windows実機で確認した範囲は§7。以下は**Windows実機を含め未受入**:

- 新UI自身へのnative D&D（受け口未実装）と新UIからのnative file dialog（未接続）。
- IME composition（UI-2に自由入力欄が無い。UI-3 #21の受入条件へ繰越）。
- 実`EventSource`/SSE（新UIはv1を使わない）、起動時間・プロセスRAMの計測。
- DPI 200%・1920×1200で既定窓（1180×820 DIP）が画面外にはみ出す制約の解消（UI-4 #22の既定切替ゲート、§7）。
- installer/portable（`build.ps1`未変更。build済みなら`gui.spec`が同梱する設計のみ）、SAC/WDAC。
- 実GPU・実モデル・実音声、B4 ready証拠、B8実setup、v1 activate（新UIは行わない）。

## 7. Windows実機受入（0.1.42、HEAD `862870deccbc2fc9cd1041b96d8c6ea64800fbfe`）

ownerがWindows実機で実施し、PR #36のコメントに記録した結果。cloud側ではGUIを操作していない。合成データのみ、実推論なし。

| 項目 | 値 |
|---|---|
| 環境 | Windows build 26300.9711、WebView2 Runtime 156.0.4314.58、Python 3.12.13、pywebview 6.2.1、Node 22.23.4 / npm 11.21.1 |
| 修正 | `862870d`: pywebviewが文字選択禁止のために注入するnonceなし`<style>`が、WebView2で新UIのCSP違反を1件起こした。`--next-ui`時だけ`create_window(..., text_select=True)`を渡す。既定旧GUIの起動引数・start callback、`script-src 'self'`は不変、`unsafe-*`なし。新UIから旧画面へ移った同じwindowでは文字選択が許可される |
| CI | push [41788997600](https://github.com/zawa356/Utteran/actions/runs/41788997600)・PR [41789005424](https://github.com/zawa356/Utteran/actions/runs/41789005424): 各**14/14 success** |
| Windows pytest | 1293 passed / 8 skipped / 1 xfailed / 0 failed（skip・xfailは既存）。UI-2対象22 passed |

| 試験 | 結果 |
|---|---|
| CSP | 修正後の実再読込でCSP違反0、JS例外0、Vuetify theme `<style>`と`<meta name="csp-nonce">`のnonce一致、外部request 0 |
| 設定・表示 | テーマ3種、ja/en、実APIでの保存・再読込・再起動後の保持、通常/Expert/sidebar、「未検証」表示、合成503での保存失敗表示とretry: PASS |
| native D&D（Explorerから合成WAV） | 新UI→旧画面、同じwindow/serverでの往復後、flagなし旧GUIの3経路すべてで**trusted DOM drop 1 / アプリ通知1 / 入力欄反映あり**。初回に通知2回を観測したが実drop数を数えていなかったため、counterを追加して再試験し、1回のdropの二重処理は再現しなかった |
| 最小窓・keyboard | native外枠のドラッグで最小900×660（100%時client 884×621）を実測。横overflow 0、Expert全ペイン切替、Tab/矢印/Enter/Escとfocus |
| DPI | OS 125/150/200%で各再起動。native DPI 120/144/192、WebView2 DPR 1.25/1.5/2.0、横overflow 0。終了後OS 100%（DPI 96 / DPR 1.0）へ復元 |
| **DPI 200%の制約** | 1920×1200・200%では既定1180×820 DIPの窓が画面外にはみ出す。最大化すれば画面内に収まり、縦scrollで設定・保存・sidebarを操作できた |

**判定: 条件付き受入。** UI-2は開発者opt-in（`--next-ui`）のみで既定GUIは旧UIのまま、という前提で、200%制約を明記してUI-2を閉じる
（owner提案）。**200%を完全PASSとは扱わない。** UI-4 #22の既定切替ゲートで、初期window sizeを画面に収める（適応サイズ・画面収容）ことを
必須とする。IME compositionはUI-3 #21で最初の自由入力フォームを作る際にE2E化して受入する（開始/更新/確定/取消、未確定入力が
実行・送信を誘発しない、確定文字列が重複しない）。installer/portable、新UI自身のD&D・file dialog、実SSE、実GPU/モデル/推論は対象外。
