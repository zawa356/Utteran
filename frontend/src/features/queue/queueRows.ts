/**
 * Bundle D2-A: the rows of the Expert queue page, and the display-only search, filter and sort.
 *
 * Pure: the input is the board's queue (`core.v1.board`, already in B7 order) plus three readers.
 * Nothing here changes B7's order, invents a place, a stage or a name, or talks to the server.
 * A display sort only reorders a copy; `queueIndex` always keeps the board's (B7) order.
 */

import type { JobV1 } from "../../api/v1Resources";
import type { QueuePlace } from "../../domain/v1/jobBoard";
import type { LogicalJob } from "../../domain/v1/logicalJobs";

export const QUEUE_FILTERS = ["all", "running", "waiting", "held", "attention"] as const;
export type QueueFilter = (typeof QUEUE_FILTERS)[number];
export const QUEUE_SORTS = ["queue", "created", "status", "name"] as const;
export type QueueSort = (typeof QUEUE_SORTS)[number];

export interface QueueRow {
  /** Stable key of the logical job (the first internal job of the `retry_of` chain). */
  readonly rootId: string;
  /** Index in the board's queue = B7's order (active, then position, unknown last). Not B7's `position`. */
  readonly queueIndex: number;
  readonly group: LogicalJob;
  /** The internal job B7 runs first among the unfinished ones (`board.current`). */
  readonly current: JobV1;
  /** B7's place for `current`, or null when `GET /queue` did not report it. */
  readonly place: QueuePlace | null;
  /** Basename known locally in this session only; null = unknown (never derived from the server). */
  readonly name: string | null;
}

export function buildQueueRows(
  queue: readonly LogicalJob[],
  current: (group: LogicalJob) => JobV1,
  placeOf: (jobId: string) => QueuePlace | null,
  nameOf: (clientFileId: string) => string | null,
): QueueRow[] {
  return queue.map((group, queueIndex) => {
    const job = current(group);
    return { rootId: group.rootId, queueIndex, group, current: job, place: placeOf(job.id), name: nameOf(job.clientFileId) };
  });
}

export type PlaceKind = "active" | "waiting" | "unknown";

/** What B7 said about the row's place. A missing item or a null position is "unknown", never 0. */
export function placeOfRow(row: Pick<QueueRow, "place">): { kind: PlaceKind; position: number | null } {
  const place = row.place;
  if (place === null) return { kind: "unknown", position: null };
  if (place.active) return { kind: "active", position: null };
  if (place.position === null) return { kind: "unknown", position: null };
  return { kind: "waiting", position: place.position };
}

export function isHeld(row: QueueRow): boolean {
  return row.place?.held === true || row.current.held || row.current.status === "held";
}

/** Facts from the DTO that need a person to look (no inferred stage, no guessed cause). */
export function needsAttention(row: QueueRow): boolean {
  const job = row.current;
  return (
    job.status === "blocked" ||
    job.status === "waiting_user" ||
    job.recoveryRequired !== null ||
    job.blockReason !== null ||
    row.group.open.length > 1 ||
    row.group.incomplete
  );
}

export function matchesFilter(row: QueueRow, filter: QueueFilter): boolean {
  const status = row.current.status;
  switch (filter) {
    case "all":
      return true;
    case "running":
      return status === "running" || status === "cancelling" || row.place?.active === true;
    case "waiting":
      return status === "queued" && !isHeld(row);
    case "held":
      return isHeld(row);
    case "attention":
      return needsAttention(row);
  }
}

export function normalizeQuery(query: string): string {
  return query.normalize("NFKC").trim().toLowerCase();
}

/** Only what the page knows: the local basename, internal job ids, model and profile. */
function haystack(row: QueueRow): string[] {
  const fields = [...row.group.jobIds, row.current.spec.modelId, row.current.spec.profileId];
  if (row.name !== null) fields.push(row.name);
  return fields.map((field) => field.normalize("NFKC").toLowerCase());
}

export function matchesQuery(row: QueueRow, query: string): boolean {
  const q = normalizeQuery(query);
  return q === "" || haystack(row).some((field) => field.includes(q));
}

// Display grouping of statuses (running first); ties fall back to B7's order.
const STATUS_RANK: Record<string, number> = { running: 0, cancelling: 1, waiting_user: 2, blocked: 3, held: 4, queued: 5 };
const collator = new Intl.Collator(["ja", "en"], { numeric: true, sensitivity: "base" });
const time = (job: JobV1) => {
  const value = Date.parse(job.createdAt);
  return Number.isNaN(value) ? Number.POSITIVE_INFINITY : value;
};

function compare(sort: QueueSort): (a: QueueRow, b: QueueRow) => number {
  const fallback = (a: QueueRow, b: QueueRow) => a.queueIndex - b.queueIndex;
  switch (sort) {
    case "queue":
      return fallback;
    case "created":
      // The logical job's creation = its first internal job.
      return (a, b) => time(a.group.attempts[0]!) - time(b.group.attempts[0]!) || fallback(a, b);
    case "status":
      return (a, b) => (STATUS_RANK[a.current.status] ?? 9) - (STATUS_RANK[b.current.status] ?? 9) || fallback(a, b);
    case "name":
      // Known names first, in locale order; unknown names keep B7's order at the end.
      return (a, b) => {
        if (a.name === null || b.name === null) return Number(a.name === null) - Number(b.name === null) || fallback(a, b);
        return collator.compare(a.name, b.name) || fallback(a, b);
      };
  }
}

export interface QueueViewOptions {
  query: string;
  filter: QueueFilter;
  sort: QueueSort;
}

/** The rows the table shows. A copy: the board's rows and B7's order are never touched. */
export function viewRows(rows: readonly QueueRow[], options: QueueViewOptions): QueueRow[] {
  return rows.filter((row) => matchesFilter(row, options.filter) && matchesQuery(row, options.query)).sort(compare(options.sort));
}

/**
 * Keeps the selection by logical job. Only a completed read decides that a row left the queue
 * (`lost`); while the board is loading or failed nothing is decided from its (empty) rows.
 */
export function reconcileSelection(selected: string | null, phase: string, rows: readonly Pick<QueueRow, "rootId">[]): { selected: string | null; lost: boolean } {
  if (selected === null || phase !== "done") return { selected, lost: false };
  return rows.some((row) => row.rootId === selected) ? { selected, lost: false } : { selected: null, lost: true };
}
