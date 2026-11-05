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
export type ExpertPane = "inputs" | "inspector" | "queue";
export type PaneColumns = 1 | 2 | 3;
/**
 * Example pane splits the reviewer can compare (owner memo: do not fix one ratio yet). Iteration 3
 * starts with the narrower inputs pane (22/53/25) as the default CANDIDATE; 30/42/28 (iteration 2)
 * stays one click away in the review strip.
 */
export type PanePreset = "compactInputs" | "wideInputs";
export const PANE_PRESET_IDS: readonly PanePreset[] = ["compactInputs", "wideInputs"];
export const DEFAULT_PRESET: PanePreset = "compactInputs";
/** App menu presentation the reviewer compares: Windows-like menu bar vs. one menu button. */
export type MenuStyle = "bar" | "button";
/** Below this window width the menu bar always folds into the single menu button. */
export const MENU_BAR_MIN_WIDTH = 1100;
/** Below this window width the Normal destinations fold into one menu (no text is shrunk). */
export const TOPNAV_MIN_WIDTH = 760;
/** Mock dialogs opened from the app menu (Tools / Help). Nothing is read, written or sent. */
export type AppDialog = "systemLog" | "jobLog" | "diagnostics" | "about";
export const SETTINGS_CATEGORIES = ["general", "logs", "advanced", "presets"] as const;
export type SettingsCategory = (typeof SETTINGS_CATEGORIES)[number];

/** Fractions of the pane area's content width (handles excluded); the Inspector takes the rest. */
export interface PaneRatios {
  inputs: number;
  queue: number;
}
export const PANE_PRESETS: Record<PanePreset, PaneRatios> = {
  compactInputs: { inputs: 0.22, queue: 0.25 },
  wideInputs: { inputs: 0.3, queue: 0.28 },
};

export const SIDEBAR = { min: 168, max: 360, default: 200, rail: 56 } as const;
/** Minimum pane widths in CSS px. Below these the layout drops a column instead of overflowing. */
export const PANE_MIN = { inputs: 220, inspector: 340, queue: 240 } as const;
/** Upper bound for a side pane as a fraction of the pane area. */
export const PANE_MAX_FRACTION = 0.45;
export const HANDLE_PX = 8;
/** Pane-area width (not viewport width) at which 3 and 2 columns fit. */
export const THREE_COLUMNS_AT = PANE_MIN.inputs + PANE_MIN.inspector + PANE_MIN.queue + 2 * HANDLE_PX + 120;
export const TWO_COLUMNS_AT = PANE_MIN.inputs + PANE_MIN.inspector + HANDLE_PX + 120;
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
  panes: PaneRatios;
  reviewStripOpen: boolean;
  menuStyle: MenuStyle;
  dialog: AppDialog | null;
  settingsCategory: SettingsCategory;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

export function isNormalSection(section: SectionId): section is NormalSection {
  return (NORMAL_SECTIONS as readonly string[]).includes(section);
}

export function sectionsFor(mode: Mode): readonly SectionId[] {
  return mode === "normal" ? NORMAL_SECTIONS : EXPERT_SECTIONS;
}

export function columnsFor(areaWidth: number): PaneColumns {
  if (areaWidth >= THREE_COLUMNS_AT) return 3;
  if (areaWidth >= TWO_COLUMNS_AT) return 2;
  return 1;
}

export interface PaneWidths {
  columns: PaneColumns;
  inputs: number;
  inspector: number;
  queue: number;
}

/**
 * Concrete pixel widths for a pane-area width. Side panes are clamped to their minimum and to
 * PANE_MAX_FRACTION, and the Inspector never goes below its minimum, so the sum always fits the
 * area (no horizontal overflow whatever the stored ratios are). In two columns the queue moves
 * below at full width; in one column every pane is full width.
 */
export function resolvePanes(areaWidth: number, ratios: PaneRatios): PaneWidths {
  const columns = columnsFor(areaWidth);
  const width = Math.max(0, Math.floor(areaWidth));
  if (columns === 1) return { columns, inputs: width, inspector: width, queue: width };
  const content = width - (columns - 1) * HANDLE_PX;
  const maxSide = Math.floor(content * PANE_MAX_FRACTION);
  if (columns === 2) {
    const inputs = clamp(Math.round(content * ratios.inputs), PANE_MIN.inputs, Math.min(maxSide, content - PANE_MIN.inspector));
    return { columns, inputs, inspector: content - inputs, queue: width };
  }
  let inputs = clamp(Math.round(content * ratios.inputs), PANE_MIN.inputs, maxSide);
  let queue = clamp(Math.round(content * ratios.queue), PANE_MIN.queue, maxSide);
  const over = inputs + queue + PANE_MIN.inspector - content;
  if (over > 0) {
    // Shrink both side panes towards their minimum, proportionally to their slack.
    const slackIn = inputs - PANE_MIN.inputs;
    const slackQ = queue - PANE_MIN.queue;
    const slack = slackIn + slackQ || 1;
    inputs -= Math.ceil((over * slackIn) / slack);
    queue -= Math.ceil((over * slackQ) / slack);
  }
  return { columns, inputs, inspector: content - inputs - queue, queue };
}

/** Allowed range of one side pane (px) for the current area, used by the separator's ARIA values. */
export function paneRange(areaWidth: number, ratios: PaneRatios, pane: "inputs" | "queue"): { min: number; max: number } {
  const resolved = resolvePanes(areaWidth, ratios);
  const content = Math.max(0, Math.floor(areaWidth)) - (resolved.columns - 1) * HANDLE_PX;
  const other = resolved.columns === 3 ? (pane === "inputs" ? resolved.queue : resolved.inputs) : 0;
  const min = PANE_MIN[pane];
  const max = Math.max(min, Math.min(Math.floor(content * PANE_MAX_FRACTION), content - PANE_MIN.inspector - other));
  return { min, max };
}

function initialLayout(): LayoutState {
  return {
    mode: "normal",
    section: "transcribe",
    expertPane: "inspector",
    sidebarWidth: SIDEBAR.default,
    sidebarCollapsed: false,
    sidebarExpandedNarrow: false,
    panes: { ...PANE_PRESETS[DEFAULT_PRESET] },
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
    this.store.patch({ panes: { ...s.panes, [pane]: value } });
  }

  applyPreset(preset: PanePreset): void {
    this.store.patch({ panes: { ...PANE_PRESETS[preset] } });
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
