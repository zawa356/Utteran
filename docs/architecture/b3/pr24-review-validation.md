# PR #24 レビュー修正・検証追補

2026-11-02。対象はOpen/DraftのPR #24、head `feat/b3-profile-worker`。
開始HEAD `0f3ff3d5b8d5b16af06688ca1371576621282d0b`、base B2
`294c383b4523c75b3a002b0c6f3d9ffaba0fd692`は指示書と一致した。
B1/B2/Phase A/mainは変更せず、同じB3ブランチへ修正を追加した。

## 修正

- P1 `c9e9aca`: テストのUTF-8テキスト読書きを8ファイルで明示。
  `tests/conftest.py`、`test_application.py`、`test_config_adapter.py`、
  `test_contracts_schema.py`、`test_contracts_boundary.py`、`test_worker_client.py`、
  `test_worker_runtime.py`、`fixtures/b3/worker_stub.py`。
  fixture JSON、YAML、Python source、合成manifest/.pth等を対象とし、binary読書きは不変。
  CI全体のencoding設定変更、skip追加、期待値再生成、assert緩和はしていない。
- P2 `03e7782`: hello直後のsequence=1・accepted前・3 IDが全nullのWorkerErrorだけを例外的に許可。
  codeはmalformed_message/schema_mismatch/protocol_mismatch/oversized_message/unexpected_eofに限定。
  accepted後の全ID一致、部分null拒否、偽装ID・順序違反・重複terminal拒否を維持する。
  DTOのwire形とSchemaを変更せず、protocol v1を維持。
  新規44件（うち6件は実worker/clientへ破損フレームを注入する結合試験）を追加。
  元reason codeと値のない公開projectionを保ち、raw request/Prompt/token/pathを診断へ出さない。

これは同じB3作業単位のレビュー修正で、依存・製品version・lock・通常CLI/GUI経路は変更しない。
詳細な責任分界・許容条件は[protocol](protocol.md)を参照。

## 修正前Windowsの100件を分類

[開始時Actions run 41367936078](https://github.com/zawa356/Utteran/actions/runs/41367936078)、
[windows-tests job 125032009025](https://github.com/zawa356/Utteran/actions/runs/41367936078/job/125032009025)。
579 passed / 3 failed / 3 skipped / 97 errors。ログ全体をGit外に保存し、100件すべての
summary reasonがcp1252のUnicodeDecodeErrorであることを確認した。

| 分類 | 件数 | 原因・由来 |
|---|---:|---|
| test_config_adapter setup ERROR | 36 | B2 fixtureがPhase A examples.jsonを既定encodingで読む。B2から存在 |
| test_worker_client setup ERROR | 31 | B3 conftest.worker_requestが同じJSONを既定encodingで読む。B3追加 |
| test_worker_protocol setup ERROR | 18 | 同じB3 fixture。B3追加 |
| test_worker_runtime setup ERROR | 12 | 同じB3 fixture。B3追加 |
| test_application::test_core_service_dependency_direction FAILED | 1 | UTF-8のapplication/config_adapter.pyをAST用に既定encodingで読む。B2から存在 |
| test_application::test_structured_adapter_and_legacy_share_identical_job FAILED | 1 | examples.jsonを直接既定encodingで読む。B2から存在 |
| test_contracts_schema::test_schema_valid_examples_and_negative_inputs FAILED | 1 | examples.jsonを直接既定encodingで読む。B1から存在 |

ERRORは97件すべてsetup失敗で、その時点では各test bodyの合否は未検証だった。
上記8ファイルの他のテキスト読書きも監査し、後続の非ASCII fixture/pathで同じ問題を残さないようにした。
日本語Promptと入力データは保持した。

## 修正後コードHEADのActions

コード修正HEAD `03e77823022f314518cf33f0aa9815ef707b2eae` をpushして新しいPR同期runを実行した。
[Actions run 41369964076](https://github.com/zawa356/Utteran/actions/runs/41369964076)。

| ジョブ/段階 | 結果 |
|---|---|
| Windows pytest | **723 passed / 0 failed / 0 errors / 3 skipped**、37.89秒 |
| Windows PowerShell構文検査 | 成功 |
| Windows PowerShell起動検査 | 失敗。下記の既存compiler前提に到達 |
| linux-quality | mypyの既存Windows ctypes属性9件で失敗。後続pytestはこのCIジョブでは未実行 |
| contracts-python-compatibility 3.11 | 成功 |
| contracts-python-compatibility 3.12 | 成功 |

[Windows job](https://github.com/zawa356/Utteran/actions/runs/41369964076/job/125038110146)の
pytest summaryにUnicodeDecodeErrorはなく、元の3 FAILED/97 ERRORと新規44ケースを含めて成功した。
3 skipはgit-filter-repo未導入1件、POSIX process group専用2件。追加skipはない。
WindowsのJob Object/process管理等の既存pytestも今回実行されたが、WebView2/実配布/実GPUの合格を意味しない。

### 残るWindows CI失敗

pytest後に初めて実行できた`Start headless-safe PowerShell scripts with Windows PowerShell 5.1`で、
`scripts/verify_uninstaller.ps1:35`が
`Inno Setup compiler was not found`をthrowし、外側stepがNativeCommandError/exit 1になった。
pytestの失敗node IDはない。

このscriptは`$env:LocalAppData/Programs/Inno Setup 6/ISCC.exe`を固定前提にする
installer/uninstaller labであり、GUI配布物等も必要とする。generic起動検査は全ps1を列挙するが、
このlabを対象から分離せず、必要資源も準備していない。scriptはB2/mainから存在し、Windows stepも
今回変更していない。**既存構成に由来する潜在失敗を今回初観測した**と分類する。
開始runではpytest失敗で後続stepがskipされたため、過去の実行成功は確認できていない。

最小の別修正計画: labのcompiler/GUI配布物/参照commit等の前提を列挙し、明示的に準備した専用の
配布受入jobへ分けるか、generic smokeで使える副作用のないdry-run入口を設計する。
無関係なskip・ErrorActionの弱化・無断installer導入でB3のCIを緑にする変更は行わない。
よって**Windows pytestは合格、Windows job/workflow全体は未合格**。

## ローカル回帰・品質

| 検査 | 結果 |
|---|---|
| Linux Python 3.12.14 全モデル不要pytest | 715 passed / 既存3 failed / 8 skipped、35.87秒 |
| P1変更後のB1/B2/B3関連 | 198 passed |
| P2後のB3 protocol/runtime/client | 126 passed（従来82 + 新規44） |
| Python 3.11.17 独立wheel環境、B1/B2/protocol | 186 passed、6.63秒（従来148 + 新規state38） |
| mypy全体 | 既存9 errors、76 source files。新規増分0 |
| 変更worker_stateのmypy | 成功 |
| Ruff lint / format | 成功、145 files |
| B1/B3 offline Schema --check | 成功、生成物差分なし |
| uv lock --check --offline | 成功、pyproject/lock差分なし |
| wheel build | utteran-0.1.32成功。3.11環境へ再導入して上記186件を実行 |

Linux既存3 failedと8 skipのnode集合を前回B3ログと比較して完全一致を確認した。
失敗は`test_devices::test_timeout_kills_probe_process_tree`のcontainer zombie判定と、
`test_gui_processes::test_profile_manifest_detects_current_and_stale_dependencies` /
`test_profile_manifest_detects_venv_move_without_disabling_profile`のWindows fixture path差。

mypyは修正前Actionsと同じprocesses.py:194/206/212/214/222/227/234における
WinDLL/WinError/get_last_errorの9件。P2で型の絞込みも確認し、新規型エラーは残していない。
既存CLI golden・stage hash・resume/force・出力・GUI import境界を含むB1/B2関連試験は成功。
API/通常GUI・profile manifest/venv・依存・core hash/policy/schemaに変更はない。

## 最終提出と制約

文書追補後の最終HEADでもGitHub Actionsを確認し、そのrun URL・結果は
[PR #24](https://github.com/zawa356/Utteran/pull/24)の最新本文に記載する。
上の具体runはコード変更HEADの証拠として固定し、後続の文書更新によって結果を取り違えない。
Draftを維持し、Ready化・merge・Phase B4着手は行わない。
native hard cancel、whisper.cpp Prompt argv保護、profile fingerprint、実配布受入は従来の未解決事項。

## 修正前97 setup ERRORのnode一覧

各項目のreasonはすべてUnicodeDecodeError。上の分類表のfixtureへ遡って確認済み。

```text
tests/test_config_adapter.py::test_structured_and_legacy_effective_config_hashes_match
tests/test_config_adapter.py::test_adapter_is_pure_and_preserves_inputs
tests/test_config_adapter.py::test_prompt_auto_language_and_speaker_sentinels[None]
tests/test_config_adapter.py::test_prompt_auto_language_and_speaker_sentinels[]
tests/test_config_adapter.py::test_prompt_auto_language_and_speaker_sentinels[synthetic prompt]
tests/test_config_adapter.py::test_cpp_maps_all_native_settings_and_vad
tests/test_config_adapter.py::test_unimplemented_settings_are_never_ignored[glossary-value0-unsupported_setting]
tests/test_config_adapter.py::test_unimplemented_settings_are_never_ignored[extra_arguments-value1-unsupported_setting]
tests/test_config_adapter.py::test_unimplemented_settings_are_never_ignored[openvino_genai-value2-inactive_backend_settings]
tests/test_config_adapter.py::test_unimplemented_settings_are_never_ignored[whisper_cpp-value3-inactive_backend_settings]
tests/test_config_adapter.py::test_unimplemented_settings_are_never_ignored[word_timestamps-never-unsupported_setting]
tests/test_config_adapter.py::test_unimplemented_settings_are_never_ignored[vad-value5-unsupported_setting]
tests/test_config_adapter.py::test_unresolved_context_is_rejected[asr_backend-auto]
tests/test_config_adapter.py::test_unresolved_context_is_rejected[asr_model-auto]
tests/test_config_adapter.py::test_unresolved_context_is_rejected[asr_device-auto]
tests/test_config_adapter.py::test_unresolved_context_is_rejected[compute_type-auto]
tests/test_config_adapter.py::test_alias_and_explicit_auto_resolution_context
tests/test_config_adapter.py::test_genai_and_conflicting_routes_are_explicitly_rejected
tests/test_config_adapter.py::test_validation_error_and_trace_never_contain_private_values
tests/test_config_adapter.py::test_native_conflict_and_context_defaults_are_not_discarded
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[ ]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[\t]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[auto]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[AUTO]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[ auto ]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[Auto ]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[ cpu]
tests/test_config_adapter.py::test_enabled_diarization_rejects_unresolved_or_padded_device[cpu ]
tests/test_config_adapter.py::test_enabled_diarization_keeps_explicit_device[cpu]
tests/test_config_adapter.py::test_enabled_diarization_keeps_explicit_device[cuda:0]
tests/test_config_adapter.py::test_enabled_diarization_keeps_explicit_device[xpu:0]
tests/test_config_adapter.py::test_disabled_diarization_preserves_legacy_device_behavior[]
tests/test_config_adapter.py::test_disabled_diarization_preserves_legacy_device_behavior[ ]
tests/test_config_adapter.py::test_disabled_diarization_preserves_legacy_device_behavior[auto]
tests/test_config_adapter.py::test_disabled_diarization_preserves_legacy_device_behavior[ auto ]
tests/test_worker_client.py::test_subprocess_protocol_faults[missing-module_missing]
tests/test_worker_client.py::test_subprocess_protocol_faults[hang-handshake_timeout]
tests/test_worker_client.py::test_subprocess_protocol_faults[version-protocol_mismatch]
tests/test_worker_client.py::test_subprocess_protocol_faults[unknown-malformed_message]
tests/test_worker_client.py::test_subprocess_protocol_faults[oversized-oversized_message]
tests/test_worker_client.py::test_subprocess_protocol_faults[invalid_utf8-malformed_message]
tests/test_worker_client.py::test_subprocess_protocol_faults[malformed-malformed_message]
tests/test_worker_client.py::test_subprocess_protocol_faults[empty-unexpected_eof]
tests/test_worker_client.py::test_subprocess_protocol_faults[crash-worker_crash]
tests/test_worker_client.py::test_subprocess_protocol_faults[wrong_id-id_mismatch]
tests/test_worker_client.py::test_subprocess_protocol_faults[duplicate-invalid_sequence]
tests/test_worker_client.py::test_subprocess_protocol_faults[flood-core_failure]
tests/test_worker_client.py::test_subprocess_protocol_faults[error-core_failure]
tests/test_worker_client.py::test_subprocess_protocol_faults[zero_error-invalid_sequence]
tests/test_worker_client.py::test_subprocess_protocol_faults[success_crash-worker_crash]
tests/test_worker_client.py::test_subprocess_protocol_faults[wrong_formats-artifact_invalid]
tests/test_worker_client.py::test_subprocess_protocol_faults[terminal_hang-io_timeout]
tests/test_worker_client.py::test_success_exit_and_private_artifact_verification
tests/test_worker_client.py::test_cancel_ack_is_not_stop_evidence[cancel-None-cancelled]
tests/test_worker_client.py::test_cancel_ack_is_not_stop_evidence[cancel_crash-None-worker_crash]
tests/test_worker_client.py::test_cancel_ack_is_not_stop_evidence[cancel-0.1-process_timeout]
tests/test_worker_client.py::test_cancel_ack_is_not_stop_evidence[validate_hang-None-validation_timeout]
tests/test_worker_client.py::test_actual_profile_python_runs_worker
tests/test_worker_client.py::test_start_failure
tests/test_worker_client.py::test_request_secrets_only_on_stdin
tests/test_worker_client.py::test_actual_worker_core_pipeline_and_protocol_isolation
tests/test_worker_client.py::test_actual_worker_core_failure_and_cooperative_cleanup[error-core_failure]
tests/test_worker_client.py::test_actual_worker_core_failure_and_cooperative_cleanup[cancel-cancelled]
tests/test_worker_client.py::test_blocked_stdin_write_is_bounded
tests/test_worker_client.py::test_completion_callback_waits_for_exit_validation
tests/test_worker_client.py::test_external_kill_is_not_cancellation
tests/test_worker_protocol.py::test_handshake_sequence_and_private_projection
tests/test_worker_protocol.py::test_typed_request_rejections[changes0]
tests/test_worker_protocol.py::test_typed_request_rejections[changes1]
tests/test_worker_protocol.py::test_typed_request_rejections[changes2]
tests/test_worker_protocol.py::test_typed_request_rejections[changes3]
tests/test_worker_protocol.py::test_typed_request_rejections[changes4]
tests/test_worker_protocol.py::test_typed_request_rejections[changes5]
tests/test_worker_protocol.py::test_typed_request_rejections[changes6]
tests/test_worker_protocol.py::test_typed_request_rejections[changes7]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[progress_first]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[accepted_twice]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[seq_gap]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[hello_twice]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[request_id]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[job_id]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[attempt_id]
tests/test_worker_protocol.py::test_invalid_event_order_and_ids[unsolicited_cancel_ack]
tests/test_worker_protocol.py::test_worker_schema_offline_artifact
tests/test_worker_runtime.py::test_real_pipeline_adapter_resume_force_and_artifact_compatibility
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[general-extra0]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[asr-extra1]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[asr-extra2]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[ffmpeg-extra3]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[general-extra4]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[general-extra5]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[general-extra6]
tests/test_worker_runtime.py::test_context_unknown_keys_and_execution_paths_are_rejected[general-extra7]
tests/test_worker_runtime.py::test_model_and_download_policy
tests/test_worker_runtime.py::test_local_tokenizer_required_before_core_preflight
tests/test_worker_runtime.py::test_cancel_at_safe_boundary
```
