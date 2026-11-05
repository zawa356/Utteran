import { describe, expect, it } from "vitest";
import {
  advanceProgress,
  DemoController,
  effectiveSettings,
  jobOf,
  miniQueue,
  normalInputId,
  outputNames,
  queueView,
  resultsView,
  selectionField,
  stageEta,
  stagePosition,
  stepJob,
  submitBlock,
} from "./demo";
import { formatDuration } from "./fixtures";
import { allPresets, overrideCount, skipReason } from "./demo";
import { engineCandidates } from "./settingsSchema";
import { jobLog } from "./diagnostics";

/** The several-files state the Expert views start from (scenario "multi"). */
function multi(): DemoController {
  const demo = new DemoController();
  demo.setScenario("multi");
  return demo;
}

describe("UI-3-0 prototype state (synthetic, in-memory)", () => {
  it("starts deterministic with ONE synthetic file (Normal shows one file), no jobs, no connection", () => {
    const s = new DemoController().store.get();
    expect(s.inputs.map((i) => i.name)).toEqual(["sample-meeting-01.wav"]);
    expect(normalInputId(s)).toBe("in-01");
    expect(s.jobs).toEqual([]);
    expect(s.sync).toBe("ok");
    expect(s.envExample).toBe("gpu");
    expect(s.accentTheme).toBe("green");
    expect(s.appearance).toBe("light");
    expect(s.overrides).toEqual({});
  });

  it("the multi scenario has five inputs (one unsupported) and one override", () => {
    const s = multi().store.get();
    expect(s.inputs).toHaveLength(5);
    expect(s.inputs.find((i) => i.format === "txt")?.supported).toBe(false);
    expect(Object.keys(s.overrides)).toEqual(["in-02"]);
  });

  it("effective settings = common + override; editing common changes only inherited fields", () => {
    const demo = multi();
    demo.setCommon("language", "auto");
    const s = demo.store.get();
    expect(effectiveSettings(s, "in-01").language).toBe("auto");
    expect(effectiveSettings(s, "in-02").language).toBe("en");
  });

  it("override on/off for a multi-selection, mixed values and coverage", () => {
    const demo = multi();
    demo.selectAll();
    expect(selectionField(demo.store.get(), "language")).toMatchObject({ mixed: true, coverage: "some" });
    // Iteration 4: switching "individual" on keeps EACH file's own value (never the first file's).
    demo.startOverride("language");
    expect(selectionField(demo.store.get(), "language")).toMatchObject({ mixed: true, coverage: "all" });
    expect(effectiveSettings(demo.store.get(), "in-02").language).toBe("en");
    demo.setOverride("language", "ja"); // an explicit edit sets them all
    expect(selectionField(demo.store.get(), "language")).toMatchObject({ mixed: false, coverage: "all", value: "ja" });
    demo.resetOverride("language");
    expect(selectionField(demo.store.get(), "language").coverage).toBe("none");
    expect(demo.store.get().overrides["in-02"]).toEqual({ speakerMode: "fixed", numSpeakers: 2, minSpeakers: 1, maxSpeakers: 4 });
    demo.resetAllOverrides();
    expect(demo.store.get().overrides).toEqual({});
  });

  it("submission freezes a snapshot, skips unsupported and already-active inputs", () => {
    const demo = multi();
    expect(demo.submit(["in-01", "in-05"])).toBe(1);
    expect(demo.submit(["in-01"])).toBe(0);
    demo.selectAll();
    demo.setOverride("formats", ["md"]);
    const job = demo.store.get().jobs[0]!;
    expect(job.snapshot.formats).toEqual(["srt", "json"]);
    expect(job.state).toBe("waiting");
  });

  it("a finished, failed or cancelled input is never submitted twice (retry resumes it instead)", () => {
    const demo = multi();
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
    const demo = multi();
    demo.submit(["in-01", "in-02", "in-03"]);
    demo.togglePause();
    demo.stepAll();
    expect(demo.store.get().jobs.every((j) => j.state === "waiting")).toBe(true);
    expect(demo.store.get().notice?.key).toBe("notice.paused");
    const third = demo.store.get().jobs[2]!.id;
    demo.move(third, -1);
    expect(demo.store.get().jobs[1]!.id).toBe(third);
  });

  it("scenarios: single-file states, empty, error (recoverable), busy; reset keeps reviewer choices", () => {
    const demo = new DemoController();
    demo.setLanguage("en");
    demo.setAppearance("dark");
    demo.setAccentTheme("blue");
    demo.setEnvExample("cpu");
    demo.setScenario("empty");
    expect(demo.store.get().inputs).toEqual([]);
    expect(normalInputId(demo.store.get())).toBeNull();
    demo.setScenario("error");
    expect(demo.store.get().inputsArea).toBe("error");
    demo.recoverArea("inputs");
    expect(demo.store.get().inputsArea).toBe("ready");
    demo.setScenario("running");
    expect(demo.store.get().jobs.map((j) => `${j.state}:${j.stage}`)).toEqual(["running:diarization"]);
    demo.setScenario("failed");
    expect(demo.store.get().jobs.map((j) => j.state)).toEqual(["failed"]);
    demo.setScenario("done");
    expect(demo.store.get().jobs.map((j) => j.state)).toEqual(["succeeded"]);
    demo.setScenario("busy");
    expect(new Set(demo.store.get().jobs.map((j) => j.state))).toEqual(new Set(["running", "preparing", "failed", "waiting"]));
    demo.reset();
    expect(demo.store.get()).toMatchObject({ scenario: "single", language: "en", appearance: "dark", accentTheme: "blue", envExample: "cpu", jobs: [] });
  });

  it("Normal shows one representative and never changes the other inputs", () => {
    const demo = multi();
    const before = demo.store.get().inputs.map((i) => i.id);
    expect(normalInputId(demo.store.get())).toBe("in-01");
    demo.toggleSelect("in-03"); // touched in Expert → becomes the representative
    expect(normalInputId(demo.store.get())).toBe("in-03");
    // Choosing a file with several inputs adds one; nothing is removed or submitted.
    demo.chooseNormalFile();
    const s = demo.store.get();
    expect(s.inputs.map((i) => i.id)).toEqual([...before, "add-01"]);
    expect(normalInputId(s)).toBe("add-01");
    expect(s.jobs).toEqual([]);
    expect(Object.keys(s.overrides)).toEqual(["in-02"]);
  });

  it("Normal 'choose another file' replaces the only, unsubmitted file; a submitted one is kept", () => {
    const demo = new DemoController();
    demo.chooseNormalFile();
    expect(demo.store.get().inputs.map((i) => i.id)).toEqual(["add-01"]);
    demo.submit(["add-01"]);
    demo.chooseNormalFile();
    expect(demo.store.get().inputs.map((i) => i.id)).toEqual(["add-01", "add-02"]);
    expect(jobOf(demo.store.get(), "add-01")?.state).toBe("waiting");
  });

  it("queue page view: search, filter and sort keep the queue position", () => {
    const demo = multi();
    demo.setScenario("busy");
    demo.setQueueFilter("attention");
    expect(queueView(demo.store.get()).map((r) => r.job.inputId)).toEqual(["in-03"]);
    demo.setQueueFilter("all");
    demo.setQueueSort("state");
    expect(queueView(demo.store.get())[0]!.job.state).toBe("failed");
    demo.setQueueSort("order");
    demo.setQueueQuery("LECTURE");
    expect(queueView(demo.store.get()).map((r) => r.position)).toEqual([4]);
  });

  it("results view lists succeeded jobs only, with example output names", () => {
    const demo = new DemoController();
    demo.setScenario("done");
    const [job] = resultsView(demo.store.get());
    expect(job && outputNames(job)).toEqual(["sample-meeting-01.srt", "sample-meeting-01.json"]);
    demo.setResultsQuery("nothing-matches");
    expect(resultsView(demo.store.get())).toEqual([]);
  });

  it("environment re-detection is a display state only, ended by an explicit demo control", () => {
    const demo = new DemoController();
    demo.redetect();
    expect(demo.store.get().envChecking).toBe(true);
    demo.finishDetect();
    expect(demo.store.get()).toMatchObject({ envChecking: false, envExample: "gpu" });
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

  it("Normal 'choose another file' carries the shown file's settings; a submitted file is kept", () => {
    const demo = new DemoController();
    demo.selectAll();
    demo.setOverride("language", "en"); // the only input, not submitted
    demo.chooseNormalFile();
    let s = demo.store.get();
    expect(s.inputs.map((i) => i.id)).toEqual(["add-01"]); // replaced: nothing is lost …
    expect(s.overrides["add-01"]).toEqual({ language: "en" }); // … because its settings moved along
    expect(s.overrides["in-01"]).toBeUndefined();
    expect(s.selected).toEqual(["add-01"]);
    demo.submit(["add-01"]);
    demo.chooseNormalFile();
    s = demo.store.get();
    expect(s.inputs.map((i) => i.id)).toEqual(["add-01", "add-02"]); // the submitted one stays
    expect(effectiveSettings(s, "add-02").language).toBe("en");
    expect(s.jobs[0]!.snapshot.language).toBe("en");
  });

  it("Normal 'choose' keeps an explicit Expert multi-selection", () => {
    const demo = multi();
    demo.selectAll();
    demo.chooseNormalFile();
    const s = demo.store.get();
    expect(s.selected).toEqual(["in-01", "in-02", "in-03", "in-04", "in-05"]);
    expect(normalInputId(s)).toBe("add-01");
  });

  it("Normal settings edit ONLY the shown file — never the common defaults other files inherit", () => {
    const demo = multi();
    demo.selectOnly("in-02"); // in-02 overrides language=en and a fixed speaker count
    demo.setNormalSetting("in-02", { language: "auto" });
    demo.setNormalSetting("in-02", { diarization: false }); // not overridden before: still only this file
    const s = demo.store.get();
    expect(s.common).toMatchObject({ language: "ja", diarization: true });
    expect(effectiveSettings(s, "in-02")).toMatchObject({ language: "auto", diarization: false, speakerMode: "fixed" });
    for (const id of ["in-01", "in-03", "in-04"]) expect(effectiveSettings(s, id)).toMatchObject({ language: "ja", diarization: true });
    // With no file at all there is nothing else to affect: the common default changes.
    const empty = new DemoController();
    empty.setScenario("empty");
    empty.setNormalSetting(null, { language: "en" });
    expect(empty.store.get().common.language).toBe("en");
  });

  it("submit feedback: created / skipped counts, cleared when the selection changes", () => {
    const demo = multi();
    demo.selectAll();
    expect(submitBlock(demo.store.get())).toBeNull();
    demo.submit(demo.store.get().selected);
    expect(demo.store.get().lastSubmit).toMatchObject({ created: 4, unsupported: 1, alreadySubmitted: 0 });
    expect(submitBlock(demo.store.get())).toBe("allSubmitted");
    demo.submit(["in-01"]);
    expect(demo.store.get().lastSubmit).toMatchObject({ created: 0, alreadySubmitted: 1 });
    demo.selectOnly("in-05");
    expect(demo.store.get().lastSubmit).toBeNull();
    expect(submitBlock(demo.store.get())).toBe("unsupported");
    demo.clearSelection();
    expect(submitBlock(demo.store.get())).toBe("noSelection");
  });

  it("the short queue shows the just-submitted jobs first, at most five rows", () => {
    const demo = multi();
    demo.setScenario("busy"); // 4 jobs: running, preparing, failed, waiting
    demo.addFiles(3);
    demo.submit(["add-01", "add-02", "add-03"]);
    const rows = miniQueue(demo.store.get());
    expect(rows).toHaveLength(5);
    expect(rows.slice(0, 3).map((j) => j.inputId)).toEqual(["add-01", "add-02", "add-03"]);
    expect(rows[3]!.state).toBe("running");
    demo.selectOnly("in-01"); // feedback cleared → priority by state
    expect(miniQueue(demo.store.get(), 3).map((j) => j.state)).toEqual(["running", "preparing", "failed"]);
  });

  it("progress mock: per-stage fraction, indeterminate output stage, ETA only from 10 %", () => {
    const demo = new DemoController();
    demo.submit(["in-01"]);
    let job = stepJob(stepJob(demo.store.get().jobs[0]!)); // waiting → preparing → running:asr
    expect(job.progress.fraction).toBe(0);
    expect(stageEta(job)).toEqual({ kind: "estimating" });
    expect(stagePosition(job)).toEqual({ index: 1, total: 3 });
    job = advanceProgress(advanceProgress(job)); // 50 % after 90 s in this stage
    expect(job.progress.fraction).toBe(0.5);
    expect(stageEta(job)).toEqual({ kind: "estimate", remainingSec: 90 });
    job = stepJob(stepJob(job)); // → diarization → output
    expect(job.stage).toBe("output");
    expect(job.progress.fraction).toBeNull();
    expect(stageEta(job)).toEqual({ kind: "none" });
    expect(advanceProgress(job)).toBe(job);
    expect(job.progress.elapsedSec).toBeGreaterThan(90); // elapsed keeps counting over the stages
    const noDiar = { ...job, snapshot: { ...job.snapshot, diarization: false } };
    expect(stagePosition(noDiar)).toEqual({ index: 2, total: 2 });
  });

  it("the running scenario starts halfway through its stage; retry clears the progress", () => {
    const demo = new DemoController();
    demo.setScenario("running");
    const job = demo.store.get().jobs[0]!;
    expect(job.progress.fraction).toBe(0.5);
    demo.fail(job.id);
    demo.retry(job.id);
    expect(demo.store.get().jobs[0]!.progress).toEqual({ fraction: null, stageElapsedSec: 0, elapsedSec: 0 });
  });

  it("job-status examples never change job states (a slow or failed fetch is not a stopped job)", () => {
    const demo = new DemoController();
    demo.setScenario("busy");
    const before = demo.store.get().jobs.map((j) => j.state);
    demo.setSync("unavailable");
    expect(demo.store.get().jobs.map((j) => j.state)).toEqual(before);
    demo.retrySync();
    expect(demo.store.get().sync).toBe("ok");
  });

  it("formats synthetic durations", () => {
    expect(formatDuration(754)).toBe("12:34");
    expect(formatDuration(3605)).toBe("1:00:05");
    expect(formatDuration(null)).toBe("—");
  });

  // ---- iteration 4 -----------------------------------------------------------------------------
  it("engine group: a per-file backend change keeps that file's model valid and never touches the others", () => {
    const demo = multi();
    demo.selectOnly("in-03");
    demo.setOverride("backend", "whisper-cpp");
    let s = demo.store.get();
    expect(engineCandidates("whisper-cpp").models).toContain(effectiveSettings(s, "in-03").model);
    expect(Object.keys(s.overrides["in-03"]!).sort()).toEqual(["backend", "computeType", "device", "model"]);
    expect(overrideCount(s, "in-03")).toBe(1); // one linked item
    demo.setCommon("backend", "faster-whisper"); // the common change does not reach the overridden file
    s = demo.store.get();
    expect(effectiveSettings(s, "in-03").backend).toBe("whisper-cpp");
    expect(engineCandidates("faster-whisper").models).toContain(s.common.model);
    expect(effectiveSettings(s, "in-01").backend).toBe("faster-whisper");
  });

  it("categories never lose input: values live in the store; the chosen category is kept", () => {
    const demo = multi();
    demo.setCommon("initialPrompt", "定例会議");
    demo.setCommon("glossary", ["Utteran"]);
    demo.setInspectorCategory("advanced");
    demo.setCommon("backend", "whisper-cpp");
    demo.setCommon("wcThreads", 4);
    demo.setInspectorCategory("prompt");
    expect(demo.store.get()).toMatchObject({ inspectorCategory: "prompt", common: { initialPrompt: "定例会議", glossary: ["Utteran"], wcThreads: 4 } });
  });

  it("submit skips files whose settings are invalid or that need diarization that is not set up", () => {
    const demo = multi();
    demo.selectOnly("in-01");
    demo.setOverride("beamSize", 0);
    demo.setEnvironment("gpu", false);
    demo.selectOnly("in-04");
    demo.setOverride("diarization", false);
    const s = demo.store.get();
    expect(skipReason(s, s.inputs[0]!)).toBe("invalid");
    expect(skipReason(s, s.inputs[1]!)).toBe("diarization"); // in-02 inherits diarization on
    expect(skipReason(s, s.inputs[3]!)).toBeNull();
    demo.selectAll();
    expect(submitBlock(demo.store.get())).toBeNull();
    demo.submit(demo.store.get().selected);
    expect(demo.store.get().lastSubmit).toMatchObject({ created: 1, invalid: 1, diarization: 2, unsupported: 1 });
    demo.selectOnly("in-01");
    expect(submitBlock(demo.store.get())).toBe("invalid");
  });

  it("the snapshot is frozen at submit: later prompt / glossary / engine edits do not change it, and logs carry no prompt", () => {
    const demo = multi();
    demo.setCommon("initialPrompt", "社外秘の議題");
    demo.setCommon("glossary", ["機密語"]);
    demo.submit(["in-01"]);
    demo.setCommon("initialPrompt", "別の文章");
    demo.setCommon("glossary", []);
    demo.setCommon("backend", "whisper-cpp");
    const job = demo.store.get().jobs[0]!;
    expect(job.snapshot).toMatchObject({ initialPrompt: "社外秘の議題", glossary: ["機密語"], backend: "auto" });
    expect(JSON.stringify(jobLog(job))).not.toMatch(/社外秘|機密語/);
  });

  it("presets: apply to common or as individual settings of the selection; built-ins are read-only", () => {
    const demo = multi();
    demo.applyPreset("builtin-subtitles", "common");
    expect(demo.store.get().common).toMatchObject({ diarization: false, formats: ["srt", "vtt"], wordTimestamps: "always" });
    expect(demo.store.get().overrides["in-01"]).toBeUndefined();
    demo.selectOnly("in-04");
    demo.applyPreset("user-01", "selection");
    let s = demo.store.get();
    expect(effectiveSettings(s, "in-04")).toMatchObject({ speakerMode: "range", minSpeakers: 2, maxSpeakers: 6, glossary: ["Utteran", "議事録", "アクションアイテム"] });
    expect(effectiveSettings(s, "in-01").glossary).toEqual([]);
    const copy = demo.duplicatePreset("builtin-standard", "標準のコピー")!;
    demo.renamePreset(copy, "社内用");
    demo.renamePreset("builtin-standard", "壊す"); // ignored
    demo.deletePreset("builtin-standard"); // ignored
    const created = demo.createPresetFromCommon("今の既定値");
    s = demo.store.get();
    expect(allPresets(s).map((p) => p.name || p.nameKey)).toEqual(["preset.builtin.standard", "preset.builtin.meeting", "preset.builtin.subtitles", "定例会議（用語集つき）", "社内用", "今の既定値"]);
    expect("device" in allPresets(s).find((p) => p.id === created)!.values).toBe(false);
    expect("initialPrompt" in allPresets(s).find((p) => p.id === created)!.values).toBe(false);
    demo.deletePreset(copy);
    expect(allPresets(demo.store.get()).some((p) => p.id === copy)).toBe(false);
    const imported = demo.importPreset("定例会議（用語集つき）", { language: "en" });
    expect(allPresets(demo.store.get()).find((p) => p.id === imported)!.name).toBe("定例会議（用語集つき） (2)");
  });
});
