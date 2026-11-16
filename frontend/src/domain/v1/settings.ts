/**
 * Pure B5 helpers for the S5-A single-job path: the explicit first Preset/Policy bodies, the
 * screen differences as B5 `SettingOverride`s and the client-side effective settings.
 *
 * - The Preset template (`presetTemplate.json`) is the legacy core default (`Config()`) in the
 *   B5 shape; `tests/test_ui3_s5a_v1_single_job.py` proves it differs only in declared leaves
 *   (diarization off for the B3 worker, explicit faster-whisper backend, no prompt).
 * - Overrides use registered absolute leaf pointers only (`/asr/language`, `/output/formats`) and
 *   never overlap. An override is produced only for a value that differs from the Preset.
 * - The effective settings are computed like B5 (`preset.settings → common → file`, leaf replace);
 *   the controller sends nothing unless the server's `POST /validations` answer is identical.
 */
import type { JsonObject, OutputFormatV1, PresetV1 } from "../../api/v1Resources";
import { OUTPUT_FORMATS_V1 } from "../../api/v1Resources";
import template from "./presetTemplate.json";

export interface SettingOverride {
  readonly path: "/asr/language" | "/output/formats";
  readonly value: string | readonly OutputFormatV1[];
}

/** The screen's differences from the selected Preset (null = inherit the Preset value). */
export interface ScreenDifferences {
  language: string | null;
  formats: readonly OutputFormatV1[] | null;
}

export const V1_LANGUAGES = ["ja", "en", "auto"] as const;
export const DEFAULT_PRESET_MODEL = "large-v3-turbo";

export interface PresetDraft {
  name: string;
  model: string;
  language: string;
  formats: readonly OutputFormatV1[];
}

export type PresetDraftIssue = "name" | "model" | "language" | "formats";

const MODEL = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/;

/** Everything that would block saving (shown on the confirmation step); empty = may be saved. */
export function presetDraftIssues(draft: PresetDraft): PresetDraftIssue[] {
  const issues: PresetDraftIssue[] = [];
  if (draft.name.trim() === "" || draft.name.length > 120) issues.push("name");
  if (!MODEL.test(draft.model)) issues.push("model");
  if (!(V1_LANGUAGES as readonly string[]).includes(draft.language)) issues.push("language");
  if (draft.formats.length === 0 || new Set(draft.formats).size !== draft.formats.length || draft.formats.some((f) => !OUTPUT_FORMATS_V1.includes(f))) issues.push("formats");
  return issues;
}

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** `ProcessingSettings` for an explicit new Preset: the template plus the person's four choices. */
export function presetSettings(draft: PresetDraft): JsonObject {
  const settings = clone(template) as JsonObject & { asr: JsonObject; output: JsonObject };
  settings.asr.model = draft.model;
  settings.asr.language = draft.language;
  settings.output.formats = [...draft.formats];
  return settings;
}

export function presetBody(draft: PresetDraft): JsonObject {
  return { schema_version: 1, name: draft.name.trim(), settings: presetSettings(draft) };
}

/** The Policy shape fixed by the S5-A instructions: automatic, CPU profile only, no fallback. */
export function policyBody(name: string): JsonObject {
  return {
    schema_version: 1,
    name: name.trim(),
    mode: "auto",
    fixed_candidate_id: null,
    allowed_profile_ids: ["cpu"],
    objective: "balanced",
    max_memory_bytes: null,
    fallback: "none",
    execution_deadline_seconds: null,
  };
}

function leaf(settings: JsonObject, path: string): unknown {
  let node: unknown = settings;
  for (const part of path.split("/").slice(1)) {
    if (typeof node !== "object" || node === null) return undefined;
    node = (node as JsonObject)[part];
  }
  return node;
}

/** Stable JSON (sorted keys) for equality checks of settings/sessions. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical((value as JsonObject)[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/** Only real differences become overrides; equal values keep inheriting the Preset. */
export function commonOverrides(preset: PresetV1, screen: ScreenDifferences): SettingOverride[] {
  const out: SettingOverride[] = [];
  if (screen.language !== null && screen.language !== leaf(preset.settings, "/asr/language")) out.push({ path: "/asr/language", value: screen.language });
  if (screen.formats !== null && canonical(screen.formats) !== canonical(leaf(preset.settings, "/output/formats"))) out.push({ path: "/output/formats", value: [...screen.formats] });
  return out;
}

/** B5 leaf inheritance on a copy: later overrides replace whole leaves (arrays are atomic). */
export function effectiveSettings(settings: JsonObject, ...layers: readonly (readonly SettingOverride[])[]): JsonObject {
  const result = clone(settings);
  for (const layer of layers) {
    for (const item of layer) {
      const parts = item.path.split("/").slice(1);
      let node = result;
      for (const part of parts.slice(0, -1)) node = node[part] as JsonObject;
      node[parts.at(-1)!] = clone(item.value);
    }
  }
  return result;
}

export interface SettingsSummary {
  backend: string;
  model: string;
  language: string;
  formats: string[];
  diarization: boolean;
  prompt: boolean;
  glossary: number;
}

export function summarize(settings: JsonObject): SettingsSummary {
  const asr = settings.asr as JsonObject;
  const output = settings.output as JsonObject;
  return {
    backend: String(asr.backend),
    model: String(asr.model),
    language: String(asr.language),
    formats: (output.formats as string[]).map(String),
    diarization: (settings.diarization as JsonObject).enabled === true,
    prompt: typeof asr.initial_prompt === "string" && asr.initial_prompt !== "",
    glossary: Array.isArray(asr.glossary) ? asr.glossary.length : 0,
  };
}

/**
 * Reasons the B3 worker (via B7) is known to refuse a Preset before any request: shown next to the
 * Preset so the person is not surprised by a 422/409. The server remains the authority.
 */
export function knownWorkerLimits(settings: JsonObject): string[] {
  const s = summarize(settings);
  const limits: string[] = [];
  if (s.backend !== "faster-whisper" && s.backend !== "auto") limits.push("worker_backend_unsupported");
  if (s.diarization) limits.push("worker_diarization_unsupported");
  if (s.glossary > 0) limits.push("worker_glossary_unsupported");
  if ((settings.asr as JsonObject).extra_arguments) limits.push("worker_arguments_unsupported");
  return limits;
}
