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
import { V1BatchController } from "./v1/batchJobs";
import { QueueMutationGate } from "./v1/queueGate";
import { V1JobBoardController } from "./v1/jobBoard";
import { V1DeletionController } from "./v1/jobDeletion";
import { V1LibraryController } from "./v1/library";
import { V1SingleJobController } from "./v1/singleJob";
import { WizardStatusController } from "./wizard";

/** S5-A v1 single-job path; present only when the server rendered `v1-dev` (`--next-ui-v1`). */
export interface V1Core {
  api: V1ResourcesApi;
  queueGate: QueueMutationGate;
  library: V1LibraryController;
  job: V1SingleJobController;
  /** S5-B: the selected rows as one atomic B7 batch (same file ids as `job`). */
  batch: V1BatchController;
  rerun: V1BatchController;
  /** Every durable job as logical jobs: queue, history, hide/show, artifact view. */
  board: V1JobBoardController;
  /** C2c: history-only / history-and-outputs deletion of one internal job (plan → approval → execute). */
  deletion: V1DeletionController;
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
    const queueGate = new QueueMutationGate();
    const job = new V1SingleJobController(api, library.store, inputs.store, transcription.store, { gate: queueGate });
    const batch = new V1BatchController(api, library.store, inputs.store, transcription.store, { gate: queueGate, fileIdFor: (rowId) => job.fileIdFor(rowId) });
    const board = new V1JobBoardController(api, { gate: queueGate, onVisibilitySettled: () => job.refreshRecentIfLoaded() });
    // After a settled deletion every list is read from the server again (nothing is removed locally).
    const deletion = new V1DeletionController(api, {
      gate: queueGate,
      onSettled: () => {
        void board.refresh();
        job.refreshRecentIfLoaded();
      },
    });
    const rerun = new V1BatchController(api, library.store, inputs.store, transcription.store, {
      gate: queueGate,
      fileIdFor: (rowId) => job.fileIdFor(rowId),
      onRerunSettled: () => {
        void board.refresh();
        void job.loadQueue();
        job.refreshRecentIfLoaded();
      },
    });
    v1 = { api, queueGate, library, job, batch, rerun, board, deletion };
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
      v1?.batch.dispose();
      v1?.rerun.dispose();
      v1?.board.dispose();
      v1?.deletion.dispose();
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
