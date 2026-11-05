/**
 * Fictional execution-environment examples for the UI-3-0 prototype (owner memo: "a mock with
 * information"). NOTHING here is detected: no GPU, driver, model or backend is queried, and the
 * values do not claim that such a configuration works. In the product the matrix, automatic
 * selection and device decision come from the backend (B4 is the source of truth); the frontend
 * only renders what the backend returns. Product names are used as plausible labels only.
 */
export type EnvExample = "gpu" | "cpu" | "unconfigured" | "error";
export const ENV_EXAMPLES: readonly EnvExample[] = ["gpu", "cpu", "unconfigured", "error"];

/** "ready" / "warning" / "unavailable" as shown for one example profile (never computed here). */
export type ProfileStatus = "ready" | "warning" | "unavailable";

export interface EnvFact {
  /** i18n key suffix of the label (env.fact.<label>). */
  label: "asr" | "model" | "device" | "diarization" | "compute" | "runtime" | "memory" | "speed";
  /** Display value: product-like names stay untranslated; `key:` values are i18n keys. */
  value: string;
}

export interface EnvProfile {
  id: string;
  /** i18n key suffix (env.profile.<name>). */
  name: "gpu" | "cpu" | "none";
  status: ProfileStatus;
  recommended: boolean;
  facts: EnvFact[];
  /** i18n keys of the "why ready / why not" evidence lines. */
  evidence: string[];
  warnings: string[];
  /** i18n keys of the steps that would be needed (unconfigured / CPU guidance). */
  actions: string[];
}

export interface EnvView {
  example: EnvExample;
  /** "error" = the (fictional) detection itself failed to load. */
  loadFailed: boolean;
  profiles: EnvProfile[];
}

const GPU: EnvProfile = {
  id: "demo-profile-gpu",
  name: "gpu",
  status: "ready",
  recommended: true,
  facts: [
    { label: "asr", value: "faster-whisper" },
    { label: "model", value: "large-v3-turbo" },
    { label: "device", value: "CUDA" },
    { label: "compute", value: "float16" },
    { label: "diarization", value: "pyannote" },
    { label: "memory", value: "key:env.value.vram" },
    { label: "speed", value: "key:env.value.speedFast" },
  ],
  evidence: ["env.evidence.gpuDevice", "env.evidence.modelCached", "env.evidence.diarizationToken"],
  warnings: [],
  actions: [],
};

const CPU_READY: EnvProfile = {
  id: "demo-profile-cpu",
  name: "cpu",
  status: "warning",
  recommended: false,
  facts: [
    { label: "asr", value: "faster-whisper" },
    { label: "model", value: "small" },
    { label: "device", value: "CPU" },
    { label: "compute", value: "int8" },
    { label: "diarization", value: "key:env.value.off" },
    { label: "speed", value: "key:env.value.speedSlow" },
  ],
  evidence: ["env.evidence.cpuOnly", "env.evidence.modelCachedSmall"],
  warnings: ["env.warning.cpuSlow", "env.warning.noDiarization"],
  actions: ["env.action.gpuSetup", "env.action.diarizationToken"],
};

const GPU_UNAVAILABLE: EnvProfile = {
  id: "demo-profile-gpu",
  name: "gpu",
  status: "unavailable",
  recommended: false,
  facts: [
    { label: "asr", value: "faster-whisper" },
    { label: "model", value: "large-v3-turbo" },
    { label: "device", value: "key:env.value.notFound" },
  ],
  evidence: ["env.evidence.noGpu"],
  warnings: [],
  actions: ["env.action.gpuSetup"],
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
  evidence: ["env.evidence.noRuntime", "env.evidence.noModel"],
  warnings: [],
  actions: ["env.action.runSetup", "env.action.downloadModel", "env.action.redetect"],
};

export function environmentView(example: EnvExample): EnvView {
  switch (example) {
    case "gpu":
      return { example, loadFailed: false, profiles: [GPU, { ...CPU_READY, warnings: ["env.warning.cpuSlow"] }] };
    case "cpu":
      return { example, loadFailed: false, profiles: [{ ...CPU_READY, recommended: true }, GPU_UNAVAILABLE] };
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
  return !view.loadFailed && view.profiles.some((p) => p.status !== "unavailable");
}
