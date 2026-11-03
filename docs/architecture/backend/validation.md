# Validation — Backend Foundation Hardening (0.1.39)

実測のみを記録する。Linuxコンテナで実行できないもの（PowerShell、Windows固有test）は**CIの結果だけ**を根拠とし、
ローカル成功とは書かない。

## 開始時の証拠（2026-11-03）

| 項目 | 値 |
|---|---|
| `main` HEAD | `e803ecc3dcaff48973342521c8bd77ecf1bbb353`（PR #33 merge）。`git fetch`後もremoteは同一、他者commitなし |
| 作業ブランチ | `claude/epic-thompson-ip1ol1`（環境でpush可能なブランチが固定されているため。推奨名`chore/backend-foundation-hardening`は不使用） |
| worktree | clean |
| ローカル環境 | Linux（コンテナ）、uv 0.11.32、venv Python 3.11.18、別途 `/usr/bin/python3.11`(3.11.18)・`python3.12`(3.12.3) |
| version | `pyproject.toml` 0.1.38（`__version__`は`importlib.metadata`由来） |
| モジュール | `utteran_contracts` `utteran` `utteran_settings` `utteran_control` `utteran_gui` `utteran_paths`。**`utteran_worker`は存在しない**（workerは`utteran.worker`） |
| 既定 `create_app()` | 旧`/api/*`のみ（`/assets` mountを含め35 entry）。`app.py`は opt-in を一切渡さない |
| opt-in全部 | `/api/v1`に26 route（[interfaces.md](interfaces.md)） |
| `AGENTS.md`/`CLAUDE.md` | 存在しなかった |
| CI baseline（指示書記載、run 41586646910、`e803ecc3`） | Windows pytest 1,214 pass / 9 skip、契約3.11/3.12 各618 pass / 1 skip。Linuxはmypy 9件で停止し、Linux pytest・BOM・public scanは**未到達**。PowerShell headless検査はInno Setup欠如で失敗 |

### ベースライン実測（ローカル、`e803ecc3`相当のtree）

| 検査 | 結果 |
|---|---|
| `mypy` | **9 errors**（すべて`src/utteran_gui/processes.py`、`WinDLL`/`WinError`/`get_last_error`） |
| `mypy --platform win32`（参考） | 修正前のprocesses.py以外に3件（`store.py` `os.geteuid`、`worker_client.py` `os.killpg`/`SIGKILL`） |
| Ruff check / format | 成功 / 200 files formatted |
| `uv lock --check` | 成功 |
| `pytest -m "not requires_model"` | **1212 passed / 2 failed / 9 skipped**。failed: `test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`、`::test_profile_manifest_detects_venv_move_without_disabling_profile`（fixtureがWindowsのvenv配置固定） |
| BOM | **失敗**: `scripts/verify_uninstaller.ps1`（日本語リテラルを含むのにBOMなし） |
| privacy worktree | 434 files、findings 93、**blocking 3**（すべて`media-file-path`: `site/src/content.config.ts`、`tests/fixtures/benchmark/japanese_reference.wav`、`tests/fixtures/diarization_quality/meeting.wav`） |
| privacy history | refs 45 / commits 363 / objects 3829、findings 18374、**blocking 373**（media 316、email 53、absolute-user-path 4） |

## 原因と是正

| #27項目 | 原因 | 是正 | 検査を緩めたか |
|---|---|---|---|
| Linux mypy 9件 | typeshedのwin32専用ctypes属性 | 各Win32 helper冒頭の`sys.platform != "win32"`分岐（mypyのplatform narrowing）。`argtypes/restype`・エラー検査は不変 | いいえ。`--platform win32`検査を**追加** |
| Linux pytest 2件 | テストfixtureのOS依存 | `CliAdapter`と同じplatform判定でvenv配置を作る | いいえ（assertion不変） |
| PID-file競合 | 存在だけ待ち空ファイルを`int('')` | 子はtmp→`os.replace`、親は完全な正整数PIDをbounded polling（publisher早期終了で即fail） | いいえ（Job Object kill-on-closeの確認は不変） |
| PowerShell headless | 全scriptを無引数実起動、`verify_uninstaller.ps1`がInno Setupを要求 | 副作用の無い引数の明示allowlist＋分類漏れ検出。installer統合はparse-only／Windows受入 | 全`.ps1`のPS 5.1 parseは例外なし維持 |
| BOM | `verify_uninstaller.ps1`のBOM欠如 | BOM 3バイトのみ付与（strict UTF-8確認済み） | いいえ |
| 後続step未実行 | 単一jobのfail-fast | `needs:`無しの独立job 10種（matrix含め11 job） | `continue-on-error`等は不使用 |
| privacy blocking 3 | `.ts`のMPEG-TS判定、合成WAV | 内容でTypeScriptを判定、`(path, git blob)`完全一致のレビュー済みfixtureのみ非blocking（`meeting.wav`は生成器でバイト一致再生成、両WAVはRIFF `fmt`/`data`のみ） | 内容不明・変更・移動はblockingのまま（テストあり） |

追加で見つけた問題:

- **pre-v1 backupがmigration後**: `JobRuntime.activate()`がstore構築（v6へmigration）後にbackupしており、
  既存v3 DBのbackupが`user_version=6`だった（ローカルprobeで確認）。`backup_before_open()`で修正。
  修正前のコードに戻すと`test_opt_in_migrates_v3_to_v6_with_restorable_pre_cutover_backup`が失敗することを確認（mutation check）。
- **B6並行snapshot testのWindows間欠失敗**: run 41592415226でWindowsのみ`store_busy`（PR #28当時と同じreader starvation）。
  `store_busy`は文書化されたretryable（503）なので有界retry（busy 50回まで）とし、25回の完全検証を必須に維持。
- **history scanのdiff記号誤検出**: デコレータ（`@app.command(...)`等）の追加行を、行頭のdiff記号`+`をlocal partとしてe-mail扱いしていた。
  内容行の1文字目（`+`/`-`/空白）を除いて走査するよう修正（実アドレスは引き続き検出、テストあり）。

## 完了時の実測（ローカル、Linux）

| 検査 | 結果 |
|---|---|
| `uv lock --check` | 成功。lock差分は`utteran` 0.1.38→0.1.39の1行のみ（ローカルuvが付けた`revision = 3`への変更は戻した） |
| Ruff check / format | 成功 / 202 files formatted |
| `mypy` / `mypy --platform win32` | **0 / 0**（118 source files） |
| offline schema 8種 `--check` | 全成功（API・DTO・schema bytesの変更なし） |
| `pytest -m "not requires_model"` | **1240 passed / 0 failed / 9 skipped**。baseline 1212/2/9 → +28 = 新規26（E2E 11、docs link 11、scan 4）＋既知failed 2件の解消。skipはbaselineと同一の9 node |
| `pytest tests/test_backend_foundation_e2e.py` | **11 passed**（3回連続、各約5秒） |
| BOM | 成功（9 tracked files） |
| privacy worktree `--fail-on-findings` | **blocking 0** |
| wheel | `uv build --wheel` → 3.11.18／3.12.3の隔離venvへinstall → `utteran_contracts`・`utteran_control`・`utteran_settings`・`utteran_paths` import成功、version 0.1.39 |

skip 9件（baselineと同一node）: Windows専用regression 6（acceptance console、Win32宣言、process-tree、Job Object×2、PID liveness）、
PowerShell起動 2（`test_profiles.py`）、`git-filter-repo`未導入 1。

E2E実行後のcleanup: 各testは`tmp_path`のみを使い、`TestClient`のlifespan終了で`JobRuntime.shutdown()`が走る。
再起動testで意図的に残した子プロセスは`finally`で`kill()`＋`wait()`する。

### privacy history（全reachable履歴、redacted）

| 時点 | blocking | 内訳 |
|---|---|---|
| 開始時 | 373 | media 316（3 pathの全commit分）、commit-message e-mail 45、commit-diff e-mail 7、blob e-mail 1、absolute-user-path 4 |
| 最終（CI run 41595136352、HEAD `b83669c`、refs 44 / commits 376 / objects 3932、ローカルgc後も同値で再現）※`51dc90f`では69（下記「PR #34レビュー修正」） | **68** | commit-message e-mail 58、commit-diff e-mail 4、blob e-mail 2、absolute-user-path 4。**media 0** |

残存分類（値は表示せず、ハッシュ・種別のみで判定）:

- commit-message 58件: 57件は`noreply`系botの`Co-Authored-By` trailerのみ（本ブランチのcommit分を含む）。残る1件は本ブランチの
  commit `4244916`の本文がデコレータ記法を引用したもので、アドレスではない。
- `docs/architecture/backend/validation.md`の旧版（`d3633f6`）のblob 1件・diff 2件: 同じデコレータ記法の説明文。現行版では解消（worktree 0）。
- commit-diff/blobのe-mail: 合成値（`.invalid` TLD、`tests/test_private_history_match.py`）。デコレータ誤検出5件は修正で解消。
- absolute-user-path 4件: `tests/test_b9_events.py`の旧版にあった合成sentinel（現行は実行時組立てで非該当）。
- （作業途中のローカル計測では、unreachable tree経由で同一WAV blobが4件出たが、`git gc`後は0件。CIのcloneにも無い。）
- **実在の秘密値・実ユーザーのアドレス・実録音と判断したものは無い。** history rewriteは行っていない（方針として実施しない）。

## GitHub Actions

| run | HEAD | 結果 | 備考 |
|---|---|---|---|
| [41591215254](https://github.com/zawa356/Utteran/actions/runs/41591215254) | `dd3436c` | **success（11/11 job）** | 独立job化直後。PID test 20/20、PowerShell起動: setup -List 0、run 1、start 0、launch-python -CheckOnly 1、reset-env -WhatIf 0 |
| [41592415226](https://github.com/zawa356/Utteran/actions/runs/41592415226) | `0dc0a01` | **failure（10/11）** | Windows pytest 1227 passed / **1 failed** / 9 skipped: `test_b6_control.py::test_snapshot_is_consistent_under_concurrent_writes`（`store_busy`）。新E2EはWindowsでも成功 |
| [41593297070](https://github.com/zawa356/Utteran/actions/runs/41593297070) | `b535e7d` | **success（11/11 job）** | Windows: PID test 20/20、B6 snapshot test 10/10 |
| [41594491528](https://github.com/zawa356/Utteran/actions/runs/41594491528) | `d3633f6` | **failure（10/11）** | `privacy-worktree`がblocking 1を検出: 本書の誤検出説明文そのものがe-mail形状だった。文言を修正（gateは正しく機能） |
| [41595136352](https://github.com/zawa356/Utteran/actions/runs/41595136352) | `b83669c` | **success（11/11 job）** | 最終コードHEAD。Linux/Windows pytest **1240 passed / 9 skipped**、PID test 20/20、B6 snapshot 10/10、worktree blocking 0、history blocking 68（上記分類） |


PID-file競合とB6 starvationは**間欠事象**のため、1回の成功で解消と結論しない。Windows CIで毎run反復し、証拠を蓄積する。

## PR #34レビュー修正（2026-11-03、[review 5748662764](https://github.com/zawa356/Utteran/pull/34#pullrequestreview-5748662764)）

開始時: branch HEAD = PR head = `51dc90fa8bbdbe3c80c5e8adc3f96811677c6cb5`、`main` = `e803ecc3dcaff48973342521c8bd77ecf1bbb353`（fetchで再確認、他者commitなし）。

### P3: 全履歴監査 68 → 69

上の「68」は run 41595136352（HEAD `b83669c`）の値で、最終HEAD `51dc90f` の値ではなかった。両方とも、その時点の実測として残す。

| run | event | HEAD | refs / commits / objects | blocking |
|---|---|---|---|---|
| [41595136352](https://github.com/zawa356/Utteran/actions/runs/41595136352) | push | `b83669c` | 44 / 376 / 3932 | 68 |
| [41596365964](https://github.com/zawa356/Utteran/actions/runs/41596365964) | push | `51dc90f` | 44 / 377 / 3938 | 69 |
| [41596371106](https://github.com/zawa356/Utteran/actions/runs/41596371106) | pull_request | `51dc90f`（仮想merge commitをcheckout） | 44 / 378 / 3939 | 69 |
| ローカル再現 | — | `51dc90f`（scanner = `51dc90f`の`tools/public_history_scan.py`） | 45 / 377 / 3938 | 69 |

特定方法: `51dc90f`と`b83669c`のredacted JSON reportを、(category, detector, scope, object, commit, path)の集合で比較した。

- 追加されたのは1件だけで、`personal-data/email-address`、scope `commit-message`、commit `51dc90f`。
- 一致値をmaskして確認すると、そのcommit自身の`Co-Authored-By`行のbot（noreply）アドレスだった。
- 削除されたfindingは無い。

PR runのcommits 378は、push runより1多い。GitHubの仮想merge commit（`refs/pull/34/merge`）を含むためと考えられる。

- fetch時点のmerge ref（parents `e803ecc3` / `51dc90f`）のmessageは「Merge … into …」だけで、アドレスを含まない。そのためblockingは同じ69になる。
- ただし、runで使われたmerge commitのSHAとfetch時点のSHAが同一かは確認していない。

再現手順:
`git checkout 51dc90f && uv run --no-sync python tools/public_history_scan.py --json <out>`を実行し、
同じ手順で作った`b83669c`のreportとfinding集合を比較する。総数はrefs・objectsの差で変わるため、合否には使わない。

### P2: 新規履歴gate

原因: `privacy-worktree`は最終treeしか見ない。`privacy-history`はreport onlyで、常に成功していた。
そのため、commit Aで漏洩してcommit Bで削除した場合でもCIが緑になった。

是正:
- `tools/public_history_delta.py`を追加した（定義は[testing-and-operations.md](testing-and-operations.md#新規履歴privacy-gateprivacy-history-delta)）。
- 範囲は`git rev-list <candidate> --not <trusted base> 51dc90f`。cutoverは`tools/public_history_baseline.json`に固定した。
- CIに独立job `privacy-history-delta`を追加し、12 jobにした。
- 合計件数の比較・前回値との差・全履歴への`--fail-on-findings`は使っていない。

回帰テスト`tests/test_public_history_delta.py`（17件、tmp git repo、合成値は実行時に組立て）:

- 既知履歴のみなら成功
- A追加・B削除（worktree 0）で失敗
- 合成のアドレス、user path、`.env`、未レビューmediaは、追加後に削除しても失敗
- 総件数が同じでも、既知findingの削除と新規findingの追加なら失敗
- 既知の値を別pathへ置くと失敗し、既知行が残ったままの編集は成功
- 厳密なbot trailerは成功し、本文中や未登録co-authorのアドレスは失敗
- 秘密値のない連続commitと、信頼済みmainのmergeは成功
- cutoverは固定され、それより後のcommitは必ず検査される
- base・candidate・cutoverの不在、空範囲、shallow clone、壊れたbaselineはexit 2
- 失敗時の出力とJSONに、合成値やpathが含まれない
- event解決（PR、branch push、main push、`before`が全0、未対応event）
- reviewed WAVとTypeScriptの例外が維持される

mutation確認: 次の2つの改変で、テストが失敗することを確かめた。
- 候補commitだけを走査するよう改変 → 8件失敗
- `noreply`を含むアドレスを全面的に許可するよう改変 → 1件失敗

実repoでの実行（ローカル、`3f692a6`）: candidate `3f692a6`、trusted base `e803ecc3`、cutover `51dc90f`、new_commits 1、**new_blocking 0**。
push event形式（branch push → `origin/main`）でも同じ結果だった。

### レビュー修正後のCI（HEAD `8e2e61b`、12 job）

| run | event | worktree blocking | **new-history gate** | full-history report |
|---|---|---|---|---|
| [41617288565](https://github.com/zawa356/Utteran/actions/runs/41617288565) | push（branch → trusted base = `origin/main` `e803ecc3`） | 0 | candidate `8e2e61b`、cutover `51dc90f`、new_commits 2、**new_blocking 0** | refs 44 / commits 379 / objects 3950、blocking 71 |
| [41617297676](https://github.com/zawa356/Utteran/actions/runs/41617297676) | pull_request（base.sha `e803ecc3`、head.sha `8e2e61b`） | 0 | new_commits 2、**new_blocking 0** | report only |

両runとも12/12 job success。Linux/Windows pytest、契約3.11/3.12、Ruff、mypy（linux/win32）、schema 8種、lock、BOM、PowerShellは退行なし。

full-historyの69→71は、ローカルでfinding集合を比較して特定した。

- 追加は2件で、新commit `3f692a6`と`8e2e61b`のcommit messageにあるbot trailer。削除は0件。
- これは新規履歴gateが例外として扱う、厳密なbot trailerと同じものである。
- 総数はこのようにcommitの追加ごとに増えるので、合否はnew-history gateで判定する。
- このdocs commit以降の値は、PR本文に記録する。

## PR #34追加レビュー修正: placeholder判定（P2、[review 5751453149](https://github.com/zawa356/Utteran/pull/34#pullrequestreview-5751453149)）

開始時: branch HEAD = PR head = `c029a39d52bb625cca174e46371a898c6575a21e`、`main` = `e803ecc3`（fetchで再確認）。

**原因:** `_is_placeholder()`は、一致値に`user`・`test`・`example`等が**部分文字列として**含まれるだけで非blockingにしていた。

- Windowsのuser-path検出式は常に固定部分`Users`/`UserData`を含むため、実在するWindowsユーザーパスはすべて免除されていた。
- delta gateも同じ関数を使っていたため、同じ見逃しがあった。

**是正:** detector別に値全体で判定する（[testing-and-operations.md](testing-and-operations.md#placeholder判定3つのprivacy検査で共通)）。

**回帰テスト:**

- 追加した12件（`test_public_history_scan.py` 10件、`test_public_history_delta.py` 2件）は、修正前のcodeでT01〜T11がすべて失敗し、修正後はすべて成功した。
- 旧substring規則に戻すmutationでは11件が失敗した。
- T09/T10（Windowsパス・marker語入りtokenの追加→削除）では、worktree 0件でもdelta gateがexit 1を返す。
- 既存のdelta 17件、scan、E2E 11件はそのまま成功した。

**worktreeへの影響:** 新規則で、tracked fileの9件がblockingになった。値は表示せず、形状と文脈で分類した。

- 8件は合成fixture（`synthetic`・`person`・`TestUser`・`.invalid`・`…DummyToken…`）と、scanner自身の正規表現。値を実行時に組み立てる方式に変えた（runtime値は不変）。
- `ユーザー確認事項.md`の`<名前>`は、segment境界を整えた。
- **1件は実在の個人のWindowsユーザーパス**（`AISTATE.md`、account名がrepository ownerの名と一致）。現行treeでは`C:\Users\<user>\`に置き換えた。値はcommit・CI・PRに書いていない。
- 結果: worktree blocking 0。

**full-history（同じHEAD `8b6fab0`、`git gc`後、旧scanner＝`c029a39`版と新scannerで比較）:**

| scanner | refs / commits / objects | blocking |
|---|---|---|
| 旧（substring） | 45 / 383 / 3982 | 75 |
| 新（typed） | 45 / 383 / 3982 | 1931 |

旧scannerでの75は、[CI 41618806086](https://github.com/zawa356/Utteran/actions/runs/41618806086)の72に、新commit 3件のbot trailerを足した値である。

増分はblob・diffの出現箇所単位で数えている。新たにblockingになった**値**（keyed hashで比較、表示なし）は**7種類だけ**だった。

- 実在の個人パス1種（`AISTATE.md`の73版）
- 合成fixture 6種（上記のfixture群と、`ユーザー確認事項.md`旧版の`<名前>`）
- diff scopeの増分は、すべてこの7種と同じ値だった。commit-messageは65→65で変化なし。
- 未分類のfindingは無い。

**要owner判断（値は書かない）:**

実在の個人パスはcommit `f6b6c09`（2026-08-26）で追加され、それ以降の公開履歴に残っている。

- 認証情報ではないため、失効させる対象は無い。個人名を含むアカウント名が公開されていることになる。
- reviewed cutoverより前のため、new-history gateの対象外（設計どおり）。
- history rewriteをするか、そのまま受容するかはowner判断とし、本作業では行わない。

**new-history gate（ローカル、`8b6fab0`）:** trusted base `e803ecc3`、cutover `51dc90f`、new_commits 6、**new_blocking 0**。

- 伏せ字にしたcommitは削除だけなので、新規blobに値は無い。
- cutoverとbot address一覧は変更していない。

CIの実測はPR本文に記録する。

## 未実施（確認済みと扱わない）

Windows実機・GPU・native build・実モデルDL・長時間音声・installer/portable（`verify_uninstaller.ps1`）・
WebView2/実ブラウザSSE・実process kill・Windows ACL・実機でのbackup restore・実smoke evidence provider。
