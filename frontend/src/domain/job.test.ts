import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { type JobStatus, type JobView, type LegacyJobsApi, parseJobSnapshot, type TranscriptionRequest } from "../api/legacyJobs";
import { deferred } from "../test/fakeFetch";
import { type EventSourceLike, JobController, MAX_SSE_CONNECTIONS, POLL_INTERVAL_MS } from "./job";

const A = "a".repeat(32);
const B = "b".repeat(32);
const ROOT = ["", "synthetic", "media"].join("/");
const request = (input = `${ROOT}/one.wav`): TranscriptionRequest => ({
  inputPath: input,
  outputDir: `${ROOT}/out`,
  profile: "cpu",
  asrBackend: "faster-whisper",
  asrModel: "small",
  asrDevice: "cpu",
  language: "ja",
  formats: ["srt"],
});

function view(id: string, status: JobStatus, patch: Record<string, unknown> = {}): JobView {
  return parseJobSnapshot({ id, status, outputs: [], events: [], logs: [], guidance: null, ...patch });
}

class FakeSource implements EventSourceLike {
  static all: FakeSource[] = [];
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  readonly listeners = new Map<string, ((event: MessageEvent) => void)[]>();
  constructor(readonly url: string) {
    FakeSource.all.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    for (const l of this.listeners.get(type) ?? []) l(new MessageEvent(type, { data: typeof data === "string" ? data : JSON.stringify(data) }));
  }
}

type Fn<K extends keyof LegacyJobsApi> = LegacyJobsApi[K];
function fakeApi() {
  const calls: string[] = [];
  const api = {
    start: vi.fn<Fn<"startJob">>(),
    get: vi.fn<Fn<"getJob">>(),
    cancel: vi.fn<Fn<"cancelJob">>(),
    queue: vi.fn<Fn<"getQueue">>(),
  };
  const jobs: LegacyJobsApi = {
    startJob: (r) => (calls.push("POST /api/jobs"), api.start(r)),
    getJob: (id, signal) => (calls.push(`GET ${id.slice(0, 1)}`), api.get(id, signal)),
    cancelJob: (id) => (calls.push(`CANCEL ${id.slice(0, 1)}`), api.cancel(id)),
    getQueue: (signal) => (calls.push("GET /api/queue"), api.queue(signal)),
  };
  return { api, jobs, calls };
}

function setup(sse = true) {
  const f = fakeApi();
  const controller = new JobController(f.jobs, { eventSource: sse ? (url) => new FakeSource(url) : undefined });
  return { ...f, controller, state: () => controller.store.get() };
}

const settle = () => vi.advanceTimersByTimeAsync(0);

describe("JobController (legacy /api/jobs, one job at a time)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSource.all = [];
  });
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("a double click sends one POST; 202 starts tracking by GET", async () => {
    const t = setup();
    const answer = deferred<JobView>();
    t.api.start.mockReturnValueOnce(answer.promise);
    t.api.get.mockResolvedValue(view(A, "running"));
    const first = t.controller.submit(request());
    expect(await t.controller.submit(request())).toBe(false);
    expect(t.controller.blockFor(request())).toBe("busy");
    expect(t.state().submit.phase).toBe("pending");
    answer.resolve(view(A, "starting"));
    expect(await first).toBe(true);
    await settle();
    expect(t.calls.filter((c) => c.startsWith("POST"))).toEqual(["POST /api/jobs"]);
    expect(t.state()).toMatchObject({ tracking: "active", submit: { phase: "accepted" } });
    expect(t.state().job?.status).toBe("running");
    expect(t.state().snapshot?.body).toMatchObject({ diarization_enabled: false, input_path: `${ROOT}/one.wav` });
    expect(Object.isFrozen(t.state().snapshot?.request)).toBe(true);
    t.controller.dispose();
  });

  it("a received refusal (409/400/503) creates nothing and is never resent", async () => {
    for (const kind of ["conflict", "validation", "unavailable"] as const) {
      const t = setup();
      t.api.start.mockRejectedValueOnce(new ApiError({ kind, status: 409 }));
      expect(await t.controller.submit(request())).toBe(false);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(t.state().submit.phase).toBe("rejected");
      expect(t.calls).toEqual(["POST /api/jobs"]);
      // A refusal did not start anything: the same input may be sent again explicitly.
      expect(t.controller.blockFor(request())).toBeNull();
    }
  });

  it("a lost answer is 'unknown': no resend, no new start until acknowledged, same input stays blocked", async () => {
    const t = setup();
    t.api.start.mockRejectedValueOnce(new ApiError({ kind: "timeout", outcomeUnknown: true, retryable: true }));
    await t.controller.submit(request());
    await vi.advanceTimersByTimeAsync(300_000);
    expect(t.calls).toEqual(["POST /api/jobs"]);
    expect(t.state().submit.phase).toBe("unknown");
    expect(t.controller.blockFor(request(`${ROOT}/other.wav`))).toBe("unknown_unresolved");
    t.api.queue.mockResolvedValueOnce([
      { id: "x", transcription: true, status: "running", position: null },
      { id: "y", transcription: false, status: "waiting", position: 1 },
    ]);
    await t.controller.checkQueue();
    expect(t.state().queueCheck).toMatchObject({ phase: "done", active: 1 });
    t.controller.dismiss();
    expect(t.controller.blockFor(request(`${ROOT}/other.wav`))).toBeNull();
    expect(t.controller.blockFor(request())).toBe("same_input");
    // POSIX paths compare exactly (a different case is a different file there).
    expect(t.controller.blockFor(request(`${ROOT.toUpperCase()}/one.wav`))).toBeNull();
  });

  it("SSE 'done' alone is never success; GET decides", async () => {
    const t = setup();
    t.api.start.mockResolvedValueOnce(view(A, "starting"));
    const pendingGet = deferred<JobView>();
    t.api.get.mockResolvedValueOnce(view(A, "running")).mockReturnValueOnce(pendingGet.promise);
    await t.controller.submit(request());
    await settle();
    const source = FakeSource.all[0]!;
    expect(source.url).toBe(`/api/jobs/${A}/events`);
    source.onopen?.(new Event("open"));
    expect(t.state().sse).toBe("open");
    source.emit("progress", { id: 1, event: "progress", stage: "asr", ratio: 0.5 });
    expect(t.state().job?.progress).toEqual({ stage: "asr", ratio: 0.5 });
    source.emit("done", { id: 2, event: "done", exit_code: 0 });
    await settle();
    expect(t.state().job?.status).toBe("running");
    expect(t.state().tracking).toBe("active");
    pendingGet.resolve(view(A, "failed", { exit_code: 1, guidance: { key: "general" } }));
    await settle();
    expect(t.state()).toMatchObject({ tracking: "terminal", sse: "off" });
    expect(t.state().job).toMatchObject({ status: "failed", guidance: "general" });
    expect(source.closed).toBe(true);
    const gets = t.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.calls.length).toBe(gets); // poller stopped
  });

  it("replayed or duplicate SSE events are ignored; unknown events and bad JSON do nothing", async () => {
    const t = setup();
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockResolvedValue(view(A, "running", { events: [{ id: 0, event: "stage_start", stage: "audio" }, { id: 1, event: "stage_start", stage: "asr" }] }));
    await t.controller.submit(request());
    await settle();
    const source = FakeSource.all[0]!;
    source.emit("stage_start", { id: 0, event: "stage_start", stage: "audio" }); // replay from cursor 0
    expect(t.state().job?.progress.stage).toBe("asr");
    source.emit("progress", "{not json");
    source.emit("run_summary", { id: 9, event: "run_summary" });
    expect(t.state().job?.lastEventId).toBe(1);
    t.controller.dispose();
  });

  it("SSE failure falls back to polling; reconnects are bounded and never automatic", async () => {
    const t = setup();
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockResolvedValue(view(A, "running"));
    await t.controller.submit(request());
    await settle();
    for (let i = 0; i < 6; i += 1) {
      const source = FakeSource.all[FakeSource.all.length - 1]!;
      source.onerror?.(new Event("error"));
      expect(source.closed).toBe(true);
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    }
    expect(FakeSource.all).toHaveLength(MAX_SSE_CONNECTIONS);
    expect(t.state().sse).toBe("closed");
    const before = t.calls.filter((c) => c.startsWith("GET")).length;
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5);
    expect(t.calls.filter((c) => c.startsWith("GET")).length).toBeGreaterThanOrEqual(before + 4);
    t.controller.dispose();
  });

  it("no EventSource: polling only", async () => {
    const t = setup(false);
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockResolvedValueOnce(view(A, "running")).mockResolvedValueOnce(view(A, "completed", { outputs: [`${ROOT}/out/one.srt`] }));
    await t.controller.submit(request());
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    expect(t.state()).toMatchObject({ tracking: "terminal", sse: "unsupported" });
    expect(t.state().job).toMatchObject({ status: "completed", outputCount: 1, outputNames: ["one.srt"] });
  });

  it("poll failures are visible and back off; a malformed (unknown status) answer is not applied", async () => {
    const t = setup(false);
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockRejectedValue(new ApiError({ kind: "malformed" }));
    await t.controller.submit(request());
    await settle();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.state().poll.failures).toBeGreaterThanOrEqual(2);
    expect(t.state().job?.status).toBe("running");
    expect(t.state().tracking).toBe("active");
    const calls = t.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.calls.length - calls).toBeLessThanOrEqual(1); // backoff (≥8 s) after three failures
    t.controller.dispose();
  });

  it("404 after acceptance = the server no longer knows the job: 'lost', not success or failure", async () => {
    const t = setup(false);
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockRejectedValueOnce(new ApiError({ kind: "not_found", status: 404 }));
    await t.controller.submit(request());
    await settle();
    expect(t.state().tracking).toBe("lost");
    expect(t.state().job?.status).toBe("running");
    const calls = t.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(t.calls.length).toBe(calls);
  });

  it("cancel: 202 = requested; 'cancelled' only from GET; a failed request is shown, not hidden", async () => {
    const t = setup(false);
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockResolvedValue(view(A, "running"));
    await t.controller.submit(request());
    await settle();
    t.api.cancel.mockRejectedValueOnce(new ApiError({ kind: "server", status: 500 }));
    await t.controller.cancel();
    expect(t.state().cancel.phase).toBe("failed");
    t.api.cancel.mockResolvedValueOnce(view(A, "running"));
    await t.controller.cancel();
    expect(t.state().cancel.phase).toBe("requested");
    expect(t.state().job?.status).toBe("running");
    await t.controller.cancel(); // no second request while requested
    expect(t.calls.filter((c) => c.startsWith("CANCEL"))).toHaveLength(2);
    t.api.get.mockResolvedValue(view(A, "cancelled", { exit_code: 130, guidance: { key: "cancelled" } }));
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(t.state()).toMatchObject({ tracking: "terminal", cancel: { phase: "requested" } });
    expect(t.state().job?.status).toBe("cancelled");
  });

  it("cancel that loses the race to completion is 'too_late'; an unknown cancel can be asked again", async () => {
    const t = setup(false);
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockResolvedValue(view(A, "running"));
    await t.controller.submit(request());
    await settle();
    t.api.cancel.mockRejectedValueOnce(new ApiError({ kind: "network", outcomeUnknown: true }));
    await t.controller.cancel();
    expect(t.state().cancel.phase).toBe("unknown");
    t.api.cancel.mockResolvedValueOnce(view(A, "completed"));
    await t.controller.cancel();
    expect(t.state()).toMatchObject({ tracking: "terminal", cancel: { phase: "too_late" } });
  });

  it("a late answer for an earlier job never touches the next one (UI-1 D-04)", async () => {
    const t = setup();
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    const lateA = deferred<JobView>();
    t.api.get.mockReturnValueOnce(lateA.promise);
    await t.controller.submit(request(`${ROOT}/one.wav`));
    await settle();
    // A finishes through a cancel answer; then B is submitted while A's GET is still open.
    t.api.cancel.mockResolvedValueOnce(view(A, "cancelled"));
    await t.controller.cancel();
    expect(t.state().tracking).toBe("terminal");
    t.api.start.mockResolvedValueOnce(view(B, "running"));
    t.api.get.mockResolvedValue(view(B, "running"));
    await t.controller.submit(request(`${ROOT}/two.wav`));
    await settle();
    lateA.resolve(view(A, "completed"));
    await settle();
    expect(t.state().job?.id).toBe(B);
    expect(t.state().tracking).toBe("active");
    const sourceB = FakeSource.all[FakeSource.all.length - 1]!;
    expect(sourceB.url).toContain(B);
    expect(sourceB.closed).toBe(false);
    expect(FakeSource.all[0]!.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(t.calls.filter((c) => c === "GET b").length).toBeGreaterThanOrEqual(2);
    t.controller.dispose();
  });

  it("dismiss is display-only and refused while running; dispose leaves no timer or stream", async () => {
    const t = setup();
    t.api.start.mockResolvedValueOnce(view(A, "running"));
    t.api.get.mockResolvedValue(view(A, "running"));
    await t.controller.submit(request());
    await settle();
    t.controller.dismiss();
    expect(t.state().tracking).toBe("active");
    t.controller.dispose();
    expect(FakeSource.all.every((s) => s.closed)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    const calls = t.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.calls.length).toBe(calls);
  });

  it("same-input guard covers every accepted input, not only the last (A → B → A is refused; PR #42 P2-1)", async () => {
    const t = setup(false);
    const a = request(`${ROOT}/a.wav`);
    const b = request(`${ROOT}/b.wav`);
    t.api.start.mockResolvedValueOnce(view(A, "running")).mockResolvedValueOnce(view(B, "running"));
    t.api.get.mockResolvedValueOnce(view(A, "completed")).mockResolvedValueOnce(view(B, "completed"));
    expect(await t.controller.submit(a)).toBe(true);
    await settle();
    expect(t.state().tracking).toBe("terminal");
    t.controller.dismiss();
    expect(await t.controller.submit(b)).toBe(true);
    await settle();
    expect(t.state().tracking).toBe("terminal");
    t.controller.dismiss();
    expect(t.controller.blockFor(a)).toBe("same_input");
    expect(t.controller.blockFor(b)).toBe("same_input");
    expect(await t.controller.submit(a)).toBe(false);
    expect(t.calls.filter((c) => c === "POST /api/jobs")).toHaveLength(2);
  });

  it("an unknown-outcome input stays guarded after another file is sent; Windows-equivalent spellings match", async () => {
    const t = setup(false);
    const drive = ["D:", "synthetic", "Media", "a.wav"].join("\\");
    const sameDrive = ["d:", "SYNTHETIC", "media", "A.WAV"].join("/");
    t.api.start.mockRejectedValueOnce(new ApiError({ kind: "timeout", outcomeUnknown: true }));
    await t.controller.submit(request(drive));
    t.controller.dismiss();
    t.api.start.mockResolvedValueOnce(view(B, "completed"));
    expect(await t.controller.submit(request(`${ROOT}/b.wav`))).toBe(true);
    t.controller.dismiss();
    expect(t.controller.blockFor(request(drive))).toBe("same_input");
    expect(t.controller.blockFor(request(sameDrive))).toBe("same_input");
    expect(await t.controller.submit(request(sameDrive))).toBe(false);
    expect(t.calls.filter((c) => c === "POST /api/jobs")).toHaveLength(2);
    // A received refusal never starts anything and is not guarded.
    t.api.start.mockRejectedValueOnce(new ApiError({ kind: "conflict", status: 409 }));
    await t.controller.submit(request(`${ROOT}/c.wav`));
    t.controller.dismiss();
    expect(t.controller.blockFor(request(`${ROOT}/c.wav`))).toBeNull();
  });

  it("an immediately terminal 202 is shown as such without monitoring", async () => {
    const t = setup();
    t.api.start.mockResolvedValueOnce(view(A, "cancelled"));
    await t.controller.submit(request());
    expect(t.state().tracking).toBe("terminal");
    expect(FakeSource.all).toHaveLength(0);
    t.controller.dismiss();
    expect(t.state().job).toBeNull();
    expect(t.controller.blockFor(request())).toBe("same_input");
  });
});
