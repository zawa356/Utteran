import { describe, expect, it } from "vitest";
import { envFromExample, type EnvState } from "./envExamples";
import { EMPTY_INVENTORY, exampleState, inventoryOf, nextBlock, planFor, planRequest, SetupController, type SetupExample, unavailableModels } from "./setup";
import { MAX_MODEL_REFS, modelAllowed, modelsFor, RECOMMENDED_MODEL, SETUP_MODELS } from "./setupCatalog";

function controller(example: SetupExample = "configured", start?: EnvState) {
  let env: EnvState = envFromExample("gpu");
  const c = new SetupController({
    currentEnvironment: () => env,
    applyEnvironment: (next) => (env = next),
    applyDefaults: () => undefined,
    notify: () => undefined,
  });
  c.setExample(example);
  if (start) env = start;
  return { c, env: () => env };
}
const finishRun = (c: SetupController) => {
  for (let i = 0; i < 20 && c.store.get().run === "running"; i += 1) c.advance();
};

describe("iteration 5 #30: standard / custom setup with model selection", () => {
  it("catalog fixture: the three catalog models exist; large-v3 is GPU-only (example rule)", () => {
    expect(SETUP_MODELS.map((m) => m.id)).toEqual(expect.arrayContaining(["large-v3-turbo", "large-v3", "kotoba-whisper-v2.0"]));
    expect(modelAllowed("large-v3", "faster-whisper", "cpu")).toBe(false);
    expect(modelAllowed("large-v3", "whisper-cpp", "gpu")).toBe(false);
    expect(modelsFor("faster-whisper", "gpu").map((m) => m.id)).toContain(RECOMMENDED_MODEL.gpu);
  });

  it("standard: the recommendation is shown, then changed to large-v3; the plan and the B8 request follow; the result keeps it", () => {
    const { c, env } = controller("first_run");
    c.next(); // environment
    expect(c.store.get().choices).toMatchObject({ method: "standard", models: ["large-v3-turbo"], primaryModel: "large-v3-turbo" });
    c.chooseModel("large-v3");
    const s = c.store.get();
    const plan = planFor(s.entry, s.choices, s.inventory);
    expect(plan.items.filter((i) => i.id === "asrModel").map((i) => i.modelId)).toEqual(["large-v3"]);
    expect(plan.items.find((i) => i.modelId === "large-v3")?.downloadMb).toBe(3329);
    expect(planRequest(s.choices)).toEqual({ profile_kind: "cuda", model_refs: ["faster-whisper:large-v3"], diarization_enabled: false });
    c.next();
    c.next();
    expect(c.approveAndStart()).toBe(true);
    finishRun(c);
    c.finish();
    expect(env()).toMatchObject({ gpu: "ready", models: { gpu: ["large-v3"] }, defaultModel: { gpu: "large-v3" } });
  });

  it("custom: several models and a main model; switching to standard and back loses nothing", () => {
    const { c } = controller("first_run");
    c.next();
    c.setMethod("custom");
    c.toggleModel("kotoba-whisper-v2.0", true);
    c.setPrimaryModel("kotoba-whisper-v2.0");
    c.setMethod("standard");
    c.setMethod("custom");
    expect(c.store.get().choices).toMatchObject({ models: ["large-v3-turbo", "kotoba-whisper-v2.0"], primaryModel: "kotoba-whisper-v2.0" });
    expect(planRequest(c.store.get().choices).model_refs).toEqual(["faster-whisper:large-v3-turbo", "faster-whisper:kotoba-whisper-v2.0"]);
  });

  it("invalid combinations cannot be chosen; a device change flags them and blocks until fixed", () => {
    const { c } = controller("first_run");
    c.next();
    c.setMethod("custom");
    c.toggleModel("large-v3", true);
    c.setEngine("whisper-cpp"); // not selectable in the prototype
    expect(c.store.get().choices.engine).toBe("faster-whisper");
    c.setChoice("device", "cpu");
    expect(unavailableModels(c.store.get().choices)).toEqual(["large-v3"]);
    expect(nextBlock(c.store.get())).toBe("modelUnavailable");
    c.next();
    expect(c.store.get().step).toBe("environment");
    c.toggleModel("large-v3", false);
    c.toggleModel("large-v3", true); // not offered for the CPU: refused
    expect(c.store.get().choices.models).not.toContain("large-v3");
    c.setChoice("device", "gpu");
    c.toggleModel("large-v3", true);
    c.setChoice("device", "cpu");
    c.removeUnavailableModels();
    expect(nextBlock(c.store.get())).toBeNull();
    expect(c.store.get().choices.models).toEqual(["large-v3-turbo"]);
  });

  it("untouched choices follow the recommendation of the device", () => {
    const { c } = controller("first_run");
    c.next();
    c.setChoice("device", "cpu");
    expect(c.store.get().choices.models).toEqual([RECOMMENDED_MODEL.cpu]);
  });

  it("add a model to a ready GPU: only the model is downloaded; GPU, CPU, diarization and the default model are kept", () => {
    const { c, env } = controller("configured");
    c.open("add", { device: "gpu" });
    expect(c.store.get().choices.models).toEqual(["large-v3-turbo"]); // the installed one, locked
    expect(nextBlock({ ...c.store.get(), step: "review" })).toBe("nothingToInstall");
    c.chooseModel("kotoba-whisper-v2.0");
    const s = c.store.get();
    const plan = planFor(s.entry, s.choices, s.inventory);
    expect(plan.items.map((i) => i.id)).toEqual(["asrModel"]);
    expect(plan.stages).toEqual(["preflight", "asr_model", "smoke"]);
    expect(plan.newModels).toEqual(["kotoba-whisper-v2.0"]);
    c.toggleModel("large-v3-turbo", false); // installed: cannot be removed here
    expect(c.store.get().choices.models).toContain("large-v3-turbo");
    c.next();
    c.next();
    expect(c.approveAndStart()).toBe(true);
    finishRun(c);
    c.finish();
    expect(env()).toMatchObject({ gpu: "ready", cpu: "ready", diarization: true, preferred: "gpu", models: { gpu: ["large-v3-turbo", "kotoba-whisper-v2.0"], cpu: ["small"] }, defaultModel: { gpu: "large-v3-turbo", cpu: "small" } });
  });

  it("a model change after the approval voids it (re-approval)", () => {
    const { c } = controller("failed");
    expect(c.store.get().approved).toBe(true);
    c.chooseModel("large-v3");
    expect(c.store.get()).toMatchObject({ approved: false, approvedRevision: null });
    expect(c.continueRun()).toBe(false);
  });

  it("sizes: unknown sizes are not added up as zero silently; at most 8 model refs", () => {
    const base = exampleState("first_run").choices;
    const plan = planFor("first_run", { ...base, models: ["not-in-catalog"] }, EMPTY_INVENTORY);
    expect(plan.sizeUnknown).toBe(true);
    expect(plan.items.find((i) => i.modelId === "not-in-catalog")).toMatchObject({ downloadMb: null });
    const s = { ...exampleState("first_run"), step: "environment" as const, choices: { ...base, models: Array.from({ length: MAX_MODEL_REFS + 1 }, () => "small") } };
    expect(nextBlock(s)).toBe("tooManyModels");
    expect(inventoryOf(envFromExample("repair")).models.gpu).toEqual(["large-v3-turbo"]); // repair keeps the models
  });
});
