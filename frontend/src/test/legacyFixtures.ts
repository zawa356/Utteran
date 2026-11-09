import type { LegacyApi, LegacyEnvironment, LegacySettings, TokenState } from "../api/legacy";

/** Synthetic legacy DTOs (no real paths, names or tokens). */
export function environment(profile: string, patch: Partial<LegacyEnvironment> = {}): LegacyEnvironment {
  const device = profile === "cuda" ? "cuda:0" : "cpu";
  return {
    profiles: [
      { name: "cpu", exists: true, compatible: true, compatibilityReason: null },
      { name: "cuda", exists: true, compatible: true, compatibilityReason: null },
    ],
    activeProfile: profile,
    asr: [
      {
        id: "faster-whisper",
        label: "faster-whisper",
        modelCount: 2,
        models: [
          { id: "synthetic-a", label: "synthetic a" },
          { id: "synthetic-b", label: "synthetic b" },
        ],
        deviceIds: [device],
        devices: [{ id: device, label: device }],
      },
    ],
    diarization: [],
    languageCount: 3,
    devices: null,
    installedModels: [],
    installedModelCount: 0,
    nativeRunnable: null,
    openvinoIr: null,
    guidance: [],
    issues: [],
    profileWarningCount: 0,
    ...patch,
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
