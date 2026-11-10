import type { ApiError } from "../api/errors";
import { type JobView, type LegacyJobsApi, type TranscriptionRequest, transcriptionBody } from "../api/legacyJobs";
import { toApiError } from "./operation";
import { samePath } from "./paths";
import { Store } from "./store";

/**
 * The one door to the legacy, non-idempotent `POST /api/jobs` (UI-3 S3-B1).
 *
 * Both the S3-A single start (`JobController`) and the S3-B1 staged batch (`BatchSubmitter`) send
 * through this gate, so two parts of the screen can never have two start requests in flight:
 *
 * - **single-flight**: at most one `POST /api/jobs` at a time, whoever asks;
 * - **batch lease**: while a batch runs, a single start is refused even between two batch POSTs
 *   (the batch owns the door until it stops);
 * - **page-lifetime ledger**: every attempt is recorded once with its frozen request. Inputs that
 *   were accepted or may have been (unknown) stay guarded for the page lifetime. This is a client
 *   aid only — a reload, another window, another client or a symlink is not covered; the
 *   authoritative same-input rule belongs to the backend (S5).
 *
 * The ledger is memory only (no browser storage, URL, log or report) and is not a B7 durable job
 * snapshot or idempotency record.
 */

/** A frozen, in-memory copy of exactly what was sent. Not a durable (B7) job snapshot. */
export interface SubmittedSnapshot {
  readonly body: Readonly<Record<string, unknown>>;
  readonly request: Readonly<TranscriptionRequest>;
  /** Client clock (ms). */
  readonly submittedAt: number;
}

export type AdmissionPhase = "pending" | "accepted" | "rejected" | "unknown";
export type AdmissionOrigin = "single" | "batch";

export interface AdmissionRecord {
  /** Page-local attempt number (1, 2, …). */
  readonly seq: number;
  /** Input row this attempt was for (null = not tied to a row). */
  readonly clientFileId: string | null;
  readonly origin: AdmissionOrigin;
  readonly snapshot: SubmittedSnapshot;
  readonly phase: AdmissionPhase;
  /** Server job id (`uuid4().hex`), only once accepted. */
  readonly jobId: string | null;
  readonly error: ApiError | null;
}

export interface GateState {
  /** seq of the attempt whose answer is awaited (single-flight), or null. */
  inFlight: number | null;
  /** A batch holds the door between its POSTs. */
  lease: "batch" | null;
  records: readonly AdmissionRecord[];
}

export type GateBlock = "busy" | "batch" | "same_input";

export type AdmitResult =
  | { kind: "accepted"; record: AdmissionRecord; job: JobView }
  | { kind: "rejected" | "unknown"; record: AdmissionRecord; error: ApiError }
  | { kind: "blocked"; reason: GateBlock };

export interface LeaseToken {
  readonly id: number;
}

/**
 * A received answer that certainly created no job is a refusal: 4xx (validation, the start-time
 * re-check, a busy slot, session). Everything else may have created one and is `unknown`:
 * no answer (network/timeout), a 2xx whose body could not be read, a 5xx (the handler can fail
 * after `JobManager.start()`), or a client-side exception.
 */
export function classifyStartFailure(error: ApiError): "rejected" | "unknown" {
  if (error.outcomeUnknown) return "unknown";
  if (error.status !== null && error.status >= 400 && error.status < 500) return "rejected";
  return "unknown";
}

function freeze(request: TranscriptionRequest, now: number): SubmittedSnapshot {
  const frozen = Object.freeze({ ...request, formats: Object.freeze([...request.formats]) as unknown as TranscriptionRequest["formats"] });
  return Object.freeze({ body: Object.freeze(transcriptionBody(frozen)), request: frozen, submittedAt: now });
}

export class AdmissionGate {
  readonly store = new Store<GateState>({ inFlight: null, lease: null, records: [] });
  private seq = 0;
  private leaseId = 0;
  private activeLease: number | null = null;

  constructor(
    private readonly api: LegacyJobsApi,
    private readonly now: () => number = () => Date.now(),
  ) {}

  /** Inputs that were accepted or whose outcome is unknown (page lifetime). */
  isGuarded(inputPath: string): boolean {
    return this.store.get().records.some((r) => (r.phase === "accepted" || r.phase === "unknown" || r.phase === "pending") && samePath(r.snapshot.request.inputPath, inputPath));
  }

  /** Why a POST for this input would be refused now (null = it may be sent). */
  blockFor(inputPath: string | null, lease?: LeaseToken): GateBlock | null {
    const s = this.store.get();
    if (s.inFlight !== null) return "busy";
    if (this.activeLease !== null && lease?.id !== this.activeLease) return "batch";
    if (inputPath !== null && this.isGuarded(inputPath)) return "same_input";
    return null;
  }

  /** The batch takes the door; null when it is busy or already leased. */
  acquireLease(): LeaseToken | null {
    if (this.activeLease !== null || this.store.get().inFlight !== null) return null;
    this.activeLease = ++this.leaseId;
    this.store.patch({ lease: "batch" });
    return { id: this.activeLease };
  }

  releaseLease(token: LeaseToken): void {
    if (this.activeLease !== token.id) return;
    this.activeLease = null;
    this.store.patch({ lease: null });
  }

  /** Latest attempt for a row, ignoring attempts before `floor` (a reset refusal). */
  latestFor(clientFileId: string, floor = 0): AdmissionRecord | null {
    const records = this.store.get().records;
    for (let i = records.length - 1; i >= 0; i -= 1) {
      const r = records[i]!;
      if (r.clientFileId === clientFileId) return r.seq >= floor ? r : null;
    }
    return null;
  }

  /** Next attempt number (for a row's refusal reset). */
  nextSeq(): number {
    return this.seq + 1;
  }

  /**
   * Send one POST, exactly once. The request is copied and frozen before sending: later edits of
   * the form, the list or the settings never change what was (or is being) sent.
   */
  async admit(request: TranscriptionRequest, tag: { clientFileId?: string | null; origin: AdmissionOrigin; lease?: LeaseToken }): Promise<AdmitResult> {
    const block = this.blockFor(request.inputPath, tag.lease);
    if (block !== null) return { kind: "blocked", reason: block };
    const record: AdmissionRecord = Object.freeze({
      seq: ++this.seq,
      clientFileId: tag.clientFileId ?? null,
      origin: tag.origin,
      snapshot: freeze(request, this.now()),
      phase: "pending",
      jobId: null,
      error: null,
    });
    this.store.set((s) => ({ ...s, inFlight: record.seq, records: [...s.records, record] }));
    try {
      const job = await this.api.startJob(record.snapshot.request as TranscriptionRequest);
      const done = this.settle(record, { phase: "accepted", jobId: job.id, error: null });
      return { kind: "accepted", record: done, job };
    } catch (error) {
      const apiError = toApiError(error);
      const done = this.settle(record, { phase: classifyStartFailure(apiError), jobId: null, error: apiError });
      return { kind: done.phase as "rejected" | "unknown", record: done, error: apiError };
    }
  }

  private settle(record: AdmissionRecord, patch: Pick<AdmissionRecord, "phase" | "jobId" | "error">): AdmissionRecord {
    const done: AdmissionRecord = Object.freeze({ ...record, ...patch });
    this.store.set((s) => ({ ...s, inFlight: null, records: s.records.map((r) => (r.seq === record.seq ? done : r)) }));
    return done;
  }
}
