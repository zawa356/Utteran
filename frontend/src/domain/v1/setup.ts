import type { ApiError } from "../../api/errors";
import { MutationIntent } from "../../api/idempotency";
import {
  type Diagnostics,
  type ProfileKind,
  type Readiness,
  type SetupApi,
  type SetupApproval,
  type SetupOperation,
  type SetupPlan,
  TERMINAL_SETUP,
} from "../../api/setup";
import type { JsonObject, PolicyV1, PresetV1, V1ResourcesApi } from "../../api/v1Resources";
import { toApiError } from "../operation";
import type { QueueMutationGate } from "./queueGate";
import { Store } from "../store";
import template from "./presetTemplate.json";

/**
 * 束F: the new GUI's setup path on top of B8 (plan → approval → durable operation), the F-B
 * diagnostics and the F-A readiness check. Nothing here runs anything itself:
 *
 * - A plan is created only on an explicit action, reviewed, and started only after the person
 *   confirms it (approval + start). Download, venv creation or verification never start implicitly.
 * - Setup success is not readiness: after success the controller *asks* B7/B4 (readiness check).
 * - Common defaults (one Preset + one Policy) are created only after a successful setup and only
 *   while the settings store has no Preset and no Policy at all; existing settings are never
 *   changed. The Idempotency-Key is derived from the operation id, so a repeat (double click,
 *   reload, second window) replays the same resources instead of creating more.
 * - Polling reads `GET /setup-operations/{id}` only; a reload recovers the newest unfinished
 *   operation from the list (the server is the source of truth).
 */

/** Models whose managed copy the B3 worker / B4 smoke can load (preprocessor_config.json). */
export const WORKER_MODELS = ["large-v3-turbo", "large-v3", "kotoba-whisper-v2.0"] as const;
export const ASR_MODELS = ["large-v3-turbo", "large-v3", "kotoba-whisper-v2.0", "medium", "small", "base", "tiny"] as const;
export const DIARIZATION_REF = "pyannote:pyannote/speaker-diarization-community-1";
/** Profiles the setup can verify end to end (B4 recipes fw-cpu / fw-cuda). */
export const VERIFIABLE_PROFILES: readonly ProfileKind[] = ["cpu", "cuda", "intel", "vulkan"];
export const NATIVE_MODELS = ["base", "large-v3-turbo-q5_0"] as const;
export const nativeProfile = (profile: ProfileKind): boolean => profile === "intel" || profile === "vulkan";
export type NativeVariant = "vulkan" | "openvino_vulkan" | "openvino";
export type DiarizationDevice = "auto" | "cpu" | "xpu" | "cuda";
export const POLL_MS = 1000;

export interface ResourceRef {
  id: string;
  revision: number;
}
export type SetupMode = "standard" | "custom" | "verify";
export type Phase = "idle" | "pending" | "done" | "error";

export interface SetupChoice {
  profile: ProfileKind;
  models: string[]; // ASR model ids (faster-whisper)
  primary: string;
  diarization: boolean;
  agreementAccepted: boolean;
  variant?: NativeVariant;
  diarizationDevice?: DiarizationDevice;
  numSpeakers?: number | null;
}

export interface SetupState {
  diagnostics: { phase: Phase; data: Diagnostics | null; error: ApiError | null };
  mode: SetupMode;
  choice: SetupChoice;
  plan: { phase: Phase; plan: SetupPlan | null; error: ApiError | null; unknown: boolean };
  start: { phase: Phase; error: ApiError | null; unknown: boolean };
  operation: SetupOperation | null;
  operationError: ApiError | null;
  action: { phase: Phase; error: ApiError | null };
  defaults: { phase: "idle" | "pending" | "created" | "kept" | "error"; error: ApiError | null };
  /** `key` = the Preset/Policy revisions the answer is for (PR #91 P3: never shown for another). */
  readiness: { phase: Phase; data: Readiness | null; error: ApiError | null; key: string | null };
  /** Settings of the planned verification (kept in memory only, for the defaults/readiness). */
  verifiedSettings: JsonObject | null;
  /**
   * The shared B6/B7 queue: v1 starts with dispatch paused (never resumed automatically), so a
   * queued setup waits until the person resumes dispatch (which also lets waiting jobs run).
   */
  queue: { dispatch: "running" | "paused" | null; revision: number | null; ahead: number; phase: Phase; error: ApiError | null };
}

const idle = { phase: "idle", error: null } as const;

export function initialChoice(profile: ProfileKind = "cpu"): SetupChoice {
  const primary = nativeProfile(profile) ? "base" : "large-v3-turbo";
  return { profile, models: [primary], primary, diarization: false, agreementAccepted: false };
}

/** The common default settings: the shared template with the primary model, ASR only. */
export function defaultSettings(primary: string, choice?: SetupChoice): JsonObject {
  const settings = JSON.parse(JSON.stringify(template)) as JsonObject;
  (settings.asr as JsonObject).model = primary;
  (settings.asr as JsonObject).backend = "faster-whisper";
  if (choice && nativeProfile(choice.profile)) {
    (settings.asr as JsonObject).backend = "whisper-cpp";
    (settings.asr as JsonObject).whisper_cpp = { variant: choice.variant ?? "vulkan" };
  }
  const diarization = settings.diarization as JsonObject;
  diarization.enabled = choice?.diarization ?? false;
  if (diarization.enabled) {
    diarization.device_preference = choice?.diarizationDevice ?? "auto";
    diarization.num_speakers = choice?.numSpeakers ?? null;
  }
  return settings;
}

/** Why a settings value cannot be verified by the setup (null when it can). */
export function verifyRefusal(settings: JsonObject | null): string | null {
  if (!settings) return "no_preset";
  const asr = settings.asr as JsonObject | undefined;
  if (!asr || (asr.backend !== "faster-whisper" && asr.backend !== "whisper-cpp")) return "backend_unsupported";
  const models: readonly string[] = asr.backend === "whisper-cpp" ? NATIVE_MODELS : WORKER_MODELS;
  if (!models.includes(String(asr.model))) return "model_unsupported";
  return null;
}

export function planRequest(choice: SetupChoice, settings: JsonObject, applyDefaults = false): JsonObject {
  const asr = settings.asr as JsonObject;
  const refs = [choice.primary, ...choice.models.filter((m) => m !== choice.primary)].map((m) => `${asr.backend}:${m}`);
  if (asr.backend === "whisper-cpp" && (asr.vad as JsonObject).enabled === true) {
    refs.push("whisper-cpp-vad:silero-v6.2.0");
  }
  if (choice.diarization) refs.push(DIARIZATION_REF);
  return {
    schema_version: 1,
    profile_kind: choice.profile,
    model_refs: refs,
    diarization_enabled: choice.diarization,
    verification_settings: settings,
    ...(applyDefaults ? { apply_defaults: true } : {}),
  };
}

/** Identity of a readiness answer: exact Preset and Policy revisions (null when incomplete). */
export function readinessKey(preset: { id: string; revision: number } | null, policy: { id: string; revision: number } | null): string | null {
  return preset && policy ? `${preset.id}@${preset.revision}|${policy.id}@${policy.revision}` : null;
}

export function isTerminal(op: SetupOperation | null): boolean {
  return op !== null && TERMINAL_SETUP.has(op.status);
}

export class SetupController {
  readonly store = new Store<SetupState>({
    diagnostics: { phase: "idle", data: null, error: null },
    mode: "standard",
    choice: initialChoice(),
    plan: { phase: "idle", plan: null, error: null, unknown: false },
    start: { phase: "idle", error: null, unknown: false },
    operation: null,
    operationError: null,
    action: idle,
    defaults: idle,
    readiness: { phase: "idle", data: null, error: null, key: null },
    verifiedSettings: null,
    queue: { dispatch: null, revision: null, ahead: 0, phase: "idle", error: null },
  });
  private readonly queueOwner = Symbol("setup-dispatch");
  private timer: ReturnType<typeof setTimeout> | null = null;
  private diagTimer: ReturnType<typeof setTimeout> | null = null;
  private planIntent: MutationIntent<JsonObject> | null = null;
  private startIntent: MutationIntent<JsonObject> | null = null;
  private approvalIntent: MutationIntent<JsonObject> | null = null;
  /** An approval the server accepted for the current plan (reused when only the start failed). */
  private approval: SetupApproval | null = null;
  private finished = new Set<string>();
  /** The person chose a profile themselves: a later detection never replaces it. */
  private profileTouched = false;
  private readinessGeneration = 0;
  private disposed = false;

  constructor(
    private readonly api: SetupApi,
    private readonly resources: V1ResourcesApi,
    private readonly hooks: {
      /** The selected Preset/Policy of the screen (re-verification, readiness). */
      selection?: () => { preset: PresetV1 | null; policy: PolicyV1 | null };
      /** After defaults were created, the screen reloads and selects them. */
      onDefaults?: (preset: ResourceRef, policy: ResourceRef) => Promise<void> | void;
      pollMs?: number;
      /** The app-wide queue write lease (shared with the job board and single job). */
      gate?: QueueMutationGate;
    } = {},
  ) {}

  // ---------------------------------------------------------------- diagnostics

  /** Read the last diagnostics (GET only; opening the page never starts a probe). */
  async loadDiagnostics(): Promise<void> {
    this.patchDiag({ phase: "pending", error: null });
    try {
      this.applyDiagnostics(await this.api.getDiagnostics());
    } catch (error) {
      this.patchDiag({ phase: "error", error: toApiError(error) });
    }
  }

  /**
   * Explicit "check" (first pass: cached runtime report allowed) or "detect again" (fresh
   * runtime probe). The server runs one pass at a time and answers at once.
   */
  async redetect(fresh = true): Promise<void> {
    this.patchDiag({ phase: "pending", error: null });
    try {
      this.applyDiagnostics(await this.api.refreshDiagnostics(true, fresh));
    } catch (error) {
      this.patchDiag({ phase: "error", error: toApiError(error) });
    }
  }

  private applyDiagnostics(data: Diagnostics): void {
    this.patchDiag({ phase: data.phase === "running" ? "pending" : "done", data, error: null });
    const recommended = data.recommendation?.profile;
    const state = this.store.get();
    // Follow the recommendation only while nothing was chosen or planned: a reviewed plan is
    // never changed under the person (and an edited profile is theirs).
    if (
      recommended &&
      VERIFIABLE_PROFILES.includes(recommended) &&
      state.mode === "standard" &&
      !this.profileTouched &&
      !this.busy() &&
      state.plan.plan === null &&
      state.choice.profile !== recommended
    ) {
      this.store.patch({ choice: { ...state.choice, profile: recommended } });
    }
    if (data.phase === "running" && !this.disposed) {
      if (this.diagTimer) clearTimeout(this.diagTimer);
      this.diagTimer = setTimeout(() => void this.pollDiagnostics(), this.hooks.pollMs ?? POLL_MS);
    }
  }

  private async pollDiagnostics(): Promise<void> {
    this.diagTimer = null;
    try {
      this.applyDiagnostics(await this.api.getDiagnostics());
    } catch (error) {
      this.patchDiag({ phase: "error", error: toApiError(error) });
    }
  }

  private patchDiag(partial: Partial<SetupState["diagnostics"]>): void {
    this.store.patch({ diagnostics: { ...this.store.get().diagnostics, ...partial } });
  }

  // ---------------------------------------------------------------- choice

  setMode(mode: SetupMode): void {
    if (this.busy()) return;
    const recommended = this.store.get().diagnostics.data?.recommendation?.profile;
    const profile = recommended && VERIFIABLE_PROFILES.includes(recommended) ? recommended : "cpu";
    if (mode === "standard") this.profileTouched = false; // the standard setup follows detection
    this.store.patch({ mode, choice: mode === "standard" ? initialChoice(profile) : this.store.get().choice });
    this.resetPlan();
  }

  edit(partial: Partial<SetupChoice>): void {
    if (this.busy()) return;
    if (partial.profile !== undefined) this.profileTouched = true;
    const current = this.store.get().choice;
    const next = { ...current, ...partial };
    if (partial.profile && partial.profile !== current.profile) {
      const defaults = initialChoice(partial.profile);
      next.models = defaults.models;
      next.primary = defaults.primary;
      next.variant = "vulkan";
      next.diarizationDevice = "auto";
    }
    if (!next.models.includes(next.primary)) next.models = [next.primary, ...next.models];
    if (!next.diarization) next.agreementAccepted = false;
    this.store.patch({ choice: next });
    this.resetPlan();
  }

  busy(): boolean {
    const s = this.store.get();
    return s.plan.phase === "pending" || s.start.phase === "pending" || (s.operation !== null && !isTerminal(s.operation));
  }

  private resetPlan(): void {
    this.planIntent = null;
    this.approvalIntent = null;
    this.approval = null;
    this.startIntent = null;
    this.store.patch({ plan: { phase: "idle", plan: null, error: null, unknown: false }, start: { phase: "idle", error: null, unknown: false } });
  }

  // ---------------------------------------------------------------- plan / start

  /** Build the plan (read-only on the server). For "verify" the selected Preset is verified. */
  async createPlan(): Promise<boolean> {
    const state = this.store.get();
    if (this.busy()) return false;
    let settings: JsonObject;
    let choice = state.choice;
    if (state.mode === "verify") {
      const preset = this.hooks.selection?.().preset ?? null;
      if (verifyRefusal(preset?.settings ?? null) !== null || !preset) return false;
      settings = preset.settings;
      const model = String((settings.asr as JsonObject).model);
      choice = { ...choice, models: [model], primary: model, diarization: (settings.diarization as JsonObject).enabled === true };
    } else {
      settings = defaultSettings(choice.primary, choice);
    }
    if (!this.planIntent || state.plan.phase !== "error" || !state.plan.unknown) {
      // First setup / additional setup: ask the server to create defaults if (and only if)
      // the settings store is empty. A re-verification never does.
      this.planIntent = new MutationIntent(planRequest(choice, settings, state.mode !== "verify"));
    }
    // A replayed (unknown-outcome) intent keeps its own body: remember exactly what it verifies.
    const sent = this.planIntent.body.verification_settings as JsonObject;
    this.store.patch({ plan: { phase: "pending", plan: null, error: null, unknown: false }, verifiedSettings: sent });
    try {
      const plan = await this.api.createPlan(this.planIntent);
      this.store.patch({ plan: { phase: "done", plan, error: null, unknown: false } });
      return true;
    } catch (error) {
      const e = toApiError(error);
      this.store.patch({ plan: { phase: "error", plan: null, error: e, unknown: e.outcomeUnknown } });
      return false;
    }
  }

  async discardPlan(): Promise<void> {
    const plan = this.store.get().plan.plan;
    if (plan && plan.state === "proposed") {
      try {
        await this.api.discardPlan(plan.id, plan.revision);
      } catch {
        // An expired or consumed plan is discarded by the server's own TTL; nothing to undo here.
      }
    }
    this.resetPlan();
  }

  /** Confirmed by the person: approve the reviewed plan, then start the durable operation. */
  async confirmAndStart(): Promise<boolean> {
    const state = this.store.get();
    const plan = state.plan.plan;
    if (!plan || state.start.phase === "pending" || this.busy()) return false;
    if (plan.missingCredentials.length > 0) return false;
    if (plan.requiredAgreements.length > 0 && !state.choice.agreementAccepted) return false;
    this.store.patch({ start: { phase: "pending", error: null, unknown: false } });
    try {
      this.approvalIntent ??= new MutationIntent({
        schema_version: 1,
        plan_hash: plan.planHash,
        accepted_agreement_ids: state.choice.agreementAccepted ? plan.requiredAgreements : [],
      });
      const approval = this.approval ?? (await this.api.approve(plan, this.approvalIntent));
      this.approval = approval;
      this.startIntent ??= new MutationIntent({
        schema_version: 1,
        plan_ref: { id: approval.planId, revision: approval.planRevision },
        approval_id: approval.id,
      });
      const op = await this.api.start(this.startIntent);
      this.store.patch({ start: { phase: "done", error: null, unknown: false }, operation: op, operationError: null, defaults: idle, readiness: { phase: "idle", data: null, error: null, key: null } });
      await this.loadQueue();
      this.schedule();
      return true;
    } catch (error) {
      const e = toApiError(error);
      // An unknown outcome keeps both intents: an explicit retry replays the same keys.
      if (!e.outcomeUnknown) {
        // A rejected approval is asked again with a new intent; an accepted one is kept unless
        // the start says the approval or plan itself is no longer valid.
        if (this.approval === null || /^(approval_|plan_|reapproval_required|inventory_stale)/.test(e.code ?? "")) {
          this.approval = null;
          this.approvalIntent = null;
        }
        this.startIntent = null;
      }
      this.store.patch({ start: { phase: "error", error: e, unknown: e.outcomeUnknown } });
      return false;
    }
  }

  // ---------------------------------------------------------------- operation

  /** Reload / reconnect: show the newest unfinished operation, if any (server is the truth). */
  async recover(): Promise<void> {
    try {
      const items = await this.api.listOperations();
      const open = items.filter((op) => !TERMINAL_SETUP.has(op.status)).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      const current = this.store.get().operation;
      const pick = open[0] ?? null;
      if (pick && (!current || isTerminal(current) || current.id !== pick.id || pick.revision > current.revision || pick.status !== current.status)) {
        this.store.patch({ operation: pick, operationError: null });
        this.schedule();
        return;
      }
      if (pick || current) return;
      // A setup that succeeded while this screen was closed: if it asked for defaults that
      // were never applied, show it and finish that step (read first; write only if pending).
      const done = items.filter((op) => op.status === "succeeded").sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0];
      if (!done) return;
      const view = await this.api.getDefaults(done.id);
      if (view.state !== "pending") return;
      this.store.patch({ operation: done, operationError: null });
      await this.settle(done);
    } catch (error) {
      this.store.patch({ operationError: toApiError(error) });
    }
  }

  private schedule(): void {
    if (this.disposed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.poll(), this.hooks.pollMs ?? POLL_MS);
  }

  async poll(): Promise<void> {
    this.timer = null;
    const current = this.store.get().operation;
    if (!current) return;
    try {
      const op = await this.api.getOperation(current.id);
      if (this.store.get().operation?.id !== op.id) return; // replaced meanwhile
      this.store.patch({ operation: op, operationError: null });
      if (op.status === "queued") await this.loadQueue();
      if (isTerminal(op)) await this.settle(op);
      else this.schedule();
    } catch (error) {
      this.store.patch({ operationError: toApiError(error) });
      this.schedule(); // reconnect: keep reading (GET only)
    }
  }

  private async settle(op: SetupOperation): Promise<void> {
    if (this.finished.has(op.id)) return;
    if (op.status !== "succeeded") {
      this.finished.add(op.id);
      return;
    }
    // Marked finished only after the defaults step settled, so an error can be retried.
    if (await this.applyDefaults(op)) this.finished.add(op.id);
    await this.checkReadiness();
  }

  async act(action: "retry" | "resume" | "cancel"): Promise<void> {
    const op = this.store.get().operation;
    if (!op || this.store.get().action.phase === "pending") return;
    this.store.patch({ action: { phase: "pending", error: null } });
    const body: JsonObject = action === "cancel" ? { schema_version: 1, mode: "cooperative" } : { schema_version: 1 };
    try {
      const next = await this.api.act(op, action, new MutationIntent(body));
      this.store.patch({ action: { phase: "done", error: null }, operation: next, operationError: null });
      this.finished.delete(next.id);
      this.schedule();
    } catch (error) {
      const e = toApiError(error);
      this.store.patch({ action: { phase: "error", error: e } });
      // Unknown or stale: read the truth again instead of resending.
      await this.recover();
      if (!isTerminal(this.store.get().operation)) this.schedule();
    }
  }

  /** GET /queue: whether dispatch is paused and how many items wait before this setup. */
  async loadQueue(): Promise<void> {
    const op = this.store.get().operation;
    try {
      const view = await this.resources.getQueue();
      const index = view.items.findIndex((item) => item.resourceId === op?.id);
      this.store.patch({ queue: { ...this.store.get().queue, dispatch: view.dispatch, revision: view.revision, ahead: Math.max(0, index), error: null } });
    } catch (error) {
      this.store.patch({ queue: { ...this.store.get().queue, error: toApiError(error) } });
    }
  }

  /** Explicit: resume dispatch of the shared queue (CAS on its revision, shared write lease). */
  async resumeDispatch(): Promise<void> {
    const q = this.store.get().queue;
    const gate = this.hooks.gate;
    if (q.revision === null || q.phase === "pending" || (gate && !gate.acquire(this.queueOwner))) return;
    this.store.patch({ queue: { ...q, phase: "pending", error: null } });
    try {
      const view = await this.resources.setDispatch(new MutationIntent({ schema_version: 1, dispatch: "running" }), q.revision);
      this.store.patch({ queue: { ...this.store.get().queue, dispatch: view.dispatch, revision: view.revision, phase: "done", error: null } });
    } catch (error) {
      this.store.patch({ queue: { ...this.store.get().queue, phase: "error", error: toApiError(error) } });
      await this.loadQueue();
    } finally {
      gate?.release(this.queueOwner);
    }
    this.schedule();
  }

  // ---------------------------------------------------------------- defaults / readiness

  /**
   * PR #91 P2-1: the server creates the defaults (one B5 transaction, create-if-empty, keyed
   * by the plan). The intent was stored in the plan (`apply_defaults`), so this works after a
   * reload, a lost answer or from another window, never leaves half a pair and never changes
   * an existing Preset/Policy. Re-verification plans never ask for it.
   */
  async applyDefaults(op: SetupOperation): Promise<boolean> {
    this.store.patch({ defaults: { phase: "pending", error: null } });
    try {
      let view = await this.api.getDefaults(op.id);
      if (view.state === "pending") view = await this.api.applyDefaults(op.id);
      if (view.state === "created" && view.preset && view.policy) {
        this.store.patch({ defaults: { phase: "created", error: null } });
        await this.hooks.onDefaults?.(view.preset, view.policy);
      } else if (view.state === "kept") {
        this.store.patch({ defaults: { phase: "kept", error: null } });
      } else {
        this.store.patch({ defaults: idle });
      }
      return true;
    } catch (error) {
      // Nothing is assumed: the person can try again (the server answer is idempotent).
      this.store.patch({ defaults: { phase: "error", error: toApiError(error) } });
      return false;
    }
  }

  async retryDefaults(): Promise<void> {
    const op = this.store.get().operation;
    if (op?.status !== "succeeded" || this.store.get().defaults.phase === "pending") return;
    if (await this.applyDefaults(op)) await this.checkReadiness();
  }

  /** Read-only: would a job with these settings be admitted now? (no smoke, no token) */
  async checkReadiness(): Promise<void> {
    const selection = this.hooks.selection?.() ?? { preset: null, policy: null };
    const policy = selection.policy;
    const settings = selection.preset?.settings ?? null;
    const key = readinessKey(selection.preset, policy);
    const generation = ++this.readinessGeneration;
    if (!policy || !settings || key === null) {
      this.store.patch({ readiness: { phase: "idle", data: null, error: null, key: null } });
      return;
    }
    this.store.patch({ readiness: { phase: "pending", data: null, error: null, key } });
    try {
      const data = await this.api.readiness({ schema_version: 1, settings, policy_ref: { id: policy.id, revision: policy.revision } });
      // A newer check (or a selection change) supersedes this answer: never show it.
      if (generation !== this.readinessGeneration || readinessKey(this.hooks.selection?.().preset ?? null, this.hooks.selection?.().policy ?? null) !== key) return;
      this.store.patch({ readiness: { phase: "done", data, error: null, key } });
    } catch (error) {
      if (generation !== this.readinessGeneration) return;
      this.store.patch({ readiness: { phase: "error", data: null, error: toApiError(error), key } });
    }
  }

  /** Start a re-verification of the selected Preset (still plan → review → confirm). */
  async prepareVerification(profile: ProfileKind): Promise<boolean> {
    if (this.busy()) return false;
    this.store.patch({ mode: "verify", choice: { ...this.store.get().choice, profile, diarization: false, agreementAccepted: false } });
    this.resetPlan();
    return this.createPlan();
  }

  acceptVerificationAgreement(agreementAccepted: boolean): void {
    if (this.store.get().mode === "verify" && !this.busy()) {
      this.store.patch({ choice: { ...this.store.get().choice, agreementAccepted } });
    }
  }

  dismissResult(): void {
    if (!isTerminal(this.store.get().operation)) return;
    this.store.patch({ operation: null, operationError: null, action: idle, defaults: idle, mode: this.store.get().mode === "verify" ? "standard" : this.store.get().mode });
    this.resetPlan();
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.diagTimer) clearTimeout(this.diagTimer);
    this.timer = this.diagTimer = null;
  }
}
