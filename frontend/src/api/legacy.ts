/**
 * Typed client for the legacy v0 `/api/*` routes the next UI may use in UI-2.
 * Contract source: src/utteran_gui/api.py and environment.py (code is the authority).
 * Only fields the UI needs are kept. Profile `path`s are dropped; the folder defaults and the
 * log folder are kept in memory only (shown on the settings page, never logged or reported).
 * Token *values* never pass through here except as the body of an explicit PUT /api/token.
 */
import { bool, record } from "./guards";
import type { HttpClient } from "./http";
import { type LegacyEnvironment, parseEnvironment } from "./legacyEnvironment";

export * from "./legacyEnvironment";

export const PROFILES = ["cpu", "cuda", "intel", "vulkan"] as const;
export type ProfileName = (typeof PROFILES)[number];
export type Theme = "system" | "dark" | "light";
export type Language = "ja" | "en";

/** The four profile names the legacy settings contract accepts; `null` = "not specified". */
export type DefaultProfile = ProfileName | null;

/** Hugging Face token state. Presence only: the API never returns the value and neither do we. */
export interface TokenState {
  configured: boolean;
  /** false = the OS credential store failed (NOT the same as "not configured"). */
  available: boolean;
  /** Portable build: the token lives in the GUI process only (`SessionTokenStore`). */
  sessionOnly: boolean;
}

export interface LogState {
  /** Server-resolved log folder; the only path the UI may pass to POST /api/open-folder. */
  dir: string | null;
  fallback: boolean;
  /** Raw subprocess logs may contain transcripts; read-only here (core config owns it). */
  rawSubprocessLogs: boolean;
}

export interface LegacySettings {
  theme: Theme;
  language: Language;
  defaultProfile: DefaultProfile;
  /** "" = not set. Free text exactly as stored (no existence/permission guarantee). */
  defaultInputDir: string;
  defaultOutputDir: string;
  token: TokenState;
  portable: boolean;
  log: LogState;
}

/** The five editable fields (wizard progress and unknown fields are never sent). */
export type EditableSettings = Pick<LegacySettings, "theme" | "language" | "defaultProfile" | "defaultInputDir" | "defaultOutputDir">;
export const EDITABLE_FIELDS = ["theme", "language", "defaultProfile", "defaultInputDir", "defaultOutputDir"] as const;
export type EditableField = (typeof EDITABLE_FIELDS)[number];

/** `undefined` = leave unchanged; `defaultProfile: null` = clear it (sent as JSON null). */
export type LegacySettingsPatch = Partial<EditableSettings>;

/** SettingsPatchPayload: folder strings are at most 4096 characters. */
export const MAX_FOLDER_LENGTH = 4096;
/** TokenPayload: 1..4096 characters. */
export const MAX_TOKEN_LENGTH = 4096;

function folder(value: unknown): string {
  return typeof value === "string" ? value.slice(0, MAX_FOLDER_LENGTH) : "";
}

export function parseSettings(raw: unknown): LegacySettings {
  const r = record(raw, "settings");
  const theme = r.theme === "dark" || r.theme === "light" || r.theme === "system" ? r.theme : null;
  const language = r.language === "ja" || r.language === "en" ? r.language : null;
  if (theme === null || language === null) throw new Error("settings.theme/language");
  const profile = r.default_profile;
  const sessionOnly = r.token_session_only === true;
  return {
    theme,
    language,
    defaultProfile: (PROFILES as readonly unknown[]).includes(profile) ? (profile as ProfileName) : null,
    defaultInputDir: folder(r.default_input_dir),
    defaultOutputDir: folder(r.default_output_dir),
    token: {
      configured: r.token_configured === true,
      // Older servers lack the field; only an explicit false means "store unavailable".
      available: r.token_store_available !== false,
      sessionOnly,
    },
    portable: r.portable_distribution === true,
    log: {
      dir: typeof r.log_dir === "string" && r.log_dir !== "" ? r.log_dir : null,
      fallback: r.log_dir_fallback === true,
      rawSubprocessLogs: r.raw_subprocess_logs === true,
    },
  };
}

/**
 * GET/PUT/DELETE /api/token → `TokenStatus.to_dict()`. `backend` names and `error_message`
 * are deliberately not kept (they can describe the machine); `backend === "session"` only
 * tells that the portable in-memory store is used.
 */
export function parseTokenStatus(raw: unknown): TokenState {
  const r = record(raw, "token");
  return {
    configured: bool(r.configured, "token.configured"),
    available: bool(r.available, "token.available"),
    sessionOnly: r.backend === "session",
  };
}

export interface WizardStatus {
  firstRun: boolean;
  resumeAvailable: boolean;
  completed: boolean;
}

/** GET /api/wizard/status, read-only. Only the three flags the settings page shows. */
export function parseWizardStatus(raw: unknown): WizardStatus {
  const r = record(raw, "wizard");
  return {
    firstRun: r.first_run === true,
    resumeAvailable: r.resume_available === true,
    completed: typeof r.completed_at === "string" && r.completed_at !== "",
  };
}

/**
 * Client-side limit for GET /api/environment (UI-3 S2 §4; was 120 s in UI-2).
 * The server runs up to six profile CLI calls in sequence: the bootstrap `profiles list`,
 * `devices --json` (its own ceiling is `_DEVICES_PROBE_TIMEOUT_SECONDS` = 200 s; an uncached
 * first run measured 94.7 s with 4 of 7 probes timing out, and 7 × 20 s probe timeouts alone
 * exceed 120 s), then models / native / model path / OpenVINO lists (normally seconds).
 * 120 s therefore reported a timeout while the server could still answer, and a retry would
 * start a second probe next to the first. 240 s = the devices ceiling + 40 s for the other calls.
 * The theoretical worst case (every call at its own 60 s limit, ~500 s) is not waited for: the
 * person sees elapsed time and can stop waiting at any moment (cancel).
 */
export const ENVIRONMENT_TIMEOUT_MS = 240_000;

export interface LegacyApi {
  getEnvironment(profile: string | null, refresh: boolean, signal?: AbortSignal): Promise<LegacyEnvironment>;
  getSettings(signal?: AbortSignal): Promise<LegacySettings>;
  patchSettings(patch: LegacySettingsPatch, signal?: AbortSignal): Promise<LegacySettings>;
  getToken(signal?: AbortSignal): Promise<TokenState>;
  /** The value is used only as this request body; it is never stored, logged or echoed. */
  putToken(value: string): Promise<TokenState>;
  deleteToken(): Promise<TokenState>;
  getWizardStatus(signal?: AbortSignal): Promise<WizardStatus>;
  openFolder(path: string): Promise<void>;
}

const SNAKE: Record<EditableField, string> = {
  theme: "theme",
  language: "language",
  defaultProfile: "default_profile",
  defaultInputDir: "default_input_dir",
  defaultOutputDir: "default_output_dir",
};

/** Only the fields present in the patch; `null` stays `null` (clears the default profile). */
export function settingsPatchBody(patch: LegacySettingsPatch): Record<string, string | null> {
  const body: Record<string, string | null> = {};
  for (const field of EDITABLE_FIELDS) {
    const value = patch[field];
    if (value !== undefined) body[SNAKE[field]] = value;
  }
  return body;
}

export function createLegacyApi(http: HttpClient): LegacyApi {
  return {
    async getEnvironment(profile, refresh, signal) {
      const response = await http.request(
        {
          method: "GET",
          path: "/api/environment",
          query: { profile: profile ?? undefined, refresh: refresh ? "true" : undefined },
          timeoutMs: ENVIRONMENT_TIMEOUT_MS,
          signal,
        },
        parseEnvironment,
      );
      return response.data;
    },
    async getSettings(signal) {
      return (await http.request({ method: "GET", path: "/api/settings", signal }, parseSettings)).data;
    },
    async patchSettings(patch, signal) {
      // PATCH of fixed values is idempotent server-side (SettingsStore.update merges and keeps
      // wizard progress); still, it is only ever resent by an explicit user action.
      const body = settingsPatchBody(patch);
      return (
        await http.request({ method: "PATCH", path: "/api/settings", body, timeoutMs: 30_000, signal }, parseSettings)
      ).data;
    },
    async getToken(signal) {
      return (await http.request({ method: "GET", path: "/api/token", signal }, parseTokenStatus)).data;
    },
    async putToken(value) {
      return (await http.request({ method: "PUT", path: "/api/token", body: { token: value }, timeoutMs: 30_000 }, parseTokenStatus)).data;
    },
    async deleteToken() {
      return (await http.request({ method: "DELETE", path: "/api/token", timeoutMs: 30_000 }, parseTokenStatus)).data;
    },
    async getWizardStatus(signal) {
      return (await http.request({ method: "GET", path: "/api/wizard/status", signal }, parseWizardStatus)).data;
    },
    async openFolder(path) {
      await http.request({ method: "POST", path: "/api/open-folder", body: { path } }, () => null);
    },
  };
}
