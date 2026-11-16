# Engine adapters — 共通の実行契約（正本）

更新: 2026-11-16 JST（開発版`0.1.52`・未リリース）。プログラム全体は[../program/roadmap-2026-11-16.md](../program/roadmap-2026-11-16.md)。

> 本書は「どのエンジンが何を受け付け、B3 workerが何を実行するか」を決める**唯一の表**と、
> それをB4/B5/B7/B3がどう読むかを記述する。**readyの主張ではない**。実行可否は従来どおりB4の証拠で決まる。

## 1. なぜ必要だったか（0.1.51までの状態）

`backend == "faster-whisper"`に相当する判定が、次の6か所に別々に書かれていた。エンジンやモデルを増やすたびに
6か所を同期させる必要があり、1か所でも漏れると「値が無言で無視される」か「B7は通すがB3が拒否する」になる。

| # | 場所 | 0.1.51の内容 |
|---|---|---|
| 1 | B4 `utteran.capabilities.evaluator._settings_issues` | engine別のif連鎖 |
| 2 | B2 `utteran.config_adapter.adapt_settings` | `{"faster-whisper","whisper-cpp"}`の集合とif/else |
| 3 | B3 `utteran.worker_runtime.offline_preflight` | `!= "faster-whisper"`、必須ファイル4個の直書き |
| 4 | B5 `utteran_settings.inheritance`（`worker_unsupported`警告） | `{"auto","faster-whisper"}` |
| 5 | B7 `utteran_gui.job_admission`（`WORKER_RECIPES`、`build_spec`の`backend="faster-whisper"`固定） | 直書き |
| 6 | B7 `utteran_gui.job_dispatcher.plan_request` | `!= "faster-whisper"`、`"asr_backend": "faster-whisper"`固定 |

## 2. 三つの軸を分ける

| 軸 | 正本 | 責務 |
|---|---|---|
| **エンジン** | `utteran_contracts.engines.ENGINE_DECLARATIONS` | 受け付ける設定、タイムスタンプ、prompt/用語集、取消・進捗の粒度、workerが実行する範囲 |
| **モデル** | `utteran.models.catalog`＋モデルmanifest/fingerprint（B4 `ExecutionIdentity`） | ID・revision・重み形式・取得元・ライセンス・実体ディレクトリ |
| **実行プロファイル** | `utteran.capabilities.recipes.RECIPES`＋profile venv | `cpu/cuda/intel/vulkan`の依存、route、device、compute type |

エンジン宣言は`utteran_contracts`に置く。GUIプロセスはcore `utteran`をimportできないが、B7もB5も同じ表を
読む必要があるためである（`utteran_contracts`は推論ライブラリも`utteran`もimportしない境界のまま）。
recipeの`worker_supported`・`word_timestamps`・`non_space_diarization`は宣言から**導出**する
（0.1.51と同じ値であることを試験で固定。B4 catalogueと証拠fingerprintは1 byteも変わらない）。

## 3. 宣言の内容

`EngineDeclaration`（ASR）: `engine` / `model_family` / `settings_namespace` / `runtime_module` / `devices` /
`native_variants` / `word_timestamp_modes` / `non_space_word_timestamps` / `segment_timestamps` / `initial_prompt` /
`glossary` / `vad_threshold` / `condition_on_previous_text` / `extra_arguments` / `unsupported_namespace_fields` /
`structured_adapter` / `cancel_granularity` / `progress_granularity` / `worker`（`status`・`blocker`・`recipes`・
`diarization`・`required_model_files`）。
`DiarizationEngineDeclaration`: `engine` / `devices` / `requires_word_timestamps` / 粒度 / `worker`・`worker_blocker`。
機械可読な形は[b4/capabilities.schema.json](../b4/capabilities.schema.json)の`EngineCatalog`。

現在の値（0.1.52）:

| engine | core/CLI | 構造化adapter(B2) | **worker(B3)** | 閉じている理由 |
|---|---|---|---|---|
| faster-whisper | ✅ | ✅ | ✅ `fw-cpu`・`fw-cuda`（話者分離なし） | — |
| whisper-cpp | ✅ | ✅ | — | `native_argv_privacy`: native CLIへprompt・入力pathをargvで渡す（同一端末の他processから見える） |
| openvino-genai | ✅ | — | — | `offline_load_unverified`: 完全ローカルロードとB4 smoke証拠が未整備 |
| pyannote（話者分離） | ✅ | ✅（設定変換） | — | `offline_load_unverified`: `Pipeline.from_pretrained`が推移的にremoteを解決し得る |

用語集（`glossary`）は全エンジン`unsupported`。**無視せず拒否する**（`unsupported_setting` /
`worker_glossary_unsupported`）。変換（prompt bias等）は束Eで宣言を`prompt_bias`へ進めるときに、
`PROMPT_COMPILER_VERSION`の更新と一緒に実装する。

## 4. 誰が何を読むか

```
ProcessingSettings ──┬─ setting_refusals(engine, settings) ──► B4 evaluator（候補のissue）
                     │                                      └► B2 adapt_settings（変換 or 理由付き拒否）
                     └─ worker_refusals(engine, settings, recipe_id) ──► B7 admission / B7 plan_request
EngineDeclaration.worker ──► B3 offline_preflight（status・diarization・runtime_module・required_model_files）
worker_engines() ──► B5 validationの worker_unsupported 警告
ENGINE_DECLARATIONS ──► core recipes（導出）・asr registry（非スペース言語の話者分離制約）
```

- **値は変換されるか、理由付きで拒否される。黙って落とさない。** `setting_refusals`の同じリストがevaluatorと
  adapterの両方を駆動する（試験: エンジン×設定の全組合せで、宣言とadapterの拒否path/codeが一致）。
- **別エンジンへの自動fallbackはしない。** `get_engine()`は未知の名前に既定値を返さず`unsupported_engine`。
- **凍結された実行計画**は従来どおりB7の`ResolvedSpec`/`JobSnapshot`。`plan_request`はsnapshotの
  `backend`・`recipe_id`・`requested_route.native_variant`・`device`・`compute_type`・`model_id`・`worker_model_dir`・
  `diarization_device`だけからprivate IPC `WorkerExecutionContext`を作る。workerはカタログから再選択しない。
  モデルID（照合用）と実体ディレクトリ（ロード用）の分離（PR #48 P1）はそのまま。
- IPCのwire形式は**無変更**（`worker.schema.json`は同一bytes）。`native_variant`は既存fieldで、native engineの
  ときだけ送る。公開API・B5/B7 DTO・DB layoutも無変更。

## 5. エンジン／経路を1つ開く手順（チェックリスト）

宣言の`worker.status`を`implemented`にするだけでは**完了ではない**。次が全部そろったPRで初めて開く。

1. coreのAdapter（`ASRBackend`実装）が明示deviceで**完全ローカル**にロードでき、取消に応答する。
2. `EngineDeclaration`の能力値がcoreの実挙動と一致（宣言とrecipe・registryの整合試験が通る）。
3. B4 smokeがその経路の実行証拠（route read-back、memory、runtime/hardware）を作れ、`ready`に到達できる。
   `worker_supported=True`だけではadmissionを通らない（`candidate_not_ready`）。
4. B3 `offline_preflight`の事前検査（`required_model_files`、`runtime_module`）がそのモデル形式に合う。
5. 秘匿: prompt・path・tokenがargv・環境変数・ログ・公開DTOへ出ない。
6. 拒否試験: 欠損モデル・不正revision・非対応device/compute・非対応設定が固定codeで止まる。
7. 実機未検証なら`implemented / automated-tested`に留め、画面にも「動作確認済」と出さない。

`tests/test_engine_declarations.py::test_opening_an_engine_in_the_declaration_is_enough_for_b7_and_b3`は、
**試験内だけ**でwhisper.cppを開いた宣言に差し替え、B7 plan → private IPC → B3 `explicit_config` →
`offline_preflight`までエンジン名・native variant・モデルID・凍結ディレクトリが運ばれることを確認する
（dispatcher・workerの編集なし）。出荷される表ではwhisper.cppは閉じたままである。

## 6. 変えていない境界

- GUIプロセスはcore `utteran`をimportしない。`utteran_contracts`は`utteran`・`utteran_gui`・推論ライブラリをimportしない。
- B6〜B9（CAS、冪等性、lease/fencing、単一heavy slot、Outbox/SSE、復旧）は無変更。
- 既定起動（旧GUI/CLI）は無変更。v1は明示opt-inのまま。`--next-ui-v1`は開発専用のまま。
- 推論アルゴリズム・話者分離アルゴリズムは無変更。未信頼のサードパーティーpluginの動的ロードは導入しない。
