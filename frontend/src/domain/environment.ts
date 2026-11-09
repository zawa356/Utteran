import type { LegacyApi, LegacyEnvironment } from "../api/legacy";
import { LatestOnly } from "../transport/latest";
import { cancelled, failed, idleOperation, type Operation, started, succeeded } from "./operation";
import { Store } from "./store";

/**
 * Readiness is decided by the backend (B4 ready evidence). The legacy environment API has
 * none (G-01), so this UI can only ever say "detected, unverified" — never "ready".
 */
export type Readiness = "unverified";

/** What one environment request asked for. `profile: null` = no profile parameter (server default). */
export interface EnvironmentRequest {
  profile: string | null;
  refresh: boolean;
  followsDefault: boolean;
}

export interface EnvironmentState {
  /**
   * The profile the person asked for last (null = no explicit profile: the saved default, or
   * the server's own choice when no default is saved). Set synchronously, never rolled back by
   * a late answer and never replaced by the server's `active_profile`.
   */
  requestedProfile: string | null;
  refresh: boolean;
  load: Operation;
  /** The last successful answer — possibly for an *earlier* request (see `dataFor`). */
  data: LegacyEnvironment | null;
  /**
   * The request that produced `data`, when it arrived (client clock, ms) and the saved default
   * profile *at the time of that request* (a later default is never attached to an old answer).
   */
  dataFor: (EnvironmentRequest & { receivedAt: number; savedDefault: string | null }) | null;
  /** Client clock (ms) when the current/last attempt started; elapsed time is shown from this. */
  startedAt: number | null;
  readiness: Readiness;
  staleDiscarded: number;
  /**
   * Requests this page stopped waiting for (superseded or cancelled). Aborting a fetch does not
   * stop the server's CLI probe: it keeps running to its own end (no backend change in S2).
   */
  abandoned: number;
  /** true while the selection is the saved default profile (not a choice made on this panel). */
  followsDefault: boolean;
}

/**
 * What the panel should say. `partial` = the answer arrived but some probe failed or part of
 * it was unreadable; `empty` = the server has no profile it can use (a valid state, not an error).
 */
export type EnvironmentView = "idle" | "pending" | "success" | "partial" | "empty" | "error" | "cancelled";

export function environmentView(state: EnvironmentState): EnvironmentView {
  const phase = state.load.phase;
  if (phase === "idle" || phase === "pending" || phase === "error" || phase === "cancelled") return phase;
  if (phase === "accepted") return "pending";
  const data = state.data;
  if (!data) return "idle";
  if (data.activeProfile === null) return "empty";
  return data.issues.length > 0 ? "partial" : "success";
}

/**
 * Whether `data` answers the *current* selection. While a new selection is loading, or after it
 * failed or was cancelled, `data` is the previous result and must be labelled as such.
 */
export function isCurrentResult(state: EnvironmentState): boolean {
  if (!state.data || !state.dataFor || state.load.phase !== "success") return false;
  return state.dataFor.profile === state.requestedProfile && state.dataFor.followsDefault === state.followsDefault;
}

export interface EnvironmentControllerOptions {
  /** The saved default profile (S1 `SettingsController.saved.defaultProfile`). */
  defaultProfile?: () => string | null;
  now?: () => number;
}

export class EnvironmentController {
  readonly store = new Store<EnvironmentState>({
    requestedProfile: null,
    refresh: false,
    load: idleOperation,
    data: null,
    dataFor: null,
    startedAt: null,
    readiness: "unverified",
    staleDiscarded: 0,
    abandoned: 0,
    followsDefault: false,
  });
  private readonly latest = new LatestOnly();
  private readonly now: () => number;
  private readonly defaultProfile: () => string | null;
  private inFlight: { request: EnvironmentRequest; promise: Promise<void> } | null = null;

  constructor(
    private readonly api: LegacyApi,
    options: EnvironmentControllerOptions = {},
  ) {
    this.now = options.now ?? (() => Date.now());
    this.defaultProfile = options.defaultProfile ?? (() => null);
  }

  /** Select a profile on this panel (manual choice; a later default change does not override it). */
  select(profile: string | null): Promise<void> {
    return this.load({ profile, refresh: false, followsDefault: false });
  }

  /**
   * Show the saved default profile (start-up, after the default was saved, or "follow the
   * default" on the panel). Like the legacy GUI's boot, this never re-detects (`refresh=false`).
   */
  selectDefault(profile: string | null = this.defaultProfile()): Promise<void> {
    return this.load({ profile, refresh: false, followsDefault: true });
  }

  /** Re-detect devices for the current selection (slow; the only path that sends `refresh=true`). */
  redetect(): Promise<void> {
    const state = this.store.get();
    return this.load({ profile: state.requestedProfile, refresh: true, followsDefault: state.followsDefault });
  }

  retry(): Promise<void> {
    const state = this.store.get();
    return this.load({ profile: state.requestedProfile, refresh: state.refresh, followsDefault: state.followsDefault });
  }

  /**
   * Stop waiting for the answer. The server-side probe is NOT stopped by this (a fetch abort
   * cannot end the profile CLI child process); a late answer is discarded.
   */
  cancel(): void {
    const state = this.store.get();
    if (state.load.phase !== "pending") return;
    this.latest.cancel();
    this.inFlight = null;
    this.store.patch({ load: cancelled(state.load), abandoned: state.abandoned + 1 });
  }

  private load(request: EnvironmentRequest): Promise<void> {
    const state = this.store.get();
    // The same request is already on its way: do not start a second server probe for it.
    if (this.inFlight && state.load.phase === "pending" && sameRequest(this.inFlight.request, request)) {
      return this.inFlight.promise;
    }
    const superseded = this.inFlight !== null && state.load.phase === "pending";
    this.store.patch({
      requestedProfile: request.profile,
      refresh: request.refresh,
      followsDefault: request.followsDefault,
      load: started(state.load),
      startedAt: this.now(),
      abandoned: state.abandoned + (superseded ? 1 : 0),
    });
    const promise = this.run(request, this.defaultProfile());
    this.inFlight = { request, promise };
    return promise;
  }

  private async run(request: EnvironmentRequest, savedDefault: string | null): Promise<void> {
    try {
      const result = await this.latest.run((signal) => this.api.getEnvironment(request.profile, request.refresh, signal));
      if (result.stale) {
        this.store.patch({ staleDiscarded: this.store.get().staleDiscarded + 1 });
        return;
      }
      this.inFlight = null;
      this.store.patch({
        data: result.value,
        dataFor: { ...request, receivedAt: this.now(), savedDefault },
        load: succeeded(this.store.get().load),
      });
    } catch (error) {
      this.inFlight = null;
      // `data` is kept as the previous result; the view labels it as such (never as current).
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }
}

function sameRequest(a: EnvironmentRequest, b: EnvironmentRequest): boolean {
  return a.profile === b.profile && a.refresh === b.refresh && a.followsDefault === b.followsDefault;
}
