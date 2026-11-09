/**
 * Typed client for the legacy transcription job routes (UI-3 S3-A).
 * Contract source: src/utteran_gui/api.py (`TranscriptionPayload`, `/api/jobs*`, `/api/queue`),
 * jobs.py (`JobManager._snapshot`, status set, synthetic `done`) and operation_queue.py
 * (code is the authority). `/api/v1/*` is never called from here.
 *
 * Kept (allowlist): job id, status, timestamps, exit code, stalled flag, the number of outputs and
 * their *file names* (no folders), a fixed guidance key and the current stage/ratio. Dropped on
 * purpose: `logs` (raw stdout/stderr, may contain transcripts or paths), raw `events` (messages,
 * error texts, paths in `output_written`), output folders, `settings_anchor`. Queue `label`s (input
 * file names) are not kept either.
 *
 * The legacy job and queue live only in the GUI process memory: an app restart forgets them
 * (`GET /api/jobs/{id}` then answers 404). Nothing here is a durable B7 job or snapshot.
 */
import { list, record, ShapeError } from "./guards";
import type { HttpClient } from "./http";
import { ENVIRONMENT_TIMEOUT_MS, type ProfileName } from "./legacy";
import { type JobLanguage, type OutputFormat, safeText } from "./legacyEnvironment";

export const JOB_STATUSES = ["starting", "running", "completed", "failed", "cancelled"] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];
export type TerminalStatus = "completed" | "failed" | "cancelled";
export function isTerminal(status: JobStatus): status is TerminalStatus {
  return status === "completed" || status === "failed" || status === "cancelled";
}

/** The five stages the legacy GUI knows; anything else is shown as "other" (never guessed). */
export const STAGES = ["audio", "asr", "diarization", "merge", "export"] as const;
export type JobStage = (typeof STAGES)[number] | "other";

/** `jobs.guidance_for()` keys; an unknown key becomes "other". */
export const GUIDANCE_KEYS = [
  "cancelled",
  "native",
  "memory",
  "license",
  "token",
  "model",
  "ffmpeg",
  "general",
  "configuration",
  "dependency",
  "input",
  "partial",
] as const;
export type GuidanceKey = (typeof GUIDANCE_KEYS)[number] | "other";

export interface JobProgress {
  /** Stage of the latest stage/progress event; null = none reported yet. */
  stage: JobStage | null;
  /** That stage's own 0..1 ratio when the CLI reported one; never an overall estimate. */
  ratio: number | null;
}

export interface JobView {
  id: string;
  status: JobStatus;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  exitCode: number | null;
  /** Server: running with no output for 30 s (not a failure). */
  stalled: boolean;
  outputCount: number;
  /** File names only (no folder), display-cleaned, at most `MAX_OUTPUT_NAMES`. */
  outputNames: string[];
  guidance: GuidanceKey | null;
  progress: JobProgress;
  /** Highest event index in this answer (`events[].id`); used to drop older SSE events. */
  lastEventId: number;
}

export const MAX_OUTPUT_NAMES = 16;
/** `JobManager` ids are `uuid4().hex`. Anything else is refused (never put into a URL). */
const JOB_ID = /^[0-9a-f]{32}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T[0-9:.+\-Z]{1,40}$/;

export function isJobId(value: unknown): value is string {
  return typeof value === "string" && JOB_ID.test(value);
}

function timestamp(value: unknown): string | null {
  return typeof value === "string" && TIMESTAMP.test(value) ? value : null;
}

function obj(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function stage(value: unknown): JobStage | null {
  if (typeof value !== "string" || value === "") return null;
  return (STAGES as readonly string[]).includes(value) ? (value as JobStage) : "other";
}

function ratio(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}

function eventIndex(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** Last path segment of a server path, for display only (`/` and `\\` both separate). */
export function fileName(path: string): string | null {
  const parts = path.split(/[\\/]/).filter((p) => p !== "");
  return safeText(parts[parts.length - 1] ?? null);
}

const PROGRESS_EVENTS = new Set(["stage_start", "progress", "stage_done"]);

/** Stage/ratio from the newest stage or progress event (raw events are not kept). */
export function progressOf(events: unknown[]): JobProgress {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = obj(events[i]);
    if (!e || typeof e.event !== "string" || !PROGRESS_EVENTS.has(e.event)) continue;
    const s = stage(e.stage);
    if (s === null) continue;
    return { stage: s, ratio: e.event === "progress" ? ratio(e.ratio) : e.event === "stage_done" ? 1 : null };
  }
  return { stage: null, ratio: null };
}

function guidance(value: unknown): GuidanceKey | null {
  const r = obj(value);
  if (!r) return null;
  return (GUIDANCE_KEYS as readonly unknown[]).includes(r.key) ? (r.key as GuidanceKey) : "other";
}

/**
 * `GET /api/jobs/{id}` and `POST /api/jobs[/{id}/cancel]` → `JobManager.snapshot()`.
 * Fail closed: a wrong/missing id, an unknown status or a non-list `outputs`/`events` makes the
 * whole answer malformed (it is then *not* applied; the previous state stays).
 */
export function parseJobSnapshot(raw: unknown, expectedId?: string): JobView {
  const r = record(raw, "job");
  if (!isJobId(r.id) || (expectedId !== undefined && r.id !== expectedId)) throw new ShapeError("job.id");
  if (!(JOB_STATUSES as readonly unknown[]).includes(r.status)) throw new ShapeError("job.status");
  const outputs = list(r.outputs, "job.outputs").filter((o): o is string => typeof o === "string");
  const events = list(r.events, "job.events");
  let lastEventId = -1;
  for (const e of events) lastEventId = Math.max(lastEventId, eventIndex(obj(e)?.id) ?? -1);
  const exit = r.exit_code;
  return {
    id: r.id,
    status: r.status as JobStatus,
    createdAt: timestamp(r.created_at),
    startedAt: timestamp(r.started_at),
    finishedAt: timestamp(r.finished_at),
    exitCode: typeof exit === "number" && Number.isSafeInteger(exit) ? exit : null,
    stalled: r.stalled === true,
    outputCount: outputs.length,
    outputNames: outputs.flatMap((o) => fileName(o) ?? []).slice(0, MAX_OUTPUT_NAMES),
    guidance: guidance(r.guidance),
    progress: progressOf(events),
    lastEventId,
  };
}

/** SSE event names that are read (all others are ignored; none of them decides an outcome). */
export const SSE_EVENTS = ["stage_start", "progress", "stage_done", "output_written", "warning", "error", "done"] as const;
export type SseEventName = (typeof SSE_EVENTS)[number];

export interface JobEvent {
  /** `events[].id` (index in the job's event list); the SSE stream restarts at 0 on reconnect. */
  id: number;
  event: SseEventName;
  stage: JobStage | null;
  ratio: number | null;
}

/** One SSE `data:` line. Returns null for anything unreadable or not in `SSE_EVENTS`. */
export function parseJobEvent(data: unknown): JobEvent | null {
  if (typeof data !== "string" || data.length > 65_536) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  const r = obj(raw);
  const id = eventIndex(r?.id);
  if (!r || id === null || !(SSE_EVENTS as readonly unknown[]).includes(r.event)) return null;
  const name = r.event as SseEventName;
  return { id, event: name, stage: stage(r.stage), ratio: name === "progress" ? ratio(r.ratio) : name === "stage_done" ? 1 : null };
}

export type QueueItemStatus = "waiting" | "running" | "completed" | "failed" | "cancelled" | "unknown";

export interface QueueItem {
  id: string;
  transcription: boolean;
  status: QueueItemStatus;
  position: number | null;
}

const QUEUE_STATUSES: readonly QueueItemStatus[] = ["waiting", "running", "completed", "failed", "cancelled"];

/**
 * `GET /api/queue` (legacy in-memory operation queue). Reference only: it shows what this GUI
 * process accepted and in which order. It is NOT a B7 queue revision and cannot prove that a
 * particular lost POST started (items carry no request identity; labels are file names, dropped).
 */
export function parseQueue(raw: unknown): QueueItem[] {
  return list(raw, "queue").flatMap((entry) => {
    const r = obj(entry);
    if (!r || typeof r.id !== "string" || r.id.length > 64) return [];
    const status = (QUEUE_STATUSES as readonly unknown[]).includes(r.status) ? (r.status as QueueItemStatus) : "unknown";
    const position = typeof r.position === "number" && Number.isSafeInteger(r.position) && r.position > 0 ? r.position : null;
    return [{ id: r.id, transcription: r.kind === "transcription", status, position }];
  });
}

/** What S3-A submits. Paths are kept in memory only (never stored, logged or put in a URL). */
export interface TranscriptionRequest {
  inputPath: string;
  outputDir: string;
  profile: ProfileName;
  asrBackend: string;
  asrModel: string;
  asrDevice: string;
  language: JobLanguage;
  formats: OutputFormat[];
}

/**
 * The exact `TranscriptionPayload` body. Speaker diarization is explicitly off in S3-A (the API
 * default `true` would need a token and consent, handled in S3-B/S4). `resume_mode="resume"` and
 * `recursive=false` keep the legacy meaning; include/exclude and speaker counts are not sent.
 */
export function transcriptionBody(request: TranscriptionRequest): Record<string, unknown> {
  return {
    input_path: request.inputPath,
    output_dir: request.outputDir,
    profile: request.profile,
    asr_backend: request.asrBackend,
    asr_model: request.asrModel,
    asr_device: request.asrDevice,
    diarization_enabled: false,
    language: request.language,
    formats: [...request.formats],
    resume_mode: "resume",
    recursive: false,
  };
}

/**
 * `POST /api/jobs` first re-reads the environment for the profile on the server (the same CLI
 * calls as `GET /api/environment`), so it gets the same 240 s limit.
 */
export const START_TIMEOUT_MS = ENVIRONMENT_TIMEOUT_MS;
export const CANCEL_TIMEOUT_MS = 30_000;

export interface LegacyJobsApi {
  /** Non-idempotent; never resent automatically (the server has no idempotency key). */
  startJob(request: TranscriptionRequest): Promise<JobView>;
  getJob(id: string, signal?: AbortSignal): Promise<JobView>;
  /** 202 = cancellation *requested*; the job is cancelled only when GET says so. */
  cancelJob(id: string): Promise<JobView>;
  getQueue(signal?: AbortSignal): Promise<QueueItem[]>;
}

export function jobEventsPath(id: string): string {
  if (!isJobId(id)) throw new Error("invalid job id");
  return `/api/jobs/${id}/events`;
}

function jobPath(id: string, suffix = ""): string {
  if (!isJobId(id)) throw new Error("invalid job id");
  return `/api/jobs/${id}${suffix}`;
}

export function createLegacyJobsApi(http: HttpClient): LegacyJobsApi {
  return {
    async startJob(request) {
      const body = transcriptionBody(request);
      return (await http.request({ method: "POST", path: "/api/jobs", body, timeoutMs: START_TIMEOUT_MS }, (raw) => parseJobSnapshot(raw))).data;
    },
    async getJob(id, signal) {
      return (await http.request({ method: "GET", path: jobPath(id), signal }, (raw) => parseJobSnapshot(raw, id))).data;
    },
    async cancelJob(id) {
      return (
        await http.request({ method: "POST", path: jobPath(id, "/cancel"), timeoutMs: CANCEL_TIMEOUT_MS }, (raw) => parseJobSnapshot(raw, id))
      ).data;
    },
    async getQueue(signal) {
      return (await http.request({ method: "GET", path: "/api/queue", signal }, parseQueue)).data;
    },
  };
}
