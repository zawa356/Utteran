import { describe, expect, it } from "vitest";
import snapshots from "../test/fixtures/environment-snapshots.json";
import { classifyIssue, LIMITS, parseEnvironment } from "./legacyEnvironment";

/** The fixture is the real `EnvironmentService.snapshot()` output (tools/ui2/synthetic_environment.py). */
type Raw = Record<string, unknown>;
const fixture = snapshots as unknown as Record<string, Raw>;
const clone = (key: string): Raw => JSON.parse(JSON.stringify(fixture[key])) as Raw;

// Synthetic machine-like values assembled at runtime (never stored as literals in the repo).
const WIN_PATH = ["C:", "Users", "synthetic-person", "AppData", "Utteran", "profiles", "cuda"].join("\\");
const TOKEN = "hf_" + "x".repeat(34);

describe("parseEnvironment — real service fixture", () => {
  it("no profile parameter with several built profiles: the server chose none", () => {
    const env = parseEnvironment(fixture.no_profile_parameter);
    expect(env.activeProfile).toBeNull();
    expect(env.devices).toBeNull();
    expect(env.installedModels).toBeNull(); // not queried by the server: "not obtained", not 0
    expect(env.installedModelCount).toBeNull();
    expect(env.nativeRunnable).toBeNull();
    expect(env.openvinoIr).toBeNull();
    expect(env.asr).toEqual([]);
    expect(env.profiles.map((p) => [p.name, p.exists, p.compatible, p.compatibilityReason])).toEqual([
      ["cpu", true, true, null],
      ["cuda", true, null, "current_lock_missing"],
      ["intel", true, false, "dependency_lock_changed"],
      ["vulkan", false, null, null],
    ]);
    expect(env.profileWarningCount).toBe(1);
  });

  it("cpu: CPU only, zero installed models and zero candidates is a normal answer", () => {
    const env = parseEnvironment(fixture.cpu);
    expect(env.activeProfile).toBe("cpu");
    expect(env.devices?.cpu).toEqual({ logicalCores: 8, physicalCores: 4 });
    expect(env.devices?.accelerators).toEqual([]);
    expect(env.installedModelCount).toBe(0);
    expect(env.asr).toEqual([]);
    expect(env.diarization).toEqual([]);
    expect(env.issues).toEqual([]);
    expect(env.devices?.native).toEqual({ built: false, variants: [] });
  });

  it("cuda: installed models are counted from models[], apart from candidate models", () => {
    const env = parseEnvironment(fixture.cuda);
    expect(env.installedModelCount).toBe(3); // 2 faster-whisper + pyannote rows with installed=true
    expect(env.installedModels?.map((m) => m.backend)).toEqual(["faster-whisper", "faster-whisper", "pyannote"]);
    const fw = env.asr.find((o) => o.id === "faster-whisper");
    expect(fw?.modelCount).toBe(2);
    expect(fw?.deviceIds).toEqual(["cpu", "cuda:0"]);
    expect(env.diarization.map((o) => o.id)).toEqual(["pyannote"]);
    expect(env.devices?.accelerators).toEqual([
      { runtime: "ctranslate2_cuda", index: 0, name: "Synthetic CUDA GPU 0", usable: true },
      { runtime: "pytorch_cuda", index: 0, name: "Synthetic CUDA GPU 0", usable: true },
    ]);
  });

  it("intel: XPU, OpenVINO, native variants and IRs; one failed call makes it partial", () => {
    const env = parseEnvironment(fixture.intel);
    expect(env.devices?.accelerators.map((a) => [a.runtime, a.usable])).toEqual([["pytorch_xpu", true]]);
    expect(env.devices?.accelerators[0]?.name).toContain("<b>XPU</b>"); // kept as text, never markup
    expect(env.devices?.openvino).toEqual({ available: true, devices: ["CPU", "GPU.0", "NPU"] });
    expect(env.devices?.native).toEqual({ built: true, variants: ["cpu", "openvino"] });
    expect(env.devices?.probes).toEqual([
      { name: "ctranslate2", status: "completed", cached: true },
      { name: "vulkan", status: "timeout", cached: false },
    ]);
    expect(env.nativeRunnable).toEqual(["cpu", "openvino"]);
    expect(env.openvinoIr).toEqual({ installed: 1, total: 2 }); // IRs are not added to the model count
    expect(env.installedModelCount).toBe(1);
    expect(env.issues).toEqual([{ kind: "failed", probe: null, seconds: null }]);
    expect(env.guidance).toEqual([]); // the legacy-form "auto selection" note is not repeated
  });

  it("a requested profile that is not built (with several built) gives no active profile", () => {
    expect(parseEnvironment(fixture.vulkan_not_built).activeProfile).toBeNull();
  });

  it("never keeps a path, the native manifest, server capability prose or raw error text", () => {
    for (const key of Object.keys(fixture)) {
      const text = JSON.stringify(parseEnvironment(fixture[key]));
      expect(text).not.toContain("synthetic-root");
      expect(text).not.toMatch(/execution_label|recommendation_reason|GPU実行可|CPU実行|manifest|xml|path/i);
      expect(text).not.toContain("CLI exited");
    }
  });
});

describe("parseEnvironment — fail closed on required fields, tolerate optional ones", () => {
  it.each([
    ["profiles missing", (r: Raw) => delete r.profiles],
    ["profile row without exists", (r: Raw) => delete (r.profiles as Raw[])[0]!.exists],
    ["options not an object", (r: Raw) => (r.options = [])],
    ["options.asr missing", (r: Raw) => delete (r.options as Raw).asr],
    ["errors missing", (r: Raw) => delete r.errors],
    ["active_profile not a string", (r: Raw) => (r.active_profile = 3)],
    ["active_profile not built", (r: Raw) => (r.active_profile = "vulkan")],
    ["active_profile unknown", (r: Raw) => (r.active_profile = "<script>")],
  ])("%s → malformed", (_name, mutate) => {
    const raw = clone("cuda");
    mutate(raw);
    expect(() => parseEnvironment(raw)).toThrow();
  });

  it("broken optional fields become unknown instead of breaking the screen", () => {
    const raw = clone("intel");
    raw.devices = { cpu: "eight", ctranslate2: null, pytorch: { xpu_devices: [null, 3, { usable: "yes" }] }, openvino: [], native: { variants: "all" } };
    raw.models = "nope";
    raw.native = { runnable: [] };
    raw.openvino_models = null;
    delete raw.profile_warnings;
    (raw.options as Raw).guidance = "text";
    const env = parseEnvironment(raw);
    expect(env.devices?.cpu).toEqual({ logicalCores: null, physicalCores: null });
    expect(env.devices?.ctranslate2Available).toBeNull();
    expect(env.devices?.accelerators).toEqual([{ runtime: "pytorch_xpu", index: null, name: "XPU ?", usable: false }]);
    expect(env.devices?.openvino).toBeNull();
    expect(env.devices?.native).toEqual({ built: null, variants: null }); // invalid variants: unknown, not "none"
    expect(env.installedModels).toBeNull();
    expect(env.installedModelCount).toBeNull();
    expect(env.nativeRunnable).toBeNull();
    expect(env.openvinoIr).toBeNull();
    expect(env.profileWarningCount).toBe(0);
    expect(env.guidance).toEqual([]);
  });

  it("one broken candidate is dropped and reported; the rest is still shown", () => {
    const raw = clone("cuda");
    (raw.options as Raw).asr = [...((raw.options as Raw).asr as unknown[]), { label: "no id" }, "x"];
    const env = parseEnvironment(raw);
    expect(env.asr.map((o) => o.id)).toEqual(["faster-whisper"]);
    expect(env.issues).toEqual([{ kind: "malformed", probe: "options", seconds: null }]);
  });

  it("caps text and list sizes, strips control characters, rejects odd identifiers", () => {
    const raw = clone("cuda");
    const devices = raw.devices as Raw;
    (devices.ctranslate2 as Raw).cuda_devices = Array.from({ length: 50 }, (_, i) => ({ index: i, name: `GPU\u0007${"x".repeat(300)}`, usable: true }));
    (devices.native as Raw).variants = { cpu: true, "bad variant!": true, ["v".repeat(80)]: true };
    const env = parseEnvironment(raw);
    expect(env.devices?.accelerators).toHaveLength(LIMITS.items);
    const name = env.devices?.accelerators[0]?.name ?? "";
    expect(name.length).toBeLessThanOrEqual(LIMITS.text);
    expect(name).not.toContain("\u0007");
    expect(env.devices?.native?.variants).toEqual(["cpu"]);
  });

  it("a GPU name carrying a token-like or path-like value is not given any meaning beyond text", () => {
    const raw = clone("cuda");
    ((raw.devices as Raw).ctranslate2 as Raw).cuda_devices = [{ index: 0, name: WIN_PATH, usable: false }];
    const env = parseEnvironment(raw);
    expect(env.devices?.accelerators[0]?.usable).toBe(false);
    raw.errors = [`Profile Python is not available: ${WIN_PATH}`, `CLI exited 1: ${TOKEN}`];
    const text = JSON.stringify(parseEnvironment(raw).issues);
    expect(text).not.toContain("synthetic-person");
    expect(text).not.toContain(TOKEN);
  });
});

describe("classifyIssue (fixed codes, never the text)", () => {
  it.each([
    ["CLI timed out after 200s: devices --json", { kind: "timeout", probe: "devices", seconds: 200 }],
    ["CLI timed out after 200s: devices --json --refresh", { kind: "timeout", probe: "devices", seconds: 200 }],
    ["CLI timed out after 60s: profiles list --json", { kind: "timeout", probe: "profiles", seconds: 60 }],
    ["CLI timed out after 60s: models list --available --all --json", { kind: "timeout", probe: "models", seconds: 60 }],
    ["CLI timed out after 60s: models list-openvino --json", { kind: "timeout", probe: "openvino_models", seconds: 60 }],
    ["CLI timed out after 60s: models path", { kind: "timeout", probe: "model_path", seconds: 60 }],
    ["CLI timed out after 60s: native status --json", { kind: "timeout", probe: "native", seconds: 60 }],
    ["CLI timed out after 60s: something else", { kind: "timeout", probe: null, seconds: 60 }],
    ["Profile is not available: cuda", { kind: "failed", probe: "profile_runtime", seconds: null }],
    ["CLI returned invalid JSON: Expecting value", { kind: "failed", probe: null, seconds: null }],
    ["[WinError 2] The system cannot find the file", { kind: "failed", probe: null, seconds: null }],
    [42, { kind: "malformed", probe: null, seconds: null }],
  ])("%s", (message, expected) => {
    expect(classifyIssue(message)).toEqual(expected);
  });
});

describe("PR #41 review P2-2: an empty model list or native report is not claimed as zero/none", () => {
  it("only `models list` failed (real service): models [] + an error → unknown, not 0", () => {
    const env = parseEnvironment(fixture.cuda_models_list_failed);
    expect(env.activeProfile).toBe("cuda");
    expect(env.installedModelsState).toBe("unknown");
    expect(env.installedModelCount).toBeNull();
    expect(env.installedModels).toBeNull();
    expect(env.issues).toEqual([{ kind: "failed", probe: null, seconds: null }]);
    expect(env.devices).not.toBeNull(); // the rest of the answer is still shown
  });

  it("models [] with no failure is a real zero", () => {
    const raw = clone("cuda");
    raw.models = [];
    const env = parseEnvironment(raw);
    expect(env.installedModelsState).toBe("counted");
    expect(env.installedModelCount).toBe(0);
  });

  it("models [] with a failure attributed to another call is still a zero; with a models timeout it is unknown", () => {
    const raw = clone("cuda");
    raw.models = [];
    raw.errors = ["CLI timed out after 200s: devices --json"];
    expect(parseEnvironment(raw).installedModelCount).toBe(0);
    raw.errors = ["CLI timed out after 60s: models list --available --all --json"];
    expect(parseEnvironment(raw).installedModelsState).toBe("unknown");
  });

  it("rows obtained: installed=true rows are counted exactly, even next to an unrelated failure", () => {
    const raw = clone("cuda");
    raw.errors = ["CLI exited 1: something"];
    const env = parseEnvironment(raw);
    expect(env.installedModelsState).toBe("counted");
    expect(env.installedModelCount).toBe(3); // 3 of 4 catalog rows; OpenVINO IRs are separate
    expect(env.openvinoIr).toEqual({ installed: 0, total: 0 });
  });

  it.each([
    ["missing", undefined, null],
    ["null", null, null],
    ["array", ["cpu"], null],
    ["string", "cpu", null],
    ["empty object", {}, []],
    ["all false", { cpu: false, vulkan: false }, []],
    ["some true", { cpu: true, vulkan: false }, ["cpu"]],
  ])("native.variants %s", (_name, variants, expected) => {
    const raw = clone("intel");
    const native = (raw.devices as Raw).native as Raw;
    if (variants === undefined) delete native.variants;
    else native.variants = variants;
    expect(parseEnvironment(raw).devices?.native?.variants).toEqual(expected);
  });
});
