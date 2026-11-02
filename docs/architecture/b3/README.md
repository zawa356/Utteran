# Phase B3 — Profile Worker・構造化IPC

B2修正 `294c383` を基点とする、レビュー用の独立実行経路。既存GUIの
`CliAdapter` / `JobManager`、CLIの引数・実行・モデル取得承認は変更しない。
新API、キュー、UI、候補自動選択、常駐worker poolは実装していない。

## 責務

| モジュール | 責務 |
|---|---|
| `utteran_contracts.worker` | Pydantic 2 IPC DTO、固定reason code、安全な公開projection |
| `utteran_contracts.worker_framing` | UTF-8 JSONL、byte上限、JSON/重複key/version検査 |
| `utteran_contracts.worker_state` | hello・accepted・sequence・3 ID・terminalの整合 |
| `utteran_contracts.worker_schema` | 実行環境不要のオフラインSchema生成 |
| `utteran.worker` | profile内の1要求worker、stdin制御、protocol専用fd |
| `utteran.worker_runtime` | Config境界、offline preflight、B2 service、進捗・成果物変換 |
| `utteran_gui.worker_client` | profile検査、起動、同時pipe読取、終了・成果物検査 |

GUIはcontractsだけを参照し、coreをimportしない。workerはGUIをimportしない。
依存追加はない。規約に従い製品versionを0.1.32へ更新し、lockのroot versionのみ追従。
既存wheelのpackage設定が新moduleも含む。
protocol 1 / DTO schema 1は製品version 0.1.32、HTTP版、core manifest版とは独立。

## 明示的な開発入口

Pythonからのみ利用する。通常GUIの操作から自動的に選ばれる入口はない。

```python
from pathlib import Path
from utteran_contracts.worker import WorkerRequest
from utteran_gui.worker_client import WorkerClient

# request_jsonは呼出し元が保護して保持するprivate JSON。ログに出さない。
request = WorkerRequest.model_validate_json(request_json)
client = WorkerClient(Path(repo_root), Path(registered_venv_root))
result = client.execute(request, on_event=handle_public_progress)
# resultはprivate。通常診断にはresult.public_projection()のみを使う。
```

`registered_venv_root/{win|linux}-{cpu|cuda|intel|vulkan}` の既存配置を使う。
PythonはWindowsで`Scripts/python.exe`、Linuxで`bin/python`。任意executable/moduleを
要求へ含めるfieldはない。起動前にprofile登録、venv/console script/Python、既存manifestの
互換性を検査する。worker導入とprotocol能力は最初のhelloで検査し、その成功前に要求を送らない。
古いprofileのmanifestを書き換えたり、worker導入・venv再構築を自動実行したりしない。

実行できるのは単一の絶対パスfile入力、faster-whisperの明示的なローカル完全モデル、
確定済みdevice/compute、話者分離無効の構成。モデルdirectoryには`model.bin`、
`config.json`、`tokenizer.json`、`preprocessor_config.json`が必要。存在検査はモデル内容の
妥当性・GPU可用性を保証しない。実ロード失敗はtyped errorで返す。

whisper.cpp、pyannote、GenAI、glossary、実験引数、batch、force-unlockは新経路では未対応。
理由は[security](security.md)。既存CLIの対応範囲は保持する。

[IPC仕様](protocol.md) / [互換性](compatibility.md) / [検証](validation.md)を参照。
