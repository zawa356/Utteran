import {
  ASR_BACKENDS,
  CONTRACT_PATH,
  cloneValue,
  COMPUTE_TYPES,
  DIARIZATION_DEVICES,
  DIARIZATION_MODELS,
  engineCandidates,
  LANGUAGES,
  MEMORY_GUARDS,
  NEWLINES,
  OUTPUT_FORMATS,
  type ProcessingSettings,
  SETTING_KEYS,
  type SettingKey,
  sameValue,
  SNAP_UNITS,
  validateSettings,
  WORD_TIMESTAMPS,
  DEFAULT_COMMON,
  DTW_OPTIONS,
  SPEAKER_MODES,
} from "./settingsSchema";

/**
 * Processing-settings presets of the prototype (iteration 4, owner memo #20). In memory only: no
 * file picker, no download, no B5 Preset CRUD. A preset holds a PART of the settings; applying it
 * changes exactly those keys.
 *
 * Exchange format: a versioned JSON DOCUMENT is a design CANDIDATE only (`format: "utteran-preset",
 * format_version: 1`, settings in the B5 contract's nested names). It is not a public contract and
 * must not be announced as compatible with anything. Device choice, tokens and PC paths are never
 * part of it; prompt and glossary are left out unless the user explicitly includes them.
 */
export interface PresetDef {
  id: string;
  /** User presets carry their name; built-ins are translated through `nameKey`. */
  name: string;
  nameKey: string | null;
  builtIn: boolean;
  values: Partial<ProcessingSettings>;
}

export const BUILTIN_PRESETS: readonly PresetDef[] = [
  { id: "builtin-standard", name: "", nameKey: "preset.builtin.standard", builtIn: true, values: { language: "ja", diarization: true, speakerMode: "auto", formats: ["srt", "json"] } },
  { id: "builtin-meeting", name: "", nameKey: "preset.builtin.meeting", builtIn: true, values: { language: "auto", diarization: true, showSpeaker: true, formats: ["txt", "json"] } },
  { id: "builtin-subtitles", name: "", nameKey: "preset.builtin.subtitles", builtIn: true, values: { language: "ja", diarization: false, wordTimestamps: "always", formats: ["srt", "vtt"] } },
];

/** One synthetic user preset (with a glossary and a prompt, to show the export choice). */
export function initialUserPresets(): PresetDef[] {
  return [
    {
      id: "user-01",
      name: "定例会議（用語集つき）",
      nameKey: null,
      builtIn: false,
      values: {
        language: "ja",
        diarization: true,
        speakerMode: "range",
        minSpeakers: 2,
        maxSpeakers: 6,
        formats: ["md", "json"],
        initialPrompt: "社内の定例会議の録音です。",
        glossary: ["Utteran", "議事録", "アクションアイテム"],
      },
    },
  ];
}

export function presetKeys(preset: Pick<PresetDef, "values">): SettingKey[] {
  return SETTING_KEYS.filter((k) => k in preset.values);
}

/** Keys whose value would change if the preset were applied on top of `base`. */
export function presetDiff(base: ProcessingSettings, preset: Pick<PresetDef, "values">): SettingKey[] {
  return presetKeys(preset).filter((k) => !sameValue(k, base[k], preset.values[k]));
}

export function hasPrivateText(preset: Pick<PresetDef, "values">): boolean {
  return Boolean(preset.values.initialPrompt) || (preset.values.glossary?.length ?? 0) > 0;
}

/** A name not used yet: "name", "name (2)", "name (3)", … */
export function uniqueName(name: string, taken: readonly string[]): string {
  if (!taken.includes(name)) return name;
  for (let i = 2; ; i += 1) if (!taken.includes(`${name} (${i})`)) return `${name} (${i})`;
}

// ---- exchange document (design candidate) ----------------------------------------------------
export const PRESET_FORMAT = "utteran-preset";
export const PRESET_FORMAT_VERSION = 1;
/** Keys that are never written to an exchange document (PC-specific). */
const NEVER_EXPORTED: readonly SettingKey[] = ["device"];
const PRIVATE_TEXT: readonly SettingKey[] = ["initialPrompt", "glossary"];
/** Speaker counts travel as one consistent group (num XOR min/max). */
const SPEAKER_KEYS: readonly SettingKey[] = ["speakerMode", "numSpeakers", "minSpeakers", "maxSpeakers"];

function setPath(target: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split(".");
  let node = target;
  for (const part of parts.slice(0, -1)) {
    if (typeof node[part] !== "object" || node[part] === null) node[part] = {};
    node = node[part] as Record<string, unknown>;
  }
  node[parts[parts.length - 1]!] = value;
}

export interface ExportResult {
  fileName: string;
  json: string;
  /** Keys present in the preset but not written. */
  omitted: SettingKey[];
}

export function exportPreset(preset: PresetDef, displayName: string, opts: { includePrivateText: boolean }): ExportResult {
  const settings: Record<string, unknown> = {};
  const omitted: SettingKey[] = [];
  const keys = presetKeys(preset);
  for (const key of keys) {
    if (NEVER_EXPORTED.includes(key) || (!opts.includePrivateText && PRIVATE_TEXT.includes(key))) {
      omitted.push(key);
      continue;
    }
    if (SPEAKER_KEYS.includes(key)) continue;
    const path = CONTRACT_PATH[key];
    if (path) setPath(settings, path, cloneValue(preset.values[key]));
  }
  if (keys.some((k) => SPEAKER_KEYS.includes(k))) {
    const merged = { ...DEFAULT_COMMON, ...preset.values };
    setPath(settings, "diarization.num_speakers", merged.speakerMode === "fixed" ? merged.numSpeakers : null);
    setPath(settings, "diarization.min_speakers", merged.speakerMode === "range" ? merged.minSpeakers : null);
    setPath(settings, "diarization.max_speakers", merged.speakerMode === "range" ? merged.maxSpeakers : null);
  }
  const doc = { format: PRESET_FORMAT, format_version: PRESET_FORMAT_VERSION, name: displayName, settings };
  const stem = displayName.replace(/[\\/:*?"<>|\s]+/g, "-").replace(/^-+|-+$/g, "") || "preset";
  return { fileName: `${stem}.utteran-preset.json`, json: JSON.stringify(doc, null, 2), omitted };
}

export type ImportIssueCode = "notJson" | "notPreset" | "version" | "unknownField" | "badValue" | "secretField" | "invalid" | "noName";
export interface ImportIssue {
  code: ImportIssueCode;
  params: Record<string, string | number>;
}
export interface ImportResult {
  ok: boolean;
  name: string | null;
  values: Partial<ProcessingSettings>;
  issues: ImportIssue[];
  /** Non-blocking notes (e.g. a model this PC does not list). */
  warnings: ImportIssue[];
}

const ENUMS: Partial<Record<SettingKey, readonly unknown[]>> = {
  backend: ASR_BACKENDS,
  language: LANGUAGES,
  computeType: COMPUTE_TYPES,
  wordTimestamps: WORD_TIMESTAMPS,
  diarizationModel: DIARIZATION_MODELS,
  diarizationDevice: DIARIZATION_DEVICES,
  memoryGuard: MEMORY_GUARDS,
  newline: NEWLINES,
  boundarySnapUnit: SNAP_UNITS,
  wcDtw: DTW_OPTIONS,
  speakerMode: SPEAKER_MODES,
};
const SECRET = /token|password|secret|api[_-]?key|path|dir/i;

function flatten(node: unknown, prefix: string, out: Map<string, unknown>): void {
  if (node !== null && typeof node === "object" && !Array.isArray(node)) {
    for (const [k, v] of Object.entries(node)) flatten(v, prefix ? `${prefix}.${k}` : k, out);
  } else out.set(prefix, node);
}

const PATH_TO_KEY = new Map<string, SettingKey>(
  (Object.entries(CONTRACT_PATH) as [SettingKey, string | null][]).filter(([k, p]) => p !== null && !SPEAKER_KEYS.includes(k)).map(([k, p]) => [p!, k]),
);

/**
 * Reads an exchange document (prototype: fixed sample texts only). Every problem is reported as
 * an issue the dialog can show; nothing is partially imported when there is an issue.
 */
export function parsePresetDocument(text: string): ImportResult {
  const fail = (code: ImportIssueCode, params: Record<string, string | number> = {}): ImportResult => ({ ok: false, name: null, values: {}, issues: [{ code, params }], warnings: [] });
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return fail("notJson");
  }
  if (typeof doc !== "object" || doc === null || (doc as Record<string, unknown>).format !== PRESET_FORMAT) return fail("notPreset");
  const d = doc as Record<string, unknown>;
  if (d.format_version !== PRESET_FORMAT_VERSION) return fail("version", { found: String(d.format_version ?? "—"), supported: PRESET_FORMAT_VERSION });
  const name = typeof d.name === "string" ? d.name.trim() : "";
  const issues: ImportIssue[] = [];
  const warnings: ImportIssue[] = [];
  if (name === "" || name.length > 120) issues.push({ code: "noName", params: {} });
  const flat = new Map<string, unknown>();
  flatten(d.settings ?? {}, "", flat);
  const values: Partial<Record<SettingKey, unknown>> = {};
  for (const [path, value] of flat) {
    if (path === "diarization.num_speakers" || path === "diarization.min_speakers" || path === "diarization.max_speakers") continue;
    const key = PATH_TO_KEY.get(path);
    if (!key) {
      issues.push({ code: SECRET.test(path) ? "secretField" : "unknownField", params: { field: path } });
      continue;
    }
    const allowed = ENUMS[key];
    const expected = typeof DEFAULT_COMMON[key];
    const shapeOk = Array.isArray(DEFAULT_COMMON[key]) ? Array.isArray(value) && value.every((v) => typeof v === "string") : typeof value === expected;
    if (!shapeOk || (allowed && !allowed.includes(value)) || (key === "formats" && !(value as string[]).every((f) => (OUTPUT_FORMATS as readonly string[]).includes(f)))) {
      issues.push({ code: "badValue", params: { field: path, value: JSON.stringify(value) } });
      continue;
    }
    values[key] = value;
  }
  const num = flat.get("diarization.num_speakers");
  const min = flat.get("diarization.min_speakers");
  const max = flat.get("diarization.max_speakers");
  if (num !== undefined || min !== undefined || max !== undefined) {
    if (typeof num === "number" && min == null && max == null) Object.assign(values, { speakerMode: "fixed", numSpeakers: num });
    else if (num == null && typeof min === "number" && typeof max === "number") Object.assign(values, { speakerMode: "range", minSpeakers: min, maxSpeakers: max });
    else if (num == null && min == null && max == null) Object.assign(values, { speakerMode: "auto" });
    else issues.push({ code: "badValue", params: { field: "diarization.num_speakers / min_speakers / max_speakers", value: "—" } });
  }
  const typed = values as Partial<ProcessingSettings>;
  if (issues.length === 0) {
    // Range and shape rules of the settings themselves (same validator as the Inspector).
    for (const issue of validateSettings({ ...DEFAULT_COMMON, ...typed })) {
      if (issue.key in typed || (issue.key.endsWith("Speakers") && "speakerMode" in typed)) {
        issues.push({ code: "invalid", params: { field: CONTRACT_PATH[issue.key] ?? issue.key, rule: issue.code } });
      }
    }
  }
  if (typed.model !== undefined) {
    const backend = typed.backend ?? DEFAULT_COMMON.backend;
    if (!engineCandidates(backend).models.includes(typed.model)) warnings.push({ code: "badValue", params: { field: "asr.model", value: typed.model } });
  }
  return { ok: issues.length === 0, name: name || null, values: issues.length === 0 ? typed : {}, issues, warnings };
}

/** Fixed sample "files" the import dialog offers instead of an OS file picker. */
export interface ImportSample {
  id: "valid" | "duplicate" | "oldVersion" | "broken" | "unsupported";
  fileName: string;
  text: string;
}

const doc = (name: string, settings: Record<string, unknown>, version: number = PRESET_FORMAT_VERSION) =>
  JSON.stringify({ format: PRESET_FORMAT, format_version: version, name, settings }, null, 2);

export const IMPORT_SAMPLES: readonly ImportSample[] = [
  {
    id: "valid",
    fileName: "interview.utteran-preset.json",
    text: doc("インタビュー（2人）", { asr: { language: "ja", beam_size: 5 }, diarization: { enabled: true, num_speakers: 2, min_speakers: null, max_speakers: null }, output: { formats: ["txt", "srt"] } }),
  },
  {
    id: "duplicate",
    fileName: "teiki-kaigi.utteran-preset.json",
    text: doc("定例会議（用語集つき）", { asr: { language: "ja" }, output: { formats: ["md"] } }),
  },
  { id: "oldVersion", fileName: "old-preset.json", text: JSON.stringify({ format: PRESET_FORMAT, format_version: 0, name: "旧形式のプリセット", language: "ja" }, null, 2) },
  { id: "broken", fileName: "broken.json", text: '{ "format": "utteran-preset", "format_version": 1, "name": "途中で切れた例", "settings": { "asr": ' },
  {
    id: "unsupported",
    fileName: "other-pc.utteran-preset.json",
    text: doc("別の環境の設定", { asr: { backend: "custom-engine", beam_size: 500 }, diarization: { hf_token: "<token>" } }),
  },
];
