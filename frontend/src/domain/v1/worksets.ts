import type { ApiError } from "../../api/errors";
import { MutationIntent } from "../../api/idempotency";
import type { InputAvailability, RecoveryItem, ViewMode, WorksetDoc, WorksetLimits, WorksetsApi, WorksetSummary } from "../../api/worksets";
import type { InputCollectionController, InputRow } from "../inputCollection";
import { toApiError } from "../operation";
import type { Mode, ShellController } from "../shell";
import { Store } from "../store";
import type { TranscriptionFormController } from "../transcription";
import { browserScheduler, type Scheduler } from "../../transport/scheduler";
import type { V1LibraryController } from "./library";
import { emptyWorksetState, project, restorePlan, stateKey } from "./worksetProjection";

/**
 * E-B: named worksets with automatic save and restore (`--next-ui-v1` only).
 *
 * Conflict model
 * - The server revision is the only CAS token (`If-Match`). `baseline` is the canonical state
 *   the server is known to hold for that revision; "dirty" = the projection differs from it.
 * - Changes are coalesced: one save after `debounceMs` of quiet, at most `maxWaitMs` after the
 *   first unsaved change, one request in flight, and the latest state only (no history).
 * - 412: the latest is read; identical content (our earlier write whose answer was lost) is
 *   adopted, anything else is a conflict the person resolves (load theirs / keep mine).
 * - No answer: retried with backoff under the same `If-Match`, so an applied write answers 412
 *   and is then recognised by GET — never a duplicate write, never a silent overwrite.
 * - 400/413/422: shown with its fixed code; the same content is not sent again.
 * - A generation is bumped whenever another workset becomes current, so a late answer for the
 *   previous one never writes into the new one; switching first flushes and refuses while the
 *   save is not confirmed, a v1 submission is in flight, or a conflict is open.
 * - Restoring changes no job, queue, admission record or model: it replaces the input list
 *   (new page-local row ids), the common output and the frozen Preset/Policy references only.
 */

export type SavePhase = "clean" | "dirty" | "saving" | "error" | "unknown" | "conflict";
export type ConflictKind = "remote_changed" | "deleted" | "edited_during_load";
export type OpKind = "switch" | "create" | "duplicate" | "rename" | "delete" | "restore" | "start";
export type OpBlock = "busy_job" | "unsaved" | "conflict" | "op_pending" | "limit" | "name_invalid" | "locked_rows";

export interface WorksetsState {
  phase: "idle" | "loading" | "ready" | "error";
  error: ApiError | null;
  items: readonly WorksetSummary[];
  limits: WorksetLimits | null;
  current: { id: string; name: string; revision: number } | null;
  save: { phase: SavePhase; error: ApiError | null; savedAt: string | null };
  conflict: { kind: ConflictKind; remote: WorksetDoc | null } | null;
  /** Another window saved a newer revision while this one had nothing unsaved. */
  outdated: { revision: number } | null;
  op: { kind: OpKind | null; phase: "idle" | "pending" | "error" | "unknown"; error: ApiError | null; block: OpBlock | null };
  check: { phase: "idle" | "pending" | "done" | "error"; missing: number; notFile: number; unchecked: number };
  recovery: { phase: "idle" | "pending" | "done" | "error"; items: readonly RecoveryItem[]; error: ApiError | null };
}

export interface WorksetOptions {
  /** True while a v1 validation/resolution/submission (or its unknown outcome) is open. */
  jobBusy: () => boolean;
  /** Localized default names ("作業セット1" / "Workset 1"). */
  nameFor: (n: number) => string;
  scheduler?: Scheduler;
  now?: () => number;
  debounceMs?: number;
  maxWaitMs?: number;
}

export const SAVE_DEBOUNCE_MS = 800;
export const SAVE_MAX_WAIT_MS = 4000;
export const RETRY_DELAYS_MS = [2000, 5000, 10_000, 30_000] as const;
export const MAX_NAME = 120;

const IDLE_OP: WorksetsState["op"] = { kind: null, phase: "idle", error: null, block: null };

export function checkName(name: string): string | null {
  const trimmed = name.trim();
  if (trimmed === "" || trimmed.length > MAX_NAME || /[\u0000-\u001f\u007f]/.test(name)) return null;
  return trimmed;
}

interface Snapshot {
  rows: unknown;
  primaryId: unknown;
  outputDir: unknown;
  outputSource: unknown;
  screen: unknown;
  preset: unknown;
  policy: unknown;
}

export class WorksetController {
  readonly store = new Store<WorksetsState>({
    phase: "idle",
    error: null,
    items: [],
    limits: null,
    current: null,
    save: { phase: "clean", error: null, savedAt: null },
    conflict: null,
    outdated: null,
    op: IDLE_OP,
    check: { phase: "idle", missing: 0, notFile: 0, unchecked: 0 },
    recovery: { phase: "idle", items: [], error: null },
  });

  private baseline: string | null = null;
  private savedName: string | null = null;
  private failedKey: string | null = null;
  private generation = 0;
  private applying = false;
  private editsDuringLoad = 0;
  private modeTouchedDuringLoad = false;
  private snapshot: Snapshot | null = null;
  private timer: unknown = null;
  private retryTimer: unknown = null;
  private retryIndex = 0;
  private firstDirtyAt: number | null = null;
  private inflight: Promise<void> | null = null;
  private modeTimer: unknown = null;
  private createIntent: MutationIntent<{ schema_version: 1; name: string; state?: ReturnType<typeof project> }> | null = null;
  private disposed = false;
  private readonly unsubscribers: (() => void)[] = [];
  private readonly scheduler: Scheduler;

  constructor(
    private readonly api: WorksetsApi,
    private readonly inputs: InputCollectionController,
    private readonly form: TranscriptionFormController,
    private readonly library: V1LibraryController,
    private readonly shell: ShellController,
    private readonly options: WorksetOptions,
  ) {
    this.scheduler = options.scheduler ?? browserScheduler;
    for (const store of [inputs.store, form.store, library.store] as const) {
      this.unsubscribers.push(store.subscribe(() => this.onChange()));
    }
    let mode = shell.store.get().mode;
    this.unsubscribers.push(
      shell.store.subscribe((s) => {
        if (s.mode === mode) return;
        mode = s.mode;
        this.onModeChange();
      }),
    );
  }

  // ---- reading -------------------------------------------------------------------------------

  /** The canonical state the screen would save now. */
  projectionKey(): string {
    return stateKey(this.projection());
  }

  /** Anything not confirmed by the server (leaving the page would lose it). */
  hasUnsaved(): boolean {
    const s = this.store.get();
    if (s.phase !== "ready" || !s.current) return false;
    return s.save.phase !== "clean" || this.projectionKey() !== this.baseline || s.current.name !== this.savedName;
  }

  /** Why switching/creating/deleting is refused now (null = allowed after a flush). */
  switchBlock(): OpBlock | null {
    const s = this.store.get();
    if (s.op.phase === "pending") return "op_pending";
    if (this.options.jobBusy()) return "busy_job";
    if (s.conflict) return "conflict";
    if (s.save.phase === "error" || s.save.phase === "unknown") return "unsaved";
    return null;
  }

  // ---- start-up ------------------------------------------------------------------------------

  /** Read (and on first run create) the store, then restore the last active workset. GET-only otherwise. */
  async start(): Promise<void> {
    if (this.disposed || this.store.get().phase === "loading") return;
    const generation = ++this.generation;
    this.editsDuringLoad = 0;
    this.modeTouchedDuringLoad = false;
    this.snapshot = this.take();
    this.store.patch({ phase: "loading", error: null, op: { ...IDLE_OP, kind: "start", phase: "pending" } });
    try {
      let listing = await this.api.list();
      if (!listing.exists || listing.items.length === 0) listing = await this.api.bootstrap(this.options.nameFor(1));
      if (generation !== this.generation) return;
      const pick = listing.active?.worksetId ?? listing.items[0]!.id;
      const doc = await this.api.get(pick);
      if (generation !== this.generation || this.disposed) return;
      this.store.patch({ items: listing.items, limits: listing.limits });
      const localKey = this.projectionKey();
      const pristine = stateKey(emptyWorksetState());
      if (this.editsDuringLoad > 0 && localKey !== pristine && localKey !== stateKey(doc.state)) {
        this.adoptCurrent(doc);
        if (stateKey(doc.state) === pristine) {
          // A new, empty workset: what was entered while it loaded is simply saved into it.
          this.store.patch({ phase: "ready", op: IDLE_OP });
          this.schedule();
        } else {
          this.store.patch({ phase: "ready", op: IDLE_OP, conflict: { kind: "edited_during_load", remote: doc }, save: { ...this.store.get().save, phase: "conflict" } });
        }
      } else {
        this.apply(doc);
        this.store.patch({ phase: "ready", op: IDLE_OP });
      }
      if (listing.active && !this.modeTouchedDuringLoad) this.shell.setMode(listing.active.mode);
      void this.checkInputs();
    } catch (error) {
      if (generation !== this.generation) return;
      // Nothing is shown as saved: the screen keeps working in memory only, and says so.
      this.store.patch({ phase: "error", error: toApiError(error), op: IDLE_OP });
    }
  }

  // ---- saving --------------------------------------------------------------------------------

  /** Save now (cancels the debounce); resolves when the latest state is confirmed or failed. */
  async flush(): Promise<boolean> {
    this.clearTimer();
    for (let i = 0; i < 3; i += 1) {
      if (this.inflight) await this.inflight;
      const s = this.store.get();
      if (s.phase !== "ready" || !s.current || s.conflict) return false;
      if (!this.needsSave()) {
        if (s.save.phase !== "clean") this.patchSave({ phase: "clean", error: null });
        return true;
      }
      await this.runSave();
      if (this.store.get().save.phase !== "clean" && this.store.get().save.phase !== "dirty") return false;
    }
    return !this.needsSave();
  }

  /** Explicit retry after an error (the same content may be sent again under CAS). */
  retry(): void {
    this.failedKey = null;
    this.retryIndex = 0;
    void this.runSave();
  }

  // ---- conflicts -----------------------------------------------------------------------------

  /** Discard this window's unsaved changes and show the stored version. */
  loadRemote(): void {
    const s = this.store.get();
    const remote = s.conflict?.remote ?? null;
    if (!remote || s.op.phase === "pending" || this.options.jobBusy()) return;
    this.generation += 1;
    this.store.patch({ conflict: null, outdated: null });
    this.apply(remote);
    void this.checkInputs();
  }

  /** Keep this window's state and write it over the stored version (an explicit CAS on it). */
  overwriteRemote(): void {
    const s = this.store.get();
    const remote = s.conflict?.remote;
    if (!remote || !s.current) return;
    this.baseline = stateKey(remote.state);
    this.savedName = remote.name;
    this.failedKey = null;
    this.store.patch({ conflict: null, outdated: null, current: { ...s.current, revision: remote.revision } });
    this.patchSave({ phase: "dirty", error: null });
    void this.runSave();
  }

  /** After the current workset was deleted elsewhere: keep the screen as a new workset. */
  async saveAsNew(): Promise<void> {
    const s = this.store.get();
    if (s.conflict?.kind !== "deleted" || !s.current) return;
    await this.createWorkset(s.current.name, true, true);
  }

  /** Read the stored version again (window focus): reports a newer save from another window. */
  async checkRemote(): Promise<void> {
    const s = this.store.get();
    if (s.phase !== "ready" || !s.current || s.op.phase === "pending") return;
    const generation = this.generation;
    try {
      const [listing, doc] = await Promise.all([this.api.list(), this.api.get(s.current.id)]);
      if (generation !== this.generation) return;
      this.store.patch({ items: listing.items, limits: listing.limits });
      const now = this.store.get();
      if (!now.current || doc.revision <= now.current.revision) return;
      if (stateKey(doc.state) === this.baseline && doc.name === this.savedName) {
        this.store.patch({ current: { ...now.current, revision: doc.revision } });
      } else if (now.save.phase === "clean" && !this.needsSave()) {
        this.store.patch({ outdated: { revision: doc.revision }, conflict: { kind: "remote_changed", remote: doc } });
      }
    } catch (error) {
      if (generation !== this.generation) return;
      if (toApiError(error).status === 404) this.store.patch({ conflict: { kind: "deleted", remote: null }, save: { ...this.store.get().save, phase: "conflict" } });
    }
  }

  // ---- named worksets ------------------------------------------------------------------------

  async switchTo(id: string): Promise<boolean> {
    const s = this.store.get();
    if (s.phase !== "ready" || s.current?.id === id) return false;
    if (!(await this.prepareLeave("switch"))) return false;
    const generation = ++this.generation;
    this.store.patch({ op: { ...IDLE_OP, kind: "switch", phase: "pending" } });
    try {
      const doc = await this.api.get(id);
      if (generation !== this.generation) return false;
      this.store.patch({ op: IDLE_OP, outdated: null, conflict: null });
      this.apply(doc);
      void this.rememberActive();
      void this.checkInputs();
      return true;
    } catch (error) {
      if (generation === this.generation) this.store.patch({ op: { ...IDLE_OP, kind: "switch", phase: "error", error: toApiError(error) } });
      void this.refreshList();
      return false;
    }
  }

  /** New empty workset (`duplicate` = a copy of the current screen), then open it. */
  async create(duplicate = false): Promise<boolean> {
    const s = this.store.get();
    if (s.phase !== "ready") return false;
    if (s.limits && s.items.length >= s.limits.maxWorksets) {
      this.store.patch({ op: { ...IDLE_OP, kind: duplicate ? "duplicate" : "create", phase: "error", block: "limit" } });
      return false;
    }
    const name = duplicate && s.current ? this.nextName(s.current.name) : this.nextName(null);
    if (!(await this.prepareLeave(duplicate ? "duplicate" : "create"))) return false;
    return this.createWorkset(name, duplicate, false);
  }

  /** Explicit re-check of a create whose answer was lost: the same key and body, once. */
  async recheckCreate(): Promise<boolean> {
    const s = this.store.get();
    if (s.op.phase !== "unknown" || !this.createIntent) return false;
    return this.sendCreate(this.createIntent, s.op.kind === "duplicate");
  }

  dismissOp(): void {
    if (this.store.get().op.phase === "pending") return;
    this.createIntent = null;
    this.store.patch({ op: IDLE_OP });
  }

  /** Rename = the same CAS save as any edit, sent at once. */
  rename(name: string): boolean {
    const s = this.store.get();
    const checked = checkName(name);
    if (!s.current || s.phase !== "ready") return false;
    if (checked === null) {
      this.store.patch({ op: { ...IDLE_OP, kind: "rename", phase: "error", block: "name_invalid" } });
      return false;
    }
    if (checked === s.current.name) return true;
    this.store.patch({ current: { ...s.current, name: checked }, op: IDLE_OP });
    this.failedKey = null;
    this.patchSave({ phase: "dirty", error: null });
    void this.runSave();
    return true;
  }

  /**
   * Delete one workset (after the view's confirmation). Only the workset and its recovery
   * checkpoints are removed: inputs, outputs, jobs and B7 history are never touched. Deleting the
   * current one discards its unsaved changes and opens the server's successor.
   */
  async remove(id: string): Promise<boolean> {
    const s = this.store.get();
    if (s.phase !== "ready" || s.op.phase === "pending") return false;
    const target = s.items.find((i) => i.id === id);
    if (!target) return false;
    const current = s.current?.id === id;
    if (current && this.options.jobBusy()) {
      this.store.patch({ op: { ...IDLE_OP, kind: "delete", phase: "error", block: "busy_job" } });
      return false;
    }
    if (current) {
      this.clearTimer();
      this.clearRetry();
      if (this.inflight) await this.inflight;
    }
    // Deleting another workset never needs the current one to be saved first.
    const revision = current ? (this.store.get().current?.revision ?? target.revision) : target.revision;
    const generation = current ? ++this.generation : this.generation;
    this.store.patch({ op: { ...IDLE_OP, kind: "delete", phase: "pending" } });
    try {
      const listing = await this.api.remove(id, revision);
      this.store.patch({ items: listing.items, limits: listing.limits, op: IDLE_OP });
      if (current && listing.active) {
        const doc = await this.api.get(listing.active.worksetId);
        if (generation !== this.generation) return true;
        this.store.patch({ conflict: null, outdated: null });
        this.apply(doc);
        void this.checkInputs();
      }
      return true;
    } catch (error) {
      this.store.patch({ op: { ...IDLE_OP, kind: "delete", phase: "error", error: toApiError(error) } });
      void this.refreshList();
      return false;
    }
  }

  async loadRecovery(): Promise<void> {
    const s = this.store.get();
    if (!s.current) return;
    this.store.patch({ recovery: { phase: "pending", items: [], error: null } });
    try {
      const items = await this.api.recovery(s.current.id);
      this.store.patch({ recovery: { phase: "done", items, error: null } });
    } catch (error) {
      this.store.patch({ recovery: { phase: "error", items: [], error: toApiError(error) } });
    }
  }

  /** Make one recovery checkpoint current (the replaced state becomes a checkpoint itself). */
  async restoreRecovery(revision: number): Promise<boolean> {
    if (!(await this.prepareLeave("restore"))) return false;
    const s = this.store.get();
    if (!s.current) return false;
    const generation = ++this.generation;
    this.store.patch({ op: { ...IDLE_OP, kind: "restore", phase: "pending" } });
    try {
      const doc = await this.api.restoreRecovery(s.current.id, revision, s.current.revision);
      if (generation !== this.generation) return false;
      this.store.patch({ op: IDLE_OP, recovery: { phase: "idle", items: [], error: null } });
      this.apply(doc);
      void this.refreshList();
      void this.checkInputs();
      return true;
    } catch (error) {
      if (generation === this.generation) this.store.patch({ op: { ...IDLE_OP, kind: "restore", phase: "error", error: toApiError(error) } });
      return false;
    }
  }

  /** Read-only existence check of the current rows (explicit, and after every restore). */
  async checkInputs(): Promise<void> {
    const rows: readonly InputRow[] = this.inputs.store.get().rows;
    if (rows.length === 0) {
      this.store.patch({ check: { phase: "done", missing: 0, notFile: 0, unchecked: 0 } });
      return;
    }
    const generation = this.generation;
    this.store.patch({ check: { ...this.store.get().check, phase: "pending" } });
    try {
      const states = await this.api.inputStatus(rows.map((r) => r.inputPath));
      if (generation !== this.generation) return;
      const map = new Map<string, InputAvailability>(rows.map((r, i) => [r.clientFileId, states[i]!]));
      this.applying = true;
      try {
        this.inputs.setAvailability(map);
      } finally {
        this.applying = false;
        this.snapshot = this.take();
      }
      this.store.patch({
        check: { phase: "done", missing: states.filter((x) => x === "missing").length, notFile: states.filter((x) => x === "not_file").length, unchecked: states.filter((x) => x === "unchecked").length },
      });
    } catch {
      if (generation === this.generation) this.store.patch({ check: { phase: "error", missing: 0, notFile: 0, unchecked: 0 } });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.clearTimer();
    this.clearRetry();
    if (this.modeTimer !== null) this.scheduler.clearTimeout(this.modeTimer);
    for (const unsubscribe of this.unsubscribers.splice(0)) unsubscribe();
  }

  // ---- internals -----------------------------------------------------------------------------

  private projection() {
    return project(this.inputs.store.get(), this.form.store.get(), this.library.store.get());
  }

  private take(): Snapshot {
    const i = this.inputs.store.get();
    const f = this.form.store.get().draft;
    const l = this.library.store.get();
    return { rows: i.rows, primaryId: i.primaryId, outputDir: f.outputDir, outputSource: f.outputSource, screen: l.screen, preset: l.preset.ref, policy: l.policy.ref };
  }

  private changed(): boolean {
    const next = this.take();
    const before = this.snapshot;
    this.snapshot = next;
    if (!before) return true;
    return (Object.keys(next) as (keyof Snapshot)[]).some((key) => next[key] !== before[key]);
  }

  private onChange(): void {
    if (this.applying || this.disposed || !this.changed()) return;
    const s = this.store.get();
    if (s.phase === "loading") {
      this.editsDuringLoad += 1;
      return;
    }
    if (s.phase !== "ready" || !s.current || s.conflict) return;
    this.schedule();
  }

  private onModeChange(): void {
    const s = this.store.get();
    if (s.phase === "loading") this.modeTouchedDuringLoad = true;
    if (s.phase !== "ready" || !s.current) return;
    if (this.modeTimer !== null) this.scheduler.clearTimeout(this.modeTimer);
    this.modeTimer = this.scheduler.setTimeout(() => {
      this.modeTimer = null;
      void this.rememberActive();
    }, 500);
  }

  private async rememberActive(): Promise<void> {
    const s = this.store.get();
    if (!s.current) return;
    const mode: ViewMode = this.shell.store.get().mode satisfies Mode;
    try {
      await this.api.setActive(s.current.id, mode);
    } catch {
      // Not a data loss: the next switch or mode change sends the absolute value again.
    }
  }

  private needsSave(): boolean {
    const s = this.store.get();
    if (!s.current) return false;
    return this.projectionKey() !== this.baseline || s.current.name !== this.savedName;
  }

  private patchSave(patch: Partial<WorksetsState["save"]>): void {
    this.store.patch({ save: { ...this.store.get().save, ...patch } });
  }

  private schedule(): void {
    const s = this.store.get();
    if (!s.current || s.conflict) return;
    if (!this.needsSave()) {
      if (s.save.phase === "dirty") this.patchSave({ phase: "clean", error: null });
      this.firstDirtyAt = null;
      return;
    }
    if (s.save.phase === "clean") this.patchSave({ phase: "dirty" });
    if (s.save.phase === "saving") return; // the finished save looks again
    const now = (this.options.now ?? Date.now)();
    this.firstDirtyAt ??= now;
    const wait = Math.max(0, Math.min(this.options.debounceMs ?? SAVE_DEBOUNCE_MS, this.firstDirtyAt + (this.options.maxWaitMs ?? SAVE_MAX_WAIT_MS) - now));
    this.clearTimer();
    this.timer = this.scheduler.setTimeout(() => {
      this.timer = null;
      void this.runSave();
    }, wait);
  }

  private clearTimer(): void {
    if (this.timer !== null) this.scheduler.clearTimeout(this.timer);
    this.timer = null;
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) this.scheduler.clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private scheduleRetry(): void {
    this.clearRetry();
    const delay = RETRY_DELAYS_MS[Math.min(this.retryIndex, RETRY_DELAYS_MS.length - 1)]!;
    this.retryIndex += 1;
    this.retryTimer = this.scheduler.setTimeout(() => {
      this.retryTimer = null;
      void this.runSave();
    }, delay);
  }

  private runSave(): Promise<void> {
    if (this.inflight) return this.inflight;
    const run = this.saveOnce().finally(() => {
      this.inflight = null;
    });
    this.inflight = run;
    return run;
  }

  private async saveOnce(): Promise<void> {
    this.clearTimer();
    const s = this.store.get();
    if (this.disposed || s.phase !== "ready" || !s.current || s.conflict) return;
    const state = this.projection();
    const key = stateKey(state);
    const name = s.current.name;
    if (key === this.baseline && name === this.savedName) {
      this.firstDirtyAt = null;
      this.patchSave({ phase: "clean", error: null });
      return;
    }
    if (key === this.failedKey) return;
    const sent = { id: s.current.id, base: s.current.revision, key, name, generation: this.generation };
    this.patchSave({ phase: "saving" });
    try {
      const doc = await this.api.put(sent.id, sent.base, name, state);
      if (sent.generation !== this.generation) return;
      this.saved(doc, key);
    } catch (error) {
      if (sent.generation !== this.generation) return;
      await this.failed(sent, toApiError(error));
    }
  }

  private saved(doc: WorksetDoc, key: string): void {
    const s = this.store.get();
    if (!s.current) return;
    this.baseline = key;
    this.savedName = doc.name;
    this.failedKey = null;
    this.retryIndex = 0;
    this.clearRetry();
    this.firstDirtyAt = null;
    const { state: _state, ...summary } = doc;
    void _state;
    const items = s.items.map((i) => (i.id === doc.id ? summary : i));
    this.store.patch({ current: { ...s.current, revision: doc.revision }, items, save: { phase: "clean", error: null, savedAt: doc.updatedAt }, outdated: null });
    // Edits made while the request was in flight are saved next.
    if (this.needsSave()) this.schedule();
  }

  private async failed(sent: { id: string; base: number; key: string; name: string; generation: number }, error: ApiError): Promise<void> {
    if (error.status === 404) {
      this.patchSave({ phase: "conflict", error });
      this.store.patch({ conflict: { kind: "deleted", remote: null } });
      return;
    }
    if (error.status === 400 || error.status === 413 || error.status === 422 || error.status === 428) {
      this.failedKey = sent.key;
      this.patchSave({ phase: "error", error });
      return;
    }
    // 412 or no answer: the stored version decides (never a blind resend of a new write).
    let remote: WorksetDoc;
    try {
      remote = await this.api.get(sent.id);
    } catch (readError) {
      if (sent.generation !== this.generation) return;
      const read = toApiError(readError);
      if (read.status === 404) {
        this.patchSave({ phase: "conflict", error: read });
        this.store.patch({ conflict: { kind: "deleted", remote: null } });
        return;
      }
      this.patchSave({ phase: "unknown", error });
      this.scheduleRetry();
      return;
    }
    if (sent.generation !== this.generation) return;
    if (stateKey(remote.state) === sent.key && remote.name === sent.name) {
      this.saved(remote, sent.key);
      return;
    }
    if (remote.revision === sent.base && error.status !== 412) {
      // Not applied (the store was busy/unreachable): retried later under the same If-Match.
      this.patchSave({ phase: "error", error });
      this.scheduleRetry();
      return;
    }
    this.patchSave({ phase: "conflict", error });
    this.store.patch({ conflict: { kind: "remote_changed", remote } });
  }

  /** Make `doc` current and put its state on the screen (one boundary; no side effects). */
  private apply(doc: WorksetDoc): void {
    const plan = restorePlan(doc.state);
    this.clearTimer();
    this.clearRetry();
    this.applying = true;
    let libraryRestore: Promise<void> = Promise.resolve();
    try {
      if (!this.inputs.replaceAll(plan.rows, plan.primaryIndex)) throw new Error("locked_rows");
      this.form.restoreOutput(plan.outputDir, plan.outputSource);
      // References are set synchronously (pending); the revision reads finish later.
      libraryRestore = this.library.restore(plan.presetRef, plan.policyRef, plan.screen);
    } finally {
      this.applying = false;
    }
    void libraryRestore;
    this.adoptCurrent(doc);
    this.snapshot = this.take();
    // e.g. an empty common output that now follows the saved default is saved as an edit.
    if (this.needsSave()) this.schedule();
  }

  private adoptCurrent(doc: WorksetDoc): void {
    this.baseline = stateKey(doc.state);
    this.savedName = doc.name;
    this.failedKey = null;
    this.retryIndex = 0;
    this.firstDirtyAt = null;
    this.store.patch({
      current: { id: doc.id, name: doc.name, revision: doc.revision },
      save: { phase: "clean", error: null, savedAt: doc.updatedAt },
      conflict: null,
      recovery: { phase: "idle", items: [], error: null },
      check: { phase: "idle", missing: 0, notFile: 0, unchecked: 0 },
    });
  }

  private async prepareLeave(kind: OpKind): Promise<boolean> {
    const block = this.switchBlock();
    if (block) {
      this.store.patch({ op: { ...IDLE_OP, kind, phase: "error", block } });
      return false;
    }
    if (!(await this.flush())) {
      this.store.patch({ op: { ...IDLE_OP, kind, phase: "error", block: this.store.get().conflict ? "conflict" : "unsaved" } });
      return false;
    }
    if (this.options.jobBusy()) {
      this.store.patch({ op: { ...IDLE_OP, kind, phase: "error", block: "busy_job" } });
      return false;
    }
    return true;
  }

  private async createWorkset(name: string, withState: boolean, replacesDeleted: boolean): Promise<boolean> {
    const body = withState ? { schema_version: 1 as const, name, state: this.projection() } : { schema_version: 1 as const, name };
    const intent = new MutationIntent(body);
    this.createIntent = intent;
    return this.sendCreate(intent, withState, replacesDeleted);
  }

  private async sendCreate(intent: NonNullable<WorksetController["createIntent"]>, duplicate: boolean, replacesDeleted = false): Promise<boolean> {
    const kind: OpKind = duplicate ? "duplicate" : "create";
    const generation = ++this.generation;
    this.store.patch({ op: { ...IDLE_OP, kind, phase: "pending" } });
    try {
      const doc = await this.api.create(intent);
      this.createIntent = null;
      if (generation !== this.generation) return false;
      const { state: _state, ...summary } = doc;
      void _state;
      this.store.patch({ op: IDLE_OP, conflict: null, outdated: null, items: [...this.store.get().items.filter((i) => i.id !== doc.id), summary] });
      if (replacesDeleted || duplicate) {
        // The screen already shows this state; only the workset identity changes.
        this.adoptCurrent(doc);
        this.snapshot = this.take();
        if (this.needsSave()) this.schedule();
      } else {
        this.apply(doc);
      }
      void this.rememberActive();
      void this.refreshList();
      return true;
    } catch (error) {
      const e = toApiError(error);
      if (generation === this.generation) {
        this.store.patch({ op: { ...IDLE_OP, kind, phase: intent.state === "unknown" ? "unknown" : "error", error: e } });
      }
      if (intent.state !== "unknown") this.createIntent = null;
      return false;
    }
  }

  private async refreshList(): Promise<void> {
    try {
      const listing = await this.api.list();
      this.store.patch({ items: listing.items, limits: listing.limits });
    } catch {
      // The list is refreshed again on the next operation or window focus.
    }
  }

  private nextName(base: string | null): string {
    const names = new Set(this.store.get().items.map((i) => i.name));
    for (let n = base === null ? 1 : 2; n < 1000; n += 1) {
      const candidate = base === null ? this.options.nameFor(n) : `${base} (${n})`;
      if (!names.has(candidate) && checkName(candidate) === candidate) return candidate;
    }
    return this.options.nameFor(Date.now() % 100000);
  }
}
