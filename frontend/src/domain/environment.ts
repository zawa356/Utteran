import type { LegacyApi, LegacyEnvironment } from "../api/legacy";
import { LatestOnly } from "../transport/latest";
import { cancelled, failed, idleOperation, type Operation, started, succeeded } from "./operation";
import { Store } from "./store";

/**
 * Readiness is decided by the backend (B4 ready evidence). The legacy environment API has
 * none (G-01), so this UI can only ever say "detected, unverified" — never "ready".
 */
export type Readiness = "unverified";

export interface EnvironmentState {
  /** What the person selected last. Set synchronously, never rolled back by a late answer. */
  requestedProfile: string | null;
  refresh: boolean;
  load: Operation;
  data: LegacyEnvironment | null;
  readiness: Readiness;
  staleDiscarded: number;
}

export class EnvironmentController {
  readonly store = new Store<EnvironmentState>({
    requestedProfile: null,
    refresh: false,
    load: idleOperation,
    data: null,
    readiness: "unverified",
    staleDiscarded: 0,
  });
  private readonly latest = new LatestOnly();

  constructor(private readonly api: LegacyApi) {}

  /** Select a profile (or the server default with null) and load its snapshot. */
  select(profile: string | null): Promise<void> {
    return this.load(profile, false);
  }

  /** Re-detect devices for the current selection (slow; can be cancelled). */
  redetect(): Promise<void> {
    return this.load(this.store.get().requestedProfile, true);
  }

  retry(): Promise<void> {
    const state = this.store.get();
    return this.load(state.requestedProfile, state.refresh);
  }

  cancel(): void {
    if (this.store.get().load.phase !== "pending") return;
    this.latest.cancel();
    this.store.patch({ load: cancelled(this.store.get().load) });
  }

  private async load(profile: string | null, refresh: boolean): Promise<void> {
    this.store.patch({ requestedProfile: profile, refresh, load: started(this.store.get().load) });
    try {
      const result = await this.latest.run((signal) => this.api.getEnvironment(profile, refresh, signal));
      if (result.stale) {
        this.store.patch({ staleDiscarded: this.store.get().staleDiscarded + 1 });
        return;
      }
      const data = result.value;
      // The server may fall back to its own default; record what it reports, but keep
      // the person's selection unless they had not chosen anything yet.
      const requestedProfile = profile ?? data.activeProfile;
      this.store.patch({ data, requestedProfile, load: succeeded(this.store.get().load) });
    } catch (error) {
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }
}
