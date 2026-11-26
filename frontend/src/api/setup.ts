/**
 * 束F: typed client for B8 setup, F-B diagnostics and F-A readiness (`--next-ui-v1` only).
 * Contract authority: code first (`src/utteran_gui/setup_api.py`, `setup_diagnostics.py`,
 * `setup_readiness.py`), then `docs/architecture/b8/setup.schema.json`.
 *
 * - Every POST that changes state sends one intent with its Idempotency-Key and is never resent
 *   here; an unknown outcome is resolved by an explicit re-check with the *same* intent.
 * - Plan views never carry the verification settings (only a digest); nothing here logs bodies.
 */
import { bool, int, list, oneOf, optStr, record, ShapeError, str } from "./guards";
import type { HttpClient } from "./http";
import { IDEMPOTENCY_KEY_PATTERN, type MutationIntent } from "./idempotency";
import { ifMatch } from "./v1";
import type { JsonObject } from "./v1Resources";

export const SETUP_WRITE_TIMEOUT_MS = 30_000;
const PLAN_ID = /^plan_[0-9a-f]{32}$/;
const SETUP_ID = /^setup_[0-9a-f]{32}$/;

export const PROFILE_KINDS = ["cpu", "cuda", "intel", "vulkan"] as const;
export type ProfileKind = (typeof PROFILE_KINDS)[number];
export const TERMINAL_SETUP = new Set(["succeeded", "failed", "cancelled"]);

export type Tri = boolean | null;

export interface Diagnostics {
  generation: number;
  phase: "idle" | "running" | "completed" | "failed";
  os: { platform: "windows" | "linux" | "other"; venvBuildSupported: boolean };
  gpu: {
    state: "detected" | "none" | "unknown";
    reasonCode: string | null;
    adapters: { name: string; vendor: string; driverVersion: string | null }[];
    driverDownloadUrl: string;
  } | null;
  memoryTotalBytes: number | null;
  diskFreeBytes: number | null;
  vcredist: { state: "present" | "missing" | "partial" | "unknown" | "not_applicable"; version: string | null; downloadUrl: string } | null;
  runtimePhase: "idle" | "running" | "completed" | "failed";
  runtime: {
    profile: string | null;
    reasonCode: string | null;
    ctranslate2Cuda: Tri;
    torchCuda: Tri;
    torchXpu?: Tri;
    vulkan?: Tri;
    openvinoGpu?: Tri;
    cudaDeviceCount: number | null;
    attempts: number;
  } | null;
  recommendation: { profile: ProfileKind; reasonCodes: string[]; confident: boolean } | null;
}

export interface SetupStepView {
  id: string;
  kind: string;
  target: string | null;
}

export interface SetupPlan {
  id: string;
  revision: number;
  state: "proposed" | "approved" | "consumed" | "expired";
  profileKind: ProfileKind;
  modelRefs: string[];
  diarizationEnabled: boolean;
  planHash: string;
  expiresAt: string;
  estimate: { downloadBytes: number | null; diskBytes: number | null };
  changes: { kind: string; id: string; action: string; reason: string }[];
  steps: SetupStepView[];
  preserved: string[];
  requiredAgreements: string[];
  missingCredentials: string[];
  verification: { recipeId: string; modelRef: string; settingsSha256: string } | null;
}

export interface SetupApproval {
  id: string;
  planId: string;
  planRevision: number;
}

export interface StepProgressView {
  stepId: string;
  kind: string;
  status: "pending" | "running" | "succeeded" | "failed" | "interrupted" | "skipped";
  reasonCode: string | null;
  evidence: string | null;
}

export interface SetupOperation {
  id: string;
  revision: number;
  planId: string;
  profileKind: ProfileKind;
  status: string;
  held: boolean;
  recoveryRequired: string | null;
  reasonCode: string;
  waitingReason: string | null;
  attemptNumber: number | null;
  steps: StepProgressView[];
  retryOf: string | null;
  error: { code: string; stepId: string | null } | null;
  createdAt: string;
  updatedAt: string;
}

export interface ReadinessCandidate {
  recipeId: string;
  profileId: string;
  modelId: string;
  device: string;
  runnable: "ready" | "blocked" | "unknown";
  issues: string[];
  workerIssues: string[];
  verifiable: boolean;
}

export interface SetupDefaults {
  state: "pending" | "waiting" | "created" | "kept" | "not_applicable";
  preset: { id: string; revision: number } | null;
  policy: { id: string; revision: number } | null;
}

function ref(value: unknown, where: string): { id: string; revision: number } | null {
  if (value === null || value === undefined) return null;
  const r = record(value, where);
  return { id: str(r.id, `${where}.id`), revision: int(r.revision, `${where}.revision`) };
}

export function parseDefaults(raw: unknown): SetupDefaults {
  const r = record(raw, "defaults");
  version(r, "defaults");
  return {
    state: oneOf(r.state, ["pending", "waiting", "created", "kept", "not_applicable"] as const, "defaults.state"),
    preset: ref(r.preset, "defaults.preset"),
    policy: ref(r.policy, "defaults.policy"),
  };
}

export interface Readiness {
  state: "ready" | "not_ready";
  reasonCodes: string[];
  selected: ReadinessCandidate | null;
  candidates: ReadinessCandidate[];
}

function version(r: Record<string, unknown>, where: string): void {
  if (r.schema_version !== 1) throw new ShapeError(`${where}.schema_version`);
}
function match(value: unknown, pattern: RegExp, where: string): string {
  const text = str(value, where);
  if (!pattern.test(text)) throw new ShapeError(where);
  return text;
}
function optInt(value: unknown, where: string): number | null {
  return value === null || value === undefined ? null : int(value, where);
}
function tri(value: unknown, where: string): Tri {
  return value === null || value === undefined ? null : bool(value, where);
}
const PHASES = ["idle", "running", "completed", "failed"] as const;

export function parseDiagnostics(raw: unknown): Diagnostics {
  const r = record(raw, "diagnostics");
  version(r, "diagnostics");
  const os = record(r.os, "diagnostics.os");
  const gpu = r.gpu === null ? null : record(r.gpu, "diagnostics.gpu");
  const vc = r.vcredist === null ? null : record(r.vcredist, "diagnostics.vcredist");
  const runtime = r.runtime === null ? null : record(r.runtime, "diagnostics.runtime");
  const rec = r.recommendation === null ? null : record(r.recommendation, "diagnostics.recommendation");
  return {
    generation: int(r.generation, "generation"),
    phase: oneOf(r.phase, PHASES, "phase"),
    os: { platform: oneOf(os.platform, ["windows", "linux", "other"] as const, "os.platform"), venvBuildSupported: bool(os.venv_build_supported, "os.venv") },
    gpu: gpu && {
      state: oneOf(gpu.state, ["detected", "none", "unknown"] as const, "gpu.state"),
      reasonCode: optStr(gpu.reason_code),
      adapters: list(gpu.adapters, "gpu.adapters").map((item, i) => {
        const a = record(item, `gpu.adapters[${i}]`);
        return { name: str(a.name, "adapter.name"), vendor: str(a.vendor, "adapter.vendor"), driverVersion: optStr(a.driver_version) };
      }),
      driverDownloadUrl: str(gpu.driver_download_url, "gpu.driver_download_url"),
    },
    memoryTotalBytes: r.memory === null ? null : optInt(record(r.memory, "memory").total_bytes, "memory.total"),
    diskFreeBytes: r.disk === null ? null : optInt(record(r.disk, "disk").free_bytes, "disk.free"),
    vcredist: vc && {
      state: oneOf(vc.state, ["present", "missing", "partial", "unknown", "not_applicable"] as const, "vcredist.state"),
      version: optStr(vc.version),
      downloadUrl: str(vc.download_url, "vcredist.download_url"),
    },
    runtimePhase: oneOf(r.runtime_phase, PHASES, "runtime_phase"),
    runtime: runtime && {
      profile: optStr(runtime.profile),
      reasonCode: optStr(runtime.reason_code),
      ctranslate2Cuda: tri(runtime.ctranslate2_cuda, "runtime.ct2"),
      torchCuda: tri(runtime.torch_cuda, "runtime.torch"),
      torchXpu: tri(runtime.torch_xpu ?? null, "runtime.xpu"),
      vulkan: tri(runtime.vulkan ?? null, "runtime.vulkan"),
      openvinoGpu: tri(runtime.openvino_gpu ?? null, "runtime.openvino"),
      cudaDeviceCount: optInt(runtime.cuda_device_count, "runtime.count"),
      attempts: int(runtime.attempts, "runtime.attempts"),
    },
    recommendation: rec && {
      profile: oneOf(rec.profile, PROFILE_KINDS, "recommendation.profile"),
      reasonCodes: list(rec.reason_codes, "recommendation.reason_codes").map((c) => str(c, "reason")),
      confident: bool(rec.confident, "recommendation.confident"),
    },
  };
}

export function parsePlan(raw: unknown): SetupPlan {
  const r = record(raw, "plan");
  version(r, "plan");
  const estimate = record(r.estimate, "plan.estimate");
  const verification = r.verification === undefined ? null : record(r.verification, "plan.verification");
  return {
    id: match(r.id, PLAN_ID, "plan.id"),
    revision: int(r.revision, "plan.revision"),
    state: oneOf(r.state, ["proposed", "approved", "consumed", "expired"] as const, "plan.state"),
    profileKind: oneOf(r.profile_kind, PROFILE_KINDS, "plan.profile_kind"),
    modelRefs: list(r.model_refs, "plan.model_refs").map((m) => str(m, "model_ref")),
    diarizationEnabled: bool(r.diarization_enabled, "plan.diarization_enabled"),
    planHash: match(r.plan_hash, /^[0-9a-f]{64}$/, "plan.plan_hash"),
    expiresAt: str(r.expires_at, "plan.expires_at"),
    estimate: { downloadBytes: optInt(estimate.download_bytes, "estimate.download"), diskBytes: optInt(estimate.disk_bytes, "estimate.disk") },
    changes: list(r.changes, "plan.changes").map((item, i) => {
      const c = record(item, `plan.changes[${i}]`);
      return { kind: str(c.resource_kind, "change.kind"), id: str(c.resource_id, "change.id"), action: str(c.action, "change.action"), reason: str(c.reason_code, "change.reason") };
    }),
    steps: list(r.steps, "plan.steps").map((item, i) => {
      const s = record(item, `plan.steps[${i}]`);
      return { id: str(s.id, "step.id"), kind: str(s.kind, "step.kind"), target: optStr(s.target) };
    }),
    preserved: list(r.preserved_environment_ids, "plan.preserved").map((p) => str(p, "preserved")),
    requiredAgreements: list(r.required_agreements, "plan.agreements").map((a) => str(a, "agreement")),
    missingCredentials: list(r.missing_credentials, "plan.credentials").map((a) => str(a, "credential")),
    verification: verification && {
      recipeId: str(verification.recipe_id, "verification.recipe"),
      modelRef: str(verification.model_ref, "verification.model"),
      settingsSha256: match(verification.settings_sha256, /^[0-9a-f]{64}$/, "verification.sha"),
    },
  };
}

export function parseApproval(raw: unknown): SetupApproval {
  const r = record(raw, "approval");
  version(r, "approval");
  return { id: match(r.id, /^apr_[0-9a-f]{32}$/, "approval.id"), planId: match(r.plan_id, PLAN_ID, "approval.plan_id"), planRevision: int(r.plan_revision, "approval.plan_revision") };
}

export function parseOperation(raw: unknown): SetupOperation {
  const r = record(raw, "operation");
  version(r, "operation");
  const plan = record(r.plan_ref, "operation.plan_ref");
  const error = r.error === null ? null : record(r.error, "operation.error");
  return {
    id: match(r.id, SETUP_ID, "operation.id"),
    revision: int(r.revision, "operation.revision"),
    planId: match(plan.id, PLAN_ID, "operation.plan_ref.id"),
    profileKind: oneOf(r.profile_kind, PROFILE_KINDS, "operation.profile_kind"),
    status: str(r.status, "operation.status"),
    held: bool(r.held, "operation.held"),
    recoveryRequired: optStr(r.recovery_required),
    reasonCode: str(r.reason_code, "operation.reason_code"),
    waitingReason: optStr(r.waiting_reason),
    attemptNumber: optInt(r.attempt_number, "operation.attempt_number"),
    steps: list(r.steps, "operation.steps").map((item, i) => {
      const s = record(item, `operation.steps[${i}]`);
      const evidence = s.evidence === null ? null : record(s.evidence, "step.evidence");
      return {
        stepId: str(s.step_id, "step.id"),
        kind: str(s.kind, "step.kind"),
        status: oneOf(s.status, ["pending", "running", "succeeded", "failed", "interrupted", "skipped"] as const, "step.status"),
        reasonCode: optStr(s.reason_code),
        evidence: evidence ? str(evidence.kind, "evidence.kind") : null,
      };
    }),
    retryOf: optStr(r.retry_of),
    error: error && { code: str(error.code, "error.code"), stepId: optStr(error.step_id) },
    createdAt: str(r.created_at, "operation.created_at"),
    updatedAt: str(r.updated_at, "operation.updated_at"),
  };
}

export function parseOperationList(raw: unknown): { items: SetupOperation[]; nextCursor: string | null } {
  const r = record(raw, "operations");
  version(r, "operations");
  return { items: list(r.items, "operations.items").map(parseOperation), nextCursor: optStr(r.next_cursor) };
}

function parseCandidate(raw: unknown, where: string): ReadinessCandidate {
  const c = record(raw, where);
  return {
    recipeId: str(c.recipe_id, "candidate.recipe"),
    profileId: str(c.profile_id, "candidate.profile"),
    modelId: str(c.model_id, "candidate.model"),
    device: str(c.device, "candidate.device"),
    runnable: oneOf(c.runnable, ["ready", "blocked", "unknown"] as const, "candidate.runnable"),
    issues: list(c.issues, "candidate.issues").map((x) => str(x, "issue")),
    workerIssues: list(c.worker_issues, "candidate.worker").map((x) => str(x, "issue")),
    verifiable: bool(c.verifiable, "candidate.verifiable"),
  };
}

export function parseReadiness(raw: unknown): Readiness {
  const r = record(raw, "readiness");
  version(r, "readiness");
  return {
    state: oneOf(r.state, ["ready", "not_ready"] as const, "readiness.state"),
    reasonCodes: list(r.reason_codes, "readiness.reasons").map((x) => str(x, "reason")),
    selected: r.selected === null ? null : parseCandidate(r.selected, "readiness.selected"),
    candidates: list(r.candidates, "readiness.candidates").map((c, i) => parseCandidate(c, `readiness.candidates[${i}]`)),
  };
}

export interface SetupApi {
  getDiagnostics(signal?: AbortSignal): Promise<Diagnostics>;
  refreshDiagnostics(runtime: boolean, force: boolean): Promise<Diagnostics>;
  createPlan(intent: MutationIntent<JsonObject>): Promise<SetupPlan>;
  getPlan(id: string): Promise<SetupPlan>;
  discardPlan(id: string, revision: number): Promise<void>;
  approve(plan: SetupPlan, intent: MutationIntent<JsonObject>): Promise<SetupApproval>;
  start(intent: MutationIntent<JsonObject>): Promise<SetupOperation>;
  getOperation(id: string, signal?: AbortSignal): Promise<SetupOperation>;
  listOperations(signal?: AbortSignal): Promise<SetupOperation[]>;
  act(op: SetupOperation, action: "retry" | "resume" | "cancel", intent: MutationIntent<JsonObject>): Promise<SetupOperation>;
  readiness(body: JsonObject, signal?: AbortSignal): Promise<Readiness>;
  /** GET: read only. */
  getDefaults(operationId: string): Promise<SetupDefaults>;
  /** POST: idempotent on the server (one transaction keyed by the plan); safe to repeat. */
  applyDefaults(operationId: string): Promise<SetupDefaults>;
}

const MAX_PAGES = 20;

export function createSetupApi(http: HttpClient): SetupApi {
  async function write<T>(path: string, intent: MutationIntent<JsonObject>, parse: (raw: unknown) => T, extra: Record<string, string> = {}): Promise<T> {
    if (!IDEMPOTENCY_KEY_PATTERN.test(intent.key)) throw new Error("idempotency key");
    intent.attempts += 1;
    intent.state = "sent";
    try {
      const response = await http.request(
        { method: "POST", path, body: intent.body, headers: { "Idempotency-Key": intent.key, ...extra }, timeoutMs: SETUP_WRITE_TIMEOUT_MS },
        parse,
      );
      intent.state = "accepted";
      return response.data;
    } catch (error) {
      const e = error as { outcomeUnknown?: boolean; status?: number | null };
      intent.state = !e.outcomeUnknown && typeof e.status === "number" && e.status >= 400 && e.status < 500 ? "rejected" : "unknown";
      throw error;
    }
  }
  const planPath = (id: string) => {
    if (!PLAN_ID.test(id)) throw new ShapeError("plan id");
    return `/api/v1/setup-plans/${id}`;
  };
  const opPath = (id: string) => {
    if (!SETUP_ID.test(id)) throw new ShapeError("setup id");
    return `/api/v1/setup-operations/${id}`;
  };
  return {
    async getDiagnostics(signal) {
      return (await http.request({ method: "GET", path: "/api/v1/setup-diagnostics", signal }, parseDiagnostics)).data;
    },
    async refreshDiagnostics(runtime, force) {
      // Starts (or joins) one background pass; it answers at once and is safe to repeat.
      return (
        await http.request(
          { method: "POST", path: "/api/v1/setup-diagnostics/refresh", body: { schema_version: 1, runtime, force }, timeoutMs: SETUP_WRITE_TIMEOUT_MS },
          parseDiagnostics,
        )
      ).data;
    },
    createPlan: (intent) => write("/api/v1/setup-plans", intent, parsePlan),
    async getPlan(id) {
      return (await http.request({ method: "GET", path: planPath(id) }, parsePlan)).data;
    },
    async discardPlan(id, revision) {
      await http.request({ method: "DELETE", path: planPath(id), headers: { "If-Match": ifMatch(revision) }, timeoutMs: SETUP_WRITE_TIMEOUT_MS }, () => null);
    },
    approve: (plan, intent) => write(`${planPath(plan.id)}/approve`, intent, parseApproval, { "If-Match": ifMatch(plan.revision) }),
    start: (intent) => write("/api/v1/setup-operations", intent, parseOperation),
    async getOperation(id, signal) {
      return (await http.request({ method: "GET", path: opPath(id), signal }, parseOperation)).data;
    },
    async listOperations(signal) {
      const items: SetupOperation[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const data: { items: SetupOperation[]; nextCursor: string | null } = (
          await http.request({ method: "GET", path: "/api/v1/setup-operations", query: { cursor }, signal }, parseOperationList)
        ).data;
        items.push(...data.items);
        cursor = data.nextCursor;
        if (cursor === null) break;
      }
      return items;
    },
    act: (op, action, intent) => write(`${opPath(op.id)}/${action}`, intent, parseOperation, { "If-Match": ifMatch(op.revision) }),
    async getDefaults(id) {
      return (await http.request({ method: "GET", path: `${opPath(id)}/defaults` }, parseDefaults)).data;
    },
    async applyDefaults(id) {
      return (await http.request({ method: "POST", path: `${opPath(id)}/defaults`, body: { schema_version: 1 }, timeoutMs: SETUP_WRITE_TIMEOUT_MS }, parseDefaults)).data;
    },
    async readiness(body, signal) {
      return (await http.request({ method: "POST", path: "/api/v1/readiness-checks", body, timeoutMs: 120_000, signal }, parseReadiness)).data;
    },
  };
}
