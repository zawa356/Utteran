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

最終コードの全回帰・Python互換・Windows CIの実行結果を確認後に追記する。
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
