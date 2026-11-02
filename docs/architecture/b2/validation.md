# Phase B2検証・未検証

2026-10-30、Linux cloud / Python3.12.14。Windows VMは利用不可。実モデル/GPUは不要・未実行。

## B1開始ゲート

- 開始main `1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`、Phase A `60d9bdd6006551149c6dde5a735dee17c16cc15d`、B1 `de67f185c3bd16eb631bf371ed853082375c0ca5`は指定値と一致。
- B1独立修正commit **`0f80d175e61200e16d97e5dda92d1b82ba8382b5`**をB1へpush済み。
- P2: leaf型検証後の配列で重複判定。不正object/nested arrayでもValidationErrorとなる3回帰例。
- P3: allOf一律除外をやめ、Policyの意図的追加条件だけを分離しPhase A条件を4ケースで意味比較。
- 修正後B1契約52件がPython3.12/3.11で成功してからB2 branchを作成し、core変更前のgoldenをcapture。
- [lock/profile調査](../b1/profile-lock-impact.md): dev/format/root version変更でもlock全体hashのため全profileに警告が出得る。未解決を明示し、manifest/venvの変更・検査回避・再構築をしていない。

## 結果

| 検査 | 修正済みB1 baseline | 最終B2 |
|---|---|---|
| 全モデル不要pytest / Python3.12 | 525 passed / 3 failed / 8 skipped（17.95秒） | **573 passed / 3 failed / 8 skipped（20.21秒）** |
| mypy全体 | 既存9 errors / 67 source files | 同じ9 errors / 69 source files |
| mypy application/config_adapter | 対象なし | 2 source files、エラー0 |
| Ruff lint/format | B1で成功 | 成功、132 files |
| lock check / contracts artifact check | 成功 | 成功 |
| 新規B2 test | 対象なし | 48件成功 |
| Python3.11.17 契約+B2 | B1契約52件成功 | **100件成功**（4.68秒） |
| wheel build | B1成功 | utteran-0.1.31 wheel成功 |

B1修正前は518/3/8。gateの7追加testで525、B2の48追加testで573となる。失敗3件のnode IDをlogから抽出してbaselineと完全一致を確認した。8 skip/既存warning1も区別し、skipやassert抑制で通したわけではない。

既存失敗:

1. `test_devices.py::test_timeout_kills_probe_process_tree` — Linux containerのkill済みorphan zombieをPID存在と判定。
2. `test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`
3. `test_gui_processes.py::test_profile_manifest_detects_venv_move_without_disabling_profile`

後者2件はWindows専用fixture pathとLinux実装の差。mypy9件はGUI processes.pyのWindows-only ctypes API属性。
既存失敗の無関係な修正をB2に混ぜていない。全チェック成功/Windows合格とは報告しない。

## 実行コマンド

```console
uv sync --extra dev --extra gui --locked
pytest -m 'not requires_model' --basetemp=/var/tmp/utteran-b2-final -q
ruff check src tests tools
ruff format --check src tests tools
mypy
mypy -p utteran.application -p utteran.config_adapter
uv lock --check --offline
python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json --check
uv build --wheel --out-dir /tmp/utteran-b2-wheel
```

実際は.venv/binの各commandを使用し、uv cacheは/workspace/.cache/uv。Linux sandboxのsocket/write制約は対応実行権限で許可。
/tmp/.gitがcoreの出力保護に干渉するため、pipeline/CLIの結果作成testはGit管理外の/var/tmpで実施した。
Python3.11は独立venvへ0.1.31 wheelを導入し、同じ最終sourceのcontract/application/config_adapter/goldenを100件実行した。
依存version変更なし、uv.lock差分は製品versionだけ。Git diffでpipeline/jobs/config/asr/diarization/exporters/GUI/packagingの変更なしを確認。
文書local link / diff check成功。公開worktree scanは273 tracked files、blockingは既存の合成WAV fixture 2件のみ、新規blockingなし。

## 試験の意味と限界

- 新規testはapplication 8、adapter 20、旧CLI/hash golden 20。実stub+pipeline、変換の全hash一致・env非再適用、拒否/trace、resume/force/no-resume、旧manifest/payload/再出力、5形式byte、Rich/JSONL、cancel/例外、batch等を検証する。
- [互換性資料](compatibility.md)の通り、変更後の2経路だけの比較ではなく、**変更前CLIの固定値**と一致を検査した。
- GUI禁止import/契約生成test、既存CLI/GUI/backend/job/export回帰も全suiteで実行。
- 型・core変換・既存意味検証・runtime可用性は別。実hardwareをprobeしてreadyを保証する仕組みはB4対象。
- Windows VM/terminal Ctrl+C/WebView2/installer/portable/PyInstaller、CPU/CUDA/Intel/Vulkan実推論は未実施。
- Promptは合成backendへの到達・通常log非露出を確認。品質A/B・native argv保護は未実施でwhisper.cppの既知制約を維持。
- GenAIのstructured変換、glossary compiler、実験引数、安全なprofile dependency fingerprintは未対応。既存CLIの対応は削減しない。
- 新API/GUI/worker/永続queue/自動選択/データ移行は未実装。Ready化・merge・B3へ進まない。

## B3への引継ぎ

profile processのversion付きIPCからadapter/serviceを呼ぶ際は、実行contextの全選択を明示し、base_configにambient Config.loadを使用しない。token/SDK/model cache/native library等のruntime environmentは現行coreにも参照箇所があるため、launcher側で拘束・検証する必要がある。CLIのmodel承認関数やexit処理をworkerへimportしない。未対応adapter fieldはerrorとして返し、CLI argvへ迂回して黙って実行しない。新規DTO/APIのerror projection、worker timeout/cancel/protocolは別PR。

## 提出結果

B1修正とB2 branchのpushは成功。B2は修正済みB1 `0f80d17`をbaseとする独立stackで、mainへ直接pushしていない。
`gh pr create --draft --base feat/b1-contracts`は`https://api.github.com/graphql`への通信がForbiddenとなり失敗し、**Draft PR未作成**。
Gitのread/pushは成功しているためtoken不足とは断定せず、GitHub APIのネットワークアクセス反映が必要。
PR本文は現作業環境の`/workspace/scratch/utteran-b2-pr.md`に保存した（Git管理外）。API復旧後、同headのPR有無を確認して再実行する。

```console
gh pr create --repo zawa356/Utteran --base feat/b1-contracts --head feat/b2-core-application-service --draft --title 'feat: Phase B2 Core Application Service・Config Adapter' --body-file /workspace/scratch/utteran-b2-pr.md
```

[stacked比較ページ](https://github.com/zawa356/Utteran/compare/feat/b1-contracts...feat/b2-core-application-service)はB2差分のレビュー用であり、PRそのものではない。
Ready化・merge・B3実装はしていない。
