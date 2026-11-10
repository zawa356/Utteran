import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeHost } from "../host/fake";
import snapshots from "../test/fixtures/environment-snapshots.json";
import { json, scriptedFetch } from "../test/fakeFetch";
import { createCore } from "./core";

const fixture = snapshots as unknown as Record<string, Record<string, unknown>>;
const ID = "c".repeat(32);
const OUT = ["", "synthetic", "out"].join("/");
const IN = ["", "synthetic", "media", "talk.wav"].join("/");

function server() {
  let status = "running";
  const fetch = scriptedFetch((call) => {
    const url = new URL(call.url, "http://x");
    if (url.pathname === "/api/settings") return json(200, { theme: "system", language: "ja", default_profile: "cuda", default_output_dir: OUT });
    if (url.pathname === "/api/environment") return json(200, JSON.parse(JSON.stringify(fixture.cuda)));
    const job = () => json(call.init.method === "POST" ? 202 : 200, { id: ID, status, outputs: [], events: [], logs: [], guidance: null });
    if (url.pathname === "/api/jobs" && call.init.method === "POST") return job();
    if (url.pathname === `/api/jobs/${ID}`) return job();
    throw new Error(`unexpected ${call.init.method} ${call.url}`);
  });
  return { fetch, finish: () => (status = "completed") };
}

describe("S3-A in AppCore", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("one form/job for Normal and Expert: mode and page switches keep the draft and the tracking", async () => {
    const { fetch, finish } = server();
    const host = createFakeHost();
    const core = createCore({ fetch, host });
    await core.start();
    const form = core.transcription;
    expect(form.store.get().draft).toMatchObject({ outputDir: OUT, outputSource: "saved_default", asrBackend: "faster-whisper", asrModel: null });
    host.nextPick = IN;
    await form.pick("input_file", host);
    form.choose("asrModel", "small");
    form.choose("asrDevice", "cpu");
    const request = form.evaluation().request;
    expect(request).toMatchObject({ profile: "cuda", asrModel: "small", asrDevice: "cpu", outputDir: OUT, inputPath: IN });
    await core.job.submit(request!);
    await vi.advanceTimersByTimeAsync(0);
    const before = { form: form.store.get(), job: core.job.store.get() };

    core.shell.setMode("expert");
    core.shell.navigate("settings");
    core.shell.navigate("transcribe");
    core.shell.setMode("normal");
    expect(core.transcription).toBe(form);
    expect(form.store.get()).toBe(before.form);
    expect(core.job.store.get().tracking).toBe("active");

    finish();
    await vi.advanceTimersByTimeAsync(5000);
    expect(core.job.store.get().job?.status).toBe("completed");
    const writes = fetch.calls.filter((c) => c.init.method !== "GET").map((c) => `${c.init.method} ${c.url}`);
    expect(writes).toEqual(["POST /api/jobs"]); // no settings PATCH, nothing resent
    expect(fetch.calls.some((c) => c.url.startsWith("/api/v1"))).toBe(false);
    // Re-detection is never started by the form (only the start-up read with refresh=false).
    expect(fetch.calls.filter((c) => c.url.startsWith("/api/environment")).map((c) => c.url)).toEqual(["/api/environment?profile=cuda"]);
    core.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });
});
