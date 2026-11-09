import { describe, expect, it } from "vitest";
import type { LegacyEnvironment } from "../api/legacy";
import { HostUnavailableError } from "../host";
import { createFakeHost } from "../host/fake";
import { environment, settings } from "../test/legacyFixtures";
import type { EnvironmentState } from "./environment";
import { idleOperation, type Operation } from "./operation";
import type { SettingsState } from "./settings";
import { Store } from "./store";
import { evaluate, TranscriptionFormController } from "./transcription";

const IN = ["", "synthetic", "media", "meeting.wav"].join("/");
const OUT = ["", "synthetic", "out"].join("/");

function envState(data: LegacyEnvironment | null, phase: Operation["phase"] = "success", patch: Partial<EnvironmentState> = {}): EnvironmentState {
  return {
    requestedProfile: data?.activeProfile ?? null,
    refresh: false,
    load: { ...idleOperation, phase },
    data,
    dataFor: data ? { profile: data.activeProfile, refresh: false, followsDefault: false, receivedAt: 0, savedDefault: null } : null,
    startedAt: null,
    readiness: "unverified",
    staleDiscarded: 0,
    abandoned: 0,
    followsDefault: false,
    ...patch,
  };
}

function settingsState(defaultOutputDir = ""): SettingsState {
  return {
    load: idleOperation,
    save: idleOperation,
    saved: settings({ defaultOutputDir }),
    draft: null,
    picking: null,
    pickError: null,
    unconfirmedSave: null,
    reconcile: idleOperation,
    reconciled: null,
  };
}

function harness(data: LegacyEnvironment | null = environment("cuda"), defaultOutputDir = OUT) {
  const env = new Store<EnvironmentState>(envState(data));
  const set = new Store<SettingsState>(settingsState(defaultOutputDir));
  const form = new TranscriptionFormController(env, set);
  return { env, set, form, draft: () => form.store.get().draft };
}

describe("choices come only from the current environment answer", () => {
  it("no static fallback while loading, failed, previous or without a profile", () => {
    const { form } = harness();
    form.edit({ inputPath: IN });
    const draft = form.store.get().draft;
    const block = (state: EnvironmentState) => evaluate(draft, state).issues.find((i) => i.field === "environment")?.problem;
    expect(block(envState(null, "idle"))).toBe("not_loaded");
    expect(block(envState(environment("cuda"), "pending"))).toBe("loading");
    expect(block(envState(null, "error"))).toBe("failed");
    expect(block(envState(environment("cuda"), "error"))).toBe("previous");
    // Answer for another selection than the one requested now.
    expect(block(envState(environment("cuda"), "success", { requestedProfile: "cpu" }))).toBe("previous");
    expect(block(envState(environment("cuda", { activeProfile: null })))).toBe("no_profile");
    for (const s of [envState(null, "idle"), envState(environment("cuda"), "pending")]) expect(evaluate(draft, s).options).toBeNull();
  });

  it("fills a level only when it has exactly one choice, and builds the exact request", () => {
    const { form, draft } = harness();
    // Backend and device are single choices here; the model (two) must be chosen.
    expect(draft()).toMatchObject({ asrBackend: "faster-whisper", asrModel: null, asrDevice: "cuda:0" });
    form.edit({ inputPath: IN });
    expect(form.evaluation().request).toBeNull();
    expect(form.evaluation().issues).toEqual([{ field: "asrModel", problem: "missing" }]);
    form.choose("asrModel", "synthetic-b");
    expect(form.evaluation().request).toEqual({
      inputPath: IN,
      outputDir: OUT,
      profile: "cuda",
      asrBackend: "faster-whisper",
      asrModel: "synthetic-b",
      asrDevice: "cuda:0",
      language: "ja",
      formats: ["srt", "json", "md"],
    });
  });

  it("a new environment answer removes choices it no longer offers (never keeps a stale id)", () => {
    const { env, form, draft } = harness();
    form.choose("asrModel", "synthetic-b");
    env.set(envState(environment("cpu")));
    expect(draft()).toMatchObject({ asrBackend: "faster-whisper", asrModel: "synthetic-b", asrDevice: "cpu" });
    const changed = environment("cpu");
    changed.jobOptions = { ...changed.jobOptions, asr: [{ ...changed.jobOptions.asr[0]!, models: [{ id: "synthetic-a", label: "a" }] }] };
    env.set(envState(changed));
    expect(draft().asrModel).toBe("synthetic-a"); // the only remaining choice
    env.set(envState(environment("cpu", { jobOptions: { asr: [], languages: [], formats: [], incomplete: false } })));
    expect(draft()).toMatchObject({ asrBackend: null, asrModel: null, asrDevice: null });
  });

  it("changing the backend clears model and device; formats need at least one", () => {
    const { form, draft } = harness();
    form.choose("asrModel", "synthetic-a");
    form.choose("asrBackend", "faster-whisper");
    // Model reset; the device (single choice) is the only legal value again.
    expect(draft()).toMatchObject({ asrBackend: "faster-whisper", asrModel: null, asrDevice: "cuda:0" });
    form.toggleFormat("srt", false);
    form.toggleFormat("json", false);
    form.toggleFormat("md", false);
    expect(form.evaluation().issues).toContainEqual({ field: "formats", problem: "missing" });
    form.toggleFormat("vtt", true);
    form.toggleFormat("vtt", true);
    expect(draft().formats).toEqual(["vtt"]);
  });
});

describe("output folder", () => {
  it("starts from the *saved* default once and never follows later changes after an edit", () => {
    const { set, form, draft } = harness(environment("cuda"), "");
    expect(draft()).toMatchObject({ outputDir: "", outputSource: null });
    expect(form.evaluation().issues).toContainEqual({ field: "output", problem: "empty" });
    set.set(settingsState(OUT));
    expect(draft()).toMatchObject({ outputDir: OUT, outputSource: "saved_default" });
    form.edit({ outputDir: `${OUT}/x` });
    set.set(settingsState(`${OUT}/other`));
    expect(draft()).toMatchObject({ outputDir: `${OUT}/x`, outputSource: "typed" });
  });

  it("a settings *draft* is never used (only `saved`)", () => {
    const { set, draft } = harness(environment("cuda"), "");
    set.set({ ...settingsState(""), draft: { theme: "system", language: "ja", defaultProfile: null, defaultInputDir: "", defaultOutputDir: OUT } });
    expect(draft().outputDir).toBe("");
  });
});

describe("input: picker and native drop (one file only)", () => {
  it("picker success, cancel (no change) and missing host", async () => {
    const { form, draft } = harness();
    const host = createFakeHost();
    host.nextPick = IN;
    await form.pick("input_file", host);
    expect(draft()).toMatchObject({ inputPath: IN, inputSource: "picker" });
    host.nextPick = null;
    await form.pick("input_file", host);
    expect(draft().inputPath).toBe(IN);
    expect(host.picks).toEqual(["input_file", "input_file"]);
    const broken = { ...host, pickPath: () => Promise.reject(new HostUnavailableError()) };
    await form.pick("output_folder", broken);
    expect(form.store.get().pickError).toEqual({ target: "output_folder", reason: "host_unavailable" });
    expect(draft().outputDir).toBe(OUT);
  });

  it("drops: one file is taken; several, a folder or unknown are refused as a whole", () => {
    const { form, draft } = harness();
    form.acceptDrop([
      { path: IN, kind: "file" },
      { path: `${IN}.2`, kind: "file" },
    ]);
    expect(form.store.get().dropNotice).toEqual({ kind: "multiple", count: 2 });
    expect(draft().inputPath).toBe("");
    form.acceptDrop([{ path: "/synthetic/media", kind: "folder" }]);
    expect(form.store.get().dropNotice).toEqual({ kind: "folder" });
    form.acceptDrop([{ path: IN, kind: "unknown" }]);
    expect(form.store.get().dropNotice).toEqual({ kind: "unknown" });
    form.acceptDrop([]);
    expect(form.store.get().dropNotice).toEqual({ kind: "unknown" });
    form.acceptDrop([{ path: IN, kind: "file" }]);
    const after = form.store.get();
    expect(after.draft).toMatchObject({ inputPath: IN, inputSource: "drop" });
    expect(after.dropNotice).toBeNull();
    form.acceptDrop([{ path: IN, kind: "file" }]); // the same notification twice
    expect(form.store.get()).toBe(after);
  });

  it("the fake host delivers drops to a subscriber until it unsubscribes", () => {
    const { form, draft } = harness();
    const host = createFakeHost();
    const unsubscribe = host.onDroppedPaths((paths) => form.acceptDrop(paths));
    host.drop([{ path: IN, kind: "file" }]);
    expect(draft().inputPath).toBe(IN);
    unsubscribe();
    host.drop([{ path: `${IN}.x`, kind: "file" }]);
    expect(draft().inputPath).toBe(IN);
  });

  it("nothing is written to browser storage or the URL", () => {
    const { form } = harness();
    form.edit({ inputPath: IN });
    expect(globalThis.localStorage?.length ?? 0).toBe(0);
    expect(globalThis.sessionStorage?.length ?? 0).toBe(0);
  });

  it("dispose stops following the environment and settings", () => {
    const { env, form, draft } = harness();
    form.dispose();
    env.set(envState(environment("cpu", { jobOptions: { asr: [], languages: [], formats: [], incomplete: false } })));
    expect(draft().asrBackend).toBe("faster-whisper");
  });
});
