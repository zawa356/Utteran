import { createHttpClient, type FetchLike } from "../api/http";
import { createLegacyApi, type LegacyApi } from "../api/legacy";
import { ApiError } from "../api/errors";
import { createLegacyJobsApi, type LegacyJobsApi } from "../api/legacyJobs";
import { createV1ResourcesApi, type V1ResourcesApi } from "../api/v1Resources";
import type { HostBridge } from "../host";
import { AdmissionGate } from "./admission";
import { BatchSubmitter } from "./batchSubmission";
import { EnvironmentController } from "./environment";
import { InputCollectionController } from "./inputCollection";
import { type EventSourceFactory, JobController } from "./job";
import { LogFolderController } from "./logs";
import { SettingsController } from "./settings";
import { ShellController } from "./shell";
import { TokenController } from "./token";
import { TranscriptionFormController } from "./transcription";
import type { ApiMode } from "./v1/apiMode";
import { V1LibraryController } from "./v1/library";
import { V1SingleJobController } from "./v1/singleJob";
import { WizardStatusController } from "./wizard";

/** S5-A v1 single-job path; present only when the server rendered `v1-dev` (`--next-ui-v1`). */
export interface V1Core {
  api: V1ResourcesApi;
  library: V1LibraryController;
  job: V1SingleJobController;
}

/**
 * In v1 mode the legacy start/cancel routes are closed in the client too (the server answers 409
 * anyway): no request is sent, so there is no legacy fallback by any path (button, Enter, batch).
 */
export function closeLegacyStarts(api: LegacyJobsApi): LegacyJobsApi {
  const closed = () => Promise.reject(new ApiError({ kind: "conflict", status: null, code: "v1_mode_legacy_start_disabled" }));
  return { ...api, startJob: closed, cancelJob: closed };
}

/** Everything the views use, created once and outside Vue (mode switches keep it). */
export interface AppCore {
  host: HostBridge;
  /** Chosen by the server page only (`utteran-api-mode` meta), fixed for the page lifetime. */
  apiMode: ApiMode;
  v1: V1Core | null;
  legacy: LegacyApi;
  shell: ShellController;
  environment: EnvironmentController;
  settings: SettingsController;
  token: TokenController;
  logs: LogFolderController;
  wizard: WizardStatusController;
  jobsApi: LegacyJobsApi;
  /** S3-A: one shared form (S3-B1: the common settings) and one job tracker for Normal and Expert (created once). */
  transcription: TranscriptionFormController;
  job: JobController;
  /** S3-B1: the one door to `POST /api/jobs`, shared by the single start and the batch. */
  gate: AdmissionGate;
  /** S3-B1: the one input list (Normal's current file = its primary row). */
  inputs: InputCollectionController;
  batch: BatchSubmitter;
  /** App exit: stop polling/SSE and listeners. */
  dispose(): void;
  /** Start-up reads: settings first, then the environment for the saved default profile. */
  start(): Promise<void>;
}

export function createCore(deps: { fetch: FetchLike; host: HostBridge; eventSource?: EventSourceFactory; apiMode?: ApiMode }): AppCore {
  const apiMode: ApiMode = deps.apiMode === "v1-dev" ? "v1-dev" : "legacy";
  const http = createHttpClient({ fetch: deps.fetch });
  const legacy = createLegacyApi(http);
  const legacyJobs = createLegacyJobsApi(http);
  const jobsApi = apiMode === "v1-dev" ? closeLegacyStarts(legacyJobs) : legacyJobs;
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
  const gate = new AdmissionGate(jobsApi);
  const job = new JobController(jobsApi, { eventSource: deps.eventSource, gate });
  const inputs = new InputCollectionController(gate, transcription.store, environment.store);
  // Each accepted legacy POST may rewrite the server's default folders (`remember_directories`).
  // After a batch, re-read the settings (a GET; unsaved settings edits are rebased, never lost)
  // so the screen shows the server's current values. Nothing is PATCHed back.
  const batch = new BatchSubmitter(gate, inputs, { afterRun: () => void settings.load() });
  let v1: V1Core | null = null;
  if (apiMode === "v1-dev") {
    const api = createV1ResourcesApi(http);
    const library = new V1LibraryController(api);
    v1 = { api, library, job: new V1SingleJobController(api, library.store, inputs.store, transcription.store) };
  }
  return {
    host: deps.host,
    apiMode,
    v1,
    jobsApi,
    transcription,
    job,
    gate,
    inputs,
    batch,
    dispose() {
      v1?.job.dispose();
      batch.dispose();
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
      // v1 reads only (GET): no Preset/Policy is created and the queue is not touched.
      const v1Reads = v1 ? Promise.all([v1.library.load(), v1.job.loadQueue(), v1.job.loadRecent()]) : Promise.resolve();
      await settings.load();
      if (environment.store.get().load.phase === "idle") {
        await environment.selectDefault(settings.store.get().saved?.defaultProfile ?? null);
      }
      await v1Reads;
    },
  };
}
