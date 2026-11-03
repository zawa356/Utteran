# Testing, CI and operations

## CIジョブ（`.github/workflows/ci.yml`）

Issue #27の是正で、各品質ゲートを**`needs:`の無い独立job**に分けた（PR #34レビュー修正で新規履歴gateを追加し12 job）。1つの赤が他のゲートを隠さない。
`continue-on-error`・`|| true`・`if: always()`による緑化はしていない。

| job | runner | 検査 | 守備範囲外 |
|---|---|---|---|
| `linux-lock-schema` | ubuntu | `uv lock --check`、offline schema 8種（B1/B3/B4/B5/B6/B7/B8/B9）`--check` | — |
| `linux-lint` | ubuntu | `ruff check`、`ruff format --check`（`src tests tools`） | — |
| `linux-types` | ubuntu | `mypy`（strict）と`mypy --platform win32`（Windows専用分岐を型検査） | `tests/`は対象外（pyprojectの`packages`） |
| `linux-tests` | ubuntu | `pytest -m "not requires_model"` | モデル・GPU・Windows専用test |
| `windows-tests` | windows | 同上＋Job Object親死亡testを**毎回20回反復**（PID-file競合の回帰監視） | GPU・実モデル・installer |
| `windows-powershell` | windows | 全tracked `.ps1`のPS 5.1 AST parse、安全な起動allowlist | installer統合（下記） |
| `contracts-python-compatibility` | ubuntu 3.11/3.12 | 契約・境界・B5〜B9・**B7〜B9横断E2E**のtestとschema `--check` | — |
| `powershell-bom` | ubuntu | 全tracked `.ps1`のUTF-8 BOM | — |
| `privacy-worktree` | ubuntu | tracked fileのscan、blocking 1件以上で失敗 | 履歴 |
| `privacy-history-delta` | ubuntu | **新規commit全系列**のprivate data検出で失敗（下記。`fetch-depth: 0`） | 信頼済み履歴（protected main＋レビュー済みcutover） |
| `privacy-history` | ubuntu | 全reachable履歴のredacted report（`fetch-depth: 0`、**report only**。合否はdelta gateが持つ） | history rewriteはしない |

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
- **reviewed cutover**: `tools/public_history_baseline.json`に固定した`51dc90fa8bbdbe3c80c5e8adc3f96811677c6cb5`
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

- mainのbranch protectionで`privacy-history-delta`をrequired status checkにする
- `.github/workflows/ci.yml`、`tools/public_history_*.py`、`tools/public_history_baseline.json`、
  `tools/public_scan_reviewed_media.json`の変更をセキュリティレビュー必須にする（CODEOWNERS等）
- baselineの変更（cutoverの移動、bot addressの追加）は信頼範囲を広げる操作として扱い、理由をPRに残す

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
