import { describe, expect, it } from "vitest";
import { DemoController, historyView, isActive, miniQueue, queueJobs, queueView, resultsView, SAMPLE_FOLDERS, skipReason, stepJob, submitBlock } from "./demo";

function done(demo: DemoController, id: string) {
  for (let i = 0; i < 9; i += 1) demo.step(id);
}

describe("#34 clean-up hides finished jobs from the queue only; results and history keep them", () => {
  it("clean up twice: terminal jobs leave the queue views, active jobs stay, nothing is deleted", () => {
    const demo = new DemoController();
    demo.setScenario("busy");
    const ids = demo.store.get().jobs.map((j) => j.id);
    done(demo, ids[0]!);
    demo.cancel(ids[1]!);
    const before = demo.store.get().jobs.map((j) => ({ id: j.id, snapshot: j.snapshot, outputs: j.outputs, state: j.state }));
    demo.selectJob(ids[0]!);
    demo.clearFinished();
    demo.clearFinished(); // twice: harmless
    const s = demo.store.get();
    expect(s.jobs.map((j) => ({ id: j.id, snapshot: j.snapshot, outputs: j.outputs, state: j.state }))).toEqual(before);
    expect(queueJobs(s).every(isActive)).toBe(true);
    expect(queueJobs(s).map((j) => j.id)).toEqual(s.jobs.filter(isActive).map((j) => j.id));
    expect(miniQueue(s, 10).every(isActive)).toBe(true);
    expect(queueView({ ...s, queueFilter: "finished" })).toEqual([]);
    expect(s.selectedJobId).toBeNull(); // the selected job was cleaned up
    expect(resultsView(s).map((j) => j.id)).toEqual([ids[0]]);
    expect(historyView(s).map((j) => j.state).sort()).toEqual(["cancelled", "failed", "succeeded"]);
    expect(historyView({ ...s, historyFilter: "failed" })).toHaveLength(1);
  });

  it("a retried job returns to the queue; mode / filter changes never lose history", () => {
    const demo = new DemoController();
    demo.setScenario("failed");
    const id = demo.store.get().jobs[0]!.id;
    demo.clearFinished();
    expect(queueJobs(demo.store.get())).toHaveLength(0);
    demo.setQueueFilter("attention");
    demo.setResultsTab("history");
    expect(historyView(demo.store.get()).map((j) => j.id)).toEqual([id]);
    demo.retry(id);
    expect(queueJobs(demo.store.get()).map((j) => j.id)).toEqual([id]);
  });
});

describe("#33 one input, several jobs with different settings", () => {
  it("submit, change the model, add again: two ids, two independent snapshots, no overwritten outputs; both stay in history", () => {
    const demo = new DemoController();
    demo.submit(["in-01"]);
    expect(submitBlock({ ...demo.store.get(), selected: ["in-01"] })).toBe("allSubmitted"); // plain submit: once
    const input = demo.store.get().inputs[0]!;
    expect(skipReason(demo.store.get(), input, true)).toBeNull(); // "add again" is allowed while it waits
    demo.setNormalSetting("in-01", { backend: "faster-whisper", model: "large-v3" });
    expect(demo.submitAgain(["in-01"])).toBe(1);
    const [a, b] = demo.store.get().jobs;
    expect(a!.id).not.toBe(b!.id);
    expect(a!.snapshot.model).toBe("demo-model-balanced");
    expect(b!.snapshot.model).toBe("large-v3");
    expect([a!.runNo, b!.runNo]).toEqual([1, 2]);
    expect(a!.outputs).toEqual(["sample-meeting-01.srt", "sample-meeting-01.json"]);
    expect(b!.outputs).toEqual([`sample-meeting-01_${b!.id}.srt`, `sample-meeting-01_${b!.id}.json`]);
    expect(demo.store.get().lastSubmit).toMatchObject({ kind: "again", created: 1, renamed: 1 });
    // A later settings change does not touch either snapshot.
    demo.setNormalSetting("in-01", { language: "en" });
    expect(demo.store.get().jobs.map((j) => j.snapshot.language)).toEqual(["ja", "ja"]);
    demo.store.set((s) => ({ ...s, jobs: s.jobs.map((j) => { let x = j; for (let i = 0; i < 9; i += 1) x = stepJob(x); return x; }) }));
    demo.clearFinished();
    expect(historyView(demo.store.get())).toHaveLength(2);
  });

  it("retry keeps the snapshot; rerun makes a new job with the current settings; unsupported / invalid still refused", () => {
    const demo = new DemoController();
    demo.setScenario("failed");
    const job = demo.store.get().jobs[0]!;
    demo.setNormalSetting("in-01", { language: "en" });
    demo.retry(job.id);
    expect(demo.store.get().jobs).toHaveLength(1);
    expect(demo.store.get().jobs[0]!.snapshot.language).toBe("ja");
    demo.fail(job.id);
    demo.step(job.id);
    demo.step(job.id);
    demo.fail(job.id);
    expect(demo.rerun(job.id)).toBe(1);
    expect(demo.store.get().jobs[1]!.snapshot.language).toBe("en");
    demo.setScenario("multi");
    expect(demo.submitAgain(["in-05"])).toBe(0); // unsupported format
    demo.setNormalSetting("in-01", { formats: [] });
    expect(demo.submitAgain(["in-01"])).toBe(0); // invalid settings
  });
});

describe("#35 default and per-file output folders, frozen at submission", () => {
  it("inherit / individual / back to default; a submitted job keeps its folder; same choice is a no-op", () => {
    const demo = new DemoController();
    demo.setScenario("multi");
    demo.setDefaultOutputDir(SAMPLE_FOLDERS[0]!);
    demo.setOutputDir(["in-02"], SAMPLE_FOLDERS[1]!);
    demo.submit(["in-01", "in-02"]);
    const [j1, j2] = demo.store.get().jobs;
    expect([j1!.outputDir, j2!.outputDir]).toEqual([SAMPLE_FOLDERS[0], SAMPLE_FOLDERS[1]]);
    demo.setDefaultOutputDir(null);
    demo.resetOutputDir(["in-02"]);
    expect(demo.store.get().jobs.map((j) => j.outputDir)).toEqual([SAMPLE_FOLDERS[0], SAMPLE_FOLDERS[1]]); // frozen
    demo.submitAgain(["in-02"]);
    expect(demo.store.get().jobs[2]!.outputDir).toBeNull(); // the input's own folder now
    expect(demo.store.get().jobs[2]!.outputs[0]).toBe("interview-sample-02.srt"); // other folder: no rename
    const seq = demo.store.get().notice!.seq;
    demo.setOutputDir(["in-01"], SAMPLE_FOLDERS[2]!);
    demo.setOutputDir(["in-01"], SAMPLE_FOLDERS[2]!);
    expect(demo.store.get().notice).toMatchObject({ key: "notice.outputUnchanged" });
    expect(demo.store.get().notice!.seq).toBeGreaterThan(seq);
    demo.remove(["in-01"]);
    expect(demo.store.get().outputDirs).toEqual({});
  });

  it("presets never carry an output folder (it is not a processing setting)", () => {
    const demo = new DemoController();
    demo.setDefaultOutputDir(SAMPLE_FOLDERS[0]!);
    const id = demo.createPresetFromCommon("x");
    const preset = demo.store.get().userPresets.find((p) => p.id === id)!;
    expect(JSON.stringify(preset)).not.toContain("Utteran-demo");
  });

  it("review scenarios: same file twice, history after clean-up, mixed folders, long glossary", () => {
    const demo = new DemoController();
    demo.setScenario("sameFile");
    expect(demo.store.get().jobs.map((j) => [j.inputId, j.state, j.snapshot.model])).toEqual([["in-01", "succeeded", "demo-model-balanced"], ["in-01", "waiting", "large-v3"]]);
    demo.setScenario("history");
    expect(queueJobs(demo.store.get()).every(isActive)).toBe(true);
    expect(historyView(demo.store.get()).length).toBe(3);
    demo.setScenario("outputs");
    expect(Object.keys(demo.store.get().outputDirs)).toHaveLength(2);
    demo.setScenario("glossary");
    expect(demo.store.get().overrides["in-01"]?.glossary).toHaveLength(120);
  });
});
