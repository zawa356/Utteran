import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { LegacySettings, LegacySettingsPatch } from "../api/legacy";
import { createFakeHost } from "../host/fake";
import type { HostBridge } from "../host";
import { HostUnavailableError } from "../host";
import { deferred } from "../test/fakeFetch";
import { settings, unusedApi } from "../test/legacyFixtures";
import { diff, isDirty, rebase, SettingsController } from "./settings";

/** A fake server: PATCH merges into `server` like SettingsStore.update (unknown/wizard fields kept). */
function fakeApi(options: { patch?: (p: LegacySettingsPatch) => Promise<LegacySettings>; initial?: LegacySettings } = {}) {
  let server = options.initial ?? settings();
  const patches: LegacySettingsPatch[] = [];
  let gets = 0;
  const api = unusedApi({
    getSettings: async () => {
      gets += 1;
      return server;
    },
    patchSettings: (p) => {
      patches.push(p);
      if (options.patch) return options.patch(p);
      server = { ...server, ...p };
      return Promise.resolve(server);
    },
  });
  return {
    api,
    patches,
    get gets() {
      return gets;
    },
    set server(value: LegacySettings) {
      server = value;
    },
    get server() {
      return server;
    },
  };
}

describe("SettingsController — five settings via PATCH /api/settings", () => {
  it("sends only changed fields and refuses a double submit while pending", async () => {
    const gate = deferred<LegacySettings>();
    const { api, patches } = fakeApi({ patch: () => gate.promise });
    const controller = new SettingsController(api);
    await controller.load();
    controller.edit({ language: "en" });
    const first = controller.save();
    expect(await controller.save()).toBe(false);
    expect(patches).toEqual([{ language: "en" }]);
    expect(controller.store.get().save.phase).toBe("pending");
    gate.resolve(settings({ language: "en" }));
    expect(await first).toBe(true);
    expect(controller.store.get().saved?.language).toBe("en");
    expect(isDirty(controller.store.get())).toBe(false);
  });

  it("diff covers all five fields; null profile and empty folders are real values", async () => {
    const fake = fakeApi({ initial: settings({ defaultProfile: "cuda", defaultInputDir: "in", defaultOutputDir: "out" }) });
    const controller = new SettingsController(fake.api);
    await controller.load();
    controller.edit({ theme: "dark", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "out2" });
    expect(diff(controller.store.get().draft!, controller.store.get().saved!)).toEqual({
      theme: "dark",
      defaultProfile: null,
      defaultInputDir: "",
      defaultOutputDir: "out2",
    });
    expect(await controller.save()).toBe(true);
    expect(fake.patches).toEqual([{ theme: "dark", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "out2" }]);
    expect(fake.server).toMatchObject({ defaultProfile: null, defaultInputDir: "", defaultOutputDir: "out2" });
  });

  it("theme/language alone still save as before (no regression of the UI-2 slice)", async () => {
    const fake = fakeApi();
    const controller = new SettingsController(fake.api);
    await controller.load();
    controller.edit({ theme: "light" });
    await controller.save();
    expect(fake.patches).toEqual([{ theme: "light" }]);
  });

  it("a failed save keeps the draft, does not apply the theme, and is only retried explicitly", async () => {
    let fail = true;
    const fake = fakeApi({
      patch: async (p) => {
        if (fail) throw new ApiError({ kind: "unavailable", status: 503, retryable: true });
        return settings(p);
      },
    });
    const controller = new SettingsController(fake.api);
    await controller.load();
    controller.edit({ theme: "dark", defaultInputDir: "in" });
    expect(await controller.save()).toBe(false);
    let state = controller.store.get();
    expect(state.save.phase).toBe("error");
    expect(state.draft).toMatchObject({ theme: "dark", defaultInputDir: "in" });
    expect(state.saved?.theme).toBe("system");
    expect(fake.patches).toHaveLength(1);
    fail = false;
    expect(await controller.save()).toBe(true);
    state = controller.store.get();
    expect(state.saved?.theme).toBe("dark");
    expect(fake.patches).toHaveLength(2);
  });

  it("nothing to save when the draft equals the server state; revert restores every field", async () => {
    const fake = fakeApi({ initial: settings({ defaultProfile: "intel", defaultOutputDir: "out" }) });
    const controller = new SettingsController(fake.api);
    await controller.load();
    expect(await controller.save()).toBe(false);
    controller.edit({ theme: "light", defaultProfile: null, defaultOutputDir: "" });
    controller.revert();
    expect(controller.store.get().draft).toEqual({ theme: "system", language: "ja", defaultProfile: "intel", defaultInputDir: "", defaultOutputDir: "out" });
    expect(fake.patches).toHaveLength(0);
  });

  it("edits made while a save is in flight are kept", async () => {
    const gate = deferred<LegacySettings>();
    const { api } = fakeApi({ patch: () => gate.promise });
    const controller = new SettingsController(api);
    await controller.load();
    controller.edit({ language: "en" });
    const run = controller.save();
    controller.edit({ theme: "dark", defaultOutputDir: "typed-meanwhile" });
    gate.resolve(settings({ language: "en" }));
    await run;
    const state = controller.store.get();
    expect(state.draft).toMatchObject({ theme: "dark", language: "en", defaultOutputDir: "typed-meanwhile" });
    expect(isDirty(state)).toBe(true);
  });

  it("a reload keeps unsaved edits and takes other fields from the server (no stale resend)", async () => {
    const fake = fakeApi();
    const controller = new SettingsController(fake.api);
    await controller.load();
    controller.edit({ language: "en" });
    // Meanwhile the legacy GUI saved another field.
    fake.server = settings({ defaultInputDir: "set-in-legacy" });
    await controller.load();
    expect(controller.store.get().draft).toMatchObject({ language: "en", defaultInputDir: "set-in-legacy" });
    await controller.save();
    expect(fake.patches).toEqual([{ language: "en" }]);
  });

  it("a slow reload that answers after a save does not overwrite the saved answer", async () => {
    const slow = deferred<LegacySettings>();
    let first = true;
    const fake = fakeApi();
    const api = { ...fake.api, getSettings: () => (first ? ((first = false), Promise.resolve(settings())) : slow.promise) };
    const controller = new SettingsController(api);
    await controller.load();
    const reload = controller.load();
    controller.edit({ defaultProfile: "cpu" });
    expect(await controller.save()).toBe(true);
    slow.resolve(settings());
    await reload;
    expect(controller.store.get().saved?.defaultProfile).toBe("cpu");
    expect(controller.store.get().load.phase).toBe("success");
  });

  it("timeout = unknown outcome: no resend, and a GET re-check compares the sent fields", async () => {
    const fake = fakeApi({
      patch: async () => {
        throw new ApiError({ kind: "timeout", outcomeUnknown: true, retryable: true });
      },
    });
    const controller = new SettingsController(fake.api);
    await controller.load();
    controller.edit({ defaultProfile: "vulkan" });
    expect(await controller.save()).toBe(false);
    expect(controller.store.get().save.error?.outcomeUnknown).toBe(true);
    expect(controller.store.get().unconfirmedSave?.patch).toEqual({ defaultProfile: "vulkan" });
    // The server had actually applied it.
    fake.server = settings({ defaultProfile: "vulkan" });
    expect(await controller.reconcile()).toEqual({ matched: ["defaultProfile"], mismatched: [] });
    let state = controller.store.get();
    expect(state.saved?.defaultProfile).toBe("vulkan");
    expect(isDirty(state)).toBe(false);
    expect(state.unconfirmedSave).toBeNull();
    expect(fake.patches).toHaveLength(1);

    controller.edit({ defaultProfile: "cpu" });
    expect(await controller.save()).toBe(false);
    expect(await controller.reconcile()).toEqual({ matched: [], mismatched: ["defaultProfile"] });
    state = controller.store.get();
    expect(state.draft?.defaultProfile).toBe("cpu");
    expect(state.save.phase).toBe("idle");
    expect(fake.patches).toHaveLength(2);
  });

  it("refuses folders longer than the server limit instead of sending them", async () => {
    const fake = fakeApi();
    const controller = new SettingsController(fake.api);
    await controller.load();
    controller.edit({ defaultInputDir: "x".repeat(4097) });
    expect(await controller.save()).toBe(false);
    expect(fake.patches).toHaveLength(0);
  });

  it("rebase keeps edited fields and follows the server for untouched ones", () => {
    const before = settings();
    const after = settings({ theme: "dark", defaultOutputDir: "srv" });
    const draft = { theme: "system", language: "en", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "" } as const;
    expect(rebase(draft, before, after)).toEqual({ theme: "dark", language: "en", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "srv" });
  });
});

describe("SettingsController — native folder chooser (fake host)", () => {
  async function ready() {
    const fake = fakeApi({ initial: settings({ defaultInputDir: "kept-in", defaultOutputDir: "kept-out" }) });
    const controller = new SettingsController(fake.api);
    await controller.load();
    return { fake, controller };
  }

  it("asks for input_folder / output_folder and changes only the draft", async () => {
    const { fake, controller } = await ready();
    const host = createFakeHost();
    host.nextPick = "picked-in";
    await controller.pickFolder("defaultInputDir", host);
    host.nextPick = "picked-out";
    await controller.pickFolder("defaultOutputDir", host);
    expect(host.picks).toEqual(["input_folder", "output_folder"]);
    const state = controller.store.get();
    expect(state.draft).toMatchObject({ defaultInputDir: "picked-in", defaultOutputDir: "picked-out" });
    expect(state.saved).toMatchObject({ defaultInputDir: "kept-in", defaultOutputDir: "kept-out" });
    expect(fake.patches).toHaveLength(0);
    controller.revert();
    expect(controller.store.get().draft).toMatchObject({ defaultInputDir: "kept-in", defaultOutputDir: "kept-out" });
  });

  it("cancel (null) is a no-op and never clears the value", async () => {
    const { controller } = await ready();
    const host = createFakeHost();
    host.nextPick = null;
    await controller.pickFolder("defaultOutputDir", host);
    expect(controller.store.get().draft?.defaultOutputDir).toBe("kept-out");
    expect(controller.store.get().pickError).toBeNull();
  });

  it("no host → an explicit host_unavailable state; other failures → failed", async () => {
    const { controller } = await ready();
    const missing: HostBridge = { ...createFakeHost(), pickPath: () => Promise.reject(new HostUnavailableError()) };
    await controller.pickFolder("defaultInputDir", missing);
    expect(controller.store.get().pickError).toEqual({ field: "defaultInputDir", reason: "host_unavailable" });
    expect(controller.store.get().picking).toBeNull();
    const broken: HostBridge = { ...createFakeHost(), pickPath: () => Promise.reject(new Error("synthetic")) };
    await controller.pickFolder("defaultOutputDir", broken);
    expect(controller.store.get().pickError).toEqual({ field: "defaultOutputDir", reason: "failed" });
    expect(controller.store.get().draft?.defaultInputDir).toBe("kept-in");
  });

  it("while a dialog is open Save and a second dialog are refused", async () => {
    const { fake, controller } = await ready();
    const gate = deferred<string | null>();
    const host: HostBridge = { ...createFakeHost(), pickPath: () => gate.promise };
    controller.edit({ theme: "dark" });
    const picking = controller.pickFolder("defaultInputDir", host);
    expect(controller.store.get().picking).toBe("defaultInputDir");
    expect(await controller.save()).toBe(false);
    await controller.pickFolder("defaultOutputDir", host);
    expect(fake.patches).toHaveLength(0);
    gate.resolve("picked");
    await picking;
    expect(await controller.save()).toBe(true);
    expect(fake.patches).toEqual([{ theme: "dark", defaultInputDir: "picked" }]);
  });
});
