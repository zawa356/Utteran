import { ApiError } from "../../api/errors";
import { MutationIntent, type RandomSource } from "../../api/idempotency";
import {
  FINAL_STATUSES_V1,
  type JobV1,
  type JsonObject,
  type QueueViewV1,
  type ResolutionV1,
  type V1ResourcesApi,
  type ValidationV1,
} from "../../api/v1Resources";
import { SingleFlightPoller } from "../../transport/poller";
import { browserScheduler, type Scheduler } from "../../transport/scheduler";
import { classifyStartFailure } from "../admission";
import type { CollectionState, InputRow } from "../inputCollection";
import { toApiError } from "../operation";
import { checkPath, type PathProblem } from "../paths";
import { type ReadonlyStore, Store } from "../store";
import type { FormState } from "../transcription";
import type { LibraryState } from "./library";
import { canonical, clone, commonOverrides, effectiveSettings, knownWorkerLimits } from "./settings";

/**
 * UI-3 S5-A: one input → B5/B7 two-step admission → durable job status (`--next-ui-v1` only).
 *
 * 1. `prepare()` freezes the EditingSession built from the frozen Preset/Policy revisions, the
 *    primary input and the screen differences. `POST /api/v1/validations` must return exactly the
 *    effective settings the screen computed (otherwise nothing is sent), then
 *    `POST /api/v1/execution-resolutions` returns the resolution (candidate, evidence, warnings).
 *    "Valid" (B5) is not "ready"; only the B7 resolution decides, from B4 evidence.
 * 2. `submit()` sends `JobSubmit` with the resolution token and the *same frozen* session, once.
 *    Any later edit makes the resolution stale (`isStale`) and blocks the submit until a new
 *    `prepare()`. An expired token is never refreshed silently.
 * 3. A 4xx is a refusal (no job). Anything else (no answer, timeout, 5xx, unreadable 2xx) is
 *    `unknown`: the intent (key + frozen body) is kept in page memory only and is resent only by an
 *    explicit `recheck()`; no other key or body is ever sent automatically. B7 checks the same
 *    key + body replay before the token, so a re-check after a lost 202 returns the original job.
 * 4. "Accepted" shows the created job id/revision only; the status is the last `GET /jobs/{id}`
 *    (bounded single-flight polling, never regressing to an older revision). No progress ratio.
 * 5. The queue may be paused (first start, recovery): shown as such, resumed only by an explicit
 *    `resumeQueue()` (CAS on the queue revision + its own key), never as a side effect.
 * 6. Cancel is cooperative only; "requested" is not "cancelled" (GET decides). Each cancel freezes its
 *    target job id, If-Match revision and intent (key + body) in one `CancelContext`; a re-check
 *    resends exactly that, never to whatever job is shown now. While a cancel is pending or unknown
 *    the shown job cannot be switched (follow/prepare/dismiss) until it is re-checked or the person
 *    explicitly discards it; an answer is applied only to the job and target it was sent for (PR #48).
 * Leaving the page stops the polling, never the server job.
 */

export type V1Issue =
  | { field: "preset" | "policy"; problem: "none" | "loading" | "error" | "missing" }
  | { field: "input"; problem: PathProblem | "none" }
  | { field: "output"; problem: PathProblem }
  | { field: "row_overrides"; problem: "unsupported" };

export interface V1Evaluation {
  issues: V1Issue[];
  /** Advisory only (the server decides): limits of the B3 worker seen in the Preset. */
  workerLimits: string[];
  /** The complete EditingSession the start would freeze (null while any issue remains). */
  session: JsonObject | null;
  effective: JsonObject | null;
  inputPath: string | null;
  outputDir: string | null;
  selectedCount: number;
}

export type Stage = "validation" | "resolution" | "submission";
export type Step = "idle" | "validating" | "resolving" | "resolved" | "submitting" | "accepted" | "rejected" | "unknown";

export interface Prepared {
  readonly session: JsonObject;
  readonly canonical: string;
  readonly effective: JsonObject;
  readonly inputPath: string;
  readonly outputDir: string;
  readonly presetName: string;
  readonly policyName: string;
  validation: ValidationV1 | null;
  /** Holds the token; never rendered, logged or stored outside page memory. */
  resolution: ResolutionV1 | null;
}

export type Tracking = "none" | "active" | "final" | "stopped" | "lost";
export type WritePhase = "idle" | "pending" | "done" | "conflict" | "rejected" | "unknown";

export interface V1JobState {
  step: Step;
  failedAt: Stage | null;
  error: ApiError | null;
  /** The server's effective settings differed from the screen's: nothing was sent further. */
  mismatch: boolean;
  prepared: Prepared | null;
  /** The intent of the last write of `failedAt` stage (kept for an explicit re-check). */
  pending: MutationIntent<JsonObject> | null;
  /** Whether the job shown was accepted by this page (vs. picked from the server list). */
  origin: "submitted" | "followed" | null;
  job: JobV1 | null;
  tracking: Tracking;
  poll: { failures: number; lastError: ApiError | null };
  queue: { phase: "idle" | "pending" | "done" | "error"; view: QueueViewV1 | null; error: ApiError | null };
  control: { phase: WritePhase; error: ApiError | null; intent: MutationIntent<JsonObject> | null };
  /** `jobId` = the job this cancel intent was sent for (frozen, not the job shown now). */
  cancel: { phase: WritePhase | "requested"; error: ApiError | null; intent: MutationIntent<JsonObject> | null; jobId: string | null };
  recent: { phase: "idle" | "pending" | "done" | "error"; items: readonly JobV1[]; error: ApiError | null };
}

export type V1Block = "busy" | "unknown_unresolved" | "tracking" | "cancel_open";

/** The shown job at one moment: its id and the target epoch (bumped on every target switch). */
export interface CancelTarget {
  readonly jobId: string;
  readonly epoch: number;
}

/** One cancel intent, frozen when it is created; the only input of its POST and of a re-check. */
export interface CancelContext {
  readonly jobId: string;
  /** If-Match of the first POST, reused unchanged by the explicit re-check. */
  readonly revision: number;
  readonly intent: MutationIntent<JsonObject>;
  readonly target: CancelTarget;
}

/** B7 keeps at most 10,000 jobs per list snapshot at 100 per page (`job_repository.LIST_MAX_ITEMS`). */
export const MAX_JOB_LIST_PAGES = 100;

export const V1_POLL_INTERVAL_MS = 2000;
export const V1_POLL_BACKOFF = { initialMs: 2000, maxMs: 30_000 } as const;
export const RECENT_LIMIT = 20;
const RESUME_MODE = "resume";

const IDLE_CANCEL: V1JobState["cancel"] = { phase: "idle", error: null, intent: null, jobId: null };

const initial: V1JobState = {
  step: "idle",
  failedAt: null,
  error: null,
  mismatch: false,
  prepared: null,
  pending: null,
  origin: null,
  job: null,
  tracking: "none",
  poll: { failures: 0, lastError: null },
  queue: { phase: "idle", view: null, error: null },
  control: { phase: "idle", error: null, intent: null },
  cancel: { phase: "idle", error: null, intent: null, jobId: null },
  recent: { phase: "idle", items: [], error: null },
};

function hex(random: RandomSource | undefined, bytes: number): string {
  const buffer = new Uint8Array(new ArrayBuffer(bytes));
  const filled = random ? random(buffer) : crypto.getRandomValues(buffer);
  return Array.from(filled, (b) => b.toString(16).padStart(2, "0")).join("");
}

function deepFreeze<T>(value: T): T {
  if (typeof value === "object" && value !== null) {
    for (const item of Object.values(value)) deepFreeze(item);
    Object.freeze(value);
  }
  return value;
}

function listError(code: "job_list_inconsistent" | "job_list_too_long"): ApiError {
  return new ApiError({ kind: "malformed", code, retryable: true });
}

const hasRowOverrides = (row: InputRow) => Object.values(row.overrides).some((v) => v !== undefined);

export interface V1JobOptions {
  scheduler?: Scheduler;
  now?: () => number;
  random?: RandomSource;
  pollIntervalMs?: number;
}

export class V1SingleJobController {
  readonly store = new Store<V1JobState>(initial);
  private readonly fileIds = new Map<string, string>();
  private poller: SingleFlightPoller | null = null;
  private generation = 0;
  /** Bumped whenever the shown job (the cancel target) changes; refreshes of the same job keep it. */
  private targetEpoch = 0;
  private cancelOp: CancelContext | null = null;
  private recentGeneration = 0;
  private recentAbort: AbortController | null = null;
  private disposed = false;
  private readonly scheduler: Scheduler;
  private readonly now: () => number;

  constructor(
    private readonly api: V1ResourcesApi,
    private readonly library: ReadonlyStore<LibraryState>,
    private readonly inputs: ReadonlyStore<CollectionState>,
    private readonly form: ReadonlyStore<FormState>,
    private readonly options: V1JobOptions = {},
  ) {
    this.scheduler = options.scheduler ?? browserScheduler;
    this.now = options.now ?? (() => Date.now());
  }

  /** A stable random id per input row for this page (no path in it). */
  fileIdFor(rowId: string): string {
    let value = this.fileIds.get(rowId);
    if (value === undefined) {
      value = `f-${hex(this.options.random, 12)}`;
      this.fileIds.set(rowId, value);
    }
    return value;
  }

  /** Pure view of what `prepare()` would freeze now. */
  evaluate(): V1Evaluation {
    const lib = this.library.get();
    const list = this.inputs.get();
    const issues: V1Issue[] = [];
    const selection = (field: "preset" | "policy") => {
      const s = lib[field];
      if (s.phase === "pending") issues.push({ field, problem: "loading" });
      else if (s.phase === "error") issues.push({ field, problem: "error" });
      else if (!s.item) issues.push({ field, problem: "none" });
      else if (s.missing) issues.push({ field, problem: "missing" });
    };
    selection("preset");
    selection("policy");
    const row = list.rows.find((r) => r.clientFileId === list.primaryId) ?? null;
    const outputDir = row ? (row.output.kind === "custom" ? row.output.path : this.form.get().draft.outputDir) : this.form.get().draft.outputDir;
    if (!row) issues.push({ field: "input", problem: "none" });
    else {
      const input = checkPath(row.inputPath, "input_file");
      if (input) issues.push({ field: "input", problem: input });
      if (hasRowOverrides(row)) issues.push({ field: "row_overrides", problem: "unsupported" });
    }
    const output = checkPath(outputDir, "output_folder");
    if (output) issues.push({ field: "output", problem: output });
    const preset = lib.preset.item;
    const workerLimits = preset ? knownWorkerLimits(preset.settings) : [];
    const selectedCount = list.rows.filter((r) => r.selected).length;
    const base = { workerLimits, inputPath: row?.inputPath ?? null, outputDir: outputDir === "" ? null : outputDir, selectedCount };
    if (issues.length > 0 || !preset || !lib.policy.item || !row) return { ...base, issues, session: null, effective: null };
    const overrides = commonOverrides(preset, lib.screen);
    const session: JsonObject = {
      schema_version: 1,
      preset_ref: { id: preset.id, revision: preset.revision },
      policy_ref: { id: lib.policy.item.id, revision: lib.policy.item.revision },
      common_overrides: overrides.map((o) => ({ path: o.path, value: clone(o.value) })),
      files: [{ client_file_id: this.fileIdFor(row.clientFileId), input_path: row.inputPath, output_dir: outputDir, overrides: [] }],
    };
    return { ...base, issues, session, effective: effectiveSettings(preset.settings, overrides, []) };
  }

  /** Why a new `prepare()` is refused now (null = allowed). */
  blockFor(): V1Block | null {
    const s = this.store.get();
    if (s.step === "validating" || s.step === "resolving" || s.step === "submitting") return "busy";
    if (s.step === "unknown") return "unknown_unresolved";
    if (this.switchBlocked() !== null) return "cancel_open";
    if (s.origin === "submitted" && s.tracking === "active") return "tracking";
    return null;
  }

  /** The resolution no longer matches what the screen would send (any edit after prepare). */
  isStale(): boolean {
    const p = this.store.get().prepared;
    if (!p) return false;
    const session = this.evaluate().session;
    return session === null || canonical(session) !== p.canonical;
  }

  isExpired(): boolean {
    const r = this.store.get().prepared?.resolution;
    return !!r && Date.parse(r.expiresAt) <= this.now();
  }

  /**
   * The only submit permission (the view's button reads this too). An open cancel (pending or
   * unknown) also blocks it: accepting a new job would switch the shown job and leave that cancel
   * intent without its explicit re-check (PR #48 additional P2). Settled cancel states do not block.
   */
  canSubmit(): boolean {
    const s = this.store.get();
    return s.step === "resolved" && !!s.prepared?.resolution && !this.isStale() && !this.isExpired() && this.switchBlocked() === null;
  }

  /** Step 1: freeze, validate (B5), resolve (B7). One click = one attempt of each, never resent. */
  async prepare(): Promise<void> {
    if (this.disposed || this.blockFor() !== null) return;
    const evaluation = this.evaluate();
    const lib = this.library.get();
    if (!evaluation.session || !evaluation.effective || !lib.preset.item || !lib.policy.item) return;
    const session = deepFreeze(clone(evaluation.session));
    const prepared: Prepared = {
      session,
      canonical: canonical(session),
      effective: deepFreeze(clone(evaluation.effective)),
      inputPath: evaluation.inputPath!,
      outputDir: evaluation.outputDir!,
      presetName: lib.preset.item.name,
      policyName: lib.policy.item.name,
      validation: null,
      resolution: null,
    };
    // One job is shown at a time: a new preparation replaces a followed/finished one on screen.
    this.stopTracking(true);
    this.store.patch({ step: "validating", failedAt: null, error: null, mismatch: false, prepared, pending: null });
    await this.runValidation(new MutationIntent<JsonObject>(session as JsonObject, this.options.random));
  }

  /** Step 2: submit the frozen session with the token, once. */
  async submit(): Promise<void> {
    if (this.disposed || !this.canSubmit()) return;
    const prepared = this.store.get().prepared!;
    const body = deepFreeze({ schema_version: 1, resolution_token: prepared.resolution!.token, editing_session: prepared.session, resume_mode: RESUME_MODE });
    await this.runSubmission(new MutationIntent<JsonObject>(body, this.options.random));
  }

  /** Explicit re-check of an unknown answer: the *same* key and frozen body, once. */
  async recheck(): Promise<void> {
    const s = this.store.get();
    if (this.disposed || s.step !== "unknown" || !s.pending || !s.failedAt) return;
    if (s.failedAt === "validation") await this.runValidation(s.pending);
    else if (s.failedAt === "resolution") await this.runResolution(s.pending);
    else await this.runSubmission(s.pending);
  }

  /** Forget the prepared request (not allowed while a request is in flight). */
  reset(): void {
    const s = this.store.get();
    if (s.step === "validating" || s.step === "resolving" || s.step === "submitting") return;
    // An unknown submission is acknowledged explicitly by the person (the server list may show it).
    this.store.patch({ step: "idle", failedAt: null, error: null, mismatch: false, prepared: null, pending: null });
  }

  private async runValidation(intent: MutationIntent<JsonObject>): Promise<void> {
    this.store.patch({ step: "validating", failedAt: null, error: null, pending: intent });
    const prepared = this.store.get().prepared!;
    try {
      const result = await this.api.validate(intent);
      if (this.disposed) return;
      const file = result.files.length === 1 ? result.files[0]! : null;
      const sessionFile = (prepared.session.files as JsonObject[])[0]!;
      const sameRefs = canonical(result.presetRef) === canonical(prepared.session.preset_ref) && canonical(result.policyRef) === canonical(prepared.session.policy_ref);
      if (!file || file.clientFileId !== sessionFile.client_file_id || !sameRefs || !file.valid || file.effectiveSettings === null || canonical(file.effectiveSettings) !== canonical(prepared.effective)) {
        // Never send what the screen did not show.
        this.store.patch({ step: "rejected", failedAt: "validation", mismatch: true, pending: null, prepared: { ...prepared, validation: result } });
        return;
      }
      this.store.patch({ prepared: { ...prepared, validation: result } });
      await this.runResolution(new MutationIntent<JsonObject>(prepared.session as JsonObject, this.options.random));
    } catch (error) {
      this.fail("validation", error, intent);
    }
  }

  private async runResolution(intent: MutationIntent<JsonObject>): Promise<void> {
    this.store.patch({ step: "resolving", failedAt: null, error: null, pending: intent });
    try {
      const resolution = await this.api.resolve(intent);
      if (this.disposed) return;
      const prepared = this.store.get().prepared!;
      const fileId = (prepared.session.files as JsonObject[])[0]!.client_file_id;
      if (resolution.files.length !== 1 || resolution.files[0]!.clientFileId !== fileId) {
        this.store.patch({ step: "rejected", failedAt: "resolution", error: toApiError(new Error("resolution shape")), pending: null });
        return;
      }
      this.store.patch({ step: "resolved", pending: null, prepared: { ...prepared, resolution } });
    } catch (error) {
      this.fail("resolution", error, intent);
    }
  }

  private async runSubmission(intent: MutationIntent<JsonObject>): Promise<void> {
    this.store.patch({ step: "submitting", failedAt: null, error: null, pending: intent });
    const prepared = this.store.get().prepared!;
    const fileId = (prepared.session.files as JsonObject[])[0]!.client_file_id;
    try {
      const { batch } = await this.api.submit(intent);
      if (this.disposed) return;
      const job = batch.jobs.length === 1 ? batch.jobs[0]! : null;
      if (!job || job.clientFileId !== fileId) {
        // An answer we cannot match to this one file is not a confirmed admission.
        this.store.patch({ step: "unknown", failedAt: "submission", error: toApiError(new Error("batch shape")), pending: intent });
        return;
      }
      this.targetEpoch += 1;
      this.store.patch({ step: "accepted", pending: null, error: null, origin: "submitted", job, tracking: "active", cancel: IDLE_CANCEL });
      this.startTracking(job.id);
    } catch (error) {
      this.fail("submission", error, intent);
    }
  }

  private fail(stage: Stage, error: unknown, intent: MutationIntent<JsonObject>): void {
    if (this.disposed) return;
    const apiError = toApiError(error);
    const outcome = classifyStartFailure(apiError);
    this.store.patch({ step: outcome, failedAt: stage, error: apiError, pending: outcome === "unknown" ? intent : null });
  }

  // ------------------------------------------------------------------ status (GET is the authority)

  /** Follow a durable job from the server list (not claimed as this page's admission). */
  follow(jobId: string): void {
    if (this.disposed || this.blockFor() === "busy" || this.switchBlocked() !== null) return;
    const job = this.store.get().recent.items.find((j) => j.id === jobId) ?? null;
    this.stopTracking(false);
    this.store.patch({ origin: "followed", job, tracking: "active", cancel: IDLE_CANCEL });
    this.startTracking(jobId);
  }

  /** One explicit GET of the job and the queue (also after the polling stopped). */
  refresh(): void {
    const job = this.store.get().job;
    if (!job) {
      void this.loadQueue();
      return;
    }
    if (this.poller && this.store.get().tracking === "active") {
      this.poller.pollNow();
      return;
    }
    this.store.patch({ tracking: "active" });
    this.startTracking(job.id);
  }

  /** Stop showing the job (the server job is untouched). */
  dismiss(): void {
    if (this.store.get().step === "submitting" || this.switchBlocked() !== null) return;
    this.stopTracking(true);
    if (this.store.get().step === "accepted") this.store.patch({ step: "idle", prepared: null });
  }

  private startTracking(jobId: string): void {
    this.poller?.stop();
    const generation = ++this.generation;
    this.store.patch({ poll: { failures: 0, lastError: null } });
    const poller = new SingleFlightPoller({
      intervalMs: this.options.pollIntervalMs ?? V1_POLL_INTERVAL_MS,
      backoff: V1_POLL_BACKOFF,
      scheduler: this.scheduler,
      task: async (signal) => {
        try {
          const job = await this.api.getJob(jobId, signal);
          if (generation !== this.generation) return;
          this.applyJob(job);
        } catch (error) {
          if (generation !== this.generation) return;
          const apiError = toApiError(error);
          if (apiError.kind === "not_found") {
            this.store.patch({ tracking: "lost" });
            this.poller?.stop();
            return;
          }
          throw apiError;
        }
        const queue = await this.api.getQueue(signal).catch((e: unknown) => {
          if (generation === this.generation) this.store.patch({ queue: { ...this.store.get().queue, phase: "error", error: toApiError(e) } });
          return null;
        });
        if (queue && generation === this.generation) this.applyQueue(queue);
        if (generation === this.generation && this.store.get().tracking !== "active") this.poller?.stop();
      },
      onChange: (state) => {
        if (generation !== this.generation) return;
        const lastError = state.lastError === null ? null : toApiError(state.lastError);
        const poll = this.store.get().poll;
        if (poll.failures !== state.consecutiveFailures || poll.lastError !== lastError) this.store.patch({ poll: { failures: state.consecutiveFailures, lastError } });
      },
    });
    this.poller = poller;
    poller.start();
  }

  private stopTracking(clearJob: boolean): void {
    this.generation += 1;
    this.targetEpoch += 1;
    if (clearJob) this.cancelOp = null;
    this.poller?.stop();
    this.poller = null;
    if (clearJob) this.store.patch({ job: null, origin: null, tracking: "none", cancel: IDLE_CANCEL });
  }

  /** Never go back to an older revision (late answers are dropped). */
  private applyJob(job: JobV1): void {
    const current = this.store.get().job;
    // Never another job's answer over the shown one, never an older revision of the same job.
    if (current && (current.id !== job.id || job.revision < current.revision)) return;
    const tracking: Tracking = FINAL_STATUSES_V1.has(job.status) ? "final" : job.status === "interrupted" ? "stopped" : "active";
    const cancel = this.store.get().cancel;
    const nextCancel = cancel.phase === "requested" && FINAL_STATUSES_V1.has(job.status) ? { ...cancel, phase: "done" as const, error: null, intent: null } : cancel;
    this.store.patch({ job, tracking, cancel: nextCancel });
  }

  private applyQueue(view: QueueViewV1): void {
    const current = this.store.get().queue.view;
    if (current && view.revision < current.revision) return;
    this.store.patch({ queue: { phase: "done", view, error: null } });
  }

  // ------------------------------------------------------------------ queue

  async loadQueue(): Promise<void> {
    if (this.disposed) return;
    this.store.patch({ queue: { ...this.store.get().queue, phase: "pending", error: null } });
    try {
      this.applyQueue(await this.api.getQueue());
      if (this.store.get().queue.phase !== "done") this.store.patch({ queue: { ...this.store.get().queue, phase: "done" } });
    } catch (error) {
      this.store.patch({ queue: { ...this.store.get().queue, phase: "error", error: toApiError(error) } });
    }
  }

  /** Explicit "run the queue": PATCH with the last GET's revision as If-Match and a new key. */
  async resumeQueue(): Promise<void> {
    const s = this.store.get();
    const view = s.queue.view;
    if (this.disposed || !view || view.dispatch !== "paused" || s.control.phase === "pending") return;
    await this.runControl(new MutationIntent<JsonObject>({ schema_version: 1, dispatch: "running" }, this.options.random), view.revision);
  }

  /** Re-check an unknown resume with the same key, body and If-Match. */
  async recheckResume(): Promise<void> {
    const c = this.store.get().control;
    const revision = this.controlRevision;
    if (c.phase !== "unknown" || !c.intent || revision === null) return;
    await this.runControl(c.intent, revision);
  }

  private controlRevision: number | null = null;

  private async runControl(intent: MutationIntent<JsonObject>, revision: number): Promise<void> {
    this.controlRevision = revision;
    this.store.patch({ control: { phase: "pending", error: null, intent } });
    try {
      const view = await this.api.setDispatch(intent, revision);
      this.applyQueue(view);
      this.store.patch({ control: { phase: "done", error: null, intent: null } });
    } catch (error) {
      const apiError = toApiError(error);
      if (apiError.kind === "precondition") {
        // Someone changed the queue: show the latest state and let the person decide again.
        this.store.patch({ control: { phase: "conflict", error: apiError, intent: null } });
        await this.loadQueue();
        return;
      }
      const outcome = classifyStartFailure(apiError);
      this.store.patch({ control: { phase: outcome, error: apiError, intent: outcome === "unknown" ? intent : null } });
    }
  }

  // ------------------------------------------------------------------ cancel (cooperative only)

  /** Why the shown job may not be switched now: an open cancel intent belongs to it. */
  switchBlocked(): "cancel_pending" | "cancel_unknown" | null {
    const phase = this.store.get().cancel.phase;
    if (phase === "pending") return "cancel_pending";
    if (phase === "unknown" && this.cancelOp !== null) return "cancel_unknown";
    return null;
  }

  /** A new cancel intent for the shown job (new key); refused while another one is open. */
  async cancel(): Promise<void> {
    const s = this.store.get();
    if (this.disposed || !s.job || s.tracking === "final" || this.switchBlocked() !== null) return;
    const ctx: CancelContext = Object.freeze({
      jobId: s.job.id,
      revision: s.job.revision,
      intent: new MutationIntent<JsonObject>(deepFreeze({ schema_version: 1, mode: "cooperative" }), this.options.random),
      target: Object.freeze({ jobId: s.job.id, epoch: this.targetEpoch }),
    });
    this.cancelOp = ctx;
    await this.runCancel(ctx);
  }

  /** Explicit re-check of an unknown cancel: the frozen job id, If-Match, key and body, once. */
  async recheckCancel(): Promise<void> {
    const ctx = this.cancelOp;
    if (this.disposed || ctx === null || this.store.get().cancel.phase !== "unknown") return;
    await this.runCancel(ctx);
  }

  /** The person gives up an unknown cancel (it may or may not have been applied); it can no longer be re-checked. */
  discardCancel(): void {
    if (this.store.get().cancel.phase !== "unknown") return;
    this.cancelOp = null;
    this.store.patch({ cancel: IDLE_CANCEL });
  }

  /** Whether an answer for `ctx` may be written now (pure; exported for the guard's unit test). */
  answerApplies(ctx: CancelContext, target: CancelTarget | null): boolean {
    return !this.disposed && this.cancelOp === ctx && target !== null && target.jobId === ctx.jobId && target.epoch === ctx.target.epoch;
  }

  /** The open cancel context (tests only; never rendered: it holds the intent). */
  cancelContextForTest(): CancelContext | null {
    return this.cancelOp;
  }

  private currentTarget(): CancelTarget | null {
    const job = this.store.get().job;
    return job ? { jobId: job.id, epoch: this.targetEpoch } : null;
  }

  private async runCancel(ctx: CancelContext): Promise<void> {
    // Set synchronously before the first await: a second click or re-check sees "pending" and stops.
    this.store.patch({ cancel: { phase: "pending", error: null, intent: ctx.intent, jobId: ctx.jobId } });
    try {
      const { job } = await this.api.cancel(ctx.jobId, ctx.revision, ctx.intent);
      if (!this.answerApplies(ctx, this.currentTarget())) return;
      this.cancelOp = null;
      // 202 = requested (queued → cancelled at once; running → cancelling); 200 = already final.
      this.store.patch({ cancel: { phase: FINAL_STATUSES_V1.has(job.status) ? "done" : "requested", error: null, intent: null, jobId: ctx.jobId } });
      this.applyJob(job);
    } catch (error) {
      if (!this.answerApplies(ctx, this.currentTarget())) return;
      const apiError = toApiError(error);
      if (apiError.kind === "precondition") {
        this.cancelOp = null;
        this.store.patch({ cancel: { phase: "conflict", error: apiError, intent: null, jobId: ctx.jobId } });
        this.refresh();
        return;
      }
      const outcome = classifyStartFailure(apiError);
      if (outcome !== "unknown") this.cancelOp = null;
      this.store.patch({ cancel: { phase: outcome, error: apiError, intent: outcome === "unknown" ? ctx.intent : null, jobId: ctx.jobId } });
    }
  }

  // ------------------------------------------------------------------ server list (re-display after restart)

  /**
   * Every `GET /api/v1/jobs` page in turn (B7: one snapshot, oldest first, 100 per page), keeping
   * only the newest `RECENT_LIMIT`. Bounded by `MAX_JOB_LIST_PAGES`; a failing page, a repeated
   * cursor, a changed snapshot revision or a duplicate id is an error, never a finished partial
   * list. No retry. A newer load or `dispose()` makes an older one write nothing.
   */
  async loadRecent(): Promise<void> {
    if (this.disposed) return;
    const generation = ++this.recentGeneration;
    this.recentAbort?.abort();
    const abort = new AbortController();
    this.recentAbort = abort;
    const current = () => generation === this.recentGeneration && !this.disposed;
    this.store.patch({ recent: { ...this.store.get().recent, phase: "pending", error: null } });
    try {
      const newest: JobV1[] = [];
      const ids = new Set<string>();
      const cursors = new Set<string>();
      let cursor: string | null = null;
      let revision: number | null = null;
      for (let page = 0; ; page += 1) {
        if (page >= MAX_JOB_LIST_PAGES) throw listError("job_list_too_long");
        const list = await this.api.listJobs(cursor, abort.signal);
        if (!current()) return;
        if (revision === null) revision = list.revision;
        else if (list.revision !== revision) throw listError("job_list_inconsistent");
        for (const item of list.items) {
          if (ids.has(item.id)) throw listError("job_list_inconsistent");
          ids.add(item.id);
          newest.push(item);
          if (newest.length > RECENT_LIMIT) newest.shift();
        }
        if (list.nextCursor === null) break;
        if (cursors.has(list.nextCursor)) throw listError("job_list_inconsistent");
        cursors.add(list.nextCursor);
        cursor = list.nextCursor;
      }
      this.store.patch({ recent: { phase: "done", items: newest.reverse(), error: null } });
    } catch (error) {
      if (!current()) return;
      // Nothing partial is shown as "the newest jobs".
      this.store.patch({ recent: { phase: "error", items: [], error: toApiError(error) } });
    } finally {
      if (this.recentAbort === abort) this.recentAbort = null;
    }
  }

  dispose(): void {
    this.disposed = true;
    this.recentAbort?.abort();
    this.generation += 1;
    this.poller?.stop();
    this.poller = null;
  }
}
