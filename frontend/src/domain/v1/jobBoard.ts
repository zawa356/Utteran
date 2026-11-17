/**
 * Bundle C2b: every durable job of the v1 store as *logical jobs* (Design Freeze §7), split into
 * the queue (not finished) and the history (finished), with hide/show and the read-only artifact view.
 *
 * - `GET /api/v1/jobs` (every page, bounded) and `GET /api/v1/queue` are the authority. Nothing is
 *   inferred from an earlier answer; a failing or inconsistent page is an error, never a partial list.
 * - Writes are explicit, one at a time: cancel, retry (B7: a new internal job linked by `retry_of`)
 *   and hide/show. Each freezes its target job id and revision. 4xx = refused, 412 = conflict (the
 *   list is re-read), anything else = unknown: resent only by an explicit `recheck()` with the same
 *   key and body (cancel/retry) or the same idempotent PUT (hide/show).
 * - Hide/show applies to every internal job of the logical job, one PUT each, stopping at the first
 *   problem. It never deletes anything; the files on disk are not touched.
 * - The artifact view carries format, size and integrity only (no name, no path).
 */

import type { ApiError } from "../../api/errors";
import { MutationIntent, type RandomSource } from "../../api/idempotency";
import type { JobArtifactsV1, JobV1, JobVisibilityFilter, JsonObject, QueueViewV1, V1ResourcesApi } from "../../api/v1Resources";
import { ApiError as ApiErrorClass } from "../../api/errors";
import { browserScheduler, type Scheduler } from "../../transport/scheduler";
import { classifyStartFailure } from "../admission";
import { toApiError } from "../operation";
import { Store } from "../store";
import { groupLogicalJobs, isFinished, type LogicalJob } from "./logicalJobs";

export { isFinished };

export const BOARD_MAX_PAGES = 100;
export const BOARD_POLL_MS = 2000;
const RETRYABLE = new Set<string>(["failed", "cancelled", "interrupted"]);

export type BoardActionKind = "cancel" | "retry" | "hide" | "show";
export type BoardActionPhase = "pending" | "done" | "rejected" | "conflict" | "unknown";

export interface BoardAction {
  readonly kind: BoardActionKind;
  /** Row the action was started from (stable key of the logical job). */
  readonly rootId: string;
  /** Internal jobs still to be written, first = the one in flight / unknown. */
  readonly remaining: readonly string[];
  readonly phase: BoardActionPhase;
  readonly error: ApiError | null;
}

export interface ArtifactEntry {
  readonly phase: "pending" | "done" | "error";
  readonly view: JobArtifactsV1 | null;
  readonly error: ApiError | null;
}

export interface BoardState {
  phase: "idle" | "pending" | "done" | "error";
  error: ApiError | null;
  /**
   * Logical jobs with at least one unfinished internal job, in the order of `GET /queue`: the active
   * item first, then waiting items by their B7 position. Rows whose place the queue did not report
   * come last and are shown without a place (`placeOf` = null), never with a guessed one.
   */
  queue: readonly LogicalJob[];
  /** Finished logical jobs, newest first. */
  history: readonly LogicalJob[];
  /** Hidden finished logical jobs, newest first (loaded only while `showHidden`). */
  hidden: readonly LogicalJob[];
  showHidden: boolean;
  /** False when the store has no history visibility (layout before v7): hide/show is not offered. */
  visibilitySupported: boolean;
  queueView: QueueViewV1 | null;
  /** B7 queue facts per internal job id, from the same refresh as `queue`. */
  places: Readonly<Record<string, QueuePlace>>;
  action: BoardAction | null;
  artifacts: Readonly<Record<string, ArtifactEntry>>;
}

/** Where B7 says an internal job is in the queue. `position` is null for the active item. */
export interface QueuePlace {
  readonly active: boolean;
  readonly position: number | null;
  readonly held: boolean;
  readonly blockers: readonly string[];
}

const initial: BoardState = { phase: "idle", error: null, queue: [], history: [], hidden: [], showHidden: false, visibilitySupported: true, queueView: null, places: {}, action: null, artifacts: {} };

/** Queue facts by internal job id (only what `GET /queue` reported; nothing is inferred). */
export function queuePlaces(view: QueueViewV1): Record<string, QueuePlace> {
  const places: Record<string, QueuePlace> = {};
  for (const item of view.items) {
    if (item.resourceKind !== "job") continue;
    places[item.resourceId] = { active: item.id === view.activeItemId, position: item.position, held: item.held, blockers: item.blockers };
  }
  return places;
}

const rank = (place: QueuePlace | undefined): number => (place === undefined ? Number.POSITIVE_INFINITY : place.active ? 0 : (place.position ?? Number.POSITIVE_INFINITY));

/**
 * The internal job an unfinished row shows and acts on: the unfinished job that B7 runs or will run
 * first (active, then lowest position); without queue facts, the first unfinished one as listed.
 * A finished row shows its newest job.
 */
export function currentJob(group: LogicalJob, places: Readonly<Record<string, QueuePlace>>): JobV1 {
  if (group.open.length === 0) return group.latest;
  return [...group.open].sort((a, b) => rank(places[a.id]) - rank(places[b.id]))[0]!;
}

function listError(code: "job_list_inconsistent" | "job_list_too_long"): ApiError {
  return new ApiErrorClass({ kind: "malformed", code, retryable: true });
}

export interface BoardOptions {
  scheduler?: Scheduler;
  random?: RandomSource;
  pollMs?: number;
  /**
   * Called once after a hide/show ended `done` and the board was re-read from the server. Other
   * cached job lists of the page re-read themselves here. Never called while the write is pending,
   * refused, in conflict or of unknown outcome.
   */
  onVisibilitySettled?: () => void;
}

interface Frozen {
  jobId: string;
  revision: number;
  intent: MutationIntent<JsonObject> | null;
}

export class V1JobBoardController {
  readonly store = new Store<BoardState>(initial);
  private generation = 0;
  private abort: AbortController | null = null;
  private timer: unknown = null;
  private watching = false;
  private disposed = false;
  private frozen: Frozen | null = null;
  private readonly scheduler: Scheduler;

  constructor(
    private readonly api: V1ResourcesApi,
    private readonly options: BoardOptions = {},
  ) {
    this.scheduler = options.scheduler ?? browserScheduler;
  }

  // ------------------------------------------------------------------ reads

  private async allJobs(visibility: JobVisibilityFilter | undefined, signal: AbortSignal): Promise<JobV1[]> {
    const items: JobV1[] = [];
    const ids = new Set<string>();
    const cursors = new Set<string>();
    let cursor: string | null = null;
    let revision: number | null = null;
    for (let page = 0; ; page += 1) {
      if (page >= BOARD_MAX_PAGES) throw listError("job_list_too_long");
      const list = await this.api.listJobs(cursor, signal, visibility);
      if (revision === null) revision = list.revision;
      else if (list.revision !== revision) throw listError("job_list_inconsistent");
      for (const item of list.items) {
        if (ids.has(item.id)) throw listError("job_list_inconsistent");
        ids.add(item.id);
        items.push(item);
      }
      if (list.nextCursor === null) return items;
      if (cursors.has(list.nextCursor)) throw listError("job_list_inconsistent");
      cursors.add(list.nextCursor);
      cursor = list.nextCursor;
    }
  }

  /** One full read of the lists and the queue. A newer call or `dispose()` makes an older one write nothing. */
  async refresh(): Promise<void> {
    if (this.disposed) return;
    const generation = ++this.generation;
    this.abort?.abort();
    const abort = new AbortController();
    this.abort = abort;
    const current = () => generation === this.generation && !this.disposed;
    if (this.store.get().phase !== "done") this.store.patch({ phase: "pending", error: null });
    try {
      const wantHidden = this.store.get().showHidden;
      const visible = await this.allJobs(undefined, abort.signal);
      if (!current()) return;
      let hiddenJobs: JobV1[] = [];
      let supported = this.store.get().visibilitySupported;
      if (wantHidden && supported) {
        try {
          hiddenJobs = await this.allJobs("hidden", abort.signal);
        } catch (error) {
          // A store without layout v7 refuses the filter (422): hide/show is simply not available.
          const apiError = toApiError(error);
          if (apiError.status !== 422) throw apiError;
          supported = false;
        }
        if (!current()) return;
      }
      const queueView = await this.api.getQueue(abort.signal);
      if (!current()) return;
      const groups = groupLogicalJobs(visible);
      const places = queuePlaces(queueView);
      // A row is finished only when *every* internal job is: a still-waiting branch keeps it in the
      // queue (and keeps the polling alive) even if the newest job of the chain already ended.
      const unfinished = groups.filter((g) => g.open.length > 0);
      const order = new Map(unfinished.map((g, index) => [g.rootId, index]));
      unfinished.sort((a, b) => rank(places[currentJob(a, places).id]) - rank(places[currentJob(b, places).id]) || order.get(a.rootId)! - order.get(b.rootId)!);
      this.store.patch({
        phase: "done",
        error: null,
        queue: unfinished,
        places,
        history: groups.filter((g) => g.open.length === 0).reverse(),
        hidden: groupLogicalJobs(hiddenJobs).reverse(),
        visibilitySupported: supported,
        queueView,
      });
    } catch (error) {
      if (!current()) return;
      // Nothing partial is shown as "the jobs".
      this.store.patch({ phase: "error", error: toApiError(error), queue: [], history: [], hidden: [], places: {} });
    } finally {
      if (this.abort === abort) this.abort = null;
      this.schedule();
    }
  }

  /** Start/stop the bounded polling of the board (only while something is not finished). */
  watch(on: boolean): void {
    this.watching = on && !this.disposed;
    if (!this.watching) {
      if (this.timer !== null) this.scheduler.clearTimeout(this.timer);
      this.timer = null;
      return;
    }
    void this.refresh();
  }

  private schedule(): void {
    if (this.timer !== null) this.scheduler.clearTimeout(this.timer);
    this.timer = null;
    const s = this.store.get();
    // Poll only while a job can still change; an error stops it (explicit refresh).
    if (!this.watching || this.disposed || s.phase !== "done" || s.queue.length === 0) return;
    this.timer = this.scheduler.setTimeout(() => {
      this.timer = null;
      void this.refresh();
    }, this.options.pollMs ?? BOARD_POLL_MS);
  }

  setShowHidden(on: boolean): void {
    if (this.store.get().showHidden === on) return;
    this.store.patch({ showHidden: on, hidden: on ? this.store.get().hidden : [] });
    void this.refresh();
  }

  async loadArtifacts(jobId: string): Promise<void> {
    if (this.disposed) return;
    const put = (entry: ArtifactEntry) => this.store.patch({ artifacts: { ...this.store.get().artifacts, [jobId]: entry } });
    put({ phase: "pending", view: this.store.get().artifacts[jobId]?.view ?? null, error: null });
    try {
      const view = await this.api.getArtifacts(jobId);
      if (!this.disposed) put({ phase: "done", view, error: null });
    } catch (error) {
      if (!this.disposed) put({ phase: "error", view: null, error: toApiError(error) });
    }
  }

  // ------------------------------------------------------------------ writes (one at a time)

  private find(rootId: string): LogicalJob | null {
    const s = this.store.get();
    return [...s.queue, ...s.history, ...s.hidden].find((g) => g.rootId === rootId) ?? null;
  }

  /** Why no new write may start (an open one must be re-checked or dismissed first). */
  actionOpen(): boolean {
    const phase = this.store.get().action?.phase;
    return phase === "pending" || phase === "unknown";
  }

  /** The job a row shows and acts on now (see `currentJob`). */
  current(group: LogicalJob): JobV1 {
    return currentJob(group, this.store.get().places);
  }

  /** B7's place for an internal job, or null when the queue did not report one. */
  placeOf(jobId: string): QueuePlace | null {
    return this.store.get().places[jobId] ?? null;
  }

  /** `jobId` = one specific unfinished job of the row (a branch); default = the row's current job. */
  canCancel(group: LogicalJob, jobId?: string): boolean {
    const target = jobId === undefined ? (group.open.length > 0 ? this.current(group) : null) : (group.open.find((job) => job.id === jobId) ?? null);
    return !this.actionOpen() && target !== null && target.status !== "cancelling";
  }

  canRetry(group: LogicalJob): boolean {
    // Only a row with nothing unfinished: a retry while a branch still waits would add another branch.
    return !this.actionOpen() && group.open.length === 0 && RETRYABLE.has(group.latest.status);
  }

  canHide(group: LogicalJob): boolean {
    // Every internal job of the row must be finished: a waiting or running job stays visible.
    return !this.actionOpen() && this.store.get().visibilitySupported && group.attempts.every(isFinished);
  }

  /**
   * C2c: a row may offer deletion only when every internal job of it is finished and no board write is
   * open. Which one internal job is deleted is chosen explicitly in the dialog (never the whole chain).
   */
  canDelete(group: LogicalJob): boolean {
    return !this.actionOpen() && group.attempts.every(isFinished);
  }

  async cancel(rootId: string, jobId?: string): Promise<void> {
    const group = this.find(rootId);
    if (this.disposed || !group || !this.canCancel(group, jobId)) return;
    // Never "the newest job": exactly the unfinished job the row (or the branch button) names.
    const job = jobId === undefined ? this.current(group) : group.open.find((item) => item.id === jobId)!;
    this.frozen = { jobId: job.id, revision: job.revision, intent: new MutationIntent<JsonObject>({ schema_version: 1, mode: "cooperative" }, this.options.random) };
    await this.run({ kind: "cancel", rootId, remaining: [job.id], phase: "pending", error: null });
  }

  async retry(rootId: string): Promise<void> {
    const group = this.find(rootId);
    if (this.disposed || !group || !this.canRetry(group)) return;
    const job = group.latest;
    this.frozen = { jobId: job.id, revision: job.revision, intent: new MutationIntent<JsonObject>({ schema_version: 1 }, this.options.random) };
    await this.run({ kind: "retry", rootId, remaining: [job.id], phase: "pending", error: null });
  }

  async setHidden(rootId: string, hidden: boolean): Promise<void> {
    const group = this.find(rootId);
    if (this.disposed || !group || this.actionOpen() || !this.store.get().visibilitySupported) return;
    if (hidden && !this.canHide(group)) return;
    this.frozen = null;
    await this.run({ kind: hidden ? "hide" : "show", rootId, remaining: [...group.jobIds], phase: "pending", error: null });
  }

  /** Explicit re-check of an unknown outcome: the same target and the same request, nothing else. */
  async recheck(): Promise<void> {
    const action = this.store.get().action;
    if (this.disposed || !action || action.phase !== "unknown") return;
    await this.run({ ...action, phase: "pending", error: null });
  }

  /** Forget a settled or unknown action (the server state is whatever the next refresh shows). */
  dismissAction(): void {
    if (this.store.get().action?.phase === "pending") return;
    this.frozen = null;
    this.store.patch({ action: null });
  }

  private async run(action: BoardAction): Promise<void> {
    this.store.patch({ action });
    let remaining = [...action.remaining];
    // The outcome of *this* write. The shown `action` can be dismissed (or replaced) while the
    // lists are re-read below, so it is not what decides the notification.
    let visibilityDone = false;
    try {
      if (action.kind === "cancel" || action.kind === "retry") {
        const frozen = this.frozen!;
        if (action.kind === "cancel") await this.api.cancel(frozen.jobId, frozen.revision, frozen.intent!);
        else await this.api.retry(frozen.jobId, frozen.revision, frozen.intent!);
        remaining = [];
      } else {
        const hidden = action.kind === "hide";
        while (remaining.length > 0) {
          const jobId = remaining[0]!;
          // Read the visibility revision right before the write (its own CAS, not the job revision).
          const current = await this.api.getVisibility(jobId);
          if (current.hidden !== hidden) await this.api.setVisibility(jobId, current.revision, hidden);
          remaining = remaining.slice(1);
          if (this.disposed) return;
          this.store.patch({ action: { ...action, remaining } });
        }
      }
      if (this.disposed) return;
      this.frozen = null;
      this.store.patch({ action: { ...action, remaining: [], phase: "done", error: null } });
      visibilityDone = action.kind === "hide" || action.kind === "show";
    } catch (error) {
      if (this.disposed) return;
      const apiError = toApiError(error);
      const phase: BoardActionPhase = apiError.kind === "precondition" ? "conflict" : classifyStartFailure(apiError);
      if (phase !== "unknown") this.frozen = null;
      this.store.patch({ action: { ...action, remaining, phase, error: apiError } });
    }
    // Whatever happened, the lists come from the server again.
    await this.refresh();
    if (visibilityDone && !this.disposed) this.options.onVisibilitySettled?.();
  }

  dispose(): void {
    this.disposed = true;
    this.watching = false;
    this.generation += 1;
    this.abort?.abort();
    if (this.timer !== null) this.scheduler.clearTimeout(this.timer);
    this.timer = null;
  }
}
