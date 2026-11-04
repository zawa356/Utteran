import type { DroppedPath, HostBridge, PathKind } from "./index";
import type { FrontendErrorReport } from "./index";

/** Explicitly injected test host (unit tests, E2E harness). Never selected automatically. */
export interface FakeHost extends HostBridge {
  readonly reports: FrontendErrorReport[];
  readonly picks: PathKind[];
  nextPick: string | null;
  drop(paths: DroppedPath[]): void;
}

export function createFakeHost(): FakeHost {
  const listeners = new Set<(paths: DroppedPath[]) => void>();
  const host: FakeHost = {
    kind: "fake",
    reports: [],
    picks: [],
    nextPick: null,
    async pickPath(kind) {
      host.picks.push(kind);
      return host.nextPick;
    },
    onDroppedPaths(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    reportError(report) {
      host.reports.push(report);
    },
    drop(paths) {
      for (const listener of listeners) listener(paths);
    },
  };
  return host;
}
