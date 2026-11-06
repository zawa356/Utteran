/**
 * Fictional execution environment of the UI-3-0 prototype (owner memo: "a mock with information").
 * NOTHING here is detected: no GPU, driver, model or backend is queried, and the values do not
 * claim that such a configuration works. In the product the matrix, automatic selection and ready
 * decision come from the backend (B4 is the source of truth); the frontend only renders it.
 *
 * Iteration 4 P1 fix: the environment is an `EnvState` with an INDEPENDENT state per profile
 * (GPU / CPU: absent / ready / needs repair) and for speaker diarization, plus which profile is
 * preferred. Setup ("add" / "repair") updates only what it installed or repaired, so profiles that
 * already worked are kept (B8 `PlanContent.preserved_environment_ids`). The review strip's
 * examples (`ENV_EXAMPLES`) are just named starting states.
 */
export type EnvExample = "gpu" | "gpuOnly" | "cpu" | "unconfigured" | "repair" | "error";
export const ENV_EXAMPLES: readonly EnvExample[] = ["gpu", "gpuOnly", "cpu", "unconfigured", "repair", "error"];

export type ProfileName = "gpu" | "cpu";
/** Install state of one profile. */
export type ProfileInstall = "absent" | "ready" | "repair";

export interface EnvState {
  gpu: ProfileInstall;
  cpu: ProfileInstall;
  /** Speaker diarization (model + credential) is set up. */
  diarization: boolean;
  /** A CUDA GPU is present (fictional detection result; not the same as the GPU profile being ready). */
  gpuDetected: boolean;
  /** Profile the user prefers / uses. Adding a profile never switches it on its own. */
  preferred: ProfileName | null;
  /** The (fictional) detection itself failed to load. */
  loadFailed: boolean;
  /**
   * ASR models installed per profile (iteration 5, #30: "add a model" must not re-download what is
   * there). Kept while a profile needs repair; repair never removes a model.
   */
  models: Record<ProfileName, string[]>;
  /** The model a profile uses by default. Set once (first setup of that profile); adding never switches it. */
  defaultModel: Record<ProfileName, string | null>;
}

const GPU_MODELS = { models: ["large-v3-turbo"], defaultModel: "large-v3-turbo" };
const CPU_MODELS = { models: ["small"], defaultModel: "small" };
function inventory(gpu: ProfileInstall, cpu: ProfileInstall): Pick<EnvState, "models" | "defaultModel"> {
  const g = gpu === "absent" ? null : GPU_MODELS;
  const c = cpu === "absent" ? null : CPU_MODELS;
  return { models: { gpu: g ? [...g.models] : [], cpu: c ? [...c.models] : [] }, defaultModel: { gpu: g?.defaultModel ?? null, cpu: c?.defaultModel ?? null } };
}

export function envFromExample(example: EnvExample, diarization = true): EnvState {
  const e = envBase(example, diarization);
  return { ...e, ...inventory(e.gpu, e.cpu) };
}

function envBase(example: EnvExample, diarization: boolean): Omit<EnvState, "models" | "defaultModel"> {
  switch (example) {
    case "gpu":
      return { gpu: "ready", cpu: "ready", diarization, gpuDetected: true, preferred: "gpu", loadFailed: false };
    case "gpuOnly":
      return { gpu: "ready", cpu: "absent", diarization, gpuDetected: true, preferred: "gpu", loadFailed: false };
    case "cpu":
      return { gpu: "absent", cpu: "ready", diarization, gpuDetected: true, preferred: "cpu", loadFailed: false };
    case "repair":
      return { gpu: "repair", cpu: "ready", diarization, gpuDetected: true, preferred: "cpu", loadFailed: false };
    case "unconfigured":
      return { gpu: "absent", cpu: "absent", diarization: false, gpuDetected: true, preferred: null, loadFailed: false };
    default:
      return { gpu: "absent", cpu: "absent", diarization: false, gpuDetected: true, preferred: null, loadFailed: true };
  }
}

/** Shown per profile (never computed from a detection). */
export type ProfileStatus = "ready" | "warning" | "repair" | "unavailable";

export interface EnvFact {
  /** i18n key suffix of the label (env.fact.<label>). */
  label: "asr" | "model" | "models" | "device" | "diarization" | "compute" | "runtime" | "memory" | "speed";
  /** Display value: product-like names stay untranslated; `key:` values are i18n keys. */
  value: string;
}

/** What a card offers. All open the (mock) setup wizard in the matching starting mode. */
export type EnvAction = "setup" | "addGpu" | "addCpu" | "repair" | "diarization";

export interface EnvProfile {
  id: string;
  /** i18n key suffix (env.profile.<name>). */
  name: ProfileName | "none";
  status: ProfileStatus;
  recommended: boolean;
  facts: EnvFact[];
  /** i18n keys of the detection results (shown under "Details"). */
  detection: string[];
  warnings: string[];
  actions: EnvAction[];
}

export interface EnvView {
  /** "error" = the (fictional) detection itself failed to load. */
  loadFailed: boolean;
  profiles: EnvProfile[];
}

/** Default model, plus the installed list when there is more than one. */
function modelFacts(env: EnvState, p: ProfileName): EnvFact[] {
  const list = env.models[p];
  const def = env.defaultModel[p] ?? list[0];
  const out: EnvFact[] = def ? [{ label: "model", value: def }] : [];
  if (list.length > 1) out.push({ label: "models", value: list.join(", ") });
  return out;
}

function gpuProfile(env: EnvState): EnvProfile | null {
  const base = { id: "demo-profile-gpu", name: "gpu" as const, recommended: false };
  if (env.gpu === "ready")
    return {
      ...base,
      status: "ready",
      facts: [
        { label: "asr", value: "faster-whisper" },
        ...modelFacts(env, "gpu"),
        { label: "device", value: "CUDA" },
        { label: "compute", value: "float16" },
        { label: "diarization", value: env.diarization ? "pyannote" : "key:env.value.notSetUp" },
        { label: "memory", value: "key:env.value.vram" },
        { label: "speed", value: "key:env.value.speedFast" },
      ],
      detection: ["env.detect.gpuDevice", "env.detect.modelCached", env.diarization ? "env.detect.diarizationToken" : "env.detect.noDiarizationToken"],
      warnings: env.diarization ? [] : ["env.warning.noDiarization"],
      actions: env.diarization ? [] : ["diarization"],
    };
  if (env.gpu === "repair")
    return {
      ...base,
      status: "repair",
      facts: [
        { label: "asr", value: "faster-whisper" },
        ...modelFacts(env, "gpu"),
        { label: "device", value: "CUDA" },
        { label: "runtime", value: "key:env.value.broken" },
      ],
      detection: ["env.detect.gpuDevice", "env.detect.runtimeBroken"],
      warnings: [env.cpu === "ready" ? "env.warning.repair" : "env.warning.repairOnly"],
      actions: ["repair"],
    };
  if (!env.gpuDetected) return null;
  return {
    ...base,
    status: "unavailable",
    facts: [
      { label: "asr", value: "faster-whisper" },
      { label: "model", value: "large-v3-turbo" },
      { label: "device", value: "key:env.value.notSetUp" },
    ],
    detection: ["env.detect.gpuDevice", "env.detect.gpuNotSetUp"],
    warnings: [],
    actions: ["addGpu"],
  };
}

function cpuProfile(env: EnvState): EnvProfile {
  const base = { id: "demo-profile-cpu", name: "cpu" as const, recommended: false };
  if (env.cpu === "ready")
    return {
      ...base,
      status: "ready",
      facts: [
        { label: "asr", value: "faster-whisper" },
        ...modelFacts(env, "cpu"),
        { label: "device", value: "CPU" },
        { label: "compute", value: "int8" },
        { label: "diarization", value: env.diarization ? "pyannote (CPU)" : "key:env.value.off" },
        { label: "speed", value: "key:env.value.speedSlow" },
      ],
      detection: ["env.detect.cpuOnly", "env.detect.modelCachedSmall"],
      warnings: ["env.warning.cpuSlow"],
      actions: env.diarization || env.gpu === "ready" ? [] : ["diarization"],
    };
  if (env.cpu === "repair")
    return {
      ...base,
      status: "repair",
      facts: [
        { label: "device", value: "CPU" },
        { label: "runtime", value: "key:env.value.broken" },
      ],
      detection: ["env.detect.cpuOnly", "env.detect.runtimeBroken"],
      warnings: [],
      actions: ["repair"],
    };
  return {
    ...base,
    status: "unavailable",
    facts: [
      { label: "device", value: "CPU" },
      { label: "runtime", value: "key:env.value.notSetUp" },
    ],
    detection: ["env.detect.cpuNotSetUp"],
    warnings: [],
    actions: ["addCpu"],
  };
}

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

/** Cards for an environment: the ready / broken / missing state of each profile, side by side. */
export function environmentView(env: EnvState): EnvView {
  if (env.loadFailed) return { loadFailed: true, profiles: [] };
  if (env.gpu === "absent" && env.cpu === "absent") return { loadFailed: false, profiles: [NONE] };
  const profiles = [gpuProfile(env), cpuProfile(env)].filter((p): p is EnvProfile => p !== null);
  const usable = profiles.filter((p) => p.status === "ready");
  const recommended = usable.find((p) => p.name === env.preferred) ?? usable[0];
  return { loadFailed: false, profiles: profiles.map((p) => (p === recommended ? { ...p, recommended: true } : p)) };
}

/** The profile a summary line shows: the recommended one, else the first. */
export function primaryProfile(view: EnvView): EnvProfile | null {
  return view.profiles.find((p) => p.recommended) ?? view.profiles[0] ?? null;
}

/** Whether the (fictional) environment lets the demo "start" button be pressed. */
export function exampleAllowsStart(view: EnvView): boolean {
  return !view.loadFailed && view.profiles.some((p) => p.status === "ready" || p.status === "warning");
}

/** The first profile that needs repair (the target "Repair…" opens with when none is named). */
export function repairTarget(env: EnvState): ProfileName | null {
  return env.gpu === "repair" ? "gpu" : env.cpu === "repair" ? "cpu" : null;
}

/** What a successful setup does to the environment — and nothing else (B8 preserved environments). */
export interface SetupOutcome {
  entry: "first_run" | "add" | "repair";
  device: ProfileName;
  diarization: boolean;
  /** ASR models the plan installed (empty for repair). */
  models?: readonly string[];
  /** The model chosen as the main one; used only if the profile has no default yet. */
  primaryModel?: string | null;
}

/**
 * The environment after a SUCCESSFUL setup:
 *  - first run / add: the chosen profile becomes ready; diarization becomes ready when it was
 *    installed; every other profile and an already ready diarization are kept;
 *  - repair: only the repaired profile changes (to ready); nothing else is touched;
 *  - models are only ever ADDED to the target profile; its default model is set only when it had none.
 * The preferred profile changes only when there was none yet.
 */
export function applySetupOutcome(env: EnvState, outcome: SetupOutcome): EnvState {
  const next: EnvState = { ...env, [outcome.device]: "ready", loadFailed: false, models: { ...env.models }, defaultModel: { ...env.defaultModel } };
  if (outcome.entry !== "repair" && outcome.diarization) next.diarization = true;
  if (outcome.entry !== "repair") {
    const before = env.models[outcome.device];
    next.models[outcome.device] = [...before, ...(outcome.models ?? []).filter((m) => !before.includes(m))];
    if (next.defaultModel[outcome.device] === null) next.defaultModel[outcome.device] = outcome.primaryModel ?? next.models[outcome.device][0] ?? null;
  }
  if (next.preferred === null) next.preferred = outcome.device;
  return next;
}
