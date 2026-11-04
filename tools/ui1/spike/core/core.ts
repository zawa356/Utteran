// Framework-agnostic UI core shared verbatim by the Vue and React spikes.
// It models the parts UI-2 must get right independent of the UI kit:
//  - shared defaults + per-file overrides -> effective settings
//  - latest-wins GET (stale response guard)
//  - POST with timeout/AbortController, error classification, retry with the same Idempotency-Key
//  - an `epoch:seq` event reducer (duplicates / out-of-order / resync_required -> /sync)
// The backend is a synthetic in-browser fake; no real API is called.

export type Settings = { model: string; language: string; diarization: boolean };
export type SettingKey = keyof Settings;
export type InputFile = { id: string; name: string };
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type Job = { id: string; fileId: string; status: JobStatus; revision: number };
export type ApiErrorKind = "conflict" | "timeout" | "network" | "server";
export type ApiError = { kind: ApiErrorKind; code: string; retryable: boolean };
export type Fault = "none" | "conflict" | "timeout";
export type Phase = "idle" | "pending" | "success" | "error";
export type EnvironmentView = { profile: string; backend: string; device: string; reason: string };

export type EventFrame = {
  id: string; // "epoch:seq"
  type: "state_changed" | "queue_changed" | "resync_required";
  job?: Job;
};

export type State = {
  mode: "normal" | "expert";
  files: InputFile[];
  selectedFileId: string;
  defaults: Settings;
  overrides: Record<string, Partial<Settings>>;
  environment: { phase: Phase; requested: string; data?: EnvironmentView; error?: ApiError };
  submit: { phase: Phase; idempotencyKey?: string; error?: ApiError; attempts: number };
  jobs: Record<string, Job>;
  cursor: { epoch: string; seq: number };
  stats: { applied: number; ignored: number; resyncs: number };
};

// --- tiny observable store (framework adapters subscribe to it) ---------------------------

export type Store<T> = { get(): T; set(next: T): void; subscribe(listener: () => void): () => void };

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next) {
      value = next;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

// --- pure domain helpers -------------------------------------------------------------------

export function effectiveSettings(state: State, fileId: string): Settings {
  return { ...state.defaults, ...(state.overrides[fileId] || {}) };
}

export function isOverridden(state: State, fileId: string, key: SettingKey): boolean {
  return Object.prototype.hasOwnProperty.call(state.overrides[fileId] || {}, key);
}

export function parseEventId(id: string): { epoch: string; seq: number } | null {
  const match = /^([A-Za-z0-9_-]+):(\d+)$/.exec(id);
  return match ? { epoch: match[1], seq: Number(match[2]) } : null;
}

/** Pure reducer: returns the next state and whether an authoritative /sync is required. */
export function reduceEvent(state: State, frame: EventFrame): { state: State; resync: boolean } {
  const parsed = parseEventId(frame.id);
  if (!parsed || frame.type === "resync_required" || parsed.epoch !== state.cursor.epoch) {
    return { state, resync: true };
  }
  if (parsed.seq <= state.cursor.seq) {
    return { state: { ...state, stats: { ...state.stats, ignored: state.stats.ignored + 1 } }, resync: false };
  }
  let jobs = state.jobs;
  if (frame.job) {
    const known = jobs[frame.job.id];
    if (!known || frame.job.revision > known.revision) jobs = { ...jobs, [frame.job.id]: frame.job };
  }
  return {
    state: {
      ...state,
      jobs,
      cursor: { epoch: parsed.epoch, seq: parsed.seq },
      stats: { ...state.stats, applied: state.stats.applied + 1 },
    },
    resync: false,
  };
}

// --- synthetic backend ------------------------------------------------------------------

export type SyncSnapshot = { cursor: { epoch: string; seq: number }; jobs: Job[] };

export class FakeBackend {
  fault: Fault = "none";
  private jobs = new Map<string, Job>();
  private byKey = new Map<string, string>();
  private seq = 0;
  readonly epoch = "e1";
  submitCalls = 0;

  environment(profile: string, signal: AbortSignal): Promise<EnvironmentView> {
    const delay = profile === "cuda" ? 400 : 40; // the newly probed profile is slower
    return abortableDelay(delay, signal).then(() => ({
      profile,
      backend: "faster-whisper",
      device: profile === "cuda" ? "cuda" : "cpu",
      reason: "resolved by backend capability evidence (synthetic)",
    }));
  }

  submit(body: { fileId: string; settings: Settings }, idempotencyKey: string, signal: AbortSignal): Promise<Job> {
    this.submitCalls += 1;
    if (this.fault === "timeout") return new Promise((_resolve, reject) => onAbort(signal, reject));
    if (this.fault === "conflict") {
      return abortableDelay(30, signal).then(() => {
        throw httpError(409, "queue_revision_conflict");
      });
    }
    return abortableDelay(30, signal).then(() => {
      const existing = this.byKey.get(idempotencyKey);
      if (existing) return { ...this.jobs.get(existing)! }; // replay, not a second job
      const job: Job = { id: `job-${this.jobs.size + 1}`, fileId: body.fileId, status: "queued", revision: 1 };
      this.jobs.set(job.id, job);
      this.byKey.set(idempotencyKey, job.id);
      this.seq += 1;
      return { ...job };
    });
  }

  advance(jobId: string, status: JobStatus): EventFrame {
    const job = this.jobs.get(jobId)!;
    const next = { ...job, status, revision: job.revision + 1 };
    this.jobs.set(jobId, next);
    this.seq += 1;
    return { id: `${this.epoch}:${this.seq}`, type: "state_changed", job: { ...next } };
  }

  sync(): Promise<SyncSnapshot> {
    return Promise.resolve({ cursor: { epoch: this.epoch, seq: this.seq }, jobs: [...this.jobs.values()].map((job) => ({ ...job })) });
  }
}

type HttpError = Error & { status: number; code: string };

function httpError(status: number, code: string): HttpError {
  return Object.assign(new Error(code), { status, code });
}

function onAbort(signal: AbortSignal, reject: (reason: unknown) => void): void {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    onAbort(signal, (reason) => {
      clearTimeout(timer);
      reject(reason);
    });
  });
}

export function classify(error: unknown): ApiError {
  if (error instanceof DOMException && error.name === "TimeoutError") return { kind: "timeout", code: "timeout", retryable: true };
  const status = (error as Partial<HttpError>)?.status;
  if (status === 409) return { kind: "conflict", code: (error as HttpError).code, retryable: true };
  if (typeof status === "number") return { kind: "server", code: (error as HttpError).code, retryable: status >= 500 };
  return { kind: "network", code: "network", retryable: true };
}

// --- controller (actions) ---------------------------------------------------------------

export const SUBMIT_TIMEOUT_MS = 1500;

export function initialState(): State {
  return {
    mode: "expert",
    files: [
      { id: "f1", name: "synthetic-meeting-a.wav" },
      { id: "f2", name: "synthetic-interview-b.mp4" },
    ],
    selectedFileId: "f1",
    defaults: { model: "large-v3-turbo", language: "ja", diarization: true },
    overrides: {},
    environment: { phase: "idle", requested: "cpu" },
    submit: { phase: "idle", attempts: 0 },
    jobs: {},
    cursor: { epoch: "e1", seq: 0 },
    stats: { applied: 0, ignored: 0, resyncs: 0 },
  };
}

export function createController(backend = new FakeBackend()) {
  const store = createStore(initialState());
  const patch = (fn: (state: State) => State) => store.set(fn(store.get()));
  let environmentRequest: AbortController | null = null;
  let keyCounter = 0;

  async function loadEnvironment(profile: string): Promise<void> {
    environmentRequest?.abort(); // latest wins: the older request is cancelled ...
    const request = new AbortController();
    environmentRequest = request;
    patch((s) => ({ ...s, environment: { ...s.environment, phase: "pending", requested: profile, error: undefined } }));
    try {
      const data = await backend.environment(profile, request.signal);
      if (environmentRequest !== request) return; // ... and ignored even if it resolves anyway
      patch((s) => ({ ...s, environment: { phase: "success", requested: profile, data } }));
    } catch (error) {
      if (environmentRequest !== request) return;
      patch((s) => ({ ...s, environment: { ...s.environment, phase: "error", error: classify(error) } }));
    }
  }

  async function submit(retry = false): Promise<void> {
    const current = store.get();
    if (current.submit.phase === "pending") return; // double click / double submit guard
    const fileId = current.selectedFileId;
    const idempotencyKey = retry && current.submit.idempotencyKey ? current.submit.idempotencyKey : `spike-${Date.now()}-${++keyCounter}`;
    patch((s) => ({ ...s, submit: { phase: "pending", idempotencyKey, attempts: s.submit.attempts + 1 } }));
    try {
      const signal = AbortSignal.timeout(SUBMIT_TIMEOUT_MS);
      const job = await backend.submit({ fileId, settings: effectiveSettings(current, fileId) }, idempotencyKey, signal);
      // POST acceptance is not a terminal state; the queue view is reconciled through /sync.
      patch((s) => ({ ...s, submit: { ...s.submit, phase: "success" }, jobs: { ...s.jobs, [job.id]: s.jobs[job.id] ?? job } }));
      await resync();
    } catch (error) {
      patch((s) => ({ ...s, submit: { ...s.submit, phase: "error", error: classify(error) } }));
    }
  }

  async function resync(): Promise<void> {
    const snapshot = await backend.sync();
    patch((s) => ({
      ...s,
      cursor: snapshot.cursor,
      jobs: Object.fromEntries(snapshot.jobs.map((job) => [job.id, job])),
      stats: { ...s.stats, resyncs: s.stats.resyncs + 1 },
    }));
  }

  async function receive(frame: EventFrame): Promise<void> {
    const result = reduceEvent(store.get(), frame);
    store.set(result.state);
    if (result.resync) await resync();
  }

  return {
    store,
    backend,
    setMode: (mode: State["mode"]) => patch((s) => ({ ...s, mode })),
    selectFile: (fileId: string) => patch((s) => ({ ...s, selectedFileId: fileId })),
    setDefault: <K extends SettingKey>(key: K, value: Settings[K]) => patch((s) => ({ ...s, defaults: { ...s.defaults, [key]: value } })),
    setOverride: <K extends SettingKey>(fileId: string, key: K, value: Settings[K] | undefined) =>
      patch((s) => {
        const next = { ...(s.overrides[fileId] || {}) };
        if (value === undefined) delete next[key];
        else next[key] = value;
        return { ...s, overrides: { ...s.overrides, [fileId]: next } };
      }),
    loadEnvironment,
    submit: () => submit(false),
    retry: () => submit(true),
    receive,
    resync,
  };
}

export type Controller = ReturnType<typeof createController>;

export const LANGUAGES = ["ja", "en", "auto"];
export const MODELS = ["large-v3-turbo", "small"];
export const SECTIONS = ["workspace", "results", "models", "queue", "setup", "settings"] as const;
export const LABELS: Record<string, string> = {
  workspace: "Transcribe",
  results: "Results",
  models: "Models",
  queue: "Queue",
  setup: "Setup",
  settings: "Settings",
};

/** Test hook: identical in every spike so one Playwright script drives all of them. */
export function exposeForTests(controller: Controller): void {
  (window as unknown as { __spike: unknown }).__spike = {
    controller,
    state: () => controller.store.get(),
    setFault: (fault: Fault) => {
      controller.backend.fault = fault;
    },
    submitCalls: () => controller.backend.submitCalls,
  };
}
