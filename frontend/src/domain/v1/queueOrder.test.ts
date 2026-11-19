import { expect, it } from "vitest";
import type { QueueViewV1 } from "../../api/v1Resources";
import { adjacentOrder, waitingItems } from "./queueOrder";
const make = (): QueueViewV1 => ({ revision: 3, dispatch: "paused", activeItemId: "active", items: [
  { id: "active", resourceId: "j0", resourceKind: "job", position: null, held: false, blockers: [] },
  ...[1, 2, 3, 4].map(n => ({ id: `q${n}`, resourceId: `j${n}`, resourceKind: n === 3 ? "setup_operation" : "job", position: n, held: n === 2, blockers: [] })),
] });
it("complete waiting permutation retains held/setup, excludes active and ignores array display order", () => {
  const q = make(); q.items.reverse();
  expect(adjacentOrder(q, "q2", -1)).toEqual({ ids: ["q2", "q1", "q3", "q4"], reason: null });
  expect(adjacentOrder(q, "q2", 1).reason).toBe("setup");
  expect(adjacentOrder(q, "q1", -1).reason).toBe("edge");
  expect(adjacentOrder(q, "active", 1).reason).toBe("waiting");
});
for (const corrupt of [
  (q: QueueViewV1) => q.items.push(q.items[1]!),
  (q: QueueViewV1) => { q.items[2]!.position = 1; },
  (q: QueueViewV1) => { q.items[2]!.position = null; },
  (q: QueueViewV1) => { q.activeItemId = "missing"; },
  (q: QueueViewV1) => { q.items[0]!.position = 2; },
  (q: QueueViewV1) => { q.items[2]!.resourceId = q.items[1]!.resourceId; },
]) it("fails closed on inconsistent queue", () => {
  const q = make(); corrupt(q); expect(waitingItems(q)).toBeNull();
  expect(adjacentOrder(q, "q2", -1).reason).toBe("invalid");
});
