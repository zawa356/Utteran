import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/errors";
import { createHttpClient } from "../../api/http";
import { MutationIntent } from "../../api/idempotency";
import { createV1ResourcesApi, type JsonObject } from "../../api/v1Resources";
import { json } from "../../test/fakeFetch";
import { fakeV1Server } from "../../test/v1Server";
import { V1JobBoardController } from "./jobBoard";
const qi = (n: number) => "qi_" + n.toString(16).padStart(32, "0");
const wire = { schema_version: 1, revision: 8, dispatch: "paused", active_item_id: null, items: [] };
function setup() {
  const server = fakeV1Server(); const ids = server.addJobs(3);
  const api = createV1ResourcesApi(createHttpClient({ fetch: server.fetch }));
  return { server, ids, api, board: new V1JobBoardController(api) };
}
describe("D2-B typed queue clients", () => {
  for (const kind of ["hold", "order"] as const) {
    it(`${kind}: method, item id, complete body, CAS, key and ETag`, async () => {
      const calls: RequestInit[] = []; const paths: string[] = [];
      const api = createV1ResourcesApi(createHttpClient({ fetch: async (path, init) => { paths.push(path); calls.push(init); return json(200, wire, { ETag: '\"8\"' }); } }));
      const intent = new MutationIntent<JsonObject>(kind === "hold" ? { schema_version: 1, held: true } : { schema_version: 1, waiting_item_ids: [qi(2), qi(1)] });
      if (kind === "hold") await api.setHold(qi(1), intent, 7); else await api.setOrder(intent, 7);
      expect(paths).toEqual([kind === "hold" ? `/api/v1/queue/items/${qi(1)}` : "/api/v1/queue/order"]);
      expect(calls[0]!.method).toBe(kind === "hold" ? "PATCH" : "PUT");
      expect(JSON.parse(calls[0]!.body as string)).toEqual(intent.body);
      expect(new Headers(calls[0]!.headers).get("If-Match")).toBe('\"7\"');
      expect(new Headers(calls[0]!.headers).get("Idempotency-Key")).toBe(intent.key);
      const bad = createV1ResourcesApi(createHttpClient({ fetch: async () => json(200, wire, { ETag: '\"9\"' }) }));
      await expect(kind === "hold" ? bad.setHold(qi(1), intent, 7) : bad.setOrder(intent, 7)).rejects.toMatchObject({ kind: "malformed" });
    });
  }
});
describe("D2-B shared board operations", () => {
  it("unknown dispatch survives GET and leases, blocks other writes and replays frozen request", async () => {
    const { server, board, ids } = setup(); await board.refresh();
    server.inject.set("PATCH /api/v1/queue/control", { reply: json(500, {}), apply: true });
    await board.setDispatch("running"); expect(board.store.get().action?.phase).toBe("unknown");
    await board.refresh(); const release = board.retainWatch(); release();
    await board.setDispatch("paused"); await board.cancel(ids[0]!);
    expect(server.writes()).toHaveLength(1);
    await board.recheck(); expect(board.store.get().action?.phase).toBe("done");
    const writes = server.writes(); expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]); board.dispose();
  });
  it("412 refreshes and waits for a new explicit intent", async () => {
    const { server, board } = setup(); await board.refresh();
    server.inject.set("PATCH /api/v1/queue/control", { reply: json(412, {}) });
    await board.setDispatch("running"); expect(board.store.get().action?.phase).toBe("conflict");
    await board.recheck(); expect(server.writes()).toHaveLength(1); expect(board.store.get().phase).toBe("done");
  });
  for (const [status, phase] of [[409, "rejected"], [422, "rejected"], [429, "rejected"], [500, "unknown"], [503, "unknown"]] as const) {
    it(`${status} is ${phase}`, async () => {
      const { server, board } = setup(); await board.refresh();
      server.inject.set("PATCH /api/v1/queue/control", { reply: json(status, {}) });
      await board.setDispatch("running"); expect(board.store.get().action?.phase).toBe(phase);
    });
  }
  it("failed reads disable stale revision writes", async () => {
    const { api, board, server } = setup(); await board.refresh();
    api.getQueue = async () => { throw new ApiError({ kind: "network" }); };
    await board.refresh(); await board.setDispatch("running"); expect(server.writes()).toHaveLength(0);
  });
});

describe("D2-B order and hold domain guards", () => {
  it("sends all waiting ids, includes held/setup, never crosses setup or a display view", async () => {
    const { api, board, ids } = setup(); await board.refresh();
    const original = board.store.get().queueView!;
    const first = original.items.find(item => item.resourceId === ids[0])!;
    const second = original.items.find(item => item.resourceId === ids[1])!;
    second.held = true;
    const setupItem = { ...second, id: qi(99), resourceId: "setup_1", resourceKind: "setup_operation", position: 3 };
    original.items = [first, second, setupItem];
    const sent: JsonObject[] = [];
    api.setOrder = async (intent) => { sent.push(intent.body); return original; };
    api.getQueue = async () => original;
    const normal = { query: "", filter: "all", sort: "queue" } as const;
    await board.move(ids[1]!, ids[1]!, -1, normal);
    expect(sent).toEqual([{ schema_version: 1, waiting_item_ids: [second.id, first.id, setupItem.id] }]);
    await board.move(ids[1]!, ids[1]!, 1, normal);
    for (const display of [{ ...normal, query: "x" }, { ...normal, filter: "held" as const }, { ...normal, sort: "name" as const }]) await board.move(ids[1]!, ids[1]!, -1, display);
    await board.move(ids[0]!, ids[0]!, -1, normal);
    expect(sent).toHaveLength(1);
  });
  it("active/missing/null places refuse hold, explicit branch required for cancellation", async () => {
    const { board, ids, server } = setup(); await board.refresh();
    const q = board.store.get().queueView!;
    q.activeItemId = q.items[0]!.id; q.items[0]!.position = null;
    await board.setHeld(ids[0]!, ids[0]!, true);
    await board.setHeld(ids[1]!, "missing", true);
    expect(server.writes()).toHaveLength(0);
    const group = board.store.get().queue[0]!;
    const branch = { ...group.open[0]!, id: ids[1]! };
    board.store.patch({ queue: [{ ...group, open: [...group.open, branch] }] });
    await board.cancel(group.rootId); expect(server.writes()).toHaveLength(0);
    await board.cancel(group.rootId, branch.id); expect(server.writes()[0]!.line).toContain(`${branch.id}/cancel`);
  });
  for (const failure of ["network", "malformed", "timeout"] as const) it(`${failure} keeps intent frozen across reads`, async () => {
    const { api, board } = setup(); await board.refresh();
    const received: { key: string; body: JsonObject; revision: number }[] = [];
    api.setDispatch = async (intent, revision) => {
      received.push({ key: intent.key, body: intent.body, revision });
      if (received.length === 1) throw new ApiError({ kind: failure });
      return board.store.get().queueView!;
    };
    await board.setDispatch("running");
    expect(board.store.get().action?.phase).toBe("unknown");
    await board.refresh(); await board.recheck();
    expect(received).toHaveLength(2); expect(received[1]).toEqual(received[0]);
  });
});

for (const kind of ["hold", "order"] as const) it(`${kind} unknown freezes endpoint, target, body, key and CAS across a newer GET`, async () => {
  const { api, board, ids, server } = setup(); await board.refresh();
  const calls: { endpoint: string; key: string; body: JsonObject; revision: number }[] = [];
  const respond = async (endpoint: string, intent: MutationIntent<JsonObject>, revision: number) => {
    calls.push({ endpoint, key: intent.key, body: intent.body, revision });
    if (calls.length === 1) throw new ApiError({ kind: "timeout" });
    return board.store.get().queueView!;
  };
  api.setHold = (id, intent, revision) => respond(`items/${id}`, intent, revision);
  api.setOrder = (intent, revision) => respond("order", intent, revision);
  if (kind === "hold") await board.setHeld(ids[1]!, ids[1]!, true);
  else await board.move(ids[1]!, ids[1]!, -1, { query: "", filter: "all", sort: "queue" });
  expect(board.store.get().action?.phase).toBe("unknown");
  server.queue.revision += 10;
  await board.refresh(); await board.recheck();
  expect(calls).toHaveLength(2); expect(calls[1]).toEqual(calls[0]);
});
