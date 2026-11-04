import { type Backoff, backoffDelay, type Scheduler } from "./scheduler";

export type PollerStatus = "idle" | "inflight" | "waiting" | "paused" | "stopped";

export interface PollerState {
  status: PollerStatus;
  consecutiveFailures: number;
  lastError: unknown;
  completed: number;
}

export interface PollerOptions {
  task: (signal: AbortSignal) => Promise<void>;
  intervalMs: number;
  backoff: Backoff;
  scheduler: Scheduler;
  onChange?: (state: PollerState) => void;
}

/**
 * Legacy single-in-flight polling (UI-1 D-07): the next run is scheduled only after the
 * previous one settled, so at most one request is ever outstanding. Failures are kept in
 * state (visible to the view) and back off; pause/stop are explicit.
 */
export class SingleFlightPoller {
  private state: PollerState = { status: "idle", consecutiveFailures: 0, lastError: null, completed: 0 };
  private timer: unknown = null;
  private controller: AbortController | null = null;
  private pausedWhileInflight = false;

  constructor(private readonly options: PollerOptions) {}

  get snapshot(): PollerState {
    return this.state;
  }

  start(): void {
    if (this.state.status === "stopped" || this.state.status === "inflight" || this.state.status === "waiting") return;
    this.runNow();
  }

  /** Run immediately unless a run is already in flight (coalesced, never parallel). */
  pollNow(): void {
    if (this.state.status === "stopped" || this.state.status === "paused" || this.state.status === "inflight") return;
    this.clearTimer();
    this.runNow();
  }

  pause(): void {
    if (this.state.status === "stopped") return;
    this.clearTimer();
    this.pausedWhileInflight = this.state.status === "inflight";
    this.set({ status: "paused" });
  }

  resume(): void {
    if (this.state.status !== "paused") return;
    if (this.pausedWhileInflight) {
      // The earlier run is still settling; it reschedules itself when it finishes.
      this.set({ status: "inflight" });
      return;
    }
    this.runNow();
  }

  stop(): void {
    this.clearTimer();
    this.controller?.abort();
    this.controller = null;
    this.set({ status: "stopped" });
  }

  private runNow(): void {
    const controller = new AbortController();
    this.controller = controller;
    this.pausedWhileInflight = false;
    this.set({ status: "inflight" });
    this.options.task(controller.signal).then(
      () => this.settle(controller, null, true),
      (error: unknown) => this.settle(controller, error, false),
    );
  }

  private settle(controller: AbortController, error: unknown, ok: boolean): void {
    if (this.controller !== controller) return;
    this.controller = null;
    const failures = ok ? 0 : this.state.consecutiveFailures + 1;
    const completed = this.state.completed + 1;
    if (this.state.status === "stopped") return;
    if (this.state.status === "paused") {
      this.pausedWhileInflight = false;
      this.set({ consecutiveFailures: failures, lastError: ok ? null : error, completed });
      return;
    }
    const delay = ok ? this.options.intervalMs : backoffDelay(this.options.backoff, failures);
    this.set({ status: "waiting", consecutiveFailures: failures, lastError: ok ? null : error, completed });
    this.timer = this.options.scheduler.setTimeout(() => {
      this.timer = null;
      if (this.state.status === "waiting") this.runNow();
    }, delay);
  }

  private clearTimer(): void {
    if (this.timer !== null) this.options.scheduler.clearTimeout(this.timer);
    this.timer = null;
  }

  private set(patch: Partial<PollerState>): void {
    this.state = { ...this.state, ...patch };
    this.options.onChange?.(this.state);
  }
}
