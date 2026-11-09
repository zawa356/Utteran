import { type ApiError, isApiError } from "../api/errors";
import {
  isTerminal,
  type JobEvent,
  jobEventsPath,
  type JobView,
  type LegacyJobsApi,
  parseJobEvent,
  SSE_EVENTS,
  type TranscriptionRequest,
  transcriptionBody,
} from "../api/legacyJobs";
import { SingleFlightPoller } from "../transport/poller";
import { browserScheduler, type Scheduler } from "../transport/scheduler";
import { toApiError } from "./operation";
import { samePath } from "./paths";
import { Store } from "./store";

/**
 * One legacy transcription job at a time (UI-3 S3-A).
 *
 * - `POST /api/jobs` is not idempotent (no server idempotency key). It is sent once per click
 *   and never resent; a lost answer becomes `unknown` ("may have started").
 * - `GET /api/jobs/{id}` is the only source of status. SSE (`/events`) only makes progress appear
 *   sooner and asks for an early GET; its `done` never decides the outcome.
 * - `POST /api/jobs/{id}/cancel` → 202 means *requested*. "Cancelled" is shown only after GET.
 * - Every async answer carries the tracking generation; an answer for an earlier job (or after
 *   `dispose`) is dropped, so it can never stop or overwrite the current one (UI-1 D-04).
 * - The legacy job and queue are in GUI-process memory only: no resume across an app restart.
 */

export type SubmitPhase = "idle" | "pending" | "accepted" | "rejected" | "unknown";
export type Tracking = "none" | "active" | "terminal" | "lost";
export type SseState = "off" | "connecting" | "open" | "closed" | "unsupported";
export type CancelPhase = "idle" | "pending" | "requested" | "failed" | "unknown" | "too_late";

/** A frozen, in-memory copy of exactly what was sent. Not a durable (B7) job snapshot. */
export interface SubmittedSnapshot {
  readonly body: Readonly<Record<string, unknown>>;
  readonly request: Readonly<TranscriptionRequest>;
  /** Client clock (ms). */
  readonly submittedAt: number;
}

export interface JobState {
  submit: { phase: SubmitPhase; error: ApiError | null; startedAt: number | null };
  snapshot: SubmittedSnapshot | null;
  job: JobView | null;
  tracking: Tracking;
  poll: { failures: number; lastError: ApiError | null; lastOkAt: number | null };
  sse: SseState;
  cancel: { phase: CancelPhase; error: ApiError | null };
  /** `GET /api/queue` reference after an unknown submit: active transcription items (any origin). */
  queueCheck: { phase: "idle" | "pending" | "done" | "error"; active: number | null; error: ApiError | null };
}

export type SubmitBlock = "busy" | "unknown_unresolved" | "same_input";

export interface EventSourceLike {
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
}
export type EventSourceFactory = (url: string) => EventSourceLike;

export interface JobControllerOptions {
  scheduler?: Scheduler;
  /** Omitted/undefined = no SSE (polling only). */
  eventSource?: EventSourceFactory;
  now?: () => number;
  pollIntervalMs?: number;
  /** SSE is opened at most this many times per job; afterwards polling alone tracks it. */
  maxSseConnections?: number;
}

export const POLL_INTERVAL_MS = 2000;
export const POLL_BACKOFF = { initialMs: 2000, maxMs: 30_000 } as const;
export const MAX_SSE_CONNECTIONS = 3;

const initial: JobState = {
  submit: { phase: "idle", error: null, startedAt: null },
  snapshot: null,
  job: null,
  tracking: "none",
  poll: { failures: 0, lastError: null, lastOkAt: null },
  sse: "off",
  cancel: { phase: "idle", error: null },
  queueCheck: { phase: "idle", active: null, error: null },
};

export class JobController {
  readonly store = new Store<JobState>(initial);
  private generation = 0;
  private poller: SingleFlightPoller | null = null;
  private source: EventSourceLike | null = null;
  private sseConnections = 0;
  /** Input of the last submission that was accepted or may have been (unknown). */
  private lastInput: string | null = null;
  private readonly scheduler: Scheduler;
  private readonly now: () => number;
  private readonly pollIntervalMs: number;
  private readonly maxSse: number;

  constructor(
    private readonly api: LegacyJobsApi,
    private readonly options: JobControllerOptions = {},
  ) {
    this.scheduler = options.scheduler ?? browserScheduler;
    this.now = options.now ?? (() => Date.now());
    this.pollIntervalMs = options.pollIntervalMs ?? POLL_INTERVAL_MS;
    this.maxSse = options.maxSseConnections ?? MAX_SSE_CONNECTIONS;
  }

  /** Why a new submission is not offered (null = it may be sent). */
  blockFor(request: TranscriptionRequest | null): SubmitBlock | null {
    const s = this.store.get();
    if (s.submit.phase === "pending" || s.tracking === "active") return "busy";
    if (s.submit.phase === "unknown") return "unknown_unresolved";
    // S3-A does not offer re-running the same input: the core derives its job id from the input
    // fingerprint, so a second run may share history/stage cache (S3-B gate). Use the legacy GUI.
    if (request && this.lastInput !== null && samePath(request.inputPath, this.lastInput)) return "same_input";
    return null;
  }

  /** Sends one POST. Returns false (and sends nothing) when blocked or already sending. */
  async submit(request: TranscriptionRequest): Promise<boolean> {
    if (this.blockFor(request) !== null) return false;
    this.stopMonitoring();
    const generation = ++this.generation;
    const frozen = Object.freeze({ ...request, formats: Object.freeze([...request.formats]) as unknown as TranscriptionRequest["formats"] });
    const snapshot: SubmittedSnapshot = Object.freeze({ body: Object.freeze(transcriptionBody(frozen)), request: frozen, submittedAt: this.now() });
    this.store.set({ ...initial, snapshot, submit: { phase: "pending", error: null, startedAt: this.now() } });
    try {
      const job = await this.api.startJob(frozen);
      if (generation !== this.generation) return false;
      this.lastInput = frozen.inputPath;
      this.store.patch({ submit: { ...this.store.get().submit, phase: "accepted" }, job });
      this.track(generation, job);
      return true;
    } catch (error) {
      if (generation !== this.generation) return false;
      const apiError = toApiError(error);
      // Only a lost answer may have started a job; a received rejection (400/409/422/503…) did not.
      if (apiError.outcomeUnknown) this.lastInput = frozen.inputPath;
      this.store.patch({ submit: { ...this.store.get().submit, phase: apiError.outcomeUnknown ? "unknown" : "rejected", error: apiError } });
      return false;
    }
  }

  /** Requests cancellation once; the GET status decides when it is cancelled. */
  async cancel(): Promise<void> {
    const s = this.store.get();
    if (s.tracking !== "active" || !s.job || isTerminal(s.job.status)) return;
    // Cancelling is idempotent on the server, so after a failed/unknown request the person may
    // ask again explicitly; it is never resent automatically.
    if (s.cancel.phase === "pending" || s.cancel.phase === "requested") return;
    const generation = this.generation;
    const id = s.job.id;
    this.store.patch({ cancel: { phase: "pending", error: null } });
    try {
      const job = await this.api.cancelJob(id);
      if (generation !== this.generation) return;
      this.store.patch({ cancel: { phase: "requested", error: null } });
      this.apply(generation, job);
    } catch (error) {
      if (generation !== this.generation) return;
      const apiError = toApiError(error);
      this.store.patch({ cancel: { phase: apiError.outcomeUnknown ? "unknown" : "failed", error: apiError } });
    }
    this.poller?.pollNow();
  }

  /** After an unknown submit: read the legacy queue (reference only; cannot identify the POST). */
  async checkQueue(): Promise<void> {
    const s = this.store.get();
    if (s.submit.phase !== "unknown" || s.queueCheck.phase === "pending") return;
    const generation = this.generation;
    this.store.patch({ queueCheck: { phase: "pending", active: null, error: null } });
    try {
      const items = await this.api.getQueue();
      if (generation !== this.generation) return;
      const active = items.filter((i) => i.transcription && (i.status === "waiting" || i.status === "running")).length;
      this.store.patch({ queueCheck: { phase: "done", active, error: null } });
    } catch (error) {
      if (generation !== this.generation) return;
      this.store.patch({ queueCheck: { phase: "error", active: null, error: toApiError(error) } });
    }
  }

  /**
   * Hide a finished/refused/unknown result from this screen. Display only: the server job, its
   * outputs and history are untouched, and the same-input guard is kept.
   */
  dismiss(): void {
    const s = this.store.get();
    if (s.submit.phase === "pending" || s.tracking === "active") return;
    this.stopMonitoring();
    this.generation += 1;
    this.store.set(initial);
  }

  /** App exit / test teardown: release the poller and SSE; late answers are dropped. */
  dispose(): void {
    this.generation += 1;
    this.stopMonitoring();
  }

  private track(generation: number, job: JobView): void {
    if (isTerminal(job.status)) {
      this.finish(job);
      return;
    }
    this.store.patch({ tracking: "active" });
    this.sseConnections = 0;
    this.openSse(generation, job.id);
    const poller = new SingleFlightPoller({
      task: (signal) => this.pollOnce(generation, job.id, signal),
      intervalMs: this.pollIntervalMs,
      backoff: POLL_BACKOFF,
      scheduler: this.scheduler,
    });
    this.poller = poller;
    poller.start();
  }

  private async pollOnce(generation: number, id: string, signal: AbortSignal): Promise<void> {
    try {
      const job = await this.api.getJob(id, signal);
      if (generation !== this.generation) return;
      this.store.patch({ poll: { failures: 0, lastError: null, lastOkAt: this.now() } });
      this.apply(generation, job);
      const state = this.store.get();
      if (state.tracking === "active" && state.sse === "closed" && this.sseConnections < this.maxSse) this.openSse(generation, id);
    } catch (error) {
      if (generation !== this.generation) return;
      if (isApiError(error) && error.kind === "aborted") return;
      const apiError = toApiError(error);
      if (apiError.kind === "not_found") {
        // The GUI process no longer knows this job (e.g. it was restarted): no outcome is known.
        this.stopMonitoring();
        this.store.patch({ tracking: "lost", poll: { ...this.store.get().poll, lastError: apiError } });
        return;
      }
      const poll = this.store.get().poll;
      this.store.patch({ poll: { ...poll, failures: poll.failures + 1, lastError: apiError } });
      throw apiError; // the poller backs off
    }
  }

  private apply(generation: number, job: JobView): void {
    if (generation !== this.generation) return;
    const current = this.store.get();
    if (current.tracking === "terminal" || current.tracking === "lost") return;
    if (isTerminal(job.status)) return this.finish(job);
    // Keep SSE-reported progress that is newer than this answer.
    const merged = current.job && current.job.lastEventId > job.lastEventId ? { ...job, progress: current.job.progress, lastEventId: current.job.lastEventId } : job;
    this.store.patch({ job: merged });
  }

  private finish(job: JobView): void {
    this.stopMonitoring();
    const cancel = this.store.get().cancel;
    const lateCancel = cancel.phase !== "idle" && job.status !== "cancelled";
    this.store.patch({ job, tracking: "terminal", cancel: lateCancel ? { phase: "too_late", error: cancel.error } : cancel });
  }

  private openSse(generation: number, id: string): void {
    const factory = this.options.eventSource;
    if (!factory) {
      this.store.patch({ sse: "unsupported" });
      return;
    }
    this.closeSse();
    this.sseConnections += 1;
    let source: EventSourceLike;
    try {
      source = factory(jobEventsPath(id));
    } catch {
      this.store.patch({ sse: "unsupported" });
      return;
    }
    this.source = source;
    this.store.patch({ sse: "connecting" });
    source.onopen = () => {
      if (this.source === source && generation === this.generation) this.store.patch({ sse: "open" });
    };
    source.onerror = () => {
      // Never let the browser reconnect on its own: the legacy stream restarts at cursor 0 and
      // ends after a terminal job, so an automatic reconnect would loop. Polling continues.
      if (this.source !== source) return;
      this.closeSse();
      if (generation !== this.generation) return;
      this.store.patch({ sse: "closed" });
      this.poller?.pollNow();
    };
    for (const name of SSE_EVENTS) {
      source.addEventListener(name, (event) => {
        if (this.source !== source || generation !== this.generation) return;
        const parsed = parseJobEvent(event.data);
        if (parsed) this.onEvent(parsed);
      });
    }
  }

  private onEvent(event: JobEvent): void {
    const s = this.store.get();
    if (s.tracking !== "active" || !s.job || event.id <= s.job.lastEventId) return; // replay/duplicate
    const progress = event.stage !== null && event.event !== "output_written" ? { stage: event.stage, ratio: event.ratio } : s.job.progress;
    this.store.patch({ job: { ...s.job, progress, lastEventId: event.id } });
    // `done`/`error` only ask for the authoritative status; they never set it.
    if (event.event === "done" || event.event === "error") this.poller?.pollNow();
  }

  private closeSse(): void {
    const source = this.source;
    this.source = null;
    if (source) {
      source.onopen = null;
      source.onerror = null;
      source.close();
    }
  }

  private stopMonitoring(): void {
    this.poller?.stop();
    this.poller = null;
    if (this.source) {
      this.closeSse();
      this.store.patch({ sse: "off" });
    }
  }
}

/** The browser EventSource, or undefined where it does not exist. Same-origin cookie session. */
export function browserEventSource(): EventSourceFactory | undefined {
  if (typeof EventSource === "undefined") return undefined;
  return (url) => new EventSource(url) as unknown as EventSourceLike;
}

