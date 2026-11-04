import { describe, expect, it } from "vitest";
import type { EventEnvelope, SyncSnapshot } from "../api/v1";
import cases from "../test/fixtures/b9-reducer-cases.json";
import { EventReducer, type GapPolicy } from "./reducer";

interface Step {
  op: string;
  [key: string]: unknown;
}

function snapshot(step: Step): SyncSnapshot {
  return {
    snapshotToken: "0".repeat(32),
    eventCursor: step.event_cursor as string,
    resources: (step.resources as { id: string; kind: "job"; revision: number; status: string }[]).map((r) => ({ ...r })),
    nextCursor: null,
    queue: { revision: step.queue_revision as number, dispatch: "running", activeItemId: null, itemCount: 0 },
    inventoryRevision: null,
  };
}

function event(step: Step): EventEnvelope {
  return {
    id: step.id as string,
    resourceId: step.resource_id as string,
    resourceKind: step.resource_kind as EventEnvelope["resourceKind"],
    resourceRevision: step.resource_revision as number,
    type: step.type as EventEnvelope["type"],
    status: (step.status as string | null) ?? null,
    reasonCode: null,
  };
}

function replay(steps: Step[], policy: GapPolicy) {
  const reducer = new EventReducer(policy);
  const outcomes: { expected: string; actual: string }[] = [];
  for (const step of steps) {
    if (step.op === "sync") reducer.applySync(snapshot(step));
    else if (step.op === "set_epoch") reducer.epoch = step.epoch as string;
    else {
      const actual =
        step.op === "resource"
          ? reducer.applyResource(step.id as string, step.revision as number, step.status as string)
          : reducer.applyEvent(event(step));
      const expected = (policy === "resync" ? (step.expect_strict ?? step.expect) : step.expect) as string;
      outcomes.push({ expected, actual });
      // In strict mode the client re-reads /sync after a resync, so later steps no longer apply.
      if (policy === "resync" && step.expect_strict === "resync") return { reducer, outcomes, truncated: true };
    }
  }
  return { reducer, outcomes, truncated: false };
}

describe("EventReducer — shared B9 fixture (parity with utteran_contracts.events)", () => {
  for (const testCase of cases.cases) {
    it(`tolerate: ${testCase.name}`, () => {
      const { reducer, outcomes } = replay(testCase.steps as Step[], "tolerate");
      for (const o of outcomes) expect(o.actual).toBe(o.expected);
      const final = testCase.final as { cursor?: string; statuses?: Record<string, string>; queue_revision?: number };
      if (final.cursor !== undefined) expect(reducer.cursor.toString()).toBe(final.cursor);
      for (const [id, status] of Object.entries(final.statuses ?? {})) expect(reducer.statuses.get(id)).toBe(status);
      if (final.queue_revision !== undefined) expect(reducer.queueRevision).toBe(final.queue_revision);
    });
    it(`resync policy: ${testCase.name}`, () => {
      const { outcomes } = replay(testCase.steps as Step[], "resync");
      for (const o of outcomes) expect(o.actual).toBe(o.expected);
    });
  }
});

describe("EventReducer — UI-2 additions", () => {
  const epoch = "c".repeat(32);
  const base = (cursor: string): SyncSnapshot => ({
    snapshotToken: "0".repeat(32),
    eventCursor: cursor,
    resources: [],
    nextCursor: null,
    queue: { revision: 1, dispatch: "running", activeItemId: null, itemCount: 0 },
    inventoryRevision: null,
  });
  const ev = (seq: string, revision = 1): EventEnvelope => ({
    id: `${epoch}:${seq}`,
    resourceId: "job_" + "9".repeat(32),
    resourceKind: "job",
    resourceRevision: revision,
    type: "state_changed",
    status: "running",
    reasonCode: null,
  });

  it("counts gaps even when tolerated (never silent)", () => {
    const reducer = new EventReducer("tolerate");
    reducer.applySync(base(`${epoch}:1`));
    expect(reducer.applyEvent(ev("4"))).toBe("applied");
    expect(reducer.stats.gaps).toBe(1);
  });

  it("a gap on the unfiltered stream asks for resync without advancing the cursor", () => {
    const reducer = new EventReducer("resync");
    reducer.applySync(base(`${epoch}:1`));
    expect(reducer.applyEvent(ev("2"))).toBe("applied");
    expect(reducer.applyEvent(ev("4", 2))).toBe("resync");
    expect(reducer.cursorString).toBe(`${epoch}:2`);
  });

  it("handles 19-digit sequence numbers exactly (beyond Number precision)", () => {
    const reducer = new EventReducer("resync");
    reducer.applySync(base(`${epoch}:9007199254740993`));
    expect(reducer.applyEvent(ev("9007199254740993"))).toBe("duplicate");
    expect(reducer.applyEvent(ev("9007199254740994"))).toBe("applied");
    expect(reducer.cursorString).toBe(`${epoch}:9007199254740994`);
  });

  it("malformed ids ask for resync", () => {
    const reducer = new EventReducer();
    reducer.applySync(base(`${epoch}:0`));
    expect(reducer.applyEvent({ ...ev("1"), id: "nope" })).toBe("resync");
  });
});
