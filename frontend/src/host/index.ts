/**
 * Host bridge: the only place that knows about the native shell (pywebview + WebView2 today).
 * Views never touch `window.pywebview`. A different host (e.g. Electron) would add another
 * implementation of this interface without touching api/transport/domain/features.
 */
export type PathKind = "input_file" | "input_folder" | "output_folder";

export interface DroppedPath {
  path: string;
  kind: "file" | "folder" | "unknown";
}

export interface FrontendErrorReport {
  kind: string;
  message: string;
  source?: string;
  line?: number;
  column?: number;
}

export class HostUnavailableError extends Error {
  constructor() {
    super("host_unavailable");
    this.name = "HostUnavailableError";
  }
}

export interface HostBridge {
  readonly kind: "pywebview" | "browser" | "fake";
  /** Native dialog. Resolves null when the person cancels; rejects when no host exists. */
  pickPath(kind: PathKind): Promise<string | null>;
  /** Native file drops (pywebview `utteran-input-dropped`). Returns an unsubscribe function. */
  onDroppedPaths(listener: (paths: DroppedPath[]) => void): () => void;
  /** Best-effort, bounded diagnostics; never throws, never blocks the UI. */
  reportError(report: FrontendErrorReport): void;
}

export const DROP_EVENT = "utteran-input-dropped";

export function sanitizeDropped(detail: unknown): DroppedPath[] {
  if (!Array.isArray(detail)) return [];
  return detail.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const { path, kind } = item as Record<string, unknown>;
    if (typeof path !== "string" || path === "") return [];
    return [{ path, kind: kind === "file" || kind === "folder" ? kind : "unknown" }];
  });
}

export function boundReport(report: FrontendErrorReport): FrontendErrorReport {
  // app.py keeps at most 2000 characters per field; trim here as well.
  const clip = (value: string) => value.slice(0, 2000);
  return {
    kind: clip(report.kind),
    message: clip(report.message),
    ...(report.source !== undefined ? { source: clip(report.source) } : {}),
    ...(report.line !== undefined ? { line: report.line } : {}),
    ...(report.column !== undefined ? { column: report.column } : {}),
  };
}
