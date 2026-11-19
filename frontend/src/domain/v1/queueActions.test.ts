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
      const api = createV1ResourcesApi(createHttpClient({ fetch: async (path, init) => { paths.push(path); calls.push(init); return json(wire, 200, { ETag: '\"8\"' }); } }));
      const intent = new MutationIntent<JsonObject>(kind === "hold" ? { schema_version: 1, held: true } : { schema_version: 1, waiting_item_ids: [qi(2), qi(1)] });
      if (kind === "hold") await api.setHold(qi(1), intent, 7); else await api.setOrder(intent, 7);
      expect(paths).toEqual([kind === "hold" ? `/api/v1/queue/items/${qi(1)}` : "/api/v1/queue/order"]);
      expect(calls[0]!.method).toBe(kind === "hold" ? "PATCH" : "PUT");
      expect(JSON.parse(calls[0]!.body as string)).toEqual(intent.body);
      expect(new Headers(calls[0]!.headers).get("If-Match")).toBe('\"7\"');
      expect(new Headers(calls[0]!.headers).get("Idempotency-Key")).toBe(intent.key);
      const bad = createV1ResourcesApi(createHttpClient({ fetch: async () => json(wire, 200, { ETag: '\"9\"' }) }));
      await expect(kind === "hold" ? bad.setHold(qi(1), intent, 7) : bad.setOrder(intent, 7)).rejects.toMatchObject({ kind: "malformed" });
    });
  }
});
describe("D2-B shared board operations", () => {
  it("unknown dispatch survives GET and leases, blocks other writes and replays frozen request", async () => {
    const { server, board, ids } = setup(); await board.refresh();
    server.inject.set("PATCH /api/v1/queue/control", { reply: json({}, 500), apply: true });
    await board.setDispatch("running"); expect(board.store.get().action?.phase).toBe("unknown");
    await board.refresh(); const release = board.retainWatch(); release();
    await board.setDispatch("paused"); await board.cancel(ids[0]!);
    expect(server.writes()).toHaveLength(1);
    await board.recheck(); expect(board.store.get().action?.phase).toBe("done");
    const writes = server.writes(); expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]); board.dispose();
  });
  it("412 refreshes and waits for a new explicit intent", async () => {
    const { server, board } = setup(); await board.refresh();
    server.inject.set("PATCH /api/v1/queue/control", { reply: json({}, 412) });
    await board.setDispatch("running"); expect(board.store.get().action?.phase).toBe("conflict");
    await board.recheck(); expect(server.writes()).toHaveLength(1); expect(board.store.get().phase).toBe("done");
  });
  for (const [status, phase] of [[409, "rejected"], [422, "rejected"], [429, "rejected"], [500, "unknown"], [503, "unknown"]] as const) {
    it(`${status} is ${phase}`, async () => {
      const { server, board } = setup(); await board.refresh();
      server.inject.set("PATCH /api/v1/queue/control", { reply: json({}, status) });
      await board.setDispatch("running"); expect(board.store.get().action?.phase).toBe(phase);
    });
  }
  it("failed reads disable stale revision writes", async () => {
    const { api, board, server } = setup(); await board.refresh();
    api.getQueue = async () => { throw new ApiError({ kind: "network" }); };
    await board.refresh(); await board.setDispatch("running"); expect(server.writes()).toHaveLength(0);
  });
});
