import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SyncSnapshot } from "../api/v1";
import { browserScheduler } from "./scheduler";
import { type EventSourceLike, SyncCoordinator } from "./sync";

const EPOCH = "e".repeat(32);
const OTHER = "f".repeat(32);
const JOB = "job_" + "4".repeat(32);

class FakeSource implements EventSourceLike {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((event: MessageEvent) => void)[]>();
  constructor(readonly url: string) {}
  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.(new Event("open"));
  }
  fail() {
    this.onerror?.(new Event("error"));
  }
  emit(type: string, data: unknown) {
    for (const l of this.listeners.get(type) ?? []) l(new MessageEvent(type, { data: typeof data === "string" ? data : JSON.stringify(data) }));
  }
}

const frame = (seq: number, revision: number, status = "running", epoch = EPOCH) => ({
  schema_version: 1,
  id: `${epoch}:${seq}`,
  resource_id: JOB,
  resource_kind: "job",
  resource_revision: revision,
  timestamp: "2026-11-04T00:00:00Z",
  type: "state_changed",
  payload: { status, reason_code: null, attempt_id: null },
});

const snapshot = (cursor: string, revision = 1): SyncSnapshot => ({
  snapshotToken: "0".repeat(32),
  eventCursor: cursor,
  resources: [{ id: JOB, kind: "job", revision, status: "queued" }],
  nextCursor: null,
  queue: { revision: 1, dispatch: "running", activeItemId: null, itemCount: 0 },
  inventoryRevision: null,
});

function harness(snapshots: (SyncSnapshot | Error)[]) {
  const sources: FakeSource[] = [];
  const order: string[] = [];
  const fetchSync = vi.fn(async () => {
    order.push("sync");
    const next = snapshots.shift();
    if (next === undefined) throw new Error("no more snapshots");
    if (next instanceof Error) throw next;
    return next;
  });
  const coordinator = new SyncCoordinator({
    fetchSync,
    eventsUrl: (after) => (after === null ? "/api/v1/events" : `/api/v1/events?after=${after}`),
    openEvents: (url) => {
      order.push(`open ${url}`);
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    },
    scheduler: browserScheduler,
    backoff: { initialMs: 100, maxMs: 1000 },
  });
  const last = () => sources[sources.length - 1] as FakeSource;
  return { coordinator, sources, order, fetchSync, last };
}

describe("SyncCoordinator (B9 /sync + /events)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reads /sync before subscribing and subscribes after the sync cursor", async () => {
    const h = harness([snapshot(`${EPOCH}:5`)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(h.order).toEqual(["sync", `open /api/v1/events?after=${EPOCH}:5`]);
    h.last().open();
    expect(h.coordinator.snapshot.connection).toBe("connected");
    h.last().emit("state_changed", frame(6, 2));
    expect(h.coordinator.reducer.statuses.get(JOB)).toBe("running");
    expect(h.coordinator.snapshot.cursor).toBe(`${EPOCH}:6`);
  });

  it("ignores frames at or before the sync cursor (no double apply)", async () => {
    const h = harness([snapshot(`${EPOCH}:5`, 3)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    h.last().open();
    h.last().emit("state_changed", frame(5, 3, "cancelled"));
    h.last().emit("state_changed", frame(4, 2, "running"));
    expect(h.coordinator.reducer.statuses.get(JOB)).toBe("queued");
    expect(h.coordinator.snapshot.stats.duplicate).toBe(2);
  });

  it("a dropped stream reconnects with ?after=<cursor> as a NEW EventSource, without /sync", async () => {
    const h = harness([snapshot(`${EPOCH}:5`)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    const first = h.last();
    first.open();
    first.emit("state_changed", frame(6, 2));
    first.fail();
    expect(first.closed).toBe(true); // native auto-reconnect would send a conflicting Last-Event-ID
    expect(h.coordinator.snapshot.connection).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(100);
    expect(h.sources).toHaveLength(2);
    expect(h.last().url).toBe(`/api/v1/events?after=${EPOCH}:6`);
    expect(h.fetchSync).toHaveBeenCalledTimes(1);
    // Replayed frames after reconnect are duplicates.
    h.last().open();
    h.last().emit("state_changed", frame(6, 2));
    expect(h.coordinator.snapshot.stats.duplicate).toBe(1);
  });

  it("a stream rejected before open (e.g. 410 expired cursor) falls back to /sync", async () => {
    const h = harness([snapshot(`${EPOCH}:5`), snapshot(`${OTHER}:2`)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    h.last().fail();
    expect(h.coordinator.snapshot.lastProblem).toBe("stream_rejected");
    await vi.advanceTimersByTimeAsync(100);
    expect(h.fetchSync).toHaveBeenCalledTimes(2);
    expect(h.last().url).toBe(`/api/v1/events?after=${OTHER}:2`);
    expect(h.coordinator.reducer.epoch).toBe(OTHER);
  });

  it("resync_required closes the stream and re-reads /sync", async () => {
    const h = harness([snapshot(`${EPOCH}:5`), snapshot(`${EPOCH}:9`, 4)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    const first = h.last();
    first.open();
    first.emit("resync_required", { schema_version: 1, type: "resync_required", reason_code: "event_cursor_expired" });
    expect(first.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(h.fetchSync).toHaveBeenCalledTimes(2);
    expect(h.coordinator.snapshot.lastProblem).toBe("event_cursor_expired");
    expect(h.last().url).toBe(`/api/v1/events?after=${EPOCH}:9`);
  });

  it("a seq gap or another epoch on the unfiltered stream triggers resync", async () => {
    const h = harness([snapshot(`${EPOCH}:5`), snapshot(`${EPOCH}:8`, 5), snapshot(`${OTHER}:1`)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    h.last().open();
    h.last().emit("state_changed", frame(8, 4));
    await vi.advanceTimersByTimeAsync(0);
    expect(h.fetchSync).toHaveBeenCalledTimes(2);
    expect(h.coordinator.snapshot.stats.gaps).toBe(1);
    expect(h.coordinator.reducer.revisions.get(JOB)).toBe(5);
    h.last().open();
    h.last().emit("state_changed", frame(9, 6, "running", OTHER));
    await vi.advanceTimersByTimeAsync(0);
    expect(h.fetchSync).toHaveBeenCalledTimes(3);
    expect(h.coordinator.reducer.epoch).toBe(OTHER);
  });

  it("Issue #37: a /sync from a restored store (new epoch, lower queue revision) is adopted", async () => {
    const queued = (cursor: string, queueRevision: number): SyncSnapshot => ({
      ...snapshot(cursor),
      queue: { revision: queueRevision, dispatch: "running", activeItemId: null, itemCount: 0 },
    });
    const queueFrame = (seq: number, revision: number, epoch: string) => ({
      ...frame(seq, revision, "running", epoch),
      resource_id: "queue",
      resource_kind: "queue",
      type: "queue_changed",
    });
    const h = harness([queued(`${EPOCH}:10`, 100), queued(`${OTHER}:3`, 5)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    const old = h.last();
    old.open();
    expect(h.coordinator.reducer.queueRevision).toBe(100);
    old.emit("resync_required", { schema_version: 1, type: "resync_required", reason_code: "event_cursor_expired" });
    await vi.advanceTimersByTimeAsync(0);
    expect(h.coordinator.reducer.epoch).toBe(OTHER);
    expect(h.coordinator.reducer.queueRevision).toBe(5);
    expect(h.coordinator.reducer.revisions.get(JOB)).toBe(1); // nothing kept from the old epoch
    const current = h.last();
    expect(current.url).toBe(`/api/v1/events?after=${OTHER}:3`);
    current.open();
    // A late frame of the closed old stream is never applied.
    old.emit("queue_changed", queueFrame(11, 101, EPOCH));
    expect(h.coordinator.reducer.queueRevision).toBe(5);
    current.emit("queue_changed", queueFrame(4, 6, OTHER));
    expect(h.coordinator.reducer.queueRevision).toBe(6);
    expect(h.coordinator.snapshot.stats.applied).toBe(1);
    current.emit("queue_changed", queueFrame(5, 6, OTHER));
    expect(h.coordinator.snapshot.stats.stale).toBe(1);
    expect(h.fetchSync).toHaveBeenCalledTimes(2); // nothing re-submitted or retried
  });

  it("a /sync answered after stop() or after a newer resync is discarded", async () => {
    let release: ((value: SyncSnapshot) => void) | null = null;
    const fetchSync = vi.fn(
      () =>
        new Promise<SyncSnapshot>((resolve) => {
          release = resolve;
        }),
    );
    const coordinator = new SyncCoordinator({
      fetchSync,
      eventsUrl: () => "/api/v1/events",
      openEvents: (url) => new FakeSource(url),
      scheduler: browserScheduler,
    });
    coordinator.start();
    coordinator.stop();
    (release as unknown as (value: SyncSnapshot) => void)(snapshot(`${OTHER}:3`));
    await vi.advanceTimersByTimeAsync(0);
    expect(coordinator.reducer.epoch).toBeNull();
    expect(coordinator.snapshot.connection).toBe("stopped");
  });

  it("an unparsable frame triggers resync instead of being ignored", async () => {
    const h = harness([snapshot(`${EPOCH}:5`), snapshot(`${EPOCH}:5`)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    h.last().open();
    h.last().emit("state_changed", "{broken");
    await vi.advanceTimersByTimeAsync(0);
    expect(h.coordinator.snapshot.lastProblem).toBe("frame_invalid");
    expect(h.fetchSync).toHaveBeenCalledTimes(2);
  });

  it("sync failure goes offline and retries with backoff; stop cancels everything", async () => {
    const h = harness([new Error("503"), new Error("503"), snapshot(`${EPOCH}:1`)]);
    h.coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(h.coordinator.snapshot.connection).toBe("offline");
    await vi.advanceTimersByTimeAsync(100);
    expect(h.fetchSync).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(199);
    expect(h.fetchSync).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.fetchSync).toHaveBeenCalledTimes(3);
    expect(h.sources).toHaveLength(1);
    h.coordinator.stop();
    expect(h.last().closed).toBe(true);
    h.last().fail();
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.fetchSync).toHaveBeenCalledTimes(3);
    expect(h.coordinator.snapshot.connection).toBe("stopped");
  });
});
