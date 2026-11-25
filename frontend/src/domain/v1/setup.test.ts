import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/errors";
import type { MutationIntent } from "../../api/idempotency";
import type { Diagnostics, Readiness, SetupApi, SetupOperation, SetupPlan } from "../../api/setup";
import type { JsonObject, PolicyV1, PresetV1, V1ResourcesApi } from "../../api/v1Resources";
import { DIARIZATION_REF, defaultSettings, planRequest, SetupController, verifyRefusal } from "./setup";

const OP = "setup_" + "ab".repeat(16);
const PLAN = "plan_" + "cd".repeat(16);

function diagnostics(phase: Diagnostics["phase"] = "completed", profile: "cpu" | "cuda" = "cuda"): Diagnostics {
  return {
    generation: 1,
    phase,
    os: { platform: "windows", venvBuildSupported: true },
    gpu: { state: "detected", reasonCode: null, adapters: [{ name: "NVIDIA Synthetic", vendor: "nvidia", driverVersion: "1.0" }], driverDownloadUrl: "https://example.com" },
    memoryTotalBytes: 1,
    diskFreeBytes: 1,
    vcredist: { state: "present", version: "v14", downloadUrl: "https://example.com" },
    runtimePhase: "completed",
    runtime: null,
    recommendation: { profile, reasonCodes: ["nvidia_adapter_detected"], confident: false },
  };
}

function plan(extra: Partial<SetupPlan> = {}): SetupPlan {
  return {
    id: PLAN,
    revision: 1,
    state: "proposed",
    profileKind: "cuda",
    modelRefs: ["faster-whisper:large-v3-turbo"],
    diarizationEnabled: false,
    planHash: "e".repeat(64),
    expiresAt: "2026-11-26T00:00:00Z",
    estimate: { downloadBytes: null, diskBytes: null },
    changes: [],
    steps: [],
    preserved: [],
    requiredAgreements: [],
    missingCredentials: [],
    verification: null,
    ...extra,
  };
}

function operation(status: string, extra: Partial<SetupOperation> = {}): SetupOperation {
  return {
    id: OP,
    revision: 1,
    planId: PLAN,
    profileKind: "cuda",
    status,
    held: false,
    recoveryRequired: null,
    reasonCode: "submitted",
    waitingReason: null,
    attemptNumber: 1,
    steps: [],
    retryOf: null,
    error: null,
    createdAt: "2026-11-25T00:00:00Z",
    updatedAt: "2026-11-25T00:00:00Z",
    ...extra,
  };
}

class FakeSetup implements SetupApi {
  calls: string[] = [];
  intents: MutationIntent<JsonObject>[] = [];
  diag = diagnostics();
  planResult: SetupPlan | Error = plan();
  ops: SetupOperation[] = [];
  list: SetupOperation[] = [];
  readinessResult: Readiness = { state: "ready", reasonCodes: [], selected: null, candidates: [] };
  actResult: SetupOperation | Error = operation("queued");
  async getDiagnostics() {
    this.calls.push("getDiagnostics");
    return this.diag;
  }
  async refreshDiagnostics(runtime: boolean, force: boolean) {
    this.calls.push(`refresh:${runtime}:${force}`);
    return this.diag;
  }
  async createPlan(intent: MutationIntent<JsonObject>) {
    this.calls.push("createPlan");
    this.intents.push(intent);
    if (this.planResult instanceof Error) throw this.planResult;
    return this.planResult;
  }
  async getPlan() {
    return plan();
  }
  async discardPlan() {
    this.calls.push("discard");
  }
  async approve(_p: SetupPlan, intent: MutationIntent<JsonObject>) {
    this.calls.push("approve");
    this.intents.push(intent);
    return { id: "apr_" + "1".repeat(32), planId: PLAN, planRevision: 2 };
  }
  async start(intent: MutationIntent<JsonObject>) {
    this.calls.push("start");
    this.intents.push(intent);
    return operation("queued");
  }
  async getOperation() {
    this.calls.push("getOperation");
    return this.ops.shift() ?? operation("running");
  }
  async listOperations() {
    this.calls.push("list");
    return this.list;
  }
  async act(_op: SetupOperation, action: string, intent: MutationIntent<JsonObject>) {
    this.calls.push(`act:${action}`);
    this.intents.push(intent);
    if (this.actResult instanceof Error) throw this.actResult;
    return this.actResult;
  }
  async readiness(body: JsonObject) {
    this.calls.push("readiness");
    this.intents.push({ key: "readiness-body-000000", body } as MutationIntent<JsonObject>);
    return this.readinessResult;
  }
}

class FakeResources {
  dispatch: "running" | "paused" = "paused";
  dispatchWrites: { body: JsonObject; revision: number }[] = [];
  async getQueue() {
    return { revision: 4, dispatch: this.dispatch, activeItemId: null, items: [{ id: "itm_1", resourceId: "job_x", resourceKind: "job", position: 0, held: false, blockers: [] }, { id: "itm_2", resourceId: OP, resourceKind: "setup_operation", position: 1, held: false, blockers: [] }] };
  }
  async setDispatch(intent: MutationIntent<JsonObject>, revision: number) {
    this.dispatchWrites.push({ body: intent.body, revision });
    this.dispatch = "running";
    return { ...(await this.getQueue()), revision: 5 };
  }
  presets: PresetV1[] = [];
  policies: PolicyV1[] = [];
  created: { kind: string; key: string; body: JsonObject }[] = [];
  async listAll(kind: string) {
    return { revision: 1, items: kind === "presets" ? this.presets : this.policies, truncated: false };
  }
  async create(kind: string, intent: MutationIntent<JsonObject>) {
    this.created.push({ kind, key: intent.key, body: intent.body });
    return kind === "presets"
      ? { id: "pre_1", revision: 1, name: "x", settings: intent.body.settings as JsonObject }
      : { id: "pol_1", revision: 1, name: "x", mode: "auto", allowedProfileIds: ["cuda"], objective: "balanced", fallback: "none" };
  }
}

function make(selection: { preset: PresetV1 | null; policy: PolicyV1 | null } = { preset: null, policy: null }) {
  const api = new FakeSetup();
  const resources = new FakeResources();
  const chosen = { ...selection };
  const defaults: string[] = [];
  const controller = new SetupController(api, resources as unknown as V1ResourcesApi, {
    selection: () => chosen,
    onDefaults: (preset, policy) => {
      defaults.push(preset.id, policy.id);
      chosen.preset = preset;
      chosen.policy = policy;
    },
    pollMs: 1e9,
  });
  return { api, resources, controller, defaults, chosen };
}

describe("SetupController", () => {
  it("opening reads only; an explicit check starts a pass and follows the recommended verifiable profile", async () => {
    const { api, controller } = make();
    api.diag = { ...diagnostics(), phase: "idle", recommendation: null, gpu: null };
    await controller.loadDiagnostics();
    expect(api.calls).toEqual(["getDiagnostics"]);
    expect(controller.store.get().choice.profile).toBe("cpu");
    const refreshed = diagnostics("completed", "cuda");
    api.refreshDiagnostics = async (runtime, force) => {
      api.calls.push(`refresh:${runtime}:${force}`);
      return refreshed;
    };
    await controller.redetect(false);
    expect(api.calls.at(-1)).toBe("refresh:true:false");
    expect(controller.store.get().choice.profile).toBe("cuda");
    await controller.redetect();
    expect(api.calls.at(-1)).toBe("refresh:true:true");
    controller.dispose();
  });

  it("plans the standard setup with the template settings of the main model, never starting anything", async () => {
    const { api, controller } = make();
    controller.edit({ profile: "cpu" });
    expect(await controller.createPlan()).toBe(true);
    expect(api.calls).toEqual(["createPlan"]);
    const body = api.intents[0]!.body;
    expect(body.profile_kind).toBe("cpu");
    expect(body.model_refs).toEqual(["faster-whisper:large-v3-turbo"]);
    expect((body.verification_settings as JsonObject).asr).toMatchObject({ model: "large-v3-turbo", backend: "faster-whisper" });
    expect(((body.verification_settings as JsonObject).diarization as JsonObject).enabled).toBe(false);
  });

  it("puts the main model first, adds diarization and needs the agreement before starting", async () => {
    const { api, controller } = make();
    controller.setMode("custom");
    controller.edit({ models: ["small", "large-v3"], primary: "large-v3", diarization: true });
    api.planResult = plan({ requiredAgreements: ["hf-gated:pyannote/speaker-diarization-community-1"], diarizationEnabled: true });
    await controller.createPlan();
    expect(api.intents[0]!.body.model_refs).toEqual(["faster-whisper:large-v3", "faster-whisper:small", DIARIZATION_REF]);
    expect(await controller.confirmAndStart()).toBe(false); // agreement not ticked
    controller.edit({ agreementAccepted: true });
    // edit() resets the plan: the reviewed plan must be created again for the new choice.
    expect(controller.store.get().plan.plan).toBeNull();
    await controller.createPlan();
    expect(await controller.confirmAndStart()).toBe(true);
    const approval = api.intents.find((i) => "plan_hash" in i.body)!;
    expect(approval.body.accepted_agreement_ids).toEqual(["hf-gated:pyannote/speaker-diarization-community-1"]);
    const start = api.intents.find((i) => "approval_id" in i.body)!;
    expect(start.body.plan_ref).toEqual({ id: PLAN, revision: 2 });
  });

  it("never starts with a missing credential", async () => {
    const { api, controller } = make();
    api.planResult = plan({ missingCredentials: ["hf_token"] });
    await controller.createPlan();
    expect(await controller.confirmAndStart()).toBe(false);
    expect(api.calls).not.toContain("approve");
  });

  it("after the first success creates defaults once (derived keys), selects them and checks readiness", async () => {
    const { api, resources, controller, defaults } = make();
    await controller.createPlan();
    await controller.confirmAndStart();
    api.ops = [operation("running"), operation("succeeded")];
    await controller.poll();
    expect(controller.store.get().operation?.status).toBe("running");
    await controller.poll();
    expect(controller.store.get().defaults.phase).toBe("created");
    expect(resources.created.map((c) => c.kind)).toEqual(["presets", "execution-policies"]);
    expect(new Set(resources.created.map((c) => c.key))).toEqual(new Set(["ui2-" + "ab".repeat(16)]));
    expect((resources.created[1]!.body.allowed_profile_ids as string[])).toEqual(["cuda"]);
    expect(defaults).toEqual(["pre_1", "pol_1"]);
    expect(api.calls.at(-1)).toBe("readiness");
    const readinessBody = api.intents.at(-1)!.body;
    expect(readinessBody.policy_ref).toEqual({ id: "pol_1", revision: 1 });
    // The same terminal answer again does not create or check twice.
    api.ops = [operation("succeeded")];
    await controller.poll();
    expect(resources.created).toHaveLength(2);
  });

  it("a queued setup behind paused dispatch shows the queue; resuming is one explicit CAS write", async () => {
    const { resources, controller } = make();
    await controller.createPlan();
    await controller.confirmAndStart();
    expect(controller.store.get().queue).toMatchObject({ dispatch: "paused", revision: 4, ahead: 1 });
    await controller.resumeDispatch();
    expect(resources.dispatchWrites).toEqual([{ body: { schema_version: 1, dispatch: "running" }, revision: 4 }]);
    expect(controller.store.get().queue.dispatch).toBe("running");
    controller.dispose();
  });

  it("keeps existing settings untouched after a later setup", async () => {
    const { api, resources, controller } = make();
    resources.presets = [{ id: "pre_x", revision: 3, name: "mine", settings: defaultSettings("large-v3") }];
    await controller.createPlan();
    await controller.confirmAndStart();
    api.ops = [operation("succeeded")];
    await controller.poll();
    expect(controller.store.get().defaults.phase).toBe("kept");
    expect(resources.created).toEqual([]);
  });

  it("failure → retry sends one new intent; an unknown action outcome re-reads instead of resending", async () => {
    const { api, controller } = make();
    await controller.createPlan();
    await controller.confirmAndStart();
    api.ops = [operation("failed", { error: { code: "smoke_failed", stepId: "verify" } })];
    await controller.poll();
    api.actResult = operation("queued", { id: "setup_" + "cd".repeat(16), retryOf: OP });
    await controller.act("retry");
    expect(controller.store.get().operation?.retryOf).toBe(OP);
    api.actResult = new ApiError({ kind: "timeout", outcomeUnknown: true });
    api.list = [operation("running", { id: "setup_" + "cd".repeat(16), createdAt: "2026-11-25T01:00:00Z" })];
    await controller.act("cancel");
    expect(api.calls.filter((c) => c === "act:cancel")).toHaveLength(1);
    expect(api.calls).toContain("list");
    expect(controller.store.get().operation?.status).toBe("running");
  });

  it("recovers the newest unfinished operation after a reload", async () => {
    const { api, controller } = make();
    api.list = [
      operation("succeeded", { id: "setup_" + "11".repeat(16), createdAt: "2026-11-25T03:00:00Z" }),
      operation("waiting_user", { id: "setup_" + "22".repeat(16), createdAt: "2026-11-25T01:00:00Z", waitingReason: "credential_required" }),
      operation("interrupted", { id: "setup_" + "33".repeat(16), createdAt: "2026-11-25T02:00:00Z" }),
    ];
    await controller.recover();
    expect(controller.store.get().operation?.id).toBe("setup_" + "33".repeat(16));
    controller.dispose();
  });

  it("an unknown plan outcome is retried with the same intent; a changed choice gets a new one", async () => {
    const { api, controller } = make();
    api.planResult = new ApiError({ kind: "network", outcomeUnknown: true });
    await controller.createPlan();
    expect(controller.store.get().plan.unknown).toBe(true);
    api.planResult = plan();
    await controller.createPlan();
    expect(api.intents[0]!.key).toBe(api.intents[1]!.key);
    controller.edit({ profile: "cpu" });
    await controller.createPlan();
    expect(api.intents[2]!.key).not.toBe(api.intents[1]!.key);
  });

  it("verifies the selected Preset only when the new path can run it", async () => {
    const preset: PresetV1 = { id: "pre_s", revision: 2, name: "s", settings: defaultSettings("kotoba-whisper-v2.0") };
    const { api, controller } = make({ preset, policy: null });
    expect(await controller.prepareVerification("cpu")).toBe(true);
    const body = api.intents[0]!.body;
    expect(body.model_refs).toEqual(["faster-whisper:kotoba-whisper-v2.0"]);
    expect(body.verification_settings).toEqual(preset.settings);
    const withDiarization = defaultSettings("large-v3");
    (withDiarization.diarization as JsonObject).enabled = true;
    expect(verifyRefusal(withDiarization)).toBe("diarization_unsupported");
    expect(verifyRefusal(defaultSettings("small"))).toBe("model_unsupported");
    expect(verifyRefusal(null)).toBe("no_preset");
  });

  it("planRequest never carries a path or token", () => {
    const body = planRequest({ profile: "cpu", models: ["large-v3-turbo"], primary: "large-v3-turbo", diarization: false, agreementAccepted: false }, defaultSettings("large-v3-turbo"));
    expect(JSON.stringify(body)).not.toMatch(/hf_|[A-Za-z]:\\|\/home\//);
  });
});
