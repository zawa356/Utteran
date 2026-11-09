import type { LegacyApi, LegacyEnvironment, LegacySettings, TokenState } from "../api/legacy";

/** Synthetic legacy DTOs (no real paths, names or tokens). */
export function environment(profile: string): LegacyEnvironment {
  return {
    profiles: [
      { name: "cpu", exists: true, compatible: true },
      { name: "cuda", exists: true, compatible: true },
    ],
    activeProfile: profile,
    asr: [{ id: "faster-whisper", label: "faster-whisper", modelCount: 2, deviceIds: [profile === "cuda" ? "cuda:0" : "cpu"] }],
    diarization: [],
    languageCount: 3,
    errors: [],
    warnings: [],
  };
}

export function tokenState(patch: Partial<TokenState> = {}): TokenState {
  return { configured: false, available: true, sessionOnly: false, ...patch };
}

/** `log.dir` is a synthetic relative-looking value, never a real machine path. */
export function settings(patch: Partial<LegacySettings> = {}): LegacySettings {
  return {
    theme: "system",
    language: "ja",
    defaultProfile: null,
    defaultInputDir: "",
    defaultOutputDir: "",
    token: tokenState(),
    portable: false,
    log: { dir: "synthetic-logs", fallback: false, rawSubprocessLogs: false },
    ...patch,
  };
}

/** A LegacyApi whose every call rejects unless overridden (tests list exactly what they use). */
export function unusedApi(overrides: Partial<LegacyApi> = {}): LegacyApi {
  const unused = () => Promise.reject(new Error("unused"));
  return {
    getEnvironment: unused,
    getSettings: unused,
    patchSettings: unused,
    getToken: unused,
    putToken: unused,
    deleteToken: unused,
    getWizardStatus: unused,
    openFolder: unused,
    ...overrides,
  };
}
