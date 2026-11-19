import type { QueueViewV1 } from "../../api/v1Resources";
export interface QueueDisplay { query: string; sort: string; filter: string; }
export type QueueItem = QueueViewV1["items"][number];
export type QueueDisabled = "busy" | "unread" | "invalid" | "branch" | "waiting" | "display" | "edge" | "setup";

/** All waiting items, including held and non-job resources. Never infer missing queue facts. */
export function waitingItems(view: QueueViewV1): QueueItem[] | null {
  if (!Number.isSafeInteger(view.revision) || view.revision < 1) return null;
  const ids = new Set<string>(); const resources = new Set<string>(); const positions = new Set<number>();
  const waiting: QueueItem[] = [];
  let active = false;
  for (const item of view.items) {
    const resource = `${item.resourceKind}:${item.resourceId}`;
    if (ids.has(item.id) || resources.has(resource)) return null;
    ids.add(item.id); resources.add(resource);
    if (item.id === view.activeItemId) {
      if (item.position !== null) return null;
      active = true;
    } else {
      if (item.position === null || !Number.isSafeInteger(item.position) || item.position < 1 || positions.has(item.position)) return null;
      positions.add(item.position); waiting.push(item);
    }
  }
  if (view.activeItemId !== null && !active) return null;
  // Positions can have gaps: B7 retains ord values after removals. Only uniqueness is required.
  return waiting.sort((a, b) => a.position! - b.position!);
}
export function adjacentOrder(view: QueueViewV1, itemId: string, direction: -1 | 1): { ids: string[] | null; reason: QueueDisabled | null } {
  const items = waitingItems(view);
  if (!items) return { ids: null, reason: "invalid" };
  const index = items.findIndex(item => item.id === itemId && item.resourceKind === "job");
  if (index < 0) return { ids: null, reason: "waiting" };
  const next = index + direction;
  if (next < 0 || next >= items.length) return { ids: null, reason: "edge" };
  if (items[next]!.resourceKind !== "job") return { ids: null, reason: "setup" };
  const ids = items.map(item => item.id);
  [ids[index], ids[next]] = [ids[next]!, ids[index]!];
  return { ids, reason: null };
}
