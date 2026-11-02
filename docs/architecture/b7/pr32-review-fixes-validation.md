# PR #32 レビュー修正と検証（P2-1〜P2-3、P3）

2026-11-02 JST。開発版0.1.36（未リリース、version変更なし）、PR #32 **Draft・未マージ**。
修正前の基準HEAD: `dd30a8be0dff6fca08090d668002ebafcdc40e88`（PR head・remote branchと一致、
他者の追加commitなし）、base main `3fd187dff64e83291253bd0d0bf4ba74f043664a`。
修正commit: `edf3f5a42fd504f616eab7e759dfd1fd10cf8678`（コード・テスト）＋本文書のcommit。
rebase・force push・Ready化・merge・tagは行っていない。

## 根本原因と修正

### P2-1 Runtime間で旧Queueの排他ownerを共有

- 原因: `JobRuntime`が固定文字列`OWNER = "utteran-v1-jobs"`で`close_for`し、`close_for`は同一ownerの
  再入を成功扱いにしていた。同じ`OperationQueue`を持つRuntime Bも「所有」でき、Aの`deactivate`
  （`reopen(OWNER)`）でBが稼働中のまま旧Queueが開き得た。
- 修正:
  - Runtimeごとに`secrets.token_hex(32)`の取得tokenを生成し、`close_for(token)`→`reopen(token)`で同一tokenを使う。
  - `close_for`は**非再入**（同一tokenでも2回目は拒否）で、所有者の有無とitem状態を同じlockで検査・更新する。
    tokenは32文字以上を要求する。
  - `reopen`は一致するtokenのみ成功し`bool`を返す。他tokenでは何も変えない（比較は`compare_digest`）。
  - `activate`は、既に所有されていれば`legacy_queue_owned`、旧queueにwaiting/runningがあれば`legacy_busy`で拒否する。
  - store open・backup・recover・admission/dispatcher構築・dispatcher起動のどこで失敗しても、
    **自分が取得したtokenだけ**を解放し、`state`/参照は`inactive`/`None`のまま保つ（dispatcherは停止）。
  - `deactivate`はactive/unknown attemptがあれば従来どおり`active_attempt`で拒否し、所有を保持する。
    所有tokenで解放できない場合は`ownership_lost`で拒否する（旧queueは開かない）。
  - 既存の呼出し箇所は`job_runtime.py`のみ（監査済み）。旧Queueの`submit/cancel/worker`は不変。
- 範囲: これは**同一Pythonプロセス内**の旧Queue排他である。別OSプロセス間で共有DBを使う場合の
  実行権は、従来どおりSQLiteの単一heavy slot（部分UNIQUE index＋lease PK）とfencing tokenで保証する（緩めていない）。

### P2-2 同一Idempotency-Keyの並行POSTが`resolution_consumed`になり得る

- 原因: `lookup_batch`がmissしてから先行要求がcommitするまでの間に、同key・同bodyの後続要求が
  token予約で`resolution_consumed`（409）になっていた。旧テストもこれを許容していた。
- 修正（`JobAdmission._single_flight`、DB schema変更なし）:
  - 要求の識別には、既存のscope（endpoint+key）と、installation鍵でHMACしたrequest digestを使う。
  - 処理順: 永続replay照合 → プロセス内のin-flight表（scope単位）→ leaderは1件だけで、`freeze`と`accept_batch`を実行する。
  - followerは同digestならleaderの完了を`Event`で**有界待機**（既定10秒、`inflight_wait`）し、待機後に必ず
    永続replayを再照会して同じJobBatch/status/ETag/Locationを返す。
  - leaderがcommit前に失敗した場合、tokenはreleaseされ、followerが改めてleaderになる（commitは1回だけ）。
  - 期限超過時は**`503 submission_in_progress`（retryable）**を返し、先行結果を偽装しない。
    後で同じkeyを再送すると永続replayが返る。応答bodyにtoken・path・本文は含めない。
  - 処理中に同key・別bodyを受けたら**409 `idempotency_conflict`**を返す（待機しない）。
  - tokenには予約者（scope+digest由来のHMAC）を記録する。
    - 別admission instance（別connection）が同じtoken registryを共有する場合: 予約者が同一要求であれば、
      replayの出現を間隔50msの`Event.wait`で有界に再照会する（busy loopなし）。
    - **異なるkey**による同token再利用: 従来どおり409 `resolution_consumed`。
    - 別session: 従来どおり`resolution_invalid`。
  - tokenの消費（consume）は`accept_batch`のcommit成功後に限る。それ以外の例外ではreleaseする。
    commit後の応答喪失は永続replayで復元される。
  - `rerun()`も同じ`_single_flight`を経由する（rerun元snapshot・`rerun_of`の関係は不変）。
- プロセス間の扱い: resolution tokenはプロセス内メモリにあり、同一GUIのsessionに束縛される。別OSプロセスでは
  tokenを検証できず`resolution_invalid`（409）となる。commit済みの要求は別プロセスでも永続replayで同一応答になる。
  未commitの要求をプロセス間で同期することはできないが、**二重登録は起きない**（tokenは一方にしか存在しない。
  加えて`accept_batch`はtransaction内でreplayを再照合する）。

### P2-3 `GET /api/v1/jobs`のcursorがページ間snapshotを固定しない

- 原因: `j1.<rowid>.<mac>`は前ページ終端位置だけを保持していた。後続ページは現在の状態を再取得するため、
  ページ間のstatus/attempt/result変化や新規Jobが混ざっていた。
- 修正（推奨案の「プロセス内materialized snapshot」、**DB layout v3は不変**）:
  - 最初のGETでは、1つのread transaction内で全B7 Jobの**allowlist済み公開`Job` DTO**・順序（rowid）・
    collection revisionを生成する。2ページ以上になる場合だけ、immutableなsnapshotとして登録する。
  - 後続ページはそのsnapshotから切り出すだけにした。そのため`revision`/ETagは全ページで同じ値になり、
    既存cursorに新規Jobや状態変化は混ざらない。`GET /jobs/{id}`は従来どおり最新状態を返す。
  - cursor `j2.<snapshot 32hex>.<offset>.<page size>.<HMAC-SHA256>`: MACはsnapshot ID・位置・page size・
    閲覧者scope（GUI session credentialのSHA-256）・filter（現行は`filter=all`のみ）に束縛する。
    private snapshot・Prompt・pathは含めない。改ざん・他session・page size変更・旧`j1` cursorは
    400 `cursor_invalid`になる。
  - 上限: TTL 300秒、registry 16件、1 snapshot 10,000件、registry合計50,000件。
    - 1 snapshotの上限を超える一覧は**黙って欠落させず**、503 `list_snapshot_too_large`で拒否する。
    - registryが満杯なら最古のsnapshotを明示的に削除し、そのcursorは409 `cursor_expired`になる。
    - TTL経過・再起動（registryは消える）でも409 `cursor_expired`になる。新しい1ページ目のGETで新しいsnapshotを作れる。
  - Phase A `contracts.md`の「filter付きsnapshot pagination、期限切れ409」に一致させた。OpenAPIは全routeに
    409/503を定義済みのため、変更していない。`JobList` contractとoffline schemaも不変。

### P3 Windows ctypes宣言

- `GetProcessTimes(HANDLE, 4×POINTER(FILETIME)) -> BOOL`、`GetExitCodeProcess(HANDLE, POINTER(DWORD)) -> BOOL`、
  `CloseHandle(HANDLE) -> BOOL`の`argtypes`/`restype`を明示した（`OpenProcess`は既存のまま）。
  これで64-bit HANDLEが暗黙の`c_int`へ切り詰められることはない。
- 判定ロジックは`observe_windows(api, pid)`として純関数化し、Linuxでも64-bit幅のhandleを持つfake APIで検証する。
  - OpenProcessが`ERROR_INVALID_PARAMETER`のときだけGONE（PID不在を証明できた場合）とする。
  - アクセス拒否、GetProcessTimes/GetExitCodeProcessの失敗はUNKNOWN。
  - handleは`finally`で必ず閉じる。**CloseHandleの失敗は観測結果を変えない**（判定はclose前に確定済みで、
    失敗は記録しない）。
- `processes.py`の既知mypy 9件（#27）は対象外。新規コードはLinuxと`--platform win32`のmypyで0件。

## 再現テスト（`tests/test_b7_review_fixes.py`、修正前→修正後）

修正前（`dd30a8b`、Linux 3.12）: **19 failed / 2 passed / 1 skipped**。
- 2 passedの内訳: leader rollback（未提供の`inflight_wait`を使わない）と、別key・別sessionのtoken拒否（従来から正しい挙動）。
- 1 skippedはWindows専用テスト。
- failedのうち、二重Runtime・並列活性化・失敗時解放・rollback保持・同key待機・503・別body 409・共有registry・
  rerun・ページ間snapshot・cursor束縛は、旧コードの挙動そのものを再現した失敗。
  TTL/容量/上限/Win32ロジックの各テストは、新API（`inflight_wait`・`list_*`・`observe_windows`）が存在しないことによる失敗。

修正後: 同ファイル **21 passed / 1 skipped**（Windows専用、Windows CIで実行）。追加した主な検証:

| 指摘 | テスト |
|---|---|
| P2-1 | 同Queueの2 Runtime（Bは拒否、Bのshutdownでも開かない、A停止後はB可）、token非再入・他owner解放無効、`Barrier`による6並列activateで活性は最大1件、store/backup/recover/dispatcher各段の失敗で自分の取得だけ解放、active lease時のrollback拒否で排他保持 |
| P2-2 | leaderをDB transaction内（`batch_before_replay`）で`Event`停止しつつ6並列で同key・同body → 全件同一JobBatch/headers・非replayは1件・Job/Outbox/replay件数は1回分。follower期限で503（行数0）→release後に同一replay。leader rollback→followerが1回だけcommit。in-flight中の別body 409。同token別key 409（in-flight中・commit後）・別session拒否。別connectionのadmission 3件が共有registryで全件replay。rerunの同key 4並列で1件 |
| P2-3 | 3 Job・1件/ページで1ページ目取得 → 1件目をqueued→running→succeeded（attempt追加）・新規Job投入 → 2・3ページ目もqueued/attemptなし・revision同一・重複/欠落なし。新規GETは4件・最新状態。改ざん/他session/page size変更/旧j1 cursorは`cursor_invalid`。TTL（fake clock）・容量（最古evict）・再起動で409 `cursor_expired`、上限超過で503。cursor/一覧にsentinel・path・ファイル名なし |
| P3 | 64-bit handleの受け渡しとclose、GONE/UNKNOWN判定（fake API、全OS）。Windows CI: 宣言の`argtypes`/`restype`、現在process ALIVE、終了済み、不正PID、作成時刻違いPID_REUSED |
| HTTP | in-flight中の同key POSTは503（`retryable: true`、token/pathなし）。release後の再送は202で同一body・Location・ETag。一覧は1件のみ（クライアントtimeoutはrollbackでもcommitでもない） |

既存`test_concurrent_resends_create_one_batch`の`resolution_consumed`許容は削除し、全件同一受理・非replay 1件へ強化した。

## 実測（Linux、container、Python 3.12.13、修正commit `edf3f5a`）

| 検査 | 修正前 `dd30a8b` | 修正後 |
|---|---|---|
| `uv run --no-sync pytest -m "not requires_model"` | 1100 passed / 2 failed / 8 skipped | **1122 passed / 2 failed / 9 skipped** |
| failed node | `tests/test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`、`tests/test_gui_processes.py::test_profile_manifest_detects_venv_move_without_disabling_profile` | **同一2件**（新規0） |
| skip増分 | — | +1: `test_win32_declarations_and_real_processes`（Windows専用） |
| B7 3ファイル | 76 | 98 passed / 1 skipped |
| wheel（`utteran-0.1.36-py3-none-any.whl`、独立venvに`[gui]`+test依存、installed packageで契約/B5/B6/B7） | — | 3.11: 299 passed / 1 skipped、3.12: 299 passed / 1 skipped |
| mypy | 既知9件 | 同一9件（`--platform win32`で`process_verifier.py`は0件） |
| Ruff check / format、schema `--check`（B1〜B7）、`uv lock --check` | — | 成功（schema・lock・version不変） |

## GitHub Actions（最終HEAD）

途中HEAD `c5eb7b9`の契約matrix（3.11、run 41402575674）で`test_list_jobs_cursor_is_stable_and_signed`が1件失敗した。
原因はテストの改ざん方法で、cursor末尾を常に`"0"`へ置き換えていたため、MAC末尾がもともと`"0"`だと
（確率1/16）改ざんにならなかった。新しい`j2`形式で末尾が64桁hexのMACになり、この偶然が表面化した。
末尾を必ず別の文字へ変えるよう修正し（検査は弱めていない）、ローカルで30回連続で通過を確認した。
同じ形のテストは他にない（`grep`で確認）。

CI結果は本文書のcommit後の最終HEADで採取し、PR #32本文の「Review fixes」節に記録する（最終HEADより前の
成功を最終HEADの合格とはみなさない）。既知の#27（mypy 9件・Inno Setup前提）により、`linux-quality`と
`windows-tests` jobは引き続き赤になる見込みであり、**緑とは表現しない**。

## 未解決ゲート（変更なし）

実smoke provider、Windows実機（ACL・Job Object・portable・installer・GPU・長時間推論）、private DBの
暗号化なし・無期限保持（retention・完全削除）、#27。B8/B9・UI刷新は未着手。
