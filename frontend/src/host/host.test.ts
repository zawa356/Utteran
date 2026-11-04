import { describe, expect, it, vi } from "vitest";
import { createFakeHost } from "./fake";
import { boundReport, DROP_EVENT, HostUnavailableError, sanitizeDropped } from "./index";
import { createPywebviewHost } from "./pywebview";

function fakeWindow(api?: object) {
  const target = new EventTarget() as EventTarget & Record<string, unknown>;
  target.setTimeout = (cb: () => void, ms: number) => setTimeout(cb, ms);
  target.clearTimeout = (h: ReturnType<typeof setTimeout>) => clearTimeout(h);
  if (api) target.pywebview = { api };
  return target as unknown as Window & Record<string, unknown>;
}

describe("host bridge", () => {
  it("sanitizes dropped paths from the native bridge", () => {
    expect(sanitizeDropped([{ path: "a.wav", kind: "file" }, { path: "", kind: "file" }, { path: "d", kind: "x" }, 3])).toEqual([
      { path: "a.wav", kind: "file" },
      { path: "d", kind: "unknown" },
    ]);
    expect(sanitizeDropped(null)).toEqual([]);
  });

  it("bounds error reports to the size app.py accepts", () => {
    const report = boundReport({ kind: "error", message: "x".repeat(5000), line: 1 });
    expect(report.message).toHaveLength(2000);
    expect(report.line).toBe(1);
  });

  it("pywebview host calls choose_path once ready and normalizes cancel", async () => {
    const choose = vi.fn(async () => "");
    const win = fakeWindow();
    const host = createPywebviewHost(win, 1000);
    (win as Record<string, unknown>).pywebview = { api: { choose_path: choose, report_frontend_error: async () => true } };
    win.dispatchEvent(new Event("pywebviewready"));
    expect(await host.pickPath("input_file")).toBeNull();
    expect(choose).toHaveBeenCalledWith("input_file");
  });

  it("without a native bridge pickPath fails explicitly instead of hanging", async () => {
    vi.useFakeTimers();
    const host = createPywebviewHost(fakeWindow(), 1000);
    const result = host.pickPath("input_folder").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(1001);
    expect(await result).toBeInstanceOf(HostUnavailableError);
    vi.useRealTimers();
  });

  it("forwards native drop events and unsubscribes", () => {
    const win = fakeWindow({ choose_path: async () => null });
    const host = createPywebviewHost(win);
    const seen: unknown[] = [];
    const off = host.onDroppedPaths((paths) => seen.push(paths));
    win.dispatchEvent(new CustomEvent(DROP_EVENT, { detail: [{ path: "b.wav", kind: "file" }] }));
    off();
    win.dispatchEvent(new CustomEvent(DROP_EVENT, { detail: [{ path: "c.wav", kind: "file" }] }));
    expect(seen).toEqual([[{ path: "b.wav", kind: "file" }]]);
  });

  it("fake host is explicit and records calls", async () => {
    const host = createFakeHost();
    host.nextPick = "synthetic.wav";
    expect(await host.pickPath("input_file")).toBe("synthetic.wav");
    host.reportError({ kind: "error", message: "m" });
    expect(host.reports).toHaveLength(1);
  });
});
