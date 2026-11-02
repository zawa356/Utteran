# Phase A schema実装カバレッジ

生成基準: Phase A HEAD `60d9bdd` のOpenAPI、74 schema。未実装はProposedのまま。

| Phase A schema | B1 |
|---|---|
| `ASRSettings` | 実装 |
| `Approval` | 未実装・後続 |
| `ApprovalRequest` | 未実装・後続 |
| `ArgumentCapability` | 未実装・後続 |
| `Attempt` | 未実装・後続 |
| `CancelRequest` | 未実装・後続 |
| `Candidate` | 未実装・後続 |
| `Capabilities` | 未実装・後続 |
| `DiagnosticEntry` | 未実装・後続 |
| `DiagnosticEntryList` | 未実装・後続 |
| `DiarizationSettings` | 実装 |
| `EditingSession` | 実装 |
| `Environment` | 未実装・後続 |
| `EnvironmentList` | 未実装・後続 |
| `Error` | 実装 |
| `ErrorDetail` | 実装 |
| `Estimate` | 未実装・後続 |
| `EventEnvelope` | 未実装・後続 |
| `EventPayload` | 未実装・後続 |
| `Evidence` | 未実装・後続 |
| `ExtraArguments` | 実装 |
| `FallbackSpec` | 未実装・後続 |
| `FasterWhisperSettings` | 実装 |
| `FileInput` | 実装 |
| `GenAISettings` | 実装 |
| `Issue` | 実装 |
| `Job` | 未実装・後続 |
| `JobBatch` | 未実装・後続 |
| `JobList` | 未実装・後続 |
| `JobSnapshot` | 未実装・後続 |
| `JobSubmit` | 未実装・後続 |
| `Observation` | 未実装・後続 |
| `OutputSettings` | 実装 |
| `ParameterCapability` | 未実装・後続 |
| `Policy` | 実装 |
| `PolicyList` | 未実装・後続 |
| `PolicyWrite` | 実装 |
| `PostprocessingSettings` | 実装 |
| `Preset` | 実装 |
| `PresetList` | 未実装・後続 |
| `PresetWrite` | 実装 |
| `ProcessingSettings` | 実装 |
| `Provenance` | 未実装・後続 |
| `Queue` | 未実装・後続 |
| `QueueControl` | 未実装・後続 |
| `QueueItem` | 未実装・後続 |
| `QueueItemPatch` | 未実装・後続 |
| `QueueOrder` | 未実装・後続 |
| `Ref` | 実装 |
| `RefreshOperation` | 未実装・後続 |
| `RefreshRequest` | 未実装・後続 |
| `RerunRequest` | 未実装・後続 |
| `Resolution` | 未実装・後続 |
| `ResolvedFile` | 未実装・後続 |
| `ResolvedSpec` | 未実装・後続 |
| `RetryRequest` | 未実装・後続 |
| `Route` | 未実装・後続 |
| `RouteObservation` | 未実装・後続 |
| `SelectionAlternative` | 未実装・後続 |
| `SettingOverride` | 実装 |
| `SetupChange` | 未実装・後続 |
| `SetupOperation` | 未実装・後続 |
| `SetupOperationList` | 未実装・後続 |
| `SetupPlan` | 未実装・後続 |
| `SetupPlanRequest` | 未実装・後続 |
| `SetupStart` | 未実装・後続 |
| `SetupStep` | 未実装・後続 |
| `StepProgress` | 未実装・後続 |
| `SyncResource` | 未実装・後続 |
| `SyncSnapshot` | 未実装・後続 |
| `VadSettings` | 実装 |
| `ValidatedFile` | 未実装・後続 |
| `ValidationResult` | 未実装・後続 |
| `WhisperCppSettings` | 実装 |

Contract / VersionedContract / SchemaVersionはB1補助型でありPhase Aの追加APIではない。

## B4追加実装との対応

B1 artifactと上表はB1時点の範囲を維持する。B4はCandidate/Observation/Route/Evidence/
SelectionAlternative/Resolutionの準備部分を追加した。型名・意図的差分・未実装APIの正本は
[対応表](../b4/README.md#phase-aとの対応と意図的差分)、生成物は
[capabilities.schema.json](../b4/capabilities.schema.json)。B1/B3 artifactは変更しない。
