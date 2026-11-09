import { isApiError } from "../api/errors";
import {
  EDITABLE_FIELDS,
  type EditableSettings,
  type LegacyApi,
  type LegacySettings,
  type LegacySettingsPatch,
  MAX_FOLDER_LENGTH,
} from "../api/legacy";
import { type HostBridge, HostUnavailableError } from "../host";
import { LatestOnly } from "../transport/latest";
import { failed, idleOperation, type Operation, started, succeeded } from "./operation";
import { Store } from "./store";

/** The five editable GUI settings (same `gui-settings.json` the legacy GUI uses). */
export type SettingsDraft = EditableSettings;
export type FolderField = "defaultInputDir" | "defaultOutputDir";
export const FOLDER_PICK_KIND = { defaultInputDir: "input_folder", defaultOutputDir: "output_folder" } as const;

export type PickError = "host_unavailable" | "failed";
/**
 * After a save whose outcome is unknown (timeout/network), a re-read tells what the server has:
 * `applied` = the server already holds the draft; `not_applied` = it does not (the draft stays).
 */
export type Reconciled = "applied" | "not_applied" | null;

export interface SettingsState {
  load: Operation;
  save: Operation;
  /** Last value confirmed by the server; this (not the draft) drives theme/language. */
  saved: LegacySettings | null;
  draft: SettingsDraft | null;
  /** A native folder dialog is open for this field; Save waits until it answers. */
  picking: FolderField | null;
  pickError: { field: FolderField; reason: PickError } | null;
  reconcile: Operation;
  reconciled: Reconciled;
}

export function draftOf(saved: LegacySettings): SettingsDraft {
  return {
    theme: saved.theme,
    language: saved.language,
    defaultProfile: saved.defaultProfile,
    defaultInputDir: saved.defaultInputDir,
    defaultOutputDir: saved.defaultOutputDir,
  };
}

/** Changed fields only; `defaultProfile: null` is a real value ("not specified"), not "unchanged". */
export function diff(draft: SettingsDraft, saved: LegacySettings): LegacySettingsPatch {
  const patch: Record<string, unknown> = {};
  for (const field of EDITABLE_FIELDS) {
    if (draft[field] !== saved[field]) patch[field] = draft[field];
  }
  return patch as LegacySettingsPatch;
}

/**
 * Re-base a draft on newer server values: fields the person edited (relative to the old saved
 * value) keep their edit, every other field follows the server. This keeps a slow reload or a
 * save answer from undoing typing, and keeps an untouched field from re-sending a stale value
 * (e.g. one changed meanwhile from the legacy GUI).
 */
export function rebase(draft: SettingsDraft, before: LegacySettings, after: LegacySettings): SettingsDraft {
  const next: Record<string, unknown> = {};
  for (const field of EDITABLE_FIELDS) next[field] = draft[field] !== before[field] ? draft[field] : after[field];
  return next as unknown as SettingsDraft;
}

export function isDirty(state: Pick<SettingsState, "draft" | "saved">): boolean {
  if (!state.draft || !state.saved) return false;
  return Object.keys(diff(state.draft, state.saved)).length > 0;
}

export function folderTooLong(draft: SettingsDraft | null): boolean {
  return draft !== null && (draft.defaultInputDir.length > MAX_FOLDER_LENGTH || draft.defaultOutputDir.length > MAX_FOLDER_LENGTH);
}

export type SavedListener = (before: LegacySettings, after: LegacySettings) => void;

/**
 * GUI settings through legacy GET/PATCH /api/settings (never PUT, never the v1 settings API).
 * The draft is separate from the server-confirmed value; a failed or unknown save never applies
 * the draft, and nothing is resent automatically. Lives outside Vue, so navigation and mode
 * switches keep the draft and the in-flight state.
 */
export class SettingsController {
  readonly store = new Store<SettingsState>({
    load: idleOperation,
    save: idleOperation,
    saved: null,
    draft: null,
    picking: null,
    pickError: null,
    reconcile: idleOperation,
    reconciled: null,
  });
  private readonly latest = new LatestOnly();
  private readonly savedListeners = new Set<SavedListener>();

  constructor(private readonly api: LegacyApi) {}

  /** Called after every server-confirmed save (not for plain reloads). */
  onSaved(listener: SavedListener): () => void {
    this.savedListeners.add(listener);
    return () => this.savedListeners.delete(listener);
  }

  async load(): Promise<void> {
    this.store.patch({ load: started(this.store.get().load) });
    try {
      const result = await this.latest.run((signal) => this.api.getSettings(signal));
      if (result.stale) return;
      this.adopt(result.value);
      this.store.patch({ load: succeeded(this.store.get().load) });
    } catch (error) {
      if (isApiError(error) && error.kind === "aborted") return;
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }

  /**
   * Explicit re-read after a save whose outcome is unknown. A GET is safe; the PATCH is not
   * resent. Reports whether the server already holds the draft.
   */
  async reconcile(): Promise<Reconciled> {
    const state = this.store.get();
    if (state.reconcile.phase === "pending" || state.save.phase === "pending") return null;
    this.store.patch({ reconcile: started(state.reconcile), reconciled: null });
    try {
      const result = await this.latest.run((signal) => this.api.getSettings(signal));
      if (result.stale) return null;
      this.adopt(result.value);
      const reconciled: Reconciled = isDirty(this.store.get()) ? "not_applied" : "applied";
      const current = this.store.get();
      this.store.patch({
        reconcile: succeeded(current.reconcile),
        reconciled,
        // The unknown-outcome error is answered either way: applied → saved, else Save again.
        save: reconciled === "applied" ? succeeded(current.save) : idleOperation,
      });
      return reconciled;
    } catch (error) {
      this.store.patch({ reconcile: failed(this.store.get().reconcile, error) });
      return null;
    }
  }

  edit(patch: Partial<SettingsDraft>): void {
    const { draft } = this.store.get();
    if (draft === null) return;
    this.store.patch({ draft: { ...draft, ...patch }, reconciled: null });
  }

  revert(): void {
    const { saved } = this.store.get();
    if (saved) this.store.patch({ draft: draftOf(saved), save: idleOperation, pickError: null, reconciled: null });
  }

  /**
   * Native folder chooser. The answer only changes the draft; cancel (null) changes nothing.
   * A chosen folder is not proof that a later job may write there (no backend check in S1).
   */
  async pickFolder(field: FolderField, host: HostBridge): Promise<void> {
    const state = this.store.get();
    if (state.picking !== null || state.draft === null || state.save.phase === "pending") return;
    this.store.patch({ picking: field, pickError: null });
    try {
      const path = await host.pickPath(FOLDER_PICK_KIND[field]);
      this.store.patch({ picking: null });
      if (path !== null) this.edit({ [field]: path } as Partial<SettingsDraft>);
    } catch (error) {
      const reason: PickError = error instanceof HostUnavailableError ? "host_unavailable" : "failed";
      this.store.patch({ picking: null, pickError: { field, reason } });
    }
  }

  /** Returns false when a save/re-check is pending (double submit), a dialog is open, or nothing changed. */
  async save(): Promise<boolean> {
    const state = this.store.get();
    if (state.save.phase === "pending" || state.reconcile.phase === "pending" || state.picking !== null) return false;
    if (state.draft === null || state.saved === null) return false;
    if (folderTooLong(state.draft)) return false;
    const patch = diff(state.draft, state.saved);
    if (Object.keys(patch).length === 0) return false;
    this.store.patch({ save: started(state.save), reconciled: null });
    try {
      const saved = await this.api.patchSettings(patch);
      // A reload that started before the save must not overwrite the newer answer.
      this.latest.cancel();
      const latest = this.store.get();
      const draft = latest.draft ? rebase(latest.draft, state.saved, saved) : draftOf(saved);
      const load = latest.load.phase === "pending" ? succeeded(latest.load) : latest.load;
      this.store.patch({ saved, draft, load, save: succeeded(latest.save) });
      for (const listener of [...this.savedListeners]) listener(state.saved, saved);
      return true;
    } catch (error) {
      // The draft stays, so the person can retry, re-check or revert; nothing is resent automatically.
      this.store.patch({ save: failed(this.store.get().save, error) });
      return false;
    }
  }

  private adopt(saved: LegacySettings): void {
    const current = this.store.get();
    // Unsaved edits survive a reload; untouched fields follow the server.
    const draft = current.draft && current.saved ? rebase(current.draft, current.saved, saved) : draftOf(saved);
    this.store.patch({ saved, draft });
  }
}
