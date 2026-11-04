import { describe, expect, it, vi } from "vitest";
import { json, scriptedFetch } from "../test/fakeFetch";
import { ApiError } from "./errors";
import { createHttpClient } from "./http";
import { IDEMPOTENCY_KEY_PATTERN, MutationIntent, newIdempotencyKey } from "./idempotency";
import { createV1Api, ifMatch, parseEventEnvelope, parseSyncSnapshot } from "./v1";

const EPOCH = "a".repeat(32);
const JOB = "job_" + "1".repeat(32);
const queue = (revision = 3) => ({ schema_version: 1, revision, dispatch: "paused", active_item_id: null, items: [] });
const page = (resources: unknown[], next: string | null, token = "0".repeat(32), cursor = `${EPOCH}:7`) => ({
  schema_version: 1,
  snapshot_token: token,
  event_cursor: cursor,
  resources,
  next_cursor: next,
  queue: queue(),
  inventory_revision: null,
});
const resource = (n: number) => ({ id: `job_${String(n).repeat(32)}`, kind: "job", revision: 1, status: "queued" });
const batch = { schema_version: 1, jobs: [{ id: JOB, revision: 1, status: "queued" }], queue_revision: 4 };

describe("v1 contract client", () => {
  it("reads every /sync page with the first snapshot token", async () => {
    const fetch = scriptedFetch([json(200, page([resource(1)], "y1.next")), json(200, page([resource(2)], null))]);
    const api = createV1Api(createHttpClient({ fetch }));
    const snapshot = await api.syncAll();
    expect(snapshot.resources.map((r) => r.id)).toHaveLength(2);
    expect(snapshot.eventCursor).toBe(`${EPOCH}:7`);
    expect(fetch.calls[1]?.url).toBe(`/api/v1/sync?cursor=y1.next&snapshot_token=${"0".repeat(32)}`);
  });

  it("rejects pages from another snapshot instead of mixing them", async () => {
    const fetch = scriptedFetch([json(200, page([], "y1.next")), json(200, page([], null, "f".repeat(32)))]);
    await expect(createV1Api(createHttpClient({ fetch })).syncAll()).rejects.toThrow(/different snapshots/);
  });

  it("builds events URLs with ?after= only and validates the cursor", () => {
    const api = createV1Api(createHttpClient({ fetch: scriptedFetch([]) }));
    expect(api.eventsUrl(null)).toBe("/api/v1/events");
    expect(api.eventsUrl(`${EPOCH}:12`)).toBe(`/api/v1/events?after=${EPOCH}%3A12`);
    expect(() => api.eventsUrl("bogus")).toThrow();
  });

  it("parsers reject malformed cursors and unknown kinds", () => {
    expect(() => parseSyncSnapshot(page([], null, "0".repeat(32), "x:1"))).toThrow();
    const event = {
      schema_version: 1,
      id: `${EPOCH}:1`,
      resource_id: JOB,
      resource_kind: "job",
      resource_revision: 1,
      timestamp: "2026-11-04T00:00:00Z",
      type: "state_changed",
      payload: { status: "running", reason_code: null, attempt_id: null },
    };
    expect(parseEventEnvelope(event).resourceRevision).toBe(1);
    expect(() => parseEventEnvelope({ ...event, resource_kind: "model" })).toThrow();
    expect(() => parseEventEnvelope({ ...event, schema_version: 2 })).toThrow();
  });

  it("keys are valid and unique per intent", () => {
    const a = newIdempotencyKey();
    expect(a).toMatch(IDEMPOTENCY_KEY_PATTERN);
    expect(newIdempotencyKey()).not.toBe(a);
  });

  it("an explicit retry of a lost POST reuses the same Idempotency-Key; 202 is accepted, not done", async () => {
    vi.useFakeTimers();
    const fetch = scriptedFetch([
      () => new Promise<Response>((_, reject) => setTimeout(() => reject(new TypeError("connection reset")), 10)),
      json(202, batch),
    ]);
    const api = createV1Api(createHttpClient({ fetch }));
    const intent = new MutationIntent({ schema_version: 1, items: [] });
    const first = api.submitJob(intent).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(20);
    const error = (await first) as ApiError;
    expect(error.outcomeUnknown).toBe(true);
    expect(intent.state).toBe("unknown");
    const accepted = await api.submitJob(intent);
    expect(accepted.jobs[0]?.status).toBe("queued");
    expect(intent.state).toBe("accepted");
    expect(intent.attempts).toBe(2);
    const keys = fetch.calls.map((c) => (c.init.headers as Record<string, string>)["Idempotency-Key"]);
    expect(keys[0]).toBe(intent.key);
    expect(keys[1]).toBe(intent.key);
    vi.useRealTimers();
  });

  it("a different intent gets a different key", () => {
    expect(new MutationIntent({}).key).not.toBe(new MutationIntent({}).key);
  });

  it("sends If-Match for queue CAS and surfaces 409 as a conflict", async () => {
    const fetch = scriptedFetch([
      json(200, queue(6)),
      json(409, { schema_version: 1, error: { code: "revision_conflict", retryable: false } }),
    ]);
    const api = createV1Api(createHttpClient({ fetch }));
    const key = newIdempotencyKey();
    expect(await api.setQueueOrder(["qi_" + "1".repeat(32)], 5, key)).toBe(6);
    const headers = fetch.calls[0]?.init.headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"5"');
    expect(JSON.parse(String(fetch.calls[0]?.init.body))).toEqual({ schema_version: 1, waiting_item_ids: ["qi_" + "1".repeat(32)] });
    const error = (await api.setQueueOrder([], 5, key).catch((e: unknown) => e)) as ApiError;
    expect([error.kind, error.code]).toEqual(["conflict", "revision_conflict"]);
    expect(() => ifMatch(-1)).toThrow();
  });
});
