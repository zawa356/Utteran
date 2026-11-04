import {
  boundReport,
  DROP_EVENT,
  type DroppedPath,
  type FrontendErrorReport,
  type HostBridge,
  HostUnavailableError,
  type PathKind,
  sanitizeDropped,
} from "./index";

/** The subset of `utteran_gui.app.NativeDialogApi` exposed to JavaScript. */
interface NativeApi {
  choose_path(kind: PathKind): Promise<string | null>;
  report_frontend_error(payload: FrontendErrorReport): Promise<boolean>;
}

interface PywebviewWindow extends Window {
  pywebview?: { api?: Partial<NativeApi> };
}

/**
 * pywebview injects `window.pywebview.api` asynchronously and fires `pywebviewready`.
 * Calls wait for that (bounded); in a plain browser they fail with HostUnavailableError.
 */
export function createPywebviewHost(win: Window, readyTimeoutMs = 5000): HostBridge {
  const w = win as PywebviewWindow;
  const ready = new Promise<NativeApi | null>((resolve) => {
    const current = () => (w.pywebview?.api?.choose_path ? (w.pywebview.api as NativeApi) : null);
    const now = current();
    if (now) return resolve(now);
    const timer = win.setTimeout(() => resolve(current()), readyTimeoutMs);
    win.addEventListener(
      "pywebviewready",
      () => {
        win.clearTimeout(timer);
        resolve(current());
      },
      { once: true },
    );
  });
  return {
    kind: "pywebview",
    async pickPath(kind) {
      const api = await ready;
      if (!api) throw new HostUnavailableError();
      const result = await api.choose_path(kind);
      return typeof result === "string" && result !== "" ? result : null;
    },
    onDroppedPaths(listener: (paths: DroppedPath[]) => void) {
      const handler = (event: Event) => listener(sanitizeDropped((event as CustomEvent).detail));
      win.addEventListener(DROP_EVENT, handler);
      return () => win.removeEventListener(DROP_EVENT, handler);
    },
    reportError(report) {
      void ready.then((api) => api?.report_frontend_error(boundReport(report))).catch(() => undefined);
    },
  };
}
