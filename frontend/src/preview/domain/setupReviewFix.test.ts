import { describe, expect, it } from "vitest";
import { DemoController } from "./demo";
import { envFromExample } from "./envExamples";
import { DIARIZATION_TERMS, planFor, runCompleted, SetupController, type SetupExample } from "./setup";
import { DEFAULT_COMMON, normalizeEngine } from "./settingsSchema";

/**
 * Iteration 5 review fixes. The setup controller is wired to a real DemoController exactly as
 * preview/main.ts does, so "the first setup's model is what a new job runs with" is checked end to end.
 */
function wired(example: SetupExample) {
  const demo = new DemoController();
  const setup = new SetupController({
    currentEnvironment: () => demo.store.get().env,
    applyEnvironment: (env, label) => demo.setEnvironment(env, label),
    applyDefaults: (patch) => demo.setCommonPatch(patch),
    notify: (key, params) => demo.say(key, params),
  });
  setup.setExample(example);
  return { demo, setup };
}
const advanceTo = (c: SetupController, stage: string) => {
  for (let i = 0; i < 20 && c.store.get().stages.find((s) => s.status === "running")?.id !== stage; i += 1) c.advance();
};
const finishRun = (c: SetupController) => {
  for (let i = 0; i < 20 && c.store.get().run === "running"; i += 1) c.advance();
};
const statusOf = (c: SetupController, id: string) => c.store.get().stages.find((s) => s.id === id)?.status;
/** First run with the standard method up to the review page, with model `model` (and the device). */
function firstRunToReview(c: SetupController, model: string, device: "gpu" | "cpu" = "gpu") {
  c.next(); // environment
  if (device === "cpu") c.setChoice("device", "cpu");
  c.chooseModel(model);
  c.next(); // features
  c.next(); // review
}

describe("review fix P1-1: a changed plan never inherits finished stages by stage id", () => {
  it("model A: asr_model done, smoke fails → change to model B → re-approve: B's asr_model is NOT done and runs; the result has B", () => {
    const { demo, setup: c } = wired("first_run");
    firstRunToReview(c, "large-v3-turbo");
    expect(c.approveAndStart()).toBe(true);
    advanceTo(c, "smoke");
    expect(statusOf(c, "asr_model")).toBe("done"); // model A downloaded
    c.fail();
    const envBefore = demo.store.get().env;
    c.changeChoices();
    expect(c.store.get().stages).toEqual([]); // the stopped run's stages are dropped
    c.goTo("environment");
    c.chooseModel("large-v3");
    c.next();
    c.next();
    expect(c.approveAndStart()).toBe(true);
    expect(statusOf(c, "asr_model")).toBe("pending");
    expect(c.store.get().stages.filter((s) => s.status === "done")).toEqual([]);
    expect(demo.store.get().env).toEqual(envBefore); // nothing applied
    advanceTo(c, "asr_model");
    expect(statusOf(c, "asr_model")).toBe("running"); // B is really fetched
    finishRun(c);
    c.finish();
    expect(demo.store.get().env.models.gpu).toEqual(["large-v3"]);
  });

  it("same plan: a plain retry after a failure keeps the stages finished under that approval", () => {
    const { setup: c } = wired("first_run");
    firstRunToReview(c, "large-v3-turbo");
    c.approveAndStart();
    advanceTo(c, "smoke");
    c.fail();
    expect(c.continueRun()).toBe(true);
    expect(["preflight", "runtime", "vad_model", "asr_model"].map((id) => statusOf(c, id))).toEqual(["done", "done", "done", "done"]);
    expect(statusOf(c, "smoke")).toBe("running");
  });

  it("device or diarization changed: re-approval starts every stage again; retry of the old approval is refused", () => {
    for (const change of ["device", "diarization"] as const) {
      const { setup: c } = wired("first_run");
      firstRunToReview(c, "large-v3-turbo");
      c.approveAndStart();
      advanceTo(c, "smoke");
      c.interrupt();
      if (change === "device") c.setChoice("device", "cpu");
      else {
        c.setChoice("diarization", true);
        c.registerCredential();
        c.setAgreement(DIARIZATION_TERMS, true);
      }
      expect(c.continueRun()).toBe(false); // the approval was for another plan
      c.changeChoices();
      c.goTo("features");
      c.next();
      expect(c.approveAndStart()).toBe(true);
      expect(c.store.get().stages.filter((s) => s.status === "done"), change).toEqual([]);
    }
  });

  it("failure and interruption change nothing: environment, jobs and individual settings", () => {
    const { demo, setup: c } = wired("configured");
    demo.setScenario("busy");
    const before = { env: demo.store.get().env, jobs: demo.store.get().jobs, overrides: demo.store.get().overrides, common: demo.store.get().common };
    c.open("add", { device: "gpu" });
    c.chooseModel("kotoba-whisper-v2.0");
    c.next();
    c.next();
    c.approveAndStart();
    c.advance();
    c.fail();
    c.continueRun();
    c.interrupt();
    const after = demo.store.get();
    expect({ env: after.env, jobs: after.jobs, overrides: after.overrides, common: after.common }).toEqual(before);
  });

  it("token registered but terms not accepted, or an approval of an older revision: nothing starts", () => {
    const { setup: c } = wired("first_run");
    c.next();
    c.next();
    c.setChoice("diarization", true);
    c.registerCredential();
    c.store.set({ ...c.store.get(), step: "review" });
    expect(c.approveAndStart()).toBe(false);
    c.setAgreement(DIARIZATION_TERMS, true);
    expect(c.approveAndStart()).toBe(true);
    c.fail();
    c.store.set({ ...c.store.get(), approvedRevision: c.store.get().planRevision - 1 });
    expect(c.continueRun()).toBe(false);
  });

  it("finish() applies nothing unless the CURRENT approved plan completed (direct calls included)", () => {
    const { demo, setup: c } = wired("first_run");
    const env0 = demo.store.get().env;
    const common0 = demo.store.get().common;
    firstRunToReview(c, "large-v3-turbo");
    c.store.set({ ...c.store.get(), run: "succeeded" }); // not approved
    c.finish();
    expect(demo.store.get().env).toEqual(env0);
    c.store.set({ ...c.store.get(), run: "idle" });
    c.approveAndStart();
    c.advance();
    c.store.set({ ...c.store.get(), run: "succeeded" }); // not every stage done
    expect(runCompleted(c.store.get())).toBe(false);
    c.finish();
    expect(demo.store.get().env).toEqual(env0);
    // Stages of plan A marked done, but the choices now describe plan B (same revision forged).
    const s = c.store.get();
    const stagesA = planFor(s.entry, s.choices, s.inventory).stages.map((id) => ({ id, status: "done" as const, fraction: null }));
    c.store.set({ ...s, run: "succeeded", stages: stagesA, choices: { ...s.choices, models: ["large-v3"], primaryModel: "large-v3" } });
    expect(runCompleted(c.store.get())).toBe(false);
    c.finish();
    expect(demo.store.get().env).toEqual(env0);
    expect(demo.store.get().common).toEqual(common0);
  });
});

describe("review fix P1-2: the first setup's main model becomes the common default", () => {
  const cases = [
    { model: "large-v3", device: "gpu" as const, expectDevice: "cuda" },
    { model: "kotoba-whisper-v2.0", device: "gpu" as const, expectDevice: "cuda" },
    { model: "large-v3-turbo", device: "gpu" as const, expectDevice: "cuda" },
    { model: "small", device: "cpu" as const, expectDevice: "cpu" },
  ];
  for (const k of cases) {
    it(`first run with ${k.model} (${k.device}) → common faster-whisper / ${k.model}; a new job's snapshot uses it`, () => {
      const { demo, setup: c } = wired(k.device === "cpu" ? "cpu_only" : "first_run");
      firstRunToReview(c, k.model, k.device);
      c.approveAndStart();
      finishRun(c);
      expect(demo.store.get().common.model).toBe(DEFAULT_COMMON.model); // not before finish()
      c.finish();
      const common = demo.store.get().common;
      expect(common).toMatchObject({ backend: "faster-whisper", model: k.model, device: k.expectDevice, diarization: false });
      expect(normalizeEngine(common)).toEqual(common); // a valid combination, nothing replaced silently
      expect(demo.store.get().env.defaultModel[k.device]).toBe(k.model);
      demo.submit(["in-01"]);
      expect(demo.store.get().jobs[0]!.snapshot).toMatchObject({ backend: "faster-whisper", model: k.model });
    });
  }

  it("several models, standard ↔ custom switched: the MAIN model becomes the default", () => {
    const { demo, setup: c } = wired("first_run");
    c.next();
    c.setMethod("custom");
    c.toggleModel("kotoba-whisper-v2.0", true);
    c.toggleModel("large-v3", true);
    c.setPrimaryModel("kotoba-whisper-v2.0");
    c.setMethod("standard");
    c.setMethod("custom");
    c.next();
    c.next();
    c.approveAndStart();
    finishRun(c);
    c.finish();
    expect(demo.store.get().env.models.gpu).toEqual(["large-v3-turbo", "kotoba-whisper-v2.0", "large-v3"]);
    expect(demo.store.get().common.model).toBe("kotoba-whisper-v2.0");
  });

  it("first run with diarization set up keeps diarization on by default", () => {
    const { demo, setup: c } = wired("first_run");
    c.next();
    c.next();
    c.setChoice("diarization", true);
    c.registerCredential();
    c.setAgreement(DIARIZATION_TERMS, true);
    c.next();
    c.approveAndStart();
    finishRun(c);
    c.finish();
    expect(demo.store.get().common).toMatchObject({ model: "large-v3-turbo", diarization: true });
  });

  it("explicit per-file overrides are kept; inheriting files use the new default", () => {
    const { demo, setup: c } = wired("first_run");
    demo.setScenario("multi"); // in-02 has its own language / speaker settings
    demo.setNormalSetting("in-03", { backend: "faster-whisper", model: "medium" });
    const overrides = demo.store.get().overrides;
    firstRunToReview(c, "large-v3");
    c.approveAndStart();
    finishRun(c);
    c.finish();
    expect(demo.store.get().overrides).toEqual(overrides);
    demo.submit(["in-01", "in-03"]);
    expect(demo.store.get().jobs.map((j) => j.snapshot.model)).toEqual(["large-v3", "medium"]);
  });

  it("failed / interrupted / not approved first run: common defaults, jobs and environment unchanged", () => {
    const { demo, setup: c } = wired("first_run");
    const before = { common: demo.store.get().common, env: demo.store.get().env, jobs: demo.store.get().jobs };
    firstRunToReview(c, "large-v3");
    c.finish(); // not approved
    c.approveAndStart();
    c.advance();
    c.fail();
    c.finish();
    c.continueRun();
    c.interrupt();
    c.finish();
    const s = demo.store.get();
    expect({ common: s.common, env: s.env, jobs: s.jobs }).toEqual(before);
  });

  it("add a model / repair: environment only — common defaults, overrides, earlier snapshots and the default model are unchanged", () => {
    const { demo, setup: c } = wired("configured");
    demo.setScenario("busy");
    const before = demo.store.get();
    c.open("add", { device: "gpu" });
    c.chooseModel("kotoba-whisper-v2.0");
    c.next();
    c.next();
    c.approveAndStart();
    finishRun(c);
    c.finish();
    let s = demo.store.get();
    expect(s.env.models.gpu).toEqual(["large-v3-turbo", "kotoba-whisper-v2.0"]);
    expect(s.env.defaultModel.gpu).toBe("large-v3-turbo");
    expect({ common: s.common, overrides: s.overrides, jobs: s.jobs, presets: s.userPresets }).toEqual({ common: before.common, overrides: before.overrides, jobs: before.jobs, presets: before.userPresets });

    demo.setEnvironment(envFromExample("repair"), "repair");
    c.open("repair");
    c.approveAndStart();
    finishRun(c);
    c.finish();
    s = demo.store.get();
    expect(s.env.gpu).toBe("ready");
    expect({ common: s.common, overrides: s.overrides, jobs: s.jobs }).toEqual({ common: before.common, overrides: before.overrides, jobs: before.jobs });
  });
});
