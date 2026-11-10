import type { ApiError } from "../api/errors";
import { fileName, type TranscriptionRequest } from "../api/legacyJobs";
import type { AdmissionGate } from "./admission";
import type { InputCollectionController } from "./inputCollection";
import { Store } from "./store";
import type { FormIssue } from "./transcription";

/**
 * Staged "batch" submission over the legacy single-job `POST /api/jobs` (UI-3 S3-B1).
 *
 * This is **not** a transaction: the legacy API has no batch endpoint and no idempotency key.
 * "Batch" = this screen sends N separate POSTs, one after another, through the shared
 * `AdmissionGate` (single-flight + a lease so the single start cannot slip in between).
 *
 * - All targets (selected, never-sent rows, in list order) are checked first; one problem → no
 *   POST at all, with a reason per file.
 * - Every request and body is frozen when the batch starts (effective output folder included),
 *   so later edits of the common settings, the list, or a server-side default change never alter
 *   what the remaining POSTs send.
 * - 202 + job id = accepted (not running, not succeeded). A received 4xx = refused: the batch
 *   stops by default; accepted rows stay accepted. No answer / unreadable 2xx / 5xx = unknown:
 *   the batch stops at once, nothing is resent or "reconciled" automatically.
 * - Stop never aborts the request in flight; it stops the not-yet-sent rest after that answer.
 *   Cancelling an accepted job on the server is a separate operation (S3-A detail / S3-B2).
 * - The rows of a running batch are reserved in the gate: the list shows them as queued and
 *   locks them, so the only way to keep them from being sent is Stop (PR #43 P2-2).
 * - Continuing later is a new batch over rows that were never sent (accepted/unknown rows are
 *   never targets; a refused row only after an explicit reset).
 */

export type BatchItemPhase = "waiting" | "pending" | "accepted" | "rejected" | "unknown" | "not_sent";
export type StopReason = "user" | "rejected" | "unknown" | "blocked" | "closed";

export interface BatchItem {
  readonly clientFileId: string;
  /** Display only (last path segment). */
  readonly fileName: string;
  readonly request: Readonly<TranscriptionRequest>;
  readonly phase: BatchItemPhase;
  readonly jobId: string | null;
  readonly error: ApiError | null;
}

export interface BatchRun {
  readonly id: number;
  readonly phase: "running" | "stopping" | "stopped" | "finished";
  readonly stopReason: StopReason | null;
  readonly items: readonly BatchItem[];
}

export interface PreflightFailure {
  clientFileId: string;
  fileName: string;
  issues: FormIssue[];
  sameInput: boolean;
}

export type BatchBlock = "nothing_selected" | "busy" | "running" | "invalid";

export interface BatchState {
  run: BatchRun | null;
  /** Why the last start request sent nothing. */
  preflight: { block: BatchBlock; failures: PreflightFailure[] } | null;
}

export interface BatchCounts {
  accepted: number;
  rejected: number;
  unknown: number;
  /** Not sent in this run (waiting, being sent, or stopped before sending). */
  notSent: number;
  pending: number;
}

export function countItems(items: readonly BatchItem[]): BatchCounts {
  const c: BatchCounts = { accepted: 0, rejected: 0, unknown: 0, notSent: 0, pending: 0 };
  for (const item of items) {
    if (item.phase === "accepted") c.accepted += 1;
    else if (item.phase === "rejected") c.rejected += 1;
    else if (item.phase === "unknown") c.unknown += 1;
    else if (item.phase === "pending") c.pending += 1;
    else c.notSent += 1;
  }
  return c;
}

function frozenRequest(request: TranscriptionRequest): Readonly<TranscriptionRequest> {
  return Object.freeze({ ...request, formats: Object.freeze([...request.formats]) as unknown as TranscriptionRequest["formats"] });
}

export class BatchSubmitter {
  readonly store = new Store<BatchState>({ run: null, preflight: null });
  private runId = 0;
  private stopRequested = false;
  private closed = false;

  constructor(
    private readonly gate: AdmissionGate,
    private readonly inputs: InputCollectionController,
    private readonly hooks: { afterRun?: (counts: BatchCounts) => void } = {},
  ) {}

  /** The rows a start would send now: selected and never sent, in list order. */
  targets() {
    return this.inputs.selected().filter((row) => this.inputs.statusOf(row) === "not_submitted");
  }

  running(): boolean {
    const phase = this.store.get().run?.phase;
    return phase === "running" || phase === "stopping";
  }

  /** Check everything, freeze everything, then send one by one. Resolves when the run ends. */
  async start(): Promise<boolean> {
    if (this.closed) return false;
    if (this.running()) return this.refuse("running");
    const targets = this.targets();
    if (targets.length === 0) return this.refuse("nothing_selected");
    const failures: PreflightFailure[] = [];
    const requests: { clientFileId: string; request: Readonly<TranscriptionRequest>; name: string }[] = [];
    for (const row of targets) {
      const evaluation = this.inputs.evaluateRow(row);
      const name = fileName(row.inputPath) ?? "?";
      if (evaluation.request === null) failures.push({ clientFileId: row.clientFileId, fileName: name, issues: evaluation.issues, sameInput: evaluation.sameInput });
      else requests.push({ clientFileId: row.clientFileId, request: frozenRequest(evaluation.request), name });
    }
    if (failures.length > 0) return this.refuse("invalid", failures);
    const lease = this.gate.acquireLease();
    if (lease === null) return this.refuse("busy");
    // Reserved rows are locked in the list (not removable, not editable) until the run ends, so
    // the list never hides a row whose frozen request is still going to be sent (PR #43 P2-2).
    this.gate.reserve(lease, requests.map((r) => r.clientFileId));

    const id = ++this.runId;
    this.stopRequested = false;
    const items: BatchItem[] = requests.map((r) =>
      Object.freeze({ clientFileId: r.clientFileId, fileName: r.name, request: r.request, phase: "waiting" as const, jobId: null, error: null }),
    );
    this.store.set({ preflight: null, run: { id, phase: "running", stopReason: null, items } });
    let stopReason: StopReason | null = null;
    try {
      for (let index = 0; index < items.length; index += 1) {
        if (this.closed) stopReason = "closed";
        else if (this.stopRequested) stopReason = "user";
        if (stopReason !== null) break;
        const item = items[index]!;
        this.setItem(id, index, { phase: "pending" });
        const result = await this.gate.admit(item.request, { clientFileId: item.clientFileId, origin: "batch", lease });
        if (result.kind === "accepted") {
          this.setItem(id, index, { phase: "accepted", jobId: result.job.id });
          continue;
        }
        if (result.kind === "blocked") {
          this.setItem(id, index, { phase: "not_sent" });
          stopReason = "blocked";
        } else {
          this.setItem(id, index, { phase: result.kind, error: result.error });
          stopReason = result.kind;
        }
        break;
      }
    } finally {
      this.gate.releaseLease(lease);
    }
    const run = this.store.get().run;
    if (run && run.id === id) {
      const finalItems = run.items.map((i) => (i.phase === "waiting" ? Object.freeze({ ...i, phase: "not_sent" as const }) : i));
      this.store.patch({ run: { ...run, items: finalItems, phase: stopReason === null ? "finished" : "stopped", stopReason } });
      const counts = countItems(finalItems);
      if (counts.accepted > 0 && !this.closed) this.hooks.afterRun?.(counts);
    }
    return stopReason === null;
  }

  /** Stop the not-yet-sent rest after the request in flight answers (it is never aborted). */
  requestStop(): void {
    const run = this.store.get().run;
    if (!run || run.phase !== "running") return;
    this.stopRequested = true;
    this.store.patch({ run: { ...run, phase: "stopping" } });
  }

  /** Hide the last run's summary (display only; accepted jobs and the input guard stay). */
  dismiss(): void {
    if (this.running()) return;
    this.store.set({ run: null, preflight: null });
  }

  clearPreflight(): void {
    if (this.store.get().preflight !== null) this.store.patch({ preflight: null });
  }

  /** App exit: the loop sends nothing more after the answer in flight. */
  dispose(): void {
    this.closed = true;
    this.stopRequested = true;
  }

  private refuse(block: BatchBlock, failures: PreflightFailure[] = []): false {
    this.store.patch({ preflight: { block, failures } });
    return false;
  }

  private setItem(runId: number, index: number, patch: Partial<Pick<BatchItem, "phase" | "jobId" | "error">>): void {
    const run = this.store.get().run;
    if (!run || run.id !== runId) return;
    const items = run.items.map((item, i) => (i === index ? Object.freeze({ ...item, ...patch }) : item));
    this.store.patch({ run: { ...run, items } });
  }
}
