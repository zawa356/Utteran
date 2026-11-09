/**
 * Typed client for the legacy v0 `/api/*` routes the next UI may use in UI-2.
 * Contract source: src/utteran_gui/api.py and environment.py (code is the authority).
 * Only fields the UI needs are kept. Profile `path`s are dropped; the folder defaults and the
 * log folder are kept in memory only (shown on the settings page, never logged or reported).
 * Token *values* never pass through here except as the body of an explicit PUT /api/token.
 */
import { bool, list, optStr, record, str } from "./guards";
import type { HttpClient } from "./http";

export const PROFILES = ["cpu", "cuda", "intel", "vulkan"] as const;
export type ProfileName = (typeof PROFILES)[number];
export type Theme = "system" | "dark" | "light";
export type Language = "ja" | "en";

export interface LegacyProfile {
  name: string;
  exists: boolean;
  compatible: boolean | null;
}

export interface LegacyOption {
  id: string;
  label: string;
  modelCount: number;
  deviceIds: string[];
}

/**
 * `options` are what the profile CLI advertised. They are NOT B4 ready evidence
 * (G-01): the UI must present them as "detected, unverified", never as "ready".
 */
export interface LegacyEnvironment {
  profiles: LegacyProfile[];
  activeProfile: string | null;
  asr: LegacyOption[];
  diarization: LegacyOption[];
  languageCount: number;
  errors: string[];
  warnings: string[];
}

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

function option(value: unknown, where: string): LegacyOption {
  const r = record(value, where);
  const models = Array.isArray(r.models) ? r.models : [];
  const devices = Array.isArray(r.devices) ? r.devices : [];
  return {
    id: str(r.id, `${where}.id`),
    label: optStr(r.label) ?? str(r.id, `${where}.id`),
    modelCount: models.length,
    deviceIds: devices.flatMap((d) => {
      const id = typeof d === "object" && d !== null ? (d as Record<string, unknown>).id : null;
      return typeof id === "string" ? [id] : [];
    }),
  };
}

export function parseEnvironment(raw: unknown): LegacyEnvironment {
  const r = record(raw, "environment");
  const options = record(r.options, "environment.options");
  return {
    profiles: list(r.profiles, "profiles").map((p, i) => {
      const row = record(p, `profiles[${i}]`);
      return {
        name: str(row.name, `profiles[${i}].name`),
        exists: bool(row.exists, `profiles[${i}].exists`),
        compatible: typeof row.compatible === "boolean" ? row.compatible : null,
      };
    }),
    activeProfile: optStr(r.active_profile),
    asr: list(options.asr, "options.asr").map((o, i) => option(o, `asr[${i}]`)),
    diarization: list(options.diarization, "options.diarization").map((o, i) => option(o, `diarization[${i}]`)),
    languageCount: Array.isArray(options.languages) ? options.languages.length : 0,
    errors: list(r.errors, "errors").filter((e): e is string => typeof e === "string").map((e) => e.slice(0, 500)),
    warnings: (Array.isArray(r.profile_warnings) ? r.profile_warnings : [])
      .filter((e): e is string => typeof e === "string")
      .map((e) => e.slice(0, 500)),
  };
}

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

/** GET /api/environment can run the device probe (~95 s worst case, UI-1 R-01). */
export const ENVIRONMENT_TIMEOUT_MS = 120_000;

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
