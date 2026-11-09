import { describe, expect, it } from "vitest";
import { createFakeHost } from "../host/fake";
import snapshots from "../test/fixtures/environment-snapshots.json";
import { type Call, deferred, flush, json, scriptedFetch } from "../test/fakeFetch";
import { createCore } from "./core";
import { selectionNote } from "./environmentSummary";

const fixture = snapshots as unknown as Record<string, Record<string, unknown>>;
const raw = (key: string) => JSON.parse(JSON.stringify(fixture[key])) as Record<string, unknown>;

/** A fake server: settings in memory, environment answers from the real-service fixture. */
function server(options: { defaultProfile?: string | null; environment?: (profile: string | null, call: Call) => Response | Promise<Response> | undefined } = {}) {
  let saved: Record<string, unknown> = { theme: "system", language: "ja", default_profile: options.defaultProfile ?? null };
  const fetch = scriptedFetch((call) => {
    const url = new URL(call.url, "http://x");
    if (url.pathname === "/api/settings") {
      if (call.init.method === "PATCH") saved = { ...saved, ...(JSON.parse(String(call.init.body)) as object) };
      return json(200, saved);
    }
    if (url.pathname === "/api/environment") {
      const profile = url.searchParams.get("profile");
      const answer = options.environment?.(profile, call);
      if (answer) return () => Promise.resolve(answer);
      const key = profile === null ? "no_profile_parameter" : profile === "vulkan" ? "vulkan_not_built" : profile;
      return json(200, raw(key));
    }
    throw new Error(`unexpected ${call.init.method} ${call.url}`);
  });
  const nonGet = () => fetch.calls.filter((c) => c.init.method !== "GET").map((c) => `${c.init.method} ${c.url}`);
  const envCalls = () => fetch.calls.filter((c) => c.url.startsWith("/api/environment")).map((c) => c.url);
  return { app: createCore({ fetch, host: createFakeHost() }), fetch, nonGet, envCalls };
}

describe("S2 environment ↔ S1 settings", () => {
  it("start-up: settings first, then the saved default with refresh=false; nothing is written", async () => {
    const { app, nonGet, envCalls } = server({ defaultProfile: "cuda" });
    await app.start();
    expect(envCalls()).toEqual(["/api/environment?profile=cuda"]);
    expect(app.environment.store.get()).toMatchObject({ requestedProfile: "cuda", followsDefault: true });
    expect(nonGet()).toEqual([]);
  });

  it("a manual profile choice and a re-detect never PATCH the settings", async () => {
    const { app, nonGet, envCalls } = server({ defaultProfile: "cpu" });
    await app.start();
    await app.environment.select("intel");
    await app.environment.redetect();
    expect(envCalls()).toEqual(["/api/environment?profile=cpu", "/api/environment?profile=intel", "/api/environment?profile=intel&refresh=true"]);
    expect(nonGet()).toEqual([]);
    expect(app.settings.store.get().saved?.defaultProfile).toBe("cpu");
  });

  it("a saved default that is not built is not rewritten; the panel reports what the server did", async () => {
    const { app, nonGet } = server({ defaultProfile: "vulkan" });
    await app.start();
    const env = app.environment.store.get();
    expect(env.data?.activeProfile).toBeNull();
    expect(selectionNote(env)).toEqual({ kind: "requested_unavailable", requested: "vulkan" });
    expect(app.settings.store.get().saved?.defaultProfile).toBe("vulkan");
    expect(nonGet()).toEqual([]);
  });

  it("'follow the default' uses the saved default, not an unsaved draft", async () => {
    const { app, envCalls } = server({ defaultProfile: "cpu" });
    await app.start();
    await app.environment.select("intel");
    app.settings.edit({ defaultProfile: "cuda" }); // draft only
    await app.environment.selectDefault();
    expect(envCalls().at(-1)).toBe("/api/environment?profile=cpu");
  });

  it("saving settings while a manual selection is loading does not roll that selection back", async () => {
    const slow = deferred<Response>();
    const { app, envCalls } = server({ defaultProfile: "cpu", environment: (profile) => (profile === "intel" ? slow.promise : undefined) });
    await app.start();
    const run = app.environment.select("intel");
    app.settings.edit({ defaultProfile: "cuda" });
    await app.settings.save();
    await flush();
    slow.resolve(json(200, raw("intel")));
    await run;
    expect(app.environment.store.get()).toMatchObject({ requestedProfile: "intel", followsDefault: false });
    expect(app.environment.store.get().data?.activeProfile).toBe("intel");
    expect(envCalls()).toEqual(["/api/environment?profile=cpu", "/api/environment?profile=intel"]);
  });

  it("while following the default, saving a new default follows it once (no refresh)", async () => {
    const { app, envCalls } = server({ defaultProfile: "cpu" });
    await app.start();
    app.settings.edit({ defaultProfile: "cuda" });
    await app.settings.save();
    await flush();
    await flush();
    expect(envCalls()).toEqual(["/api/environment?profile=cpu", "/api/environment?profile=cuda"]);
  });

  it("an environment failure leaves settings loaded and editable", async () => {
    const { app } = server({ defaultProfile: "cpu", environment: () => json(500, { detail: "boom" }) });
    await app.start();
    expect(app.environment.store.get().load.phase).toBe("error");
    expect(app.settings.store.get().saved?.defaultProfile).toBe("cpu");
    app.settings.edit({ theme: "dark" });
    await app.settings.save();
    expect(app.settings.store.get().saved?.theme).toBe("dark");
  });
});
