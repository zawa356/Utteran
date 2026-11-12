# B4 smoke — Windowsローカル実機受入手順（**未実施**）

更新: 2026-11-12 JST（開発版`0.1.50`）。設計は[smoke](smoke.md)。

> **本書は手順書であり、実施結果ではない。** Cloud工程では実施していない。成功件数・実行時間・メモリ値は実測するまで記入しない。
> 実ユーザーのdata root・既定のユーザー領域・既存データのあるディレクトリでは**絶対に実行しない**。

## 0. 前提

| 項目 | 条件 |
|---|---|
| OS | Windows開発環境（PowerShell 5.1以降）。Linux実機でも同じ手順の`bin/python`版で可 |
| checkout | 本ブランチのHEAD（0.1.50）。repo直下で作業する |
| GUI環境 | repo直下の`.venv`（`uv sync --extra dev --extra gui --locked`済み） |
| 推論profile | 専用root内に`setup.ps1 -Profile cpu`（CUDA時は`cuda`）で**このcheckoutから**作成したもの。lockが違えば`profile_incompatible`で止まる |
| モデル | `large-v3-turbo`（または`large-v3`/`kotoba-whisper-v2.0`）。**B3/B4はmodel.bin・config.json・tokenizer.json・preprocessor_config.jsonを要求**し、catalogの`tiny`/`base`/`small`/`medium`は`preprocessor_config.json`を含まないため対象外（既存B3の条件） |
| network | モデル取得（手順2）だけ。smoke自体はofflineで動き、ダウンロード・sync・変換を一切しない |
| GUI | 同じrootのGUIは**終了しておく**（起動中はsmokeが`gui_already_running`で拒否） |

## 1. 専用data rootを作る（初回のみ）

```powershell
$Root = "D:\utteran-dev-b4"   # 例。既存データのない新しい空ディレクトリ
New-Item -ItemType Directory $Root | Out-Null
$env:UTTERAN_DATA_ROOT = $Root
uv run --no-sync utteran-gui --next-ui-v1   # 空dirにmarkerが作られる。起動したらすぐ閉じる
```

smoke入口は**markerを作らない**。markerの無いrootは`dev_root_not_marked`で拒否される（空dirでも）。

## 2. profileとモデルを準備する（smokeの外、明示操作）

```powershell
$env:UTTERAN_DATA_ROOT = $Root
.\setup.ps1 -Profile cpu -Yes
& "$Root\.venvs\windows-cpu\Scripts\utteran.exe" models download faster-whisper:large-v3-turbo
Get-ChildItem "$Root\models\faster-whisper\large-v3-turbo" -Name   # 4ファイルの存在を目視確認
```

## 3. smoke用のsettingsを作る

smokeの証拠はProcessingSettings**全体**のdigestに拘束される。後でB7で実行するジョブと**同じeffective settings**を使うこと。
例（examplesのpresetを話者分離なし・worker条件に合わせたもの。値はリポジトリ管理の合成例）:

```powershell
$Settings = "$env:TEMP\utteran-b4-smoke-settings.json"
uv run --no-sync python -c "import json,sys; d=json.load(open('docs/architecture/phase-a/examples.json',encoding='utf-8'))['preset']['settings']; d['asr'].update(backend='faster-whisper', model='large-v3-turbo', glossary=[]); d['asr'].pop('extra_arguments', None); d['diarization']['enabled']=False; open(sys.argv[1],'w',encoding='utf-8').write(json.dumps(d))" $Settings
```

PowerShellのパイプは非ASCIIを変換し得るため、stdinには`cmd /c ... < file`でbyteのまま渡す。

## 4. 明示smokeを実行する（CPU）

```powershell
$env:UTTERAN_DATA_ROOT = $Root
cmd /c "uv run --no-sync python -m utteran_gui.capability_smoke --profile cpu --recipe fw-cpu --model large-v3-turbo < `"$Settings`""
echo "exit=$LASTEXITCODE"
```

出力は`utteran capability smoke: [<code>]`の1行だけ（path・digest・文字起こし・stderrは出ない）。終了code 0=passed、1=未成立、2=拒否。

## 5. readyの確認（metadata-only、件数とcodeだけ）

```powershell
$Py = "$Root\.venvs\windows-cpu\Scripts\python.exe"
cmd /c "`"$Py`" -I -B -c `"import sys,json,collections; from datetime import UTC,datetime; from utteran_contracts import ProcessingSettings; from utteran_contracts.capabilities import InventoryRefreshRequest; from utteran.capabilities.collector import refresh_inventory; s=ProcessingSettings.model_validate_json(sys.stdin.buffer.read()); inv=refresh_inventory(InventoryRefreshRequest(schema_version=1, profile_id='cpu', settings=s, execution_path='worker', revision=1), now=datetime.now(UTC)); print(dict(collections.Counter(c.runnable for c in inv.candidates))); print(sorted({i.code for c in inv.candidates if c.deployment.identity.recipe_id=='fw-cpu' and c.deployment.identity.model_id=='large-v3-turbo' for i in c.issues}))`" < `"$Settings`""
```

（`UTTERAN_DATA_ROOT`を設定したまま、repo直下で実行する。collectorはcwdの`uv.lock`を読む。）任意で、`--next-ui-v1`を起動し同じsettingsのPreset/Policyで
`/api/v1`のresolution（[G-09](../ui3/g09-next-ui-v1-startup.md)の認証手順）が409でなくなることを確認する。

## 6. 受入条件

| 確認 | 期待 |
|---|---|
| fw-cpu smoke | `[passed]`、終了code 0 |
| 手順5 | fw-cpu × large-v3-turboが`ready`、issueが空 |
| GUI起動中に再実行 | `[gui_already_running]`、終了code 2、証拠ファイル無変更 |
| 期限 | 24時間後（またはprofile再作成・モデル更新・settings変更後）は`ready`でない |
| privacy | 出力・`$Root\cache\capability-evidence-v1\*.json`にpath・prompt・文字起こし・tokenが無い（digestと固定codeのみ） |
| 後片付け | `%TEMP%\utteran-smoke-*`が残っていない |

想定される`unknown`/失敗（偽装せずそのまま記録する）:

- モデル未導入・ファイル不足 → `[model_missing]`。lock不一致 → `[profile_incompatible]`。
- CPU名が読めない環境 → smokeは`passed`でも手順5は`unknown`（`evidence_scope_incomplete`）。
- メモリ値が取れない → `unknown`（`memory_unknown`）。
- CTranslate2が実効compute typeを別名で返す → `[compute_mismatch]`（CPU `int8`→`int8_float32`だけは許容済み）。

## 7. CUDA（実GPU・driverがある場合のみ）

profileを`cuda`で作り、`--profile cuda --recipe fw-cuda`で手順4を実行する。**本版の期待値はready=0**:
smokeが`[passed]`（replicaのdevice=cuda・index 0をattest）でも、driver/GPU identityとVRAMを観測できないため
手順5は`unknown`（`evidence_scope_incomplete`・`memory_unknown`）。`[backend_unavailable]`・`[out_of_memory]`・`[compute_mismatch]`も結果として記録する。
CPUへ自動fallbackしないこと（fallbackしたら不具合）を確認する。

## 8. rollback / 後片付け

- 証拠だけ消す: `Remove-Item -Recurse "$Root\cache\capability-evidence-v1"`（専用rootのみ。既定のユーザー領域には証拠は作られない）。
- 全部消す: 専用root自体を削除する。実ユーザーのDB・設定・モデルには触れていない。
