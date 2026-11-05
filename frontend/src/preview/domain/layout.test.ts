import { describe, expect, it } from "vitest";
import { columnsFor, HANDLE_PX, LayoutController, PANE_MIN, paneRange, resolvePanes, SIDEBAR, THREE_COLUMNS_AT, TWO_COLUMNS_AT } from "./layout";

describe("prototype layout (mode IA, sidebar and pane sizes)", () => {
  it("Normal offers three destinations; switching back from an Expert-only page goes home", () => {
    const layout = new LayoutController();
    layout.navigate("queue"); // not offered in Normal
    expect(layout.store.get().section).toBe("transcribe");
    layout.setMode("expert");
    layout.navigate("queue");
    expect(layout.store.get().section).toBe("queue");
    layout.setMode("normal");
    expect(layout.store.get()).toMatchObject({ mode: "normal", section: "transcribe" });
    layout.setMode("expert");
    layout.navigate("setup");
    layout.setMode("normal");
    expect(layout.store.get().section).toBe("setup"); // offered in both
  });

  it("columns follow the pane-area width: 3 → 2 → 1", () => {
    expect(columnsFor(THREE_COLUMNS_AT)).toBe(3);
    expect(columnsFor(THREE_COLUMNS_AT - 1)).toBe(2);
    expect(columnsFor(TWO_COLUMNS_AT)).toBe(2);
    expect(columnsFor(TWO_COLUMNS_AT - 1)).toBe(1);
  });

  it("resolved pane widths always fit the area and respect the minimums", () => {
    for (const width of [THREE_COLUMNS_AT, 1000, 1280, 1700, 2400]) {
      for (const ratios of [{ inputs: 0.3, queue: 0.28 }, { inputs: 0.05, queue: 0.05 }, { inputs: 0.9, queue: 0.9 }]) {
        const r = resolvePanes(width, ratios);
        expect(r.columns).toBe(3);
        expect(r.inputs + r.inspector + r.queue + 2 * HANDLE_PX).toBe(width);
        expect(r.inputs).toBeGreaterThanOrEqual(PANE_MIN.inputs);
        expect(r.queue).toBeGreaterThanOrEqual(PANE_MIN.queue);
        expect(r.inspector).toBeGreaterThanOrEqual(PANE_MIN.inspector);
      }
    }
    const two = resolvePanes(800, { inputs: 0.9, queue: 0.3 });
    expect(two.columns).toBe(2);
    expect(two.inputs + two.inspector + HANDLE_PX).toBe(800);
    expect(two.inspector).toBeGreaterThanOrEqual(PANE_MIN.inspector);
  });

  it("setPaneWidth clamps to the allowed range; presets and reset restore ratios", () => {
    const layout = new LayoutController();
    layout.setPaneWidth("inputs", 10, 1200);
    expect(resolvePanes(1200, layout.store.get().panes).inputs).toBe(PANE_MIN.inputs);
    layout.setPaneWidth("inputs", 5000, 1200);
    const r = resolvePanes(1200, layout.store.get().panes);
    expect(r.inputs).toBe(paneRange(1200, layout.store.get().panes, "inputs").max);
    layout.applyPreset("wideInputs");
    expect(layout.store.get().panes).toEqual({ inputs: 0.3, queue: 0.28 });
    layout.resetPanes(); // iteration 3: the narrower files pane (22/53/25) is the default candidate
    expect(layout.store.get().panes).toEqual({ inputs: 0.22, queue: 0.25 });
  });

  it("22/53/25 at 1280 px with the default sidebar keeps every pane at or above its minimum", () => {
    const area = 1280 - SIDEBAR.default - 2 - 16; // window − sidebar − its border − work-area padding
    const r = resolvePanes(area, new LayoutController().store.get().panes);
    expect(r.columns).toBe(3);
    expect(r.inputs).toBeGreaterThanOrEqual(PANE_MIN.inputs);
    expect(r.queue).toBeGreaterThanOrEqual(PANE_MIN.queue);
    expect(r.inspector).toBeGreaterThan(2 * r.inputs); // the Inspector gets the room
  });

  it("menu style, dialogs and settings category are plain UI state", () => {
    const layout = new LayoutController();
    expect(layout.store.get()).toMatchObject({ menuStyle: "bar", dialog: null, settingsCategory: "general" });
    layout.setMenuStyle("button");
    layout.openDialog("about");
    layout.setSettingsCategory("presets");
    expect(layout.store.get()).toMatchObject({ menuStyle: "button", dialog: "about", settingsCategory: "presets" });
    layout.openDialog(null);
    expect(layout.store.get().dialog).toBeNull();
  });

  it("sidebar width is clamped and resettable; collapse state is separate for narrow windows", () => {
    const layout = new LayoutController();
    layout.setSidebarWidth(10);
    expect(layout.store.get().sidebarWidth).toBe(SIDEBAR.min);
    layout.setSidebarWidth(9999);
    expect(layout.store.get().sidebarWidth).toBe(SIDEBAR.max);
    layout.resetSidebar();
    expect(layout.store.get().sidebarWidth).toBe(SIDEBAR.default);
    layout.toggleSidebar(false);
    expect(layout.store.get()).toMatchObject({ sidebarCollapsed: true, sidebarExpandedNarrow: false });
    layout.toggleSidebar(true);
    expect(layout.store.get()).toMatchObject({ sidebarCollapsed: true, sidebarExpandedNarrow: true });
  });
});
