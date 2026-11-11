/**
 * TypeScript port of `utteran_contracts.events.EventReducer` (B9, the authority).
 *
 * Parity: with `gapPolicy: "tolerate"` every outcome equals the Python reducer — the
 * shared fixture `src/test/fixtures/b9-reducer-cases.json` is replayed by both Vitest and
 * pytest (tests/test_ui2_reducer_parity.py).
 *
 * Addition (UI-2): gaps are never silent. They are counted, and an *unfiltered* stream
 * uses `gapPolicy: "resync"`: the server allocates outbox seq from one counter inside the
 * writing transaction (utteran_control/store.py) and reads `seq > after` without filter,
 * so a gap there means a missed frame and the client re-reads `/sync`. A resource-filtered
 * stream legitimately skips rows (B9 events-sse.md) and must use "tolerate".
 */
import { CURSOR_PATTERN, type EventEnvelope, type SyncSnapshot } from "../api/v1";

export type Outcome = "applied" | "duplicate" | "stale" | "resync";
export type GapPolicy = "tolerate" | "resync";

export interface ReducerStats {
  applied: number;
  duplicate: number;
  stale: number;
  resync: number;
  gaps: number;
}

export function parseCursor(cursor: string): { epoch: string; seq: bigint } | null {
  const match = CURSOR_PATTERN.exec(cursor);
  if (!match) return null;
  return { epoch: match[1] as string, seq: BigInt(match[2] as string) };
}

export class EventReducer {
  epoch: string | null = null;
  cursor = 0n;
  readonly revisions = new Map<string, number>();
  readonly statuses = new Map<string, string>();
  queueRevision = 0;
  private seen = new Set<bigint>();
  readonly stats: ReducerStats = { applied: 0, duplicate: 0, stale: 0, resync: 0, gaps: 0 };

  constructor(readonly gapPolicy: GapPolicy = "resync") {}

  get cursorString(): string | null {
    return this.epoch === null ? null : `${this.epoch}:${this.cursor}`;
  }

  applySync(snapshot: SyncSnapshot): void {
    const parsed = parseCursor(snapshot.eventCursor);
    if (parsed === null) throw new Error("invalid sync cursor");
    if (this.epoch !== parsed.epoch) {
      this.epoch = parsed.epoch;
      this.cursor = 0n;
      this.seen = new Set();
      this.revisions.clear();
      this.statuses.clear();
      // Revisions are only comparable inside one epoch (Issue #37): the new epoch's queue
      // revision is adopted even when it is lower (e.g. a restored backup).
      this.queueRevision = 0;
    }
    if (parsed.seq > this.cursor) this.cursor = parsed.seq;
    for (const resource of snapshot.resources) this.applyResource(resource.id, resource.revision, resource.status);
    this.queueRevision = Math.max(this.queueRevision, snapshot.queue.revision);
  }

  /** A resource GET (or sync row). Older than known -> stale; never rolls back. */
  applyResource(resourceId: string, revision: number, status: string): Outcome {
    if (revision <= (this.revisions.get(resourceId) ?? 0)) return "stale";
    this.revisions.set(resourceId, revision);
    this.statuses.set(resourceId, status);
    return "applied";
  }

  applyEvent(event: EventEnvelope): Outcome {
    const outcome = this.reduce(event);
    this.stats[outcome] += 1;
    return outcome;
  }

  private reduce(event: EventEnvelope): Outcome {
    const parsed = parseCursor(event.id);
    if (parsed === null || this.epoch === null || parsed.epoch !== this.epoch) return "resync";
    const { seq } = parsed;
    if (this.seen.has(seq) || seq <= this.cursor) return "duplicate";
    if (seq > this.cursor + 1n) {
      this.stats.gaps += 1;
      if (this.gapPolicy === "resync") return "resync";
    }
    this.seen.add(seq);
    this.cursor = seq;
    if (event.resourceKind === "queue") {
      if (event.resourceRevision <= this.queueRevision) return "stale";
      this.queueRevision = event.resourceRevision;
      return "applied";
    }
    const status = event.status ?? this.statuses.get(event.resourceId) ?? "";
    return this.applyResource(event.resourceId, event.resourceRevision, status);
  }
}
