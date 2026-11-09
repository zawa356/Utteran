import { describe, expect, it } from "vitest";
import { json, scriptedFetch } from "../test/fakeFetch";
import { createHttpClient } from "./http";
import { createLegacyApi, parseSettings, parseTokenStatus, parseWizardStatus, settingsPatchBody } from "./legacy";

const BASE = { theme: "dark", language: "en" };

describe("parseSettings (legacy GET/PATCH /api/settings)", () => {
  it("maps the full response and ignores unknown and wizard fields", () => {
    const parsed = parseSettings({
      ...BASE,
      default_profile: "cuda",
      default_input_dir: "in-folder",
      default_output_dir: "out-folder",
      setup_wizard_step: "model",
      setup_wizard_completed_stages: ["venv"],
      some_future_field: { nested: true },
      token_configured: true,
      token_store_available: true,
      token_store_backend: "synthetic.Backend",
      token_store_error: "",
      portable_distribution: false,
      token_session_only: false,
      log_dir: "synthetic-logs",
      log_dir_fallback: false,
      raw_subprocess_logs: false,
    });
    expect(parsed).toEqual({
      theme: "dark",
      language: "en",
      defaultProfile: "cuda",
      defaultInputDir: "in-folder",
      defaultOutputDir: "out-folder",
      token: { configured: true, available: true, sessionOnly: false },
      portable: false,
      log: { dir: "synthetic-logs", fallback: false, rawSubprocessLogs: false },
    });
    // Backend names and wizard progress are not kept in the UI model.
    expect(JSON.stringify(parsed)).not.toContain("synthetic.Backend");
    expect(JSON.stringify(parsed)).not.toContain("setup_wizard");
  });

  it("missing fields fall back safely; null profile and empty folders stay distinct", () => {
    expect(parseSettings({ ...BASE, default_profile: null, default_input_dir: "", default_output_dir: "" })).toMatchObject({
      defaultProfile: null,
      defaultInputDir: "",
      defaultOutputDir: "",
      token: { configured: false, available: true, sessionOnly: false },
      portable: false,
      log: { dir: null, fallback: false, rawSubprocessLogs: false },
    });
    expect(parseSettings({ ...BASE, default_profile: "gpu", default_input_dir: 3 })).toMatchObject({ defaultProfile: null, defaultInputDir: "" });
  });

  it("store unavailable is not 'not configured'; portable session-only, fallback and raw logs are kept", () => {
    const parsed = parseSettings({
      ...BASE,
      token_configured: false,
      token_store_available: false,
      portable_distribution: true,
      token_session_only: true,
      log_dir: "synthetic-fallback",
      log_dir_fallback: true,
      raw_subprocess_logs: true,
    });
    expect(parsed.token).toEqual({ configured: false, available: false, sessionOnly: true });
    expect(parsed.portable).toBe(true);
    expect(parsed.log).toEqual({ dir: "synthetic-fallback", fallback: true, rawSubprocessLogs: true });
  });

  it("rejects a response without a valid theme/language", () => {
    expect(() => parseSettings({ theme: "blue", language: "en" })).toThrow();
    expect(() => parseSettings(null)).toThrow();
  });
});

describe("parseTokenStatus / parseWizardStatus", () => {
  it("keeps presence only; backend names and error messages are dropped", () => {
    const parsed = parseTokenStatus({ configured: false, available: false, backend: "synthetic.Keyring", error_type: "RuntimeError", error_message: "synthetic detail" });
    expect(parsed).toEqual({ configured: false, available: false, sessionOnly: false });
    expect(parseTokenStatus({ configured: true, available: true, backend: "session" }).sessionOnly).toBe(true);
    expect(() => parseTokenStatus({ configured: "yes", available: true })).toThrow();
  });

  it("wizard status: three flags only", () => {
    expect(parseWizardStatus({ first_run: false, resume_available: false, completed_at: "2026-11-01T00:00:00Z", step: "done" })).toEqual({
      firstRun: false,
      resumeAvailable: false,
      completed: true,
    });
    expect(parseWizardStatus({ first_run: true, resume_available: true, completed_at: null })).toEqual({ firstRun: true, resumeAvailable: true, completed: false });
  });
});

describe("legacy settings/token requests", () => {
  it("PATCH sends only the given fields in snake_case; null clears the profile, '' clears a folder", () => {
    expect(settingsPatchBody({ defaultProfile: null })).toEqual({ default_profile: null });
    expect(settingsPatchBody({ defaultInputDir: "", language: "ja" })).toEqual({ default_input_dir: "", language: "ja" });
    expect(settingsPatchBody({})).toEqual({});
  });

  it("uses PATCH (never PUT) for settings and the documented token routes", async () => {
    const fetch = scriptedFetch([
      json(200, { ...BASE, default_profile: null }),
      json(200, { configured: true, available: true, backend: "x" }),
      json(200, { configured: false, available: true, backend: "x" }),
      json(200, { configured: false, available: true, backend: "x" }),
    ]);
    const api = createLegacyApi(createHttpClient({ fetch }));
    await api.patchSettings({ defaultProfile: null, defaultOutputDir: "out" });
    await api.putToken("synthetic-value");
    await api.deleteToken();
    await api.getToken();
    expect(fetch.calls.map((c) => `${c.init.method} ${c.url}`)).toEqual([
      "PATCH /api/settings",
      "PUT /api/token",
      "DELETE /api/token",
      "GET /api/token",
    ]);
    expect(fetch.calls[0]?.init.body).toBe('{"default_profile":null,"default_output_dir":"out"}');
    expect(fetch.calls[2]?.init.body).toBeUndefined();
  });
});
