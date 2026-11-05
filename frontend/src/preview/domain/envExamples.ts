/**
 * Fictional execution-environment examples for the UI-3-0 prototype (owner memo: "a mock with
 * information"). NOTHING here is detected: no GPU, driver, model or backend is queried, and the
 * values do not claim that such a configuration works. In the product the matrix, automatic
 * selection and ready decision come from the backend (B4 is the source of truth); the frontend
 * only renders what the backend returns. Product names are used as plausible labels only.
 *
 * Iteration 4 (owner memos #16/#23): "evidence" is shown as plain detection results, and what used
 * to be a list of "needed actions" in prose became actions the card offers as buttons
 * ("Set up…", "Repair…", "Set up speaker diarization…"). A detected GPU is a detection result; whether
 * a profile is READY is a separate status (never inferred from the detection alone).
 */
export type EnvExample = "gpu" | "cpu" | "unconfigured" | "repair" | "error";
export const ENV_EXAMPLES: readonly EnvExample[] = ["gpu", "cpu", "unconfigured", "repair", "error"];

/** Shown per example profile (never computed here). */
export type ProfileStatus = "ready" | "warning" | "repair" | "unavailable";

export interface EnvFact {
  /** i18n key suffix of the label (env.fact.<label>). */
  label: "asr" | "model" | "device" | "diarization" | "compute" | "runtime" | "memory" | "speed";
  /** Display value: product-like names stay untranslated; `key:` values are i18n keys. */
  value: string;
}

/** What a card offers. All open the (mock) setup wizard in the matching starting mode. */
export type EnvAction = "setup" | "addGpu" | "repair" | "diarization";

export interface EnvProfile {
  id: string;
  /** i18n key suffix (env.profile.<name>). */
  name: "gpu" | "cpu" | "none";
  status: ProfileStatus;
  recommended: boolean;
  facts: EnvFact[];
  /** i18n keys of the detection results (shown under "Details"). */
  detection: string[];
  warnings: string[];
  actions: EnvAction[];
}

export interface EnvView {
  example: EnvExample;
  /** "error" = the (fictional) detection itself failed to load. */
  loadFailed: boolean;
  profiles: EnvProfile[];
}

function gpuReady(diarization: boolean): EnvProfile {
  return {
    id: "demo-profile-gpu",
    name: "gpu",
    status: "ready",
    recommended: true,
    facts: [
      { label: "asr", value: "faster-whisper" },
      { label: "model", value: "large-v3-turbo" },
      { label: "device", value: "CUDA" },
      { label: "compute", value: "float16" },
      { label: "diarization", value: diarization ? "pyannote" : "key:env.value.notSetUp" },
      { label: "memory", value: "key:env.value.vram" },
      { label: "speed", value: "key:env.value.speedFast" },
    ],
    detection: ["env.detect.gpuDevice", "env.detect.modelCached", diarization ? "env.detect.diarizationToken" : "env.detect.noDiarizationToken"],
    warnings: diarization ? [] : ["env.warning.noDiarization"],
    actions: diarization ? [] : ["diarization"],
  };
}

function cpuProfile(opts: { recommended: boolean; diarization: boolean; ready: boolean }): EnvProfile {
  return {
    id: "demo-profile-cpu",
    name: "cpu",
    status: opts.ready ? "ready" : "warning",
    recommended: opts.recommended,
    facts: [
      { label: "asr", value: "faster-whisper" },
      { label: "model", value: "small" },
      { label: "device", value: "CPU" },
      { label: "compute", value: "int8" },
      { label: "diarization", value: opts.diarization ? "pyannote (CPU)" : "key:env.value.off" },
      { label: "speed", value: "key:env.value.speedSlow" },
    ],
    detection: ["env.detect.cpuOnly", "env.detect.modelCachedSmall"],
    warnings: ["env.warning.cpuSlow"],
    actions: opts.diarization ? [] : ["diarization"],
  };
}

const GPU_UNAVAILABLE: EnvProfile = {
  id: "demo-profile-gpu",
  name: "gpu",
  status: "unavailable",
  recommended: false,
  facts: [
    { label: "asr", value: "faster-whisper" },
    { label: "model", value: "large-v3-turbo" },
    { label: "device", value: "key:env.value.notSetUp" },
  ],
  detection: ["env.detect.gpuNotSetUp"],
  warnings: [],
  actions: ["addGpu"],
};

const GPU_REPAIR: EnvProfile = {
  id: "demo-profile-gpu",
  name: "gpu",
  status: "repair",
  recommended: false,
  facts: [
    { label: "asr", value: "faster-whisper" },
    { label: "model", value: "large-v3-turbo" },
    { label: "device", value: "CUDA" },
    { label: "runtime", value: "key:env.value.broken" },
  ],
  detection: ["env.detect.gpuDevice", "env.detect.runtimeBroken"],
  warnings: ["env.warning.repair"],
  actions: ["repair"],
};

const NONE: EnvProfile = {
  id: "demo-profile-none",
  name: "none",
  status: "unavailable",
  recommended: false,
  facts: [
    { label: "runtime", value: "key:env.value.notInstalled" },
    { label: "model", value: "key:env.value.notDownloaded" },
  ],
  detection: ["env.detect.noRuntime", "env.detect.noModel"],
  warnings: [],
  actions: ["setup"],
};

/** `diarizationReady` = a diarization credential/model exists in this (fictional) setup. */
export function environmentView(example: EnvExample, diarizationReady = true): EnvView {
  switch (example) {
    case "gpu":
      return { example, loadFailed: false, profiles: [gpuReady(diarizationReady), { ...cpuProfile({ recommended: false, diarization: diarizationReady, ready: false }), actions: [] }] };
    case "cpu":
      return { example, loadFailed: false, profiles: [cpuProfile({ recommended: true, diarization: diarizationReady, ready: false }), GPU_UNAVAILABLE] };
    case "repair":
      return { example, loadFailed: false, profiles: [GPU_REPAIR, cpuProfile({ recommended: true, diarization: diarizationReady, ready: true })] };
    case "unconfigured":
      return { example, loadFailed: false, profiles: [NONE] };
    default:
      return { example, loadFailed: true, profiles: [] };
  }
}

/** The profile a summary line shows: the recommended one, else the first. */
export function primaryProfile(view: EnvView): EnvProfile | null {
  return view.profiles.find((p) => p.recommended) ?? view.profiles[0] ?? null;
}

/** Whether the (fictional) example lets the demo "start" button be pressed. */
export function exampleAllowsStart(view: EnvView): boolean {
  return !view.loadFailed && view.profiles.some((p) => p.status === "ready" || p.status === "warning");
}
