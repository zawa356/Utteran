import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { LegacySettings, LegacySettingsPatch } from "../api/legacy";
import { settings, unusedApi } from "../test/legacyFixtures";
import { isDirty, SettingsController } from "./settings";

/**
 * PR #40 review P2: after a PATCH whose answer was lost, the re-check compares the fields and
 * values that PATCH sent (an immutable snapshot) with the server — not the whole current draft.
 * A match is reported as "the server now has these values", never as proof the PATCH did it.
 */
const lost = () => new ApiError({ kind: "network", outcomeUnknown: true, retryable: true });

function fake(initial: LegacySettings = settings()) {
  let server = initial;
  const patches: LegacySettingsPatch[] = [];
  const control = { apply: true, lose: true, getFails: false };
  const api = unusedApi({
    getSettings: async () => {
      if (control.getFails) throw new ApiError({ kind: "network", retryable: true });
      return server;
    },
    patchSettings: async (p) => {
      patches.push(p);
      if (control.apply) server = { ...server, ...p };
      if (control.lose) throw lost();
      return server;
    },
  });
  return {
    api,
    patches,
    control,
    set server(v: LegacySettings) {
      server = v;
    },
    get server() {
      return server;
    },
  };
}

async function loaded(f: ReturnType<typeof fake>) {
  const c = new SettingsController(f.api);
  await c.load();
  return c;
}

describe("SettingsController.reconcile — review P2 (compare the sent PATCH)", () => {
  it("lost PATCH {theme:dark} applied, then language edited → sent change matches, the language edit stays unsaved", async () => {
    const f = fake();
    const c = await loaded(f);
    c.edit({ theme: "dark" });
    expect(await c.save()).toBe(false);
    expect(c.store.get().unconfirmedSave?.patch).toEqual({ theme: "dark" });
    c.edit({ language: "en" });
    const result = await c.reconcile();
    expect(result).toEqual({ matched: ["theme"], mismatched: [] });
    const state = c.store.get();
    expect(state.reconciled).toEqual({ matched: ["theme"], mismatched: [] });
    expect(state.saved).toMatchObject({ theme: "dark", language: "ja" });
    expect(state.draft).toMatchObject({ theme: "dark", language: "en" });
    expect(isDirty(state)).toBe(true); // the later edit is still unsaved
    expect(state.unconfirmedSave).toBeNull();
    expect(f.patches).toHaveLength(1); // no resend
  });

  it("PATCH not applied → mismatch; the draft keeps the change for an explicit save", async () => {
    const f = fake();
    f.control.apply = false;
    const c = await loaded(f);
    c.edit({ defaultProfile: "cuda" });
    await c.save();
    expect(await c.reconcile()).toEqual({ matched: [], mismatched: ["defaultProfile"] });
    expect(c.store.get().draft?.defaultProfile).toBe("cuda");
    expect(f.patches).toHaveLength(1);
  });

  it("partial match across fields (another GUI changed one meanwhile)", async () => {
    const f = fake();
    const c = await loaded(f);
    c.edit({ theme: "dark", defaultOutputDir: "sent-out" });
    await c.save();
    f.server = { ...f.server, defaultOutputDir: "other-gui" };
    expect(await c.reconcile()).toEqual({ matched: ["theme"], mismatched: ["defaultOutputDir"] });
    // Untouched-since-send field: the draft still holds what was sent, so it stays dirty for a resave.
    expect(c.store.get().draft?.defaultOutputDir).toBe("sent-out");
  });

  it("null profile and empty folder are compared as values, with other edits made meanwhile", async () => {
    const f = fake(settings({ defaultProfile: "intel", defaultInputDir: "old-in" }));
    const c = await loaded(f);
    c.edit({ defaultProfile: null, defaultInputDir: "" });
    await c.save();
    expect(f.patches[0]).toEqual({ defaultProfile: null, defaultInputDir: "" });
    c.edit({ defaultOutputDir: "typed-later" });
    expect(await c.reconcile()).toEqual({ matched: ["defaultProfile", "defaultInputDir"], mismatched: [] });
    expect(c.store.get().draft).toMatchObject({ defaultProfile: null, defaultInputDir: "", defaultOutputDir: "typed-later" });
  });

  it("a failed GET keeps the unconfirmed save; Save stays refused until it is checked", async () => {
    const f = fake();
    const c = await loaded(f);
    c.edit({ theme: "light" });
    await c.save();
    f.control.getFails = true;
    expect(await c.reconcile()).toBeNull();
    expect(c.store.get().unconfirmedSave?.patch).toEqual({ theme: "light" });
    expect(c.store.get().reconcile.phase).toBe("error");
    c.edit({ language: "en" });
    expect(await c.save()).toBe(false); // a new save would hide the unconfirmed one
    expect(f.patches).toHaveLength(1);
    f.control.getFails = false;
    expect(await c.reconcile()).toEqual({ matched: ["theme"], mismatched: [] });
    f.control.lose = false;
    expect(await c.save()).toBe(true);
    expect(f.patches[1]).toEqual({ language: "en" });
  });

  it("an edit after the re-check result clears the shown result but not the draft", async () => {
    const f = fake();
    const c = await loaded(f);
    c.edit({ theme: "dark" });
    await c.save();
    await c.reconcile();
    c.edit({ language: "en" });
    expect(c.store.get().reconciled).not.toBeNull(); // the observation stays until the next save
    f.control.lose = false;
    await c.save();
    expect(c.store.get().reconciled).toBeNull();
  });
});
