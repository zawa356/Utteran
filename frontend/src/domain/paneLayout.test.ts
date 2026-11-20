import { describe, expect, it } from "vitest";
import { HANDLE_PX, paneRange, PANE_MIN, resolvePanes, THREE_COLUMNS_AT, TWO_COLUMNS_AT } from "./paneLayout";
import { ShellController } from "./shell";

describe("D4 Expert pane sizes", () => {
  it("uses the available area at both thresholds", () => {
    expect([TWO_COLUMNS_AT - 1, TWO_COLUMNS_AT, THREE_COLUMNS_AT - 1, THREE_COLUMNS_AT].map(
      (w) => resolvePanes(w, null).columns,
    )).toEqual([1, 2, 2, 3]);
  });

  it("keeps each pane within bounds even when restored ratios are cramped", () => {
    for (const area of [936, 1050, 1440, 1864]) {
      const w = resolvePanes(area, { inputs: 0.45, queue: 0.45 });
      expect(w.inputs + w.inspector + w.queue + 2 * HANDLE_PX).toBe(area);
      expect(w.inputs).toBeGreaterThanOrEqual(PANE_MIN.inputs);
      expect(w.inspector).toBeGreaterThanOrEqual(PANE_MIN.inspector);
      expect(w.queue).toBeGreaterThanOrEqual(PANE_MIN.queue);
    }
  });

  it("resizes either side to its published limit without moving the other side", () => {
    const shell = new ShellController();
    const area = 1110;
    const at = () => shell.store.get().paneRatios;
    const before = resolvePanes(area, at());
    const max = paneRange(area, at(), "inputs").max;
    shell.resizePane("inputs", 4000, area);
    expect(resolvePanes(area, at())).toMatchObject({ inputs: max, queue: before.queue });
    shell.resizePane("queue", 0, area);
    expect(resolvePanes(area, at())).toMatchObject({ inputs: max, queue: PANE_MIN.queue });
    shell.resetPanes();
    expect(at()).toBeNull();
  });

  it("retains the chosen right pane and sidebar across mode and layout changes", () => {
    const shell = new ShellController();
    shell.showPane("queue");
    shell.showPane("inputs");
    shell.resizeSidebar(999);
    shell.resizePane("queue", 999, TWO_COLUMNS_AT);
    shell.resizePane("inputs", 999, TWO_COLUMNS_AT - 1);
    shell.setMode("expert");
    shell.setMode("normal");
    expect(shell.store.get()).toMatchObject({
      expertPane: "inputs", expertRightPane: "queue", sidebarWidth: 360, paneRatios: null,
    });
  });
});
