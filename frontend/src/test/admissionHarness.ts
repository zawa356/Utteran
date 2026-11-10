import { ApiError } from "../api/errors";
import type { LegacyEnvironment } from "../api/legacy";
import { type JobView, type LegacyJobsApi, parseJobSnapshot, type TranscriptionRequest, transcriptionBody } from "../api/legacyJobs";
import { AdmissionGate } from "../domain/admission";
import { BatchSubmitter } from "../domain/batchSubmission";
import type { EnvironmentState } from "../domain/environment";
import { InputCollectionController } from "../domain/inputCollection";
import { idleOperation } from "../domain/operation";
import type { SettingsState } from "../domain/settings";
import { Store } from "../domain/store";
import { TranscriptionFormController } from "../domain/transcription";
import { deferred } from "./fakeFetch";
import { environment, settings } from "./legacyFixtures";

/** Synthetic paths only (assembled at runtime). */
export const MEDIA = ["", "synthetic", "media"].join("/");
export const OUT = ["", "synthetic", "out"].join("/");
export const media = (name: string) => `${MEDIA}/${name}`;

export function envState(data: LegacyEnvironment | null): EnvironmentState {
  return {
    requestedProfile: data?.activeProfile ?? null,
    refresh: false,
    load: { ...idleOperation, phase: data ? "success" : "idle" },
    data,
    dataFor: data ? { profile: data.activeProfile, refresh: false, followsDefault: false, receivedAt: 0, savedDefault: null } : null,
    startedAt: null,
    readiness: "unverified",
    staleDiscarded: 0,
    abandoned: 0,
    followsDefault: false,
  };
}

export function settingsState(defaultOutputDir = OUT): SettingsState {
  return {
    load: idleOperation,
    save: idleOperation,
    saved: settings({ defaultOutputDir }),
    draft: null,
    picking: null,
    pickError: null,
    unconfirmedSave: null,
    reconcile: idleOperation,
    reconciled: null,
  };
}

/** A second backend so backend/model/device overrides can be exercised. */
export function twoBackends(): LegacyEnvironment {
  const env = environment("cuda");
  env.jobOptions = {
    ...env.jobOptions,
    asr: [
      ...env.jobOptions.asr,
      { id: "synthetic-cpp", label: "synthetic cpp", models: [{ id: "cpp-only", label: "cpp only" }], devices: [{ id: "cpu", label: "cpu" }] },
    ],
  };
  return env;
}

export const jobId = (n: number) => n.toString(16).padStart(32, "0");
export const accepted = (id: string): JobView => parseJobSnapshot({ id, status: "starting", outputs: [], events: [], logs: [], guidance: null });

/** Each POST waits until the test answers it; the number in flight is recorded. */
export function controlledJobs() {
  const posts: { request: TranscriptionRequest; body: Record<string, unknown>; answer: ReturnType<typeof deferred<JobView>> }[] = [];
  let inFlight = 0;
  let maxInFlight = 0;
  const api: LegacyJobsApi = {
    startJob(request) {
      const answer = deferred<JobView>();
      // The client sends `transcriptionBody(request)`; record it as sent.
      posts.push({ request, body: JSON.parse(JSON.stringify(transcriptionBody(request))), answer });
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return answer.promise.finally(() => (inFlight -= 1));
    },
    getJob: () => new Promise<JobView>(() => {}),
    cancelJob: () => Promise.reject(new Error("unused")),
    getQueue: () => Promise.reject(new Error("unused")),
  };
  return {
    api,
    posts,
    maxInFlight: () => maxInFlight,
    acceptAt: (i: number) => posts[i]!.answer.resolve(accepted(jobId(i + 1))),
    refuseAt: (i: number, status = 400) => posts[i]!.answer.reject(new ApiError({ kind: status === 409 ? "conflict" : "validation", status, detail: `refused ${media("x.wav")}` })),
    loseAt: (i: number) => posts[i]!.answer.reject(new ApiError({ kind: "network", outcomeUnknown: true, retryable: true })),
  };
}

export const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** Gate + common form + list + batch over stores the test controls. */
export function b1Harness(data: LegacyEnvironment | null = environment("cuda"), defaultOutputDir = OUT) {
  const jobs = controlledJobs();
  const env = new Store<EnvironmentState>(envState(data));
  const set = new Store<SettingsState>(settingsState(defaultOutputDir));
  const gate = new AdmissionGate(jobs.api);
  const form = new TranscriptionFormController(env, set);
  const inputs = new InputCollectionController(gate, form.store, env);
  const afterRun: unknown[] = [];
  const batch = new BatchSubmitter(gate, inputs, { afterRun: (c) => afterRun.push(c) });
  return { jobs, env, set, gate, form, inputs, batch, afterRun };
}
