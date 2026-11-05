/**
 * Processing settings of the UI-3-0 prototype (iteration 4, owner memo #19).
 *
 * The prototype keeps the settings FLAT (one key per field) so the common-default / per-file override
 * machinery stays one simple rule. `CONTRACT_PATH` maps every key to the field of the B5 contract
 * (`src/utteran_contracts/settings.py` ProcessingSettings) it stands for; `toContractShape` builds
 * that nested shape. A field existing in the contract does NOT mean the GUI can save or run it today.
 *
 * Engine candidates (`MOCK_ENGINE_CANDIDATES`) are a SYNTHETIC fixture isolated in this file: in the
 * product the backend (B4 inventory / profile resolution) returns which backend, model, device and
 * compute type are available and valid together. The UI never ships a static truth table of
 * compatibility; replacing the fixture with the backend's resolved candidates is the only change
 * needed in `engineCandidates`.
 */
export const OUTPUT_FORMATS = ["srt", "vtt", "json", "md", "txt"] as const;
export type OutputFormat = (typeof OUTPUT_FORMATS)[number];

export const LANGUAGES = ["auto", "ja", "en"] as const;
export type InputLanguage = (typeof LANGUAGES)[number];

export const ASR_BACKENDS = ["auto", "faster-whisper", "whisper-cpp", "openvino-genai"] as const;
export type AsrBackend = (typeof ASR_BACKENDS)[number];
export const COMPUTE_TYPES = ["auto", "int8", "int8_float32", "int8_float16", "float16", "float32"] as const;
export type ComputeType = (typeof COMPUTE_TYPES)[number];
export const WORD_TIMESTAMPS = ["auto", "always", "never"] as const;
export const DIARIZATION_DEVICES = ["auto", "cpu", "cuda", "xpu"] as const;
export const DIARIZATION_MODELS = ["demo-diarization-standard", "demo-diarization-light"] as const;
export const SPEAKER_MODES = ["auto", "fixed", "range"] as const;
export const MEMORY_GUARDS = ["auto", "warn", "off"] as const;
export const NEWLINES = ["lf", "crlf"] as const;
export const SNAP_UNITS = ["A", "B"] as const;
export const DTW_OPTIONS = ["off", "model"] as const;

/** Example labels only (the "auto" backend's quality tiers). */
export const DEMO_MODELS = ["demo-model-accurate", "demo-model-balanced", "demo-model-fast"] as const;

export interface ProcessingSettings {
  // 音声認識 (asr)
  backend: AsrBackend;
  model: string;
  language: InputLanguage;
  /** Execution device. Not part of ProcessingSettings in B5: the profile/policy decides (see CONTRACT_PATH). */
  device: string;
  computeType: ComputeType;
  vad: boolean;
  vadThreshold: number;
  beamSize: number;
  wordTimestamps: (typeof WORD_TIMESTAMPS)[number];
  conditionOnPreviousText: boolean;
  // プロンプト・用語集
  initialPrompt: string;
  glossary: string[];
  // 話者分離
  diarization: boolean;
  diarizationModel: (typeof DIARIZATION_MODELS)[number];
  diarizationDevice: (typeof DIARIZATION_DEVICES)[number];
  speakerMode: (typeof SPEAKER_MODES)[number];
  numSpeakers: number;
  minSpeakers: number;
  maxSpeakers: number;
  memoryGuard: (typeof MEMORY_GUARDS)[number];
  // 出力
  formats: OutputFormat[];
  showSpeaker: boolean;
  newline: (typeof NEWLINES)[number];
  srtBom: boolean;
  // 詳細: whisper.cpp
  wcThreads: number;
  wcTemperature: number;
  wcTemperatureIncrement: number;
  wcDtw: (typeof DTW_OPTIONS)[number];
  wcNoSpeechThreshold: number;
  wcEntropyThreshold: number;
  wcLogprobThreshold: number;
  // 詳細: OpenVINO GenAI
  genaiMaxNewTokens: number;
  // 詳細: 後処理
  mergeGap: number;
  speakerSwitchPenalty: number;
  boundarySnap: boolean;
  boundarySnapUnit: (typeof SNAP_UNITS)[number];
}
export type SettingKey = keyof ProcessingSettings;

/** Values from src/utteran/config.py where the core has one; the rest are prototype examples. */
export const DEFAULT_COMMON: ProcessingSettings = {
  backend: "auto",
  model: "demo-model-balanced",
  language: "ja",
  device: "auto",
  computeType: "auto",
  vad: true,
  vadThreshold: 0.5,
  beamSize: 5,
  wordTimestamps: "auto",
  conditionOnPreviousText: true,
  initialPrompt: "",
  glossary: [],
  diarization: true,
  diarizationModel: "demo-diarization-standard",
  diarizationDevice: "auto",
  speakerMode: "auto",
  numSpeakers: 2,
  minSpeakers: 1,
  maxSpeakers: 4,
  memoryGuard: "auto",
  formats: ["srt", "json"],
  showSpeaker: true,
  newline: "lf",
  srtBom: false,
  wcThreads: 0,
  wcTemperature: 0,
  wcTemperatureIncrement: 0.2,
  wcDtw: "off",
  wcNoSpeechThreshold: 0.6,
  wcEntropyThreshold: 2.4,
  wcLogprobThreshold: -1,
  genaiMaxNewTokens: 448,
  mergeGap: 0.5,
  speakerSwitchPenalty: 0.75,
  boundarySnap: true,
  boundarySnapUnit: "A",
};
export const SETTING_KEYS = Object.keys(DEFAULT_COMMON) as SettingKey[];

export function cloneSettings(s: ProcessingSettings): ProcessingSettings {
  return { ...s, formats: [...s.formats], glossary: [...s.glossary] };
}

export function cloneValue<T>(v: T): T {
  return (Array.isArray(v) ? [...v] : v) as T;
}

/** Equality per field: formats are a set, the glossary keeps its order. */
export function sameValue(key: SettingKey, a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    const x = key === "formats" ? [...a].sort() : a;
    const y = key === "formats" ? [...b].sort() : b;
    return x.length === y.length && x.every((v, i) => v === y[i]);
  }
  return a === b;
}

// ---- categories and fields ------------------------------------------------------------------
export const CATEGORIES = ["asr", "prompt", "diarization", "output", "advanced"] as const;
export type SettingsCategoryId = (typeof CATEGORIES)[number];

/**
 * Fields that are always overridden together, so a file can never inherit half of a combination
 * (e.g. a whisper.cpp backend with a faster-whisper model inherited from the common defaults, or a
 * fixed speaker count next to an inherited range). Every other key is its own group.
 */
export const GROUPS = {
  engine: ["backend", "model", "device", "computeType"],
  speakers: ["speakerMode", "numSpeakers", "minSpeakers", "maxSpeakers"],
} as const satisfies Record<string, readonly SettingKey[]>;

export function groupOf(key: SettingKey): readonly SettingKey[] {
  for (const keys of Object.values(GROUPS) as (readonly SettingKey[])[]) if (keys.includes(key)) return keys;
  return [key];
}

export type FieldKind = "select" | "switch" | "number" | "formats" | "prompt" | "glossary" | "speakers";
export interface FieldDef {
  key: SettingKey;
  category: SettingsCategoryId;
  /** Sub-heading inside the category; sections marked `folded` start collapsed. */
  section: string;
  kind: FieldKind;
  /** Number fields: bounds used by the validator and the input's attributes. */
  min?: number;
  max?: number;
  step?: number;
  integer?: boolean;
  /** Shown only when this holds for the target's effective settings. */
  visible?: (s: ProcessingSettings) => boolean;
  /** Shown but disabled (with a hint) when this holds. */
  disabled?: (s: ProcessingSettings) => boolean;
}

const isWc = (s: ProcessingSettings) => s.backend === "whisper-cpp";
const isFw = (s: ProcessingSettings) => s.backend === "faster-whisper";
const isGenai = (s: ProcessingSettings) => s.backend === "openvino-genai";
const diarOff = (s: ProcessingSettings) => !s.diarization;

export const FIELDS: readonly FieldDef[] = [
  { key: "backend", category: "asr", section: "engine", kind: "select" },
  { key: "model", category: "asr", section: "engine", kind: "select" },
  { key: "language", category: "asr", section: "engine", kind: "select" },
  { key: "device", category: "asr", section: "engine", kind: "select" },
  { key: "computeType", category: "asr", section: "engine", kind: "select", visible: isFw },
  { key: "beamSize", category: "asr", section: "decode", kind: "number", min: 1, max: 100, step: 1, integer: true },
  { key: "wordTimestamps", category: "asr", section: "decode", kind: "select" },
  { key: "conditionOnPreviousText", category: "asr", section: "decode", kind: "switch" },
  { key: "vad", category: "asr", section: "vad", kind: "switch" },
  { key: "vadThreshold", category: "asr", section: "vad", kind: "number", min: 0, max: 1, step: 0.05, visible: (s) => s.vad },

  { key: "initialPrompt", category: "prompt", section: "prompt", kind: "prompt" },
  { key: "glossary", category: "prompt", section: "prompt", kind: "glossary" },

  { key: "diarization", category: "diarization", section: "main", kind: "switch" },
  { key: "speakerMode", category: "diarization", section: "main", kind: "speakers", disabled: diarOff },
  { key: "diarizationModel", category: "diarization", section: "main", kind: "select", disabled: diarOff },
  { key: "diarizationDevice", category: "diarization", section: "main", kind: "select", disabled: diarOff },
  { key: "memoryGuard", category: "diarization", section: "memory", kind: "select", disabled: diarOff },

  { key: "formats", category: "output", section: "main", kind: "formats" },
  { key: "showSpeaker", category: "output", section: "main", kind: "switch" },
  { key: "newline", category: "output", section: "main", kind: "select" },
  { key: "srtBom", category: "output", section: "main", kind: "switch", visible: (s) => s.formats.includes("srt") },

  { key: "wcThreads", category: "advanced", section: "whisperCpp", kind: "number", min: 0, max: 64, step: 1, integer: true, visible: isWc },
  { key: "wcTemperature", category: "advanced", section: "whisperCpp", kind: "number", min: 0, max: 1, step: 0.1, visible: isWc },
  { key: "wcTemperatureIncrement", category: "advanced", section: "whisperCpp", kind: "number", min: 0, max: 1, step: 0.1, visible: isWc },
  { key: "wcDtw", category: "advanced", section: "whisperCpp", kind: "select", visible: isWc },
  { key: "wcNoSpeechThreshold", category: "advanced", section: "whisperCppThresholds", kind: "number", min: 0, max: 1, step: 0.05, visible: isWc },
  { key: "wcEntropyThreshold", category: "advanced", section: "whisperCppThresholds", kind: "number", step: 0.1, visible: isWc },
  { key: "wcLogprobThreshold", category: "advanced", section: "whisperCppThresholds", kind: "number", step: 0.1, visible: isWc },
  { key: "genaiMaxNewTokens", category: "advanced", section: "genai", kind: "number", min: 1, max: 4096, step: 1, integer: true, visible: isGenai },
  { key: "mergeGap", category: "advanced", section: "postprocessing", kind: "number", min: 0, max: 10, step: 0.1 },
  { key: "speakerSwitchPenalty", category: "advanced", section: "postprocessing", kind: "number", min: 0, max: 10, step: 0.05 },
  { key: "boundarySnap", category: "advanced", section: "postprocessing", kind: "switch" },
  { key: "boundarySnapUnit", category: "advanced", section: "postprocessing", kind: "select", visible: (s) => s.boundarySnap },
];

/** Sections that start collapsed (details behind a heading, never an endless form). */
export const FOLDED_SECTIONS = new Set(["memory", "whisperCppThresholds", "postprocessing"]);

export function fieldDef(key: SettingKey): FieldDef {
  const def = FIELDS.find((f) => f.key === key);
  if (!def) throw new Error(`no field ${key}`);
  return def;
}

export function fieldsOf(category: SettingsCategoryId): FieldDef[] {
  return FIELDS.filter((f) => f.category === category);
}

/** Keys edited in each category (incl. the speaker group, rendered by the "speakers" field). */
export function categoryKeys(category: SettingsCategoryId): SettingKey[] {
  return fieldsOf(category).flatMap((f) => (f.kind === "speakers" ? [...groupOf(f.key)] : [f.key]));
}

// ---- engine candidates (SYNTHETIC fixture; the backend owns the real answer) ----------------
export interface EngineCandidates {
  models: readonly string[];
  devices: readonly string[];
  /** null = this backend has no compute-type choice. */
  computeTypes: readonly ComputeType[] | null;
}

const MOCK_ENGINE_CANDIDATES: Record<AsrBackend, EngineCandidates> = {
  auto: { models: DEMO_MODELS, devices: ["auto"], computeTypes: null },
  "faster-whisper": { models: ["large-v3-turbo", "large-v3", "medium", "small"], devices: ["auto", "cpu", "cuda"], computeTypes: COMPUTE_TYPES },
  "whisper-cpp": { models: ["ggml-large-v3-turbo", "ggml-medium", "ggml-small"], devices: ["auto", "cpu", "cuda", "vulkan"], computeTypes: null },
  "openvino-genai": { models: ["whisper-large-v3-turbo-ov", "whisper-small-ov"], devices: ["auto", "cpu", "gpu", "npu"], computeTypes: null },
};

/** Candidates for one backend. Product: values resolved by the backend for this PC (B4). */
export function engineCandidates(backend: AsrBackend): EngineCandidates {
  return MOCK_ENGINE_CANDIDATES[backend];
}

/** Options of a select field for the given effective settings. */
export function optionsFor(key: SettingKey, s: ProcessingSettings): readonly string[] {
  const c = engineCandidates(s.backend);
  switch (key) {
    case "backend":
      return ASR_BACKENDS;
    case "model":
      return c.models;
    case "device":
      return c.devices;
    case "computeType":
      return c.computeTypes ?? ["auto"];
    case "language":
      return LANGUAGES;
    case "wordTimestamps":
      return WORD_TIMESTAMPS;
    case "diarizationModel":
      return DIARIZATION_MODELS;
    case "diarizationDevice":
      return DIARIZATION_DEVICES;
    case "memoryGuard":
      return MEMORY_GUARDS;
    case "newline":
      return NEWLINES;
    case "boundarySnapUnit":
      return SNAP_UNITS;
    case "wcDtw":
      return DTW_OPTIONS;
    default:
      return [];
  }
}

/**
 * Keeps the engine group consistent after a change: a model / device / compute type the chosen
 * backend does not offer falls back to that backend's first candidate (the UI never lets an
 * unsupported combination be selected in the first place; this covers a backend switch).
 */
export function normalizeEngine(s: ProcessingSettings): ProcessingSettings {
  const c = engineCandidates(s.backend);
  const model = c.models.includes(s.model) ? s.model : (c.models[0] ?? s.model);
  const device = c.devices.includes(s.device) ? s.device : (c.devices[0] ?? "auto");
  const computeType = c.computeTypes && !c.computeTypes.includes(s.computeType) ? "auto" : c.computeTypes ? s.computeType : "auto";
  return { ...s, model, device, computeType };
}

// ---- validation -----------------------------------------------------------------------------
export const PROMPT_MAX = 32768;
export const GLOSSARY_ITEM_MAX = 256;
export const GLOSSARY_MAX = 1000;

export type IssueCode = "range" | "integer" | "tooLong" | "duplicate" | "empty" | "order" | "tooMany" | "required" | "lineBreak";
export interface SettingIssue {
  key: SettingKey;
  code: IssueCode;
  params: Record<string, string | number>;
  /** Glossary entry index, when the issue is about one entry. */
  index?: number;
}

function checkNumber(issues: SettingIssue[], def: FieldDef, v: number): void {
  if (!Number.isFinite(v)) {
    issues.push({ key: def.key, code: "required", params: {} });
    return;
  }
  if (def.integer && !Number.isInteger(v)) issues.push({ key: def.key, code: "integer", params: {} });
  const lo = def.min ?? -Infinity;
  const hi = def.max ?? Infinity;
  if (v < lo || v > hi) issues.push({ key: def.key, code: "range", params: { min: def.min ?? "—", max: def.max ?? "—" } });
}

/** Checks of one glossary entry (also used while typing a new entry). */
export function glossaryEntryIssue(entry: string, others: readonly string[]): IssueCode | null {
  const v = entry.trim();
  if (v === "") return "empty";
  if (/[\r\n]/.test(entry)) return "lineBreak";
  if (v.length > GLOSSARY_ITEM_MAX) return "tooLong";
  if (others.some((o) => o.trim().toLowerCase() === v.toLowerCase())) return "duplicate";
  return null;
}

/**
 * Issues of one effective settings object. Only fields that apply are checked (an inactive
 * engine's fields, or speaker counts of a disabled diarization, never block a submission).
 */
export function validateSettings(s: ProcessingSettings): SettingIssue[] {
  const issues: SettingIssue[] = [];
  for (const def of FIELDS) {
    if (def.kind !== "number") continue;
    if (def.visible && !def.visible(s)) continue;
    if (def.disabled?.(s)) continue;
    checkNumber(issues, def, s[def.key] as number);
  }
  if (s.initialPrompt.length > PROMPT_MAX) issues.push({ key: "initialPrompt", code: "tooLong", params: { max: PROMPT_MAX } });
  if (s.glossary.length > GLOSSARY_MAX) issues.push({ key: "glossary", code: "tooMany", params: { max: GLOSSARY_MAX } });
  s.glossary.forEach((entry, i) => {
    const code = glossaryEntryIssue(entry, s.glossary.slice(0, i));
    if (code) issues.push({ key: "glossary", code, params: { max: GLOSSARY_ITEM_MAX }, index: i });
  });
  if (s.formats.length === 0) issues.push({ key: "formats", code: "required", params: {} });
  if (s.diarization) {
    const count = (key: "numSpeakers" | "minSpeakers" | "maxSpeakers") => {
      const v = s[key];
      if (!Number.isInteger(v) || v < 1 || v > 100) issues.push({ key, code: "range", params: { min: 1, max: 100 } });
    };
    if (s.speakerMode === "fixed") count("numSpeakers");
    if (s.speakerMode === "range") {
      count("minSpeakers");
      count("maxSpeakers");
      if (s.minSpeakers > s.maxSpeakers) issues.push({ key: "maxSpeakers", code: "order", params: {} });
    }
  }
  return issues;
}

export function issueCategory(issue: SettingIssue): SettingsCategoryId {
  if (issue.key === "numSpeakers" || issue.key === "minSpeakers" || issue.key === "maxSpeakers") return "diarization";
  return fieldDef(issue.key).category;
}

// ---- mapping to the B5 contract shape ---------------------------------------------------------
/**
 * Contract field each flat key stands for. `null` = not part of ProcessingSettings: the execution
 * device is chosen by the backend's profile/policy (B4/B5), never stored in a shared preset.
 */
export const CONTRACT_PATH: Record<SettingKey, string | null> = {
  backend: "asr.backend",
  model: "asr.model",
  language: "asr.language",
  device: null,
  computeType: "asr.faster_whisper.compute_type",
  vad: "asr.vad.enabled",
  vadThreshold: "asr.vad.threshold",
  beamSize: "asr.beam_size",
  wordTimestamps: "asr.word_timestamps",
  conditionOnPreviousText: "asr.condition_on_previous_text",
  initialPrompt: "asr.initial_prompt",
  glossary: "asr.glossary",
  diarization: "diarization.enabled",
  diarizationModel: "diarization.model",
  diarizationDevice: "diarization.device_preference",
  speakerMode: "diarization.num_speakers|min_speakers|max_speakers",
  numSpeakers: "diarization.num_speakers",
  minSpeakers: "diarization.min_speakers",
  maxSpeakers: "diarization.max_speakers",
  memoryGuard: "diarization.memory_guard",
  formats: "output.formats",
  showSpeaker: "output.show_speaker",
  newline: "output.newline",
  srtBom: "output.srt_bom",
  wcThreads: "asr.whisper_cpp.threads",
  wcTemperature: "asr.whisper_cpp.temperature",
  wcTemperatureIncrement: "asr.whisper_cpp.temperature_increment",
  wcDtw: "asr.whisper_cpp.dtw",
  wcNoSpeechThreshold: "asr.whisper_cpp.no_speech_threshold",
  wcEntropyThreshold: "asr.whisper_cpp.entropy_threshold",
  wcLogprobThreshold: "asr.whisper_cpp.logprob_threshold",
  genaiMaxNewTokens: "asr.openvino_genai.max_new_tokens",
  mergeGap: "postprocessing.merge_gap",
  speakerSwitchPenalty: "postprocessing.speaker_switch_penalty",
  boundarySnap: "postprocessing.boundary_snap_enabled",
  boundarySnapUnit: "postprocessing.boundary_snap_unit",
};

/**
 * The nested shape the backend contract uses. Only the selected backend's own section is included
 * and the speaker count is exactly one of num / min+max / none, so the conflicting combinations
 * the contract rejects cannot be produced from the UI.
 */
export function toContractShape(s: ProcessingSettings): Record<string, unknown> {
  const speakers =
    s.speakerMode === "fixed"
      ? { num_speakers: s.numSpeakers, min_speakers: null, max_speakers: null }
      : s.speakerMode === "range"
        ? { num_speakers: null, min_speakers: s.minSpeakers, max_speakers: s.maxSpeakers }
        : { num_speakers: null, min_speakers: null, max_speakers: null };
  const asr: Record<string, unknown> = {
    backend: s.backend,
    model: s.model,
    language: s.language,
    initial_prompt: s.initialPrompt === "" ? null : s.initialPrompt,
    glossary: [...s.glossary],
    beam_size: s.beamSize,
    vad: s.vad ? { enabled: true, threshold: s.vadThreshold } : { enabled: false },
    word_timestamps: s.wordTimestamps,
    condition_on_previous_text: s.conditionOnPreviousText,
  };
  if (s.backend === "faster-whisper") asr.faster_whisper = { compute_type: s.computeType };
  if (s.backend === "whisper-cpp")
    asr.whisper_cpp = {
      threads: s.wcThreads,
      temperature: s.wcTemperature,
      temperature_increment: s.wcTemperatureIncrement,
      dtw: s.wcDtw === "off" ? null : s.model,
      no_speech_threshold: s.wcNoSpeechThreshold,
      entropy_threshold: s.wcEntropyThreshold,
      logprob_threshold: s.wcLogprobThreshold,
    };
  if (s.backend === "openvino-genai") asr.openvino_genai = { max_new_tokens: s.genaiMaxNewTokens };
  return {
    asr,
    diarization: {
      enabled: s.diarization,
      backend: "pyannote",
      model: s.diarizationModel,
      device_preference: s.diarizationDevice,
      ...speakers,
      memory_guard: s.memoryGuard,
    },
    postprocessing: {
      boundary_snap_enabled: s.boundarySnap,
      boundary_snap_unit: s.boundarySnapUnit,
      merge_gap: s.mergeGap,
      speaker_switch_penalty: s.speakerSwitchPenalty,
    },
    output: { formats: [...s.formats], newline: s.newline, srt_bom: s.srtBom, show_speaker: s.showSpeaker, speaker_labels: {} },
  };
}
