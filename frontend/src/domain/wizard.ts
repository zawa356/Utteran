import type { LegacyApi, WizardStatus } from "../api/legacy";
import { failed, idleOperation, type Operation, started, succeeded } from "./operation";
import { Store } from "./store";

/**
 * Read-only view of the legacy setup wizard (GET /api/wizard/status). S1 never starts, resumes
 * or completes the wizard (no POST /api/wizard/*, no B8 setup); the person is sent to the
 * legacy screen, which owns the real wizard.
 */
export class WizardStatusController {
  readonly store = new Store<{ load: Operation; data: WizardStatus | null }>({ load: idleOperation, data: null });

  constructor(private readonly api: LegacyApi) {}

  async load(): Promise<void> {
    const state = this.store.get();
    if (state.load.phase === "pending") return;
    this.store.patch({ load: started(state.load) });
    try {
      const data = await this.api.getWizardStatus();
      this.store.patch({ data, load: succeeded(this.store.get().load) });
    } catch (error) {
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }
}

export type WizardSummary = "completed" | "resumable" | "not_started";

export function wizardSummary(status: WizardStatus): WizardSummary {
  if (status.completed) return "completed";
  return status.resumeAvailable ? "resumable" : "not_started";
}
