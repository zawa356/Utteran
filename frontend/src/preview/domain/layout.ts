import { type ExpertPane, type PaneRatios, clamp, columnsFor, HANDLE_PX, paneRange, PANE_PRESETS, ratiosFor, SIDEBAR } from "../../domain/paneLayout";
export * from "../../domain/paneLayout";
import { Store } from "../../domain/store";

/**
 * Prototype-only information architecture (UI-3-0 iteration 2). The mode switches the whole app,
 * not just the transcription page: Normal = top bar with three destinations and one file;
 * Expert = resizable management sidebar with six destinations and three resizable panes.
 * Framework-independent (like `domain/`) so view unmounts never lose it. Nothing is persisted.
 */
export type Mode = "normal" | "expert";
export const NORMAL_SECTIONS = ["transcribe", "setup", "settings"] as const;
export const EXPERT_SECTIONS = ["transcribe", "queue", "results", "models", "setup", "settings"] as const;
export type SectionId = (typeof EXPERT_SECTIONS)[number];
export type NormalSection = (typeof NORMAL_SECTIONS)[number];
/**
 * Pane splits (owner memos: do not fix one ratio yet; #21 file names were cut at 1280 px with
 * 22/53/25). Iteration 4 default CANDIDATE is "auto": 30/42/28 while the pane area is narrower
 * than AUTO_WIDE_AREA, 22/53/25 above it, and the files pane never below PANE_MIN.inputs. A drag
 * or a fixed preset replaces "auto" until "reset pane widths".
 */
export type PanePreset = "auto" | "compactInputs" | "wideInputs";
export const PANE_PRESET_IDS: readonly PanePreset[] = ["auto", "compactInputs", "wideInputs"];
export const DEFAULT_PRESET: PanePreset = "auto";
/** App menu presentation the reviewer compares: Windows-like menu bar vs. one menu button. */
export type MenuStyle = "bar" | "button";
/** Below this window width the menu bar always folds into the single menu button. */
export const MENU_BAR_MIN_WIDTH = 1100;
/** Below this window width the Normal destinations fold into one menu (no text is shrunk). */
export const TOPNAV_MIN_WIDTH = 760;
/** Mock dialogs opened from the app menu (Tools / Help). Nothing is read, written or sent. */
export type AppDialog = "systemLog" | "jobLog" | "setupLog" | "diagnostics" | "about" | "maintenance";
export const SETTINGS_CATEGORIES = ["general", "logs", "advanced", "presets"] as const;
export type SettingsCategory = (typeof SETTINGS_CATEGORIES)[number];

/** Narrow windows start with the rail; the expand button stays visible and works at any width. */
export const NARROW_WINDOW = 1024;

export interface LayoutState {
  mode: Mode;
  section: SectionId;
  /** The pane shown when only one column fits. */
  expertPane: ExpertPane;
  sidebarWidth: number;
  /** User choice for wide windows. */
  sidebarCollapsed: boolean;
  /** User choice for narrow windows (they start as a rail). */
  sidebarExpandedNarrow: boolean;
  /** null = automatic ratios (see ratiosFor). */
  panes: PaneRatios | null;
  reviewStripOpen: boolean;
  menuStyle: MenuStyle;
  dialog: AppDialog | null;
  settingsCategory: SettingsCategory;
}

export function isNormalSection(section: SectionId): section is NormalSection {
  return (NORMAL_SECTIONS as readonly string[]).includes(section);
}

export function sectionsFor(mode: Mode): readonly SectionId[] {
  return mode === "normal" ? NORMAL_SECTIONS : EXPERT_SECTIONS;
}

function initialLayout(): LayoutState {
  return {
    mode: "normal",
    section: "transcribe",
    expertPane: "inspector",
    sidebarWidth: SIDEBAR.default,
    sidebarCollapsed: false,
    sidebarExpandedNarrow: false,
    panes: null,
    reviewStripOpen: true,
    menuStyle: "bar",
    dialog: null,
    settingsCategory: "general",
  };
}

export class LayoutController {
  readonly store = new Store<LayoutState>(initialLayout());

  /**
   * Switching mode never touches the demo data. A section that the target mode does not offer
   * (queue/results/models in Normal) falls back to the transcription home.
   */
  setMode(mode: Mode): void {
    const s = this.store.get();
    const section = mode === "normal" && !isNormalSection(s.section) ? "transcribe" : s.section;
    this.store.patch({ mode, section });
  }

  navigate(section: SectionId): void {
    if (!sectionsFor(this.store.get().mode).includes(section)) return;
    this.store.patch({ section });
  }

  /** Normal → Expert jump to a management page (e.g. "manage several files in Expert"). */
  openInExpert(section: SectionId): void {
    this.store.patch({ mode: "expert", section });
  }

  showPane(expertPane: ExpertPane): void {
    this.store.patch({ expertPane });
  }

  /** Rail ⇄ expanded. `narrow` = the window is below NARROW_WINDOW, which has its own default. */
  toggleSidebar(narrow: boolean): void {
    const s = this.store.get();
    if (narrow) this.store.patch({ sidebarExpandedNarrow: !s.sidebarExpandedNarrow });
    else this.store.patch({ sidebarCollapsed: !s.sidebarCollapsed });
  }

  setSidebarWidth(px: number): void {
    this.store.patch({ sidebarWidth: clamp(Math.round(px), SIDEBAR.min, SIDEBAR.max) });
  }

  resetSidebar(): void {
    this.store.patch({ sidebarWidth: SIDEBAR.default });
  }

  /** Stores a side pane width as a fraction of the area, clamped to the range that fits. */
  setPaneWidth(pane: "inputs" | "queue", px: number, areaWidth: number): void {
    const s = this.store.get();
    const { min, max } = paneRange(areaWidth, s.panes, pane);
    const columns = columnsFor(areaWidth);
    const content = Math.max(1, Math.floor(areaWidth) - (columns - 1) * HANDLE_PX);
    const value = clamp(Math.round(px), min, max) / content;
    this.store.patch({ panes: { ...ratiosFor(areaWidth, s.panes), [pane]: value } });
  }

  applyPreset(preset: PanePreset): void {
    this.store.patch({ panes: preset === "auto" ? null : { ...PANE_PRESETS[preset] } });
  }

  resetPanes(): void {
    this.applyPreset(DEFAULT_PRESET);
  }

  setMenuStyle(menuStyle: MenuStyle): void {
    this.store.patch({ menuStyle });
  }

  openDialog(dialog: AppDialog | null): void {
    this.store.patch({ dialog });
  }

  setSettingsCategory(settingsCategory: SettingsCategory): void {
    this.store.patch({ settingsCategory });
  }

  toggleReviewStrip(): void {
    this.store.patch({ reviewStripOpen: !this.store.get().reviewStripOpen });
  }
}
