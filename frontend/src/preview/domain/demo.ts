import { Store } from "../../domain/store";
import {
  DEFAULT_COMMON,
  type DemoInput,
  initialInputs,
  type ProcessingSettings,
  poolInput,
  SETTING_KEYS,
  type SettingKey,
} from "./fixtures";

/**
 * In-memory state of the UI-3-0 prototype. Framework-independent (like `domain/`), so mode
 * switches and navigation never lose it. It never calls an API, the host bridge or a timer:
 * every transition happens only because the reviewer pressed a (clearly labelled) demo control.
 * A reload loses everything by design (G-05/G-06 are open owner decisions).
 */
export type PreviewLanguage = "ja" | "en";
export type PreviewTheme = "system" | "light" | "dark";
export type Overrides = Partial<ProcessingSettings>;

export type JobState = "waiting" | "preparing" | "running" | "succeeded" | "failed" | "cancelled";
export type JobStage = "asr" | "diarization" | "output";
export const ACTIVE_STATES: readonly JobState[] = ["waiting", "preparing", "running"];

export interface DemoJob {
  id: string;
  inputId: string;
  inputName: string;
  state: JobState;
  stage: JobStage | null;
  attempt: number;
  /** Settings frozen at submission ("settings at submit time"); edits afterwards do not change it. */
  snapshot: ProcessingSettings;
  errorCode: string | null;
}

/** Per-area display state, switched explicitly by the reviewer (empty/loading/error/ready). */
export type AreaState = "ready" | "loading" | "error";
export type Scenario = "default" | "empty" | "loading" | "error" | "busy" | "success";
export const SCENARIOS: readonly Scenario[] = ["default", "empty", "loading", "error", "busy", "success"];
/** How the connection indicator is drawn. "none" = honest default (no backend connection at all). */
export type SyncExample = "none" | "synced" | "resyncing" | "offline";
export type InspectorTab = "common" | "selection";

export interface Notice {
  key: string;
  params: Record<string, string | number>;
  seq: number;
}

export interface DemoState {
  inputs: DemoInput[];
  poolCursor: number;
  selected: string[];
  common: ProcessingSettings;
  overrides: Record<string, Overrides>;
  inspectorTab: InspectorTab;
  jobs: DemoJob[];
  jobSeq: number;
  selectedJobId: string | null;
  queuePaused: boolean;
  scenario: Scenario;
  inputsArea: AreaState;
  queueArea: AreaState;
  sync: SyncExample;
  language: PreviewLanguage;
  theme: PreviewTheme;
  notice: Notice | null;
}

function cloneSettings(s: ProcessingSettings): ProcessingSettings {
  return { ...s, formats: [...s.formats] };
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && [...a].sort().join() === [...b].sort().join();
  return a === b;
}

/** Effective settings of one input = common defaults with that input's overrides on top. */
export function effectiveSettings(state: Pick<DemoState, "common" | "overrides">, inputId: string): ProcessingSettings {
  const o = state.overrides[inputId] ?? {};
  return cloneSettings({ ...state.common, ...o, formats: o.formats ?? state.common.formats });
}

export function overrideCount(state: Pick<DemoState, "overrides">, inputId: string): number {
  return Object.keys(state.overrides[inputId] ?? {}).length;
}

export type OverrideCoverage = "none" | "some" | "all";
export interface FieldView<K extends SettingKey = SettingKey> {
  /** Value shown in the field; `mixed` when selected inputs differ. */
  value: ProcessingSettings[K];
  mixed: boolean;
  coverage: OverrideCoverage;
}

/** What the "selected inputs" Inspector shows for one field across the current selection. */
export function selectionField<K extends SettingKey>(state: DemoState, key: K): FieldView<K> {
  const ids = state.selected;
  if (ids.length === 0) return { value: state.common[key], mixed: false, coverage: "none" };
  const values = ids.map((id) => effectiveSettings(state, id)[key]);
  const overridden = ids.filter((id) => key in (state.overrides[id] ?? {})).length;
  const first = values[0] as ProcessingSettings[K];
  return {
    value: first,
    mixed: values.some((v) => !sameValue(v, first)),
    coverage: overridden === 0 ? "none" : overridden === ids.length ? "all" : "some",
  };
}

const NEXT: Record<JobState, JobState | null> = {
  waiting: "preparing",
  preparing: "running",
  running: null, // depends on the stage
  succeeded: null,
  failed: null,
  cancelled: null,
};

/** One explicit demo step. Running walks ASR → (diarization if enabled) → output → succeeded. */
export function stepJob(job: DemoJob): DemoJob {
  if (job.state === "running") {
    if (job.stage === "asr") return { ...job, stage: job.snapshot.diarization ? "diarization" : "output" };
    if (job.stage === "diarization") return { ...job, stage: "output" };
    return { ...job, state: "succeeded", stage: null };
  }
  const next = NEXT[job.state];
  if (!next) return job;
  return { ...job, state: next, stage: next === "running" ? "asr" : null };
}

export function isActive(job: DemoJob): boolean {
  return ACTIVE_STATES.includes(job.state);
}

/** Inputs that already have a job (in any state). */
export function submittedInputs(state: Pick<DemoState, "jobs">): Set<string> {
  return new Set(state.jobs.map((j) => j.inputId));
}

function initialState(): DemoState {
  return {
    inputs: initialInputs(),
    poolCursor: 0,
    selected: ["in-01"],
    common: cloneSettings(DEFAULT_COMMON),
    overrides: { "in-02": { language: "en", speakers: "2" } },
    inspectorTab: "common",
    jobs: [],
    jobSeq: 0,
    selectedJobId: null,
    queuePaused: false,
    scenario: "default",
    inputsArea: "ready",
    queueArea: "ready",
    sync: "none",
    language: "ja",
    theme: "light",
    notice: null,
  };
}

export class DemoController {
  readonly store = new Store<DemoState>(initialState());
  private noticeSeq = 0;

  private notify(key: string, params: Record<string, string | number> = {}): Notice {
    this.noticeSeq += 1;
    return { key, params, seq: this.noticeSeq };
  }

  private update(fn: (s: DemoState) => Partial<DemoState>, key?: string, params?: Record<string, string | number>): void {
    this.store.set((s) => ({ ...s, ...fn(s), ...(key ? { notice: this.notify(key, params) } : {}) }));
  }

  /** Back to the deterministic initial state; language and theme are kept for the reviewer. */
  reset(): void {
    const { language, theme } = this.store.get();
    this.store.set({ ...initialState(), language, theme, notice: this.notify("notice.reset") });
  }

  setLanguage(language: PreviewLanguage): void {
    this.update(() => ({ language }));
  }

  setTheme(theme: PreviewTheme): void {
    this.update(() => ({ theme }), "notice.theme");
  }

  // ---- inputs -------------------------------------------------------------------------------
  addFiles(count: number): void {
    this.update((s) => {
      const added = Array.from({ length: count }, (_, i) => poolInput(s.poolCursor + i));
      return { inputs: [...s.inputs, ...added], poolCursor: s.poolCursor + count, inputsArea: "ready" };
    }, "notice.added", { count });
  }

  toggleSelect(id: string): void {
    this.update((s) => ({ selected: s.selected.includes(id) ? s.selected.filter((x) => x !== id) : [...s.selected, id] }));
  }

  selectAll(): void {
    this.update((s) => ({ selected: s.inputs.map((i) => i.id) }));
  }

  clearSelection(): void {
    this.update(() => ({ selected: [] }));
  }

  /** Removes the selected inputs from the (in-memory) list; submitted jobs keep their snapshot. */
  removeSelected(): void {
    this.remove(this.store.get().selected);
  }

  remove(ids: string[]): void {
    const gone = new Set(ids);
    const count = this.store.get().inputs.filter((i) => gone.has(i.id)).length;
    if (count === 0) return;
    this.update((s) => {
      const overrides = Object.fromEntries(Object.entries(s.overrides).filter(([id]) => !gone.has(id)));
      return { inputs: s.inputs.filter((i) => !gone.has(i.id)), selected: s.selected.filter((id) => !gone.has(id)), overrides };
    }, "notice.removed", { count });
  }

  // ---- settings -----------------------------------------------------------------------------
  setInspectorTab(inspectorTab: InspectorTab): void {
    this.update(() => ({ inspectorTab }));
  }

  setCommon<K extends SettingKey>(key: K, value: ProcessingSettings[K]): void {
    this.update((s) => ({ common: { ...s.common, [key]: value } }), "notice.commonChanged");
  }

  resetCommon(): void {
    this.update(() => ({ common: cloneSettings(DEFAULT_COMMON) }), "notice.commonReset");
  }

  /** Sets an override on every selected input (the field becomes "individual" for all of them). */
  setOverride<K extends SettingKey>(key: K, value: ProcessingSettings[K]): void {
    const count = this.store.get().selected.length;
    if (count === 0) return;
    this.update((s) => {
      const overrides = { ...s.overrides };
      for (const id of s.selected) overrides[id] = { ...overrides[id], [key]: Array.isArray(value) ? [...value] : value };
      return { overrides };
    }, "notice.overrideSet", { count });
  }

  /** Turns an override on with the currently effective value, so nothing changes visually yet. */
  startOverride(key: SettingKey): void {
    const s = this.store.get();
    if (s.selected.length === 0) return;
    this.setOverride(key, selectionField(s, key).value);
  }

  /** "Back to common" for one field of the selected inputs. */
  resetOverride(key: SettingKey): void {
    const count = this.store.get().selected.length;
    if (count === 0) return;
    this.update((s) => {
      const overrides: Record<string, Overrides> = {};
      for (const [id, o] of Object.entries(s.overrides)) {
        if (!s.selected.includes(id)) {
          overrides[id] = o;
          continue;
        }
        const rest = { ...o };
        delete rest[key];
        if (Object.keys(rest).length > 0) overrides[id] = rest;
      }
      return { overrides };
    }, "notice.overrideReset", { count });
  }

  resetAllOverrides(): void {
    const s = this.store.get();
    for (const key of SETTING_KEYS) if (selectionField(s, key).coverage !== "none") this.resetOverride(key);
  }

  // ---- jobs (synthetic only; nothing is created anywhere but in this store) -------------------
  /**
   * Creates demo jobs for the given inputs that are supported and were never submitted. A failed or
   * cancelled job is resumed with "retry"; re-running a finished input (B7 `rerun`) is an S3 design
   * item, so it is not offered here. Returns how many jobs were created.
   */
  submit(inputIds: string[]): number {
    const s = this.store.get();
    const submitted = submittedInputs(s);
    const targets = s.inputs.filter((i) => inputIds.includes(i.id) && i.supported && !submitted.has(i.id));
    if (targets.length === 0) {
      this.update(() => ({}), "notice.nothingToSubmit");
      return 0;
    }
    this.update((cur) => {
      let seq = cur.jobSeq;
      const created = targets.map((i): DemoJob => {
        seq += 1;
        return {
          id: `demo-job-${String(seq).padStart(3, "0")}`,
          inputId: i.id,
          inputName: i.name,
          state: "waiting",
          stage: null,
          attempt: 1,
          snapshot: effectiveSettings(cur, i.id),
          errorCode: null,
        };
      });
      return { jobs: [...cur.jobs, ...created], jobSeq: seq, selectedJobId: created[0]?.id ?? cur.selectedJobId, queueArea: "ready" };
    }, "notice.submitted", { count: targets.length });
    return targets.length;
  }

  selectJob(id: string | null): void {
    this.update(() => ({ selectedJobId: id }));
  }

  private mapJob(id: string, fn: (j: DemoJob) => DemoJob, key: string): void {
    const job = this.store.get().jobs.find((j) => j.id === id);
    if (!job) return;
    const next = fn(job);
    if (next === job) return;
    this.update((s) => ({ jobs: s.jobs.map((j) => (j.id === id ? next : j)) }), key, { name: job.inputName });
  }

  /** Demo control: advance one job by one step (the queue order is not enforced in the demo). */
  step(id: string): void {
    this.mapJob(id, stepJob, "notice.stepped");
  }

  /** Demo control: advance every active job by one step unless the queue is paused. */
  stepAll(): void {
    const s = this.store.get();
    if (s.queuePaused) {
      this.update(() => ({}), "notice.paused");
      return;
    }
    if (!s.jobs.some(isActive)) {
      this.update(() => ({}), "notice.nothingActive");
      return;
    }
    this.update((cur) => ({ jobs: cur.jobs.map((j) => (isActive(j) ? stepJob(j) : j)) }), "notice.steppedAll");
  }

  /** Demo control: make a preparing/running job fail with a synthetic code. */
  fail(id: string): void {
    this.mapJob(id, (j) => (j.state === "preparing" || j.state === "running" ? { ...j, state: "failed", errorCode: "demo_failure" } : j), "notice.failed");
  }

  cancel(id: string): void {
    this.mapJob(id, (j) => (isActive(j) ? { ...j, state: "cancelled", stage: null } : j), "notice.cancelled");
  }

  /** Retry keeps the frozen snapshot (a new attempt of the same job, not a re-submission). */
  retry(id: string): void {
    this.mapJob(
      id,
      (j) => (j.state === "failed" || j.state === "cancelled" ? { ...j, state: "waiting", stage: null, errorCode: null, attempt: j.attempt + 1 } : j),
      "notice.retried",
    );
  }

  /** Reorder among waiting jobs only (running jobs keep their slot). */
  move(id: string, delta: -1 | 1): void {
    const jobs = [...this.store.get().jobs];
    const from = jobs.findIndex((j) => j.id === id);
    const job = jobs[from];
    if (!job || job.state !== "waiting") return;
    let to = from + delta;
    while (to >= 0 && to < jobs.length && jobs[to]!.state !== "waiting") to += delta;
    if (to < 0 || to >= jobs.length) return;
    jobs[from] = jobs[to]!;
    jobs[to] = job;
    this.update(() => ({ jobs }), "notice.moved", { name: job.inputName });
  }

  togglePause(): void {
    this.update((s) => ({ queuePaused: !s.queuePaused }), this.store.get().queuePaused ? "notice.resumed" : "notice.pausedOn");
  }

  clearFinished(): void {
    this.update((s) => {
      const jobs = s.jobs.filter(isActive);
      return { jobs, selectedJobId: jobs.some((j) => j.id === s.selectedJobId) ? s.selectedJobId : null };
    }, "notice.cleared");
  }

  // ---- display-state scenarios ----------------------------------------------------------------
  setSync(sync: SyncExample): void {
    this.update(() => ({ sync }));
  }

  /** "Retry" of a demo error/loading area: it simply returns to ready (no request is made). */
  recoverArea(area: "inputs" | "queue"): void {
    this.update(() => (area === "inputs" ? { inputsArea: "ready" } : { queueArea: "ready" }), "notice.recovered");
  }

  setScenario(scenario: Scenario): void {
    const { language, theme } = this.store.get();
    const base: DemoState = { ...initialState(), language, theme, scenario };
    let next: DemoState = base;
    if (scenario === "empty") next = { ...base, inputs: [], selected: [], overrides: {} };
    if (scenario === "loading") next = { ...base, inputsArea: "loading", queueArea: "loading" };
    if (scenario === "error") next = { ...base, inputsArea: "error", queueArea: "error" };
    this.store.set({ ...next, notice: this.notify("notice.scenario", { scenario }) });
    if (scenario === "busy" || scenario === "success") this.fillQueue(scenario);
  }

  private fillQueue(scenario: "busy" | "success"): void {
    const ids = this.store.get().inputs.filter((i) => i.supported).map((i) => i.id);
    const before = this.store.get().notice;
    this.submit(ids);
    const steps: Record<string, number> = scenario === "success" ? { "in-01": 9, "in-02": 9, "in-03": 9, "in-04": 9 } : { "in-01": 3, "in-02": 1, "in-03": 2 };
    this.store.set((s) => ({
      ...s,
      notice: before,
      jobs: s.jobs.map((j) => {
        let job = j;
        for (let i = 0; i < (steps[j.inputId] ?? 0); i += 1) job = stepJob(job);
        if (scenario === "busy" && j.inputId === "in-03") job = { ...job, state: "failed", errorCode: "demo_failure" };
        return job;
      }),
    }));
  }
}
