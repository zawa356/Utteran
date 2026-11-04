/** Injectable timers so pollers/backoff are testable with fake clocks. */
export interface Scheduler {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export const browserScheduler: Scheduler = {
  setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export interface Backoff {
  initialMs: number;
  maxMs: number;
}

export function backoffDelay(policy: Backoff, failures: number): number {
  if (failures <= 0) return policy.initialMs;
  return Math.min(policy.maxMs, policy.initialMs * 2 ** Math.min(failures, 16));
}
