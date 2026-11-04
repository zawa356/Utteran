/**
 * Typed boundary for the opt-in `/api/v1/*` contracts (B7 jobs/queue, B9 sync/events).
 * Kept separate from the legacy client: different errors, CAS and idempotency rules.
 * UI-2 does not call any of this against a real server (v1 is never activated by the
 * next UI); it is verified with contract tests and fakes only.
 * Schema authority: docs/architecture/b9/events.schema.json, b7/jobs.schema.json.
 */
import { int, list, oneOf, optStr, record, str } from "./guards";
import type { HttpClient } from "./http";
import { IDEMPOTENCY_KEY_PATTERN, type MutationIntent } from "./idempotency";

export const CURSOR_PATTERN = /^([0-9a-f]{32}):(0|[1-9][0-9]{0,18})$/;
export const OPERATION_KINDS = ["job", "setup_operation", "refresh_operation"] as const;
export const EVENT_RESOURCE_KINDS = [...OPERATION_KINDS, "queue"] as const;

export interface SyncResource {
  id: string;
  kind: (typeof OPERATION_KINDS)[number];
  revision: number;
  status: string;
}

export interface QueueSummary {
  revision: number;
  dispatch: "running" | "paused";
  activeItemId: string | null;
  itemCount: number;
}

export interface SyncSnapshot {
  snapshotToken: string;
  eventCursor: string;
  resources: SyncResource[];
  nextCursor: string | null;
  queue: QueueSummary;
  inventoryRevision: number | null;
}

export interface EventEnvelope {
  id: string;
  resourceId: string;
  resourceKind: (typeof EVENT_RESOURCE_KINDS)[number];
  resourceRevision: number;
  type: "state_changed" | "queue_changed";
  status: string | null;
  reasonCode: string | null;
}

function cursor(value: unknown, where: string): string {
  const text = str(value, where);
  if (!CURSOR_PATTERN.test(text)) throw new Error(where);
  return text;
}

function version(r: Record<string, unknown>, where: string): void {
  if (r.schema_version !== 1) throw new Error(`${where}.schema_version`);
}

export function parseSyncSnapshot(raw: unknown): SyncSnapshot {
  const r = record(raw, "sync");
  version(r, "sync");
  const queue = record(r.queue, "sync.queue");
  return {
    snapshotToken: str(r.snapshot_token, "snapshot_token"),
    eventCursor: cursor(r.event_cursor, "event_cursor"),
    resources: list(r.resources, "resources").map((item, i) => {
      const x = record(item, `resources[${i}]`);
      return {
        id: str(x.id, "id"),
        kind: oneOf(x.kind, OPERATION_KINDS, "kind"),
        revision: int(x.revision, "revision"),
        status: str(x.status, "status"),
      };
    }),
    nextCursor: optStr(r.next_cursor),
    queue: {
      revision: int(queue.revision, "queue.revision"),
      dispatch: oneOf(queue.dispatch, ["running", "paused"] as const, "queue.dispatch"),
      activeItemId: optStr(queue.active_item_id),
      itemCount: list(queue.items, "queue.items").length,
    },
    inventoryRevision: r.inventory_revision === null ? null : int(r.inventory_revision, "inventory_revision"),
  };
}

export function parseEventEnvelope(raw: unknown): EventEnvelope {
  const r = record(raw, "event");
  version(r, "event");
  const payload = record(r.payload, "event.payload");
  return {
    id: cursor(r.id, "event.id"),
    resourceId: str(r.resource_id, "resource_id"),
    resourceKind: oneOf(r.resource_kind, EVENT_RESOURCE_KINDS, "resource_kind"),
    resourceRevision: int(r.resource_revision, "resource_revision"),
    type: oneOf(r.type, ["state_changed", "queue_changed"] as const, "type"),
    status: optStr(payload.status),
    reasonCode: optStr(payload.reason_code),
  };
}

export interface ResyncRequired {
  reasonCode: "event_cursor_expired" | "stream_limit_reached";
}

export function parseResyncRequired(raw: unknown): ResyncRequired {
  const r = record(raw, "resync");
  version(r, "resync");
  if (r.type !== "resync_required") throw new Error("resync.type");
  return { reasonCode: oneOf(r.reason_code, ["event_cursor_expired", "stream_limit_reached"] as const, "reason_code") };
}

export interface JobAccepted {
  jobs: { id: string; revision: number; status: string }[];
  queueRevision: number;
}

/** B7 `JobBatch` (202 accepted, or 200 replay of the same Idempotency-Key). Not terminal. */
export function parseJobBatch(raw: unknown): JobAccepted {
  const r = record(raw, "batch");
  version(r, "batch");
  const jobs = list(r.jobs, "batch.jobs").map((item, i) => {
    const job = record(item, `jobs[${i}]`);
    return { id: str(job.id, "job.id"), revision: int(job.revision, "job.revision"), status: str(job.status, "job.status") };
  });
  if (jobs.length === 0) throw new Error("batch.jobs");
  return { jobs, queueRevision: int(r.queue_revision, "batch.queue_revision") };
}

/** `If-Match` takes the strong ETag form the B5/B7 servers emit: `"<revision>"`. */
export function ifMatch(revision: number): string {
  if (!Number.isSafeInteger(revision) || revision < 0) throw new Error("revision");
  return `"${revision}"`;
}

export interface V1Api {
  /** Reads every /sync page with the same snapshot_token (B9 sync.md). */
  syncAll(signal?: AbortSignal): Promise<SyncSnapshot>;
  /** `?after=` only — never combined with Last-Event-ID (server answers 400 cursor_conflict). */
  eventsUrl(after: string | null): string;
  submitJob(intent: MutationIntent<unknown>, signal?: AbortSignal): Promise<JobAccepted>;
  setQueueOrder(itemIds: string[], expectedRevision: number, idempotencyKey: string, signal?: AbortSignal): Promise<number>;
}

const MAX_SYNC_PAGES = 200;

export function createV1Api(http: HttpClient): V1Api {
  return {
    async syncAll(signal) {
      let page = (await http.request({ method: "GET", path: "/api/v1/sync", signal }, parseSyncSnapshot)).data;
      const first = page;
      const resources = [...page.resources];
      for (let n = 0; page.nextCursor !== null; n += 1) {
        if (n >= MAX_SYNC_PAGES) throw new Error("sync paging did not terminate");
        page = (
          await http.request(
            {
              method: "GET",
              path: "/api/v1/sync",
              query: { cursor: page.nextCursor, snapshot_token: first.snapshotToken },
              signal,
            },
            parseSyncSnapshot,
          )
        ).data;
        if (page.snapshotToken !== first.snapshotToken || page.eventCursor !== first.eventCursor) {
          throw new Error("sync pages from different snapshots");
        }
        resources.push(...page.resources);
      }
      return { ...first, resources, nextCursor: null };
    },
    eventsUrl(after) {
      if (after !== null && !CURSOR_PATTERN.test(after)) throw new Error("cursor");
      return after === null ? "/api/v1/events" : `/api/v1/events?after=${encodeURIComponent(after)}`;
    },
    async submitJob(intent, signal) {
      if (!IDEMPOTENCY_KEY_PATTERN.test(intent.key)) throw new Error("idempotency key");
      intent.attempts += 1;
      intent.state = "sent";
      try {
        const response = await http.request(
          { method: "POST", path: "/api/v1/jobs", body: intent.body, headers: { "Idempotency-Key": intent.key }, timeoutMs: 30_000, signal },
          parseJobBatch,
        );
        intent.state = "accepted";
        return response.data;
      } catch (error) {
        const unknown = typeof error === "object" && error !== null && (error as { outcomeUnknown?: boolean }).outcomeUnknown;
        intent.state = unknown ? "unknown" : "rejected";
        throw error;
      }
    },
    async setQueueOrder(itemIds, expectedRevision, idempotencyKey, signal) {
      const response = await http.request(
        {
          method: "PUT",
          path: "/api/v1/queue/order",
          body: { schema_version: 1, waiting_item_ids: itemIds },
          headers: { "If-Match": ifMatch(expectedRevision), "Idempotency-Key": idempotencyKey },
          signal,
        },
        (raw) => int(record(raw, "queue").revision, "queue.revision"),
      );
      return response.data;
    },
  };
}
