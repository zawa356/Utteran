# Phase B8 — Setup Plan / Approval / Durable Setup Execution

> **現在の状態（2026-11-03追補）:** 本書は**Draft提出時の記録**。B8は[PR #33](https://github.com/zawa356/Utteran/pull/33)で
> **main統合済み**（merge `e803ecc3`）・未リリース・opt-in。現行の構成・制約は[backend/README.md](../backend/README.md)、
> pre-v1 backupの訂正は[storage.md](storage.md)末尾を参照。

2026-11-02 JST、開発版 **0.1.37（未リリース）**、**Draft・未マージ**。
基点: `main` `4fbb4d1cf834a67e79d1c6e46f8e85f8d1989e8f`（Phase B7マージ、着手時にremote再確認・他者commitなし）。

**ユーザーが内容を確認し承認したSetup Planだけ**を、記録可能・再開判定可能なSetup Operationとして
実行する。初回CUDA構築と既存環境へのIntel追加を同じ`plan → approve → operation`で扱う。
**planの作成はread-onlyで、download・venv構築・モデル更新などの副作用を一切起こさない**。

既定のGUI/CLI・旧`/api/*`・旧Setup wizardは不変。v1 Setupは
`create_app(..., enable_v1_settings=True, enable_v1_jobs=True, enable_v1_setup=True, v1_authority=...)`
を明示した場合のみ有効（`app.py`の通常起動は有効化しない）。

## 責務とモジュール

| モジュール | 責務 |
|---|---|
| `utteran_contracts.setup` / `setup_schema` | 純DTO（Plan/Approval/Operation/StepProgress）、宣言的setup catalog、offline schema |
| `utteran_control.setup_repository` | layout v4（`SetupControlStore`）、v1〜v3→v4 migration、plan/approval/operation/step journal/replay、共有heavy slotのdispatch guard |
| `utteran_gui.setup_planner` | read-only observer（stat・manifest・credential有無のみ）と純粋plan builder |
| `utteran_gui.setup_executor` | 固定allowlistのstep executor、process runner（`shell=False`）、production adapter |
| `utteran_gui.setup_api` | 既存v1 sub-appへのroute追加（mountは1個）、固定エラー |
| `utteran_gui.job_runtime` / `job_dispatcher`（変更） | `SetupComponents`をopt-inで受け、B7の**同じdispatcher thread・同じheavy slot**でsetupを実行 |

詳細: [contracts](contracts.md) / [storage](storage.md) / [api](api.md) /
[execution-recovery](execution-recovery.md) / [security](security.md) /
[compatibility](compatibility.md) / [validation](validation.md) / [docs-audit](docs-audit.md) /
offline schema [`setup.schema.json`](setup.schema.json)。

## 実際に実行可能な範囲（production adapter）

| step | 実行手段（固定argv、`shell=False`） | 成功条件 |
|---|---|---|
| `venv_sync` | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -Profile <p> -Yes`（Windowsのみ） | exit 0 **かつ** profile manifestが現在の`uv.lock`/extras/pathと一致（既存一致時は実行せず`skipped/validated_reuse`） |
| `credential_preflight` | profile CLI `config token-status --json --check-model <ref>` | exit 0 **かつ** 出力JSONの`access == "available"`。token欠落/無効→`waiting_user`、同意未了→`waiting_user`、network→failed |
| `model_download` | 先に`models verify <ref>`、不足時のみ`models download <ref> --progress-json`→再度`models verify` | ModelManagerのverify成功（partialはinstalled扱いしない） |
| `smoke` | `transcribe <合成無音wav> --format txt --asr-backend faster-whisper --asr-model <id>`（話者分離は計画どおり） | exit 0 **かつ** txt出力の存在。**B4 readyへは昇格しない**（ローカル証拠のみ） |

対象subsetは**faster-whisper（7モデル）とpyannote community-1**のみ。whisper.cpp（native build）、
OpenVINO IR/GenAI、既存venvの再構築、`register`（B4 catalog登録）は**固定reasonで拒否**し、実行を装わない。
Linux/macOSではvenv作成（setup.ps1）が不可のため`venv_build_platform_unsupported`。既存venvへの
モデル追加・検証のみのplanはOSに依存しない。

**fake/unitのみで検証した事項**: 全step（setup.ps1・CLIはfake child runnerで置換）。実Windows・
実GPU・実ダウンロード・実smokeは未実施。`ProcessChildRunner`は実Python子processで`shell=False`・
出力非保持のみ検証。

## 受入規則の対応

- Plan/Approvalなしで資源を変更しない: plan作成はstat/manifest読取のみ（テストでprocess起動0・FS不変）。
- Exit 0・ファイル存在・mock smokeだけでsucceededにしない: すべてのstep成功はverification evidence必須
  （store側でも`succeeded/skipped`にはevidence必須、全step充足でない`finish(succeeded)`は拒否）。
- Job/Setup/Legacyを同時実行しない: B6の部分UNIQUE index + lease PK（`heavy`）、共有dispatch guard、
  旧OperationQueueはcutoverで閉鎖（旧wizardは409）。
- State/revision/replay/Outboxは1 `BEGIN IMMEDIATE`。
- 公開JSONに秘密値なし: sentinel tokenをchild出力へ流してもDB dump・HTTP応答に出ない。

## 停止位置

Draft・未マージ・未リリース。B9は本ブランチの後続commit系列（`docs/architecture/b9/`）。
