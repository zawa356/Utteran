# B1検証記録・未完了事項

実施日: 2026-10-30。環境はLinux managed cloud。Windows VMは利用できず、Windowsでの実行/配布成功は主張しない。

## ベースラインと差分

main `1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`上で依存をlock通り導入して再確認。
Phase A head `60d9bdd6006551149c6dde5a735dee17c16cc15d`は指示書と一致し、基点mainからの差分は文書のみ。

| 検査 | main / Python 3.12.14 | B1 / Python 3.12.14 |
|---|---|---|
| モデル不要pytest | 473 passed / 3 failed / 8 skipped | 518 passed / 3 failed / 8 skipped |
| Ruff lint/format | 成功、119 files | 成功、127 files |
| mypy全体 | Windows ctypes属性9件、62 source files | 同じ9件、67 source files |
| mypy共通DTOのみ | 対象なし | 5 source files、エラー0 |
| uv lock --check | 導入時locked確認 | 成功 |
| 新契約のテスト | 対象なし | 45 passed |

pytest実行は`pytest -m 'not requires_model' --basetemp=/var/tmp/<run-specific-directory> -q`。
platformの`/tmp/.git`がcore出力保護へ干渉するためGit管理外の/var/tmpを使った。ローカル通信等は実行環境が必要とするsandbox escalationで許可した。テストskipやassert無効化はしていない。

既存失敗は同一node IDで、新しい失敗なし:

- `test_devices.py::test_timeout_kills_probe_process_tree`: Linux containerでkill済み孤児childがPID 1配下のzombieとして残り、PID存在検査がtrueとなる。Windows process-tree受入とは別。
- `test_gui_processes.py::test_profile_manifest_detects_current_and_stale_dependencies`
- `test_gui_processes.py::test_profile_manifest_detects_venv_move_without_disabling_profile`

後者2件はtest helperがWindows専用`win-*/Scripts/utteran.exe`を生成する一方、Linux実装が`linux-*/bin/utteran`を参照する。B1では対象の実装・既存testを変更しない。
mypyは`utteran_gui/processes.py`のWinDLL/WinError/get_last_errorで発生する既存9件。これらの既存問題は別PR案とし、B1でskip/ignoreして通したとは報告しない。
8 skipはWindows専用処理7件とgit-filter-repo未導入1件。全体warningは既存FastAPI/Starlette TestClientのdeprecation。

## 新規検証

- `tests/test_contracts.py`: Phase Aのpreset/policy/editing/error例を再利用。JSON往復、未知version/field、strict型、enum/range、null/omitted/false/auto/空Prompt、leafとvalue、重複path/ID、immutableと検証付きcopy、repr/例外projection、純粋交差制約、unordered入力拒否。
- `tests/test_contracts_schema.py`: 21 schemaのwire意味比較、Draft 2020-12、正/不正例、path別value制約、同一byte生成、check成功/不一致/非書込み、schema artifact一致。
- `tests/test_contracts_boundary.py`: AST方向、fresh processのimport guard、書込み/通信/子processのaudit guard（bytecode書込みは-Bで無効化）、GUIとの共存、wheel対象設定。
- Python 3.12.14 + lock Pydantic 2.13.4: 新規45件成功。
- Python 3.11.17 + Pydantic 2.13.4、独立venvへインストールしたwheel: 新規45件成功。
- Python 3.11.17 + **Pydantic 2.9.2**、別の最小venv: DTO/schemaの41件成功。GUI依存はこの最小venvに入れず境界4件は実行していない。現行lock/2.9双方の生成物byte一致も成功。

Pydantic 2.9でのschema参照解決はhandler.resolve_ref_schemaを使用。constと同じ1要素enum、整数と同値な数値境界の表記を正規化し、表記差だけで大きな差分を出さない。検証制約/署名/checksumを緩める処置ではない。生成物更新はlocked環境からのコマンドを正とする。

## 配布・依存境界

`uv build --wheel --out-dir /tmp/utteran-b1-wheel`成功。wheelに`utteran_contracts`5moduleを収録。
lockからGUI extrasのみをexportした独立Python 3.12 venvへwheelを`--no-deps`で導入し、checkout外（/tmp）から`utteran_gui.api`/`utteran_contracts`のimportとschema exportが成功。torch/torchaudio/OpenVINO/GenAI/faster-whisper/pyannote/ctranslate2は**インストールなし・importなし**、coreもimportなし。実GUI windowやPyInstallerの成果物検証ではない。

lockのpackage version比較では既存依存の更新なし。追加はjsonschema/jsonschema-specifications/referencing/rpds-pyの4package。attrsは既存lockに存在。製品版はrelease規約に従い0.1.29→0.1.30、`utteran`/`utteran_gui`のversionは既存どおりdistribution metadataから解決する。runtime新依存は追加せず、devへjsonschema/PyYAMLを明示した。
CIへoffline artifact checkとPython3.11/3.12の契約jobを追加したが、GitHub Actionsでの実行結果は未取得。
最終pytestは518 passed / 3 failed / 8 skipped（16.89秒）、失敗node ID 3件がmainと完全一致することを機械的に照合。
`git diff --check`成功。公開worktree scanは261 tracked files、blockingは既存の合成WAV fixture 2件のみで、新規blockingなし。
Phase A文書・既存core/GUI/packagingの処理コードに差分がないことも確認した。

## 未検証・後続

- Windows VM、PyInstaller、WebView2、installer/portable/Python直起動の実配布、native dialog/D&Dは未実施。
- 新API/worker/core adapter/store/実行機能は実装していないため、その機能のテスト成功とは扱わない。
- Phase Aの53未実装schema、runtime backend互換性、preset継承/正規化、private APIのerror handler接続はB2以降。
- raw private dump/ValidationError.errors()をログへ渡さない運用が必要。B1はOS上の秘密保管や通信を実装しない。
- mypy/既存pytestの失敗は別PR候補として残る。Ready化・merge・B2開始は行わない。

## PR提出

GitHub APIへの通信は開始時にForbidden、Git fetch/Web Issue取得は成功。関連Issue #18〜#22はOPENでbodyを確認。公開PR検索にPhase Aの同head PRリンクはなかったが、APIによる有無確認はできていない。Phase A PR未mergeを前提に、B1はbase=`docs/phase-a-architecture`として提出する。最終作成結果は最終報告に記載し、失敗ならPR URLを捏造しない。

最終結果: `feat/b1-contracts`のpush成功。`gh pr create --draft --base docs/phase-a-architecture`は
`https://api.github.com/graphql`への通信がForbiddenとなり失敗し、**B1 Draft PRは未作成**。
GitHub HTTPS Gitのread/pushは成功しているため、token未設定が原因とは断定しない。
実行環境のネットワーク設定で`api.github.com`へのアクセスを反映後、PR有無を再確認して作成する。
PR本文はこの作業環境の`/workspace/scratch/utteran-b1-pr.md`へ保存した（Git管理外）。再実行:

```console
gh pr create --repo zawa356/Utteran --base docs/phase-a-architecture --head feat/b1-contracts --draft --title 'feat: Phase B1 共通DTO・オフラインSchema Export基盤' --body-file /workspace/scratch/utteran-b1-pr.md
```

[stacked比較ページ](https://github.com/zawa356/Utteran/compare/docs/phase-a-architecture...feat/b1-contracts)でB1差分だけを確認できる。
比較ページはPRではなく、DoDのDraft PR項目は外部通信制限により未完了。mainへの直接push、Ready化・merge・B2実装はしていない。


## B2着手前のB1レビュー修正

P2: SettingOverrideはleaf型検証後の値でunique検査を行う。不正なobject/nested arrayを渡しても
生TypeErrorではなくValidationErrorとなる回帰3例を追加。P3: 全schemaのallOfを一律に捨てる比較をやめ、
Policy/PolicyWriteの意図的な追加auto条件だけを明示分離し、Phase Aの条件を4ケースで意味比較する。
修正後の契約テストはPython3.12.14/3.11.17双方で52件成功。生成schema変更なし。
lock/profileの既知影響は[調査記録](profile-lock-impact.md)を参照。pending B1のレビュー修正として独立コミットし、B2へ積む。
