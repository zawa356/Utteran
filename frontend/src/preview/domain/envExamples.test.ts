import { describe, expect, it } from "vitest";
import { ja } from "../i18n/ja";
import { ENV_EXAMPLES, environmentView, exampleAllowsStart, primaryProfile } from "./envExamples";

describe("fictional environment examples", () => {
  it("GPU / CPU / unconfigured / repair / load failure are distinct and only some allow the demo start", () => {
    expect(primaryProfile(environmentView("gpu"))).toMatchObject({ name: "gpu", status: "ready" });
    expect(primaryProfile(environmentView("cpu"))).toMatchObject({ name: "cpu", status: "warning" });
    expect(primaryProfile(environmentView("unconfigured"))).toMatchObject({ name: "none", status: "unavailable" });
    expect(environmentView("error")).toMatchObject({ loadFailed: true, profiles: [] });
    expect(primaryProfile(environmentView("repair"))).toMatchObject({ name: "cpu", status: "ready" });
    expect(environmentView("repair").profiles[0]).toMatchObject({ name: "gpu", status: "repair", actions: ["repair"] });
    expect(ENV_EXAMPLES.map((e) => exampleAllowsStart(environmentView(e)))).toEqual([true, true, false, true, false]);
  });

  it("cards offer actions only where something is missing or broken (a ready profile is never reinstalled)", () => {
    expect(environmentView("gpu").profiles.map((p) => p.actions)).toEqual([[], []]);
    expect(environmentView("gpu", false).profiles[0]!.actions).toEqual(["diarization"]);
    expect(environmentView("cpu").profiles.find((p) => p.name === "gpu")!.actions).toEqual(["addGpu"]);
    expect(environmentView("unconfigured").profiles[0]!.actions).toEqual(["setup"]);
  });

  it("every detection / warning / action / value key exists in the dictionary", () => {
    for (const example of ENV_EXAMPLES) {
      for (const p of [...environmentView(example).profiles, ...environmentView(example, false).profiles]) {
        for (const key of [...p.detection, ...p.warnings, ...p.actions.map((a) => `env.action.${a}`), `env.status.${p.status}`]) expect(key in ja, key).toBe(true);
        for (const f of p.facts) if (f.value.startsWith("key:")) expect(f.value.slice(4) in ja, f.value).toBe(true);
      }
    }
  });
});
