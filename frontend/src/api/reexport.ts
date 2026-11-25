/**
 * E-C: typed client for the re-export routes (`--next-ui-v1` only). Contract authority: code
 * first, `src/utteran_gui/reexport_api.py` / `reexport_service.py`, spec
 * `docs/architecture/ui3/e-c-reexport.md`.
 *
 * - `getSource` reads only (availability with a fixed refusal code, defaults, displayed labels).
 * - `create` sends one intent once: `If-Match` = the job revision and an Idempotency-Key. It never
 *   resends; after an unknown outcome the caller resends the *same* intent (same key and body)
 *   only on an explicit recheck. A new re-export is always a new intent (new key).
 * - Labels and the output directory exist only in memory and authenticated bodies.
 */
import { ApiError } from "./errors";
import { bool, int, list, oneOf, optStr, record, ShapeError, str } from "./guards";
import type { HttpClient } from "./http";
import { IDEMPOTENCY_KEY_PATTERN, type MutationIntent } from "./idempotency";
import { ifMatch } from "./v1";
import { OUTPUT_FORMATS_V1, type OutputFormatV1 } from "./v1Resources";

export const REEXPORT_TIMEOUT_MS = 120_000;
const JOB_ID = /^job_[0-9a-f]{32}$/;
const REEXPORT_ID = /^rex_[0-9a-f]{32}$/;
const SHA = /^[0-9a-f]{64}$/;
export type Newline = "lf" | "crlf";
export const REEXPORT_STATES = ["pending", "completed", "failed", "interrupted"] as const;
export type ReexportState = (typeof REEXPORT_STATES)[number];

export interface ReexportSource {
  jobId: string;
  jobRevision: number;
  available: boolean;
  refusal: string | null;
  source: {
    sha256: string;
    size: number;
    newline: Newline;
    segments: number;
    words: number;
    speakers: string[];
    diarization: boolean;
  } | null;
  defaults: { formats: OutputFormatV1[]; showSpeaker: boolean; srtBom: boolean; newline: Newline };
  originalLabelsApplied: boolean;
  defaultParent: string;
  limits: { maxSourceBytes: number; maxLabel: number; maxSpeakers: number };
}

export interface ReexportRecord {
  id: string;
  jobId: string;
  jobRevision: number;
  state: ReexportState;
  errorCode: string | null;
  partial: boolean;
  createdAt: string;
  updatedAt: string;
  formats: OutputFormatV1[];
  options: { showSpeaker: boolean; srtBom: boolean; newline: Newline; speakerLabelCount: number; explicitParent: boolean };
  sourceSha256: string;
  directory: string | null;
  files: { format: OutputFormatV1; size: number; sha256: string }[];
  locationVerified: boolean;
}

export interface ReexportBody {
  schema_version: 1;
  source_sha256: string;
  formats: OutputFormatV1[];
  show_speaker: boolean;
  srt_bom: boolean;
  newline: Newline;
  speaker_labels: Record<string, string>;
  output_parent: string | null;
}

function version(r: Record<string, unknown>, where: string): void {
  if (r.schema_version !== 1) throw new ShapeError(`${where}.schema_version`);
}
function match(value: unknown, pattern: RegExp, where: string): string {
  const text = str(value, where);
  if (!pattern.test(text)) throw new ShapeError(where);
  return text;
}
const formats = (value: unknown, where: string) => list(value, where).map((f, i) => oneOf(f, OUTPUT_FORMATS_V1, `${where}[${i}]`));
const newline = (value: unknown, where: string) => oneOf(value, ["lf", "crlf"] as const, where);

export function parseSource(raw: unknown): ReexportSource {
  const r = record(raw, "reexportSource");
  version(r, "reexportSource");
  const d = record(r.defaults, "reexportSource.defaults");
  const l = record(r.limits, "reexportSource.limits");
  const available = bool(r.available, "reexportSource.available");
  const refusal = optStr(r.refusal);
  let source: ReexportSource["source"] = null;
  if (r.source !== null) {
    const s = record(r.source, "reexportSource.source");
    source = {
      sha256: match(s.sha256, SHA, "reexportSource.source.sha256"),
      size: int(s.size, "reexportSource.source.size"),
      newline: newline(s.newline, "reexportSource.source.newline"),
      segments: int(s.segments, "reexportSource.source.segments"),
      words: int(s.words, "reexportSource.source.words"),
      speakers: list(s.speakers, "reexportSource.source.speakers").map((x, i) => str(x, `reexportSource.source.speakers[${i}]`)),
      diarization: bool(s.diarization, "reexportSource.source.diarization"),
    };
  }
  // Exactly one of: available with a source, or refused with a code.
  if (available !== (source !== null) || available === (refusal !== null)) throw new ShapeError("reexportSource.available");
  return {
    jobId: match(r.job_id, JOB_ID, "reexportSource.job_id"),
    jobRevision: int(r.job_revision, "reexportSource.job_revision"),
    available,
    refusal,
    source,
    defaults: {
      formats: formats(d.formats, "reexportSource.defaults.formats"),
      showSpeaker: bool(d.show_speaker, "reexportSource.defaults.show_speaker"),
      srtBom: bool(d.srt_bom, "reexportSource.defaults.srt_bom"),
      newline: newline(d.newline, "reexportSource.defaults.newline"),
    },
    originalLabelsApplied: bool(r.original_labels_applied, "reexportSource.original_labels_applied"),
    defaultParent: str(r.default_parent, "reexportSource.default_parent"),
    limits: {
      maxSourceBytes: int(l.max_source_bytes, "reexportSource.limits.max_source_bytes"),
      maxLabel: int(l.max_label, "reexportSource.limits.max_label"),
      maxSpeakers: int(l.max_speakers, "reexportSource.limits.max_speakers"),
    },
  };
}

export function parseRecord(raw: unknown): ReexportRecord {
  const r = record(raw, "reexport");
  version(r, "reexport");
  const o = record(r.options, "reexport.options");
  return {
    id: match(r.id, REEXPORT_ID, "reexport.id"),
    jobId: match(r.job_id, JOB_ID, "reexport.job_id"),
    jobRevision: int(r.job_revision, "reexport.job_revision"),
    state: oneOf(r.state, REEXPORT_STATES, "reexport.state"),
    errorCode: optStr(r.error_code),
    partial: bool(r.partial, "reexport.partial"),
    createdAt: str(r.created_at, "reexport.created_at"),
    updatedAt: str(r.updated_at, "reexport.updated_at"),
    formats: formats(r.formats, "reexport.formats"),
    options: {
      showSpeaker: bool(o.show_speaker, "reexport.options.show_speaker"),
      srtBom: bool(o.srt_bom, "reexport.options.srt_bom"),
      newline: newline(o.newline, "reexport.options.newline"),
      speakerLabelCount: int(o.speaker_label_count, "reexport.options.speaker_label_count"),
      explicitParent: bool(o.explicit_parent, "reexport.options.explicit_parent"),
    },
    sourceSha256: match(r.source_sha256, SHA, "reexport.source_sha256"),
    directory: optStr(r.directory),
    files: list(r.files, "reexport.files").map((item, i) => {
      const f = record(item, `reexport.files[${i}]`);
      return {
        format: oneOf(f.format, OUTPUT_FORMATS_V1, `reexport.files[${i}].format`),
        size: int(f.size, `reexport.files[${i}].size`),
        sha256: match(f.sha256, SHA, `reexport.files[${i}].sha256`),
      };
    }),
    locationVerified: bool(r.location_verified, "reexport.location_verified"),
  };
}

export interface ReexportApi {
  getSource(jobId: string, signal?: AbortSignal): Promise<ReexportSource>;
  /** `POST /jobs/{id}/reexports`: 201 new, 200 the record this key already made. */
  create(jobId: string, expectedRevision: number, intent: MutationIntent<ReexportBody>): Promise<{ status: number; record: ReexportRecord }>;
  list(jobId: string, signal?: AbortSignal): Promise<ReexportRecord[]>;
  get(reexportId: string, signal?: AbortSignal): Promise<ReexportRecord>;
}

function jobId(value: string): string {
  if (!JOB_ID.test(value)) throw new ShapeError("job id");
  return value;
}

export function createReexportApi(http: HttpClient): ReexportApi {
  return {
    async getSource(id, signal) {
      const response = await http.request({ method: "GET", path: `/api/v1/jobs/${jobId(id)}/reexport-source`, signal, timeoutMs: REEXPORT_TIMEOUT_MS }, parseSource);
      if (response.data.jobId !== id) throw new ApiError({ kind: "malformed", status: response.status, retryable: true });
      return response.data;
    },
    async create(id, expectedRevision, intent) {
      if (!IDEMPOTENCY_KEY_PATTERN.test(intent.key)) throw new Error("idempotency key");
      intent.attempts += 1;
      intent.state = "sent";
      try {
        const response = await http.request(
          {
            method: "POST",
            path: `/api/v1/jobs/${jobId(id)}/reexports`,
            body: intent.body,
            headers: { "Idempotency-Key": intent.key, "If-Match": ifMatch(expectedRevision) },
            timeoutMs: REEXPORT_TIMEOUT_MS,
          },
          parseRecord,
        );
        if (response.data.jobId !== id) throw new ApiError({ kind: "malformed", status: response.status });
        intent.state = "accepted";
        return { status: response.status, record: response.data };
      } catch (error) {
        const e = error as { outcomeUnknown?: boolean; status?: number | null };
        intent.state = !e.outcomeUnknown && typeof e.status === "number" && e.status >= 400 && e.status < 500 ? "rejected" : "unknown";
        throw error;
      }
    },
    async list(id, signal) {
      const response = await http.request({ method: "GET", path: `/api/v1/jobs/${jobId(id)}/reexports`, signal }, (raw) => {
        const r = record(raw, "reexports");
        version(r, "reexports");
        return list(r.items, "reexports.items").map(parseRecord);
      });
      return response.data;
    },
    async get(reexportId, signal) {
      if (!REEXPORT_ID.test(reexportId)) throw new ShapeError("reexport id");
      const response = await http.request({ method: "GET", path: `/api/v1/reexports/${reexportId}`, signal }, parseRecord);
      return response.data;
    },
  };
}
