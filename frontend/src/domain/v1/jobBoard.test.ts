import { describe, expect, it } from "vitest";
import { createHttpClient } from "../../api/http";
import { createV1ResourcesApi } from "../../api/v1Resources";
import { json } from "../../test/fakeFetch";
import { fakeV1Server } from "../../test/v1Server";
import { BOARD_POLL_MS, V1JobBoardController } from "./jobBoard";

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

function board(options: { visibility?: boolean } = {}) {
  const server = fakeV1Server();
  if (options.visibility === false) server.visibility = false;
  const t = timers();
  const api = createV1ResourcesApi(createHttpClient({ fetch: server.fetch }));
  return { server, api, t, board: new V1JobBoardController(api, { scheduler: t.scheduler }) };
}
const writes = (server: ReturnType<typeof fakeV1Server>) => server.writes().map((w) => w.line);
const rows = (groups: readonly { rootId: string }[]) => groups.map((g) => g.rootId);

describe("v1 job board: logical jobs, queue and history", () => {
  it("splits logical jobs into queue (oldest first) and history (newest first); only reads", async () => {
    const { server, board: b } = board();
    const [a, c, d] = server.addJobs(3);
    server.setStatus(a!, "succeeded");
    server.setStatus(c!, "failed");
    await b.refresh();
    const s = b.store.get();
    expect(s.phase).toBe("done");
    expect(rows(s.queue)).toEqual([d]);
    expect(rows(s.history)).toEqual([c, a]);
    expect(s.queueView?.dispatch).toBe("paused");
    expect(writes(server)).toEqual([]);
  });

  it("a retry is the same row: the new internal job is linked, and the row moves back to the queue", async () => {
    const { server, board: b } = board();
    const [a] = server.addJobs(1);
    server.setStatus(a!, "failed");
    await b.refresh();
    const row = b.store.get().history[0]!;
    expect(b.canRetry(row)).toBe(true);
    await b.retry(row.rootId);
    const s = b.store.get();
    expect(s.action).toMatchObject({ kind: "retry", phase: "done" });
    expect(s.history).toEqual([]);
    expect(s.queue).toHaveLength(1);
    expect(s.queue[0]!.rootId).toBe(a); // same logical job
    expect(s.queue[0]!.jobIds).toHaveLength(2);
    expect(s.queue[0]!.latest.retryOf).toBe(a);
    const sent = server.writes()[0]!;
    expect(sent.line).toBe(`POST /api/v1/jobs/${a}/retry`);
    expect(sent.ifMatch).toBe('"2"');
    expect(JSON.parse(sent.body!)).toEqual({ schema_version: 1 });
    // The failed internal job itself was not changed.
    expect(server.jobs.find((j) => j.id === a)).toMatchObject({ status: "failed", revision: 2 });
  });

  it("cancel targets the newest internal job with its frozen revision; a conflict re-reads", async () => {
    const { server, board: b } = board();
    const [a] = server.addJobs(1);
    await b.refresh();
    server.setStatus(a!, "running"); // the screen still holds revision 1
    await b.cancel(a!);
    expect(b.store.get().action).toMatchObject({ kind: "cancel", phase: "conflict" });
    expect(server.jobs[0]!.status).toBe("running");
    b.dismissAction();
    await b.cancel(a!);
    expect(b.store.get().action).toMatchObject({ kind: "cancel", phase: "done" });
    expect(server.jobs[0]!.status).toBe("cancelling");
    expect(server.writes().map((w) => w.ifMatch)).toEqual(['"1"', '"2"']);
  });

  it("an unknown outcome blocks other writes and is re-checked with the same key, body and target only", async () => {
    const { server, board: b } = board();
    const [a, c] = server.addJobs(2);
    server.setStatus(a!, "failed");
    server.setStatus(c!, "failed");
    await b.refresh();
    server.inject.set(`POST /api/v1/jobs/${a}/retry`, { reply: new TypeError("network"), apply: true });
    await b.retry(a!);
    expect(b.store.get().action).toMatchObject({ kind: "retry", phase: "unknown" });
    // The row already shows the server truth (the retry was applied), but no second write may start.
    expect(b.actionOpen()).toBe(true);
    await b.retry(c!);
    await b.cancel(a!);
    await b.setHidden(c!, true);
    expect(writes(server)).toEqual([`POST /api/v1/jobs/${a}/retry`]);
    await b.recheck();
    const sent = server.writes();
    expect(sent).toHaveLength(2);
    expect(sent[1]).toMatchObject({ line: sent[0]!.line, key: sent[0]!.key, body: sent[0]!.body, ifMatch: sent[0]!.ifMatch });
    expect(b.store.get().action).toMatchObject({ phase: "done" });
    expect(server.jobs.filter((j) => j.retry_of === a)).toHaveLength(1); // replayed, not retried twice
  });
});

describe("v1 job board: hide / show (nothing is deleted)", () => {
  it("hides every internal job of the row with its own visibility revision, then shows it again", async () => {
    const { server, board: b } = board();
    const [a] = server.addJobs(1);
    server.setStatus(a!, "failed");
    await b.refresh();
    await b.retry(a!);
    const second = server.jobs[1]!.id as string;
    server.setStatus(second, "succeeded");
    b.dismissAction();
    await b.refresh();
    const before = JSON.stringify(server.jobs);
    await b.setHidden(a!, true);
    expect(b.store.get().action).toMatchObject({ kind: "hide", phase: "done", remaining: [] });
    expect(b.store.get().history).toEqual([]);
    const puts = server.writes().filter((w) => w.line.endsWith("/visibility"));
    expect(puts.map((w) => w.line)).toEqual([`PUT /api/v1/jobs/${a}/visibility`, `PUT /api/v1/jobs/${second}/visibility`]);
    expect(puts.every((w) => w.ifMatch === '"0"' && w.key === null)).toBe(true);
    expect(JSON.stringify(server.jobs)).toBe(before); // job state and revisions untouched
    b.dismissAction();
    b.setShowHidden(true);
    await b.refresh();
    expect(rows(b.store.get().hidden)).toEqual([a]);
    expect(b.store.get().hidden[0]!.jobIds).toEqual([a, second]);
    await b.setHidden(a!, false);
    expect(b.store.get().action).toMatchObject({ kind: "show", phase: "done" });
    expect(rows(b.store.get().history)).toEqual([a]);
    expect(b.store.get().hidden).toEqual([]);
    expect(server.jobs).toHaveLength(2);
  });

  it("a row with an unfinished internal job cannot be hidden; a lost answer is repeated safely", async () => {
    const { server, board: b } = board();
    const [a, c] = server.addJobs(2);
    server.setStatus(c!, "succeeded");
    await b.refresh();
    expect(b.canHide(b.store.get().queue[0]!)).toBe(false);
    await b.setHidden(a!, true);
    expect(writes(server)).toEqual([]);
    server.inject.set(`PUT /api/v1/jobs/${c}/visibility`, { reply: new TypeError("network"), apply: true });
    await b.setHidden(c!, true);
    expect(b.store.get().action).toMatchObject({ kind: "hide", phase: "unknown", remaining: [c] });
    await b.recheck();
    expect(b.store.get().action).toMatchObject({ phase: "done", remaining: [] });
    // The re-check read the stored state and did not write a second revision.
    expect((server.visibility as Map<string, { revision: number }>).get(c!)!.revision).toBe(1);
  });

  it("a store without layout v7 simply has no hide/show (422 on the filter is not an error)", async () => {
    const { server, board: b } = board({ visibility: false });
    const [a] = server.addJobs(1);
    server.setStatus(a!, "succeeded");
    b.setShowHidden(true);
    await b.refresh();
    const s = b.store.get();
    expect(s).toMatchObject({ phase: "done", visibilitySupported: false, hidden: [] });
    expect(rows(s.history)).toEqual([a]);
    expect(b.canHide(s.history[0]!)).toBe(false);
    await b.setHidden(a!, true);
    expect(writes(server)).toEqual([]);
  });
});

describe("v1 job board: lists, polling and artifacts", () => {
  it("an inconsistent or failing page is an error and nothing partial is shown", async () => {
    const { server, board: b } = board();
    server.addJobs(150);
    server.listPage.set(100, json(500, { schema_version: 1, error: { code: "store_busy", message_key: "x", request_id: "r", retryable: true, issues: [] } }));
    await b.refresh();
    expect(b.store.get()).toMatchObject({ phase: "error", queue: [], history: [] });
    await b.refresh();
    expect(b.store.get().phase).toBe("done");
    expect(b.store.get().queue).toHaveLength(150);
  });

  it("polls only while watched and something is unfinished; stops on dispose", async () => {
    const { server, board: b, t } = board();
    const [a] = server.addJobs(1);
    await b.refresh();
    expect(t.pending).toHaveLength(0); // not watched: no timer
    b.watch(true);
    await Promise.resolve();
    await b.refresh();
    expect(t.pending).toHaveLength(1);
    expect(t.pending[0]!.ms).toBe(BOARD_POLL_MS);
    server.setStatus(a!, "succeeded");
    await b.refresh();
    expect(t.pending).toHaveLength(0); // everything finished: no more polling
    b.dispose();
    await b.refresh();
    expect(t.pending).toHaveLength(0);
  });

  it("artifact views are read on demand and keep format, size and integrity only", async () => {
    const { server, board: b } = board();
    const [a] = server.addJobs(1);
    server.artifacts.set(a!, { schema_version: 1, job_id: a, state: "available", items: [{ format: "srt", size: 12, integrity: "changed" }], unexpected_count: 1 });
    await b.loadArtifacts(a!);
    expect(b.store.get().artifacts[a!]).toMatchObject({ phase: "done", view: { state: "available", unexpectedCount: 1, items: [{ format: "srt", size: 12, integrity: "changed" }] } });
    server.inject.set(`GET /api/v1/jobs/${a}/artifacts`, { reply: json(404, { schema_version: 1, error: { code: "resource_not_found", message_key: "x", request_id: "r", retryable: false, issues: [] } }) });
    await b.loadArtifacts(a!);
    expect(b.store.get().artifacts[a!]).toMatchObject({ phase: "error", view: null });
  });
});

describe("PR #53 review P2-1: the queue order is B7's order, not the age of a row's first job", () => {
  it("A failed, B waiting, retry A: the retried row comes after B, with B7's positions", async () => {
    const { server, board: b } = board();
    const [a, bId] = server.addJobs(2);
    server.setStatus(a!, "failed");
    await b.refresh();
    await b.retry(a!);
    const retried = server.jobs[2]!.id as string;
    const s = b.store.get();
    // The row keeps its key (root A) but sits where its unfinished job is in the queue: after B.
    expect(rows(s.queue)).toEqual([bId, a]);
    expect(b.current(s.queue[1]!).id).toBe(retried);
    expect(b.placeOf(bId!)).toMatchObject({ active: false, position: 1 });
    expect(b.placeOf(retried)).toMatchObject({ active: false, position: 2 });
    expect(b.placeOf(a!)).toBeNull(); // the failed job itself is not in the queue
  });

  it("follows a B7 reorder, shows the active item first and reports holds", async () => {
    const { server, board: b } = board();
    const [a, c, d] = server.addJobs(3);
    await b.refresh();
    expect(rows(b.store.get().queue)).toEqual([a, c, d]);
    server.queueOrder = [d!, a!, c!];
    server.held.add(a!);
    await b.refresh();
    expect(rows(b.store.get().queue)).toEqual([d, a, c]);
    expect(b.placeOf(a!)).toMatchObject({ position: 2, held: true });
    server.setStatus(c!, "running");
    await b.refresh();
    expect(rows(b.store.get().queue)).toEqual([c, d, a]);
    expect(b.placeOf(c!)).toMatchObject({ active: true, position: null });
  });

  it("a row whose place the queue did not report comes last and claims no position", async () => {
    const { server, board: b } = board();
    const [a, c] = server.addJobs(2);
    server.setStatus(a!, "waiting_user"); // unfinished, but not a queue item in this snapshot
    await b.refresh();
    expect(rows(b.store.get().queue)).toEqual([c, a]);
    expect(b.placeOf(a!)).toBeNull();
  });
});

describe("PR #53 review P2-2: a branched retry chain never loses an unfinished job", () => {
  async function branched() {
    const ctx = board();
    const [a] = ctx.server.addJobs(1);
    ctx.server.setStatus(a!, "failed");
    await ctx.board.refresh();
    // B7 allows two retries of the same failed job with different keys (two sibling jobs).
    const { MutationIntent } = await import("../../api/idempotency");
    const first = await ctx.api.retry(a!, 2, new MutationIntent({ schema_version: 1 }));
    const second = await ctx.api.retry(a!, 2, new MutationIntent({ schema_version: 1 }));
    return { ...ctx, a: a!, b: first.job.id, c: second.job.id };
  }

  it("the newest branch is cancelled, the older one still waits: the row stays in the queue and is polled", async () => {
    const { server, board: b, t, a, b: older, c: newer } = await branched();
    server.setStatus(newer, "cancelled");
    b.watch(true);
    await b.refresh();
    const s = b.store.get();
    expect(s.history).toEqual([]);
    expect(rows(s.queue)).toEqual([a]);
    const row = s.queue[0]!;
    expect(row.latest.id).toBe(newer); // the newest job is finished ...
    expect(row.open.map((j) => j.id)).toEqual([older]); // ... but one job is not
    expect(row.branched).toBe(true);
    expect(b.current(row).id).toBe(older);
    expect(t.pending).toHaveLength(1); // still polling
    // No retry while something is unfinished (it would add another branch), no hide either.
    expect(b.canRetry(row)).toBe(false);
    expect(b.canHide(row)).toBe(false);
    // Cancel acts on the unfinished job, not on "the newest".
    await b.cancel(a);
    expect(server.writes().at(-1)!.line).toBe(`POST /api/v1/jobs/${older}/cancel`);
    expect(server.jobs.find((j) => j.id === older)!.status).toBe("cancelled");
    const after = b.store.get();
    expect(after.queue).toEqual([]);
    expect(rows(after.history)).toEqual([a]);
    expect(t.pending).toHaveLength(0); // everything finished: polling stops
    expect(b.canRetry(after.history[0]!)).toBe(true);
  });

  it("two unfinished branches: both are listed and each is cancelled by its own id", async () => {
    const { server, board: b, a, b: older, c: newer } = await branched();
    await b.refresh();
    const row = b.store.get().queue[0]!;
    expect(row.open.map((j) => j.id)).toEqual([older, newer]);
    expect(b.current(row).id).toBe(older); // the one B7 will run first
    expect(b.canCancel(row, newer)).toBe(true);
    expect(b.canCancel(row, a)).toBe(false); // a finished job of the row is not a cancel target
    await b.cancel(a, newer);
    expect(server.writes().at(-1)!.line).toBe(`POST /api/v1/jobs/${newer}/cancel`);
    expect(server.jobs.find((j) => j.id === older)!.status).toBe("queued");
    expect(b.store.get().queue[0]!.open.map((j) => j.id)).toEqual([older]);
    await b.cancel(a, "job_" + "0".repeat(32)); // not a job of this row: nothing is sent
    expect(server.writes().filter((w) => w.line.endsWith("/cancel"))).toHaveLength(1);
  });
});

describe("D3-A finished history with mixed visibility", () => {
  it("keeps the same logical root in each view and re-shows every surviving attempt", async () => {
    const { server, board: b } = board();
    const [root, child] = server.addJobs(2);
    server.setStatus(root!, "failed");
    server.setStatus(child!, "succeeded");
    server.jobs[1]!.retry_of = root!;
    if (server.visibility === false) throw new Error("expected visibility support");
    server.visibility.set(root!, { hidden: true, revision: 3 });

    await b.refresh();
    expect(b.store.get().history).toMatchObject([{ rootId: root, jobIds: [child], latest: { id: child } }]);
    expect(b.store.get().hidden).toEqual([]);
    b.setShowHidden(true);
    await b.refresh();
    expect(b.store.get().hidden).toMatchObject([{ rootId: root, jobIds: [root], latest: { id: root } }]);
    await b.setHidden(root!, false);
    expect(b.store.get().history).toMatchObject([{ rootId: root, jobIds: [root, child] }]);
    expect(b.store.get().hidden).toEqual([]);
    expect(server.writes().map(w => [w.line, w.ifMatch])).toEqual([[`PUT /api/v1/jobs/${root}/visibility`, '"3"']]);
  });

  it("excludes a hidden ancestor from finished history while any visible branch is open", async () => {
    const { server, board: b } = board();
    const [root, waiting, success] = server.addJobs(3);
    server.setStatus(root!, "failed");
    server.setStatus(success!, "succeeded");
    server.jobs[1]!.retry_of = root!;
    server.jobs[2]!.retry_of = root!;
    if (server.visibility === false) throw new Error("expected visibility support");
    server.visibility.set(root!, { hidden: true, revision: 1 });

    b.setShowHidden(true);
    await b.refresh();
    expect(b.store.get()).toMatchObject({ queue: [{ rootId: root, open: [{ id: waiting }] }], history: [], hidden: [] });
    expect(server.writes()).toEqual([]);
  });
});

it("D3A-09: unknown visibility PUT retains its CAS if another visibility revision intervenes", async () => {
  const { server, board: b } = board();
  const [id] = server.addJobs(1);
  server.setStatus(id!, "succeeded");
  await b.refresh();
  server.inject.set(`PUT /api/v1/jobs/${id}/visibility`, { reply: new TypeError("network") });
  await b.setHidden(id!, true);
  expect(b.store.get().action?.phase).toBe("unknown");
  if (server.visibility === false) throw new Error("expected visibility support");
  server.visibility.set(id!, { hidden: false, revision: 2 });
  await b.refresh();
  expect(b.actionOpen()).toBe(true);
  await b.recheck();
  expect(server.writes().map(w => w.ifMatch)).toEqual(['"0"', '"0"']);
  expect(b.store.get().action?.phase).toBe("conflict");
  expect(b.actionOpen()).toBe(false);
});
