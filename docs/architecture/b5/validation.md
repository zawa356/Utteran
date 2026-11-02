# B5 検証記録

2026-11-02 JST。基点B4 `f6c2a9546e1ee9b780f8c1a098f7bcdfc7096314`、B5 branch
`feat/b5-preset-policy-editing-api`、version 0.1.34。未マージ・未リリース。

## 環境とbaseline

Linux Python3.12.14、lockからexportしたdev/gui依存を独立venvへ導入。
基点を別worktreeに展開し、同じlock依存で870 passed / 3 failed / 8 skippedを再現（26.39秒）。
最初の環境準備時にlock未固定依存で得た結果は比較根拠に採用せず、固定後のbaselineを使う。

| 検査 | 結果 |
|---|---|
| B5専用 | 73 passed（最後の2件は不正Unicode/非有限数値の安全な400） |
| Linux全体モデル不要 | 943 passed / 既知3 failed / 8 skipped、26.51秒 |
| 増分・node比較 | +73 passed、failed/error/skipped集合は基点と完全一致 |
| Python3.11.17、独立wheel install、B1/B2/B3/B4/B5関連 | 470 passed、15.22秒、editableなし |
| Ruff lint / format | 成功、165 files |
| strict mypy | 93 source files、既知Windows ctypes9件のみ（基点86 filesも同じ9件） |
| B1/B3/B4/B5 schema --check | 成功、B5は3.11 wheelでも一致 |
| uv lock --check | 成功、root version 0.1.33→0.1.34のみ |
| wheel | utteran-0.1.34-py3-none-any.whl成功、新package/preview/routerを収録 |
| git diff --check | 成功 |
| 公開worktree scan | 331 tracked / 80 findings / blocking 2、既存合成WAV2件のみ、新規blocking0 |
| 文書監査 | docs-audit.md参照、CI/PR/Issue記録は提出時に更新 |

比較の安全な機械可読記録: [test-comparison.json](test-comparison.json)。
既知failed node:

- test_devices.py::test_timeout_kills_probe_process_tree
- test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies
- test_gui_processes.py::test_profile_manifest_detects_venv_move_without_disabling_profile

mypyはprocesses.pyのWinDLL/WinError/get_last_errorのみ。修正・skip・基準golden変更なし。
PID-file生成競合、Windows Inno Setup未配置、lock全体hashによるprofile非互換も別課題。
Starlette TestClientのhttpx deprecation warningは既存依存側の通知。

## 試験内容

immutable resource history/tombstone、2-client CAS、同時create、restart後create/preview replay、
同key異body、snapshot中の更新/削除、kind混同、raw private値のerror/log非漏洩を検査。
rollback/full/readonly/permission/DB busy/未知schema/破損/初期migration失敗は合成障害注入。
別processをappend後commit前にos._exitで終了しhot journal回復→再送を確認。
受理後response喪失相当の別interpreterで同resource/status/Location再現を確認。
実際の電源断や物理disk満杯を行ったとは主張しない。

leaf登録/型/親子・重複/配列・map/null/auto/false/空値/解除、共通追従、
backend/ref変更時の再検証、元DTO/別file不変、人数/GenAI/timestamp/引数衝突、
provenance、B2 worker未対応と保存可能性を分離する。
認証/Host/Origin/cookie CSRF/容量/JSON/unknown fields/schema、opt-in無効の従来動作を検査。
validation中のPopen禁止と軽量import境界、B2 CLI19ケース/hash4構成/export5形式/resume/force、
B3 IPC/cancel、B4 ready/unknown/blocked/auto/fixedは関連・全体回帰で維持。

## 再現コマンド

通常はlock同期済み環境で次を実行する。今回の独立venvでも同じmodule/対象を実行した。

```console
uv export --frozen --extra dev --extra gui --no-hashes --no-emit-project --output-file requirements.txt
uv lock --check
python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json --check
python -m utteran_contracts.worker_schema --output docs/architecture/b3/worker.schema.json --check
python -m utteran_contracts.capability_schema --output docs/architecture/b4/capabilities.schema.json --check
python -m utteran_contracts.preview_schema --output docs/architecture/b5/preview.schema.json --check
ruff check src tests tools
ruff format --check src tests tools
mypy
pytest -m 'not requires_model' --junitxml=b5.xml
uv build --wheel
```

3.11 wheel subset: test_b5_settings、contracts/schema/boundary、config_adapter、application/golden、
worker_protocol/runtime/client、capability_inventory/bridge。wheelのimport先がsite-packagesであることを確認。

## CI / 未検証

実装HEAD `f7f0f11d164e0063e7d18bc3a94d1a0d094968e9`に対する
[push run 41380126274](https://github.com/zawa356/Utteran/actions/runs/41380126274)と
[PR run 41380161152](https://github.com/zawa356/Utteran/actions/runs/41380161152)を確認。
[Draft PR #26](https://github.com/zawa356/Utteran/pull/26)、base B4。最終追補は文書のみ。

| CI段階 | 実測 |
|---|---|
| Windowsモデル不要pytest | 951 passed / 3 skipped、54.52秒（push run） |
| Windows PowerShell構文 | 成功 |
| Windows汎用PowerShell起動 | 既知Inno Setup compiler未配置で失敗 |
| contracts Python3.11 | 350 passed、11.95秒、schema checks成功（push run） |
| contracts Python3.12 | 350 passed、14.40秒、schema checks成功（push run） |
| linux-quality | lock/4 schema/Ruff成功、既知mypy9件で停止、後続pytestはskip |

Windows基点878 passedから+73、skipは既存3件のみ。PID-file競合の新規失敗はこのrunで観測なし。
workflow全体は既知問題で赤。Linux943 passedはローカル独立実行であり、CI後続の推測ではない。
品質ゲートが全て緑になったとは報告しない。既知問題は別CI修正PRの対象として維持。
Windows ACL/installer/portable/WebView2実受入、GPU/native推論、quality/Prompt compiler、
実電源断/物理disk failureは未検証。mock成功と実機成功を混同しない。
全DoD無条件合格・release可能とは報告しない。Draftレビュー待ちで停止する。
