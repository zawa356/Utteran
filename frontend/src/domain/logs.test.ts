import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { LegacyEnvironment } from "../api/legacy";
import { createFakeHost } from "../host/fake";
import { json, scriptedFetch } from "../test/fakeFetch";
import { environment, settings, unusedApi } from "../test/legacyFixtures";
import { createCore } from "./core";
import { LogFolderController } from "./logs";
import { WizardStatusController, wizardSummary } from "./wizard";

describe("LogFolderController", () => {
  it("opens exactly the server log_dir; nothing without one", async () => {
    const opened: string[] = [];
    const logs = new LogFolderController(unusedApi({ openFolder: async (path) => void opened.push(path) }));
    expect(await logs.open(null)).toBe(false);
    expect(await logs.open("")).toBe(false);
    expect(await logs.open(settings().log.dir)).toBe(true);
    expect(opened).toEqual(["synthetic-logs"]);
    expect(logs.store.get().open.phase).toBe("success");
  });

  it("a failure (400 missing / 503 no file manager) is shown and can be tried again", async () => {
    let status = 503;
    const logs = new LogFolderController(
      unusedApi({
        openFolder: async () => {
          if (status) throw new ApiError({ kind: status === 400 ? "validation" : "unavailable", status });
        },
      }),
    );
    expect(await logs.open("synthetic-logs")).toBe(false);
    expect(logs.store.get().open.error?.status).toBe(503);
    status = 400;
    expect(await logs.open("synthetic-logs")).toBe(false);
    expect(logs.store.get().open.error?.status).toBe(400);
    status = 0;
    expect(await logs.open("synthetic-logs")).toBe(true);
  });
});

describe("WizardStatusController", () => {
  it("reads GET /api/wizard/status only (no start / B8 setup)", async () => {
    const fetch = scriptedFetch((call) => {
      if (call.url === "/api/settings") return json(200, { theme: "system", language: "ja" });
      if (call.url === "/api/wizard/status") return json(200, { first_run: false, resume_available: true, completed_at: null });
      return json(200, { profiles: [], active_profile: null, options: { asr: [], diarization: [] }, errors: [] });
    });
    const core = createCore({ fetch, host: createFakeHost() });
    await core.start();
    await core.wizard.load();
    expect(wizardSummary(core.wizard.store.get().data!)).toBe("resumable");
    const urls = fetch.calls.map((c) => `${c.init.method} ${c.url.split("?")[0]}`);
    expect(urls).not.toContain("POST /api/wizard/start");
    expect(urls.filter((u) => !u.startsWith("GET "))).toEqual([]);
    expect(urls.some((u) => u.startsWith("GET /api/v1"))).toBe(false);
  });

  it("summaries", () => {
    expect(wizardSummary({ firstRun: false, resumeAvailable: false, completed: true })).toBe("completed");
    expect(wizardSummary({ firstRun: true, resumeAvailable: false, completed: false })).toBe("not_started");
    void WizardStatusController;
  });
});

describe("AppCore start-up and the saved default profile", () => {
  function core(defaultProfile: string | null) {
    const asked: (string | null)[] = [];
    const fetch = scriptedFetch((call) => {
      const url = new URL(call.url, "http://x");
      if (url.pathname === "/api/settings" && call.init.method === "GET") return json(200, { theme: "system", language: "ja", default_profile: defaultProfile });
      if (url.pathname === "/api/settings") return json(200, { theme: "system", language: "ja", ...JSON.parse(String(call.init.body)) });
      const profile = url.searchParams.get("profile");
      asked.push(profile);
      expect(url.searchParams.get("refresh")).toBeNull(); // never an implicit re-detection
      const env: LegacyEnvironment = environment(profile ?? "cpu");
      return json(200, {
        profiles: env.profiles,
        active_profile: env.activeProfile,
        options: { asr: [], diarization: [] },
        errors: [],
      });
    });
    return { app: createCore({ fetch, host: createFakeHost() }), asked };
  }

  it("start-up shows the saved default profile, like the legacy GUI boot", async () => {
    const { app, asked } = core("cuda");
    await app.start();
    expect(asked).toEqual(["cuda"]);
    expect(app.environment.store.get().requestedProfile).toBe("cuda");
  });

  it("saving a new default follows on the panel only if it was following the default", async () => {
    const { app, asked } = core(null);
    await app.start();
    app.settings.edit({ defaultProfile: "intel" });
    await app.settings.save();
    await new Promise((r) => setTimeout(r, 0));
    expect(asked).toEqual([null, "intel"]);
    // A profile chosen on the panel is kept when the default changes again.
    await app.environment.select("cpu");
    app.settings.edit({ defaultProfile: "cuda" });
    await app.settings.save();
    await new Promise((r) => setTimeout(r, 0));
    expect(asked).toEqual([null, "intel", "cpu"]);
    expect(app.environment.store.get().requestedProfile).toBe("cpu");
  });
});
