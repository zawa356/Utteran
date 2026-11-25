# Testing, CI and operations

## CIの起動条件

| イベント | ワークフローと対象 |
|---|---|
| 非mainへのpush（tagを含む） | `ci-light.yml`。push HEADで両privacy blocking、lock・schema 8種、Ruff、mypy（Linux／win32）、PowerShell BOM |
| PR opened／synchronize／reopened／ready_for_review | `ci.yml`。Draft・forkを含み、PRのmerge候補をfull検証。`ci-historical.yml`は歴史的試作に関係する変更を検証 |
| mainへのpush | `ci.yml`。統合後のSHAで全製品ゲート |
| 毎日 18:37 UTC（翌日03:37 JST）／手動 | Actions → Historical checks → Run workflow。全reachable履歴のreport-only監査、UI-1 spike、UI-3-0 prototype UXを実行 |

Historical checksのPR判定は、merge候補の第1親をイベントのbase SHAと照合し、全差分をGitで比較する。
`tools/ui1/`はUI-1、`frontend/`はUI-3-0、`.github/`は両方を起動する。リネーム両端・削除も判定する。
比較不能の場合は両方を実行する。製品bundleのprototype隔離は毎fullで実行する。

PRの新runは同一PR番号の古いfullだけを取消す。mainはrun IDで分離する。
非main pushは毎回独立して両privacy検査を完走させる。force-pushで前のcommitが次のHEADから消える場合もあるため、
同じbranchでも軽量runを取消さない。PRの旧結果・手動履歴監査・push HEADの軽量成功はfullの証拠に使わない。

## Full CIジョブ（`.github/workflows/ci.yml`）

Issue #27の方針を継承し、製品の検査jobは独立して実行する。固定名の `Full product quality gate` は全依存jobの
結果を検査し、失敗・取消・skip・欠損で失敗する。matrixは `fail-fast: false` で全runnerを実行する。

| job | runner | 検査 | 守備範囲外 |
|---|---|---|---|
| `linux-lock-schema` | ubuntu | `uv lock --check`、offline schema 8種（B1/B3/B4/B5/B6/B7/B8/B9）`--check` | — |
| `linux-lint` | ubuntu | `ruff check`、`ruff format --check`（`src tests tools`） | — |
| `linux-types` | ubuntu | `mypy`（strict）と`mypy --platform win32`（Windows専用分岐を型検査） | `tests/`は対象外（pyprojectの`packages`） |
| `linux-tests` | ubuntu | `pytest -m "not requires_model"` | モデル・GPU・Windows専用test |
| `windows-tests` | windows | 同上＋Job Object親死亡testを**毎回20回反復**＋B6 concurrent snapshot **10回反復**。本体は `--durations=20` で計測 | GPU・実モデル・installer |
| `windows-powershell` | windows | 全tracked `.ps1`のPS 5.1 AST parse、安全な起動allowlist | installer統合（下記） |
| `contracts-python-compatibility` | ubuntu 3.11/3.12 | 契約・境界・B5〜B9・**B7〜B9横断E2E**のtestとschema `--check` | — |
| `powershell-bom` | ubuntu | 全tracked `.ps1`のUTF-8 BOM | — |
| `privacy-worktree` | ubuntu | tracked fileのscan、blocking 1件以上で失敗 | 履歴 |
| `privacy-history-delta` | ubuntu | **新規commit全系列**のprivate data検出で失敗（下記。`fetch-depth: 0`） | 信頼済み履歴（protected main＋レビュー済みcutover） |
| `ui3-frontend-windows` | windows | lockfileからのnpm ci、lint、型、Vitest、Vite build | WebView2 |
| `frontend-quality` | ubuntu | lint、型、Vitest、Vite build | ブラウザー統合 |
| `frontend-bundle-wheel` | ubuntu | 実bundle必須Python tests、prototype隔離、wheelの新規venvからのserve | prototype UX |
| `npm-production-audit` | ubuntu | 本番依存の既存blocking audit。失効・例外範囲、registryエラーも検査 | — |
| `product-e2e` ×3 | ubuntu | Playwrightのファイル単位shard、実FastAPI、合成サービス | WebView2 |
| `full-product-gate` | ubuntu | 全製品jobの成功を集約 | 定期監査・歴史的試作 |

### 再実行と計測

`frontend/`で `npm run e2e` を実行すると製品E2E全件を直列で再現できる。CIと同じ収集照合は
`node scripts/check-e2e-shards.mjs 0 3`、個別shardは先頭引数を 1〜3 に変更する。
CIではrunnerごとに異なるloopback portと合成データrootを使い、各specを同じrunnerの1 workerに保持する。
全件identity・各shardの収集と実行の一致、空shard、失敗を検査する。reportはrunner内に残しuploadしない。
npm cacheはOS・architecture・Node／npm pin・lockfileに紐付くdownload cacheのみ。npm ciとuvのlocked installは常に実行する。

基準のWindows pytestでは本体が約6分42秒〜10分47秒、競合反復の合計が約38〜52秒だった。
反復だけを別runnerに移す効果が小さいため、今回は単一Windows jobを維持する。新しいdurationsの計測を、
順序・子process・排他に配慮したファイル単位の分割を検討する材料にする。
失敗時はActionsでRe-run failed jobsを選ぶ。baseが更新されたときは最新mainを取り込み、
新しいPR候補でfullを実行する。SHA・所要時間・runner-minutesの実測はIssue #84のDraft PRに記録する。

### Required checksの移行（オーナー操作）

保護APIは403で設定未確認。ブランチ保護とrulesetは変更していない。オーナーはSettingsの
Branches／Rulesetsでmainに適用されるrequired status checks、checkの発行元、最新baseを要求する設定、bypass対象を確認する。
Draftのまま、このPRの最新head・base・merge SHAが示す候補でfullが成功したことを確認する。
既存requiredを維持したままGitHub Actions発行元の `Full product quality gate` を追加し、最新baseを要求する。
新gateが必須になったことを確かめてから、設定されている場合に限り、置き換え対象の旧UI-2一括checkと
report-only・歴史的試作の旧required設定を外す。差分に該当する歴史的試作は、オーナーがPRの結果を確認する。
両privacy blockingの既存required設定は維持できる。lightのcheck名は `Light /` で始まる。

| 状況 | 判定 |
|---|---|
| Draft／Ready、reopen、fork | 通常のpull_request権限で対象候補をfull検査。fork側の承認待ちは未完了 |
| synchronize／force-push | 新headでfull。旧headのfullや軽量push結果を使用しない |
| base更新 | 最新baseの要件で候補更新を強制し、再度full |
| label変更／manual／schedule | fullの代替経路を作らない |
| skip／cancel／fail | Latest候補のfull successを待つ |
| 同時実行 | main、別PR、軽量push、監査の結果を互いに取り消さない |

この移行でrequired checkの最新候補への適用を確認するまで、Draft軽量化は導入しない。

### 新規履歴privacy gate（`privacy-history-delta`）

3つのprivacy検査は役割が別で、件数も別々に記録する。

| 検査 | 対象 | 合否 |
|---|---|---|
| `privacy-worktree` | 候補HEADのtracked file | blocking 1件以上で失敗 |
| `privacy-history-delta` | 候補HEADから到達でき、信頼済み履歴から到達できない**全commit**（中間・merge commitを含む） | 新規blocking 1件以上で失敗（exit 1）、入力不備で失敗（exit 2） |
| `privacy-history` | 全reachable履歴 | report only（既知の過去findingを含む総数。refs・objectの差で変動するので合否に使わない） |

検査範囲は `git rev-list <candidate> --not <trusted base> <reviewed cutover>`。

- **trusted base**（eventから決める。`resolve_range()`、単体テストあり）:
  `pull_request` = `pull_request.base.sha`／`push`でmain以外 = その時点の`origin/main`／
  `push`でmain = push**前**のSHA（`before`。全0なら失敗）。候補HEADと同じSHAは使わない（空範囲は失敗）。
- **reviewed cutover**: `tools/public_history_baseline.json`に固定した`e0960bdc4cd53264f214d85844f89742e587d39c`（2026-11-03の履歴書換え後のSHA。書換え前の`51dc90f`に1:1で対応）
  （PR #34のレビュー基準。全履歴を分類済み）。CLI・環境変数・候補HEADからは変更できず、自動で進まない。
  cutoverより後に追加されたcommitは必ず検査される（`test_cutover_never_moves_with_new_commits`）。
- **将来のPR**: #34がmainへ入るとcutoverはmainの祖先になり、範囲は「保護されたmainのbase SHAより後のcommit」だけで決まる。
  cutoverは不要になるが、残しても範囲を狭めない。squash merge等でcutover objectが消えた場合はgateが
  `cutover_missing`で失敗するので、レビュー付きPRでbaselineを更新する。

「新規」の判定: commitが**すべての親**に対して導入した内容だけを見る。

- blob本文: 一致値（1回の実行ごとのランダムHMAC鍵で比較。値は保存しない）が、同じpathの親versionに無い
- path判定: media・`.env`・path pattern。親versionに無かったもの
- commit message: すべて。例外は、末尾trailer段落の`Co-authored-by: <name> <addr>`行で、addrがbaselineに列挙された
  bot専用アドレス（`noreply`の完全一致のみ、最大5件）の場合だけ

この判定により、次のように扱われる。

- 既知findingを含むfileの別の行を変えただけなら再検出しない
- 同じ値を別pathへ置けば新規
- 信頼済みmainをmergeしただけなら新規なし
- diffの内容は新blob本文に含まれるので、blob単位で検査する

**fail closed（exit 2）**: `shallow_repository`、`candidate_missing`、`trusted_base_missing`、`cutover_missing`、
`baseline_invalid`、`empty_range`、`event_incomplete`、`push_before_missing`、`main_ref_missing`、
`unsupported_event`、`git_failed`、`scanner_error`。どれも「0件」として成功させない。

**出力**: category・detector・scope・短縮commit SHA・pathのSHA-256先頭16桁・件数のみ。一致値・path・本文は出さない。
JSON reportも同じ内容で、artifactとしてはuploadしていない。full-history reportにはpathが含まれるが、こちらもuploadしない。

**信頼の限界**: PRはworkflow・gate・baselineそのものを書き換えられるので、CIだけでは完全には防げない。
次の運用が必要になる（ownerの設定事項）。

- mainのbranch protectionでブロッキングcheck `Privacy gate for new commits (blocking)` をrequired status checkにする
- `.github/workflows/ci*.yml`、`tools/public_history_*.py`、`tools/public_history_baseline.json`、
  `tools/public_scan_reviewed_media.json`の変更をセキュリティレビュー必須にする（CODEOWNERS等）
- baselineの変更（cutoverの移動、bot addressの追加）は信頼範囲を広げる操作として扱い、理由をPRに残す

### placeholder判定（3つのprivacy検査で共通）

`tools/public_history_scan.py`の`_is_placeholder(detector, value)`を、worktree・full-history・new-history gateが共有する。
判定は**detectorごとに値全体**で行い、部分文字列（`user`・`test`・`example`・`person`・`fake`・`xxxx`・`workdir`等）では免除しない
（PR #34 review P2）。placeholderと判定された値は`test-placeholder`（非blocking）として表示に残る。

| detector | 非blockingになる条件（これ以外はblocking） |
|---|---|
| `absolute-user-path` | account部分が`<user>`・`<名前>`のような角括弧表記で、path segment全体と一致するもの（括弧内1〜32 byte） |
| `email-address` | domainが`example.com`・`example.net`・`example.org`、またはそのsubdomain（RFC 2606） |
| `hugging-face-token` / `github-token` / `aws-access-key` | 本体が「1種類の文字の繰返し（8文字以上）」か、「`example`・`dummy`・`fake`・`placeholder`・`your`の直後がその繰返しだけ」のもの |
| `private-key`・その他 | なし |

ルールの要点:
- `Users`/`UserData`の固定部分は判断に使わない。
- `testuser`のような実在し得るaccount名はblockingになる。
- bot co-authorの例外は、new-history gateが扱う末尾trailerの完全一致だけで、メール全般には広げない。

テストfixtureの規則: 合成値でも、実形式のpath・address・tokenはsourceに直書きしない。`"hf_" + "…"`のように実行時に組み立てる。

### PowerShell起動allowlist（`windows-powershell`）

起動検査は**ソースで副作用が無いと確認した引数だけ**を使う。新しい`.ps1`を追加したら、この表かparse-only表に
分類しないとjobが失敗する（分類漏れ検出）。

| script | 引数 / 入力 | 期待 | 根拠 |
|---|---|---|---|
| `setup.ps1` | `-List` | exit 0、`cpu`を含む | venv一覧を表示して`exit 0`。venv作成・削除なし |
| `run.ps1` | `-Profile cpu --help` | exit 1、`win-cpu`を含む | runnerにprofile venvが無いので案内表示で終了 |
| `start.ps1` | stdin `0` | exit 0 | メインmenuの「0. 終了」。`input/` `output/`作成のみ（既存） |
| `launch-python.ps1` | `-CheckOnly` | exit 0または1 | 前提条件（Python/uv/ffmpeg）確認だけで終了。LocalAppDataへtranscript |
| `scripts/reset-env.ps1` | `-WhatIf` | exit 0、`openvino-genai-compiled`を含む | `SupportsShouldProcess`。削除しない |
| parse-only: `build.ps1` | — | — | Inno Setup・release artifact生成 |
| parse-only: `gui.ps1` | — | — | native window |
| parse-only: `scripts/verify_uninstaller.ps1` | — | — | Inno Setup 6でlab buildをinstall/uninstall。**Windows受入でのみ実行** |
| parse-only: `tools/generate_benchmark_fixture.ps1` | — | — | tracked fixtureをTTSで上書き |

旧CIは`verify_uninstaller.ps1`等を無引数で実起動しており、Inno Setup欠如で失敗していた（#27）。

## ローカルで同じ検査を回す

```bash
uv sync --extra dev --extra gui --locked
uv lock --check
uv run --no-sync ruff check src tests tools
uv run --no-sync ruff format --check src tests tools
uv run --no-sync mypy
uv run --no-sync mypy --platform win32
uv run --no-sync pytest -m "not requires_model"
uv run --no-sync pytest tests/test_backend_foundation_e2e.py
uv run --no-sync python tools/check_powershell_bom.py
uv run --no-sync python tools/public_history_scan.py --worktree --fail-on-findings
```

```bash
uv run --no-sync python tools/public_history_delta.py --candidate "$(git rev-parse HEAD)" --trusted-base "$(git rev-parse origin/main)"
```

## B7〜B9横断E2E（`tests/test_backend_foundation_e2e.py`）

同一`create_app()` runtime・同一control DBで、実FastAPI/ASGI（`TestClient`）を通す11ケース。

| 保証すること | 使う代替物（実機ではない部分） |
|---|---|
| 既定appはlegacyのみ・DB非作成／opt-inは1つのguarded mount・旧queue閉鎖 | — |
| Setup plan→approve→operation→paused queue→resume→executor→`succeeded`、replayの同一ETag/Location、環境変化後の`inventory_stale`、`/sync`・diagnostics・Outboxの一致 | `World`（setup.ps1・DLを模擬するfake child runner） |
| Job resolution→batch（replay重複なし）→hold/release→dispatch→`succeeded`、1 epoch・単調seq、Last-Event-ID再接続で再起動しない、未検証構成は409でlegacy fallbackなし | `FakeProvider`（テスト専用のB4 ready証拠、永続化しない）、`FakeRunner`（実の短命Python子＋合成completion） |
| `/sync`→SSEのreducerが最終`/sync`に収束、重複・他epochを無視 | TestClientのSSE framing（**ブラウザEventSourceではない**） |
| v3→v6でv3のままのbackup、旧classの拒否、v4/v5→v6、migration中故障のrollback | fault hook |
| 確認できないownerでの再起動はslotを保持し二重起動しない | `UnknownVerifier`、生き残る子プロセス（実kill・Windows ACLではない） |
| prompt/token/pathがHTTP/SSE/sync/diagnostics/logに出ない、上限・cursor改ざん、compaction後もdiagnostics不変・古いcursorは410 | 合成sentinel |

既存のB7/B8/B9単体HTTPテストの複製ではなく、**同一runtime・同一DBでの接続**を新たに保証する。

## 既存の`tools/acceptance`

`tools/acceptance/harness.py`＋`cases.json`（194ケース）は**旧CLI経路の実機受入**用（実モデル・実入力）。
B7〜B9の新v1 APIを実Windows/GPUで通した根拠では**ない**。v1の実機受入は別途設計が必要。

## 未受入ゲート

本ブランチの完了条件に含まれず、**確認済みではない**:
Windows実機でのv1 opt-in起動、GPU（CUDA/Intel/Vulkan）、native build、実モデルDL、長時間音声、
installer/portable（`verify_uninstaller.ps1`を含む）、WebView2/実ブラウザでのSSE、
実process kill後の復旧、Windows ACL・reparse point判定、実機でのbackup restore、実smoke evidence provider。

## 障害時の確認

| 症状 | 確認 | 備考 |
|---|---|---|
| `/api/v1/*`が404 | `create_app`のopt-in引数 | 既定起動では存在しないのが正常 |
| 401/403 | session header/cookie、Host=`v1_authority`、Origin | |
| Job投入が409 `execution_unavailable` | B4のready証拠（smoke）有無 | 実機では標準collectorにsmokeが無く通常この状態（fail closed） |
| 起動直後に何も走らない | `GET /api/v1/queue`の`dispatch` | activate後は必ず`paused`。`PATCH /queue/control`で明示再開 |
| `recovery_required`のまま | owner/子プロセスの生存確認 | 自動では解除しない（二重起動防止） |
| `store_schema_unsupported` | DBの`user_version`と使用中のアプリ版 | 新しいDBを古い版で開いた。`operations-pre-v1-*`からの復元を検討 |
| SSEが410 / `resync_required` | cursorのepoch・floor | `/sync`から取り直す |
| 旧wizard/jobsが409 | v1 runtimeがactive | 旧queueはcutover中は閉じる |
