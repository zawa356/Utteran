import { describe, expect, it } from "vitest";
import { DemoController, effectiveSettings, selectionField, stepJob } from "./demo";
import { formatDuration } from "./fixtures";

describe("UI-3-0 prototype state (synthetic, in-memory)", () => {
  it("starts deterministic: synthetic inputs, one override, no jobs, no connection", () => {
    const s = new DemoController().store.get();
    expect(s.inputs.map((i) => i.name)).toContain("sample-meeting-01.wav");
    expect(s.inputs.find((i) => i.format === "txt")?.supported).toBe(false);
    expect(s.jobs).toEqual([]);
    expect(s.sync).toBe("none");
    expect(Object.keys(s.overrides)).toEqual(["in-02"]);
  });

  it("effective settings = common + override; editing common changes only inherited fields", () => {
    const demo = new DemoController();
    demo.setCommon("language", "auto");
    const s = demo.store.get();
    expect(effectiveSettings(s, "in-01").language).toBe("auto");
    expect(effectiveSettings(s, "in-02").language).toBe("en");
  });

  it("override on/off for a multi-selection, mixed values and coverage", () => {
    const demo = new DemoController();
    demo.selectAll();
    expect(selectionField(demo.store.get(), "language")).toMatchObject({ mixed: true, coverage: "some" });
    demo.startOverride("language"); // copies the first effective value to every selected input
    expect(selectionField(demo.store.get(), "language")).toMatchObject({ mixed: false, coverage: "all", value: "ja" });
    demo.resetOverride("language");
    expect(selectionField(demo.store.get(), "language").coverage).toBe("none");
    expect(demo.store.get().overrides["in-02"]).toEqual({ speakers: "2" });
    demo.resetAllOverrides();
    expect(demo.store.get().overrides).toEqual({});
  });

  it("submission freezes a snapshot, skips unsupported and already-active inputs", () => {
    const demo = new DemoController();
    expect(demo.submit(["in-01", "in-05"])).toBe(1);
    expect(demo.submit(["in-01"])).toBe(0);
    demo.selectAll();
    demo.setOverride("formats", ["md"]);
    const job = demo.store.get().jobs[0]!;
    expect(job.snapshot.formats).toEqual(["srt", "json"]);
    expect(job.state).toBe("waiting");
  });

  it("a finished, failed or cancelled input is never submitted twice (retry resumes it instead)", () => {
    const demo = new DemoController();
    demo.submit(["in-01", "in-02"]);
    const [a, b] = demo.store.get().jobs;
    for (let i = 0; i < 5; i += 1) demo.step(a!.id);
    demo.cancel(b!.id);
    expect(demo.submit(["in-01", "in-02"])).toBe(0);
    expect(demo.store.get().notice?.key).toBe("notice.nothingToSubmit");
    expect(demo.store.get().jobs).toHaveLength(2);
  });

  it("steps only on explicit demo actions; stages follow the snapshot", () => {
    const demo = new DemoController();
    demo.submit(["in-01"]);
    let job = demo.store.get().jobs[0]!;
    const seen: string[] = [];
    for (let i = 0; i < 6; i += 1) {
      seen.push(`${job.state}:${job.stage ?? "-"}`);
      job = stepJob(job);
    }
    expect(seen).toEqual(["waiting:-", "preparing:-", "running:asr", "running:diarization", "running:output", "succeeded:-"]);
    expect(stepJob({ ...job, snapshot: { ...job.snapshot, diarization: false }, state: "running", stage: "asr" }).stage).toBe("output");
  });

  it("fail → retry keeps the snapshot and counts the attempt; cancel only while active", () => {
    const demo = new DemoController();
    demo.submit(["in-01"]);
    const id = demo.store.get().jobs[0]!.id;
    demo.fail(id); // waiting cannot fail
    expect(demo.store.get().jobs[0]!.state).toBe("waiting");
    demo.step(id);
    demo.fail(id);
    expect(demo.store.get().jobs[0]).toMatchObject({ state: "failed", errorCode: "demo_failure" });
    demo.retry(id);
    expect(demo.store.get().jobs[0]).toMatchObject({ state: "waiting", attempt: 2, errorCode: null });
    demo.cancel(id);
    expect(demo.store.get().jobs[0]!.state).toBe("cancelled");
  });

  it("pause blocks 'step all'; reorder moves only waiting jobs", () => {
    const demo = new DemoController();
    demo.submit(["in-01", "in-02", "in-03"]);
    demo.togglePause();
    demo.stepAll();
    expect(demo.store.get().jobs.every((j) => j.state === "waiting")).toBe(true);
    expect(demo.store.get().notice?.key).toBe("notice.paused");
    const third = demo.store.get().jobs[2]!.id;
    demo.move(third, -1);
    expect(demo.store.get().jobs[1]!.id).toBe(third);
  });

  it("scenarios: empty, error (recoverable), busy, success; reset keeps language/theme", () => {
    const demo = new DemoController();
    demo.setLanguage("en");
    demo.setTheme("dark");
    demo.setScenario("empty");
    expect(demo.store.get().inputs).toEqual([]);
    demo.setScenario("error");
    expect(demo.store.get().inputsArea).toBe("error");
    demo.recoverArea("inputs");
    expect(demo.store.get().inputsArea).toBe("ready");
    demo.setScenario("busy");
    expect(new Set(demo.store.get().jobs.map((j) => j.state))).toEqual(new Set(["running", "preparing", "failed", "waiting"]));
    demo.setScenario("success");
    expect(demo.store.get().jobs.every((j) => j.state === "succeeded")).toBe(true);
    demo.reset();
    expect(demo.store.get()).toMatchObject({ scenario: "default", language: "en", theme: "dark", jobs: [] });
  });

  it("every action leaves visible feedback (a new notice)", () => {
    const demo = new DemoController();
    const seqs: number[] = [];
    const record = () => seqs.push(demo.store.get().notice?.seq ?? 0);
    demo.addFiles(1);
    record();
    demo.submit(["in-01"]);
    record();
    demo.submit(["in-01"]);
    record();
    demo.removeSelected();
    record();
    expect(new Set(seqs).size).toBe(seqs.length);
  });

  it("formats synthetic durations", () => {
    expect(formatDuration(754)).toBe("12:34");
    expect(formatDuration(3605)).toBe("1:00:05");
    expect(formatDuration(null)).toBe("—");
  });
});
