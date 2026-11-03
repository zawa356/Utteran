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
- **history scanのdiff記号誤検出**: `+@app.command(...)`等のデコレータ追加行を、diff記号`+`をlocal partとしてe-mail扱いしていた。
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
| 本ブランチ | 下記CI参照 | media（tracked fixtureの同一blob・TypeScript）は非blocking化。残りは下記分類 |

残存分類（値は表示せず、ハッシュ・種別のみで判定）:

- commit-message 45件: すべて`noreply`系botの`Co-Authored-By` trailer。
- commit-diff/blobのe-mail: 合成値（`.invalid` TLD、`tests/test_private_history_match.py`）。デコレータ誤検出5件は修正で解消。
- absolute-user-path 4件: `tests/test_b9_events.py`の旧版にあった合成sentinel（現行は実行時組立てで非該当）。
- ローカルcloneだけに存在するunreachable tree経由のWAV（同一blob、CIのcloneには無い）。
- **実在の秘密値・実ユーザーのアドレス・実録音と判断したものは無い。** history rewriteは行っていない（方針として実施しない）。

## GitHub Actions

| run | HEAD | 結果 | 備考 |
|---|---|---|---|
| [41591215254](https://github.com/zawa356/Utteran/actions/runs/41591215254) | `dd3436c` | **success（11/11 job）** | 独立job化直後。PID test 20/20、PowerShell起動: setup -List 0、run 1、start 0、launch-python -CheckOnly 1、reset-env -WhatIf 0 |
| [41592415226](https://github.com/zawa356/Utteran/actions/runs/41592415226) | `0dc0a01` | **failure（10/11）** | Windows pytest 1227 passed / **1 failed** / 9 skipped: `test_b6_control.py::test_snapshot_is_consistent_under_concurrent_writes`（`store_busy`）。新E2EはWindowsでも成功 |
| [41593297070](https://github.com/zawa356/Utteran/actions/runs/41593297070) | `b535e7d` | **success（11/11 job）** | Windows: PID test 20/20、B6 snapshot test 10/10 |
| 最終 | 下記 | — | PR本文とIssue #27コメントに最終HEADのrun URLを記載 |

PID-file競合とB6 starvationは**間欠事象**のため、1回の成功で解消と結論しない。Windows CIで毎run反復し、証拠を蓄積する。

## 未実施（確認済みと扱わない）

Windows実機・GPU・native build・実モデルDL・長時間音声・installer/portable（`verify_uninstaller.ps1`）・
WebView2/実ブラウザSSE・実process kill・Windows ACL・実機でのbackup restore・実smoke evidence provider。
