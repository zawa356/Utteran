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
