import { describe, expect, it } from "vitest";
import { ja } from "../i18n/ja";
import { ENV_EXAMPLES, environmentView, exampleAllowsStart, primaryProfile } from "./envExamples";

describe("fictional environment examples", () => {
  it("GPU / CPU / unconfigured / load failure are distinct and only some allow the demo start", () => {
    expect(primaryProfile(environmentView("gpu"))).toMatchObject({ name: "gpu", status: "ready" });
    expect(primaryProfile(environmentView("cpu"))).toMatchObject({ name: "cpu", status: "warning" });
    expect(primaryProfile(environmentView("unconfigured"))).toMatchObject({ name: "none", status: "unavailable" });
    expect(environmentView("error")).toMatchObject({ loadFailed: true, profiles: [] });
    expect(ENV_EXAMPLES.map((e) => exampleAllowsStart(environmentView(e)))).toEqual([true, true, false, false]);
  });

  it("every evidence / warning / action / value key exists in the dictionary", () => {
    for (const example of ENV_EXAMPLES) {
      for (const p of environmentView(example).profiles) {
        for (const key of [...p.evidence, ...p.warnings, ...p.actions]) expect(key in ja, key).toBe(true);
        for (const f of p.facts) if (f.value.startsWith("key:")) expect(f.value.slice(4) in ja, f.value).toBe(true);
      }
    }
  });
});
