# B8 契約（`utteran_contracts.setup`）とPhase Aとの差分

全DTOは`schema_version: 1`、`extra="forbid"`、frozen。正規化schemaは
`python -m utteran_contracts.setup_schema --output docs/architecture/b8/setup.schema.json --check`（CI）。
**client生成に使うwire schemaの正本は`setup.schema.json`（実装）**。Phase Aの`openapi.yaml`は歴史的仕様案として
維持し、実装へ合わせて書き換えない（差分は下表）。

## 型

| 型 | 主なfield |
|---|---|
| `SetupPlanRequest` | profile_kind（cpu/cuda/intel/vulkan）、model_refs（1〜8）、diarization_enabled |
| `PlanContent`（hash対象） | 上記＋inventory_fingerprint、estimate、changes、steps、preserved_environment_ids、required_agreements、missing_credentials |
| `SetupPlan` | id `plan_*`、revision、state（proposed/approved/consumed/expired）、inventory_revision、plan_hash、created_at/expires_at＋PlanContent |
| `ApprovalRequest` / `Approval` | plan_hash、accepted_agreement_ids / id `apr_*`、plan_revision、expires_at |
| `SetupStart` | plan_ref{id,revision}、approval_id |
| `SetupOperation` | id `setup_*`、revision、plan_ref、profile_kind、status、held、recovery_required、reason_code、waiting_reason、attempt_number（未dispatchはnull）、attempts、steps（StepProgress）、retry_of、error{code,step_id} |
| `StepProgress` | step_id、kind、status（pending/running/succeeded/failed/interrupted/skipped）、reason_code、evidence{kind,observed_at}\|null |

`plan_hash` = canonical JSON（sort_keys・区切り固定・ensure_ascii=False）のSHA-256。同じrequest＋観測は同じhash。

## Estimate（捏造しない）

download/disk bytesは宣言的catalogのapproximate sizeの合計のみ。venv作成を含む場合（uvの取得量は事前不明）は
**null**。duration_secondsは常にnull、confidenceは`low`。catalogは`utteran.models.catalog`と
一致することをテストで検査（GUI環境はcoreをimportしない）。

## inventory revision / fingerprint

B4の正本から同時点のinventory revisionは得られない（B4 refreshはprofile内probe、未構築profileでは不可）。
そのためB8は**setup観測のfingerprint**（platform、`uv.lock` SHA-256、各profileのexists/compatible/reason、
管理モデルdirの有無）を正本とし、`inventory_revision`は**store内のobservation counter**（fingerprintが
変わるたびに+1、単調）とする。B4 `inventory_revision`とは別物であり、承認・開始・再開の検査は
fingerprintの完全一致で行う。credential有無はfingerprintに含めず、承認・開始・再開時に毎回再検査する。

## Phase A OpenAPIとの意図的差分

| 項目 | Phase A | B8実装 | 理由 |
|---|---|---|---|
| SetupPlan | — | `model_refs`・`diarization_enabled`・`inventory_fingerprint`・`created_at`追加 | 承認対象を明示し、fingerprintで照合 |
| SetupStep | id/kind/depends_on/required/recovery | `target`（profile/model ref）追加 | 実行対象を承認内容に含める |
| StepProgress.evidence | Observation | `StepEvidence{kind,observed_at}` | 内部証拠（PID・path・出力）を公開しない固定語彙 |
| SetupOperation | attempt_number ≥1 必須 | nullable＋`attempts`/`held`/`recovery_required`/`reason_code`/`waiting_reason`/`profile_kind`追加 | 未dispatchで1を捏造しない。B7 Jobと同じ復旧情報 |
| Approval | — | `accepted_agreement_ids`追加 | 実際に選択された同意IDを記録 |
| cancel | cooperative/force | forceは409 `cancel_mode_unsupported` | 安全性未実証 |
| ReasonCode（B6） | — | `step_progress`、`reapproval_required`追加（store専用予約） | step revisionと再承認待ち |
| setup遷移表（B6） | waiting_user→queued、interrupted→queued | 加えて→cancelled | 待機中・中断中setupを明示的に破棄できるようにする（lifecycles §2への追補） |
