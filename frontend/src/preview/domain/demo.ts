import { Store } from "../../domain/store";
import type { EnvExample } from "./envExamples";
import { type DemoInput, initialInputs, poolInput } from "./fixtures";
import { initialUserPresets, type PresetDef, BUILTIN_PRESETS, presetKeys, uniqueName } from "./presets";
import {
  cloneSettings,
  cloneValue,
  DEFAULT_COMMON,
  GROUPS,
  groupOf,
  normalizeEngine,
  type ProcessingSettings,
  type SettingIssue,
  type SettingKey,
  type SettingsCategoryId,
  sameValue,
  validateSettings,
} from "./settingsSchema";

/**
 * In-memory state of the UI-3-0 prototype. Framework-independent (like `domain/`), so mode
 * switches and navigation never lose it. It never calls an API, the host bridge or a timer:
 * every transition happens only because the reviewer pressed a (clearly labelled) demo control.
 * A reload loses everything by design (G-05/G-06 are open owner decisions).
 */
export type PreviewLanguage = "ja" | "en";
/** Light/dark appearance, independent of the accent colour (iteration 4, owner memo #22). */
export type Appearance = "system" | "light" | "dark";
export const APPEARANCES: readonly Appearance[] = ["system", "light", "dark"];
/** Accent colour theme: Utteran green (UI-3) or blue (UI-2). Semantic colours do not change with it. */
export type AccentTheme = "green" | "blue";
export const ACCENT_THEMES: readonly AccentTheme[] = ["green", "blue"];
export type Overrides = Partial<ProcessingSettings>;

export type JobState = "waiting" | "preparing" | "running" | "succeeded" | "failed" | "cancelled";
export type JobStage = "asr" | "diarization" | "output";
export const ACTIVE_STATES: readonly JobState[] = ["waiting", "preparing", "running"];

/**
 * Fictional progress of the current stage (iteration 3 mock of G-03). `fraction` is the share of
 * the CURRENT stage only (null = the stage reports no progress → indeterminate); there is no
 * job-wide percentage, because fixed stage weights would invent a number the backend never sends.
 * Elapsed seconds are synthetic and only move on explicit demo controls.
 */
export interface DemoProgress {
  fraction: number | null;
  stageElapsedSec: number;
  elapsedSec: number;
}

export interface DemoJob {
  id: string;
  inputId: string;
  inputName: string;
  state: JobState;
  stage: JobStage | null;
  progress: DemoProgress;
  attempt: number;
  /** Settings frozen at submission ("settings at submit time"); edits afterwards do not change it. */
  snapshot: ProcessingSettings;
  errorCode: string | null;
}

/** Per-area display state, switched explicitly by the reviewer (empty/loading/error/ready). */
export type AreaState = "ready" | "loading" | "error";
/**
 * Display scenarios. The first five are the Normal (single file) states; "multi"/"busy" load
 * several inputs for the Expert views; "loading"/"error" are area states.
 */
export type Scenario = "single" | "empty" | "running" | "failed" | "done" | "multi" | "busy" | "loading" | "error";
export const SCENARIOS: readonly Scenario[] = ["single", "empty", "running", "failed", "done", "multi", "busy", "loading", "error"];
export type QueueFilter = "all" | "active" | "attention" | "finished";
export type QueueSort = "order" | "name" | "state";
/**
 * Review-only example of how fresh the job status is. The product shows nothing while it is "ok";
 * "checking" = a status refresh is slow (a short inline note where job states are shown);
 * "unavailable" = the status cannot be fetched (a warning with retry). Neither means a job stopped.
 */
export type SyncExample = "ok" | "checking" | "unavailable";
export const SYNC_EXAMPLES: readonly SyncExample[] = ["ok", "checking", "unavailable"];
export type InspectorTab = "common" | "selection";

/** Outcome of the last "submit" in the Expert work area, shown inline under the button. */
export interface SubmitResult {
  created: number;
  unsupported: number;
  alreadySubmitted: number;
  /** Skipped because their effective settings have an error. */
  invalid: number;
  /** Skipped because they use speaker diarization, which is not set up. */
  diarization: number;
  jobIds: string[];
}

export interface Notice {
  key: string;
  params: Record<string, string | number>;
  seq: number;
}

export interface DemoState {
  inputs: DemoInput[];
  poolCursor: number;
  selected: string[];
  /** Last input the user chose or touched; Normal mode shows this one (see normalInputId). */
  focusInputId: string | null;
  common: ProcessingSettings;
  overrides: Record<string, Overrides>;
  inspectorTab: InspectorTab;
  /** Category shown in the Inspector (kept across pane / mode switches). */
  inspectorCategory: SettingsCategoryId;
  /** User presets (built-ins are constants). In memory only. */
  userPresets: PresetDef[];
  presetSeq: number;
  jobs: DemoJob[];
  jobSeq: number;
  selectedJobId: string | null;
  lastSubmit: SubmitResult | null;
  queuePaused: boolean;
  scenario: Scenario;
  inputsArea: AreaState;
  queueArea: AreaState;
  sync: SyncExample;
  /** Fictional environment example (never detected) and whether a demo re-detection is shown. */
  envExample: EnvExample;
  envChecking: boolean;
  /** Speaker diarization is set up in the (fictional) environment. */
  diarizationReady: boolean;
  /** Queue page list controls (search / filter / sort on the synthetic jobs only). */
  queueQuery: string;
  queueFilter: QueueFilter;
  queueSort: QueueSort;
  /** Results page: search text and the opened result. */
  resultsQuery: string;
  selectedResultId: string | null;
  language: PreviewLanguage;
  appearance: Appearance;
  accentTheme: AccentTheme;
  notice: Notice | null;
}

/** Effective settings of one input = common defaults with that input's overrides on top. */
export function effectiveSettings(state: Pick<DemoState, "common" | "overrides">, inputId: string): ProcessingSettings {
  return cloneSettings({ ...state.common, ...(state.overrides[inputId] ?? {}) });
}

/** Number of individually set ITEMS (a linked group such as the engine counts once). */
export function overrideCount(state: Pick<DemoState, "overrides">, inputId: string): number {
  const keys = Object.keys(state.overrides[inputId] ?? {}) as SettingKey[];
  return new Set(keys.map((k) => groupOf(k)[0])).size;
}

/** Problems of the settings one input would run with (empty = it can be submitted). */
export function inputIssues(state: Pick<DemoState, "common" | "overrides">, inputId: string): SettingIssue[] {
  return validateSettings(effectiveSettings(state, inputId));
}

/** Whether an input would need speaker diarization that is not set up. */
export function needsDiarization(state: Pick<DemoState, "common" | "overrides" | "diarizationReady">, inputId: string): boolean {
  return !state.diarizationReady && effectiveSettings(state, inputId).diarization;
}

/** Built-in and user presets in display order. */
export function allPresets(state: Pick<DemoState, "userPresets">): PresetDef[] {
  return [...BUILTIN_PRESETS, ...state.userPresets];
}

export type OverrideCoverage = "none" | "some" | "all";
export interface FieldView<K extends SettingKey = SettingKey> {
  /** Value shown in the field; `mixed` when selected inputs differ. */
  value: ProcessingSettings[K];
  mixed: boolean;
  coverage: OverrideCoverage;
}

/** What the "selected inputs" Inspector shows for one field across the current selection. */
export function selectionField<K extends SettingKey>(state: Pick<DemoState, "selected" | "common" | "overrides">, key: K): FieldView<K> {
  const ids = state.selected;
  if (ids.length === 0) return { value: state.common[key], mixed: false, coverage: "none" };
  const values = ids.map((id) => effectiveSettings(state, id)[key]);
  // A linked group (engine, speaker count) is always overridden as a whole.
  const overridden = ids.filter((id) => groupOf(key).some((k) => k in (state.overrides[id] ?? {}))).length;
  const first = values[0] as ProcessingSettings[K];
  return {
    value: first,
    mixed: values.some((v) => !sameValue(key, v, first)),
    coverage: overridden === 0 ? "none" : overridden === ids.length ? "all" : "some",
  };
}

/** Writes `patch` into one input's override, keeping linked groups whole and the engine consistent. */
function withOverride(state: Pick<DemoState, "common" | "overrides">, inputId: string, patch: Partial<ProcessingSettings>): Overrides {
  let next: ProcessingSettings = { ...effectiveSettings(state, inputId), ...patch };
  if (Object.keys(patch).some((k) => (GROUPS.engine as readonly string[]).includes(k))) next = normalizeEngine(next);
  const o: Overrides = { ...state.overrides[inputId] };
  for (const key of Object.keys(patch) as SettingKey[]) for (const k of groupOf(key)) (o as Record<string, unknown>)[k] = cloneValue(next[k]);
  return o;
}

const NEXT: Record<JobState, JobState | null> = {
  waiting: "preparing",
  preparing: "running",
  running: null, // depends on the stage
  succeeded: null,
  failed: null,
  cancelled: null,
};

export const NO_PROGRESS: DemoProgress = { fraction: null, stageElapsedSec: 0, elapsedSec: 0 };
/** Synthetic seconds added by one demo step / one demo progress advance. */
const STEP_SEC = 20;
const ADVANCE_SEC = 45;

/**
 * Whether a stage reports progress in the fictional mock. ASR (segment end / duration, whisper.cpp
 * "progress = n%") and diarization (pyannote hook completed/total) exist in the core today; the
 * output stage has no progress source, so it stays indeterminate.
 */
export function stageReportsProgress(stage: JobStage): boolean {
  return stage !== "output";
}

function enterStage(job: DemoJob, stage: JobStage): DemoJob {
  return {
    ...job,
    stage,
    progress: { fraction: stageReportsProgress(stage) ? 0 : null, stageElapsedSec: 0, elapsedSec: job.progress.elapsedSec + STEP_SEC },
  };
}

/** One explicit demo step. Running walks ASR → (diarization if enabled) → output → succeeded. */
export function stepJob(job: DemoJob): DemoJob {
  if (job.state === "running") {
    if (job.stage === "asr") return enterStage(job, job.snapshot.diarization ? "diarization" : "output");
    if (job.stage === "diarization") return enterStage(job, "output");
    return { ...job, state: "succeeded", stage: null, progress: { ...job.progress, fraction: null, stageElapsedSec: 0 } };
  }
  const next = NEXT[job.state];
  if (!next) return job;
  if (next === "running") return enterStage({ ...job, state: "running" }, "asr");
  return { ...job, state: next, stage: null };
}

/** Demo control: +25 % within the current stage (only stages that report progress). */
export function advanceProgress(job: DemoJob): DemoJob {
  const f = job.progress.fraction;
  if (job.state !== "running" || f === null || f >= 1) return job;
  return {
    ...job,
    progress: {
      fraction: Math.min(1, Math.round((f + 0.25) * 100) / 100),
      stageElapsedSec: job.progress.stageElapsedSec + ADVANCE_SEC,
      elapsedSec: job.progress.elapsedSec + ADVANCE_SEC,
    },
  };
}

export type EtaView = { kind: "none" } | { kind: "estimating" } | { kind: "estimate"; remainingSec: number };

/**
 * Remaining time of the CURRENT stage, extrapolated from the stage's own elapsed time and fraction.
 * Below 10 % (or without elapsed time) the estimate is too noisy: "estimating". A stage without
 * progress has no estimate at all. Real ETAs need smoothing and may go up again (non-monotonic).
 */
export function stageEta(job: Pick<DemoJob, "state" | "progress">): EtaView {
  const { fraction, stageElapsedSec } = job.progress;
  if (job.state !== "running" || fraction === null) return { kind: "none" };
  if (fraction < 0.1 || stageElapsedSec <= 0) return { kind: "estimating" };
  if (fraction >= 1) return { kind: "estimate", remainingSec: 0 };
  return { kind: "estimate", remainingSec: Math.round((stageElapsedSec * (1 - fraction)) / fraction) };
}

/** Stage position "n of m" for a job (diarization off → two stages). */
export function stagePosition(job: Pick<DemoJob, "stage" | "snapshot">): { index: number; total: number } | null {
  if (!job.stage) return null;
  const stages: JobStage[] = job.snapshot.diarization ? ["asr", "diarization", "output"] : ["asr", "output"];
  return { index: stages.indexOf(job.stage) + 1, total: stages.length };
}

export function isActive(job: DemoJob): boolean {
  return ACTIVE_STATES.includes(job.state);
}

/** Inputs that already have a job (in any state). */
export function submittedInputs(state: Pick<DemoState, "jobs">): Set<string> {
  return new Set(state.jobs.map((j) => j.inputId));
}

/** Latest job of one input (an input has at most one job; retry reuses it). */
export function jobOf(state: Pick<DemoState, "jobs">, inputId: string): DemoJob | null {
  const jobs = state.jobs.filter((j) => j.inputId === inputId);
  return jobs[jobs.length - 1] ?? null;
}

/**
 * The one input Normal mode shows. It never changes the list: with several inputs (made in Expert)
 * Normal shows a representative and only points to Expert for the rest.
 * Order: the last chosen/touched input → the first selected → the first input.
 */
export function normalInputId(state: Pick<DemoState, "inputs" | "selected" | "focusInputId">): string | null {
  const ids = new Set(state.inputs.map((i) => i.id));
  if (state.focusInputId && ids.has(state.focusInputId)) return state.focusInputId;
  const selected = state.selected.find((id) => ids.has(id));
  return selected ?? state.inputs[0]?.id ?? null;
}

export type SubmitBlock = "noSelection" | "allSubmitted" | "unsupported" | "invalid" | "diarization" | "area";
type SubmitState = Pick<DemoState, "inputs" | "selected" | "jobs" | "inputsArea" | "common" | "overrides" | "diarizationReady">;
export type SkipReason = "unsupported" | "alreadySubmitted" | "invalid" | "diarization";

/** Why one input cannot be submitted now (null = it can). */
export function skipReason(state: Omit<SubmitState, "selected" | "inputsArea">, input: DemoInput): SkipReason | null {
  if (!input.supported) return "unsupported";
  if (submittedInputs(state).has(input.id)) return "alreadySubmitted";
  if (inputIssues(state, input.id).length > 0) return "invalid";
  if (needsDiarization(state, input.id)) return "diarization";
  return null;
}

/** Why the selection cannot be submitted (null = at least one selected input can be). */
export function submitBlock(state: SubmitState): SubmitBlock | null {
  if (state.inputsArea !== "ready") return "area";
  const selected = state.inputs.filter((i) => state.selected.includes(i.id));
  if (selected.length === 0) return "noSelection";
  const reasons = selected.map((i) => skipReason(state, i));
  if (reasons.some((r) => r === null)) return null;
  for (const r of ["invalid", "diarization", "allSubmitted", "unsupported"] as const) {
    if (reasons.includes(r === "allSubmitted" ? "alreadySubmitted" : r)) return r;
  }
  return "unsupported";
}

const MINI_ORDER: Record<JobState, number> = { running: 1, preparing: 2, failed: 3, cancelled: 4, waiting: 5, succeeded: 6 };

/**
 * The Expert work area's short queue (at most `limit` rows): the jobs just submitted first, then
 * running, needing attention, waiting and finished ones. The full list is the Queue page.
 */
export function miniQueue(state: Pick<DemoState, "jobs" | "lastSubmit">, limit = 5): DemoJob[] {
  const recent = new Set(state.lastSubmit?.jobIds ?? []);
  const rank = (j: DemoJob) => (recent.has(j.id) ? 0 : MINI_ORDER[j.state]);
  return state.jobs
    .map((job, i) => ({ job, i }))
    .sort((a, b) => rank(a.job) - rank(b.job) || (a.job.state === "succeeded" ? b.i - a.i : a.i - b.i))
    .slice(0, limit)
    .map((r) => r.job);
}

const STATE_ORDER: Record<JobState, number> = { failed: 0, running: 1, preparing: 2, waiting: 3, cancelled: 4, succeeded: 5 };

/** Queue page view: filter, search and sort over the synthetic jobs (the queue order is kept). */
export function queueView(state: Pick<DemoState, "jobs" | "queueQuery" | "queueFilter" | "queueSort">): { job: DemoJob; position: number }[] {
  const query = state.queueQuery.trim().toLowerCase();
  const rows = state.jobs
    .map((job, i) => ({ job, position: i + 1 }))
    .filter(({ job }) => {
      if (query && !job.inputName.toLowerCase().includes(query)) return false;
      if (state.queueFilter === "active") return isActive(job);
      if (state.queueFilter === "attention") return job.state === "failed" || job.state === "cancelled";
      if (state.queueFilter === "finished") return !isActive(job);
      return true;
    });
  if (state.queueSort === "name") rows.sort((a, b) => a.job.inputName.localeCompare(b.job.inputName));
  if (state.queueSort === "state") rows.sort((a, b) => STATE_ORDER[a.job.state] - STATE_ORDER[b.job.state] || a.position - b.position);
  return rows;
}

/** Results page view: succeeded jobs only, searched by name. */
export function resultsView(state: Pick<DemoState, "jobs" | "resultsQuery">): DemoJob[] {
  const query = state.resultsQuery.trim().toLowerCase();
  return state.jobs.filter((j) => j.state === "succeeded" && (!query || j.inputName.toLowerCase().includes(query)));
}

/** Example output file names of a finished job (nothing is written). */
export function outputNames(job: Pick<DemoJob, "inputName" | "snapshot">): string[] {
  const stem = job.inputName.replace(/\.[^.]+$/, "");
  return job.snapshot.formats.map((f) => `${stem}.${f}`);
}

/** Default: one file, nothing submitted (Normal is the default mode and shows one file). */
function initialState(): DemoState {
  return {
    inputs: initialInputs().slice(0, 1),
    poolCursor: 0,
    selected: ["in-01"],
    focusInputId: "in-01",
    common: cloneSettings(DEFAULT_COMMON),
    overrides: {},
    inspectorTab: "common",
    inspectorCategory: "asr",
    userPresets: initialUserPresets(),
    presetSeq: 1,
    jobs: [],
    jobSeq: 0,
    selectedJobId: null,
    lastSubmit: null,
    queuePaused: false,
    scenario: "single",
    inputsArea: "ready",
    queueArea: "ready",
    sync: "ok",
    envExample: "gpu",
    envChecking: false,
    diarizationReady: true,
    queueQuery: "",
    queueFilter: "all",
    queueSort: "order",
    resultsQuery: "",
    selectedResultId: null,
    language: "ja",
    appearance: "light",
    accentTheme: "green",
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

  /** Reviewer preferences that survive "reset" and scenario changes. */
  private kept(): Pick<DemoState, "language" | "appearance" | "accentTheme" | "envExample" | "diarizationReady"> {
    const { language, appearance, accentTheme, envExample, diarizationReady } = this.store.get();
    return { language, appearance, accentTheme, envExample, diarizationReady };
  }

  /** Back to the deterministic initial state; language, theme and the environment are kept. */
  reset(): void {
    this.store.set({ ...initialState(), ...this.kept(), notice: this.notify("notice.reset") });
  }

  /** Public so that the setup controller can report its own outcomes in the same snackbar. */
  say(key: string, params: Record<string, string | number> = {}): void {
    this.update(() => ({}), key, params);
  }

  setAccentTheme(accentTheme: AccentTheme): void {
    this.update(() => ({ accentTheme }), "notice.accentTheme");
  }

  setLanguage(language: PreviewLanguage): void {
    this.update(() => ({ language }));
  }

  setAppearance(appearance: Appearance): void {
    this.update(() => ({ appearance }), "notice.appearance");
  }

  // ---- inputs -------------------------------------------------------------------------------
  addFiles(count: number): void {
    this.update((s) => {
      const added = Array.from({ length: count }, (_, i) => poolInput(s.poolCursor + i));
      return { inputs: [...s.inputs, ...added], poolCursor: s.poolCursor + count, inputsArea: "ready" };
    }, "notice.added", { count });
  }

  toggleSelect(id: string): void {
    this.update((s) => ({ selected: s.selected.includes(id) ? s.selected.filter((x) => x !== id) : [...s.selected, id], focusInputId: id, lastSubmit: null }));
  }

  /** Explorer-style plain click: select only this row. */
  selectOnly(id: string): void {
    this.update(() => ({ selected: [id], focusInputId: id, lastSubmit: null }));
  }

  /**
   * Normal mode "choose a file" (demo: the next synthetic name, never a real picker). The new file
   * becomes the one Normal shows, so it LOOKS like a replacement. The old input is dropped only when
   * that loses nothing: it is the only input, never submitted and has no individual settings.
   * Otherwise it stays (Expert still lists it, with its jobs and settings) and is just not shown.
   */
  /**
   * Normal "choose a file" (demo: the next synthetic name, never a real picker). The new file
   * becomes the one Normal shows and CARRIES the settings of the file shown before (iteration 4:
   * Normal edits belong to the shown file, so "the next file" keeps what the user just chose). The
   * old input is dropped only when that loses nothing: it is the only input and never submitted
   * (its settings move to the new file). Otherwise it stays (Expert still lists it) and is not shown.
   */
  chooseNormalFile(): void {
    this.update((s) => {
      const added = poolInput(s.poolCursor);
      const current = normalInputId(s);
      const carried = current ? s.overrides[current] : undefined;
      const replace = s.inputs.length === 1 && current !== null && !submittedInputs(s).has(current);
      const inputs = replace ? [added] : [...s.inputs, added];
      const selected = replace || s.selected.length === 0 || (s.selected.length === 1 && s.selected[0] === current) ? [added.id] : s.selected;
      const overrides = { ...s.overrides };
      if (replace && current) delete overrides[current];
      if (carried && Object.keys(carried).length > 0) overrides[added.id] = { ...carried, ...(carried.formats ? { formats: [...carried.formats] } : {}), ...(carried.glossary ? { glossary: [...carried.glossary] } : {}) };
      return { inputs, selected, overrides, focusInputId: added.id, poolCursor: s.poolCursor + 1, inputsArea: "ready" };
    }, "notice.chosen", { name: poolInput(this.store.get().poolCursor).name });
  }

  /**
   * Normal settings edit ONLY the shown file (iteration 4, owner concern on iteration 3): the change
   * becomes that file's individual setting and never the common defaults, so files prepared in
   * Expert are not changed behind the user's back. With no file at all, the common defaults change
   * (there is nothing else they could affect).
   */
  setNormalSetting(inputId: string | null, patch: Partial<ProcessingSettings>): void {
    if (!inputId || !this.store.get().inputs.some((i) => i.id === inputId)) {
      this.setCommonPatch(patch);
      return;
    }
    this.update((cur) => ({ overrides: { ...cur.overrides, [inputId]: withOverride(cur, inputId, patch) } }), "notice.settingChanged");
  }

  selectAll(): void {
    this.update((s) => ({ selected: s.inputs.map((i) => i.id), lastSubmit: null }));
  }

  clearSelection(): void {
    this.update(() => ({ selected: [], lastSubmit: null }));
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

  setInspectorCategory(inspectorCategory: SettingsCategoryId): void {
    this.update(() => ({ inspectorCategory }));
  }

  setCommon<K extends SettingKey>(key: K, value: ProcessingSettings[K]): void {
    this.setCommonPatch({ [key]: value } as Partial<ProcessingSettings>);
  }

  /** Common defaults; a backend change keeps model / device / compute type valid for it. */
  setCommonPatch(patch: Partial<ProcessingSettings>): void {
    this.update((s) => {
      let common: ProcessingSettings = cloneSettings({ ...s.common, ...patch });
      if (Object.keys(patch).some((k) => (GROUPS.engine as readonly string[]).includes(k))) common = normalizeEngine(common);
      return { common };
    }, "notice.commonChanged");
  }

  resetCommon(): void {
    this.update(() => ({ common: cloneSettings(DEFAULT_COMMON) }), "notice.commonReset");
  }

  /** Sets an override on every selected input (the field becomes "individual" for all of them). */
  setOverride<K extends SettingKey>(key: K, value: ProcessingSettings[K]): void {
    this.setOverridePatch({ [key]: value } as Partial<ProcessingSettings>);
  }

  setOverridePatch(patch: Partial<ProcessingSettings>): void {
    const count = this.store.get().selected.length;
    if (count === 0) return;
    this.update((s) => {
      const overrides = { ...s.overrides };
      for (const id of s.selected) overrides[id] = withOverride(s, id, patch);
      return { overrides };
    }, "notice.overrideSet", { count });
  }

  /**
   * Turns an override on with EACH input's own effective value (iteration 4: a multi-selection is
   * never overwritten with the first file's value just by switching "individual" on).
   */
  startOverride(key: SettingKey): void {
    const s = this.store.get();
    if (s.selected.length === 0) return;
    this.update((cur) => {
      const overrides = { ...cur.overrides };
      for (const id of cur.selected) {
        const eff = effectiveSettings(cur, id);
        const o: Overrides = { ...overrides[id] };
        for (const k of groupOf(key)) (o as Record<string, unknown>)[k] = cloneValue(eff[k]);
        overrides[id] = o;
      }
      return { overrides };
    }, "notice.overrideSet", { count: s.selected.length });
  }

  /** "Back to common" for one field (its whole linked group) of the selected inputs. */
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
        for (const k of groupOf(key)) delete rest[k];
        if (Object.keys(rest).length > 0) overrides[id] = rest;
      }
      return { overrides };
    }, "notice.overrideReset", { count });
  }

  resetAllOverrides(): void {
    const count = this.store.get().selected.length;
    if (count === 0) return;
    this.update((s) => ({ overrides: Object.fromEntries(Object.entries(s.overrides).filter(([id]) => !s.selected.includes(id))) }), "notice.overrideReset", { count });
  }

  // ---- presets (in memory; no file, no B5 CRUD) -----------------------------------------------
  /**
   * Applies a preset to the common defaults or, as individual settings, to the selected inputs.
   * Exactly the preset's keys change; everything else is kept.
   */
  applyPreset(presetId: string, target: InspectorTab): void {
    const preset = allPresets(this.store.get()).find((p) => p.id === presetId);
    if (!preset) return;
    const patch = Object.fromEntries(presetKeys(preset).map((k) => [k, cloneValue(preset.values[k])])) as Partial<ProcessingSettings>;
    if (target === "common") this.setCommonPatch(patch);
    else this.setOverridePatch(patch);
    this.update(() => ({}), "notice.presetApplied");
  }

  private addUserPreset(name: string, values: Partial<ProcessingSettings>): string {
    const s = this.store.get();
    const id = `user-${String(s.presetSeq + 1).padStart(2, "0")}`;
    const taken = s.userPresets.map((p) => p.name);
    const copy = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, cloneValue(v)])) as Partial<ProcessingSettings>;
    this.update((cur) => ({ userPresets: [...cur.userPresets, { id, name: uniqueName(name, taken), nameKey: null, builtIn: false, values: copy }], presetSeq: cur.presetSeq + 1 }));
    return id;
  }

  /** New preset from the current common defaults (prompt and glossary only if not empty). */
  createPresetFromCommon(name: string): string {
    const { common } = this.store.get();
    const values: Partial<ProcessingSettings> = { ...cloneSettings(common) };
    delete values.device; // PC-specific
    if (!common.initialPrompt) delete values.initialPrompt;
    if (common.glossary.length === 0) delete values.glossary;
    const id = this.addUserPreset(name, values);
    this.update(() => ({}), "notice.presetCreated");
    return id;
  }

  duplicatePreset(id: string, name: string): string | null {
    const preset = allPresets(this.store.get()).find((p) => p.id === id);
    if (!preset) return null;
    const created = this.addUserPreset(name, preset.values);
    this.update(() => ({}), "notice.presetCreated");
    return created;
  }

  renamePreset(id: string, name: string): void {
    const trimmed = name.trim();
    if (!trimmed) return;
    this.update((s) => ({ userPresets: s.userPresets.map((p) => (p.id === id && !p.builtIn ? { ...p, name: trimmed } : p)) }), "notice.presetSaved");
  }

  /** Replaces a user preset's content with the current common defaults (built-ins are never changed). */
  replacePresetWithCommon(id: string): void {
    const { common } = this.store.get();
    this.update((s) => ({
      userPresets: s.userPresets.map((p) => {
        if (p.id !== id) return p;
        const values = Object.fromEntries(Object.keys(p.values).filter((k) => k !== "device").map((k) => [k, cloneValue(common[k as SettingKey])])) as Partial<ProcessingSettings>;
        return { ...p, values };
      }),
    }), "notice.presetSaved");
  }

  deletePreset(id: string): void {
    this.update((s) => ({ userPresets: s.userPresets.filter((p) => p.id !== id || p.builtIn) }), "notice.presetDeleted");
  }

  importPreset(name: string, values: Partial<ProcessingSettings>): string {
    const id = this.addUserPreset(name, values);
    this.update(() => ({}), "notice.presetImported");
    return id;
  }

  // ---- jobs (synthetic only; nothing is created anywhere but in this store) -------------------
  /**
   * Creates demo jobs for the given inputs that are supported and were never submitted. A failed or
   * cancelled job is resumed with "retry"; re-running a finished input (B7 `rerun`) is an S3 design
   * item, so it is not offered here. The outcome (created / skipped and why) is kept for the inline
   * feedback under the submit button. Returns how many jobs were created.
   */
  submit(inputIds: string[]): number {
    const s = this.store.get();
    const asked = s.inputs.filter((i) => inputIds.includes(i.id));
    const reasons = asked.map((i) => ({ input: i, reason: skipReason(s, i) }));
    const targets = reasons.filter((r) => r.reason === null).map((r) => r.input);
    const count = (reason: SkipReason) => reasons.filter((r) => r.reason === reason).length;
    const skipped = { unsupported: count("unsupported"), alreadySubmitted: count("alreadySubmitted"), invalid: count("invalid"), diarization: count("diarization") };
    if (targets.length === 0) {
      this.update(() => ({ lastSubmit: { created: 0, ...skipped, jobIds: [] } }), "notice.nothingToSubmit");
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
          progress: { ...NO_PROGRESS },
          attempt: 1,
          snapshot: effectiveSettings(cur, i.id),
          errorCode: null,
        };
      });
      return {
        jobs: [...cur.jobs, ...created],
        jobSeq: seq,
        selectedJobId: created[0]?.id ?? cur.selectedJobId,
        queueArea: "ready",
        lastSubmit: { created: created.length, ...skipped, jobIds: created.map((j) => j.id) },
      };
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

  /** Demo control: +25 % in the current stage of a running job (fictional progress). */
  advance(id: string): void {
    this.mapJob(id, advanceProgress, "notice.advanced");
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
      (j) =>
        j.state === "failed" || j.state === "cancelled" ? { ...j, state: "waiting", stage: null, progress: { ...NO_PROGRESS }, errorCode: null, attempt: j.attempt + 1 } : j,
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

  /** "Retry" of the job-status fetch: in the demo it simply succeeds (no request is made). */
  retrySync(): void {
    this.update(() => ({ sync: "ok" }), "notice.syncRecovered");
  }

  /** "Retry" of a demo error/loading area: it simply returns to ready (no request is made). */
  recoverArea(area: "inputs" | "queue"): void {
    this.update(() => (area === "inputs" ? { inputsArea: "ready" } : { queueArea: "ready" }), "notice.recovered");
  }

  setScenario(scenario: Scenario): void {
    const base: DemoState = { ...initialState(), ...this.kept(), scenario };
    const multi: Partial<DemoState> = { inputs: initialInputs(), overrides: { "in-02": { language: "en", speakerMode: "fixed", numSpeakers: 2, minSpeakers: 1, maxSpeakers: 4 } } };
    let next: DemoState = base;
    if (scenario === "empty") next = { ...base, inputs: [], selected: [], focusInputId: null };
    if (scenario === "multi" || scenario === "busy") next = { ...base, ...multi };
    if (scenario === "loading") next = { ...base, inputsArea: "loading", queueArea: "loading" };
    if (scenario === "error") next = { ...base, inputsArea: "error", queueArea: "error" };
    this.store.set({ ...next, notice: this.notify("notice.scenario", { scenario }) });
    if (scenario === "busy") this.fillQueue({ "in-01": 3, "in-02": 1, "in-03": 2, "in-04": 0 }, "in-03");
    if (scenario === "running") this.fillQueue({ "in-01": 3 });
    if (scenario === "failed") this.fillQueue({ "in-01": 2 }, "in-01");
    if (scenario === "done") this.fillQueue({ "in-01": 9 });
  }

  // ---- fictional environment ------------------------------------------------------------------
  setEnvExample(envExample: EnvExample): void {
    this.update(() => ({ envExample, envChecking: false }));
  }

  /** Used by the setup wizard and its examples (never by detection: nothing is detected). */
  setEnvironment(envExample: EnvExample, diarizationReady: boolean): void {
    this.update(() => ({ envExample, diarizationReady, envChecking: false }));
  }

  /** Demo "re-detect": only shows the checking state; nothing is detected (no request, no timer). */
  redetect(): void {
    this.update(() => ({ envChecking: true }), "notice.redetect");
  }

  /** Demo control that ends the checking display with the same fictional example. */
  finishDetect(): void {
    this.update(() => ({ envChecking: false }), "notice.detected");
  }

  // ---- queue / results page list controls -------------------------------------------------------
  setQueueQuery(queueQuery: string): void {
    this.update(() => ({ queueQuery }));
  }

  setQueueFilter(queueFilter: QueueFilter): void {
    this.update(() => ({ queueFilter }));
  }

  setQueueSort(queueSort: QueueSort): void {
    this.update(() => ({ queueSort }));
  }

  setResultsQuery(resultsQuery: string): void {
    this.update(() => ({ resultsQuery }));
  }

  selectResult(id: string | null): void {
    this.update(() => ({ selectedResultId: id }));
  }

  private fillQueue(steps: Record<string, number>, failId?: string): void {
    const ids = this.store.get().inputs.filter((i) => i.supported && i.id in steps).map((i) => i.id);
    const before = this.store.get().notice;
    this.submit(ids);
    this.store.set((s) => ({
      ...s,
      notice: before,
      lastSubmit: null,
      jobs: s.jobs.map((j) => {
        let job = j;
        for (let i = 0; i < (steps[j.inputId] ?? 0); i += 1) job = stepJob(job);
        // A running stage starts halfway, so the fictional progress and estimate are visible at once.
        if (job.state === "running" && job.progress.fraction !== null) job = advanceProgress(advanceProgress(job));
        if (j.inputId === failId) job = { ...job, state: "failed", errorCode: "demo_failure" };
        return job;
      }),
    }));
  }
}
