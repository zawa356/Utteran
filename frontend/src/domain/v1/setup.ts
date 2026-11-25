import type { ApiError } from "../../api/errors";
import { MutationIntent } from "../../api/idempotency";
import {
  type Diagnostics,
  type ProfileKind,
  type Readiness,
  type SetupApi,
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
export const VERIFIABLE_PROFILES: readonly ProfileKind[] = ["cpu", "cuda"];
export const POLL_MS = 1000;

export type SetupMode = "standard" | "custom" | "verify";
export type Phase = "idle" | "pending" | "done" | "error";

export interface SetupChoice {
  profile: ProfileKind;
  models: string[]; // ASR model ids (faster-whisper)
  primary: string;
  diarization: boolean;
  agreementAccepted: boolean;
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
  readiness: { phase: Phase; data: Readiness | null; error: ApiError | null; target: "defaults" | "selection" | null };
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
  return { profile, models: ["large-v3-turbo"], primary: "large-v3-turbo", diarization: false, agreementAccepted: false };
}

/** The common default settings: the shared template with the primary model, ASR only. */
export function defaultSettings(primary: string): JsonObject {
  const settings = JSON.parse(JSON.stringify(template)) as JsonObject;
  (settings.asr as JsonObject).model = primary;
  (settings.asr as JsonObject).backend = "faster-whisper";
  (settings.diarization as JsonObject).enabled = false;
  return settings;
}

/** Why a settings value cannot be verified by the setup (null when it can). */
export function verifyRefusal(settings: JsonObject | null): string | null {
  if (!settings) return "no_preset";
  const asr = settings.asr as JsonObject | undefined;
  const diarization = settings.diarization as JsonObject | undefined;
  if (!asr || asr.backend !== "faster-whisper") return "backend_unsupported";
  if (diarization?.enabled === true) return "diarization_unsupported";
  if (!WORKER_MODELS.includes(String(asr.model) as (typeof WORKER_MODELS)[number])) return "model_unsupported";
  return null;
}

export function planRequest(choice: SetupChoice, settings: JsonObject): JsonObject {
  const refs = [choice.primary, ...choice.models.filter((m) => m !== choice.primary)].map((m) => `faster-whisper:${m}`);
  if (choice.diarization) refs.push(DIARIZATION_REF);
  return {
    schema_version: 1,
    profile_kind: choice.profile,
    model_refs: refs,
    diarization_enabled: choice.diarization,
    verification_settings: settings,
  };
}

/** A deterministic intent for the post-setup defaults: same operation → same key. */
function derivedIntent(operationId: string, body: JsonObject): MutationIntent<JsonObject> {
  const hex = operationId.replace(/^setup_/, "");
  return new MutationIntent(body, (bytes) => {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16) || 0;
    return bytes;
  });
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
    readiness: { phase: "idle", data: null, error: null, target: null },
    verifiedSettings: null,
    queue: { dispatch: null, revision: null, ahead: 0, phase: "idle", error: null },
  });
  private readonly queueOwner = Symbol("setup-dispatch");
  private timer: ReturnType<typeof setTimeout> | null = null;
  private diagTimer: ReturnType<typeof setTimeout> | null = null;
  private planIntent: MutationIntent<JsonObject> | null = null;
  private startIntent: MutationIntent<JsonObject> | null = null;
  private approvalIntent: MutationIntent<JsonObject> | null = null;
  private finished = new Set<string>();
  private disposed = false;

  constructor(
    private readonly api: SetupApi,
    private readonly resources: V1ResourcesApi,
    private readonly hooks: {
      /** The selected Preset/Policy of the screen (re-verification, readiness). */
      selection?: () => { preset: PresetV1 | null; policy: PolicyV1 | null };
      /** After defaults were created, the screen reloads and selects them. */
      onDefaults?: (preset: PresetV1, policy: PolicyV1) => Promise<void> | void;
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
    const state = this.store.get();
    const first = state.diagnostics.data === null;
    this.patchDiag({ phase: data.phase === "running" ? "pending" : "done", data, error: null });
    const recommended = data.recommendation?.profile;
    if ((first || data.phase === "completed") && recommended && state.mode === "standard" && VERIFIABLE_PROFILES.includes(recommended)) {
      this.store.patch({ choice: { ...this.store.get().choice, profile: recommended } });
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
    this.store.patch({ mode, choice: mode === "standard" ? initialChoice(profile) : this.store.get().choice });
    this.resetPlan();
  }

  edit(partial: Partial<SetupChoice>): void {
    if (this.busy()) return;
    const next = { ...this.store.get().choice, ...partial };
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
      choice = { ...choice, models: [model], primary: model, diarization: false };
    } else {
      settings = defaultSettings(choice.primary);
    }
    if (!this.planIntent || state.plan.phase !== "error" || !state.plan.unknown) {
      this.planIntent = new MutationIntent(planRequest(choice, settings));
    }
    this.store.patch({ plan: { phase: "pending", plan: null, error: null, unknown: false }, verifiedSettings: settings });
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
      const approval = await this.api.approve(plan, this.approvalIntent);
      this.startIntent ??= new MutationIntent({
        schema_version: 1,
        plan_ref: { id: approval.planId, revision: approval.planRevision },
        approval_id: approval.id,
      });
      const op = await this.api.start(this.startIntent);
      this.store.patch({ start: { phase: "done", error: null, unknown: false }, operation: op, operationError: null, defaults: idle, readiness: { phase: "idle", data: null, error: null, target: null } });
      await this.loadQueue();
      this.schedule();
      return true;
    } catch (error) {
      const e = toApiError(error);
      // An unknown outcome keeps both intents: an explicit retry replays the same keys.
      if (!e.outcomeUnknown) {
        this.approvalIntent = e.code === "plan_already_approved" ? this.approvalIntent : null;
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
      }
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
    this.finished.add(op.id);
    if (op.status !== "succeeded") return;
    await this.applyDefaults(op);
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

  private async applyDefaults(op: SetupOperation): Promise<void> {
    const settings = this.store.get().verifiedSettings;
    if (this.store.get().mode === "verify" || !settings) {
      this.store.patch({ defaults: { phase: "kept", error: null } });
      return;
    }
    this.store.patch({ defaults: { phase: "pending", error: null } });
    try {
      const [presets, policies] = await Promise.all([this.resources.listAll("presets"), this.resources.listAll("execution-policies")]);
      if (presets.items.length > 0 || policies.items.length > 0 || presets.truncated || policies.truncated) {
        this.store.patch({ defaults: { phase: "kept", error: null } });
        return;
      }
      const preset = (await this.resources.create(
        "presets",
        derivedIntent(op.id, { schema_version: 1, name: `Setup ${String((settings.asr as JsonObject).model)}`, settings }),
      )) as PresetV1;
      const policy = (await this.resources.create(
        "execution-policies",
        derivedIntent(op.id, {
          schema_version: 1,
          name: `Setup ${op.profileKind}`,
          mode: "auto",
          fixed_candidate_id: null,
          allowed_profile_ids: [op.profileKind],
          objective: "balanced",
          max_memory_bytes: null,
          fallback: "none",
          execution_deadline_seconds: null,
        }),
      )) as PolicyV1;
      this.store.patch({ defaults: { phase: "created", error: null } });
      await this.hooks.onDefaults?.(preset, policy);
    } catch (error) {
      this.store.patch({ defaults: { phase: "error", error: toApiError(error) } });
    }
  }

  /** Read-only: would a job with these settings be admitted now? (no smoke, no token) */
  async checkReadiness(): Promise<void> {
    const selection = this.hooks.selection?.() ?? { preset: null, policy: null };
    const policy = selection.policy;
    const settings = selection.preset?.settings ?? null;
    if (!policy || !settings) {
      this.store.patch({ readiness: { phase: "idle", data: null, error: null, target: null } });
      return;
    }
    this.store.patch({ readiness: { phase: "pending", data: null, error: null, target: "selection" } });
    try {
      const data = await this.api.readiness({ schema_version: 1, settings, policy_ref: { id: policy.id, revision: policy.revision } });
      this.store.patch({ readiness: { phase: "done", data, error: null, target: "selection" } });
    } catch (error) {
      this.store.patch({ readiness: { phase: "error", data: null, error: toApiError(error), target: "selection" } });
    }
  }

  /** Start a re-verification of the selected Preset (still plan → review → confirm). */
  async prepareVerification(profile: ProfileKind): Promise<boolean> {
    if (this.busy()) return false;
    this.store.patch({ mode: "verify", choice: { ...this.store.get().choice, profile, diarization: false, agreementAccepted: false } });
    this.resetPlan();
    return this.createPlan();
  }

  dismissResult(): void {
    if (!isTerminal(this.store.get().operation)) return;
    this.store.patch({ operation: null, operationError: null, action: idle, defaults: idle });
    this.resetPlan();
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.diagTimer) clearTimeout(this.diagTimer);
    this.timer = this.diagTimer = null;
  }
}
