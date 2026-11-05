import { Store } from "../../domain/store";
import { applySetupOutcome, type EnvExample, envFromExample, type EnvState, repairTarget } from "./envExamples";

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
 *
 * Iteration 4 P1 fixes:
 *  - a credential (present or not) and the acceptance of the model terms are SEPARATE: a plan lists
 *    what it requires (B8 `PlanContent.required_agreements`) and the approval carries what was
 *    accepted (`ApprovalRequest.accepted_agreement_ids`). Missing either blocks "next", the approval
 *    and any retry / resume — in the controller, not only in the view;
 *  - a successful setup changes only what its plan installed or repaired (`applySetupOutcome`);
 *    nothing is applied before success.
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
  /** The profile to install (first run / add) or to repair (repair). */
  device: DevicePlan;
  diarization: boolean;
  /** Demo stand-in for "a credential was registered in this wizard" — no value is entered or kept. */
  credential: boolean;
  /** Ids of the terms the user accepted (checked against the CURRENT plan's required ids). */
  acceptedAgreements: string[];
}

/** Terms of the (fictional) diarization model; the id names the version that was accepted. */
export const DIARIZATION_TERMS = "demo-diarization-model-terms-v1";

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
  /** A credential must be present before the plan can be approved. */
  requiresCredential: boolean;
  /** Agreement ids the approval must accept (B8 `required_agreements`). */
  requiredAgreements: string[];
  /** The profile this plan installs or repairs; every other profile is preserved. */
  target: DevicePlan;
}

/** The plan for the current choices (B8: read-only, computed before any approval). */
export function planFor(entry: WizardEntry, choices: WizardChoices): SetupPlan {
  const items: PlanItem[] = [];
  if (entry === "repair") {
    items.push(choices.device === "gpu" ? { id: "repairRuntime", downloadMb: 900, diskMb: 2100 } : { id: "repairRuntime", downloadMb: 300, diskMb: 700 });
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
    // The repair mock installs no new model, so it requires no terms; a real repair plan would
    // carry whatever `required_agreements` the backend lists for it.
    requiresCredential: entry !== "repair" && choices.diarization,
    requiredAgreements: entry !== "repair" && choices.diarization ? [DIARIZATION_TERMS] : [],
    target: choices.device,
  };
}

export type Requirement = "credential" | "agreement";

/** Whether a credential exists (found before, or registered in this wizard). Presence only. */
export function credentialPresent(s: Pick<SetupState, "detected" | "choices">): boolean {
  return s.detected.credential || s.choices.credential;
}

/** What the CURRENT plan still needs before it may be approved or run (credential and terms, independently). */
export function missingRequirements(s: Pick<SetupState, "entry" | "choices" | "detected">): Requirement[] {
  const plan = planFor(s.entry, s.choices);
  const missing: Requirement[] = [];
  if (plan.requiresCredential && !credentialPresent(s)) missing.push("credential");
  if (plan.requiredAgreements.some((id) => !s.choices.acceptedAgreements.includes(id))) missing.push("agreement");
  return missing;
}

/**
 * Whether the credential / terms step is part of this run of the wizard: whenever the plan
 * requires a credential or terms — also when a credential already exists, because the terms of
 * this plan still have to be accepted.
 */
export function credentialStepNeeded(s: Pick<SetupState, "entry" | "choices" | "detected">): boolean {
  const plan = planFor(s.entry, s.choices);
  return plan.requiresCredential || plan.requiredAgreements.length > 0;
}

/** The pre-approval steps in order (the run and done pages follow the approval). */
export function wizardSteps(s: Pick<SetupState, "entry" | "choices" | "detected">): WizardStep[] {
  if (s.entry === "repair") return ["review"];
  const steps: WizardStep[] = s.entry === "first_run" ? ["welcome", "environment", "features"] : ["environment", "features"];
  if (credentialStepNeeded(s)) steps.push("credentials");
  steps.push("review");
  return steps;
}

/** Why "next" (or, on the review step, "approve") is unavailable now (null = it can be pressed). */
export function nextBlock(s: SetupState): "gpuMissing" | Requirement | null {
  if (s.step === "environment" && s.choices.device === "gpu" && !s.detected.gpu) return "gpuMissing";
  if (s.step === "credentials" || s.step === "review") return missingRequirements(s)[0] ?? null;
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

const NO_CHOICES: WizardChoices = { device: "gpu", diarization: false, credential: false, acceptedAgreements: [] };

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
      const choices: WizardChoices = { device: "gpu", diarization: false, credential: false, acceptedAgreements: [] };
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
      const choices: WizardChoices = { device: "gpu", diarization: true, credential: true, acceptedAgreements: [DIARIZATION_TERMS] };
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

/** Environment the rest of the prototype starts from for a setup example (and the review-strip label). */
export function envForExample(example: SetupExample): { env: EnvState; label: EnvExample } {
  switch (example) {
    case "first_run":
    case "incomplete":
    case "failed":
      return { env: envFromExample("unconfigured"), label: "unconfigured" };
    case "cpu_only":
      return { env: { ...envFromExample("unconfigured"), gpuDetected: false }, label: "unconfigured" };
    case "needs_repair":
      return { env: envFromExample("repair", true), label: "repair" };
    case "missing_token":
      return { env: envFromExample("gpu", false), label: "gpu" };
    default:
      return { env: envFromExample("gpu", true), label: "gpu" };
  }
}

export interface SetupEffects {
  /** The environment as it is now (the wizard changes it only after a successful run). */
  currentEnvironment(): EnvState;
  /** Shows a new environment (computed from the current one by the wizard, or an example). */
  applyEnvironment(env: EnvState, label: EnvExample | null): void;
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
    const { env, label } = envForExample(example);
    const state = exampleState(example);
    this.store.set({ ...state, detected: { ...state.detected, gpu: env.gpuDetected } });
    this.effects.applyEnvironment(env, label);
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
    const env = this.effects.currentEnvironment();
    // First run keeps what was chosen before the wizard was closed; add / repair start fresh
    // with what the card asked for (e.g. "Set up the GPU…" → device gpu).
    let choices: WizardChoices;
    if (entry === "first_run") choices = { ...s.choices, ...preset };
    else if (entry === "repair") choices = { ...NO_CHOICES, device: preset.device ?? repairTarget(env) ?? "gpu" };
    else {
      const fallback: DevicePlan = env.gpu !== "ready" && env.gpuDetected ? "gpu" : "cpu";
      choices = { ...NO_CHOICES, device: fallback, ...preset, acceptedAgreements: [...(preset.acceptedAgreements ?? [])] };
    }
    const detected = { ...s.detected, gpu: env.gpuDetected };
    if (!detected.gpu && entry !== "repair") choices.device = "cpu";
    const steps = wizardSteps({ ...s, entry, choices, detected });
    this.patch({
      detected,
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

  /** A choice that changes the PLAN (device, diarization): any earlier approval is void (B8 reapproval_required). */
  setChoice<K extends "device" | "diarization">(key: K, value: WizardChoices[K]): void {
    const s = this.store.get();
    if (s.choices[key] === value) return;
    this.patch({ choices: { ...s.choices, [key]: value }, planRevision: s.planRevision + 1, approved: false, approvedRevision: null });
  }

  /** Demo: mark a credential as registered (no value is typed, shown or stored). */
  registerCredential(): void {
    const s = this.store.get();
    this.patch({ choices: { ...s.choices, credential: true } });
  }

  /** Accept or withdraw one set of terms (sent with the next approval). */
  setAgreement(id: string, accepted: boolean): void {
    const s = this.store.get();
    const rest = s.choices.acceptedAgreements.filter((a) => a !== id);
    this.patch({ choices: { ...s.choices, acceptedAgreements: accepted ? [...rest, id] : rest } });
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

  /**
   * Approves the shown plan and starts the (fictional) operation. Refused — whoever calls it — while
   * the plan's credential or terms are missing. Done stages of an earlier attempt are kept.
   * Returns whether the operation started.
   */
  approveAndStart(): boolean {
    const s = this.store.get();
    if (s.step !== "review") return false;
    if (missingRequirements(s).length > 0) {
      this.effects.notify("notice.setupBlocked");
      return false;
    }
    const plan = planFor(s.entry, s.choices);
    const previous = new Map(s.stages.map((st) => [st.id, st.status]));
    const stages = plan.stages.map((id): StageRun => ({ id, status: previous.get(id) === "done" ? "done" : "pending", fraction: null }));
    const first = stages.findIndex((st) => st.status !== "done");
    if (first >= 0) stages[first] = this.enter(stages[first]!);
    this.patch({ approved: true, approvedRevision: s.planRevision, step: first >= 0 ? "run" : "done", run: first >= 0 ? "running" : "succeeded", stages, attempt: s.attempt + 1, errorCode: null });
    this.effects.notify("notice.setupStarted");
    return true;
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
  continueRun(): boolean {
    const s = this.store.get();
    if (s.run !== "failed" && s.run !== "interrupted") return false;
    // Retry / resume reuse the approval only for the plan it was given for, with its requirements met.
    if (!s.approved || s.approvedRevision !== s.planRevision || missingRequirements(s).length > 0) {
      this.effects.notify("notice.setupBlocked");
      return false;
    }
    const stages = s.stages.map((st) => (st.status === "failed" || st.status === "interrupted" ? this.enter(st) : st));
    this.patch({ stages, run: "running", attempt: s.attempt + 1, errorCode: null });
    this.effects.notify(s.run === "failed" ? "notice.setupRetried" : "notice.setupResumed");
    return true;
  }

  /** "Change settings" from a stopped run: back to the choices; a new approval is needed. */
  changeChoices(): void {
    const s = this.store.get();
    if (s.run !== "failed" && s.run !== "interrupted") return;
    const steps = wizardSteps(s);
    const target: WizardStep = steps.includes("features") ? "features" : "review";
    this.patch({ run: "idle", step: target, approved: false, approvedRevision: null, planRevision: s.planRevision + 1, errorCode: null });
  }

  /**
   * Last page, only after a SUCCESSFUL run: the plan's outcome is applied to the CURRENT environment
   * (added / repaired profile only; everything else is preserved). Jobs, queue and settings are
   * not touched — except that a first run without diarization makes "diarization off" the default.
   */
  finish(): void {
    const s = this.store.get();
    if (s.run !== "succeeded") return;
    const before = this.effects.currentEnvironment();
    const env = applySetupOutcome(before, { entry: s.entry, device: s.choices.device, diarization: s.choices.diarization });
    this.store.set({
      ...s,
      configured: true,
      wizardOpen: false,
      run: "idle",
      stages: [],
      step: "welcome",
      example: "configured",
      choices: { ...s.choices, acceptedAgreements: [] },
      detected: { ...s.detected, credential: credentialPresent(s) },
    });
    this.effects.applyEnvironment(env, null);
    if (s.entry === "first_run" && !env.diarization) this.effects.applyDefaults({ diarization: false });
    this.effects.notify("notice.setupDone");
  }
}
