import type { JobLanguage, JobOptions, OutputFormat } from "../api/legacyEnvironment";
import { type DroppedPath, type HostBridge, HostUnavailableError } from "../host";
import type { AdmissionGate, AdmissionRecord } from "./admission";
import type { EnvironmentState } from "./environment";
import { checkPath, type PathProblem, samePath } from "./paths";
import { Store, type ReadonlyStore } from "./store";
import { type CommonDraft, evaluate, type FormEvaluation, type FormState, type PickError, type TranscriptionDraft } from "./transcription";

/**
 * The shared input list (UI-3 S3-B1): one controller for Normal and Expert, created once by
 * `AppCore`. Normal mode works on the *primary* row; Expert works on the *selection*.
 *
 * - Rows are identified by a page-local `clientFileId` (never the path). The path is kept exactly
 *   as given (no trimming, joining or normalisation) and lives in memory only.
 * - Settings are the common draft (`TranscriptionFormController`) plus per-row overrides of only
 *   the changed leaves; the output folder is a separate per-row choice (inherit / custom), like
 *   B5 `FileInput.output_dir` vs `SettingOverride`.
 * - A row's submission state is *derived* from the shared `AdmissionGate` ledger (never stored
 *   twice). Rows that are sending, accepted or unknown are locked: they cannot be removed or
 *   edited, so hiding a row can never re-open a guarded input.
 * - The duplicate check is a client aid (Windows-like case and separators are folded); symlinks,
 *   8.3 names and the same file in another window are not detected.
 */

/** Inputs per workspace. Below B5 `FileInput`'s 1000; keeps list rendering and a sequential batch bounded. */
export const MAX_INPUTS = 100;

export type InputSource = "picker" | "drop" | "typed";
export type OutputChoice = { kind: "inherit" } | { kind: "custom"; path: string };

/** Only the leaves that differ from the common settings. Absent = follow the common value. */
export interface SettingOverrides {
  asrBackend?: string;
  asrModel?: string;
  asrDevice?: string;
  language?: JobLanguage;
  formats?: readonly OutputFormat[];
}
export type OverrideField = keyof SettingOverrides;
export const OVERRIDE_FIELDS: readonly OverrideField[] = ["asrBackend", "asrModel", "asrDevice", "language", "formats"];

export interface InputRow {
  readonly clientFileId: string;
  readonly inputPath: string;
  readonly source: InputSource;
  readonly selected: boolean;
  readonly overrides: Readonly<SettingOverrides>;
  readonly output: OutputChoice;
  /** Gate attempts below this seq are ignored (an explicit reset after a refusal). */
  readonly attemptFloor: number;
}

export type RowStatus = "not_submitted" | "pending" | "accepted" | "rejected" | "unknown";

export type ListNotice =
  | { kind: "added"; count: number }
  | { kind: "duplicate" }
  | { kind: "invalid"; problem: PathProblem }
  | { kind: "limit"; max: number; requested: number; room: number }
  | { kind: "drop"; added: number; duplicates: number; folders: number; unknown: number; invalid: number }
  | { kind: "removed"; removed: number; kept: number };

/** A pending bulk edit for the selection; nothing changes until `applyStaged()`. */
export interface StagedEdit {
  asrBackend?: string;
  asrModel?: string;
  asrDevice?: string;
  language?: JobLanguage;
  formats?: readonly OutputFormat[];
  output?: OutputChoice;
}

export interface CollectionState {
  rows: readonly InputRow[];
  /** The row Normal mode shows and starts (null = none). */
  primaryId: string | null;
  /** The typed-path entry (kept across navigation, memory only). */
  typed: string;
  notice: ListNotice | null;
  picking: boolean;
  pickError: PickError | null;
  staged: StagedEdit;
}

export function rowStatus(record: AdmissionRecord | null): RowStatus {
  return record === null ? "not_submitted" : record.phase;
}

/** Sending, accepted or unknown: never removed, edited or re-sent from this list. */
export function isLocked(status: RowStatus): boolean {
  return status === "pending" || status === "accepted" || status === "unknown";
}

/**
 * Common settings + this row's overrides + its output. A backend override detaches the ASR
 * group: an inherited model/device is used only while the row's backend equals the common one,
 * otherwise a missing model/device stays missing (an explicit error, never a guess).
 */
export function effectiveDraft(row: InputRow, common: CommonDraft): TranscriptionDraft {
  const o = row.overrides;
  const asrBackend = o.asrBackend ?? common.asrBackend;
  const sameBackend = asrBackend === common.asrBackend;
  return {
    ...common,
    inputPath: row.inputPath,
    inputSource: row.source,
    outputDir: row.output.kind === "custom" ? row.output.path : common.outputDir,
    asrBackend,
    asrModel: o.asrModel ?? (sameBackend ? common.asrModel : null),
    asrDevice: o.asrDevice ?? (sameBackend ? common.asrDevice : null),
    language: o.language ?? common.language,
    formats: o.formats ? [...o.formats] : [...common.formats],
  };
}

export interface RowEvaluation extends FormEvaluation {
  /** The input was accepted or may have been (page-lifetime guard). */
  sameInput: boolean;
}

export function evaluateRow(row: InputRow, common: CommonDraft, env: EnvironmentState, guarded: (path: string) => boolean): RowEvaluation {
  const result = evaluate(effectiveDraft(row, common), env);
  const sameInput = guarded(row.inputPath);
  return { ...result, sameInput, request: sameInput ? null : result.request };
}

export type FieldValue = string | readonly OutputFormat[] | null;

export interface FieldSummary {
  /** Rows summarised (the editable part of the selection). */
  count: number;
  mixed: boolean;
  /** The shared effective value when not mixed. */
  value: FieldValue;
  /** How many of them override this field (or use a custom output). */
  overridden: number;
}

const sameValue = (a: FieldValue, b: FieldValue) =>
  Array.isArray(a) || Array.isArray(b) ? [...((a as OutputFormat[]) ?? [])].sort().join() === [...((b as OutputFormat[]) ?? [])].sort().join() : a === b;

/** Effective value of one field over several rows; a mixed selection is reported as mixed. */
export function summarize(rows: readonly InputRow[], common: CommonDraft, field: OverrideField | "output"): FieldSummary {
  let value: FieldValue = null;
  let mixed = false;
  let overridden = 0;
  rows.forEach((row, index) => {
    const draft = effectiveDraft(row, common);
    const v: FieldValue = field === "output" ? draft.outputDir : field === "formats" ? draft.formats : draft[field];
    if (field === "output" ? row.output.kind === "custom" : row.overrides[field] !== undefined) overridden += 1;
    if (index === 0) value = v;
    else if (!sameValue(value, v)) mixed = true;
  });
  return { count: rows.length, mixed, value: mixed ? null : value, overridden };
}

/** Overrides after setting the backend: model/device belonged to the old backend and are cleared; single choices are filled. */
function withBackend(overrides: SettingOverrides, backend: string, options: JobOptions | null): SettingOverrides {
  const next: SettingOverrides = { ...overrides, asrBackend: backend };
  delete next.asrModel;
  delete next.asrDevice;
  const choice = options?.asr.find((b) => b.id === backend);
  if (choice?.models.length === 1) next.asrModel = choice.models[0]!.id;
  if (choice?.devices.length === 1) next.asrDevice = choice.devices[0]!.id;
  return next;
}

let rowCounter = 0;
function newRowId(): string {
  rowCounter += 1;
  return `in-${Date.now().toString(36)}-${rowCounter.toString(36)}`;
}

export class InputCollectionController {
  readonly store = new Store<CollectionState>({ rows: [], primaryId: null, typed: "", notice: null, picking: false, pickError: null, staged: {} });

  constructor(
    private readonly gate: AdmissionGate,
    private readonly form: ReadonlyStore<FormState>,
    private readonly environment: ReadonlyStore<EnvironmentState>,
  ) {}

  // ---- reading -------------------------------------------------------------------------------

  row(id: string | null): InputRow | null {
    return id === null ? null : (this.store.get().rows.find((r) => r.clientFileId === id) ?? null);
  }

  primary(): InputRow | null {
    return this.row(this.store.get().primaryId);
  }

  statusOf(row: InputRow): RowStatus {
    return rowStatus(this.gate.latestFor(row.clientFileId, row.attemptFloor));
  }

  recordOf(row: InputRow): AdmissionRecord | null {
    return this.gate.latestFor(row.clientFileId, row.attemptFloor);
  }

  evaluateRow(row: InputRow): RowEvaluation {
    return evaluateRow(row, this.form.get().draft, this.environment.get(), (p) => this.gate.isGuarded(p));
  }

  selected(): InputRow[] {
    return this.store.get().rows.filter((r) => r.selected);
  }

  /** The part of the selection a bulk edit changes (locked rows keep their sent values). */
  editableSelection(): InputRow[] {
    return this.selected().filter((r) => !isLocked(this.statusOf(r)));
  }

  // ---- adding --------------------------------------------------------------------------------

  setTyped(value: string): void {
    this.store.patch({ typed: value });
  }

  /** Add the typed path as a new row (the field is cleared only when it was added). */
  addTyped(): boolean {
    const typed = this.store.get().typed;
    const added = this.add([{ path: typed, source: "typed" }]);
    if (added) this.store.patch({ typed: "" });
    return added;
  }

  /** Native dialog: one file per call (HostBridge), always *added* to the list. */
  async pick(host: HostBridge): Promise<void> {
    if (this.store.get().picking) return;
    this.store.patch({ picking: true, pickError: null });
    try {
      const path = await host.pickPath("input_file");
      this.store.patch({ picking: false });
      if (path === null) return; // cancelled: nothing changes
      this.add([{ path, source: "picker" }]);
    } catch (error) {
      this.store.patch({ picking: false, pickError: error instanceof HostUnavailableError ? "host_unavailable" : "failed" });
    }
  }

  /**
   * A native drop as one operation. Files are added; folders and unknown items are refused and
   * counted (never treated as files). If the files would exceed `MAX_INPUTS`, nothing is added.
   */
  acceptDrop(paths: DroppedPath[]): void {
    if (paths.length === 0) return;
    const files = paths.filter((p) => p.kind === "file" && p.path !== "");
    const folders = paths.filter((p) => p.kind === "folder").length;
    const unknown = paths.length - files.length - folders;
    const plan = this.plan(files.map((f) => ({ path: f.path, source: "drop" as const })));
    if (plan.kind === "limit") return this.store.patch({ notice: plan.notice });
    this.commit(plan.rows);
    const notice: ListNotice =
      folders === 0 && unknown === 0 && plan.duplicates === 0 && plan.invalid === 0
        ? { kind: "added", count: plan.rows.length }
        : { kind: "drop", added: plan.rows.length, duplicates: plan.duplicates, folders, unknown, invalid: plan.invalid };
    this.store.patch({ notice });
  }

  // ---- selection -----------------------------------------------------------------------------

  setPrimary(id: string): void {
    if (this.row(id)) this.store.patch({ primaryId: id });
  }

  toggle(id: string, on: boolean): void {
    this.setSelection((r) => (r.clientFileId === id ? on : r.selected));
  }

  selectAll(): void {
    this.setSelection(() => true);
  }

  clearSelection(): void {
    this.setSelection(() => false);
  }

  /**
   * Remove rows from this list (display only: no server job, output or history is touched).
   * Sending, accepted and unknown rows are kept and counted.
   */
  remove(ids: readonly string[]): void {
    const { rows, primaryId } = this.store.get();
    const target = new Set(ids);
    let removed = 0;
    let kept = 0;
    const next = rows.filter((r) => {
      if (!target.has(r.clientFileId)) return true;
      if (isLocked(this.statusOf(r))) {
        kept += 1;
        return true;
      }
      removed += 1;
      return false;
    });
    const primary = next.some((r) => r.clientFileId === primaryId) ? primaryId : (next[0]?.clientFileId ?? null);
    this.store.patch({ rows: next, primaryId: primary, notice: { kind: "removed", removed, kept }, staged: {} });
  }

  removeSelected(): void {
    this.remove(this.selected().map((r) => r.clientFileId));
  }

  /** After a refusal (nothing was created), make the row "not submitted" again — explicitly. */
  resetRefusal(id: string): void {
    const row = this.row(id);
    if (!row || this.statusOf(row) !== "rejected") return;
    this.updateRows([id], (r) => ({ ...r, attemptFloor: this.gate.nextSeq() }));
  }

  // ---- per-row settings ----------------------------------------------------------------------

  /** Stage one field for the selection (applied only by `applyStaged`). A backend clears the staged model/device. */
  stage(patch: StagedEdit): void {
    const staged = { ...this.store.get().staged, ...patch };
    if (patch.asrBackend !== undefined) {
      if (patch.asrModel === undefined) delete staged.asrModel;
      if (patch.asrDevice === undefined) delete staged.asrDevice;
    }
    this.store.patch({ staged });
  }

  /** Native folder dialog for the selection's output; the choice is only staged. */
  async pickOutput(host: HostBridge): Promise<void> {
    if (this.store.get().picking) return;
    this.store.patch({ picking: true, pickError: null });
    try {
      const path = await host.pickPath("output_folder");
      this.store.patch({ picking: false });
      if (path !== null) this.stage({ output: { kind: "custom", path } });
    } catch (error) {
      this.store.patch({ picking: false, pickError: error instanceof HostUnavailableError ? "host_unavailable" : "failed" });
    }
  }

  discardStaged(): void {
    this.store.patch({ staged: {} });
  }

  /** Staged values that cannot be applied: an unusable custom folder, or no format at all. */
  stagedProblems(): { output: PathProblem | null; formats: boolean } {
    const staged = this.store.get().staged;
    return {
      output: staged.output?.kind === "custom" ? checkPath(staged.output.path, "output_folder") : null,
      formats: staged.formats !== undefined && staged.formats.length === 0,
    };
  }

  /** Apply the staged edit to the editable selection; returns how many rows changed (0 = refused). */
  applyStaged(): number {
    const staged = this.store.get().staged;
    const ids = this.editableSelection().map((r) => r.clientFileId);
    if (ids.length === 0 || Object.keys(staged).length === 0) return 0;
    const problems = this.stagedProblems();
    if (problems.output !== null || problems.formats) return 0;
    const options = this.currentOptions();
    this.updateRows(ids, (row) => {
      let overrides: SettingOverrides = { ...row.overrides };
      if (staged.asrBackend !== undefined) overrides = withBackend(overrides, staged.asrBackend, options);
      if (staged.asrModel !== undefined) overrides.asrModel = staged.asrModel;
      if (staged.asrDevice !== undefined) overrides.asrDevice = staged.asrDevice;
      if (staged.language !== undefined) overrides.language = staged.language;
      if (staged.formats !== undefined) overrides.formats = Object.freeze([...staged.formats]);
      return { ...row, overrides, output: staged.output ?? row.output };
    });
    this.store.patch({ staged: {} });
    return ids.length;
  }

  /** Follow the common value again for these fields (the selection's editable rows). */
  resetToCommon(fields: readonly (OverrideField | "output")[]): void {
    const ids = this.editableSelection().map((r) => r.clientFileId);
    this.updateRows(ids, (row) => {
      const overrides: SettingOverrides = { ...row.overrides };
      for (const field of fields) {
        if (field === "output") continue;
        delete overrides[field];
        // The model/device of an overridden backend have no meaning without it.
        if (field === "asrBackend") {
          delete overrides.asrModel;
          delete overrides.asrDevice;
        }
      }
      return { ...row, overrides, output: fields.includes("output") ? { kind: "inherit" } : row.output };
    });
  }

  // ---- internals -----------------------------------------------------------------------------

  private currentOptions(): JobOptions | null {
    return evaluate({ ...this.form.get().draft, inputPath: "", inputSource: null }, this.environment.get()).options;
  }

  private add(items: { path: string; source: InputSource }[]): boolean {
    const plan = this.plan(items);
    if (plan.kind === "limit") {
      this.store.patch({ notice: plan.notice });
      return false;
    }
    if (plan.rows.length === 0) {
      this.store.patch({ notice: plan.invalidProblem ? { kind: "invalid", problem: plan.invalidProblem } : { kind: "duplicate" } });
      return false;
    }
    this.commit(plan.rows);
    this.store.patch({ notice: { kind: "added", count: plan.rows.length } });
    return true;
  }

  private plan(items: { path: string; source: InputSource }[]):
    | { kind: "ok"; rows: InputRow[]; duplicates: number; invalid: number; invalidProblem: PathProblem | null }
    | { kind: "limit"; notice: ListNotice } {
    const existing = this.store.get().rows;
    const rows: InputRow[] = [];
    let duplicates = 0;
    let invalid = 0;
    let invalidProblem: PathProblem | null = null;
    for (const item of items) {
      const problem = checkPath(item.path, "input_file");
      if (problem) {
        invalid += 1;
        invalidProblem ??= problem;
        continue;
      }
      if ([...existing, ...rows].some((r) => samePath(r.inputPath, item.path))) {
        duplicates += 1;
        continue;
      }
      rows.push({ clientFileId: newRowId(), inputPath: item.path, source: item.source, selected: true, overrides: {}, output: { kind: "inherit" }, attemptFloor: 0 });
    }
    const room = MAX_INPUTS - existing.length;
    if (rows.length > room) return { kind: "limit", notice: { kind: "limit", max: MAX_INPUTS, requested: rows.length, room } };
    return { kind: "ok", rows, duplicates, invalid, invalidProblem };
  }

  /** New rows go to the end (stable order); the first new row becomes the primary one. */
  private commit(rows: InputRow[]): void {
    if (rows.length === 0) return;
    const state = this.store.get();
    this.store.patch({ rows: [...state.rows, ...rows], primaryId: rows[0]!.clientFileId, pickError: null });
  }

  private setSelection(pick: (row: InputRow) => boolean): void {
    const rows = this.store.get().rows;
    let changed = false;
    const next = rows.map((r) => {
      const on = pick(r);
      if (on === r.selected) return r;
      changed = true;
      return { ...r, selected: on };
    });
    // A staged bulk edit belongs to the selection it was made for.
    if (changed) this.store.patch({ rows: next, staged: {} });
  }

  private updateRows(ids: readonly string[], update: (row: InputRow) => InputRow): void {
    const target = new Set(ids);
    this.store.patch({ rows: this.store.get().rows.map((r) => (target.has(r.clientFileId) ? update(r) : r)) });
  }
}
