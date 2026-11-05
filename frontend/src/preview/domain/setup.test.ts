import { describe, expect, it } from "vitest";
import { envFromExample, type EnvState } from "./envExamples";
import {
  credentialStepNeeded,
  DIARIZATION_TERMS,
  envForExample,
  exampleState,
  missingRequirements,
  nextBlock,
  planFor,
  SETUP_EXAMPLES,
  type SetupExample,
  SetupController,
  wizardSteps,
} from "./setup";

/** A controller wired to an in-memory environment, as preview/main.ts wires it to the demo store. */
function controller(example: SetupExample = "configured") {
  let env: EnvState = envFromExample("gpu");
  const applied: EnvState[] = [];
  const defaults: { diarization?: boolean }[] = [];
  const notices: string[] = [];
  const c = new SetupController({
    currentEnvironment: () => env,
    applyEnvironment: (next) => {
      env = next;
      applied.push(next);
    },
    applyDefaults: (p) => defaults.push(p),
    notify: (k) => notices.push(k),
  });
  c.setExample(example);
  applied.length = 0;
  return { c, env: () => env, setEnv: (e: EnvState) => (env = e), applied, defaults, notices };
}
const finishRun = (c: SetupController) => {
  for (let i = 0; i < 20 && c.store.get().run === "running"; i += 1) c.advance();
};
/** Walks the add wizard from "environment" to "review" with the given device and diarization. */
function toReview(c: SetupController, device: "gpu" | "cpu", diarization: boolean) {
  c.setChoice("device", device);
  c.next();
  c.setChoice("diarization", diarization);
  c.next();
}

describe("setup wizard mock (iteration 4, owner memos #23/#24; B8 plan → approve → operation)", () => {
  it("steps depend on the entry; the credential/terms step only when the plan requires them", () => {
    const first = exampleState("first_run");
    expect(wizardSteps(first)).toEqual(["welcome", "environment", "features", "review"]);
    expect(wizardSteps({ ...first, choices: { ...first.choices, diarization: true } })).toEqual(["welcome", "environment", "features", "credentials", "review"]);
    // A credential that already exists does not accept the terms: the step is still needed.
    expect(credentialStepNeeded({ ...first, detected: { gpu: true, credential: true }, choices: { ...first.choices, diarization: true } })).toBe(true);
    expect(wizardSteps({ ...first, entry: "add" })[0]).toBe("environment");
    expect(wizardSteps({ ...first, entry: "repair" })).toEqual(["review"]);
  });

  it("first run → CPU → no diarization → approve → stages → done → CPU ready, GPU not set up", () => {
    const { c, env, defaults } = controller("first_run");
    expect(env()).toMatchObject({ gpu: "absent", cpu: "absent" });
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
    expect(c.approveAndStart()).toBe(true);
    expect(c.store.get()).toMatchObject({ step: "run", run: "running", approved: true });
    expect(c.store.get().stages.map((s) => s.id)).toEqual(["preflight", "runtime", "vad_model", "asr_model", "smoke"]);
    expect(env()).toMatchObject({ cpu: "absent" }); // nothing applied while running
    finishRun(c);
    expect(c.store.get()).toMatchObject({ run: "succeeded", step: "done" });
    c.finish();
    expect(c.store.get()).toMatchObject({ configured: true, wizardOpen: false });
    expect(env()).toMatchObject({ cpu: "ready", gpu: "absent", diarization: false });
    expect(defaults).toEqual([{ diarization: false }]); // not installed → off by default
  });

  it("CPU only: the GPU cannot be chosen, the CPU completes the wizard", () => {
    const { c, env } = controller("cpu_only");
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
    expect(env()).toMatchObject({ cpu: "ready", gpu: "absent", gpuDetected: false });
  });

  it("failure → retry keeps finished stages; change settings needs a new approval", () => {
    const { c } = controller("failed");
    expect(c.store.get()).toMatchObject({ wizardOpen: true, step: "run", run: "failed", errorCode: "demo_download_failed" });
    expect(c.continueRun()).toBe(true);
    expect(c.store.get().stages.filter((st) => st.status === "done").map((st) => st.id)).toEqual(["preflight", "runtime", "vad_model"]);
    expect(c.store.get()).toMatchObject({ run: "running", attempt: 2 });
    c.fail();
    c.changeChoices();
    expect(c.store.get()).toMatchObject({ run: "idle", step: "features", approved: false, approvedRevision: null });
    c.next();
    c.next();
    expect(c.approveAndStart()).toBe(true);
    expect(c.store.get().stages.find((st) => st.id === "runtime")!.status).toBe("done"); // already installed
  });

  it("interrupted → continue from the stopped stage → done", () => {
    const { c } = controller("incomplete");
    expect(c.store.get()).toMatchObject({ run: "interrupted", step: "run" });
    c.close();
    c.open("first_run"); // reopening shows the unfinished operation, not a new start
    expect(c.store.get()).toMatchObject({ wizardOpen: true, step: "run", run: "interrupted" });
    c.continueRun();
    expect(c.store.get().stages.find((st) => st.id === "asr_model")!.status).toBe("running");
    finishRun(c);
    expect(c.store.get().step).toBe("done");
  });

  it("a plan choice changed after approval invalidates it (reapproval)", () => {
    const { c } = controller("first_run");
    c.next();
    c.next();
    c.next();
    c.approveAndStart();
    expect(c.store.get().approved).toBe(true);
    c.setChoice("diarization", true);
    expect(c.store.get()).toMatchObject({ approved: false, approvedRevision: null });
  });

  it("examples map to a per-profile environment", () => {
    for (const e of SETUP_EXAMPLES) expect(envForExample(e).env).toBeTruthy();
    expect(envForExample("missing_token").env).toMatchObject({ gpu: "ready", diarization: false });
    expect(envForExample("needs_repair").env).toMatchObject({ gpu: "repair", cpu: "ready" });
    expect(envForExample("cpu_only").env).toMatchObject({ gpu: "absent", cpu: "absent", gpuDetected: false });
  });
});

describe("P1-1: credential and terms are separate requirements, enforced by the controller", () => {
  /** First run with diarization on, at the credential/terms step. */
  function atCredentials(credential: boolean) {
    const ctl = controller("first_run");
    if (credential) ctl.c.store.set({ ...ctl.c.store.get(), detected: { ...ctl.c.store.get().detected, credential: true } });
    ctl.c.next();
    ctl.c.next();
    ctl.c.setChoice("diarization", true);
    ctl.c.next();
    return ctl;
  }

  it("diarization on, credential present, terms NOT accepted: the terms step shows and blocks; approval is refused", () => {
    const { c, notices } = atCredentials(true);
    expect(c.store.get().step).toBe("credentials");
    expect(missingRequirements(c.store.get())).toEqual(["agreement"]);
    expect(nextBlock(c.store.get())).toBe("agreement");
    c.next();
    expect(c.store.get().step).toBe("credentials");
    // Even if the review step is reached by other means, approval is refused.
    c.store.set({ ...c.store.get(), step: "review" });
    expect(nextBlock(c.store.get())).toBe("agreement");
    expect(c.approveAndStart()).toBe(false);
    expect(c.store.get()).toMatchObject({ run: "idle", approved: false, stages: [] });
    expect(notices).toContain("notice.setupBlocked");
    c.setAgreement(DIARIZATION_TERMS, true);
    expect(c.approveAndStart()).toBe(true);
    expect(c.store.get().run).toBe("running");
  });

  it("diarization on, credential missing, terms accepted: still blocked on the credential", () => {
    const { c } = atCredentials(false);
    c.setAgreement(DIARIZATION_TERMS, true);
    expect(missingRequirements(c.store.get())).toEqual(["credential"]);
    c.store.set({ ...c.store.get(), step: "review" });
    expect(c.approveAndStart()).toBe(false);
    expect(c.store.get().run).toBe("idle");
    c.registerCredential();
    expect(c.approveAndStart()).toBe(true);
  });

  it("diarization on, both given: next goes to review and approval starts", () => {
    const { c } = atCredentials(false);
    c.registerCredential();
    c.setAgreement(DIARIZATION_TERMS, true);
    c.next();
    expect(c.store.get().step).toBe("review");
    expect(c.approveAndStart()).toBe(true);
  });

  it("diarization off, neither given: nothing is required and the plan lists no terms", () => {
    const { c } = controller("first_run");
    c.next();
    c.next();
    c.next();
    expect(c.store.get().step).toBe("review");
    expect(planFor(c.store.get().entry, c.store.get().choices)).toMatchObject({ requiresCredential: false, requiredAgreements: [] });
    expect(missingRequirements(c.store.get())).toEqual([]);
    expect(c.approveAndStart()).toBe(true);
  });

  it("'continue without diarization' drops the requirement; turning it on again requires the terms again", () => {
    const { c } = atCredentials(true);
    c.skipDiarization();
    expect(c.store.get()).toMatchObject({ step: "review", choices: { diarization: false } });
    expect(missingRequirements(c.store.get())).toEqual([]);
    c.back();
    c.setChoice("diarization", true);
    expect(missingRequirements(c.store.get())).toEqual(["agreement"]);
  });

  it("retry / resume refuse to run without the current plan's approval and requirements", () => {
    const { c } = controller("failed");
    // The terms were withdrawn: retry is refused.
    c.setAgreement(DIARIZATION_TERMS, false);
    expect(c.continueRun()).toBe(false);
    expect(c.store.get().run).toBe("failed");
    c.setAgreement(DIARIZATION_TERMS, true);
    // The plan changed after the approval: retry is refused until a new approval.
    c.setChoice("device", "cpu");
    expect(c.continueRun()).toBe(false);
    expect(c.store.get().run).toBe("failed");
    c.changeChoices();
    c.next(); // features → credentials (diarization still on)
    expect(c.store.get().step).toBe("credentials");
    c.next();
    expect(c.approveAndStart()).toBe(true);
    c.interrupt();
    c.setAgreement(DIARIZATION_TERMS, false);
    expect(c.continueRun()).toBe(false); // resume checks too
    expect(c.store.get().run).toBe("interrupted");
  });

  it("an add wizard starts with no accepted terms (an earlier plan's acceptance is not reused)", () => {
    const { c } = controller("failed");
    expect(c.store.get().choices.acceptedAgreements).toEqual([DIARIZATION_TERMS]);
    c.continueRun();
    finishRun(c);
    c.finish();
    c.open("add", { diarization: true });
    expect(c.store.get().choices.acceptedAgreements).toEqual([]);
    expect(missingRequirements(c.store.get())).toEqual(["agreement"]); // the credential exists now, the terms do not
  });
});

describe("P1-2: add / repair keep the environment they did not touch", () => {
  it("GPU ready + diarization ready → add CPU: GPU, diarization, nothing else changes; applied only on success", () => {
    const ctl = controller("configured");
    ctl.setEnv({ ...envFromExample("gpu", true), cpu: "absent" });
    ctl.c.open("add", { device: "cpu", diarization: false });
    expect(ctl.c.store.get()).toMatchObject({ step: "environment", choices: { device: "cpu", diarization: false } });
    toReview(ctl.c, "cpu", false);
    ctl.c.approveAndStart();
    ctl.c.fail();
    expect(ctl.env()).toMatchObject({ gpu: "ready", cpu: "absent", diarization: true }); // failure: unchanged
    ctl.c.continueRun();
    ctl.c.interrupt();
    expect(ctl.env()).toMatchObject({ cpu: "absent" }); // interruption: unchanged
    ctl.c.continueRun();
    finishRun(ctl.c);
    expect(ctl.env()).toMatchObject({ cpu: "absent" }); // done page, not yet finished
    ctl.c.finish();
    expect(ctl.env()).toMatchObject({ gpu: "ready", cpu: "ready", diarization: true, preferred: "gpu" });
    expect(ctl.defaults).toEqual([]); // processing defaults untouched
  });

  it("CPU ready → add GPU: the CPU stays, the GPU becomes ready", () => {
    const ctl = controller("configured");
    ctl.setEnv(envFromExample("cpu", false));
    ctl.c.open("add", { device: "gpu", diarization: false });
    toReview(ctl.c, "gpu", false);
    ctl.c.approveAndStart();
    finishRun(ctl.c);
    ctl.c.finish();
    expect(ctl.env()).toMatchObject({ gpu: "ready", cpu: "ready", diarization: false, preferred: "cpu" });
  });

  it("GPU needs repair + CPU ready → repair: only the GPU becomes ready; diarization is not turned on", () => {
    const ctl = controller("needs_repair");
    ctl.setEnv(envFromExample("repair", false));
    ctl.c.open("repair");
    expect(ctl.c.store.get()).toMatchObject({ step: "review", choices: { device: "gpu" } });
    expect(planFor("repair", ctl.c.store.get().choices)).toMatchObject({ target: "gpu", requiredAgreements: [], stages: ["preflight", "runtime", "smoke"] });
    ctl.c.approveAndStart();
    finishRun(ctl.c);
    ctl.c.finish();
    expect(ctl.env()).toEqual({ ...envFromExample("repair", false), gpu: "ready" });
  });

  it("GPU ready → add diarization: the GPU is kept; diarization is ready only after success", () => {
    const ctl = controller("missing_token");
    expect(ctl.env()).toMatchObject({ gpu: "ready", diarization: false });
    ctl.c.open("add", { device: "gpu", diarization: true });
    ctl.c.next(); // features
    ctl.c.next(); // credentials
    ctl.c.registerCredential();
    ctl.c.setAgreement(DIARIZATION_TERMS, true);
    ctl.c.next();
    ctl.c.approveAndStart();
    expect(ctl.env().diarization).toBe(false);
    finishRun(ctl.c);
    ctl.c.finish();
    expect(ctl.env()).toMatchObject({ gpu: "ready", cpu: "ready", diarization: true });
  });
});
