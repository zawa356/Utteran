import { Store } from "../../domain/store";
import type { EnvExample } from "./envExamples";

/**
 * Setup wizard of the UI-3-0 prototype (iteration 4, owner memos #23/#24). It models the
 * OPERATION FLOW only: nothing is detected, downloaded, installed or stored, and no token is ever
 * typed. Every progress step happens because the reviewer pressed a dashed "demo" control.
 *
 * Shape follows B8 (docs/architecture/b8): a read-only PLAN (what would be installed) → explicit
 * user APPROVAL → an OPERATION with stages that can fail, be retried, be interrupted and resumed.
 * Changing a choice after approval invalidates the approval (B8 `reapproval_required`). The legacy
 * wizard (utteran_gui/setup_wizard.py) and B8 are not mixed: nothing here reads the legacy
 * `setup_wizard_*` settings or treats their completion as evidence for this flow.
 */
export type SetupExample = "configured" | "first_run" | "incomplete" | "failed" | "needs_repair" | "cpu_only" | "missing_token";
export const SETUP_EXAMPLES: readonly SetupExample[] = ["configured", "first_run", "incomplete", "failed", "needs_repair", "cpu_only", "missing_token"];

/** Why the wizard was opened. Same screens, different starting point and plan. */
export type WizardEntry = "first_run" | "add" | "repair";
export type WizardStep = "welcome" | "environment" | "features" | "credentials" | "review" | "run" | "done";
export type DevicePlan = "gpu" | "cpu";
export type StageId = "preflight" | "runtime" | "vad_model" | "asr_model" | "diarization_model" | "smoke";
export type StageStatus = "pending" | "running" | "done" | "failed" | "interrupted";
/** B8 operation states the prototype shows (subset; see iteration-4.md §3 for the full mapping). */
export type RunState = "idle" | "running" | "interrupted" | "failed" | "succeeded";

export interface Detected {
  gpu: boolean;
  /** A diarization credential already exists (only its presence is ever known, never its value). */
  credential: boolean;
}

export interface WizardChoices {
  device: DevicePlan;
  diarization: boolean;
  /** Demo stand-in for "a credential has been registered" — no value is entered or kept. */
  credential: boolean;
  /** The model terms of the diarization model were accepted. */
  agreement: boolean;
}

export interface StageRun {
  id: StageId;
  status: StageStatus;
  /** Fraction of this stage (null = the stage reports none). */
  fraction: number | null;
}

export interface SetupState {
  example: SetupExample;
  /** A usable configuration exists (the app can transcribe with at least one profile). */
  configured: boolean;
  wizardOpen: boolean;
  entry: WizardEntry;
  step: WizardStep;
  detected: Detected;
  choices: WizardChoices;
  approved: boolean;
  /** Bumped whenever the plan changes; an approval belongs to one revision. */
  planRevision: number;
  approvedRevision: number | null;
  run: RunState;
  stages: StageRun[];
  attempt: number;
  errorCode: string | null;
}

export interface PlanItem {
  id: "runtime" | "asrModel" | "vadModel" | "diarizationModel" | "repairRuntime";
  /** Synthetic sizes in MB (examples, not measured). */
  downloadMb: number;
  diskMb: number;
}

export interface SetupPlan {
  items: PlanItem[];
  stages: StageId[];
  downloadMb: number;
  diskMb: number;
  needsAgreement: boolean;
}

/** The plan for the current choices (B8: read-only, computed before any approval). */
export function planFor(entry: WizardEntry, choices: WizardChoices): SetupPlan {
  const items: PlanItem[] = [];
  if (entry === "repair") {
    items.push({ id: "repairRuntime", downloadMb: 900, diskMb: 2100 });
  } else {
    items.push(choices.device === "gpu" ? { id: "runtime", downloadMb: 2400, diskMb: 5200 } : { id: "runtime", downloadMb: 600, diskMb: 1400 });
    items.push(choices.device === "gpu" ? { id: "asrModel", downloadMb: 1600, diskMb: 1600 } : { id: "asrModel", downloadMb: 480, diskMb: 480 });
    items.push({ id: "vadModel", downloadMb: 2, diskMb: 2 });
    if (choices.diarization) items.push({ id: "diarizationModel", downloadMb: 60, diskMb: 60 });
  }
  const stages: StageId[] =
    entry === "repair"
      ? ["preflight", "runtime", "smoke"]
      : (["preflight", "runtime", "vad_model", "asr_model", ...(choices.diarization ? (["diarization_model"] as const) : []), "smoke"] as StageId[]);
  return {
    items,
    stages,
    downloadMb: items.reduce((n, i) => n + i.downloadMb, 0),
    diskMb: items.reduce((n, i) => n + i.diskMb, 0),
    needsAgreement: entry !== "repair" && choices.diarization,
  };
}

/** Whether the credential step is part of this run of the wizard (only when it is needed). */
export function credentialStepNeeded(s: Pick<SetupState, "entry" | "choices" | "detected">): boolean {
  return s.entry !== "repair" && s.choices.diarization && !s.detected.credential;
}

/** The pre-approval steps in order (the run and done pages follow the approval). */
export function wizardSteps(s: Pick<SetupState, "entry" | "choices" | "detected">): WizardStep[] {
  if (s.entry === "repair") return ["review"];
  const steps: WizardStep[] = s.entry === "first_run" ? ["welcome", "environment", "features"] : ["environment", "features"];
  if (credentialStepNeeded(s)) steps.push("credentials");
  steps.push("review");
  return steps;
}

/** Why "next" is unavailable on the current step (null = it can be pressed). */
export function nextBlock(s: SetupState): "gpuMissing" | "credential" | "agreement" | null {
  if (s.step === "environment" && s.choices.device === "gpu" && !s.detected.gpu) return "gpuMissing";
  if (s.step === "credentials" && s.choices.diarization) {
    if (!s.choices.credential) return "credential";
    if (!s.choices.agreement) return "agreement";
  }
  return null;
}

export function currentStage(s: Pick<SetupState, "stages">): StageRun | null {
  return s.stages.find((st) => st.status === "running" || st.status === "failed" || st.status === "interrupted") ?? null;
}

const STAGE_REPORTS_PROGRESS: Record<StageId, boolean> = {
  preflight: false,
  runtime: true,
  vad_model: true,
  asr_model: true,
  diarization_model: true,
  smoke: false,
};

const FAIL_CODE: Record<StageId, string> = {
  preflight: "demo_preflight_failed",
  runtime: "demo_install_failed",
  vad_model: "demo_download_failed",
  asr_model: "demo_download_failed",
  diarization_model: "demo_credential_rejected",
  smoke: "demo_smoke_failed",
};

const NO_CHOICES: WizardChoices = { device: "gpu", diarization: false, credential: false, agreement: false };

function base(example: SetupExample): SetupState {
  return {
    example,
    configured: true,
    wizardOpen: false,
    entry: "first_run",
    step: "welcome",
    detected: { gpu: true, credential: true },
    choices: { ...NO_CHOICES },
    approved: false,
    planRevision: 1,
    approvedRevision: null,
    run: "idle",
    stages: [],
    attempt: 0,
    errorCode: null,
  };
}

function stagesOf(ids: StageId[], statuses: Partial<Record<StageId, StageStatus>> = {}): StageRun[] {
  return ids.map((id) => ({ id, status: statuses[id] ?? "pending", fraction: statuses[id] === "interrupted" ? 0.5 : null }));
}

/** Synthetic starting points the reviewer switches between in the review strip. */
export function exampleState(example: SetupExample): SetupState {
  const s = base(example);
  switch (example) {
    case "first_run":
      return { ...s, configured: false, wizardOpen: true, detected: { gpu: true, credential: false } };
    case "cpu_only":
      return { ...s, configured: false, wizardOpen: true, detected: { gpu: false, credential: false }, choices: { ...NO_CHOICES, device: "cpu" } };
    case "incomplete": {
      const choices: WizardChoices = { device: "gpu", diarization: false, credential: false, agreement: false };
      return {
        ...s,
        configured: false,
        wizardOpen: true,
        step: "run",
        detected: { gpu: true, credential: false },
        choices,
        approved: true,
        approvedRevision: 1,
        run: "interrupted",
        attempt: 1,
        stages: stagesOf(planFor("first_run", choices).stages, { preflight: "done", runtime: "done", vad_model: "done", asr_model: "interrupted" }),
      };
    }
    case "failed": {
      const choices: WizardChoices = { device: "gpu", diarization: true, credential: true, agreement: true };
      return {
        ...s,
        configured: false,
        wizardOpen: true,
        step: "run",
        detected: { gpu: true, credential: false },
        choices,
        approved: true,
        approvedRevision: 1,
        run: "failed",
        attempt: 1,
        errorCode: FAIL_CODE.asr_model,
        stages: stagesOf(planFor("first_run", choices).stages, { preflight: "done", runtime: "done", vad_model: "done", asr_model: "failed" }),
      };
    }
    case "missing_token":
      return { ...s, detected: { gpu: true, credential: false } };
    default:
      return s;
  }
}

/** Environment example the rest of the prototype shows for a setup example. */
export function envForExample(example: SetupExample): { env: EnvExample; diarizationReady: boolean } {
  switch (example) {
    case "first_run":
    case "cpu_only":
    case "incomplete":
    case "failed":
      return { env: "unconfigured", diarizationReady: false };
    case "needs_repair":
      return { env: "repair", diarizationReady: true };
    case "missing_token":
      return { env: "gpu", diarizationReady: false };
    default:
      return { env: "gpu", diarizationReady: true };
  }
}

export interface SetupEffects {
  /** Called when the wizard finishes: the rest of the prototype then shows this environment. */
  applyEnvironment(env: EnvExample, diarizationReady: boolean): void;
  /** Defaults that follow the wizard's feature choices (e.g. diarization off when it was not installed). */
  applyDefaults(patch: { diarization?: boolean }): void;
  notify(key: string, params?: Record<string, string | number>): void;
}

export class SetupController {
  readonly store = new Store<SetupState>(exampleState("configured"));

  constructor(private readonly effects: SetupEffects) {}

  private patch(p: Partial<SetupState>): void {
    this.store.set((s) => ({ ...s, ...p }));
  }

  setExample(example: SetupExample): void {
    this.store.set(exampleState(example));
    const { env, diarizationReady } = envForExample(example);
    this.effects.applyEnvironment(env, diarizationReady);
  }

  /**
   * Opens the wizard. `add` = install something more (GPU, diarization) on a working setup;
   * `repair` = fix a broken profile; `first_run` = nothing usable yet. An unfinished operation is
   * shown as it is (resume) instead of starting over.
   */
  open(entry: WizardEntry, preset: Partial<WizardChoices> = {}): void {
    const s = this.store.get();
    if (s.run === "interrupted" || s.run === "failed" || s.run === "running") {
      this.patch({ wizardOpen: true, step: "run" });
      return;
    }
    const steps = wizardSteps({ ...s, entry });
    const choices: WizardChoices = { ...s.choices, ...preset };
    if (!s.detected.gpu) choices.device = "cpu";
    this.patch({
      wizardOpen: true,
      entry,
      step: steps[0]!,
      choices,
      approved: false,
      approvedRevision: null,
      run: "idle",
      stages: [],
      errorCode: null,
    });
  }

  close(): void {
    this.patch({ wizardOpen: false });
  }

  setChoice<K extends keyof WizardChoices>(key: K, value: WizardChoices[K]): void {
    const s = this.store.get();
    if (s.choices[key] === value) return;
    // Any change of the plan after an approval needs a new approval (B8 reapproval_required).
    this.patch({ choices: { ...s.choices, [key]: value }, planRevision: s.planRevision + 1, approved: false });
  }

  /** Demo: mark a credential as registered (no value is typed, shown or stored). */
  registerCredential(): void {
    this.setChoice("credential", true);
  }

  next(): void {
    const s = this.store.get();
    if (nextBlock(s)) return;
    const steps = wizardSteps(s);
    const i = steps.indexOf(s.step);
    if (i >= 0 && i < steps.length - 1) this.patch({ step: steps[i + 1]! });
  }

  back(): void {
    const s = this.store.get();
    const steps = wizardSteps(s);
    const i = steps.indexOf(s.step);
    if (i > 0) this.patch({ step: steps[i - 1]! });
  }

  /** "Continue without diarization" on the credential step. */
  skipDiarization(): void {
    this.setChoice("diarization", false);
    this.patch({ step: "review" });
  }

  /** Approves the shown plan and starts the (fictional) operation. Done stages of an earlier attempt are kept. */
  approveAndStart(): void {
    const s = this.store.get();
    if (s.step !== "review") return;
    const plan = planFor(s.entry, s.choices);
    const previous = new Map(s.stages.map((st) => [st.id, st.status]));
    const stages = plan.stages.map((id): StageRun => ({ id, status: previous.get(id) === "done" ? "done" : "pending", fraction: null }));
    const first = stages.findIndex((st) => st.status !== "done");
    if (first >= 0) stages[first] = this.enter(stages[first]!);
    this.patch({ approved: true, approvedRevision: s.planRevision, step: first >= 0 ? "run" : "done", run: first >= 0 ? "running" : "succeeded", stages, attempt: s.attempt + 1, errorCode: null });
    this.effects.notify("notice.setupStarted");
  }

  private enter(stage: StageRun): StageRun {
    return { ...stage, status: "running", fraction: STAGE_REPORTS_PROGRESS[stage.id] ? 0 : null };
  }

  private mapCurrent(fn: (st: StageRun, i: number, all: StageRun[]) => StageRun[]): void {
    const s = this.store.get();
    const i = s.stages.findIndex((st) => st.status === "running");
    if (i < 0) return;
    const stages = fn(s.stages[i]!, i, [...s.stages]);
    const done = stages.every((st) => st.status === "done");
    this.patch({ stages, run: done ? "succeeded" : s.run, step: done ? "done" : s.step });
  }

  /** Demo: +50 % in the current stage (stages that report progress), else completes it. */
  advance(): void {
    this.mapCurrent((st, i, all) => {
      if (st.fraction !== null && st.fraction < 0.5) {
        all[i] = { ...st, fraction: st.fraction + 0.5 };
        return all;
      }
      all[i] = { ...st, status: "done", fraction: null };
      if (i + 1 < all.length) all[i + 1] = this.enter(all[i + 1]!);
      return all;
    });
  }

  /** Demo: the current stage fails. */
  fail(): void {
    const s = this.store.get();
    const i = s.stages.findIndex((st) => st.status === "running");
    if (i < 0) return;
    const stages = [...s.stages];
    stages[i] = { ...stages[i]!, status: "failed" };
    this.patch({ stages, run: "failed", errorCode: FAIL_CODE[stages[i]!.id] });
  }

  /** Demo: the app was closed / the PC slept during the stage (B8 interrupted). */
  interrupt(): void {
    const s = this.store.get();
    const i = s.stages.findIndex((st) => st.status === "running");
    if (i < 0) return;
    const stages = [...s.stages];
    stages[i] = { ...stages[i]!, status: "interrupted" };
    this.patch({ stages, run: "interrupted" });
  }

  /** retry (failed) / resume (interrupted): same approved plan, the stopped stage starts again. */
  continueRun(): void {
    const s = this.store.get();
    if (s.run !== "failed" && s.run !== "interrupted") return;
    const stages = s.stages.map((st) => (st.status === "failed" || st.status === "interrupted" ? this.enter(st) : st));
    this.patch({ stages, run: "running", attempt: s.attempt + 1, errorCode: null });
    this.effects.notify(s.run === "failed" ? "notice.setupRetried" : "notice.setupResumed");
  }

  /** "Change settings" from a stopped run: back to the choices; a new approval is needed. */
  changeChoices(): void {
    const s = this.store.get();
    if (s.run !== "failed" && s.run !== "interrupted") return;
    const steps = wizardSteps(s);
    const target: WizardStep = steps.includes("features") ? "features" : "review";
    this.patch({ run: "idle", step: target, approved: false, approvedRevision: null, planRevision: s.planRevision + 1, errorCode: null });
  }

  /** Last page: the rest of the prototype now shows the configured (fictional) environment. */
  finish(): void {
    const s = this.store.get();
    if (s.run !== "succeeded") return;
    const env: EnvExample = s.entry === "repair" ? "gpu" : s.choices.device === "gpu" ? "gpu" : "cpu";
    const diarizationReady = s.entry === "repair" ? true : s.choices.diarization;
    this.store.set({ ...s, configured: true, wizardOpen: false, run: "idle", stages: [], step: "welcome", example: "configured", detected: { ...s.detected, credential: s.detected.credential || s.choices.credential } });
    this.effects.applyEnvironment(env, diarizationReady);
    if (s.entry !== "repair" && !s.choices.diarization) this.effects.applyDefaults({ diarization: false });
    this.effects.notify("notice.setupDone");
  }
}
