import type { Language, LegacyApi, LegacySettings, Theme } from "../api/legacy";
import { LatestOnly } from "../transport/latest";
import { failed, idleOperation, type Operation, started, succeeded } from "./operation";
import { Store } from "./store";

export interface SettingsDraft {
  theme: Theme;
  language: Language;
}

export interface SettingsState {
  load: Operation;
  save: Operation;
  /** Last value confirmed by the server; this (not the draft) drives theme/language. */
  saved: LegacySettings | null;
  draft: SettingsDraft | null;
}

/**
 * The UI-2 settings slice: theme and language only, via legacy GET/PATCH /api/settings
 * (the same file the legacy GUI uses, so both stay compatible). No destructive actions.
 */
export class SettingsController {
  readonly store = new Store<SettingsState>({ load: idleOperation, save: idleOperation, saved: null, draft: null });
  private readonly latest = new LatestOnly();

  constructor(private readonly api: LegacyApi) {}

  async load(): Promise<void> {
    this.store.patch({ load: started(this.store.get().load) });
    try {
      const result = await this.latest.run((signal) => this.api.getSettings(signal));
      if (result.stale) return;
      const current = this.store.get();
      const saved = result.value;
      // An unsaved draft survives a reload; otherwise mirror the server.
      const draft = current.draft && isDirty(current) ? current.draft : { theme: saved.theme, language: saved.language };
      this.store.patch({ saved, draft, load: succeeded(current.load) });
    } catch (error) {
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }

  edit(patch: Partial<SettingsDraft>): void {
    const { draft } = this.store.get();
    if (draft === null) return;
    this.store.patch({ draft: { ...draft, ...patch } });
  }

  revert(): void {
    const { saved } = this.store.get();
    if (saved) this.store.patch({ draft: { theme: saved.theme, language: saved.language }, save: idleOperation });
  }

  /** Returns false when a save is already pending (double submit) or nothing changed. */
  async save(): Promise<boolean> {
    const state = this.store.get();
    if (state.save.phase === "pending" || state.draft === null || state.saved === null) return false;
    const patch: Partial<SettingsDraft> = {};
    if (state.draft.theme !== state.saved.theme) patch.theme = state.draft.theme;
    if (state.draft.language !== state.saved.language) patch.language = state.draft.language;
    if (Object.keys(patch).length === 0) return false;
    this.store.patch({ save: started(state.save) });
    try {
      const saved = await this.api.patchSettings(patch);
      const latest = this.store.get();
      // Keep edits made while the request was in flight.
      const draft = latest.draft && sameDraft(latest.draft, state.draft) ? { theme: saved.theme, language: saved.language } : latest.draft;
      this.store.patch({ saved, draft, save: succeeded(latest.save) });
      return true;
    } catch (error) {
      // The draft stays, so the person can retry or revert; nothing is resent automatically.
      this.store.patch({ save: failed(this.store.get().save, error) });
      return false;
    }
  }
}

function sameDraft(a: SettingsDraft, b: SettingsDraft): boolean {
  return a.theme === b.theme && a.language === b.language;
}

export function isDirty(state: SettingsState): boolean {
  if (!state.draft || !state.saved) return false;
  return state.draft.theme !== state.saved.theme || state.draft.language !== state.saved.language;
}
