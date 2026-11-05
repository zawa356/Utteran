import { describe, expect, it } from "vitest";
import type { EnvExample } from "./envExamples";
import { credentialStepNeeded, envForExample, exampleState, nextBlock, planFor, SETUP_EXAMPLES, SetupController, wizardSteps } from "./setup";

function controller() {
  const applied: { env: EnvExample; diarizationReady: boolean }[] = [];
  const defaults: { diarization?: boolean }[] = [];
  const notices: string[] = [];
  const c = new SetupController({
    applyEnvironment: (env, diarizationReady) => applied.push({ env, diarizationReady }),
    applyDefaults: (p) => defaults.push(p),
    notify: (k) => notices.push(k),
  });
  return { c, applied, defaults, notices };
}
const finishRun = (c: SetupController) => {
  for (let i = 0; i < 20 && c.store.get().run === "running"; i += 1) c.advance();
};

describe("setup wizard mock (iteration 4, owner memos #23/#24; B8 plan → approve → operation)", () => {
  it("steps depend on the entry; the credential step only when diarization needs it", () => {
    const first = exampleState("first_run");
    expect(wizardSteps(first)).toEqual(["welcome", "environment", "features", "review"]);
    expect(wizardSteps({ ...first, choices: { ...first.choices, diarization: true } })).toEqual(["welcome", "environment", "features", "credentials", "review"]);
    expect(credentialStepNeeded({ ...first, detected: { gpu: true, credential: true }, choices: { ...first.choices, diarization: true } })).toBe(false);
    expect(wizardSteps({ ...first, entry: "add" })[0]).toBe("environment");
    expect(wizardSteps({ ...first, entry: "repair" })).toEqual(["review"]);
  });

  it("first run → CPU → no diarization → approve → stages → done → configured CPU environment", () => {
    const { c, applied, defaults } = controller();
    c.setExample("first_run");
    expect(applied.at(-1)).toEqual({ env: "unconfigured", diarizationReady: false });
    expect(c.store.get()).toMatchObject({ configured: false, wizardOpen: true, step: "welcome", run: "idle" });
    c.next();
    c.setChoice("device", "cpu");
    c.next(); // features
    c.back();
    expect(c.store.get()).toMatchObject({ step: "environment", choices: { device: "cpu" } }); // kept going back
    c.next();
    c.next(); // review (no credential step)
    expect(c.store.get().step).toBe("review");
    expect(c.store.get().stages).toEqual([]); // nothing runs before the approval
    c.approveAndStart();
    expect(c.store.get()).toMatchObject({ step: "run", run: "running", approved: true });
    expect(c.store.get().stages.map((s) => s.id)).toEqual(["preflight", "runtime", "vad_model", "asr_model", "smoke"]);
    finishRun(c);
    expect(c.store.get()).toMatchObject({ run: "succeeded", step: "done" });
    c.finish();
    expect(c.store.get()).toMatchObject({ configured: true, wizardOpen: false });
    expect(applied.at(-1)).toEqual({ env: "cpu", diarizationReady: false });
    expect(defaults).toEqual([{ diarization: false }]); // not installed → off by default
  });

  it("CPU only: the GPU cannot be chosen, the CPU completes the wizard", () => {
    const { c, applied } = controller();
    c.setExample("cpu_only");
    c.next();
    expect(c.store.get().choices.device).toBe("cpu");
    c.setChoice("device", "gpu");
    expect(nextBlock(c.store.get())).toBe("gpuMissing");
    c.next();
    expect(c.store.get().step).toBe("environment");
    c.setChoice("device", "cpu");
    c.next();
    c.next();
    c.approveAndStart();
    finishRun(c);
    c.finish();
    expect(applied.at(-1)?.env).toBe("cpu");
  });

  it("diarization without a credential: register (presence only) and agree, or continue without it", () => {
    const { c } = controller();
    c.setExample("first_run");
    c.next();
    c.next();
    c.setChoice("diarization", true);
    c.next();
    expect(c.store.get().step).toBe("credentials");
    expect(nextBlock(c.store.get())).toBe("credential");
    c.registerCredential();
    expect(nextBlock(c.store.get())).toBe("agreement");
    c.setChoice("agreement", true);
    c.next();
    expect(c.store.get().step).toBe("review");
    expect(planFor("first_run", c.store.get().choices)).toMatchObject({ needsAgreement: true });
    c.back();
    c.skipDiarization();
    expect(c.store.get()).toMatchObject({ step: "review", choices: { diarization: false } });
  });

  it("failure → log/retry keeps finished stages; change settings needs a new approval", () => {
    const { c } = controller();
    c.setExample("failed");
    const s = c.store.get();
    expect(s).toMatchObject({ wizardOpen: true, step: "run", run: "failed", errorCode: "demo_download_failed" });
    c.continueRun();
    expect(c.store.get().stages.filter((st) => st.status === "done").map((st) => st.id)).toEqual(["preflight", "runtime", "vad_model"]);
    expect(c.store.get()).toMatchObject({ run: "running", attempt: 2 });
    c.fail();
    c.changeChoices();
    expect(c.store.get()).toMatchObject({ run: "idle", step: "features", approved: false, approvedRevision: null });
    c.next();
    c.next();
    c.approveAndStart();
    expect(c.store.get().stages.find((st) => st.id === "runtime")!.status).toBe("done"); // already installed
  });

  it("interrupted → continue from the stopped stage → done", () => {
    const { c } = controller();
    c.setExample("incomplete");
    expect(c.store.get()).toMatchObject({ run: "interrupted", step: "run" });
    c.close();
    c.open("first_run"); // reopening shows the unfinished operation, not a new start
    expect(c.store.get()).toMatchObject({ wizardOpen: true, step: "run", run: "interrupted" });
    c.continueRun();
    expect(c.store.get().stages.find((st) => st.id === "asr_model")!.status).toBe("running");
    finishRun(c);
    expect(c.store.get().step).toBe("done");
  });

  it("a choice changed after approval invalidates it (reapproval)", () => {
    const { c } = controller();
    c.setExample("first_run");
    c.next();
    c.next();
    c.next();
    c.approveAndStart();
    expect(c.store.get().approved).toBe(true);
    c.setChoice("diarization", true);
    expect(c.store.get().approved).toBe(false);
  });

  it("repair: a short plan; configured examples keep the app usable", () => {
    const { c, applied } = controller();
    c.setExample("needs_repair");
    expect(c.store.get()).toMatchObject({ configured: true, wizardOpen: false });
    expect(applied.at(-1)).toEqual({ env: "repair", diarizationReady: true });
    c.open("repair");
    expect(c.store.get().step).toBe("review");
    expect(planFor("repair", c.store.get().choices).stages).toEqual(["preflight", "runtime", "smoke"]);
    c.approveAndStart();
    finishRun(c);
    c.finish();
    expect(applied.at(-1)?.env).toBe("gpu");
    for (const e of SETUP_EXAMPLES) expect(envForExample(e).env).toBeTruthy();
    expect(envForExample("missing_token")).toEqual({ env: "gpu", diarizationReady: false });
  });
});
