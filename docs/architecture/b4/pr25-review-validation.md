# PR #25 P2/P3レビュー修正・検証

2026-11-02。開始HEAD `9b99a6fe7e2bd44017ea2db50e76d8ddd1dd0e84`、base B3
`5ef7bab7ba81cab7afc1fe23b85abc8da399ec1b`、OPEN/Draftを再確認して同branchへ修正。

## P2

`eb14c6c`でsmoke routeをverified/unverified/mismatchへ分離。
新鮮なpassedだけを照合し、実測欠落はsmoke_route_unverifiedでunknown、矛盾は
smoke_route_mismatchでfailed。他のfailedがあればcandidate blockedを優先する。
失効したsmokeをroute証拠で復活させず、元deployment/要求/identityを変更しない。

recipeごとの必須native/encoder/decoder実測は[recipes](recipes.md#pr-25-p2-recipe別の実測要件)。
backend-defaultは観測不要、selected-deviceはidentityの明示CPU/GPU/NPUへ具体化する。
gpu/openvino-gpuを安易に同一視しない。GPU等にはengine別の実測device indexも要求する。
providerの実測がないindexを要求値からコピーしない。複数engineの物理device対応はproviderが
hardware/runtime fingerprintへ拘束する。標準collectorはsmokeを作らない。

成功fixtureは明示的な**合成**route/index実測へ更新。全passed・実route unknownはready不可。
未観測CUDA/片方だけのCUDA/native欠落・矛盾/GenAI device/異index/freshness/autoとfixed拒否を検査。
P2直後: `uv run --no-sync pytest tests/test_capability_inventory.py -q` = **134 passed**。

## P3

既存B3のProcessSupervisor/build_popen_kwargsを再利用する独立metadata transportを追加。
B3 worker/client/processes自体の変更、汎用process管理の全面改修はない。P3は同PR内で対応し、切出しなし。

- stdoutは**読取中**に8 MiBを検査し、上限超過を固定codeで失敗にする。
- stderrも並行drainして64 KiBを検査。本文は蓄積しない。
- stdinは別threadで送り、blocked writeも同じdeadlineに含める。
- stdout raw bufferは8 MiB以内＋reader一時chunk最大8 KiB。stderrは一時chunkのみ。
  最終bytes化/UTF-8 decode/JSON parseの追加メモリはあるため、RSS全体の上限とは主張しない。
- 超過・timeoutではWindows Job Object/POSIX process groupの子孫を停止し、直接子をwaitして回収。
  leader終了後の子孫pipe保持も検査。pipe/threadの回収を確認する。
- 有効なJSON先頭があっても超過を成功扱いしない。完了判定とlimit通知の競合にも最終error再確認を行う。
- 固定reasonで超過/timeout/exit/UTF-8・JSON不正/IO障害を区別し、要求・出力・raw例外をログへ渡さない。
- 専用環境はOS/path/locale/library/data rootsのallowlist。CliAdapter.environmentを呼ばず、
  HF/cloud/session/proxy credentials、PYTHONPATH/PYTHONHOME、未知変数を継承しない。
  UTF-8/offline/profileの固定値だけを追加する。詳細は[observations](observations.md)。

関連155件はP2 134 + transport 21。実childで256 MiB flood、同時stdout/stderr、上限未満7 MiB JSON、
有効JSON先頭の後の超過、UTF-8/partial JSON/異常終了、無応答、blocked stdin、子孫pipeを検査。
tracemallocでflood時のparent側peakが32 MiB未満であることを検査し、全量capture方式への回帰を防ぐ。
合成secret sentinelが子env/argv/公開結果・例外/logへ漏れないこと、必要OS変数の保持を確認する。
通常GUI importで暗黙起動しないこと、GUI core/heavy import禁止も確認する。

## 最終検証

最終実装コード `e64176be1e68bd511fac226b78c335f4d22ce534` を検証。
P2コミット `eb14c6cdd0bdd9a3a3217832b216157462b50f93`、P3コミットは上記実装HEAD。

| 検査 | 修正後の結果 | 基準との差分 |
|---|---|---|
| Linux Python3.12.14 モデル不要pytest | **870 passed / 3 failed / 8 skipped**、33.16秒 | 新規49成功、failed/error/skip node集合一致 |
| B4 route/inventory | **134 passed** | 合成route/index実測で全recipe、未観測拒否を検証 |
| bridge transport | **21 passed** | 実childの出力/環境/回収/障害検証 |
| Python3.11.17 独立wheel環境、B1/B2/B3/B4関連 | **397 passed**、18.21秒 | editable導入なし、wheel再導入して検査 |
| Ruff lint / format | 成功、157 files | 新規違反0 |
| mypy strict | 既知9 errors、86 source files | processes.pyのWindows ctypes属性のみ、新規0 |
| B1/B3/B4 offline schema --check | 成功、B4は3.11/3.12両方 | B1/B3 artifact bytesに差分なし |
| uv lock --check | 成功 | pyproject.toml/uv.lock差分なし |
| wheel build | utteran-0.1.33成功 | 新transport moduleを収録 |
| 公開worktree scan | 313 tracked files / 78 findings / blocking 2 | 既存合成WAV 2件のみ、新規blocking0 |

Linux既知3件はtest_timeout_kills_probe_process_tree、profile manifest依存判定、venv移動判定。
今回の変更前に独立baselineを取り、JUnit XMLでfailed/error/skippedのnode集合を完全一致と確認した。
scanのblockingはtests/fixtures/benchmark/japanese_reference.wavとdiarization_quality/meeting.wav。
出力/保存はredacted位置だけでmatched値を出していない。git diff --check成功。

3.11は独立環境にlockからexportしたdev/gui依存と新wheelを導入し、下記関連subsetを実行:

```console
python -m pytest tests/test_capability_inventory.py tests/test_capability_bridge.py tests/test_contracts.py tests/test_contracts_schema.py tests/test_contracts_boundary.py tests/test_config_adapter.py tests/test_application.py tests/test_application_golden.py tests/test_worker_protocol.py tests/test_worker_runtime.py tests/test_worker_client.py -q
```

[PR Actions run 41376627021](https://github.com/zawa356/Utteran/actions/runs/41376627021)
は実装HEAD e64176bに対して実行:

| CI段階 | 結果 |
|---|---|
| Windowsモデル不要pytest | **878 passed / 0 failed / 0 errors / 3 skipped**、60.58秒 |
| Windows PowerShell構文検査 | 成功 |
| Windows汎用PowerShell起動検査 | 既存verify_uninstaller.ps1のInno Setup compiler未配置で失敗 |
| contracts-python-compatibility 3.11 | **277 passed**、11.09秒、B1/B4 schema check成功 |
| contracts-python-compatibility 3.12 | **277 passed**、11.93秒、B1/B4 schema check成功 |
| linux-quality | lock/schema/Ruff成功。mypy既知9件で停止、後続pytestはskip |

Windowsのpassedは基準829から49増、skipは既存3件のみ。
Windowsでもflood/memory上限/同時pipe/blocked stdin/Job Objectによる子孫終了/秘密値除去が成功。
**workflow全体は既知失敗により赤のまま**。Linuxの870 passedはCI後続の推測ではなく独立実行の証拠。
この後の最終commitは検証文書のみであり、実装コードは上記HEADと同一。
基準は今回の変更前に再実行したLinux **821 passed / 3 failed / 8 skipped**（25.76秒）。
既知mypyはWindows ctypes属性9件、Windows基準は829 passed / 3 skipped。

実施コマンド:

```console
uv run --no-sync pytest tests/test_capability_inventory.py
uv run --no-sync pytest tests/test_capability_bridge.py
uv run --no-sync pytest -m 'not requires_model' --junitxml=/tmp/b4-review-final.xml
uv run --no-sync ruff check src tests tools
uv run --no-sync ruff format --check src tests tools
uv run --no-sync mypy
uv run --no-sync python -m utteran_contracts.capability_schema --output docs/architecture/b4/capabilities.schema.json --check
uv run --no-sync python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json --check
uv run --no-sync python -m utteran_contracts.worker_schema --output docs/architecture/b3/worker.schema.json --check
uv lock --check
uv build --wheel --out-dir /tmp/utteran-b4-review-wheel
uv run --no-sync python tools/public_history_scan.py --worktree --json /tmp/b4-review-scan.json
```

同じ0.1.33のPR内レビュー修正としてversion/uv.lockを維持。B1/B3 DTO/schema、B2 service、
既存CLI/GUI/worker実装、stage hash/resume/forceに変更なし。profile/manifest/venv/model/nativeを変更しない。
CIの追加は既存3.11/3.12契約matrixにtransport testsを含めるだけ。

実GPU/native推論・性能/quality、Windows実配布/WebView2/PyInstallerは未検証。
[PR #25](https://github.com/zawa356/Utteran/pull/25)をDraftのまま更新し、Ready化・merge・B5着手はしない。
