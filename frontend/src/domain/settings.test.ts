import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { LegacyApi, LegacySettings, LegacySettingsPatch } from "../api/legacy";
import { deferred } from "../test/fakeFetch";
import { settings } from "../test/legacyFixtures";
import { isDirty, SettingsController } from "./settings";

function fakeApi(patch: (p: LegacySettingsPatch) => Promise<LegacySettings>, initial = settings()) {
  const patches: LegacySettingsPatch[] = [];
  const api: LegacyApi = {
    getEnvironment: () => Promise.reject(new Error("unused")),
    getSettings: async () => initial,
    patchSettings: (p) => {
      patches.push(p);
      return patch(p);
    },
    openFolder: () => Promise.reject(new Error("unused")),
  };
  return { api, patches };
}

describe("SettingsController (theme/language slice)", () => {
  it("sends only changed fields and refuses a double submit while pending", async () => {
    const gate = deferred<LegacySettings>();
    const { api, patches } = fakeApi(() => gate.promise);
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

  it("a failed save keeps the draft, shows the error and can be retried explicitly", async () => {
    let fail = true;
    const { api, patches } = fakeApi(async (p) => {
      if (fail) throw new ApiError({ kind: "server", status: 500 });
      return settings(p);
    });
    const controller = new SettingsController(api);
    await controller.load();
    controller.edit({ theme: "dark" });
    expect(await controller.save()).toBe(false);
    let state = controller.store.get();
    expect(state.save.phase).toBe("error");
    expect(state.draft?.theme).toBe("dark");
    expect(state.saved?.theme).toBe("system"); // the applied theme is not changed by a failure
    expect(patches).toHaveLength(1); // nothing is resent automatically
    fail = false;
    expect(await controller.save()).toBe(true);
    state = controller.store.get();
    expect(state.saved?.theme).toBe("dark");
    expect(patches).toHaveLength(2);
  });

  it("nothing to save when the draft equals the server state; revert restores it", async () => {
    const { api, patches } = fakeApi(async (p) => settings(p));
    const controller = new SettingsController(api);
    await controller.load();
    expect(await controller.save()).toBe(false);
    controller.edit({ theme: "light" });
    controller.revert();
    expect(controller.store.get().draft?.theme).toBe("system");
    expect(patches).toHaveLength(0);
  });

  it("edits made while a save is in flight are kept", async () => {
    const gate = deferred<LegacySettings>();
    const { api } = fakeApi(() => gate.promise);
    const controller = new SettingsController(api);
    await controller.load();
    controller.edit({ language: "en" });
    const run = controller.save();
    controller.edit({ theme: "dark" });
    gate.resolve(settings({ language: "en" }));
    await run;
    const state = controller.store.get();
    expect(state.draft).toEqual({ theme: "dark", language: "en" });
    expect(isDirty(state)).toBe(true);
  });

  it("a reload keeps an unsaved draft", async () => {
    const { api } = fakeApi(async (p) => settings(p));
    const controller = new SettingsController(api);
    await controller.load();
    controller.edit({ language: "en" });
    await controller.load();
    expect(controller.store.get().draft?.language).toBe("en");
  });
});
