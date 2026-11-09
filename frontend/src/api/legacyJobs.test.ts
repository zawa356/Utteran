import { describe, expect, it } from "vitest";
import snapshots from "../test/fixtures/environment-snapshots.json";
import { hanging, json, scriptedFetch, text } from "../test/fakeFetch";
import { isApiError } from "./errors";
import { createHttpClient } from "./http";
import { jobOptions, parseEnvironment } from "./legacyEnvironment";
import {
  createLegacyJobsApi,
  fileName,
  jobEventsPath,
  parseJobEvent,
  parseJobSnapshot,
  parseQueue,
  type TranscriptionRequest,
  transcriptionBody,
} from "./legacyJobs";

export const JOB_ID = "0123456789abcdef0123456789abcdef";
// Synthetic paths assembled at runtime (never a real user path).
const ROOT = ["", "synthetic", "media"].join("/");

export function rawJob(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: JOB_ID,
    status: "running",
    created_at: "2026-11-09T05:00:00.000000+00:00",
    started_at: "2026-11-09T05:00:01.000000+00:00",
    finished_at: null,
    exit_code: null,
    stalled: false,
    outputs: [],
    events: [],
    logs: [`stderr line mentioning ${ROOT}/secret.wav`, "hf_" + "synthetictokenvalue"],
    guidance: null,
    ...patch,
  };
}

export const request: TranscriptionRequest = {
  inputPath: `${ROOT}/meeting.wav`,
  outputDir: `${ROOT}/out`,
  profile: "cuda",
  asrBackend: "faster-whisper",
  asrModel: "large-v3-turbo",
  asrDevice: "cuda:0",
  language: "ja",
  formats: ["srt", "json"],
};

describe("parseJobSnapshot (allowlisted DTO)", () => {
  it("keeps only status, times, counts, file names and a fixed guidance key", () => {
    const view = parseJobSnapshot(
      rawJob({
        status: "failed",
        exit_code: 4,
        outputs: [`${ROOT}/out/meeting.srt`, "C:\\synthetic\\out\\meeting.json"],
        guidance: { key: "input", settings_anchor: "" },
        events: [
          { id: 0, event: "stage_start", stage: "audio" },
          { id: 1, event: "progress", stage: "asr", ratio: 0.25, message: `${ROOT}/x` },
          { id: 2, event: "error", message: "boom", error_type: "X" },
        ],
      }),
      JOB_ID,
    );
    expect(view).toEqual({
      id: JOB_ID,
      status: "failed",
      createdAt: "2026-11-09T05:00:00.000000+00:00",
      startedAt: "2026-11-09T05:00:01.000000+00:00",
      finishedAt: null,
      exitCode: 4,
      stalled: false,
      outputCount: 2,
      outputNames: ["meeting.srt", "meeting.json"],
      guidance: "input",
      progress: { stage: "asr", ratio: 0.25 },
      lastEventId: 2,
    });
    const serialized = JSON.stringify(view);
    expect(serialized).not.toContain("synthetic/media");
    expect(serialized).not.toContain("hf_");
    expect(serialized).not.toContain("boom");
  });

  it("fails closed on a wrong id, an unknown status or broken lists", () => {
    expect(() => parseJobSnapshot(rawJob(), "f".repeat(32))).toThrow();
    expect(() => parseJobSnapshot(rawJob({ id: "../../api/v1" }))).toThrow();
    expect(() => parseJobSnapshot(rawJob({ status: "succeeded" }))).toThrow();
    expect(() => parseJobSnapshot(rawJob({ status: "queued" }))).toThrow();
    expect(() => parseJobSnapshot(rawJob({ outputs: "x" }))).toThrow();
    expect(() => parseJobSnapshot(rawJob({ events: null }))).toThrow();
    expect(() => parseJobSnapshot(null)).toThrow();
  });

  it("unknown guidance/stage values become 'other'; bad ratios and timestamps are dropped", () => {
    const view = parseJobSnapshot(
      rawJob({
        guidance: { key: "<img src=x onerror=alert(1)>" },
        created_at: "<b>now</b>",
        events: [{ id: 5, event: "progress", stage: "<script>", ratio: 7 }],
      }),
    );
    expect(view.guidance).toBe("other");
    expect(view.createdAt).toBeNull();
    expect(view.progress).toEqual({ stage: "other", ratio: null });
  });

  it("an HTML-looking output name stays plain text (rendered escaped by Vue)", () => {
    expect(parseJobSnapshot(rawJob({ outputs: [`${ROOT}/<img src=x onerror=alert(1)>.srt`] })).outputNames).toEqual(["<img src=x onerror=alert(1)>.srt"]);
    expect(fileName("C:\\a\\b\\c.txt")).toBe("c.txt");
    expect(fileName("/")).toBeNull();
  });

  it("stage_done reports the stage as finished, without inventing an overall percentage", () => {
    expect(parseJobSnapshot(rawJob({ events: [{ id: 0, event: "stage_done", stage: "asr" }] })).progress).toEqual({ stage: "asr", ratio: 1 });
    expect(parseJobSnapshot(rawJob()).progress).toEqual({ stage: null, ratio: null });
  });
});

describe("SSE events", () => {
  it("reads only allowlisted events with an index; anything else is ignored", () => {
    expect(parseJobEvent('{"id":3,"event":"progress","stage":"asr","ratio":0.5}')).toEqual({ id: 3, event: "progress", stage: "asr", ratio: 0.5 });
    expect(parseJobEvent('{"id":4,"event":"done","exit_code":0}')).toEqual({ id: 4, event: "done", stage: null, ratio: null });
    expect(parseJobEvent("not json")).toBeNull();
    expect(parseJobEvent('{"event":"progress"}')).toBeNull();
    expect(parseJobEvent('{"id":1,"event":"run_summary"}')).toBeNull();
    expect(parseJobEvent('{"id":-1,"event":"done"}')).toBeNull();
    expect(parseJobEvent(42)).toBeNull();
  });

  it("builds the events path only for a server job id", () => {
    expect(jobEventsPath(JOB_ID)).toBe(`/api/jobs/${JOB_ID}/events`);
    expect(() => jobEventsPath("../settings")).toThrow();
  });
});

describe("queue reference", () => {
  it("drops labels (file names) and keeps kind/status/position", () => {
    const items = parseQueue([
      { id: "a", kind: "transcription", label: "meeting.wav", status: "running", position: null },
      { id: "b", kind: "model_download", label: "x", status: "waiting", position: 1 },
      { id: "c", kind: "transcription", label: "y", status: "odd", position: -3 },
      "junk",
    ]);
    expect(items).toEqual([
      { id: "a", transcription: true, status: "running", position: null },
      { id: "b", transcription: false, status: "waiting", position: 1 },
      { id: "c", transcription: true, status: "unknown", position: null },
    ]);
    expect(JSON.stringify(items)).not.toContain("meeting.wav");
  });
});

describe("TranscriptionPayload body", () => {
  it("sends diarization off explicitly and keeps the legacy resume/recursive meaning", () => {
    expect(transcriptionBody(request)).toEqual({
      input_path: `${ROOT}/meeting.wav`,
      output_dir: `${ROOT}/out`,
      profile: "cuda",
      asr_backend: "faster-whisper",
      asr_model: "large-v3-turbo",
      asr_device: "cuda:0",
      diarization_enabled: false,
      language: "ja",
      formats: ["srt", "json"],
      resume_mode: "resume",
      recursive: false,
    });
  });
});

describe("createLegacyJobsApi", () => {
  const api = (fetch: ReturnType<typeof scriptedFetch>) => createLegacyJobsApi(createHttpClient({ fetch }));

  it("POSTs once to /api/jobs and parses the 202 snapshot", async () => {
    const fetch = scriptedFetch([json(202, rawJob({ status: "starting" }))]);
    const view = await api(fetch).startJob(request);
    expect(view.status).toBe("starting");
    expect(fetch.calls.map((c) => `${c.init.method} ${c.url}`)).toEqual(["POST /api/jobs"]);
    expect(JSON.parse(String(fetch.calls[0]!.init.body))).toMatchObject({ diarization_enabled: false });
  });

  it("classifies refusals and never retries; a lost answer is outcome-unknown", async () => {
    for (const [status, kind] of [
      [400, "validation"],
      [409, "conflict"],
      [422, "validation"],
      [503, "unavailable"],
    ] as const) {
      const fetch = scriptedFetch([json(status, { detail: `ASR model is unavailable ${ROOT}` })]);
      const error = await api(fetch).startJob(request).catch((e: unknown) => e);
      expect(isApiError(error) && error.kind).toBe(kind);
      expect(isApiError(error) && error.outcomeUnknown).toBe(false);
      expect(fetch.calls).toHaveLength(1);
    }
    const lost = scriptedFetch([new TypeError("connection reset")]);
    const error = await api(lost).startJob(request).catch((e: unknown) => e);
    expect(isApiError(error) && error.outcomeUnknown).toBe(true);
    expect(lost.calls).toHaveLength(1);
  });

  it("bad JSON in a 202 is malformed (not success)", async () => {
    const error = await api(scriptedFetch([text(202, "<html>")])).startJob(request).catch((e: unknown) => e);
    expect(isApiError(error) && error.kind).toBe("malformed");
  });

  it("GET/cancel use the validated id; GET can be aborted", async () => {
    const fetch = scriptedFetch([json(200, rawJob()), json(202, rawJob())]);
    const jobs = api(fetch);
    await jobs.getJob(JOB_ID);
    await jobs.cancelJob(JOB_ID);
    expect(fetch.calls.map((c) => `${c.init.method} ${c.url}`)).toEqual([`GET /api/jobs/${JOB_ID}`, `POST /api/jobs/${JOB_ID}/cancel`]);
    await expect(jobs.getJob("bad")).rejects.toThrow();
    const controller = new AbortController();
    const hung = scriptedFetch(() => () => hanging(hung.calls[hung.calls.length - 1]!.init));
    const pending = api(hung).getJob(JOB_ID, controller.signal);
    controller.abort();
    const aborted = await pending.catch((e: unknown) => e);
    expect(isApiError(aborted) && aborted.kind).toBe("aborted");
  });
});

describe("submission projection of /api/environment options", () => {
  const fixture = snapshots as unknown as Record<string, Record<string, unknown>>;

  it("real-service cuda answer: exact ids for backend/model/device, languages and formats", () => {
    const env = parseEnvironment(JSON.parse(JSON.stringify(fixture.cuda)));
    expect(env.jobOptions.asr.map((b) => [b.id, b.models.map((m) => m.id), b.devices.map((d) => d.id)])).toEqual([
      ["faster-whisper", ["large-v3-turbo", "small"], ["cpu", "cuda:0"]],
    ]);
    expect(env.jobOptions.languages).toEqual(["auto", "ja", "en"]);
    expect(env.jobOptions.formats).toEqual(["srt", "vtt", "json", "txt", "md"]);
    expect(env.jobOptions.incomplete).toBe(false);
  });

  it("keeps every model the display list would cap (33 whisper.cpp models) and ids with '/'", () => {
    const models = Array.from({ length: 33 }, (_, i) => ({ id: `ggml-model-${i}`, label: `m${i}` }));
    const options = jobOptions({
      asr: [
        { id: "whisper-cpp", label: "whisper.cpp", models, devices: [{ id: "cpu", label: "CPU" }] },
        { id: "custom", label: "c", models: [{ id: "org/model-name", label: "x" }], devices: [{ id: "cpu" }] },
      ],
      languages: ["auto", "ja", "en", "zz"],
      formats: ["srt", "docx"],
    });
    expect(options.asr[0]!.models).toHaveLength(33);
    expect(options.asr[1]!.models[0]!.id).toBe("org/model-name");
    expect(options.languages).toEqual(["auto", "ja", "en"]);
    expect(options.formats).toEqual(["srt"]);
  });

  it("drops unsafe ids (never rewritten) and marks the projection incomplete; empty backends vanish", () => {
    const options = jobOptions({
      asr: [
        { id: "a", models: [{ id: " padded" }, { id: "ok\u0007" }, { id: "good", label: "<b>x</b>" }], devices: [{ id: "cpu" }] },
        { id: "b", models: [], devices: [{ id: "cpu" }] },
        { id: "x".repeat(101), models: [{ id: "m" }], devices: [{ id: "cpu" }] },
      ],
    });
    expect(options.asr.map((b) => b.id)).toEqual(["a"]);
    expect(options.asr[0]!.models).toEqual([{ id: "good", label: "<b>x</b>" }]);
    expect(options.incomplete).toBe(true);
    expect(options.languages).toEqual([]);
  });
});
