import { describe, expect, it } from "vitest";
import { createHttpClient } from "../../api/http";
import { createV1ResourcesApi } from "../../api/v1Resources";
import { fakeV1Server } from "../../test/v1Server";
import { V1JobBoardController } from "./jobBoard";

/**
 * Bundle D2-A: two views read the one board (Expert ③ `V1JobBoard` and the queue page). A view
 * holds the polling with a lease; the polling runs while any lease is held and stops when the last
 * one is released, whatever order Vue mounts and unmounts them in. There is still one timer.
 */
function timers() {
  const pending: { run: () => void; ms: number }[] = [];
  return {
    pending,
    scheduler: {
      setTimeout: (run: () => void, ms: number) => {
        const entry = { run, ms };
        pending.push(entry);
        return entry;
      },
      clearTimeout: (handle: unknown) => {
        const index = pending.indexOf(handle as (typeof pending)[number]);
        if (index >= 0) pending.splice(index, 1);
      },
    },
  };
}

async function setup() {
  const server = fakeV1Server();
  server.addJobs(2);
  const t = timers();
  const api = createV1ResourcesApi(createHttpClient({ fetch: server.fetch }));
  const board = new V1JobBoardController(api, { scheduler: t.scheduler });
  return { server, t, board };
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const reads = (server: ReturnType<typeof fakeV1Server>) => server.calls().filter((c) => c === "GET /api/v1/queue").length;

describe("D2A-08 board polling ownership (one controller, leases)", () => {
  it("the new view mounts before the old one unmounts: polling continues with one timer", async () => {
    const { t, board } = await setup();
    const releaseOld = board.retainWatch();
    await settle();
    expect(t.pending).toHaveLength(1);
    const releaseNew = board.retainWatch();
    await settle();
    releaseOld();
    await settle();
    expect(t.pending).toHaveLength(1);
    releaseNew();
    expect(t.pending).toHaveLength(0);
  });

  it("the old view unmounts before the new one mounts: polling restarts once", async () => {
    const { server, t, board } = await setup();
    const releaseOld = board.retainWatch();
    await settle();
    releaseOld();
    expect(t.pending).toHaveLength(0);
    const before = reads(server);
    const releaseNew = board.retainWatch();
    await settle();
    expect(reads(server)).toBe(before + 1);
    expect(t.pending).toHaveLength(1);
    releaseNew();
    expect(t.pending).toHaveLength(0);
  });

  it("releasing a lease twice does not release another view's lease; ten cycles leave no timer", async () => {
    const { t, board } = await setup();
    const keep = board.retainWatch();
    const other = board.retainWatch();
    other();
    other();
    await settle();
    expect(t.pending).toHaveLength(1);
    for (let i = 0; i < 10; i += 1) {
      const lease = board.retainWatch();
      await settle();
      lease();
    }
    await settle();
    expect(t.pending).toHaveLength(1);
    keep();
    expect(t.pending).toHaveLength(0);
  });

  it("leases only read: no write is ever sent", async () => {
    const { server, t, board } = await setup();
    const lease = board.retainWatch();
    await settle();
    t.pending[0]!.run();
    await settle();
    lease();
    expect(server.writes()).toEqual([]);
  });
});
