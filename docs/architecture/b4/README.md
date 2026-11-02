# Phase B4 — Capability Inventory / Candidate Resolver

2026-11-02、製品version 0.1.33。B3 `5ef7bab7ba81cab7afc1fe23b85abc8da399ec1b`
からの追加基盤。既存CLI/GUIの通常経路、workerのbackend、ジョブ、profileは変更しない。
作業開始時GitHub確認: main `1097aef`、B2 `294c383`、PR #24はOPEN/Draft、base B2。
B4はB3をbaseとするstacked Draft PRとしてレビューする。

## 監査: 既存の正本と責任分界

| 既存機能 | 正本・観測元 | 選択/表示の意味 | B4との関係 |
|---|---|---|---|
| CLI ASR auto | `devices._auto_selection()`、`asr.registry.create_asr_backend()` | CUDA→Vulkan→OV+Vulkan→OV→CPU。native側に既存fallback | 維持。B4 resolverとは独立 |
| faster-whisper device/compute | `devices.select_faster_whisper_device()` | CTranslate2の利用可能device/precision | 維持。一般的なlibrary対応からrecipeを追加しない |
| whisper.cpp route | `native.VARIANT_NAMES`、`asr.whisper_cpp` | cpu/openvino/vulkan/openvino_vulkan、モデル別IRが必要 | 登録recipeの根拠。build/executable存在はsmokeではない |
| GenAI・言語制約 | `asr.registry.validate_asr_configuration()`、`asr.openvino_genai` | 別backend、非スペース言語/autoと話者分離の併用禁止 | 同じNON_SPACE_LANGUAGESを参照 |
| profile既定選択 | `profiles.default_profile_name()`、CLI/run.ps1 | 明示config→単一既存profile→エラー | B4から呼出し/変更なし |
| profile layout/extras | `profiles.PROFILE_EXTRAS`、setup.ps1 | 登録済みcpu/cuda/intel/vulkan | collectorはこの定義を参照、新設なし |
| GUI候補表示 | `EnvironmentService.snapshot()`、`derive_options()` | profile CLI devices/models/nativeの現行摘要 | 未接続。表示をB4 readinessへ切り替えない |
| GUI軽量OS検出 | `hardware` | venv導入前のOS/runtime観測 | B4から自動呼出しなし |
| GUI profile互換性 | `CliAdapter._profile_compatibility()` | manifestとlock全体hash/extras/path照合 | 維持。collectorも同じ保守的条件を独立に読取、等価性テスト |
| structured設定変換 | `config_adapter.adapt_settings()`、`application` | B2変換/preflight/実行。GenAI structured変換未対応 | 実行には接続しない。B4は準備値のみ |
| B3 worker | `worker_runtime.offline_preflight()` | local完全FW、explicit device/compute、話者分離なし、single input | execution_pathで別判定。workerコード無変更 |

## 追加モジュール

| モジュール | 責務 |
|---|---|
| `utteran_contracts.capabilities` | version 1、immutable DTO、固定source/reason allowlist、private projection |
| `utteran_contracts.capability_schema` | B1/B3と独立したオフラインschemaと`--check` |
| `utteran.capabilities.recipes` | coreが管理する7 recipeの単一正本 |
| `utteran.capabilities.evaluator` | 条件fingerprint、鮮度正規化、pure readiness、inventory作成 |
| `utteran.capabilities.resolver` | readyのみのauto/fixed、hard filter、比較可能benchmark、静的順位 |
| `utteran.capabilities.collector` | 明示的なread-only収集、差替可能provider |
| `utteran.capabilities.bridge` | profile Pythonの独立CLI。旧CLI loggingを起動しない |
| `utteran_gui.capability_inventory` | subprocessと軽量DTOだけ。core/heavy import禁止 |
| `utteran_gui.capability_transport` | 読取中のstdout/stderr上限、子孫回収、専用environment allowlist |

## 開発入口

```console
python -B -m utteran.capabilities.bridge recipes
# private JSONはstdin。出力もprivateとして保持し、通常ログに出さない。
python -B -m utteran.capabilities.bridge refresh < private-refresh-request.json
python -m utteran_contracts.capability_schema --output docs/architecture/b4/capabilities.schema.json --check
```

GUI側は明示的に`CapabilityInventoryAdapter(cli).refresh(InventoryRefreshRequest(...))`を呼ぶ。
呼出しはGUI起動・API・既存EnvironmentServiceには追加していない。profile Pythonに`-I -B`を渡し、
cwdをrepoへ固定、shellなし、stdinへ設定、既定20秒timeout。子はmetadata readerのみで孫processなし。
失敗は固定reasonと`inventory=null`、成功時もprofile/settings/path/revisionのscopeを再検査する。
旧profileにB4が未導入なら`bridge_unavailable`。自動syncしない。

core利用は`make_inventory(deployments, settings, execution_path, revision=..., now=...)`、
`resolve(inventory, policy, now=...)`。時計・refresh・I/Oはresolveに含まれない。
標準collectorは実機smokeを生成しないため、成功してもready候補ゼロが正常である。

## Phase Aとの対応と意図的差分

| Phase A OpenAPI | B4型・範囲 |
|---|---|
| Observation | CapabilityObservation: kind/source/expiry/実route観測を追加。stateは4値 |
| Candidate | CapabilityCandidate + DeploymentInventory/ExecutionIdentity。software_supportもObservation |
| Route / RouteObservation | RequestedRouteとsmokeのinitialized_route/encoder_observed/decoder_observed |
| Capabilities | RecipeCatalog / CapabilityRecipe。HTTPやparameter/argument registry全体は未実装 |
| Evidence | smoke Observation、構造化BenchmarkEvidence、resultのevidence_kinds/confidence |
| SelectionAlternative | 候補全体を凍結するSelectionAlternative、eligible/reason_codes |
| Resolution / ResolvedSpec / FallbackSpec | ResolvedSelectionの準備値、selected/preapprovedの具体構成。永続job/token/attemptではない |
| Environment / EnvironmentList | 未実装。candidate readinessとprofile lifecycleを混同しない |
| Policy / PolicyWrite / ProcessingSettings | B1を再利用。Policyのfixed/fallback制約もschemaへ引継ぐ |

B4補助型はInventorySnapshot、InventoryExclusion、SelectionIssue、InventoryRefreshRequest/Result等。
Phase AのHTTP構造の完全実装・74 schema全実装ではない。新API、SSE、永続DB、queue、setupはない。
B5へpolicy/preset採用、B7へrevision再確認/token/job snapshot/attempt/fallback実行、B8へsetupを引継ぐ。

[recipes](recipes.md) / [observations](observations.md) / [resolution](resolution.md) /
[compatibility](compatibility.md) / [validation](validation.md)

PR #25のP2/P3レビュー修正・検証は[追補](pr25-review-validation.md)。
