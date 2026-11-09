import { describe, expect, it } from "vitest";
import { parseEnvironment } from "../api/legacyEnvironment";
import { en } from "../i18n/en";
import { ja } from "../i18n/ja";
import snapshots from "../test/fixtures/environment-snapshots.json";
import { environment } from "../test/legacyFixtures";
import { type EnvironmentState } from "./environment";
import { noticeCount, profileStatus, selectionNote, summarize } from "./environmentSummary";
import { idleOperation } from "./operation";

const fixture = snapshots as unknown as Record<string, unknown>;

function state(patch: Partial<EnvironmentState>): EnvironmentState {
  return {
    requestedProfile: null,
    refresh: false,
    load: { ...idleOperation, phase: "success" },
    data: null,
    dataFor: null,
    startedAt: null,
    readiness: "unverified",
    staleDiscarded: 0,
    abandoned: 0,
    followsDefault: false,
    ...patch,
  };
}

function answered(requested: string | null, data: ReturnType<typeof environment>, followsDefault = false): EnvironmentState {
  return state({ requestedProfile: requested, data, followsDefault, dataFor: { profile: requested, refresh: false, followsDefault, receivedAt: 0 } });
}

describe("the four summary items (legacy environment strip)", () => {
  it("nothing loaded yet: every item is 'not obtained'", () => {
    expect(summarize(null)).toEqual({
      hardware: { kind: "not_obtained" },
      profile: { kind: "not_obtained" },
      models: { kind: "not_obtained" },
      native: { kind: "not_obtained" },
    });
  });

  it("server chose no profile: profile 'none', the rest 'not obtained' (not zero)", () => {
    expect(summarize(parseEnvironment(fixture.no_profile_parameter))).toEqual({
      hardware: { kind: "not_obtained" },
      profile: { kind: "none" },
      models: { kind: "not_obtained" },
      native: { kind: "not_obtained" },
    });
  });

  it("CPU only: CPU threads, 0 installed models, native 'none'", () => {
    expect(summarize(parseEnvironment(fixture.cpu))).toEqual({
      hardware: { kind: "cpu", threads: 8 },
      profile: { kind: "value", name: "cpu" },
      models: { kind: "count", count: 0 },
      native: { kind: "none" },
    });
  });

  it("CUDA: the GPU seen by two runtimes is named once; installed count is not the candidate count", () => {
    const s = summarize(parseEnvironment(fixture.cuda));
    expect(s.hardware).toEqual({ kind: "gpu", names: ["Synthetic CUDA GPU 0"], more: 0 });
    expect(s.models).toEqual({ kind: "count", count: 3 });
  });

  it("Intel: XPU name and native variants; OpenVINO IRs are not added to installed models", () => {
    const s = summarize(parseEnvironment(fixture.intel));
    expect(s.hardware.kind).toBe("gpu");
    expect(s.native).toEqual({ kind: "variants", names: ["cpu", "openvino"] });
    expect(s.models).toEqual({ kind: "count", count: 1 });
  });

  it("devices reported without a native section: native is 'unknown', not 'none'", () => {
    const data = environment("cpu", {
      devices: {
        cpu: { logicalCores: null, physicalCores: null },
        ctranslate2Available: null,
        pytorchAvailable: null,
        accelerators: [
          { runtime: "ctranslate2_cuda", index: 0, name: "A", usable: false },
          { runtime: "pytorch_cuda", index: 0, name: "B", usable: true },
          { runtime: "pytorch_xpu", index: 0, name: "C", usable: true },
          { runtime: "pytorch_xpu", index: 1, name: "D", usable: true },
        ],
        openvino: null,
        native: null,
        probes: [],
        probeCacheHit: null,
      },
    });
    const s = summarize(data);
    expect(s.native).toEqual({ kind: "unknown" });
    expect(s.hardware).toEqual({ kind: "gpu", names: ["B", "C"], more: 1 }); // a non-usable GPU is not listed
  });
});

describe("profile status keeps presence and compatibility apart", () => {
  it.each([
    [{ exists: false, compatible: true }, "missing"],
    [{ exists: true, compatible: false }, "incompatible"],
    [{ exists: true, compatible: null }, "compat_unknown"],
    [{ exists: true, compatible: true }, "built"],
  ] as const)("%o → %s", (row, expected) => {
    expect(profileStatus({ name: "cpu", compatibilityReason: null, ...row })).toBe(expected);
  });
});

describe("requested vs. server-chosen profile", () => {
  it("the server silently used another profile: reported as a fallback", () => {
    expect(selectionNote(answered("vulkan", environment("cpu")))).toEqual({ kind: "fallback", requested: "vulkan", active: "cpu" });
  });
  it("the requested profile cannot be used and nothing else was chosen", () => {
    expect(selectionNote(answered("vulkan", environment("cpu", { activeProfile: null })))).toEqual({ kind: "requested_unavailable", requested: "vulkan" });
  });
  it("no request: the server's own choice, or a prompt to choose, or nothing built", () => {
    expect(selectionNote(answered(null, environment("cpu")))).toEqual({ kind: "server_choice", active: "cpu" });
    expect(selectionNote(answered(null, parseEnvironment(fixture.no_profile_parameter)))).toEqual({ kind: "choose" });
    const none = environment("cpu", { activeProfile: null, profiles: [{ name: "cpu", exists: false, compatible: null, compatibilityReason: null }] });
    expect(selectionNote(answered(null, none))).toEqual({ kind: "none_usable" });
  });
  it("matching request and answer: no note; a previous result: no note at all", () => {
    expect(selectionNote(answered("cpu", environment("cpu")))).toBeNull();
    const previous = answered("cpu", environment("cpu"));
    expect(selectionNote({ ...previous, requestedProfile: "cuda", load: { ...previous.load, phase: "pending" } })).toBeNull();
  });
  it("notice count does not double-count rebuild warnings", () => {
    const data = parseEnvironment(fixture.intel);
    expect(noticeCount(data)).toEqual({ issues: 1, rebuild: 1, guidance: 0 });
  });
});

describe("S2 never claims readiness", () => {
  it("no UI text says ready/available/recommended/verified-OK for the environment", () => {
    for (const catalog of [ja, en]) {
      for (const [key, text] of Object.entries(catalog)) {
        if (!key.startsWith("env.")) continue;
        // "ready" may only appear inside the explicit denial ("not \"ready\"" / "（ready）ではありません").
        const withoutDenial = text.replace(/\(not "ready"\)|（ready）ではありません/g, "");
        expect(withoutDenial, key).not.toMatch(/\bready\b|実行可能です|動作します|推奨|recommended|verified to work|利用可能です/i);
      }
    }
  });

  it("even the most complete answer stays unverified (exists, compatible, installed, candidates)", () => {
    const data = parseEnvironment(fixture.cuda);
    expect(data.activeProfile).toBe("cuda");
    expect(data.installedModelCount).toBeGreaterThan(0);
    expect(data.asr.length).toBeGreaterThan(0);
    // The DTO has no field that could express readiness.
    expect(Object.keys(data)).not.toEqual(expect.arrayContaining(["ready"]));
    expect(JSON.stringify(data)).not.toMatch(/"ready"|recommend/i);
  });
});
