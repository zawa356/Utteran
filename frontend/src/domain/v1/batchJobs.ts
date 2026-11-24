/**
 * UI-3 S5-B (bundle C2b): several inputs -> one atomic B7 batch (`--next-ui-v1` only).
 *
 * The same two-step admission as the single job (`singleJob.ts`), for every selected row at once:
 * 1. `prepare()` freezes one EditingSession with one file per selected row (its own leaf overrides
 *    and its own output folder), validates it (B5) and resolves it (B7). Every file must come back
 *    valid with exactly the effective settings this screen computed; otherwise nothing is sent.
 * 2. `submit()` sends that frozen session with the token once. B7 accepts all files or none (one
 *    transaction), so there is no partial admission to track on the client.
 * 3. 4xx = refused (no job). Anything else = unknown: the intent (key + frozen body) is kept in page
 *    memory and resent only by an explicit `recheck()`. No other key or body is sent automatically.
 * A per-file setting this route cannot express (the device) is refused before any request; it is
 * never dropped. This controller does not poll: the queue/history view owns job status.
 */

import { QueueMutationGate } from "./queueGate";
import { MutationIntent, type RandomSource } from "../../api/idempotency";
import type { JobV1, JsonObject, ResolutionV1, V1ResourcesApi, ValidationV1 } from "../../api/v1Resources";
import type { ApiError } from "../../api/errors";
import { classifyStartFailure } from "../admission";
import { type AvailabilityProblem, availabilityProblem, type CollectionState, type InputRow } from "../inputCollection";
import { toApiError } from "../operation";
import { checkPath, type PathProblem } from "../paths";
import { type ReadonlyStore, Store } from "../store";
import type { FormState } from "../transcription";
import { isFinished, type LogicalJob } from "./logicalJobs";
import type { LibraryState } from "./library";
import { canonical, clone, commonOverrides, effectiveSettings, type SettingOverride } from "./settings";

/** B7 `MAX_BATCH`. */
export const V1_MAX_BATCH = 100;
const RESUME_MODE = "resume";

export type BatchStage = "validation" | "resolution" | "submission";
export type BatchStep = "idle" | "validating" | "resolving" | "resolved" | "submitting" | "accepted" | "rejected" | "unknown";

export type BatchIssue =
  | { field: "preset" | "policy"; problem: "loading" | "error" | "none" | "missing" }
  | { field: "rows"; problem: "none" | "too_many" }
  | { field: "input"; rowId: string; problem: PathProblem | AvailabilityProblem }
  | { field: "output"; rowId: string; problem: PathProblem }
  | { field: "device"; rowId: string; problem: "unsupported" };

export interface PreparedFile {
  readonly rowId: string;
  readonly fileId: string;
  readonly inputPath: string;
  readonly outputDir: string;
  readonly effective: JsonObject;
}

export interface PreparedBatch {
  readonly session: JsonObject;
  readonly canonical: string;
  readonly files: readonly PreparedFile[];
  readonly validation: ValidationV1 | null;
  readonly resolution: ResolutionV1 | null;
}

export interface BatchEvaluation {
  readonly issues: readonly BatchIssue[];
  readonly session: JsonObject | null;
  readonly files: readonly PreparedFile[];
  readonly count: number;
}

export interface V1BatchState {
  step: BatchStep;
  failedAt: BatchStage | null;
  error: ApiError | null;
  /** Row ids whose validated settings differ from what this screen computed (nothing was sent on). */
  mismatched: readonly string[];
  prepared: PreparedBatch | null;
  /** The in-flight or unknown write (page memory only). */
  pending: MutationIntent<JsonObject> | null;
  /** Jobs created by the accepted batch, in file order. */
  jobs: readonly JobV1[];
}

const initial: V1BatchState = { step: "idle", failedAt: null, error: null, mismatched: [], prepared: null, pending: null, jobs: [] };

function deepFreeze<T>(value: T): T {
  if (typeof value === "object" && value !== null) {
    for (const item of Object.values(value)) deepFreeze(item);
    Object.freeze(value);
  }
  return value;
}

function leaf(settings: JsonObject, path: string): unknown {
  let node: unknown = settings;
  for (const part of path.split("/").slice(1)) node = (node as JsonObject)[part];
  return node;
}

/** A row's own differences from the common effective settings, as registered B5 leaf overrides. */
export function fileOverrides(common: JsonObject, row: InputRow): SettingOverride[] {
  const out: SettingOverride[] = [];
  const add = (path: SettingOverride["path"], value: string | readonly string[] | undefined) => {
    if (value === undefined || canonical(value) === canonical(leaf(common, path))) return;
    out.push({ path, value: (Array.isArray(value) ? [...value] : value) as SettingOverride["value"] });
  };
  add("/asr/backend", row.overrides.asrBackend);
  add("/asr/model", row.overrides.asrModel);
  add("/asr/language", row.overrides.language);
  add("/output/formats", row.overrides.formats);
  if (row.overrides.glossary !== undefined) out.push({ path: "/asr/glossary", value: [...row.overrides.glossary] });
  return out;
}

interface RerunSelection {
  readonly jobId: string;
  readonly revision: number;
  readonly sourceRowId: string | null;
  readonly path: string | null;
}

export function canRerun(group: LogicalJob): boolean {
  return !group.branched && !group.incomplete && group.open.length === 0 && isFinished(group.latest);
}

export interface V1BatchOptions {
  /** Refresh shared reads after an accepted rerun or a stale source revision. */
  onRerunSettled?: () => void;
  gate?: QueueMutationGate;
  now?: () => number;
  random?: RandomSource;
  /** A stable random file id per row for this page (shared with the single-job controller). */
  fileIdFor: (rowId: string) => string;
}

export class V1BatchController {
  readonly store = new Store<V1BatchState>(initial);
  readonly gate: QueueMutationGate;
  readonly rerunSelection = new Store<RerunSelection | null>(null);
  private readonly submitOwner = Symbol("batch submit");
  private disposed = false;
  private readonly now: () => number;

  constructor(
    private readonly api: V1ResourcesApi,
    private readonly library: ReadonlyStore<LibraryState>,
    private readonly inputs: ReadonlyStore<CollectionState>,
    private readonly form: ReadonlyStore<FormState>,
    private readonly options: V1BatchOptions,
  ) {
    this.gate = options.gate ?? new QueueMutationGate();
    this.now = options.now ?? (() => Date.now());
  }

  openRerun(group: LogicalJob): void {
    if (!this.options.onRerunSettled || this.gate.busy() || this.rerunSelection.get() || !canRerun(group)) return;
    const source = group.latest;
    const candidate = this.inputs.get().rows.find(row => this.options.fileIdFor(row.clientFileId) === source.clientFileId);
    this.rerunSelection.set({ jobId: source.id, revision: source.revision, sourceRowId: candidate?.clientFileId ?? null, path: candidate?.inputPath ?? null });
  }

  selectRerunInput(path: string): void {
    const target = this.rerunSelection.get();
    if (!target || this.busy() || this.store.get().step === "unknown") return;
    this.reset();
    this.rerunSelection.set({ ...target, path });
  }

  closeRerun(): void {
    if (this.busy() || this.store.get().step === "unknown") return;
    this.reset();
    this.rerunSelection.set(null);
  }

  /** Pure view of what `prepare()` would freeze now: the selected rows, in list order. */
  evaluate(): BatchEvaluation {
    const lib = this.library.get();
    const inputs = this.inputs.get().rows;
    const target = this.rerunSelection.get();
    let rows = inputs.filter(row => row.selected);
    if (this.options.onRerunSettled) {
      rows = [];
      if (target?.path) {
        // A picker replaces the input path. Settings come from the job's original row.
        const sourceRow = inputs.find(row => row.clientFileId === target.sourceRowId);
        rows = [sourceRow ? { ...sourceRow, inputPath: target.path, availability: undefined } : {
          clientFileId: target.jobId, inputPath: target.path, source: "picker", selected: true,
          overrides: {}, output: { kind: "inherit" }, attemptFloor: 0,
        }];
      }
    }
    const issues: BatchIssue[] = [];
    for (const field of ["preset", "policy"] as const) {
      const s = lib[field];
      if (s.phase === "pending") issues.push({ field, problem: "loading" });
      else if (s.missing) issues.push({ field, problem: "missing" });
      else if (s.phase === "error") issues.push({ field, problem: "error" });
      else if (!s.item) issues.push({ field, problem: "none" });
    }
    if (rows.length === 0) issues.push({ field: "rows", problem: "none" });
    if (rows.length > V1_MAX_BATCH) issues.push({ field: "rows", problem: "too_many" });
    const defaultOutput = this.form.get().draft.outputDir;
    const outputs = rows.map((row) => (row.output.kind === "custom" ? row.output.path : defaultOutput));
    rows.forEach((row, index) => {
      const input = checkPath(row.inputPath, "input_file") ?? availabilityProblem(row);
      if (input) issues.push({ field: "input", rowId: row.clientFileId, problem: input });
      const output = checkPath(outputs[index]!, "output_folder");
      if (output) issues.push({ field: "output", rowId: row.clientFileId, problem: output });
      // The execution device is decided by the Policy and B4 evidence; there is no per-file leaf.
      if (row.overrides.asrDevice !== undefined) issues.push({ field: "device", rowId: row.clientFileId, problem: "unsupported" });
    });
    const preset = lib.preset.item;
    const policy = lib.policy.item;
    if (issues.length > 0 || !preset || !policy) return { issues, session: null, files: [], count: rows.length };
    const common = commonOverrides(preset, lib.screen);
    const commonEffective = effectiveSettings(preset.settings, common);
    const files: PreparedFile[] = [];
    const wire: JsonObject[] = [];
    rows.forEach((row, index) => {
      const overrides = fileOverrides(commonEffective, row);
      const fileId = this.options.fileIdFor(row.clientFileId);
      files.push({ rowId: row.clientFileId, fileId, inputPath: row.inputPath, outputDir: outputs[index]!, effective: effectiveSettings(commonEffective, overrides) });
      wire.push({ client_file_id: fileId, input_path: row.inputPath, output_dir: outputs[index]!, overrides: overrides.map((o) => ({ path: o.path, value: clone(o.value) })) });
    });
    const session: JsonObject = {
      schema_version: 1,
      preset_ref: { id: preset.id, revision: preset.revision },
      policy_ref: { id: policy.id, revision: policy.revision },
      common_overrides: common.map((o) => ({ path: o.path, value: clone(o.value) })),
      files: wire,
    };
    return { issues, session, files, count: rows.length };
  }

  busy(): boolean {
    const step = this.store.get().step;
    return step === "validating" || step === "resolving" || step === "submitting";
  }

  /** Any edit after `prepare()` (rows, selection, overrides, folders, Preset/Policy) makes it stale. */
  isStale(): boolean {
    const p = this.store.get().prepared;
    if (!p) return false;
    const session = this.evaluate().session;
    return session === null || canonical(session) !== p.canonical;
  }

  isExpired(): boolean {
    const r = this.store.get().prepared?.resolution;
    return !!r && Date.parse(r.expiresAt) <= this.now();
  }

  canPrepare(): boolean {
    const { step, failedAt, error } = this.store.get();
    return !this.disposed && !this.busy() && step !== "unknown"
      && !(this.rerunSelection.get() && failedAt === "submission" && [404, 410, 412].includes(error?.status ?? 0))
      && this.evaluate().session !== null;
  }

  canSubmit(): boolean {
    const s = this.store.get();
    return !this.disposed && !this.gate.busy() && s.step === "resolved" && !!s.prepared?.resolution && !this.isStale() && !this.isExpired();
  }

  async prepare(): Promise<void> {
    if (!this.canPrepare()) return;
    const evaluation = this.evaluate();
    const session = deepFreeze(clone(evaluation.session!));
    const prepared: PreparedBatch = { session, canonical: canonical(session), files: deepFreeze(clone(evaluation.files) as PreparedFile[]), validation: null, resolution: null };
    this.store.patch({ step: "validating", failedAt: null, error: null, mismatched: [], prepared, pending: null, jobs: [] });
    await this.runValidation(new MutationIntent<JsonObject>(session, this.options.random));
  }

  async submit(): Promise<void> {
    if (!this.canSubmit()) return;
    const prepared = this.store.get().prepared!;
    const body = deepFreeze({ schema_version: 1, resolution_token: prepared.resolution!.token, editing_session: prepared.session, resume_mode: RESUME_MODE });
    const request = this.rerunSelection.get() ? deepFreeze({ schema_version: 1, submission: body }) : body;
    await this.runSubmission(new MutationIntent<JsonObject>(request, this.options.random));
  }

  /** Explicit re-check of an unknown answer: the same key and frozen body, once. */
  async recheck(): Promise<void> {
    const s = this.store.get();
    if (this.disposed || s.step !== "unknown" || !s.pending || !s.failedAt) return;
    if (s.failedAt === "validation") await this.runValidation(s.pending);
    else if (s.failedAt === "resolution") await this.runResolution(s.pending);
    else await this.runSubmission(s.pending);
  }

  /** Forget the prepared batch (not while a request is in flight). An unknown submission is given up explicitly. */
  reset(): void {
    if (this.busy()) return;
    this.gate.release(this.submitOwner);
    this.store.set(initial);
  }

  private async runValidation(intent: MutationIntent<JsonObject>): Promise<void> {
    this.store.patch({ step: "validating", failedAt: null, error: null, pending: intent });
    const prepared = this.store.get().prepared!;
    try {
      const result = await this.api.validate(intent);
      if (this.disposed) return;
      const refs = canonical(result.presetRef) === canonical(prepared.session.preset_ref) && canonical(result.policyRef) === canonical(prepared.session.policy_ref);
      const shape = refs && result.files.length === prepared.files.length && result.files.every((file, i) => file.clientFileId === prepared.files[i]!.fileId);
      // Never send what the screen did not show: every file must match exactly.
      const mismatched = shape
        ? prepared.files.filter((file, i) => {
            const got = result.files[i]!;
            return !got.valid || got.effectiveSettings === null || canonical(got.effectiveSettings) !== canonical(file.effective);
          })
        : prepared.files;
      if (mismatched.length > 0) {
        this.store.patch({ step: "rejected", failedAt: "validation", mismatched: mismatched.map((f) => f.rowId), pending: null, prepared: { ...prepared, validation: result } });
        return;
      }
      this.store.patch({ prepared: { ...prepared, validation: result } });
      await this.runResolution(new MutationIntent<JsonObject>(prepared.session, this.options.random));
    } catch (error) {
      this.fail("validation", error, intent);
    }
  }

  private async runResolution(intent: MutationIntent<JsonObject>): Promise<void> {
    this.store.patch({ step: "resolving", failedAt: null, error: null, pending: intent });
    try {
      const resolution = await this.api.resolve(intent);
      if (this.disposed) return;
      const prepared = this.store.get().prepared!;
      if (resolution.files.length !== prepared.files.length || !resolution.files.every((file, i) => file.clientFileId === prepared.files[i]!.fileId)) {
        this.store.patch({ step: "rejected", failedAt: "resolution", error: toApiError(new Error("resolution shape")), pending: null });
        return;
      }
      this.store.patch({ step: "resolved", pending: null, prepared: { ...prepared, resolution } });
    } catch (error) {
      this.fail("resolution", error, intent);
    }
  }

  private async runSubmission(intent: MutationIntent<JsonObject>): Promise<void> {
    if (!this.gate.owns(this.submitOwner) && !this.gate.acquire(this.submitOwner)) return;
    const target = this.rerunSelection.get();
    this.store.patch({ step: "submitting", failedAt: null, error: null, pending: intent });
    const prepared = this.store.get().prepared!;
    try {
      const { batch } = target
        ? await this.api.rerun(target.jobId, target.revision, intent)
        : await this.api.submit(intent);
      if (this.disposed) return;
      const expected = prepared.files.map((f) => f.fileId);
      const got = batch.jobs.map((job) => job.clientFileId);
      if (canonical([...got].sort()) !== canonical([...expected].sort())) {
        // An answer that is not exactly one job per file is not a confirmed admission.
        this.store.patch({ step: "unknown", failedAt: "submission", error: toApiError(new Error("batch shape")), pending: intent });
        return;
      }
      const jobs = expected.map((fileId) => batch.jobs.find((job) => job.clientFileId === fileId)!);
      this.store.patch({ step: "accepted", pending: null, error: null, jobs });
      if (target) this.options.onRerunSettled?.();
    } catch (error) {
      this.fail("submission", error, intent);
      if (target && this.store.get().error?.status === 412) this.options.onRerunSettled?.();
    } finally {
      if (this.store.get().step !== "unknown") this.gate.release(this.submitOwner);
    }
  }

  private fail(stage: BatchStage, error: unknown, intent: MutationIntent<JsonObject>): void {
    if (this.disposed) return;
    const apiError = toApiError(error);
    const outcome = classifyStartFailure(apiError);
    this.store.patch({ step: outcome, failedAt: stage, error: apiError, pending: outcome === "unknown" ? intent : null });
  }

  dispose(): void {
    this.disposed = true;
  }
}
