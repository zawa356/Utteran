import { createHttpClient, type FetchLike } from "../api/http";
import { createLegacyApi, type LegacyApi } from "../api/legacy";
import { createLegacyJobsApi, type LegacyJobsApi } from "../api/legacyJobs";
import type { HostBridge } from "../host";
import { EnvironmentController } from "./environment";
import { type EventSourceFactory, JobController } from "./job";
import { LogFolderController } from "./logs";
import { SettingsController } from "./settings";
import { ShellController } from "./shell";
import { TokenController } from "./token";
import { TranscriptionFormController } from "./transcription";
import { WizardStatusController } from "./wizard";

/** Everything the views use, created once and outside Vue (mode switches keep it). */
export interface AppCore {
  host: HostBridge;
  legacy: LegacyApi;
  shell: ShellController;
  environment: EnvironmentController;
  settings: SettingsController;
  token: TokenController;
  logs: LogFolderController;
  wizard: WizardStatusController;
  jobsApi: LegacyJobsApi;
  /** S3-A: one shared form and one job tracker for Normal and Expert (created once). */
  transcription: TranscriptionFormController;
  job: JobController;
  /** App exit: stop polling/SSE and listeners. */
  dispose(): void;
  /** Start-up reads: settings first, then the environment for the saved default profile. */
  start(): Promise<void>;
}

export function createCore(deps: { fetch: FetchLike; host: HostBridge; eventSource?: EventSourceFactory }): AppCore {
  const http = createHttpClient({ fetch: deps.fetch });
  const legacy = createLegacyApi(http);
  const jobsApi = createLegacyJobsApi(http);
  const settings = new SettingsController(legacy);
  // "Follow the default" on the environment panel reads the *saved* default (never the draft).
  const environment = new EnvironmentController(legacy, { defaultProfile: () => settings.store.get().saved?.defaultProfile ?? null });
  // A saved default profile is shown on the environment panel only if the panel was following
  // the default (a profile the person picked there is kept). Saving and the environment check
  // are separate operations with separate states; no device re-detection is started.
  settings.onSaved((before, after) => {
    const env = environment.store.get();
    if (before.defaultProfile === after.defaultProfile || !env.followsDefault || env.load.phase === "idle") return;
    void environment.selectDefault(after.defaultProfile);
  });
  const transcription = new TranscriptionFormController(environment.store, settings.store);
  const job = new JobController(jobsApi, { eventSource: deps.eventSource });
  return {
    host: deps.host,
    jobsApi,
    transcription,
    job,
    dispose() {
      job.dispose();
      transcription.dispose();
    },
    legacy,
    shell: new ShellController(),
    environment,
    settings,
    token: new TokenController(legacy),
    logs: new LogFolderController(legacy),
    wizard: new WizardStatusController(legacy),
    async start() {
      await settings.load();
      if (environment.store.get().load.phase === "idle") {
        await environment.selectDefault(settings.store.get().saved?.defaultProfile ?? null);
      }
    },
  };
}
