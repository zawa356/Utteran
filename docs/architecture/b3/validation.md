# Phase B3検証・レビュー提出

**PR #24レビュー修正後の最新結果は[検証追補](pr24-review-validation.md)を参照。**
以下の初回提出時結果と区別する。

2026-11-02、Linux cloud、Python 3.12.14。実モデル・GPU・私的音声は使用していない。
Windows VMは利用不可。Ready化・merge・B4実装は行わずレビュー待ちとする。

## 基点とB2先行修正

- main: `1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`
- Phase A: `60d9bdd6006551149c6dde5a735dee17c16cc15d`
- B1: `0f80d175e61200e16d97e5dda92d1b82ba8382b5`
- 指定B2 HEAD: `ff45892ea1d2ee2ef4c43561d1ea4c8cfc97c281`
- B2独立修正: `294c383`。空白/前後空白/auto表現を有効diarizationで拒否し、無効時の従来動作を保持。
  関連104件成功、B2へpush後、修正済みB2から`feat/b3-profile-worker`を作成した。
- 修正済みB2全体: 589 passed / 3 failed / 8 skipped（27.07秒）。旧573件から16件追加。

## 結果

| 検査 | 結果 |
|---|---|
| B3 protocol/runtime/launcher | 新規82件成功 |
| 全モデル不要pytest | 671 passed / 3 failed / 8 skipped（35.33秒）。B2既知失敗・skipと一致 |
| mypy全体 | 既存9 errors / processes.py、76 source files |
| 新規7moduleのmypy | 0 errors |
| Ruff lint/format | src/tests/tools成功 |
| lock check | 成功。依存version差分なし、root 0.1.31→0.1.32のみ |
| B1/B3 schema artifact check | 成功、オフライン生成物一致 |
| GUI禁止import/core→GUI禁止import | ASTとcore/heavy import阻止subprocess試験で成功 |
| wheel | utteran-0.1.32、全7新moduleの包含確認 |
| Python 3.11.17独立wheel環境 | 契約/adapter/application/CLI golden/protocol 148件成功 |

Python 3.11の最初の試行はwheelの緩い依存範囲から最新依存を解決し、GUI extras未導入で
import試験1件、Pydanticのerror URL版の違いで旧CLI golden1件が失敗した。
GUI extrasを含むuv.lock exportへ依存を揃え、pytestもlockの8.4.2に固定すると148件成功。
goldenを変更して合わせていない。任意の将来依存versionでのgolden一致を保証した試験ではない。
3.11ではモデル/GPUなしのwheel・DTO・service互換を確認し、全launcher fault suiteは3.12で実施。

B2から継続する既知失敗:

1. `test_devices.py::test_timeout_kills_probe_process_tree` — Linux containerのkill済みorphan zombieの存在判定。
2. `test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`
3. `test_gui_processes.py::test_profile_manifest_detects_venv_move_without_disabling_profile`

後者2件はWindows用fixture pathとLinuxとの差。mypy9件は既存Windows ctypes属性。
これらをskipへ変更したり、assertを弱めて成功扱いにしたりしていない。

## 試験の実体

- Protocol: strict版/unknown field、3 ID、順序/gap、重複terminal、UTF-8/JSON/重複key/NaN、
  16MiB request/1MiB eventのbounded read、schema生成、private projection。
- Launcher: 登録profileと絶対Python、venvなし、古いmanifestの無変更拒否、worker module欠落、
  起動失敗、handshake timeout、blocked stdin write、stderr大量出力の同時drain、異常EOF、
  terminalなしexit 0、terminal後非zero、terminal後pipe沈黙、偽cancelと実killの分類。
- Process tree: handshake中に子孫を起動したpeerと、leaderが先に退出したpeerのgroup cleanup。
  Linuxの残留zombieは実行中processと区別する。Windows Job Objectは実機未試験。
- 実worker: 新規テストvenvのPythonから実moduleを起動し、hello後のモデル不足を確認。
  別のtest-only bootstrapで合成ASRを注入し、実worker main→Config Adapter→B2 service→
  実Pipelineをprocess境界越しに実行。productionにstub backendや任意module指定は追加しない。
- core互換: Prompt到達、env非再適用、全stage hash、resume/force/fresh、既存成果物と出力byte、
  旧CLI golden維持。実workerではhostileな設定env（raw log指定・不正JSON・小文字section）も
  除去して確定requestだけを適用する。cancelは安全な境界で例外→lock解放後のerror、受理だけで停止としない。
- 秘密値: 合成Prompt/入力path/token形文字列をbackendのfd1/fd2・例外へ注入しても、
  protocol/public callback/通常ログに混入しない。private manifest/resultの内容は公開しない。
- 完了: exitとterminal整合、manifest/export hash照合、出力改ざん、不正な形式集合を拒否。
  親のcompleted callbackも、exit・成果物検査後にだけ呼ぶ。

## 再現コマンド

```console
uv sync --extra dev --extra gui --locked
.venv/bin/pytest -m 'not requires_model' -q --basetemp=/var/tmp/utteran-b3-final
.venv/bin/ruff check src tests tools
.venv/bin/ruff format --check src tests tools
.venv/bin/mypy
uv lock --check --offline
.venv/bin/python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json --check
.venv/bin/python -m utteran_contracts.worker_schema --output docs/architecture/b3/worker.schema.json --check
uv build --wheel --out-dir /workspace/scratch/b3-wheel
```

独立3.11環境はuv venvで新規作成し、wheelとGUI runtime依存のlock export・pytest/jsonschema/PyYAMLを
導入して上記148件を実行。作業ログはGit外の`/workspace/scratch/b3-*.log`に保持する。
既存profileのmanifest/venvへ変更せず、試験用profileだけをpytest temporary directoryに作成した。

## 未検証・引継ぎ

- Windows CreateProcess、CREATE_NO_WINDOW、Job Objectの親強制終了、WebView2/pywebview、
  onedir/portable、wheel再導入の配布受入はWindows VMで別途必要。
- 実faster-whisper model/GPU/driverの推論・品質・性能は未実施。ファイル存在だけでruntime readyとしない。
- pyannote transitiveモデル、whisper.cpp/native/VAD/OpenVINO、GenAI、用語集、実験引数は新worker未対応。
- whisper.cpp --promptと全native argvのpath保護、hard cancel/pause、安全な強制停止後reconcile、
  Linux親強制終了時のnative区間の即時退出は未解決。
- deadline/検証timeoutはsoft cancellation。推論が応答しないと退出待ちが延びる。hard SLAを宣言しない。
- lock/profile runtime closure fingerprintの独立修正計画は[compatibility](compatibility.md)参照。
  既存profileがblockされてもmanifest更新・再構築・検査無効化はしない。
- B2 ConfigはBaseSettingsでありmodel_validate単独では環境非参照を保証しない。B3は専用process境界で
  設定envを除去する。B4のcontext生成を別processへ移す場合もこの前提を維持・検証すること。
- B4以降はcandidate inventory/resolve・確定contextの生成へ接続し、B6/B7で永続attemptとcore IDを紐付ける。
  capabilities完全版、setup承認、snapshot ACL/永続queue/SSEは今回の成果物ではない。

## 最終差分確認

全体の671件成功は修正済みB2の589件から新規82件の増加。失敗node ID・skipは同じ。
最後のworker設定env分離後にもB3全82件を再実行して成功し、通常CLI側のsourceは変更していない。
新規moduleの型検査0件、全体の既存9件を区別する。依存解決後のlock差分はroot versionだけ。
公開worktree scanは292 tracked files、blockingは既存合成WAV 2件のみで新規blockingなし。
wheelに新規7moduleが含まれ、差分whitespace検査も成功。
最終wheelを独立3.11環境へ再導入した148件も成功（4.80秒）。

## 提出

- [Draft PR #24](https://github.com/zawa356/Utteran/pull/24)
- base: `feat/b2-core-application-service` / `294c383b4523c75b3a002b0c6f3d9ffaba0fd692`
- head: `feat/b3-profile-worker`。実装commit: `23b059267d73b6e6b2c7d51d3ac6326f0bb58968`。
- [比較ページ](https://github.com/zawa356/Utteran/compare/feat/b2-core-application-service...feat/b3-profile-worker)
- Git pushとGitHub connectorによるDraft作成は成功。以前のAPI Forbiddenは今回再現しなかった。
  Draftのまま停止し、Ready化・merge・B4着手はしていない。
