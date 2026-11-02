# B4検証記録

2026-11-02。開始B3 `5ef7bab7ba81cab7afc1fe23b85abc8da399ec1b`、製品version 0.1.33。
GPU/modelを取得せず、profile/manifest/jobを変更しない開発用.venvで検査した。
`uv sync --extra dev --extra gui --locked`は開発依存の準備だけで、製品profileへのsyncではない。

## 基準差分

| 検査 | B3変更前 | B4最終コード | 増分 |
|---|---|---|---|
| Linux Python3.12.14 モデル不要pytest | 715 passed / 3 failed / 8 skipped | 821 passed / 3 failed / 8 skipped、25.20秒 | 新規106成功、失敗0増 |
| mypy strict全体 | 9 errors、76 source files | 同じ9 errors、85 source files | 新規0 |
| Ruff lint / format | B3記録で成功 | 成功、155 files | 新規違反0 |
| Python3.11.17 独立wheel環境、B1/B2/B3/B4関連 | B3記録は一部subset | 348 passed、11.73秒 | B4 106を含む |
| B1/B3 offline schema --check | 成功 | 成功、artifact差分なし | 0 |
| B4 schema --check | 未実装 | 3.11/3.12成功、import順にも再現 | 追加 |
| uv lock --check --offline | 成功 | 成功 | root版0.1.33のみ、依存版更新なし |
| wheel | B3版 | utteran-0.1.33-py3-none-any.whl成功、3.11へ導入 | 新module収録 |

baseline/finalのJUnit XMLからfailed/errorのnode集合とskip集合を比較し完全一致。
Linuxの既知3失敗:

- `tests/test_devices.py::test_timeout_kills_probe_process_tree`
- `tests/test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`
- `tests/test_gui_processes.py::test_profile_manifest_detects_venv_move_without_disabling_profile`

mypy既知9件は全て`src/utteran_gui/processes.py`のWindows ctypes属性。
B4にskip/type-ignore/検査無効化は追加しない。全体CI/製品受入は未合格であり、
B4新規回帰0と既存課題を区別する。

## 実行コマンド

```console
uv run --no-sync pytest -m 'not requires_model' --junitxml=/tmp/b4-final.xml
uv run --no-sync pytest tests/test_capability_inventory.py -q
uv run --no-sync ruff check src tests tools
uv run --no-sync ruff format --check src tests tools
uv run --no-sync mypy
uv lock --check --offline
uv build --wheel --out-dir /tmp/utteran-b4-wheel
uv run --no-sync python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json --check
uv run --no-sync python -m utteran_contracts.worker_schema --output docs/architecture/b3/worker.schema.json --check
uv run --no-sync python -m utteran_contracts.capability_schema --output docs/architecture/b4/capabilities.schema.json --check
```

独立3.11環境は`uv export --extra dev --extra gui --no-emit-project --no-hashes`の固定依存と
wheelを導入し、以下を実行した（その環境のpython/pytestを使用、editable導入なし）。

```console
python -m pytest tests/test_capability_inventory.py tests/test_contracts.py tests/test_contracts_schema.py tests/test_contracts_boundary.py tests/test_config_adapter.py tests/test_application.py tests/test_application_golden.py tests/test_worker_protocol.py tests/test_worker_runtime.py tests/test_worker_client.py -q
```

## 新規106ケースの範囲

- 7 recipe、OpenVINO+CUDA拒否、OV encoder＋GGML Vulkan、GenAI CPU/GPU/NPU route、言語制約。
- pyannote CPU/CUDA/XPU独立component、Windows以外のXPU拒否、model/device固定条件。
- worker backend/diarization/local完全モデル/single input制限。
- profile/model/driver/native不足、smoke欠落、required not_applicable、fresh failed/unknownの区別。
- profile generation/lock/path/extras、OS/Python ABI、runtime/driver、model/files、native、hardware/index、
  transport/settings変更による失効、未来/期限切れ/観測時刻なし、timeout/error/cache理由保持。
- identity不完全でもpassedを並べただけではready不可。model file存在だけでもunknown。
- 指定backend/modelに矛盾する構成を候補から外し、exclusionsへ理由を記録。
- auto ready限定、fixed失敗時fallbackなし、allowed profiles、memory不足/不明/上限、deadline未保証。
- benchmark比較群/precision/model/language/scope/時刻、決定的tie-break、confidence、preapprovedの具体構成。
- 新inventory/profile追加後も以前の候補証拠/Resolved値は不変。
- requested routeを実績へコピーせず、別routeへのsmoke成功を拒否。
- immutable DTO、version/unknown-field/duplicate拒否、safe repr/projection、JSON Schema一致と非書込check。
- GUI core/heavy importなし、stdinのみprivate設定、timeout/error sanitization、他profile応答拒否。
- audit hookでfresh-process import/schema/collector/resolveのwrite/network/subprocessを拒否して成功。
  OS情報取得で`platform.platform()`が外部processを起動する点は検出してprimitive取得へ修正済み。

B1/B2のCLI golden/hash/resume、B3 protocol/runtime/clientは全回帰と3.11関連subsetで成功。
既存CLI devices/models/nativeの実装fileを変更しておらず、既存JSON testも全体に含む。
実モデル/GPUのpassed証拠は作成していない。synthetic passedはテストhelperのみ。

公開worktree scanは310 tracked files、77 findings、blocking 2件。blockingはB3以前からの
合成WAV fixture 2件（benchmark/japanese_reference.wav、diarization_quality/meeting.wav）だけ。
新規blockingなし。matched値は出力・保存せずredacted reportで位置だけ確認した。
`git diff --check`成功。既存実行module/B1・B3 schemaに差分なし。

## Windows・CI・未検証

B3のWindows基準は723 passed / 3 skipped。
B4のWindows CI結果はPRのActions確認後に追記する。
Linux Actionsは既存mypy 9件で止まり得るため、上記独立pytest結果も併記する。
既存Windows workflowにはpytest後のverify_uninstaller/Inno Setup前提で失敗する問題があり、
B4に無関係な修正を混ぜない。

Windows VM、WebView2/PyInstaller/installer、実GPU/native推論、driver実probe、モデル内容のchecksum検証、
性能/quality、B3未対応backendの実行は未検証。標準collectorでreadyゼロは正常な結果。
profile dependency closure fingerprint、mypy既知9件、PowerShell CI分割は独立PR対象。
