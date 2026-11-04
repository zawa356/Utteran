/**
 * v1 sync + SSE coordinator (B9): `/sync` first (authoritative snapshot), then
 * `/events?after=<cursor>`; only frames after the cursor are applied.
 *
 * EventSource cannot send headers, so the session travels in the SameSite=Strict cookie
 * (B9 events-sse.md). Native auto-reconnect is never used: it would resend the original
 * `?after=` together with a newer `Last-Event-ID`, which the server rejects as
 * `400 cursor_conflict`. Instead every reconnect is a new EventSource with `?after=` set to
 * the reducer cursor. EventSource exposes no status code, so a stream that fails before
 * `open` (400 cursor_invalid, 410 event_cursor_expired, 503 limit) falls back to `/sync`.
 * Reconnecting or resyncing never starts or retries any job/setup.
 */
import { parseEventEnvelope, parseResyncRequired, type SyncSnapshot } from "../api/v1";
import { type GapPolicy, EventReducer, type ReducerStats } from "./reducer";
import { type Backoff, backoffDelay, type Scheduler } from "./scheduler";

export interface EventSourceLike {
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export type ConnectionState = "idle" | "syncing" | "connected" | "reconnecting" | "resyncing" | "offline" | "stopped";

export interface SyncState {
  connection: ConnectionState;
  cursor: string | null;
  syncs: number;
  reconnects: number;
  stats: ReducerStats;
  lastProblem: string | null;
}

export interface SyncCoordinatorOptions {
  fetchSync: (signal: AbortSignal) => Promise<SyncSnapshot>;
  eventsUrl: (after: string | null) => string;
  openEvents: EventSourceFactory;
  scheduler: Scheduler;
  backoff?: Backoff;
  gapPolicy?: GapPolicy;
  onChange?: (state: SyncState, reducer: EventReducer) => void;
}

export class SyncCoordinator {
  readonly reducer: EventReducer;
  private state: SyncState;
  private source: EventSourceLike | null = null;
  private timer: unknown = null;
  private sync: AbortController | null = null;
  private failures = 0;
  private readonly backoff: Backoff;

  constructor(private readonly options: SyncCoordinatorOptions) {
    this.reducer = new EventReducer(options.gapPolicy ?? "resync");
    this.backoff = options.backoff ?? { initialMs: 1000, maxMs: 30_000 };
    this.state = { connection: "idle", cursor: null, syncs: 0, reconnects: 0, stats: this.reducer.stats, lastProblem: null };
  }

  get snapshot(): SyncState {
    return this.state;
  }

  start(): void {
    if (this.state.connection !== "idle" && this.state.connection !== "stopped") return;
    void this.resync("syncing", null);
  }

  stop(): void {
    this.closeSource();
    this.clearTimer();
    this.sync?.abort();
    this.sync = null;
    this.set({ connection: "stopped" });
  }

  private async resync(connection: "syncing" | "resyncing", problem: string | null): Promise<void> {
    this.closeSource();
    this.clearTimer();
    this.sync?.abort();
    const controller = new AbortController();
    this.sync = controller;
    this.set({ connection, lastProblem: problem ?? this.state.lastProblem });
    try {
      const snapshot = await this.options.fetchSync(controller.signal);
      if (this.sync !== controller) return;
      this.sync = null;
      this.reducer.applySync(snapshot);
      this.set({ syncs: this.state.syncs + 1, cursor: this.reducer.cursorString });
      this.open();
    } catch {
      if (this.sync !== controller) return;
      this.sync = null;
      this.failures += 1;
      this.set({ connection: "offline", lastProblem: "sync_failed" });
      this.schedule(() => void this.resync("resyncing", "sync_failed"));
    }
  }

  private open(): void {
    let opened = false;
    const source = this.options.openEvents(this.options.eventsUrl(this.reducer.cursorString));
    this.source = source;
    source.onopen = () => {
      if (this.source !== source) return;
      opened = true;
      this.failures = 0;
      this.set({ connection: "connected" });
    };
    source.onerror = () => {
      if (this.source !== source) return;
      this.closeSource();
      this.failures += 1;
      if (!opened) {
        // Rejected before streaming (400/410/503 are indistinguishable here): re-read /sync.
        this.set({ connection: "offline", lastProblem: "stream_rejected" });
        this.schedule(() => void this.resync("resyncing", "stream_rejected"));
        return;
      }
      this.set({ connection: "reconnecting", reconnects: this.state.reconnects + 1, lastProblem: "stream_closed" });
      this.schedule(() => this.open());
    };
    const onFrame = (event: MessageEvent) => {
      if (this.source !== source) return;
      let outcome;
      try {
        outcome = this.reducer.applyEvent(parseEventEnvelope(JSON.parse(String(event.data))));
      } catch {
        void this.resync("resyncing", "frame_invalid");
        return;
      }
      if (outcome === "resync") {
        void this.resync("resyncing", "sequence_mismatch");
        return;
      }
      this.set({ cursor: this.reducer.cursorString, stats: { ...this.reducer.stats } });
    };
    source.addEventListener("state_changed", onFrame);
    source.addEventListener("queue_changed", onFrame);
    source.addEventListener("resync_required", (event: MessageEvent) => {
      if (this.source !== source) return;
      let reason = "resync_required";
      try {
        reason = parseResyncRequired(JSON.parse(String(event.data))).reasonCode;
      } catch {
        // An unparsable control frame still means "the stream is no longer valid".
      }
      void this.resync("resyncing", reason);
    });
  }

  private schedule(work: () => void): void {
    this.clearTimer();
    this.timer = this.options.scheduler.setTimeout(() => {
      this.timer = null;
      if (this.state.connection !== "stopped") work();
    }, backoffDelay(this.backoff, this.failures - 1));
  }

  private closeSource(): void {
    const source = this.source;
    this.source = null;
    if (source) {
      source.onopen = null;
      source.onerror = null;
      source.close();
    }
  }

  private clearTimer(): void {
    if (this.timer !== null) this.options.scheduler.clearTimeout(this.timer);
    this.timer = null;
  }

  private set(patch: Partial<SyncState>): void {
    this.state = { ...this.state, ...patch, stats: { ...this.reducer.stats } };
    this.options.onChange?.(this.state, this.reducer);
  }
}
