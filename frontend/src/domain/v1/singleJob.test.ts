import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FetchLike } from "../../api/http";
import { createFakeHost } from "../../host/fake";
import { json } from "../../test/fakeFetch";
import { fakeV1Server, presetWire, V1_POLICY, V1_PRESET } from "../../test/v1Server";
import { createCore } from "../core";

const OUT = ["", "synthetic", "out"].join("/");
const IN = ["", "synthetic", "media", "talk.wav"].join("/");

/** Legacy reads (settings/environment) + the fake v1 server; records every request line. */
function setup(options: Parameters<typeof fakeV1Server>[0] = {}) {
  const v1 = fakeV1Server(options);
  const lines: string[] = [];
  const fetch: FetchLike = async (url, init) => {
    const path = new URL(url, "http://x").pathname;
    lines.push(`${init.method} ${path}`);
    if (path.startsWith("/api/v1/")) return v1.fetch(url, init);
    if (path === "/api/settings") return json(200, { theme: "system", language: "ja", default_profile: null, default_output_dir: OUT });
    if (path === "/api/environment") return json(503, { detail: "synthetic" });
    throw new Error(`unexpected ${init.method} ${url}`);
  };
  const host = createFakeHost();
  const core = createCore({ fetch, host, apiMode: "v1-dev" });
  return { v1, lines, core, host };
}

async function ready(options: Parameters<typeof fakeV1Server>[0] = {}) {
  const s = setup(options);
  await s.core.start();
  const lib = s.core.v1!.library;
  await lib.select("preset", V1_PRESET);
  await lib.select("policy", V1_POLICY);
  s.host.nextPick = IN;
  await s.core.inputs.pick(s.host);
  return { ...s, lib, job: s.core.v1!.job };
}

const legacyStarts = (lines: string[]) => lines.filter((l) => /^(POST|PATCH|PUT|DELETE) \/api\/(?!v1\/)/.test(l));

describe("S5-A v1 single job (AppCore in v1-dev mode)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("start-up only reads: no Preset/Policy is created and the queue is not touched", async () => {
    const { core, lines, v1 } = setup({ presets: [], policies: [] });
    await core.start();
    expect(lines.filter((l) => !l.startsWith("GET "))).toEqual([]);
    expect(v1.applied).toEqual([]);
    expect(core.v1!.library.store.get().presets).toMatchObject({ phase: "done", items: [] });
    expect(core.v1!.job.store.get().queue.view?.dispatch).toBe("paused");
    expect(core.v1!.job.evaluate().issues.map((i) => i.field)).toEqual(expect.arrayContaining(["preset", "policy", "input"]));
  });

  it("happy path: freeze → validate → resolve → submit → GET; the job shows the server status; never the legacy start", async () => {
    const { job, v1, lines, lib } = await ready();
    lib.setFormats(["srt"]);
    await job.prepare();
    expect(job.store.get().step).toBe("resolved");
    const sent = v1.writes();
    expect(sent.map((w) => w.line)).toEqual(["POST /api/v1/validations", "POST /api/v1/execution-resolutions"]);
    const session = JSON.parse(sent[1]!.body!);
    expect(session).toMatchObject({ schema_version: 1, preset_ref: { id: V1_PRESET, revision: 1 }, policy_ref: { id: V1_POLICY, revision: 1 }, common_overrides: [{ path: "/output/formats", value: ["srt"] }] });
    expect(session.files).toHaveLength(1);
    expect(session.files[0]).toMatchObject({ input_path: IN, output_dir: OUT, overrides: [] });
    expect(session.files[0].client_file_id).toMatch(/^f-[0-9a-f]{24}$/);
    expect(sent[0]!.key).not.toBe(sent[1]!.key);
    await job.submit();
    const submit = v1.writes()[2]!;
    expect(submit.line).toBe("POST /api/v1/jobs");
    const body = JSON.parse(submit.body!);
    expect(body).toMatchObject({ schema_version: 1, resume_mode: "resume" });
    expect(body.editing_session).toEqual(session);
    expect(new Set(v1.writes().map((w) => w.key)).size).toBe(3);
    expect(job.store.get()).toMatchObject({ step: "accepted", origin: "submitted", tracking: "active" });
    await vi.advanceTimersByTimeAsync(0);
    expect(lines).toContain(`GET /api/v1/jobs/${v1.jobs[0]!.id as string}`);
    expect(job.store.get().job?.status).toBe("queued");
    v1.setJobStatus("succeeded");
    await vi.advanceTimersByTimeAsync(2100);
    expect(job.store.get()).toMatchObject({ tracking: "final", job: { status: "succeeded", revision: 2 } });
    const after = lines.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(lines.length).toBe(after); // polling stopped at the final status
    expect(legacyStarts(lines)).toEqual([]);
    expect(v1.queue.dispatch).toBe("paused"); // accepting a job never resumes the queue
  });

  it("an edit after the check makes the resolution stale; submit sends nothing until a new check", async () => {
    const { job, v1, lib } = await ready();
    await job.prepare();
    lib.setLanguage("en");
    expect(job.isStale()).toBe(true);
    expect(job.canSubmit()).toBe(false);
    await job.submit();
    expect(v1.writes().map((w) => w.line)).not.toContain("POST /api/v1/jobs");
    await job.prepare();
    const latest = JSON.parse(v1.writes().at(-1)!.body!);
    expect(latest.common_overrides).toEqual([{ path: "/asr/language", value: "en" }]);
    expect(job.isStale()).toBe(false);
  });

  it("refuses to continue when the server's effective settings differ from the screen", async () => {
    const { job, v1, core } = await ready();
    v1.inject.set("POST /api/v1/validations", {
      reply: json(200, {
        schema_version: 1,
        valid: true,
        preset_ref: { id: V1_PRESET, revision: 1 },
        policy_ref: { id: V1_POLICY, revision: 1 },
        files: [{ client_file_id: (core.v1!.job.evaluate().session!.files as { client_file_id: string }[])[0]!.client_file_id, valid: true, effective_settings: { ...presetWire().settings as object, schema_version: 1, asr: { ...(presetWire().settings as { asr: object }).asr, beam_size: 1 } }, provenance: [], issues: [], warnings: [], worker_support: "unverified" }],
      }),
    });
    await job.prepare();
    expect(job.store.get()).toMatchObject({ step: "rejected", mismatch: true, failedAt: "validation" });
    expect(v1.writes().map((w) => w.line)).toEqual(["POST /api/v1/validations"]);
  });

  it("a 409 execution_unavailable is a refusal with its issue codes; no job, no fallback", async () => {
    const { job, v1, lines } = await ready();
    v1.resolveError = { status: 409, code: "execution_unavailable", issues: [{ path: "/files/0", code: "candidate_not_ready" }] };
    await job.prepare();
    const s = job.store.get();
    expect(s).toMatchObject({ step: "rejected", failedAt: "resolution" });
    expect(s.error?.issues).toEqual([{ path: "/files/0", code: "candidate_not_ready" }]);
    expect(v1.jobs).toHaveLength(0);
    expect(legacyStarts(lines)).toEqual([]);
  });

  it("5xx on submit: unknown, zero automatic resends; an explicit re-check resends the same key and body and gets the same job", async () => {
    const { job, v1 } = await ready();
    await job.prepare();
    // The server applies the job but the answer is lost as a 500.
    v1.inject.set("POST /api/v1/jobs", { reply: json(500, {}), apply: true });
    await job.submit();
    expect(job.store.get()).toMatchObject({ step: "unknown", failedAt: "submission" });
    expect(job.blockFor()).toBe("unknown_unresolved");
    await vi.advanceTimersByTimeAsync(60_000);
    const submits = () => v1.writes().filter((w) => w.line === "POST /api/v1/jobs");
    expect(submits()).toHaveLength(1);
    await job.prepare(); // blocked while unknown
    expect(v1.writes().filter((w) => w.line === "POST /api/v1/validations")).toHaveLength(1);
    await job.recheck();
    expect(submits()).toHaveLength(2);
    expect(submits()[1]!.key).toBe(submits()[0]!.key);
    expect(submits()[1]!.body).toBe(submits()[0]!.body);
    expect(v1.jobs).toHaveLength(1);
    expect(job.store.get()).toMatchObject({ step: "accepted", job: { id: v1.jobs[0]!.id } });
  });

  it("network failure and a broken 202 are unknown too", async () => {
    const { job, v1 } = await ready();
    await job.prepare();
    v1.inject.set("POST /api/v1/jobs", { reply: new TypeError("network down") });
    await job.submit();
    expect(job.store.get().step).toBe("unknown");
    v1.inject.set("POST /api/v1/jobs", { reply: json(202, { schema_version: 1, jobs: "broken" }), apply: true });
    await job.recheck();
    expect(job.store.get().step).toBe("unknown");
    await job.recheck();
    expect(job.store.get().step).toBe("accepted");
    expect(v1.jobs).toHaveLength(1);
    expect(new Set(v1.writes().filter((w) => w.line === "POST /api/v1/jobs").map((w) => w.key)).size).toBe(1);
  });

  it("double click and concurrent prepare send one validation; submit twice sends one job", async () => {
    const { job, v1 } = await ready();
    await Promise.all([job.prepare(), job.prepare()]);
    expect(v1.writes().filter((w) => w.line === "POST /api/v1/validations")).toHaveLength(1);
    await Promise.all([job.submit(), job.submit()]);
    expect(v1.writes().filter((w) => w.line === "POST /api/v1/jobs")).toHaveLength(1);
  });

  it("a token refused as consumed is a refusal (no other candidate, no new token silently)", async () => {
    const { job, v1 } = await ready();
    await job.prepare();
    v1.inject.set("POST /api/v1/jobs", { reply: json(409, { schema_version: 1, error: { code: "resolution_consumed", message_key: "x", request_id: "r", retryable: false, issues: [] } }) });
    await job.submit();
    expect(job.store.get()).toMatchObject({ step: "rejected", failedAt: "submission", error: { code: "resolution_consumed" } });
    expect(v1.writes().filter((w) => w.line === "POST /api/v1/execution-resolutions")).toHaveLength(1);
  });

  it("an expired resolution cannot be submitted", async () => {
    const { job, v1 } = await ready();
    v1.inject.set("POST /api/v1/execution-resolutions", {
      reply: json(201, { schema_version: 1, id: "res_" + "0".repeat(32), token: `v1.${"0".repeat(32)}.${"1".repeat(13)}.${"f".repeat(64)}`, expires_at: "2000-01-01T00:00:00Z", files: [{ client_file_id: (job.evaluate().session!.files as { client_file_id: string }[])[0]!.client_file_id, input_identity: "in_" + "a".repeat(32), spec: (await import("../../test/v1Server")).SPEC }] }),
    });
    await job.prepare();
    expect(job.isExpired()).toBe(true);
    await job.submit();
    expect(v1.writes().map((w) => w.line)).not.toContain("POST /api/v1/jobs");
  });

  it("queue: paused is shown; resume is explicit with If-Match + key; 412 re-reads and does not repeat", async () => {
    const { job, v1 } = await ready();
    expect(job.store.get().queue.view?.dispatch).toBe("paused");
    v1.queue.revision = 5; // changed by someone else since our GET
    await job.resumeQueue();
    expect(job.store.get().control.phase).toBe("conflict");
    expect(job.store.get().queue.view?.revision).toBe(5);
    expect(v1.writes().filter((w) => w.line === "PATCH /api/v1/queue/control")).toHaveLength(1);
    await job.resumeQueue();
    const patches = v1.writes().filter((w) => w.line === "PATCH /api/v1/queue/control");
    expect(patches).toHaveLength(2);
    expect(patches[1]!.ifMatch).toBe('"5"');
    expect(JSON.parse(patches[1]!.body!)).toEqual({ schema_version: 1, dispatch: "running" });
    expect(patches[0]!.key).not.toBe(patches[1]!.key);
    expect(job.store.get().queue.view?.dispatch).toBe("running");
  });

  it("cancel is cooperative with If-Match; requested ≠ cancelled until GET says so", async () => {
    const { job, v1 } = await ready();
    await job.prepare();
    await job.submit();
    await vi.advanceTimersByTimeAsync(0);
    v1.jobs[0]!.status = "running";
    v1.jobs[0]!.revision = 2;
    await vi.advanceTimersByTimeAsync(2100);
    await job.cancel();
    const cancel = v1.writes().find((w) => w.line.endsWith("/cancel"))!;
    expect(cancel.ifMatch).toBe('"2"');
    expect(JSON.parse(cancel.body!)).toEqual({ schema_version: 1, mode: "cooperative" });
    expect(job.store.get()).toMatchObject({ cancel: { phase: "requested" }, job: { status: "cancelling" }, tracking: "active" });
    v1.setJobStatus("cancelled");
    await vi.advanceTimersByTimeAsync(2100);
    expect(job.store.get()).toMatchObject({ cancel: { phase: "done" }, job: { status: "cancelled" }, tracking: "final" });
  });

  it("late answers never move the job back to an older revision; dispose stops polling only", async () => {
    const { job, v1, core, lines } = await ready();
    await job.prepare();
    await job.submit();
    await vi.advanceTimersByTimeAsync(0);
    v1.setJobStatus("running");
    await vi.advanceTimersByTimeAsync(2100);
    const id = v1.jobs[0]!.id as string;
    v1.inject.set(`GET /api/v1/jobs/${id}`, { reply: json(200, { ...v1.jobs[0]!, revision: 1, status: "queued" }, { ETag: '"1"' }) });
    await vi.advanceTimersByTimeAsync(2100);
    expect(job.store.get().job).toMatchObject({ status: "running", revision: 2 });
    core.dispose();
    const after = lines.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(lines.length).toBe(after);
    expect(lines.filter((l) => l.endsWith("/cancel"))).toEqual([]);
  });

  it("per-file legacy overrides block the v1 start (not silently dropped)", async () => {
    const { job, core } = await ready();
    const row = core.inputs.primary()!;
    core.inputs.store.patch({ rows: core.inputs.store.get().rows.map((r) => (r.clientFileId === row.clientFileId ? { ...r, overrides: { language: "en" } } : r)) });
    expect(job.evaluate().issues).toContainEqual({ field: "row_overrides", problem: "unsupported" });
    expect(job.evaluate().session).toBeNull();
  });

  it("the server list re-displays durable jobs; following one is not claimed as this page's admission", async () => {
    const { job, v1 } = await ready();
    await job.prepare();
    await job.submit();
    job.dismiss();
    await job.loadRecent();
    expect(job.store.get().recent.items).toHaveLength(1);
    job.follow(v1.jobs[0]!.id as string);
    expect(job.store.get()).toMatchObject({ origin: "followed", tracking: "active" });
  });
});

describe("legacy mode keeps the legacy client only", () => {
  it("no v1 core and no /api/v1 request; v1 mode closes the legacy start without a request", async () => {
    const lines: string[] = [];
    const fetch: FetchLike = async (url, init) => {
      lines.push(`${init.method} ${new URL(url, "http://x").pathname}`);
      if (url.startsWith("/api/settings")) return json(200, { theme: "system", language: "ja", default_profile: null, default_output_dir: OUT });
      return json(503, { detail: "synthetic" });
    };
    const legacy = createCore({ fetch, host: createFakeHost() });
    await legacy.start();
    expect(legacy.apiMode).toBe("legacy");
    expect(legacy.v1).toBeNull();
    expect(lines.some((l) => l.includes("/api/v1/"))).toBe(false);
    const v1 = createCore({ fetch, host: createFakeHost(), apiMode: "v1-dev" });
    lines.length = 0;
    await expect(v1.jobsApi.startJob({ inputPath: IN, outputDir: OUT, profile: "cpu", asrBackend: "faster-whisper", asrModel: "small", asrDevice: "cpu", language: "ja", formats: ["srt"] })).rejects.toMatchObject({ code: "v1_mode_legacy_start_disabled" });
    expect(lines).toEqual([]);
    // An unknown mode word never becomes v1.
    expect(createCore({ fetch, host: createFakeHost(), apiMode: "V1-DEV" as never }).apiMode).toBe("legacy");
  });
});
