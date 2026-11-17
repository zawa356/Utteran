/**
 * Typed client for the v1 routes the S5-A single-job path uses (`--next-ui-v1` only).
 *
 * Contract authority (code first): `src/utteran_gui/settings_api.py` (B5 Preset/Policy/validations),
 * `src/utteran_gui/jobs_api.py` + `src/utteran_contracts/jobs.py` (B7 resolution, jobs, queue),
 * offline schemas `docs/architecture/b5/preview.schema.json`, `b7/jobs.schema.json`.
 *
 * - Every write carries an Idempotency-Key from a `MutationIntent` (one key per user intent).
 *   This client never resends; an explicit retry of the same intent reuses its key and body.
 * - Responses are parsed into allowlisted views. The resolution token is kept only in the returned
 *   object (never logged, rendered or stored). Public B7 views carry no path, prompt or transcript.
 * - Nothing here is used by the legacy (`--next-ui`) client.
 */
import { ApiError } from "./errors";
import { bool, int, list, oneOf, record, ShapeError, str } from "./guards";
import type { HttpClient } from "./http";
import { IDEMPOTENCY_KEY_PATTERN, type MutationIntent } from "./idempotency";
import { ifMatch } from "./v1";

export type JsonObject = Record<string, unknown>;

export const OUTPUT_FORMATS_V1 = ["srt", "vtt", "json", "txt", "md"] as const;
export type OutputFormatV1 = (typeof OUTPUT_FORMATS_V1)[number];

export const JOB_STATUSES_V1 = [
  "queued",
  "held",
  "blocked",
  "running",
  "waiting_user",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
] as const;
export type JobStatusV1 = (typeof JOB_STATUSES_V1)[number];
/** No further change without an explicit action (retry/rerun are later stages). */
export const FINAL_STATUSES_V1: ReadonlySet<JobStatusV1> = new Set(["succeeded", "failed", "cancelled"]);

const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const JOB_ID = /^job_[0-9a-f]{32}$/;
const RESOURCE_ID = /^(pre|pol)_[0-9a-f]{32}$/;
const TOKEN = /^v1\.[0-9a-f]{32}\.[0-9]{13}\.[0-9a-f]{64}$/;
const CODE = /^[a-z][a-z0-9_]{0,63}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T[0-9:.+\-Z]{1,40}$/;

export function isJobIdV1(value: unknown): value is string {
  return typeof value === "string" && JOB_ID.test(value);
}

function version(r: JsonObject, where: string): void {
  if (r.schema_version !== 1) throw new ShapeError(`${where}.schema_version`);
}
function id(value: unknown, where: string, pattern = ID): string {
  const text = str(value, where);
  if (!pattern.test(text)) throw new ShapeError(where);
  return text;
}
function positive(value: unknown, where: string): number {
  const n = int(value, where);
  if (n < 1) throw new ShapeError(where);
  return n;
}
function code(value: unknown, where: string): string {
  const text = str(value, where);
  if (!CODE.test(text)) throw new ShapeError(where);
  return text;
}
function optCode(value: unknown, where: string): string | null {
  return value === null || value === undefined ? null : code(value, where);
}
function timestamp(value: unknown, where: string): string {
  const text = str(value, where);
  if (!TIMESTAMP.test(text) || Number.isNaN(Date.parse(text))) throw new ShapeError(where);
  return text;
}
function codes(value: unknown, where: string): string[] {
  return list(value, where).map((item, i) => code(item, `${where}[${i}]`));
}

export interface Ref {
  id: string;
  revision: number;
}
function ref(value: unknown, where: string): Ref {
  const r = record(value, where);
  return { id: id(r.id, `${where}.id`), revision: positive(r.revision, `${where}.revision`) };
}

// ------------------------------------------------------------------ B5 Preset / Policy

export interface PresetV1 {
  id: string;
  revision: number;
  name: string;
  /** The whole `ProcessingSettings` as stored (wire form). Never sent back as a Preset write. */
  settings: JsonObject;
}

export interface PolicyV1 {
  id: string;
  revision: number;
  name: string;
  mode: "auto" | "fixed";
  allowedProfileIds: string[];
  objective: "quality" | "speed" | "balanced";
  fallback: "none" | "preapproved";
}

/** Minimal shape check; B5 already validated the full contract when it was saved. */
export function parseSettings(raw: unknown, where = "settings"): JsonObject {
  const r = record(raw, where);
  version(r, where);
  const asr = record(r.asr, `${where}.asr`);
  str(asr.model, `${where}.asr.model`);
  str(asr.language, `${where}.asr.language`);
  str(asr.backend, `${where}.asr.backend`);
  bool(record(r.diarization, `${where}.diarization`).enabled, `${where}.diarization.enabled`);
  const output = record(r.output, `${where}.output`);
  list(output.formats, `${where}.output.formats`).forEach((f, i) => oneOf(f, OUTPUT_FORMATS_V1, `${where}.output.formats[${i}]`));
  record(r.postprocessing, `${where}.postprocessing`);
  return r;
}

export function parsePreset(raw: unknown): PresetV1 {
  const r = record(raw, "preset");
  version(r, "preset");
  return { id: id(r.id, "preset.id", RESOURCE_ID), revision: positive(r.revision, "preset.revision"), name: str(r.name, "preset.name"), settings: parseSettings(r.settings) };
}

export function parsePolicy(raw: unknown): PolicyV1 {
  const r = record(raw, "policy");
  version(r, "policy");
  return {
    id: id(r.id, "policy.id", RESOURCE_ID),
    revision: positive(r.revision, "policy.revision"),
    name: str(r.name, "policy.name"),
    mode: oneOf(r.mode, ["auto", "fixed"] as const, "policy.mode"),
    allowedProfileIds: list(r.allowed_profile_ids, "policy.allowed_profile_ids").map((p, i) => id(p, `policy.allowed_profile_ids[${i}]`)),
    objective: oneOf(r.objective, ["quality", "speed", "balanced"] as const, "policy.objective"),
    fallback: oneOf(r.fallback, ["none", "preapproved"] as const, "policy.fallback"),
  };
}

export type CollectionKind = "presets" | "execution-policies";

export interface CollectionPage<T> {
  revision: number;
  items: T[];
  nextCursor: string | null;
}

function parsePage<T>(parse: (raw: unknown) => T, where: string) {
  return (raw: unknown): CollectionPage<T> => {
    const r = record(raw, where);
    version(r, where);
    const next = r.next_cursor;
    if (next !== null && (typeof next !== "string" || next.length === 0 || next.length > 512)) throw new ShapeError(`${where}.next_cursor`);
    return { revision: positive(r.revision, `${where}.revision`), items: list(r.items, `${where}.items`).map(parse), nextCursor: next };
  };
}

export interface CollectionAll<T> {
  revision: number;
  items: T[];
  /** More pages exist than this client reads; the view must say so (never silently cut). */
  truncated: boolean;
}

/** Pages are read until the end or this many (100 items each); beyond it `truncated` is set. */
export const MAX_COLLECTION_PAGES = 10;

// ------------------------------------------------------------------ B5 validation

export interface ValidatedFileV1 {
  clientFileId: string;
  valid: boolean;
  effectiveSettings: JsonObject | null;
  issues: { path: string; code: string }[];
  warnings: { path: string; code: string }[];
  /** B5 can only say "unsupported" or "unverified" — never ready. */
  workerSupport: "unsupported" | "unverified";
}

export interface ValidationV1 {
  valid: boolean;
  presetRef: Ref;
  policyRef: Ref;
  files: ValidatedFileV1[];
}

function issueList(value: unknown, where: string): { path: string; code: string }[] {
  return list(value, where).map((item, i) => {
    const r = record(item, `${where}[${i}]`);
    return { path: str(r.path, `${where}[${i}].path`), code: code(r.code, `${where}[${i}].code`) };
  });
}

export function parseValidation(raw: unknown): ValidationV1 {
  const r = record(raw, "validation");
  version(r, "validation");
  return {
    valid: bool(r.valid, "validation.valid"),
    presetRef: ref(r.preset_ref, "validation.preset_ref"),
    policyRef: ref(r.policy_ref, "validation.policy_ref"),
    files: list(r.files, "validation.files").map((item, i) => {
      const f = record(item, `files[${i}]`);
      return {
        clientFileId: str(f.client_file_id, "client_file_id"),
        valid: bool(f.valid, "valid"),
        effectiveSettings: f.effective_settings === null ? null : parseSettings(f.effective_settings, "effective_settings"),
        issues: issueList(f.issues, "issues"),
        warnings: issueList(f.warnings, "warnings"),
        workerSupport: oneOf(f.worker_support, ["unsupported", "unverified"] as const, "worker_support"),
      };
    }),
  };
}

// ------------------------------------------------------------------ B7 resolution / jobs / queue

/** Allowlisted `ResolvedSpecView` (no settings, model path or prompt exist in it). */
export interface SpecViewV1 {
  candidateId: string;
  recipeId: string;
  profileId: "cpu" | "cuda" | "intel" | "vulkan";
  modelId: string;
  modelRevision: string;
  backend: string;
  device: string;
  computeType: string;
  policyRef: Ref;
  reasonCodes: string[];
  confidence: "low" | "medium";
  evidenceKinds: string[];
  warnings: { path: string; code: string }[];
  fallbackCandidateIds: string[];
  workerSupport: "supported";
}

export function parseSpec(raw: unknown, where = "spec"): SpecViewV1 {
  const r = record(raw, where);
  return {
    candidateId: id(r.candidate_id, `${where}.candidate_id`),
    recipeId: id(r.recipe_id, `${where}.recipe_id`),
    profileId: oneOf(r.profile_id, ["cpu", "cuda", "intel", "vulkan"] as const, `${where}.profile_id`),
    modelId: str(r.model_id, `${where}.model_id`).slice(0, 200),
    modelRevision: id(r.model_revision, `${where}.model_revision`),
    backend: oneOf(r.backend, ["faster-whisper", "whisper-cpp", "openvino-genai"] as const, `${where}.backend`),
    device: oneOf(r.device, ["cpu", "cuda", "gpu", "npu"] as const, `${where}.device`),
    computeType: id(r.compute_type, `${where}.compute_type`),
    policyRef: ref(r.policy_ref, `${where}.policy_ref`),
    reasonCodes: list(r.reason_codes, `${where}.reason_codes`).map((c, i) => id(c, `${where}.reason_codes[${i}]`)),
    confidence: oneOf(r.confidence, ["low", "medium"] as const, `${where}.confidence`),
    evidenceKinds: list(r.evidence_kinds, `${where}.evidence_kinds`).map((k, i) => oneOf(k, ["smoke", "benchmark", "static_rule"] as const, `${where}.evidence_kinds[${i}]`)),
    warnings: issueList(r.warnings, `${where}.warnings`),
    fallbackCandidateIds: list(r.fallback_candidate_ids, `${where}.fallback_candidate_ids`).map((c, i) => id(c, `${where}.fallback[${i}]`)),
    workerSupport: oneOf(r.worker_support, ["supported"] as const, `${where}.worker_support`),
  };
}

export interface ResolutionV1 {
  id: string;
  /** Opaque, short-lived, single-use. Sent back once in `JobSubmit`; never displayed or stored. */
  token: string;
  expiresAt: string;
  files: { clientFileId: string; inputIdentity: string; spec: SpecViewV1 }[];
}

export function parseResolution(raw: unknown): ResolutionV1 {
  const r = record(raw, "resolution");
  version(r, "resolution");
  const token = str(r.token, "resolution.token");
  if (!TOKEN.test(token)) throw new ShapeError("resolution.token");
  const files = list(r.files, "resolution.files").map((item, i) => {
    const f = record(item, `files[${i}]`);
    return { clientFileId: str(f.client_file_id, "client_file_id"), inputIdentity: id(f.input_identity, "input_identity", /^in_[0-9a-f]{32}$/), spec: parseSpec(f.spec) };
  });
  if (files.length === 0) throw new ShapeError("resolution.files");
  return { id: id(r.id, "resolution.id", /^res_[0-9a-f]{32}$/), token, expiresAt: timestamp(r.expires_at, "resolution.expires_at"), files };
}

export interface JobV1 {
  id: string;
  revision: number;
  status: JobStatusV1;
  held: boolean;
  blockers: string[];
  blockReason: string | null;
  recoveryRequired: string | null;
  reasonCode: string;
  clientFileId: string;
  presetRef: Ref;
  policyRef: Ref;
  spec: SpecViewV1;
  attempts: number;
  lastAttemptState: "active" | "released" | "interrupted" | null;
  artifactCount: number | null;
  errorCode: string | null;
  /** B7 keeps `retry`: a new job with the same snapshot, linked to the job it retries. */
  retryOf: string | null;
  /** `rerun`: a different logical job (new snapshot); kept only to show where it came from. */
  rerunOf: string | null;
  createdAt: string;
  updatedAt: string;
}

function optJobId(value: unknown, where: string): string | null {
  return value === null ? null : id(value, where, JOB_ID);
}

export function parseJob(raw: unknown): JobV1 {
  const r = record(raw, "job");
  version(r, "job");
  const snapshot = record(r.snapshot, "job.snapshot");
  const attempts = list(r.attempts, "job.attempts").map((a, i) => oneOf(record(a, `attempts[${i}]`).state, ["active", "released", "interrupted"] as const, "attempt.state"));
  const result = r.result === null ? null : record(r.result, "job.result");
  const error = r.error === null ? null : record(r.error, "job.error");
  return {
    id: id(r.id, "job.id", JOB_ID),
    revision: positive(r.revision, "job.revision"),
    status: oneOf(r.status, JOB_STATUSES_V1, "job.status"),
    held: bool(r.held, "job.held"),
    blockers: codes(r.blockers, "job.blockers"),
    blockReason: optCode(r.block_reason, "job.block_reason"),
    recoveryRequired: optCode(r.recovery_required, "job.recovery_required"),
    reasonCode: code(r.reason_code, "job.reason_code"),
    clientFileId: str(snapshot.client_file_id, "snapshot.client_file_id"),
    presetRef: ref(snapshot.preset_ref, "snapshot.preset_ref"),
    policyRef: ref(snapshot.policy_ref, "snapshot.policy_ref"),
    spec: parseSpec(snapshot.spec, "snapshot.spec"),
    attempts: attempts.length,
    lastAttemptState: attempts.at(-1) ?? null,
    artifactCount: result === null ? null : positive(result.artifact_count, "result.artifact_count"),
    errorCode: error === null ? null : code(error.code, "error.code"),
    retryOf: optJobId(r.retry_of, "job.retry_of"),
    rerunOf: optJobId(r.rerun_of, "job.rerun_of"),
    createdAt: timestamp(r.created_at, "job.created_at"),
    updatedAt: timestamp(r.updated_at, "job.updated_at"),
  };
}

export interface JobBatchV1 {
  jobs: JobV1[];
  queueRevision: number;
}

export function parseJobBatchV1(raw: unknown): JobBatchV1 {
  const r = record(raw, "batch");
  version(r, "batch");
  const jobs = list(r.jobs, "batch.jobs").map(parseJob);
  if (jobs.length === 0) throw new ShapeError("batch.jobs");
  return { jobs, queueRevision: positive(r.queue_revision, "batch.queue_revision") };
}

export interface JobListV1 {
  revision: number;
  items: JobV1[];
  nextCursor: string | null;
}

export function parseJobList(raw: unknown): JobListV1 {
  const r = record(raw, "jobs");
  version(r, "jobs");
  const next = r.next_cursor;
  // B7 `JobList.next_cursor`: opaque, at most 200 characters, or null on the last page.
  if (next !== null && (typeof next !== "string" || next.length === 0 || next.length > 200)) throw new ShapeError("jobs.next_cursor");
  return { revision: int(r.revision, "jobs.revision"), items: list(r.items, "jobs.items").map(parseJob), nextCursor: next };
}

export interface QueueViewV1 {
  revision: number;
  dispatch: "running" | "paused";
  activeItemId: string | null;
  items: { id: string; resourceId: string; resourceKind: string; position: number | null; held: boolean; blockers: string[] }[];
}

export function parseQueue(raw: unknown): QueueViewV1 {
  const r = record(raw, "queue");
  version(r, "queue");
  return {
    revision: positive(r.revision, "queue.revision"),
    dispatch: oneOf(r.dispatch, ["running", "paused"] as const, "queue.dispatch"),
    activeItemId: r.active_item_id === null ? null : id(r.active_item_id, "queue.active_item_id", /^qi_[0-9a-f]{32}$/),
    items: list(r.items, "queue.items").map((item, i) => {
      const x = record(item, `queue.items[${i}]`);
      return {
        id: id(x.id, "item.id", /^qi_[0-9a-f]{32}$/),
        resourceId: id(x.resource_id, "item.resource_id"),
        resourceKind: code(x.resource_kind, "item.resource_kind"),
        position: x.position === null ? null : positive(x.position, "item.position"),
        held: bool(x.held, "item.held"),
        blockers: codes(x.blockers, "item.blockers"),
      };
    }),
  };
}

export const JOB_VISIBILITY_FILTERS = ["visible", "hidden", "all"] as const;
export type JobVisibilityFilter = (typeof JOB_VISIBILITY_FILTERS)[number];

/** History-list visibility of one job (layout v7). Its revision is not the job revision. */
export interface JobVisibilityV1 {
  jobId: string;
  hidden: boolean;
  revision: number;
  updatedAt: string | null;
}

export function parseVisibility(raw: unknown): JobVisibilityV1 {
  const r = record(raw, "visibility");
  version(r, "visibility");
  const revision = int(r.revision, "visibility.revision");
  if (revision < 0) throw new ShapeError("visibility.revision");
  return {
    jobId: id(r.job_id, "visibility.job_id", JOB_ID),
    hidden: bool(r.hidden, "visibility.hidden"),
    revision,
    updatedAt: r.updated_at === null ? null : timestamp(r.updated_at, "visibility.updated_at"),
  };
}

export const ARTIFACT_STATES = ["available", "missing", "legacy", "none"] as const;
export const ARTIFACT_INTEGRITY = ["verified", "changed", "unverified"] as const;

/** Read-only view of a job's own output directory: no file name, no path. */
export interface JobArtifactsV1 {
  jobId: string;
  state: (typeof ARTIFACT_STATES)[number];
  items: { format: OutputFormatV1; size: number; integrity: (typeof ARTIFACT_INTEGRITY)[number] }[];
  unexpectedCount: number;
}

export function parseArtifacts(raw: unknown): JobArtifactsV1 {
  const r = record(raw, "artifacts");
  version(r, "artifacts");
  const unexpected = int(r.unexpected_count, "artifacts.unexpected_count");
  if (unexpected < 0) throw new ShapeError("artifacts.unexpected_count");
  return {
    jobId: id(r.job_id, "artifacts.job_id", JOB_ID),
    state: oneOf(r.state, ARTIFACT_STATES, "artifacts.state"),
    items: list(r.items, "artifacts.items").map((item, i) => {
      const x = record(item, `artifacts.items[${i}]`);
      const size = int(x.size, "artifact.size");
      if (size < 0) throw new ShapeError("artifact.size");
      return { format: oneOf(x.format, OUTPUT_FORMATS_V1, "artifact.format"), size, integrity: oneOf(x.integrity, ARTIFACT_INTEGRITY, "artifact.integrity") };
    }),
    unexpectedCount: unexpected,
  };
}

// ------------------------------------------------------------------ C2c deletion

export const DELETION_MODES = ["history", "history_and_artifacts"] as const;
export type DeletionModeV1 = (typeof DELETION_MODES)[number];
export const DELETION_REFUSALS = [
  "not_finished",
  "deletion_in_progress",
  "not_succeeded",
  "legacy_output",
  "evidence_unverified",
  "platform_unverified",
  "directory_missing",
  "directory_ambiguous",
  "marker_mismatch",
  "unexpected_entries",
  "unsafe_entry",
  "artifact_changed",
  "artifact_missing",
  "filesystem_error",
] as const;
export type DeletionRefusalV1 = (typeof DELETION_REFUSALS)[number];
export const RETAINED_CODES = ["backups", "related_jobs", "replay_public_views", "event_skeleton", "core_cache", "output_files"] as const;
export type RetainedCodeV1 = (typeof RETAINED_CODES)[number];
const DELETION_TOKEN = /^dp1\.[0-9]{13}\.[0-9a-f]{64}$/;

/** Every key of a deletion answer is known: an unknown field is a malformed answer, not ignored. */
function exactKeys(r: JsonObject, allowed: readonly string[], where: string): void {
  for (const key of Object.keys(r)) if (!allowed.includes(key)) throw new ShapeError(`${where}.${key}`);
  for (const key of allowed) if (!(key in r)) throw new ShapeError(`${where}.${key}`);
}

/** Read-only preflight of one deletion (`POST /jobs/{id}/deletion-plans`). No name, no path. */
export interface JobDeletionPlanV1 {
  jobId: string;
  mode: DeletionModeV1;
  allowed: boolean;
  refusal: DeletionRefusalV1 | null;
  /** Opaque, short-lived; sent back unchanged with the explicit approval. Never rendered. */
  planToken: string | null;
  expiresAt: string | null;
  resume: boolean;
  artifacts: { format: OutputFormatV1; size: number }[];
  relatedJobs: number;
  retained: RetainedCodeV1[];
}

export function parseDeletionPlan(raw: unknown): JobDeletionPlanV1 {
  const r = record(raw, "deletionPlan");
  exactKeys(r, ["schema_version", "job_id", "mode", "allowed", "refusal", "plan_token", "expires_at", "resume", "artifacts", "related_jobs", "retained"], "deletionPlan");
  version(r, "deletionPlan");
  const allowed = bool(r.allowed, "deletionPlan.allowed");
  const planToken = r.plan_token === null ? null : str(r.plan_token, "deletionPlan.plan_token");
  if (planToken !== null && !DELETION_TOKEN.test(planToken)) throw new ShapeError("deletionPlan.plan_token");
  const refusal = r.refusal === null ? null : oneOf(r.refusal, DELETION_REFUSALS, "deletionPlan.refusal");
  const expiresAt = r.expires_at === null ? null : timestamp(r.expires_at, "deletionPlan.expires_at");
  // The same consistency rule as the server DTO: allowed <=> token <=> no refusal.
  if (allowed !== (planToken !== null) || allowed !== (refusal === null) || allowed !== (expiresAt !== null)) throw new ShapeError("deletionPlan");
  const mode = oneOf(r.mode, DELETION_MODES, "deletionPlan.mode");
  const artifacts = list(r.artifacts, "deletionPlan.artifacts").map((item, i) => {
    const x = record(item, `deletionPlan.artifacts[${i}]`);
    exactKeys(x, ["format", "size"], "deletionPlan.artifact");
    return { format: oneOf(x.format, OUTPUT_FORMATS_V1, "deletionPlan.artifact.format"), size: int(x.size, "deletionPlan.artifact.size") };
  });
  if (mode === "history" && artifacts.length > 0) throw new ShapeError("deletionPlan.artifacts");
  return {
    jobId: id(r.job_id, "deletionPlan.job_id", JOB_ID),
    mode,
    allowed,
    refusal,
    planToken,
    expiresAt,
    resume: bool(r.resume, "deletionPlan.resume"),
    artifacts,
    relatedJobs: int(r.related_jobs, "deletionPlan.related_jobs"),
    retained: list(r.retained, "deletionPlan.retained").map((item, i) => oneOf(item, RETAINED_CODES, `deletionPlan.retained[${i}]`)),
  };
}

/** Outcome of one deletion. `incomplete`: the history record was kept (nothing is claimed erased). */
export interface JobDeletionV1 {
  jobId: string;
  mode: DeletionModeV1;
  outcome: "completed" | "incomplete";
  deletedAt: string | null;
  artifactsRemoved: number;
  artifactsRemaining: number;
  retained: RetainedCodeV1[];
}

export function parseDeletion(raw: unknown): JobDeletionV1 {
  const r = record(raw, "deletion");
  exactKeys(r, ["schema_version", "job_id", "mode", "outcome", "deleted_at", "artifacts_removed", "artifacts_remaining", "retained"], "deletion");
  version(r, "deletion");
  const outcome = oneOf(r.outcome, ["completed", "incomplete"] as const, "deletion.outcome");
  const deletedAt = r.deleted_at === null ? null : timestamp(r.deleted_at, "deletion.deleted_at");
  const remaining = int(r.artifacts_remaining, "deletion.artifacts_remaining");
  if ((outcome === "completed") !== (deletedAt !== null) || (outcome === "completed" && remaining > 0)) throw new ShapeError("deletion");
  return {
    jobId: id(r.job_id, "deletion.job_id", JOB_ID),
    mode: oneOf(r.mode, DELETION_MODES, "deletion.mode"),
    outcome,
    deletedAt,
    artifactsRemoved: int(r.artifacts_removed, "deletion.artifacts_removed"),
    artifactsRemaining: remaining,
    retained: list(r.retained, "deletion.retained").map((item, i) => oneOf(item, RETAINED_CODES, `deletion.retained[${i}]`)),
  };
}

/** A 2xx answer that does not fit what was asked (not a refusal: the outcome cannot be read). */
function malformed(status: number): ApiError {
  return new ApiError({ kind: "malformed", status, retryable: true });
}

/** The strong ETag (`"<revision>"`) must agree with the body revision; otherwise the answer is malformed. */
function etagRevision(headers: Headers, body: number, status: number): number {
  const etag = headers.get("ETag");
  if (etag !== null && etag !== `"${body}"`) throw malformed(status);
  return body;
}

// ------------------------------------------------------------------ client

export interface V1ResourcesApi {
  listAll<K extends CollectionKind>(kind: K, signal?: AbortSignal): Promise<CollectionAll<K extends "presets" ? PresetV1 : PolicyV1>>;
  getRevision<K extends CollectionKind>(kind: K, resourceId: string, revision: number, signal?: AbortSignal): Promise<K extends "presets" ? PresetV1 : PolicyV1>;
  create<K extends CollectionKind>(kind: K, intent: MutationIntent<JsonObject>): Promise<K extends "presets" ? PresetV1 : PolicyV1>;
  validate(intent: MutationIntent<JsonObject>): Promise<ValidationV1>;
  resolve(intent: MutationIntent<JsonObject>): Promise<ResolutionV1>;
  submit(intent: MutationIntent<JsonObject>): Promise<{ status: number; batch: JobBatchV1 }>;
  getJob(jobId: string, signal?: AbortSignal): Promise<JobV1>;
  /** One page of `GET /api/v1/jobs` (B7: oldest first, 100 per page, opaque `next_cursor`). */
  listJobs(cursor?: string | null, signal?: AbortSignal, visibility?: JobVisibilityFilter): Promise<JobListV1>;
  getVisibility(jobId: string, signal?: AbortSignal): Promise<JobVisibilityV1>;
  /**
   * `PUT /jobs/{id}/visibility` with `If-Match` = the visibility revision. Idempotent on the server
   * (asking for the stored state changes nothing), so an unknown outcome is resolved by sending the
   * same request again; no Idempotency-Key is involved. Nothing is deleted by this call.
   */
  setVisibility(jobId: string, expectedRevision: number, hidden: boolean, signal?: AbortSignal): Promise<JobVisibilityV1>;
  getArtifacts(jobId: string, signal?: AbortSignal): Promise<JobArtifactsV1>;
  getQueue(signal?: AbortSignal): Promise<QueueViewV1>;
  setDispatch(intent: MutationIntent<JsonObject>, expectedRevision: number): Promise<QueueViewV1>;
  cancel(jobId: string, expectedRevision: number, intent: MutationIntent<JsonObject>): Promise<{ status: number; job: JobV1 }>;
  /** B7 retry: a *new* job with the same snapshot, `retry_of` = the job retried (Design Freeze §7). */
  retry(jobId: string, expectedRevision: number, intent: MutationIntent<JsonObject>): Promise<{ status: number; job: JobV1 }>;
  /** C2c preflight. Read-only on the server: nothing is deleted, no key is involved. */
  planDeletion(jobId: string, mode: DeletionModeV1, signal?: AbortSignal): Promise<JobDeletionPlanV1>;
  /**
   * C2c execution with the plan token, `If-Match` = the job revision the plan was made for, and an
   * Idempotency-Key (the same intent replays the first answer). Never sent without an explicit approval.
   */
  deleteJob(jobId: string, expectedRevision: number, intent: MutationIntent<JsonObject>): Promise<JobDeletionV1>;
}

export const V1_WRITE_TIMEOUT_MS = 30_000;
/** Resolution hashes the input file and asks every allowed profile: allow a long answer. */
export const V1_RESOLVE_TIMEOUT_MS = 120_000;

function resourceId(value: string): string {
  if (!RESOURCE_ID.test(value)) throw new ShapeError("resource id");
  return value;
}
function jobId(value: string): string {
  if (!JOB_ID.test(value)) throw new ShapeError("job id");
  return value;
}

export function createV1ResourcesApi(http: HttpClient): V1ResourcesApi {
  /** One send of one intent. State `sent` → `accepted` / `rejected` / `unknown` (never resent here). */
  async function write<T>(path: string, method: "POST" | "PATCH", intent: MutationIntent<JsonObject>, parse: (raw: unknown) => T, extra: Record<string, string> = {}, timeoutMs = V1_WRITE_TIMEOUT_MS) {
    if (!IDEMPOTENCY_KEY_PATTERN.test(intent.key)) throw new Error("idempotency key");
    intent.attempts += 1;
    intent.state = "sent";
    try {
      const response = await http.request({ method, path, body: intent.body, headers: { "Idempotency-Key": intent.key, ...extra }, timeoutMs }, parse);
      intent.state = "accepted";
      return response;
    } catch (error) {
      const e = error as { outcomeUnknown?: boolean; status?: number | null };
      intent.state = !e.outcomeUnknown && typeof e.status === "number" && e.status >= 400 && e.status < 500 ? "rejected" : "unknown";
      throw error;
    }
  }
  const parserFor = (kind: CollectionKind): ((raw: unknown) => PresetV1 | PolicyV1) => (kind === "presets" ? parsePreset : parsePolicy);

  return {
    async listAll(kind, signal) {
      const parse = parsePage<PresetV1 | PolicyV1>(parserFor(kind), kind);
      const items: (PresetV1 | PolicyV1)[] = [];
      let cursor: string | null = null;
      let revision = 0;
      for (let page = 0; page < MAX_COLLECTION_PAGES; page += 1) {
        const data: CollectionPage<PresetV1 | PolicyV1> = (await http.request({ method: "GET", path: `/api/v1/${kind}`, query: { cursor }, signal }, parse)).data;
        if (page === 0) revision = data.revision;
        items.push(...data.items);
        cursor = data.nextCursor;
        if (cursor === null) return { revision, items, truncated: false } as never;
      }
      return { revision, items, truncated: true } as never;
    },
    async getRevision(kind, id, revision, signal) {
      const response = await http.request({ method: "GET", path: `/api/v1/${kind}/${resourceId(id)}`, query: { revision }, signal }, parserFor(kind));
      // The answer must be exactly the revision asked for (B5 revisions are immutable).
      if (response.data.id !== id || response.data.revision !== revision) throw malformed(response.status);
      return response.data as never;
    },
    async create(kind, intent) {
      return (await write(`/api/v1/${kind}`, "POST", intent, parserFor(kind))).data as never;
    },
    async validate(intent) {
      return (await write("/api/v1/validations", "POST", intent, parseValidation)).data;
    },
    async resolve(intent) {
      return (await write("/api/v1/execution-resolutions", "POST", intent, parseResolution, {}, V1_RESOLVE_TIMEOUT_MS)).data;
    },
    async submit(intent) {
      const response = await write("/api/v1/jobs", "POST", intent, parseJobBatchV1, {}, V1_RESOLVE_TIMEOUT_MS);
      return { status: response.status, batch: response.data };
    },
    async getJob(id, signal) {
      const response = await http.request({ method: "GET", path: `/api/v1/jobs/${jobId(id)}`, signal }, parseJob);
      if (response.data.id !== id) throw malformed(response.status);
      etagRevision(response.headers, response.data.revision, response.status);
      return response.data;
    },
    async listJobs(cursor, signal, visibility) {
      // The filter is part of the cursor's MAC on the server: every page of one listing sends the same one.
      return (await http.request({ method: "GET", path: "/api/v1/jobs", query: { cursor: cursor ?? null, visibility: visibility ?? null }, signal }, parseJobList)).data;
    },
    async getVisibility(id, signal) {
      const response = await http.request({ method: "GET", path: `/api/v1/jobs/${jobId(id)}/visibility`, signal }, parseVisibility);
      if (response.data.jobId !== id) throw malformed(response.status);
      etagRevision(response.headers, response.data.revision, response.status);
      return response.data;
    },
    async setVisibility(id, expectedRevision, hidden, signal) {
      const response = await http.request(
        { method: "PUT", path: `/api/v1/jobs/${jobId(id)}/visibility`, body: { schema_version: 1, hidden }, headers: { "If-Match": ifMatch(expectedRevision) }, timeoutMs: V1_WRITE_TIMEOUT_MS, signal },
        parseVisibility,
      );
      // The answer must describe the job and the state that was asked for.
      if (response.data.jobId !== id || response.data.hidden !== hidden) throw malformed(response.status);
      etagRevision(response.headers, response.data.revision, response.status);
      return response.data;
    },
    async getArtifacts(id, signal) {
      const response = await http.request({ method: "GET", path: `/api/v1/jobs/${jobId(id)}/artifacts`, signal }, parseArtifacts);
      if (response.data.jobId !== id) throw malformed(response.status);
      return response.data;
    },
    async getQueue(signal) {
      const response = await http.request({ method: "GET", path: "/api/v1/queue", signal }, parseQueue);
      etagRevision(response.headers, response.data.revision, response.status);
      return response.data;
    },
    async setDispatch(intent, expectedRevision) {
      return (await write("/api/v1/queue/control", "PATCH", intent, parseQueue, { "If-Match": ifMatch(expectedRevision) })).data;
    },
    async cancel(id, expectedRevision, intent) {
      const response = await write(`/api/v1/jobs/${jobId(id)}/cancel`, "POST", intent, parseJob, { "If-Match": ifMatch(expectedRevision) });
      return { status: response.status, job: response.data };
    },
    async retry(id, expectedRevision, intent) {
      const response = await write(`/api/v1/jobs/${jobId(id)}/retry`, "POST", intent, parseJob, { "If-Match": ifMatch(expectedRevision) });
      // The answer must be the new job that retries exactly this one.
      if (response.data.retryOf !== id || response.data.id === id) throw malformed(response.status);
      return { status: response.status, job: response.data };
    },
    async planDeletion(id, mode, signal) {
      const response = await http.request(
        { method: "POST", path: `/api/v1/jobs/${jobId(id)}/deletion-plans`, body: { schema_version: 1, mode }, timeoutMs: V1_WRITE_TIMEOUT_MS, signal },
        parseDeletionPlan,
      );
      if (response.data.jobId !== id || response.data.mode !== mode) throw malformed(response.status);
      return response.data;
    },
    async deleteJob(id, expectedRevision, intent) {
      const response = await write(`/api/v1/jobs/${jobId(id)}/deletions`, "POST", intent, parseDeletion, { "If-Match": ifMatch(expectedRevision) });
      // The answer must be about exactly this job and the mode that was approved.
      if (response.data.jobId !== id || response.data.mode !== intent.body.mode) throw malformed(response.status);
      return response.data;
    },
  };
}
