import { Store } from "./store";

export const SECTIONS = ["transcribe", "results", "models", "queue", "setup", "settings"] as const;
export type SectionId = (typeof SECTIONS)[number];
export type Mode = "normal" | "expert";
export type ExpertPane = "inputs" | "inspector" | "queue";

/**
 * Migration status is explicit so that nothing un-migrated looks operable.
 * `partial` = a real, limited slice; `not_migrated` = only an explanation is shown.
 */
export type MigrationStatus = "partial" | "not_migrated";

export const SECTION_STATUS: Record<SectionId, MigrationStatus> = {
  transcribe: "partial", // S3-A: one file, legacy /api/jobs (no diarization, no multi-input/queue)
  results: "not_migrated",
  models: "not_migrated",
  queue: "not_migrated",
  setup: "not_migrated",
  settings: "partial", // S1: display, defaults, folders, token, logs; setup and Expert categories later
};

export interface ShellState {
  mode: Mode;
  section: SectionId;
  /** Which Expert pane is shown when the window is too narrow for three columns. */
  expertPane: ExpertPane;
  navCollapsed: boolean;
}

/** Session-only UI state (G-05/G-06: nothing here is persisted in UI-2). */
export class ShellController {
  readonly store = new Store<ShellState>({ mode: "normal", section: "transcribe", expertPane: "inspector", navCollapsed: false });

  setMode(mode: Mode): void {
    this.store.patch({ mode });
  }

  navigate(section: SectionId): void {
    this.store.patch({ section });
  }

  showPane(expertPane: ExpertPane): void {
    this.store.patch({ expertPane });
  }

  toggleNav(): void {
    this.store.patch({ navCollapsed: !this.store.get().navCollapsed });
  }
}
