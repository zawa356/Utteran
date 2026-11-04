/**
 * Typed client for the legacy v0 `/api/*` routes the next UI may use in UI-2.
 * Contract source: src/utteran_gui/api.py and environment.py (code is the authority).
 * Only fields the UI needs are kept; local paths (profile `path`, `log_dir`) are dropped.
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

export interface LegacySettings {
  theme: Theme;
  language: Language;
  defaultProfile: string | null;
  /** Presence only; the token value is never returned by the API nor shown. */
  tokenConfigured: boolean;
}

export type LegacySettingsPatch = Partial<Pick<LegacySettings, "theme" | "language">>;

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

export function parseSettings(raw: unknown): LegacySettings {
  const r = record(raw, "settings");
  const theme = r.theme === "dark" || r.theme === "light" || r.theme === "system" ? r.theme : null;
  const language = r.language === "ja" || r.language === "en" ? r.language : null;
  if (theme === null || language === null) throw new Error("settings.theme/language");
  return {
    theme,
    language,
    defaultProfile: optStr(r.default_profile),
    tokenConfigured: r.token_configured === true,
  };
}

/** GET /api/environment can run the device probe (~95 s worst case, UI-1 R-01). */
export const ENVIRONMENT_TIMEOUT_MS = 120_000;

export interface LegacyApi {
  getEnvironment(profile: string | null, refresh: boolean, signal?: AbortSignal): Promise<LegacyEnvironment>;
  getSettings(signal?: AbortSignal): Promise<LegacySettings>;
  patchSettings(patch: LegacySettingsPatch, signal?: AbortSignal): Promise<LegacySettings>;
  openFolder(path: string): Promise<void>;
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
      // PATCH of fixed values is idempotent server-side (SettingsStore.update); still, it is
      // only ever resent by an explicit user retry, never automatically.
      const body: Record<string, string> = {};
      if (patch.theme !== undefined) body.theme = patch.theme;
      if (patch.language !== undefined) body.language = patch.language;
      return (
        await http.request({ method: "PATCH", path: "/api/settings", body, timeoutMs: 30_000, signal }, parseSettings)
      ).data;
    },
    async openFolder(path) {
      await http.request({ method: "POST", path: "/api/open-folder", body: { path } }, () => null);
    },
  };
}
