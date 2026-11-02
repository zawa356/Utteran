# B8 検証記録（2026-11-02、ローカルLinux container）

基点main `4fbb4d1`。同一環境で修正前mainを先に実行して比較した。GitHub Actionsの結果はPR本文・
最終報告に記載する（本ファイルはpush前のローカル実測）。

## 実測

| 項目 | 修正前main | B8 |
|---|---|---|
| `pytest -m "not requires_model"`（Python 3.11.18） | 1122 passed / **2 failed** / 9 skipped | 1163 passed / **2 failed（同一node）** / 9 skipped |
| 契約matrix（13ファイル、3.11.18） | — | 569 passed / 1 skipped |
| 契約matrix（13ファイル、3.12.3、別venv） | — | 569 passed / 1 skipped |
| Ruff check / format | — | 成功 / 193 files formatted |
| mypy strict | 既知9件（`processes.py`、#27） | 同じ9件のみ |
| offline schema `--check`（b1/b3/b4/b5/b6/b7/b8） | — | 全成功（b6/b7はReasonCode追加で再生成） |
| `uv lock --check` | — | 成功（root version行のみ変更） |
| wheel build → isolated install（3.11.18 / 3.12.3）→ 実import・v4 store作成 | — | 成功（`0.1.37`、`TARGET_VERSION=4`） |
| PowerShell BOM | `scripts/verify_uninstaller.ps1`欠落（既知） | 同一（新規.ps1なし） |
| public scan worktree | blocking 3件（既存media fixture/設定、既知） | 同一3件 |

既存の2 failed（`test_gui_processes.py::test_profile_manifest_detects_*`）はmainでも同じく失敗する
（このcontainerにWindows PowerShellがなくprofile manifestが生成されない環境要因）。B8起因の失敗はない。

## 追加テスト（41件）と指示書§4 B8の対応

| 区分 | テスト（`tests/test_b8_setup.py` / `test_b8_api.py`） |
|---|---|
| 1 純Plan | catalogとcoreの一致、新規CUDA（副作用ゼロ・FS不変・process 0・hash固定・null見積）、CUDA→Intel（cuda保持、共有モデルverify、cuda向けcommand 0）、catalog見積、未対応8組合せ、platform/rebuild/diarization/gated DAG |
| 2 Approve | revision 412・hash・inventory stale・未知同意・再承認・期限切れ、同意未選択・credential欠落、key再利用/別kind |
| 3 Atomic accept | replay同一（status/body/ETag/Location）、approval再使用拒否、commit前故障2箇所で全表不変、readonly、同key 6並列（別connection）で1件、別processからreplay照会 |
| 4 Schema | v1(B6)/v3(B7)→v4、B6/B7のv4拒否、migration故障でv3維持、未知version 5、旧header+新表=corrupt、本文/journal/replay不変trigger |
| 5 Stepper | spawn前intentの観測、journal順序、exit 0でもverify失敗はfailed、failure→retry（venv再実行なし）、inventory変化→waiting_user→resumeは409・cancel可、同意待ち→resume→新attempt成功、runtime credential欠落、queued/running cancel（境界停止・lease保持）、終了未確認→slot保持→recovery（alive=required、gone=interrupted）、intent後crash=recovery_required、spawn後失敗 |
| 6 排他 | 非B7/B8先頭で追い越さない、Job/Setupで同じheavy lease、別connectionで2つ目不可、pause/holdで停止（setupはqueuedのまま） |
| 7 Adapter | 固定argv（PowerShell 5.1互換）、`shell=False`の実子process、出力非保持（sentinel）、既定appにroute/import/DBなし |
| HTTP | 作成→replay→409→approve（428/412/201）→開始→再送→再利用409→一覧→queue→dispatch→成功→force 409、固定エラー、DELETE、Origin、旧wizard 409 |

## 未検証（fakeで通過したものを実環境成功と扱わない）

- 実Windowsでの`setup.ps1`実行、実`uv sync`、実モデルdownload/verify、実smoke、GPU。
- Windowsの`ProcessChildRunner`（Job object経由のpopen_factory）と実PID start identity。
- PR CI（Linux/Windows/3.11/3.12）の結果はpush後に追記。

## PR #33レビュー修正の検証（2026-11-02追補）

本節は追補であり、上記の提出時記録は当時の事実として保持する（提出時のテストはdual-pipe deadlockを検出していなかった）。

| 項目 | 修正前（`8264ab1`のrunner） | 修正後 |
|---|---|---|
| `tests/test_b8_review_fixes.py`（16件、実OS subprocess、Python 3.11） | **10 failed / 6 passed**（deadlockによる期限切れ4、status channel/不正JSON/複数JSON 3、異常終了・reader失敗・子孫pipe 3） | **16 passed** |
| 既存B8（`test_b8_setup.py`・`test_b8_api.py`、41件） | passed | passed |
| 追加1件（JSONでない上限超過行の後の有効status） | `6b5aebf`のrunnerで失敗 | passed（計17件） |

CIで見つかった追加不具合: `6b5aebf`のWindows CI（[run 41416498870](https://github.com/zawa356/Utteran/actions/runs/41416498870)、
[run 41416490232](https://github.com/zawa356/Utteran/actions/runs/41416490232)）で`test_both_channels_drained_concurrently`が失敗。
Windowsの子はtext modeで`\n`を`\r\n`に変換するため65,536文字の進捗行が上限+1 byteとなり、「上限超過行は常に
status曖昧」とした規則で有効なJSON行まで破棄していた（Linuxでは境界内で再現しない）。JSONに見える上限超過行
だけを曖昧扱いにする修正と、OSに依存しない回帰ケースを追加した（修正前`6b5aebf`で失敗、修正後成功を確認）。

再現条件: stderrへ4 MiB書いた後にstdout出力、stdoutへ4 MiB後にstderrへ4 MiB、64 KiB行を両channelへ交互に64回、
改行なし32 MiB×2、stderrのみの偽status、複数・不正・上限超過JSON、exit 7、`on_spawn`例外、reader例外（終了後／生存中）、
pipeを保持する孫process。各ケースは60秒の期限付きthreadで実行し、期限切れ時は子をkillして失敗とする。
全体の実測は[B9 validation](../b9/validation.md)の追補を参照。
