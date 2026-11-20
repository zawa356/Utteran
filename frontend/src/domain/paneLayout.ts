export type ExpertPane = "inputs" | "inspector" | "queue";
export type PaneColumns = 1 | 2 | 3;
export const AUTO_WIDE_AREA = 1400;

/** Fractions of the pane area's content width (handles excluded); the Inspector takes the rest. */
export interface PaneRatios {
  inputs: number;
  queue: number;
}
export const PANE_PRESETS: Record<"compactInputs" | "wideInputs", PaneRatios> = {
  compactInputs: { inputs: 0.22, queue: 0.25 },
  wideInputs: { inputs: 0.3, queue: 0.28 },
};

/** Ratios for an area width: the stored ones, or the automatic choice when none are stored. */
export function ratiosFor(areaWidth: number, stored: PaneRatios | null): PaneRatios {
  if (stored) return stored;
  return areaWidth < AUTO_WIDE_AREA ? PANE_PRESETS.wideInputs : PANE_PRESETS.compactInputs;
}

export const SIDEBAR = { min: 168, max: 360, default: 200, rail: 56 } as const;
/**
 * Minimum pane widths in CSS px. Below these the layout drops a column instead of overflowing.
 * 260 px for the files pane keeps ~20 characters of a name next to the checkbox and state icon
 * (measured in the E2E at 1280 px); 380 px fits the Inspector's category tabs and field rows.
 */
export const PANE_MIN = { inputs: 260, inspector: 380, queue: 240 } as const;
/** Upper bound for a side pane as a fraction of the pane area. */
export const PANE_MAX_FRACTION = 0.45;
export const HANDLE_PX = 8;
/**
 * Pane-area width (not viewport width) at which 3 and 2 columns fit. Iteration 4 (owner memo #21):
 * panes are NEVER stacked vertically. Two columns = files on the left and ONE of Inspector / queue
 * on the right (switched by a segmented control); one column = one pane at a time.
 */
export const THREE_COLUMNS_AT = PANE_MIN.inputs + PANE_MIN.inspector + PANE_MIN.queue + 2 * HANDLE_PX + 40;
export const TWO_COLUMNS_AT = PANE_MIN.inputs + PANE_MIN.inspector + HANDLE_PX + 40;
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
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
 * area (no horizontal overflow whatever the stored ratios are). In two columns the right column
 * shows the Inspector OR the queue at the same width; in one column every pane is full width.
 */
export function resolvePanes(areaWidth: number, stored: PaneRatios | null): PaneWidths {
  const ratios = ratiosFor(areaWidth, stored);
  const columns = columnsFor(areaWidth);
  const width = Math.max(0, Math.floor(areaWidth));
  if (columns === 1) return { columns, inputs: width, inspector: width, queue: width };
  const content = width - (columns - 1) * HANDLE_PX;
  const maxSide = Math.floor(content * PANE_MAX_FRACTION);
  if (columns === 2) {
    const inputs = clamp(Math.round(content * ratios.inputs), PANE_MIN.inputs, Math.min(maxSide, content - PANE_MIN.inspector));
    return { columns, inputs, inspector: content - inputs, queue: content - inputs };
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
export function paneRange(areaWidth: number, ratios: PaneRatios | null, pane: "inputs" | "queue"): { min: number; max: number } {
  const resolved = resolvePanes(areaWidth, ratios);
  const content = Math.max(0, Math.floor(areaWidth)) - (resolved.columns - 1) * HANDLE_PX;
  const other = resolved.columns === 3 ? (pane === "inputs" ? resolved.queue : resolved.inputs) : 0;
  const min = PANE_MIN[pane];
  const max = Math.max(min, Math.min(Math.floor(content * PANE_MAX_FRACTION), content - PANE_MIN.inspector - other));
  return { min, max };
}

