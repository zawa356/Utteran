import { describe, expect, it } from "vitest";
import { ja } from "../i18n/ja";
import { applySetupOutcome, ENV_EXAMPLES, envFromExample, type EnvState, environmentView, exampleAllowsStart, primaryProfile, repairTarget } from "./envExamples";

const view = (e: Parameters<typeof envFromExample>[0], diarization = true) => environmentView(envFromExample(e, diarization));
const status = (env: EnvState) => Object.fromEntries(environmentView(env).profiles.map((p) => [p.name, p.status]));

describe("fictional environment (per-profile state)", () => {
  it("GPU / CPU / unconfigured / repair / load failure are distinct and only some allow the demo start", () => {
    expect(primaryProfile(view("gpu"))).toMatchObject({ name: "gpu", status: "ready" });
    expect(primaryProfile(view("cpu"))).toMatchObject({ name: "cpu", status: "ready" });
    expect(primaryProfile(view("unconfigured"))).toMatchObject({ name: "none", status: "unavailable" });
    expect(view("error")).toMatchObject({ loadFailed: true, profiles: [] });
    expect(primaryProfile(view("repair"))).toMatchObject({ name: "cpu", status: "ready" });
    expect(view("repair").profiles[0]).toMatchObject({ name: "gpu", status: "repair", actions: ["repair"] });
    expect(ENV_EXAMPLES.map((e) => exampleAllowsStart(view(e)))).toEqual([true, true, true, false, true, false]);
  });

  it("cards offer actions only where something is missing or broken (a ready profile is never reinstalled)", () => {
    expect(view("gpu").profiles.map((p) => p.actions)).toEqual([[], []]);
    expect(view("gpu", false).profiles[0]!.actions).toEqual(["diarization"]);
    expect(view("cpu").profiles.find((p) => p.name === "gpu")!.actions).toEqual(["addGpu"]);
    expect(view("unconfigured").profiles[0]!.actions).toEqual(["setup"]);
    expect(environmentView({ ...envFromExample("gpu"), cpu: "absent" }).profiles.find((p) => p.name === "cpu")!.actions).toEqual(["addCpu"]);
    expect(environmentView({ ...envFromExample("cpu"), gpuDetected: false }).profiles.map((p) => p.name)).toEqual(["cpu"]); // no GPU card without a GPU
  });

  it("a detected GPU is not a ready GPU profile", () => {
    const env = envFromExample("cpu");
    expect(env.gpuDetected).toBe(true);
    expect(status(env).gpu).toBe("unavailable");
  });

  it("every detection / warning / action / value key exists in the dictionary", () => {
    const envs: EnvState[] = [...ENV_EXAMPLES.flatMap((e) => [envFromExample(e), envFromExample(e, false)]), { ...envFromExample("gpu"), cpu: "absent" }, { ...envFromExample("repair"), cpu: "absent" }, { ...envFromExample("gpu"), cpu: "repair" }];
    for (const env of envs) {
      for (const p of environmentView(env).profiles) {
        for (const key of [...p.detection, ...p.warnings, ...p.actions.map((a) => `env.action.${a}`), `env.status.${p.status}`]) expect(key in ja, key).toBe(true);
        for (const f of p.facts) if (f.value.startsWith("key:")) expect(f.value.slice(4) in ja, f.value).toBe(true);
      }
    }
  });
});

describe("setup outcome keeps what it did not install (iteration 4 P1-2)", () => {
  it("GPU ready + diarization ready → add CPU (no diarization): GPU and diarization stay ready", () => {
    const before: EnvState = { ...envFromExample("gpu", true), cpu: "absent" };
    const after = applySetupOutcome(before, { entry: "add", device: "cpu", diarization: false });
    expect(after).toMatchObject({ gpu: "ready", cpu: "ready", diarization: true, preferred: "gpu" });
    expect(primaryProfile(environmentView(after))?.name).toBe("gpu"); // adding does not switch the preferred profile
  });

  it("CPU ready → add GPU: the CPU stays ready, the GPU becomes ready", () => {
    const after = applySetupOutcome(envFromExample("cpu", false), { entry: "add", device: "gpu", diarization: false });
    expect(after).toMatchObject({ gpu: "ready", cpu: "ready", diarization: false, preferred: "cpu" });
  });

  it("GPU needs repair + CPU ready → repair GPU: only the GPU changes; diarization is not invented", () => {
    const before = envFromExample("repair", false);
    expect(repairTarget(before)).toBe("gpu");
    const after = applySetupOutcome(before, { entry: "repair", device: "gpu", diarization: true });
    expect(after).toEqual({ ...before, gpu: "ready" });
  });

  it("GPU ready → add diarization: the GPU is kept and diarization becomes ready", () => {
    const after = applySetupOutcome(envFromExample("gpu", false), { entry: "add", device: "gpu", diarization: true });
    expect(after).toMatchObject({ gpu: "ready", cpu: "ready", diarization: true });
  });

  it("first run, CPU only: CPU ready, GPU still not set up, diarization off", () => {
    const after = applySetupOutcome({ ...envFromExample("unconfigured"), gpuDetected: false }, { entry: "first_run", device: "cpu", diarization: false });
    expect(after).toMatchObject({ cpu: "ready", gpu: "absent", diarization: false, preferred: "cpu" });
    expect(environmentView(after).profiles.map((p) => p.name)).toEqual(["cpu"]);
    expect(exampleAllowsStart(environmentView(after))).toBe(true);
  });
});
