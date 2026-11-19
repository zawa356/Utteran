/**
 * Bundle C2c: deleting one *internal* job's history record (③) or its history and verified outputs
 * (④), as two separate operations, each "plan → explicit approval → execute".
 *
 * - The target is always one internal job id chosen explicitly (a logical row with several attempts
 *   lets the person pick which one). Siblings, retries and reruns are never deleted along with it.
 * - `plan()` is read-only on the server. The plan, the job revision it was made for and the mode are
 *   frozen; `confirm()` sends exactly that, once. A plan that no longer matches the server is refused
 *   there (409 stale/expired) and is never replaced by a new plan here.
 * - Outcomes: `done` (completed), `incomplete` (files partly removed, history kept), `rejected` (4xx,
 *   nothing deleted by this request), `conflict` (412), `unknown` (network/timeout/5xx/unreadable).
 *   `unknown` is resolved only by an explicit `recheck()` with the same key and body; never by itself.
 * - "Complete erasure" is never claimed: the plan and the result carry what is retained.
 */

import type { ApiError } from "../../api/errors";
import { MutationIntent, type RandomSource } from "../../api/idempotency";
import type { DeletionModeV1, JobDeletionPlanV1, JobDeletionV1, JsonObject, V1ResourcesApi } from "../../api/v1Resources";
import { classifyStartFailure } from "../admission";
import { toApiError } from "../operation";
import { Store } from "../store";
import { QueueMutationGate } from "./queueGate";

export type DeletionPhase = "idle" | "planning" | "planned" | "plan_error" | "executing" | "done" | "incomplete" | "rejected" | "conflict" | "unknown";

export interface DeletionTarget {
  /** Row (logical job) the dialog was opened from. */
  readonly rootId: string;
  /** The one internal job that would be deleted. */
  readonly jobId: string;
  /** The job revision the plan is made for (sent as If-Match). */
  readonly revision: number;
  readonly mode: DeletionModeV1;
}

export interface DeletionState {
  phase: DeletionPhase;
  target: DeletionTarget | null;
  plan: JobDeletionPlanV1 | null;
  result: JobDeletionV1 | null;
  error: ApiError | null;
}

const initial: DeletionState = { phase: "idle", target: null, plan: null, result: null, error: null };

export interface DeletionOptions {
  gate?: QueueMutationGate;
  random?: RandomSource;
  /** Called after every settled execution (done / incomplete / rejected / conflict), not for unknown. */
  onSettled?: () => void;
}

export class V1DeletionController {
  readonly store = new Store<DeletionState>(initial);
  private intent: MutationIntent<JsonObject> | null = null;
  private generation = 0;
  private disposed = false;
  readonly gate: QueueMutationGate;
  private readonly owner = Symbol("deletion");

  constructor(
    private readonly api: V1ResourcesApi,
    private readonly options: DeletionOptions = {},
  ) {
    this.gate = options.gate ?? new QueueMutationGate();
  }

  /** A dialog is open (planning, planned, executing or an outcome not yet dismissed). */
  isOpen(): boolean {
    return this.store.get().phase !== "idle";
  }

  /** An execution was sent and its outcome is not settled: nothing else may start. */
  busy(): boolean {
    const phase = this.store.get().phase;
    return phase === "executing" || phase === "unknown";
  }

  /** Read-only preflight for one internal job. Replaces an earlier plan that was not executed. */
  async plan(target: DeletionTarget): Promise<void> {
    if (this.disposed || this.busy()) return;
    const generation = ++this.generation;
    this.intent = null;
    this.store.set({ phase: "planning", target, plan: null, result: null, error: null });
    try {
      const plan = await this.api.planDeletion(target.jobId, target.mode);
      if (this.disposed || generation !== this.generation) return;
      this.store.patch({ phase: "planned", plan });
    } catch (error) {
      if (this.disposed || generation !== this.generation) return;
      this.store.patch({ phase: "plan_error", error: toApiError(error) });
    }
  }

  canConfirm(): boolean {
    const s = this.store.get();
    return !this.gate.busy() && s.phase === "planned" && s.plan !== null && s.plan.allowed && s.plan.planToken !== null;
  }

  /** The explicit approval: sends the frozen plan once. A second click while it is in flight is ignored. */
  async confirm(): Promise<void> {
    if (this.disposed || !this.canConfirm()) return;
    const { target, plan } = this.store.get();
    this.intent = new MutationIntent<JsonObject>({ schema_version: 1, mode: target!.mode, plan_token: plan!.planToken }, this.options.random);
    await this.send();
  }

  /** Re-check an unknown outcome with exactly the same request (the server replays a finished one). */
  async recheck(): Promise<void> {
    if (this.disposed || this.store.get().phase !== "unknown" || this.intent === null) return;
    await this.send();
  }

  private async send(): Promise<void> {
    if (!this.gate.owns(this.owner) && !this.gate.acquire(this.owner)) return;
    const { target } = this.store.get();
    const intent = this.intent!;
    const generation = ++this.generation;
    this.store.patch({ phase: "executing", error: null });
    try {
      const result = await this.api.deleteJob(target!.jobId, target!.revision, intent);
      if (this.disposed || generation !== this.generation) return;
      this.intent = null;
      this.store.patch({ phase: result.outcome === "completed" ? "done" : "incomplete", result, error: null });
    } catch (error) {
      if (this.disposed || generation !== this.generation) return;
      const apiError = toApiError(error);
      const phase: DeletionPhase = apiError.kind === "precondition" ? "conflict" : classifyStartFailure(apiError);
      if (phase !== "unknown") this.intent = null;
      this.store.patch({ phase, error: apiError });
      if (phase === "unknown") return; // the server state is not known: do not re-read as if settled
    }
    this.gate.release(this.owner);
    this.options.onSettled?.();
  }

  /** Close the dialog. Refused while a request is in flight; an unknown outcome must be dismissed explicitly. */
  close(): void {
    if (this.store.get().phase === "executing") return;
    this.generation += 1;
    this.intent = null;
    this.gate.release(this.owner);
    this.store.set(initial);
  }

  dispose(): void {
    this.disposed = true;
    this.generation += 1;
  }
}
