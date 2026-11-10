import { PROFILES, type ProfileName } from "../api/legacy";
import type { JobBackend, JobLanguage, JobOptions, OutputFormat } from "../api/legacyEnvironment";
import type { TranscriptionRequest } from "../api/legacyJobs";
import { type HostBridge, HostUnavailableError } from "../host";
import { type EnvironmentState, isCurrentResult } from "./environment";
import { checkPath, type PathProblem } from "./paths";
import type { SettingsState } from "./settings";
import { type ReadonlyStore, Store } from "./store";

/**
 * Everything one `POST /api/jobs` needs, for one input (S3-A form shape). Built per input row by
 * `inputCollection.effectiveDraft()` (common settings + that row's overrides and output).
 */
export interface TranscriptionDraft {
  inputPath: string;
  inputSource: "picker" | "drop" | "typed" | null;
  outputDir: string;
  /** `saved_default` = the server-confirmed default output folder (S1), never the settings draft. */
  outputSource: "saved_default" | "picker" | "typed" | null;
  asrBackend: string | null;
  asrModel: string | null;
  asrDevice: string | null;
  language: JobLanguage;
  formats: OutputFormat[];
}

/**
 * The common processing settings (S3-B1): the S3-A form without its input, which now lives in the
 * shared input list (`InputCollectionController`). Lives outside Vue, so navigation and mode
 * switches keep it. In memory only: no path is written to localStorage/sessionStorage/URL, and
 * nothing here PATCHes settings or re-detects the environment.
 */
export type CommonDraft = Omit<TranscriptionDraft, "inputPath" | "inputSource">;

/** Legacy form defaults (web/app.js): language "ja", formats srt/json/md. */
export const DEFAULT_LANGUAGE: JobLanguage = "ja";
export const DEFAULT_FORMATS: readonly OutputFormat[] = ["srt", "json", "md"];

export type PickError = "host_unavailable" | "failed";

export interface FormState {
  draft: CommonDraft;
  /** Only the common output folder is picked here (inputs are picked by the input list). */
  picking: "output_folder" | null;
  pickError: { target: "output_folder"; reason: PickError } | null;
}

/** Why the environment cannot supply choices right now (no static fallback is ever offered). */
export type EnvironmentBlock = "not_loaded" | "loading" | "failed" | "previous" | "no_profile";

export type FormIssue =
  | { field: "input"; problem: PathProblem }
  | { field: "output"; problem: PathProblem }
  | { field: "environment"; problem: EnvironmentBlock }
  | { field: "asrBackend" | "asrModel" | "asrDevice" | "language"; problem: "missing" | "unavailable" }
  | { field: "formats"; problem: "missing" | "unavailable" };

export interface FormEvaluation {
  /** Server profile that produced the choices (`active_profile` of the *current* answer). */
  profile: ProfileName | null;
  options: JobOptions | null;
  backend: JobBackend | null;
  issues: FormIssue[];
  /** Complete, client-checked request; null while any issue remains. */
  request: TranscriptionRequest | null;
}

function currentChoices(env: EnvironmentState): { profile: ProfileName; options: JobOptions } | EnvironmentBlock {
  const phase = env.load.phase;
  if (phase === "idle") return "not_loaded";
  if (phase === "pending" || phase === "accepted") return "loading";
  if (phase === "error" || phase === "cancelled") return env.data ? "previous" : "failed";
  if (!isCurrentResult(env) || !env.data) return "previous";
  const profile = env.data.activeProfile;
  if (profile === null || !(PROFILES as readonly string[]).includes(profile)) return "no_profile";
  return { profile: profile as ProfileName, options: env.data.jobOptions };
}

/** Pure: what the form can submit with the environment answer as it is *now*. */
export function evaluate(draft: TranscriptionDraft, env: EnvironmentState): FormEvaluation {
  const issues: FormIssue[] = [];
  const input = checkPath(draft.inputPath, "input_file");
  if (input) issues.push({ field: "input", problem: input });
  const output = checkPath(draft.outputDir, "output_folder");
  if (output) issues.push({ field: "output", problem: output });
  const choices = currentChoices(env);
  if (typeof choices === "string") {
    issues.push({ field: "environment", problem: choices });
    return { profile: null, options: null, backend: null, issues, request: null };
  }
  const { options, profile } = choices;
  const backend = options.asr.find((b) => b.id === draft.asrBackend) ?? null;
  const pick = (field: "asrBackend" | "asrModel" | "asrDevice", value: string | null, ok: boolean) => {
    if (value === null) issues.push({ field, problem: "missing" });
    else if (!ok) issues.push({ field, problem: "unavailable" });
  };
  pick("asrBackend", draft.asrBackend, backend !== null);
  pick("asrModel", draft.asrModel, backend?.models.some((m) => m.id === draft.asrModel) ?? false);
  pick("asrDevice", draft.asrDevice, backend?.devices.some((d) => d.id === draft.asrDevice) ?? false);
  if (!options.languages.includes(draft.language)) issues.push({ field: "language", problem: "unavailable" });
  if (draft.formats.length === 0) issues.push({ field: "formats", problem: "missing" });
  else if (draft.formats.some((f) => !options.formats.includes(f))) issues.push({ field: "formats", problem: "unavailable" });
  const request =
    issues.length === 0 && draft.asrBackend !== null && draft.asrModel !== null && draft.asrDevice !== null
      ? {
          inputPath: draft.inputPath,
          outputDir: draft.outputDir,
          profile,
          asrBackend: draft.asrBackend,
          asrModel: draft.asrModel,
          asrDevice: draft.asrDevice,
          language: draft.language,
          formats: [...draft.formats],
        }
      : null;
  return { profile, options, backend, issues, request };
}

/**
 * Keep only choices the current answer offers; fill a level only when it has exactly one choice
 * (the only legal value — not a recommendation). Returns the same object when nothing changes.
 */
export function reconcile<D extends CommonDraft>(draft: D, options: JobOptions | null): D {
  if (options === null) return draft;
  let { asrBackend, asrModel, asrDevice } = draft;
  if (asrBackend !== null && !options.asr.some((b) => b.id === asrBackend)) asrBackend = null;
  if (asrBackend === null && options.asr.length === 1) asrBackend = options.asr[0]!.id;
  const backend = options.asr.find((b) => b.id === asrBackend);
  if (!backend) {
    asrModel = null;
    asrDevice = null;
  } else {
    if (asrModel !== null && !backend.models.some((m) => m.id === asrModel)) asrModel = null;
    if (asrModel === null && backend.models.length === 1) asrModel = backend.models[0]!.id;
    if (asrDevice !== null && !backend.devices.some((d) => d.id === asrDevice)) asrDevice = null;
    if (asrDevice === null && backend.devices.length === 1) asrDevice = backend.devices[0]!.id;
  }
  if (asrBackend === draft.asrBackend && asrModel === draft.asrModel && asrDevice === draft.asrDevice) return draft;
  return { ...draft, asrBackend, asrModel, asrDevice };
}

export function emptyDraft(): CommonDraft {
  return {
    outputDir: "",
    outputSource: null,
    asrBackend: null,
    asrModel: null,
    asrDevice: null,
    language: DEFAULT_LANGUAGE,
    formats: [...DEFAULT_FORMATS],
  };
}

export class TranscriptionFormController {
  readonly store = new Store<FormState>({ draft: emptyDraft(), picking: null, pickError: null });
  private readonly unsubscribers: (() => void)[] = [];

  constructor(
    private readonly environment: ReadonlyStore<EnvironmentState>,
    settings: ReadonlyStore<SettingsState>,
  ) {
    this.unsubscribers.push(environment.subscribe(() => this.syncChoices()));
    this.unsubscribers.push(settings.subscribe((s) => this.adoptDefaultOutput(s)));
    this.adoptDefaultOutput(settings.get());
    this.syncChoices();
  }

  /** The common settings evaluated without an input (choices, profile, setting issues). */
  evaluation(): FormEvaluation {
    return evaluate({ ...this.store.get().draft, inputPath: "", inputSource: null }, this.environment.get());
  }

  edit(patch: Partial<Pick<CommonDraft, "outputDir" | "language" | "formats">>): void {
    const { draft } = this.store.get();
    const next: CommonDraft = { ...draft, ...patch };
    if (patch.outputDir !== undefined) next.outputSource = "typed";
    this.store.patch({ draft: next });
  }

  /** Changing the backend clears model/device (they belong to one backend), then refills singles. */
  choose(field: "asrBackend" | "asrModel" | "asrDevice", value: string | null): void {
    const { draft } = this.store.get();
    const next = field === "asrBackend" ? { ...draft, asrBackend: value, asrModel: null, asrDevice: null } : { ...draft, [field]: value };
    this.store.patch({ draft: reconcile(next, this.evaluation().options) });
  }

  toggleFormat(format: OutputFormat, on: boolean): void {
    const { draft } = this.store.get();
    const formats = on ? [...new Set([...draft.formats, format])] : draft.formats.filter((f) => f !== format);
    this.store.patch({ draft: { ...draft, formats } });
  }

  async pick(target: "output_folder", host: HostBridge): Promise<void> {
    if (this.store.get().picking !== null) return;
    this.store.patch({ picking: target, pickError: null });
    try {
      const path = await host.pickPath(target);
      this.store.patch({ picking: null });
      if (path === null) return; // cancelled: nothing changes
      this.store.patch({ draft: { ...this.store.get().draft, outputDir: path, outputSource: "picker" } });
    } catch (error) {
      this.store.patch({ picking: null, pickError: { target, reason: error instanceof HostUnavailableError ? "host_unavailable" : "failed" } });
    }
  }

  dispose(): void {
    for (const unsubscribe of this.unsubscribers.splice(0)) unsubscribe();
  }

  private syncChoices(): void {
    const { draft } = this.store.get();
    const next = reconcile(draft, this.evaluation().options);
    if (next !== draft) this.store.patch({ draft: next });
  }

  /** Fill the output folder from the *saved* default once, only while the field was never touched. */
  private adoptDefaultOutput(settings: SettingsState): void {
    const saved = settings.saved?.defaultOutputDir ?? "";
    const { draft } = this.store.get();
    if (saved === "" || draft.outputSource !== null || draft.outputDir !== "") return;
    this.store.patch({ draft: { ...draft, outputDir: saved, outputSource: "saved_default" } });
  }
}
