import type { DemoJob } from "./demo";
import type { SetupState, StageId } from "./setup";

/**
 * Sample logs and the diagnostics outline for the mock Tools dialogs (iteration 3, owner memo #12).
 * NOTHING is read from disk or the backend: the lines below are fixed synthetic examples. They show
 * the intended policy for the real feature: logs and diagnostics never carry input paths, audio,
 * transcript text or tokens — such values are replaced by placeholders before display/export.
 */
export type LogLevel = "INFO" | "WARN" | "ERROR";
export interface LogLine {
  time: string;
  level: LogLevel;
  source: string;
  /** i18n key of the message (texts are in i18n so ja/en both read naturally). */
  key: string;
  params?: Record<string, string | number>;
}

export const SYSTEM_LOG: readonly LogLine[] = [
  { time: "10:00:01", level: "INFO", source: "app", key: "log.sys.start", params: { version: "0.1.43" } },
  { time: "10:00:01", level: "INFO", source: "app", key: "log.sys.ui" },
  { time: "10:00:02", level: "INFO", source: "env", key: "log.sys.profile" },
  { time: "10:00:02", level: "WARN", source: "env", key: "log.sys.tokenMasked" },
  { time: "10:00:03", level: "INFO", source: "queue", key: "log.sys.queueReady" },
  { time: "10:04:18", level: "ERROR", source: "queue", key: "log.sys.statusFailed" },
  { time: "10:04:21", level: "INFO", source: "queue", key: "log.sys.statusRecovered" },
];

/** A synthetic job log derived from the demo job's state only (no file name, no text). */
export function jobLog(job: Pick<DemoJob, "id" | "state" | "stage" | "attempt" | "snapshot">): LogLine[] {
  const lines: LogLine[] = [
    { time: "10:01:00", level: "INFO", source: job.id, key: "log.job.accepted" },
    { time: "10:01:02", level: "INFO", source: job.id, key: "log.job.settings", params: { language: job.snapshot.language, formats: job.snapshot.formats.join(", ") } },
  ];
  if (job.state === "waiting") return lines;
  lines.push({ time: "10:01:05", level: "INFO", source: job.id, key: "log.job.preparing" });
  if (job.state === "preparing") return lines;
  if (job.stage === "asr" || job.stage === "diarization" || job.stage === "output" || job.state !== "running") {
    lines.push({ time: "10:01:20", level: "INFO", source: job.id, key: "log.job.asr" });
  }
  if (job.stage === "diarization" || job.stage === "output" || (job.state !== "running" && job.snapshot.diarization)) {
    lines.push({ time: "10:06:40", level: "INFO", source: job.id, key: "log.job.diarization" });
  }
  if (job.state === "failed") lines.push({ time: "10:07:12", level: "ERROR", source: job.id, key: "log.job.failed", params: { code: "demo_failure" } });
  if (job.state === "cancelled") lines.push({ time: "10:07:12", level: "WARN", source: job.id, key: "log.job.cancelled" });
  if (job.state === "succeeded") lines.push({ time: "10:08:30", level: "INFO", source: job.id, key: "log.job.done" });
  return lines;
}

/** Synthetic log of the (fictional) setup operation, derived from its stage states only. */
export function setupLog(s: Pick<SetupState, "stages" | "attempt" | "errorCode">): LogLine[] {
  const lines: LogLine[] = [{ time: "09:30:00", level: "INFO", source: "setup", key: "log.setup.approved", params: { attempt: Math.max(1, s.attempt) } }];
  let minute = 30;
  for (const st of s.stages) {
    if (st.status === "pending") continue;
    minute += 1;
    const time = `09:${String(minute).padStart(2, "0")}:00`;
    const stage = st.id as StageId;
    lines.push({ time, level: "INFO", source: "setup", key: "log.setup.stageStarted", params: { stage } });
    if (st.status === "done") lines.push({ time, level: "INFO", source: "setup", key: "log.setup.stageDone", params: { stage } });
    if (st.status === "failed") lines.push({ time, level: "ERROR", source: "setup", key: "log.setup.stageFailed", params: { stage, code: s.errorCode ?? "—" } });
    if (st.status === "interrupted") lines.push({ time, level: "WARN", source: "setup", key: "log.setup.stageInterrupted", params: { stage } });
  }
  return lines;
}

/** Log export (iteration 4, owner memo #17): what would be saved. Nothing is written. */
export type LogExportFormat = "log" | "txt";
export const LOG_EXPORT_FORMATS: readonly LogExportFormat[] = ["log", "txt"];

/** Suggested file name; the date is the synthetic log date, not the clock. */
export function logFileName(kind: "system" | "job" | "setup", format: LogExportFormat, jobId?: string): string {
  const stem = kind === "job" && jobId ? `utteran-${jobId}` : `utteran-${kind}`;
  return `${stem}-20261105.${format}`;
}

export const DIAGNOSTICS_FILE_NAME = "utteran-diagnostics-20261105.zip";

/** What a diagnostics export would contain vs. leave out (i18n keys; behind "details" in the dialog). */
export const DIAGNOSTICS_INCLUDED = ["diag.in.version", "diag.in.os", "diag.in.env", "diag.in.logs", "diag.in.settings"] as const;
export const DIAGNOSTICS_EXCLUDED = ["diag.out.paths", "diag.out.media", "diag.out.text", "diag.out.tokens", "diag.out.prompts"] as const;

const WINDOWS_PATH = /\b[A-Za-z]:\\[^\s"'<>|]+/g;
const UNIX_HOME_PATH = /(?:\/home|\/Users|\/root)\/[^\s"'<>|]+/g;
const HF_TOKEN = /\bhf_[A-Za-z0-9]{8,}\b/g;

/**
 * The masking rule the real export must apply to free text before it leaves the app. Shown here
 * as a tested function so the policy is concrete; the prototype itself never handles real values.
 */
export function maskForDiagnostics(text: string): string {
  return text.replace(WINDOWS_PATH, "<path>").replace(UNIX_HOME_PATH, "<path>").replace(HF_TOKEN, "<token>");
}
