# Job Snapshot（private・不変）

`utteran_contracts.jobs.JobSnapshot`（schema_version 1）:

| field | 内容 |
|---|---|
| `created_at` | 受理時刻（UTC） |
| `preset_ref` / `policy_ref` | B5 immutable revision参照 |
| `preset_digest` / `policy_digest` | 解決時に読んだrevision内容のSHA-256（提出時に再照合） |
| `policy` | Policy revisionのコピー（B5 DBは別DBのため凍結コピー。pre-start再検査に使用） |
| `common_overrides` / `file` | B5 `SettingOverride` / `FileInput`（入力・出力path、file overrides） |
| `input_identity` | `InputIdentity`: 正規化path、platform、device、inode、size、mtime_ns、ctime_ns、**content SHA-256** |
| `provenance` | B5 `Provenance`（absent/null/empty/value・normalizationを維持） |
| `resolved` | `ResolvedSpec`: candidate_id、`candidate_fingerprint`、recipe、profile id/generation/fingerprint、model id/revision/files fingerprint、backend、requested_route、device/index、compute、effective settings、adapter `b4.1`、prompt compiler `b2-verbatim`、inventory revision、policy_ref、reason codes、confidence、evidence kinds、warnings、`fallback_specs`、runtime fingerprint、`worker_support="supported"`、`worker_model_dir`（private） |
| `resume_mode` | resume / fresh / force |

`candidate_fingerprint`は identity全体・requested route・runnable・各observationの
(kind,state,source,reason,fingerprint) のSHA-256。observation時刻は含めない（refreshごとに変わるため）。

## 保存とDB上の不変性

- `job_snapshots(operation_id, batch_id, body, digest, retry_of, rerun_of, created_at)`。
  `body`はcanonical JSON、`digest`はそのSHA-256。**UPDATE/DELETEはtriggerでABORT**。
  読取時にdigestを再計算し、不一致は`store_corrupt`（書換えを信用しない）。
- B6 `operation_private`には`{"b7_snapshot_digest": ...}`のmarkerのみ。
- `_check_private`を通すため、認証情報（`token`等のkey、`hf_`値）は保存できない。
  resolution tokenはDBに保存しない。
- retryは**同じbody**を新Jobへコピー（`retry_of`）。rerunは新しい解決・新snapshot（`rerun_of`）。
  元Jobのsnapshot・terminal statusは変更しない。

## 公開projection

`Job`はstore内でallowlistから再構成: id、revision、status、held、blockers、block_reason、
recovery_required、reason_code、`JobSnapshotSummary`、attempts、retry_of、rerun_of、
result（legacy core job id・artifact数）、error code、時刻。Prompt・terms・input/output path・
model dir・argv・nonce・env・token・raw errorはコピーしない（sentinelテストで検証）。

## 保持・削除（未解決ゲート）

B6と同様に**暗号化なし・無期限保持**。snapshotはPrompt・path等の秘密値を含み得る。
ACL（POSIX 0700/0600のみ実装、Windows ACL未検証）・バックアップ・保持期間・完全削除は未策定。
