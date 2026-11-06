import { Store } from "../../domain/store";
import { applySetupOutcome, type EnvExample, envFromExample, type EnvState, repairTarget } from "./envExamples";
import {
  DIARIZATION_SIZE,
  MAX_MODEL_REFS,
  modelAllowed,
  modelRef,
  PROFILE_KIND,
  RECOMMENDED_MODEL,
  REPAIR_SIZE,
  RUNTIME_SIZE,
  type SetupDevice,
  type SetupEngine,
  SETUP_ENGINES,
  setupModel,
  VAD_SIZE,
} from "./setupCatalog";

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
 *
 * Iteration 5 (owner memos #26–#28, #30): standard / custom setup with model selection (catalog
 * fixture in `setupCatalog.ts`), the plan installs only the difference to what is installed, and
 * the credential / terms are on the features page (the controller checks them exactly as before).
 */
export type SetupExample = "configured" | "first_run" | "incomplete" | "failed" | "needs_repair" | "cpu_only" | "missing_token";
export const SETUP_EXAMPLES: readonly SetupExample[] = ["configured", "first_run", "incomplete", "failed", "needs_repair", "cpu_only", "missing_token"];

/** Why the wizard was opened. Same screens, different starting point and plan. */
export type WizardEntry = "first_run" | "add" | "repair";
/**
 * Iteration 5 (#26): no separate credentials step any more — the token state and the terms are
 * shown on the "features" page as soon as diarization is switched on.
 */
export type WizardStep = "welcome" | "environment" | "features" | "review" | "run" | "done";
export type DevicePlan = SetupDevice;
/**
 * Standard = take the (fictional) recommendation, with "Change model…"; custom = choose engine,
 * device, models and the main model. Independent of the app's Normal / Expert display mode, and
 * both write the SAME choices, so switching never loses a selection (iteration 5, #30).
 */
export type SetupMethod = "standard" | "custom";
export type StageId = "preflight" | "runtime" | "vad_model" | "asr_model" | "diarization_model" | "smoke";
export type StageStatus = "pending" | "running" | "done" | "failed" | "interrupted";
/** B8 operation states the prototype shows (subset; see iteration-4.md §3 for the full mapping). */
export type RunState = "idle" | "running" | "interrupted" | "failed" | "succeeded";

export interface Detected {
  gpu: boolean;
  /** A diarization credential already exists (only its presence is ever known, never its value). */
  credential: boolean;
}

/**
 * What is already installed, copied from the environment when the wizard opens (the analogue of
 * B8's inventory revision). The plan installs only the difference.
 */
export interface Inventory {
  ready: Record<DevicePlan, boolean>;
  models: Record<DevicePlan, string[]>;
}
export const EMPTY_INVENTORY: Inventory = { ready: { gpu: false, cpu: false }, models: { gpu: [], cpu: [] } };

export function inventoryOf(env: EnvState): Inventory {
  return {
    ready: { gpu: env.gpu === "ready", cpu: env.cpu === "ready" },
    models: { gpu: env.gpu === "absent" ? [] : [...env.models.gpu], cpu: env.cpu === "absent" ? [] : [...env.models.cpu] },
  };
}

export interface WizardChoices {
  method: SetupMethod;
  engine: SetupEngine;
  /** The profile to install (first run / add) or to repair (repair). */
  device: DevicePlan;
  /** Every ASR model the profile should have after the run (installed ones included; B8 `model_refs`). */
  models: string[];
  /** The model the profile uses by default (applies only if the profile has no default yet). */
  primaryModel: string;
  /** False while the models simply follow the recommendation for the device. */
  modelsTouched: boolean;
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
  inventory: Inventory;
  choices: WizardChoices;
  approved: boolean;
  /** Bumped whenever the plan changes; an approval belongs to one revision. */
  planRevision: number;
  approvedRevision: number | null;
  /**
   * Identity of the plan the current `stages` belong to (`planKey`), set when a plan is approved.
   * Review fix P1-1: stages are never carried over to a DIFFERENT plan — only retry / resume of the
   * same approved plan keeps finished stages, and `finish()` applies nothing unless this matches.
   */
  runPlanKey: string | null;
  run: RunState;
  stages: StageRun[];
  attempt: number;
  errorCode: string | null;
}

export interface PlanItem {
  id: "runtime" | "asrModel" | "vadModel" | "diarizationModel" | "repairRuntime";
  /** The ASR model of an `asrModel` item. */
  modelId?: string;
  /** Synthetic sizes in MB (examples, not measured); null = unknown (B8 `bytes_unknown`). */
  downloadMb: number | null;
  diskMb: number | null;
}

export interface SetupPlan {
  items: PlanItem[];
  stages: StageId[];
  /** Sum of the KNOWN sizes; `sizeUnknown` says whether some item's size is not known. */
  downloadMb: number;
  diskMb: number;
  sizeUnknown: boolean;
  /** A credential must be present before the plan can be approved. */
  requiresCredential: boolean;
  /** Agreement ids the approval must accept (B8 `required_agreements`). */
  requiredAgreements: string[];
  /** The profile this plan installs or repairs; every other profile is preserved. */
  target: DevicePlan;
  /** Models this plan downloads (already installed ones are not downloaded again). */
  newModels: string[];
}

/** Credential / terms of a plan: they depend on the diarization choice only. */
function requirementsOf(entry: WizardEntry, choices: Pick<WizardChoices, "diarization">): Pick<SetupPlan, "requiresCredential" | "requiredAgreements"> {
  const on = entry !== "repair" && choices.diarization;
  // The repair mock installs no new model, so it requires no terms; a real repair plan would
  // carry whatever `required_agreements` the backend lists for it.
  return { requiresCredential: on, requiredAgreements: on ? [DIARIZATION_TERMS] : [] };
}

/**
 * The plan for the current choices (B8: read-only, computed before any approval). Only the
 * DIFFERENCE to the inventory is installed: a ready profile gets no new runtime, an installed
 * model is not downloaded again.
 */
export function planFor(entry: WizardEntry, choices: WizardChoices, inventory: Inventory = EMPTY_INVENTORY): SetupPlan {
  const items: PlanItem[] = [];
  const device = choices.device;
  let newModels: string[] = [];
  if (entry === "repair") {
    items.push({ id: "repairRuntime", ...REPAIR_SIZE[device] });
  } else {
    const ready = inventory.ready[device];
    if (!ready) items.push({ id: "runtime", ...RUNTIME_SIZE[device] });
    newModels = choices.models.filter((m) => !(ready && inventory.models[device].includes(m)));
    for (const m of newModels) {
      const size = setupModel(m)?.sizeMb ?? null;
      items.push({ id: "asrModel", modelId: m, downloadMb: size, diskMb: size });
    }
    if (!ready) items.push({ id: "vadModel", ...VAD_SIZE });
    if (choices.diarization) items.push({ id: "diarizationModel", ...DIARIZATION_SIZE });
  }
  const has = (id: PlanItem["id"]) => items.some((i) => i.id === id);
  const stages: StageId[] = ["preflight"];
  if (has("runtime") || has("repairRuntime")) stages.push("runtime");
  if (has("vadModel")) stages.push("vad_model");
  if (has("asrModel")) stages.push("asr_model");
  if (has("diarizationModel")) stages.push("diarization_model");
  stages.push("smoke");
  const sum = (k: "downloadMb" | "diskMb") => items.reduce((n, i) => n + (i[k] ?? 0), 0);
  return {
    items,
    stages,
    downloadMb: sum("downloadMb"),
    diskMb: sum("diskMb"),
    sizeUnknown: items.some((i) => i.downloadMb === null || i.diskMb === null),
    ...requirementsOf(entry, choices),
    target: device,
    newModels,
  };
}

/** The B8 `SetupPlanRequest` fields the choices stand for (shape check only; nothing is sent). */
export function planRequest(choices: WizardChoices): { profile_kind: "cuda" | "cpu"; model_refs: string[]; diarization_enabled: boolean } {
  return { profile_kind: PROFILE_KIND[choices.device], model_refs: choices.models.map((m) => modelRef(choices.engine, m)), diarization_enabled: choices.diarization };
}

/**
 * Everything that decides WHAT a plan installs (entry, target, engine, models, main model, features,
 * and the inventory it was computed against). Two plans with the same stage ids but a different key
 * install different things, so a finished stage of one never counts for the other.
 */
export function planKey(entry: WizardEntry, choices: WizardChoices, inventory: Inventory): string {
  const plan = planFor(entry, choices, inventory);
  return JSON.stringify({
    entry,
    device: choices.device,
    engine: choices.engine,
    models: [...choices.models].sort(),
    primary: choices.primaryModel,
    diarization: choices.diarization,
    items: plan.items.map((i) => `${i.id}:${i.modelId ?? ""}`),
    stages: plan.stages,
  });
}

/** Chosen models that the chosen engine/device does not offer (they block "next"). */
export function unavailableModels(choices: Pick<WizardChoices, "models" | "engine" | "device">): string[] {
  return choices.models.filter((m) => !modelAllowed(m, choices.engine, choices.device));
}

/** Models the profile will have without asking: the installed ones, else the recommendation. */
function baseModels(inventory: Inventory, device: DevicePlan): string[] {
  const installed = inventory.ready[device] ? inventory.models[device] : [];
  return installed.length > 0 ? [...installed] : [RECOMMENDED_MODEL[device]];
}

/** Models after a device change: untouched choices follow the device; touched ones are kept (and checked). */
function modelsAfterDevice(choices: WizardChoices, inventory: Inventory, device: DevicePlan): Pick<WizardChoices, "models" | "primaryModel"> {
  const base = baseModels(inventory, device);
  if (!choices.modelsTouched) return { models: base, primaryModel: base[0]! };
  const installed = inventory.ready[device] ? inventory.models[device] : [];
  const models = [...installed, ...choices.models.filter((m) => !installed.includes(m))];
  return { models, primaryModel: models.includes(choices.primaryModel) ? choices.primaryModel : models[0]! };
}

/** Installed models of the target profile: always part of the plan, never removed by this wizard. */
export function lockedModels(s: Pick<SetupState, "entry" | "choices" | "inventory">): string[] {
  if (s.entry === "repair" || !s.inventory.ready[s.choices.device]) return [];
  return s.inventory.models[s.choices.device];
}

/** Whether the main-model choice applies (a ready profile keeps its default model). */
export function primaryChoosable(s: Pick<SetupState, "entry" | "choices" | "inventory">): boolean {
  return !s.inventory.ready[s.choices.device];
}

export type Requirement = "credential" | "agreement";

/** Whether a credential exists (found before, or registered in this wizard). Presence only. */
export function credentialPresent(s: Pick<SetupState, "detected" | "choices">): boolean {
  return s.detected.credential || s.choices.credential;
}

/** What the CURRENT plan still needs before it may be approved or run (credential and terms, independently). */
export function missingRequirements(s: Pick<SetupState, "entry" | "choices" | "detected">): Requirement[] {
  const req = requirementsOf(s.entry, s.choices);
  const missing: Requirement[] = [];
  if (req.requiresCredential && !credentialPresent(s)) missing.push("credential");
  if (req.requiredAgreements.some((id) => !s.choices.acceptedAgreements.includes(id))) missing.push("agreement");
  return missing;
}

/** The pre-approval steps in order (the run and done pages follow the approval). */
export function wizardSteps(s: Pick<SetupState, "entry"> & Partial<Pick<SetupState, "choices">>): WizardStep[] {
  if (s.entry === "repair") return ["review"];
  return s.entry === "first_run" ? ["welcome", "environment", "features", "review"] : ["environment", "features", "review"];
}

export type NextBlock = "gpuMissing" | "noModel" | "modelUnavailable" | "tooManyModels" | "nothingToInstall" | Requirement;

/** Why "next" (or, on the review step, "start") is unavailable now (null = it can be pressed). */
export function nextBlock(s: SetupState): NextBlock | null {
  const modelBlock = (): NextBlock | null => {
    if (s.choices.models.length === 0) return "noModel";
    if (s.choices.models.length > MAX_MODEL_REFS) return "tooManyModels";
    if (unavailableModels(s.choices).length > 0) return "modelUnavailable";
    return null;
  };
  if (s.step === "environment") {
    if (s.choices.device === "gpu" && !s.detected.gpu) return "gpuMissing";
    return modelBlock();
  }
  if (s.step === "features") return missingRequirements(s)[0] ?? null;
  if (s.step === "review") {
    if (s.entry !== "repair") {
      const m = modelBlock();
      if (m) return m;
    }
    const missing = missingRequirements(s)[0];
    if (missing) return missing;
    if (s.entry !== "repair" && planFor(s.entry, s.choices, s.inventory).items.length === 0) return "nothingToInstall";
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

function noChoices(device: DevicePlan = "gpu"): WizardChoices {
  const m = RECOMMENDED_MODEL[device];
  return { method: "standard", engine: "faster-whisper", device, models: [m], primaryModel: m, modelsTouched: false, diarization: false, credential: false, acceptedAgreements: [] };
}

function base(example: SetupExample): SetupState {
  return {
    example,
    configured: true,
    wizardOpen: false,
    entry: "first_run",
    step: "welcome",
    detected: { gpu: true, credential: true },
    inventory: EMPTY_INVENTORY,
    choices: noChoices(),
    approved: false,
    planRevision: 1,
    approvedRevision: null,
    runPlanKey: null,
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
      return { ...s, configured: false, wizardOpen: true, detected: { gpu: false, credential: false }, choices: noChoices("cpu") };
    case "incomplete": {
      const choices = noChoices();
      return {
        ...s,
        configured: false,
        wizardOpen: true,
        step: "run",
        detected: { gpu: true, credential: false },
        choices,
        approved: true,
        approvedRevision: 1,
        runPlanKey: planKey("first_run", choices, EMPTY_INVENTORY),
        run: "interrupted",
        attempt: 1,
        stages: stagesOf(planFor("first_run", choices).stages, { preflight: "done", runtime: "done", vad_model: "done", asr_model: "interrupted" }),
      };
    }
    case "failed": {
      const choices: WizardChoices = { ...noChoices(), diarization: true, credential: true, acceptedAgreements: [DIARIZATION_TERMS] };
      return {
        ...s,
        configured: false,
        wizardOpen: true,
        step: "run",
        detected: { gpu: true, credential: false },
        choices,
        approved: true,
        approvedRevision: 1,
        runPlanKey: planKey("first_run", choices, EMPTY_INVENTORY),
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

/** Common processing defaults a first setup may set (a subset of the prototype's ProcessingSettings). */
export interface SetupDefaults {
  backend: SetupEngine;
  model: string;
  device: "cuda" | "cpu";
  computeType: "auto";
  diarization: boolean;
}

export interface SetupEffects {
  /** The environment as it is now (the wizard changes it only after a successful run). */
  currentEnvironment(): EnvState;
  /** Shows a new environment (computed from the current one by the wizard, or an example). */
  applyEnvironment(env: EnvState, label: EnvExample | null): void;
  /** Defaults that follow the wizard's feature choices (e.g. diarization off when it was not installed). */
  applyDefaults(patch: SetupDefaults): void;
  notify(key: string, params?: Record<string, string | number>): void;
}

/** What a completed first setup writes into the common processing defaults. */
export function firstRunDefaults(choices: WizardChoices, diarizationReady: boolean): SetupDefaults {
  return { backend: choices.engine, model: choices.primaryModel, device: choices.device === "gpu" ? "cuda" : "cpu", computeType: "auto", diarization: diarizationReady };
}

/**
 * A run counts as completed only when every stage of the CURRENT approved plan is done: approved,
 * the approval is for the current revision, the stages belong to this plan, and none is missing.
 */
export function runCompleted(s: SetupState): boolean {
  if (s.run !== "succeeded" || !s.approved || s.approvedRevision !== s.planRevision) return false;
  if (s.runPlanKey !== planKey(s.entry, s.choices, s.inventory)) return false;
  const expected = planFor(s.entry, s.choices, s.inventory).stages;
  return s.stages.length === expected.length && s.stages.every((st, i) => st.id === expected[i] && st.status === "done");
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
    this.store.set({ ...state, detected: { ...state.detected, gpu: env.gpuDetected }, inventory: inventoryOf(env) });
    this.effects.applyEnvironment(env, label);
  }

  /**
   * Opens the wizard. `add` = install something more (a profile, models, diarization) on a working
   * setup; `repair` = fix a broken profile; `first_run` = nothing usable yet. An unfinished
   * operation is shown as it is (resume) instead of starting over.
   */
  open(entry: WizardEntry, preset: Partial<WizardChoices> = {}): void {
    const s = this.store.get();
    if (s.run === "interrupted" || s.run === "failed" || s.run === "running") {
      this.patch({ wizardOpen: true, step: "run" });
      return;
    }
    const env = this.effects.currentEnvironment();
    const inventory = inventoryOf(env);
    // First run keeps what was chosen before the wizard was closed; add / repair start fresh
    // with what the card asked for (e.g. "Set up the GPU…" → device gpu).
    let choices: WizardChoices;
    if (entry === "first_run") choices = { ...s.choices, ...preset };
    else if (entry === "repair") choices = noChoices(preset.device ?? repairTarget(env) ?? "gpu");
    else {
      const fallback: DevicePlan = env.gpu !== "ready" && env.gpuDetected ? "gpu" : "cpu";
      choices = { ...noChoices(preset.device ?? fallback), ...preset, acceptedAgreements: [...(preset.acceptedAgreements ?? [])] };
    }
    const detected = { ...s.detected, gpu: env.gpuDetected };
    if (!detected.gpu && entry !== "repair") choices.device = "cpu";
    if (entry !== "repair") choices = { ...choices, ...modelsAfterDevice(choices, inventory, choices.device) };
    const steps = wizardSteps({ entry });
    this.patch({
      detected,
      inventory,
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

  /** Every change of the PLAN voids an earlier approval (B8 reapproval_required). */
  private changePlan(choices: WizardChoices): void {
    const s = this.store.get();
    this.patch({ choices, planRevision: s.planRevision + 1, approved: false, approvedRevision: null });
  }

  /** A choice that changes the PLAN (device, diarization). A device change re-checks the chosen models. */
  setChoice<K extends "device" | "diarization">(key: K, value: WizardChoices[K]): void {
    const s = this.store.get();
    if (s.choices[key] === value) return;
    let choices: WizardChoices = { ...s.choices, [key]: value };
    if (key === "device") choices = { ...choices, ...modelsAfterDevice(s.choices, s.inventory, value as DevicePlan) };
    this.changePlan(choices);
  }

  /** Standard ↔ custom only changes how the choices are shown; nothing is lost or re-planned. */
  setMethod(method: SetupMethod): void {
    const s = this.store.get();
    if (s.choices.method !== method) this.patch({ choices: { ...s.choices, method } });
  }

  setEngine(engine: SetupEngine): void {
    const s = this.store.get();
    if (s.choices.engine === engine || !SETUP_ENGINES.some((e) => e.id === engine && e.selectable)) return;
    this.changePlan({ ...s.choices, engine });
  }

  /** Standard "Change model…": exactly this model (plus the installed ones, which are never removed). */
  chooseModel(id: string): void {
    const s = this.store.get();
    if (!modelAllowed(id, s.choices.engine, s.choices.device)) return;
    const locked = lockedModels(s);
    const models = locked.includes(id) ? [...locked] : [...locked, id];
    this.changePlan({ ...s.choices, models, primaryModel: primaryChoosable(s) ? id : s.choices.primaryModel, modelsTouched: true });
  }

  /** Custom: add or remove one model. Installed models and offers the device lacks cannot be added/removed. */
  toggleModel(id: string, on: boolean): void {
    const s = this.store.get();
    if (lockedModels(s).includes(id)) return;
    if (on && (!modelAllowed(id, s.choices.engine, s.choices.device) || s.choices.models.includes(id) || s.choices.models.length >= MAX_MODEL_REFS)) return;
    if (!on && !s.choices.models.includes(id)) return;
    const models = on ? [...s.choices.models, id] : s.choices.models.filter((m) => m !== id);
    const primaryModel = models.includes(s.choices.primaryModel) ? s.choices.primaryModel : (models[0] ?? "");
    this.changePlan({ ...s.choices, models, primaryModel, modelsTouched: true });
  }

  setPrimaryModel(id: string): void {
    const s = this.store.get();
    if (!s.choices.models.includes(id) || s.choices.primaryModel === id) return;
    this.changePlan({ ...s.choices, primaryModel: id, modelsTouched: true });
  }

  /** "Remove the models this device cannot use" after a device change. */
  removeUnavailableModels(): void {
    const s = this.store.get();
    const bad = unavailableModels(s.choices);
    if (bad.length === 0) return;
    let models = s.choices.models.filter((m) => !bad.includes(m));
    if (models.length === 0) models = baseModels(s.inventory, s.choices.device);
    this.changePlan({ ...s.choices, models, primaryModel: models.includes(s.choices.primaryModel) ? s.choices.primaryModel : models[0]! });
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

  /** Jump to an earlier step (the review's "Change" links). */
  goTo(step: WizardStep): void {
    const s = this.store.get();
    const steps = wizardSteps(s);
    if (steps.indexOf(step) >= 0 && steps.indexOf(step) < steps.indexOf(s.step)) this.patch({ step });
  }

  /**
   * Approves the shown plan and starts the (fictional) operation. Refused — whoever calls it — while
   * the plan's credential or terms are missing or the plan is not valid. Every stage of the plan
   * runs (nothing is carried over from an earlier approval). Returns whether the operation started.
   */
  approveAndStart(): boolean {
    const s = this.store.get();
    if (s.step !== "review") return false;
    if (nextBlock(s) !== null) {
      this.effects.notify("notice.setupBlocked");
      return false;
    }
    const plan = planFor(s.entry, s.choices, s.inventory);
    // Review fix P1-1: a (re-)approved plan always starts with ALL of its own stages pending. Stages
    // finished under an earlier approval are not reused, even when the stage id is the same
    // ("asr_model" of model A is not "asr_model" of model B). Only continueRun() — retry / resume
    // of the SAME approved plan — keeps finished stages.
    const stages = plan.stages.map((id): StageRun => ({ id, status: "pending", fraction: null }));
    stages[0] = this.enter(stages[0]!);
    this.patch({ approved: true, approvedRevision: s.planRevision, runPlanKey: planKey(s.entry, s.choices, s.inventory), step: "run", run: "running", stages, attempt: s.attempt + 1, errorCode: null });
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
    if (!s.approved || s.approvedRevision !== s.planRevision || s.runPlanKey !== planKey(s.entry, s.choices, s.inventory) || missingRequirements(s).length > 0) {
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
    // The stopped run's stages are dropped: whatever is approved next starts from its own first stage.
    this.patch({ run: "idle", step: target, approved: false, approvedRevision: null, runPlanKey: null, stages: [], planRevision: s.planRevision + 1, errorCode: null });
  }

  /**
   * Last page, only after a SUCCESSFUL run: the plan's outcome is applied to the CURRENT environment
   * (added / repaired profile and added models only; everything else is preserved). Jobs, queue and
   * settings are not touched — except that a completed FIRST run makes the chosen engine / main
   * model / device the common defaults (and "diarization off" when it was not set up).
   * Refused unless the run that succeeded belongs to the currently approved plan (review fix P1-1).
   */
  finish(): void {
    const s = this.store.get();
    if (!runCompleted(s)) return;
    const before = this.effects.currentEnvironment();
    const plan = planFor(s.entry, s.choices, s.inventory);
    const env = applySetupOutcome(before, { entry: s.entry, device: s.choices.device, diarization: s.choices.diarization, models: plan.newModels, primaryModel: primaryChoosable(s) ? s.choices.primaryModel : null });
    this.store.set({
      ...s,
      configured: true,
      wizardOpen: false,
      run: "idle",
      stages: [],
      step: "welcome",
      example: "configured",
      inventory: inventoryOf(env),
      choices: { ...s.choices, acceptedAgreements: [] },
      detected: { ...s.detected, credential: credentialPresent(s) },
    });
    this.effects.applyEnvironment(env, null);
    // Review fix P1-2: only a completed FIRST setup sets the common defaults, in ONE patch so the
    // engine group is normalised once (never via "auto" and its example models): the chosen engine,
    // the main model and the device of the installed profile. Diarization is on only if it is set up.
    // Add / repair never touch processing settings, presets, overrides or job snapshots.
    if (s.entry === "first_run") this.effects.applyDefaults(firstRunDefaults(s.choices, env.diarization));
    this.effects.notify("notice.setupDone");
  }
}
