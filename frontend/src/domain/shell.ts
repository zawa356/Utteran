import { type ExpertPane, type PaneRatios, clamp, HANDLE_PX, paneRange, ratiosFor, resolvePanes, SIDEBAR } from "./paneLayout";
import { Store } from "./store";
import type { ApiMode } from "./v1/apiMode";

/**
 * Information architecture (Design Freeze, iteration 5; bundle D1). The mode switches the whole
 * navigation, not the data: Normal = three destinations in the app bar and no sidebar; Expert =
 * the six-destination management sidebar. Both show the same AppCore (inputs, settings drafts,
 * jobs); switching never submits, cancels or deletes anything.
 */
export const NORMAL_SECTIONS = ["transcribe", "setup", "settings"] as const;
export const EXPERT_SECTIONS = ["transcribe", "queue", "results", "models", "setup", "settings"] as const;
/** Every section (= the Expert set; Normal is a subset). */
export const SECTIONS = EXPERT_SECTIONS;
export type SectionId = (typeof EXPERT_SECTIONS)[number];
export type NormalSection = (typeof NORMAL_SECTIONS)[number];
export type Mode = "normal" | "expert";
export type { ExpertPane } from "./paneLayout";

/**
 * Below this window width the three Normal destinations fold into one menu that names the current
 * page (measured: the app bar of the longest language must fit without shrinking any label).
 */
export const TOPNAV_FOLD_WIDTH = 1000;
/** Below this window width the Expert sidebar starts as an icon rail (the toggle still expands it). */
export const NARROW_WINDOW = 1024;

/**
 * Migration status is explicit so that nothing un-migrated looks operable.
 * `partial` = a real, limited slice; `not_migrated` = only an explanation is shown.
 */
export type MigrationStatus = "partial" | "not_migrated";

export const SECTION_STATUS: Record<SectionId, MigrationStatus> = {
  transcribe: "partial", // S3-A/S3-B1 (legacy) or S5-A/S5-B (v1-dev): no diarization
  results: "not_migrated", // v1-dev: partial (D3-A)
  models: "not_migrated",
  queue: "not_migrated", // legacy `--next-ui`; v1-dev: partial (bundle D2-A, see `sectionStatus`)
  setup: "not_migrated",
  settings: "partial", // S1: display, defaults, folders, token, logs; setup and Expert categories later
};

/**
 * The queue and results pages are available only with the v1 API.
 */
export function sectionStatus(section: SectionId, apiMode: ApiMode): MigrationStatus {
  if ((section === "queue" || section === "results") && apiMode === "v1-dev") return "partial";
  return SECTION_STATUS[section];
}

export function isNormalSection(section: SectionId): section is NormalSection {
  return (NORMAL_SECTIONS as readonly string[]).includes(section);
}

export function sectionsFor(mode: Mode): readonly SectionId[] {
  return mode === "normal" ? NORMAL_SECTIONS : EXPERT_SECTIONS;
}

export interface ShellState {
  mode: Mode;
  /** The page shown now: always `normalSection` in Normal and `expertSection` in Expert. */
  section: SectionId;
  /** Last page chosen in each mode (kept separately so a mode switch returns to it). */
  normalSection: NormalSection;
  expertSection: SectionId;
  /** Which Expert pane is shown when the window is too narrow for three columns. */
  expertPane: ExpertPane;
  expertRightPane: Exclude<ExpertPane, "inputs">;
  /** Expert sidebar: the person's choice for wide windows (rail when true). */
  navCollapsed: boolean;
  /** Expert sidebar: the person's choice for narrow windows (they start as a rail). */
  navExpandedNarrow: boolean;
  sidebarWidth: number;
  paneRatios: PaneRatios | null;
}

const INITIAL: ShellState = {
  mode: "normal",
  section: "transcribe",
  normalSection: "transcribe",
  expertSection: "transcribe",
  expertPane: "inspector",
  expertRightPane: "inspector",
  navCollapsed: false,
  navExpandedNarrow: false,
  sidebarWidth: SIDEBAR.default,
  paneRatios: null,
};

/** Whether the Expert sidebar is drawn as an icon rail for this window. */
export function isRail(state: Pick<ShellState, "navCollapsed" | "navExpandedNarrow">, narrow: boolean): boolean {
  return narrow ? !state.navExpandedNarrow : state.navCollapsed;
}

/** Session-only UI state (G-05/G-06: nothing here is persisted). */
export class ShellController {
  readonly store = new Store<ShellState>({ ...INITIAL });

  /** Shows the target mode's last page (Normal falls back to Transcribe by construction). */
  setMode(mode: Mode): void {
    const s = this.store.get();
    if (s.mode === mode) return;
    this.store.patch({ mode, section: mode === "normal" ? s.normalSection : s.expertSection });
  }

  /**
   * Goes to a page of the current mode. A page the mode does not offer (an Expert-only page in
   * Normal) is refused and changes nothing; the return value says whether it was applied.
   */
  navigate(section: SectionId): boolean {
    const s = this.store.get();
    if (s.mode === "normal") {
      if (!isNormalSection(section)) return false;
      if (s.section !== section || s.normalSection !== section) this.store.patch({ section, normalSection: section });
      return true;
    }
    if (s.section !== section || s.expertSection !== section) this.store.patch({ section, expertSection: section });
    return true;
  }

  /** Normal → Expert on the transcription page (the input list and per-file settings). */
  openExpertTranscribe(): void {
    this.setMode("expert");
    this.navigate("transcribe");
  }

  showPane(expertPane: ExpertPane): void {
    this.store.patch(expertPane === "inputs" ? { expertPane } : { expertPane, expertRightPane: expertPane });
  }

  resizeSidebar(width: number): void {
    this.store.patch({ sidebarWidth: clamp(Math.round(width), SIDEBAR.min, SIDEBAR.max) });
  }

  resizePane(pane: "inputs" | "queue", width: number, areaWidth: number): void {
    const { paneRatios } = this.store.get();
    const widths = resolvePanes(areaWidth, paneRatios);
    if (widths.columns === 1 || (pane === "queue" && widths.columns === 2)) return;
    const { min, max } = paneRange(areaWidth, paneRatios, pane);
    const content = Math.floor(areaWidth) - (widths.columns - 1) * HANDLE_PX;
    const ratios = widths.columns === 3
      ? { inputs: widths.inputs / content, queue: widths.queue / content }
      : ratiosFor(areaWidth, paneRatios);
    this.store.patch({ paneRatios: { ...ratios, [pane]: clamp(Math.round(width), min, max) / content } });
  }

  resetPanes(): void {
    this.store.patch({ paneRatios: null });
  }

  /** Toggles the Expert sidebar for the current window class; wide and narrow choices are kept apart. */
  toggleNav(narrow = false): void {
    const s = this.store.get();
    if (narrow) this.store.patch({ navExpandedNarrow: !s.navExpandedNarrow });
    else this.store.patch({ navCollapsed: !s.navCollapsed });
  }
}

/**
 * The standing notice of the shell. `apiMode` is the server-rendered value only (S5-A); the legacy
 * text (`--next-ui`, sequential legacy submission) is never shown as a description of v1 and the
 * v1 text never replaces it in legacy mode.
 */
export function previewNoticeKey(apiMode: ApiMode): "app.previewNotice" | "app.previewNoticeV1" {
  return apiMode === "v1-dev" ? "app.previewNoticeV1" : "app.previewNotice";
}
