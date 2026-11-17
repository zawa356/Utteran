import { describe, expect, it, vi } from "vitest";
import type { FetchLike } from "../../api/http";
import { createFakeHost } from "../../host/fake";
import { json } from "../../test/fakeFetch";
import { fakeV1Server } from "../../test/v1Server";
import { createCore } from "../core";

/**
 * PR #53 Windows acceptance, P3: the auxiliary list "jobs on the server (v1)" kept showing the
 * internal jobs of a row that had just been hidden. The list is re-read only after a hide/show is
 * settled (`done`); it is never edited optimistically and never re-read on an unsettled outcome.
 */
function setup() {
  const server = fakeV1Server();
  const lines: string[] = [];
  // While set, every `GET /api/v1/jobs` waits for it (a slow list read after a successful write).
  const gate: { hold: Promise<void> | null; waiting: number } = { hold: null, waiting: 0 };
  const fetch: FetchLike = async (url, init) => {
    const line = `${init.method} ${new URL(url, "http://x").pathname}`;
    lines.push(line);
    if (gate.hold !== null && line === "GET /api/v1/jobs") {
      gate.waiting += 1;
      await gate.hold;
    }
    return server.fetch(url, init);
  };
  const core = createCore({ fetch, host: createFakeHost(), apiMode: "v1-dev" });
  return { server, lines, gate, core, board: core.v1!.board, job: core.v1!.job };
}

/** One finished logical job made of two internal jobs (failed → retried → succeeded). */
async function finishedRetryChain(s: ReturnType<typeof setup>) {
  const [first] = s.server.addJobs(1);
  s.server.setStatus(first!, "failed");
  await s.board.refresh();
  await s.board.retry(first!);
  const second = s.server.jobs[1]!.id as string;
  s.server.setStatus(second, "succeeded");
  s.board.dismissAction();
  await s.board.refresh();
  expect(s.board.store.get().history[0]!.jobIds).toEqual([first, second]);
  return { root: first!, ids: [first!, second] };
}

const recentIds = (s: ReturnType<typeof setup>) => s.job.store.get().recent.items.map((item) => item.id);
const listReads = (s: ReturnType<typeof setup>) => s.lines.filter((line) => line === "GET /api/v1/jobs").length;
const visibilityPuts = (s: ReturnType<typeof setup>) => s.lines.filter((line) => line.startsWith("PUT ") && line.endsWith("/visibility")).length;
const settled = (s: ReturnType<typeof setup>) => vi.waitFor(() => expect(s.job.store.get().recent.phase).not.toBe("pending"));

describe("v1 board hide/show keeps the auxiliary server job list in step", () => {
  it("a hidden retry chain leaves the loaded list (every internal job) and returns on show; nothing is deleted", async () => {
    const s = setup();
    const { root, ids } = await finishedRetryChain(s);
    await s.job.loadRecent();
    expect(recentIds(s).sort()).toEqual([...ids].sort());
    const before = JSON.stringify(s.server.jobs);

    await s.board.setHidden(root, true);
    expect(s.board.store.get().action).toMatchObject({ kind: "hide", phase: "done", remaining: [] });
    await settled(s);
    expect(s.job.store.get().recent).toMatchObject({ phase: "done", error: null });
    expect(recentIds(s)).toEqual([]);
    expect(s.board.store.get().history).toEqual([]);

    // No polling follows: the lists are read once per settled write.
    const reads = listReads(s);
    await Promise.resolve();
    expect(listReads(s)).toBe(reads);

    s.board.dismissAction();
    s.board.setShowHidden(true);
    await s.board.refresh();
    await s.board.setHidden(root, false);
    expect(s.board.store.get().action).toMatchObject({ kind: "show", phase: "done" });
    await settled(s);
    expect(recentIds(s).sort()).toEqual([...ids].sort());

    expect(JSON.stringify(s.server.jobs)).toBe(before); // job records and revisions untouched
    expect(s.lines.filter((line) => line.startsWith("DELETE "))).toEqual([]);
  });

  it("a list the user never asked for is not read because of a hide", async () => {
    const s = setup();
    const { root } = await finishedRetryChain(s);
    const untouched = s.job.store.get().recent;
    expect(untouched.phase).toBe("idle");
    await s.board.setHidden(root, true);
    expect(s.board.store.get().action).toMatchObject({ phase: "done" });
    await Promise.resolve();
    expect(s.job.store.get().recent).toBe(untouched);
  });

  it("an unknown outcome changes nothing in the list and resends nothing until the explicit re-check", async () => {
    const s = setup();
    const { root, ids } = await finishedRetryChain(s);
    await s.job.loadRecent();
    const shown = s.job.store.get().recent;
    // The second internal job's answer is lost; whether it was applied is unknown to the page.
    s.server.inject.set(`PUT /api/v1/jobs/${ids[1]}/visibility`, { reply: new TypeError("network"), apply: true });
    await s.board.setHidden(root, true);
    expect(s.board.store.get().action).toMatchObject({ kind: "hide", phase: "unknown", remaining: [ids[1]] });
    await Promise.resolve();
    // Not treated as completed: the list is exactly what the server answered last, not edited locally.
    expect(s.job.store.get().recent).toBe(shown);
    expect(recentIds(s).sort()).toEqual([...ids].sort());
    const puts = visibilityPuts(s);
    await s.board.refresh();
    expect(visibilityPuts(s)).toBe(puts); // no unsolicited resend
    expect(s.job.store.get().recent).toBe(shown);

    await s.board.recheck();
    expect(s.board.store.get().action).toMatchObject({ phase: "done", remaining: [] });
    await settled(s);
    expect(recentIds(s)).toEqual([]);
  });

  it("a conflict (412) or a refusal does not re-read the list as if the hide had completed", async () => {
    const error = (status: number, code: string) => json(status, { schema_version: 1, error: { code, message_key: "x", request_id: "r", retryable: false, issues: [] } });
    for (const [status, code, phase] of [
      [412, "revision_conflict", "conflict"],
      [409, "history_visibility_unavailable", "rejected"],
    ] as const) {
      const s = setup();
      const { root, ids } = await finishedRetryChain(s);
      await s.job.loadRecent();
      const shown = s.job.store.get().recent;
      s.server.inject.set(`PUT /api/v1/jobs/${ids[0]}/visibility`, { reply: error(status, code) });
      await s.board.setHidden(root, true);
      expect(s.board.store.get().action).toMatchObject({ kind: "hide", phase });
      await Promise.resolve();
      expect(s.job.store.get().recent).toBe(shown);
      expect(visibilityPuts(s)).toBe(1); // stopped at the first problem, nothing resent
    }
  });

  it("closing the 'done' message while the lists are still being re-read does not lose the sync (PR #53 review)", async () => {
    const s = setup();
    const { root, ids } = await finishedRetryChain(s);
    await s.job.loadRecent();
    s.board.setShowHidden(true);
    await s.board.refresh();

    for (const [hidden, expected] of [
      [true, []],
      [false, [...ids].sort()],
    ] as const) {
      let release!: () => void;
      s.gate.hold = new Promise<void>((resolve) => (release = resolve));
      s.gate.waiting = 0;
      const puts = visibilityPuts(s);
      const writing = s.board.setHidden(root, hidden);
      // Both PUTs succeeded, the message says so, and the re-read of the lists is still waiting.
      await vi.waitFor(() => expect(s.gate.waiting).toBe(1));
      expect(s.board.store.get().action).toMatchObject({ kind: hidden ? "hide" : "show", phase: "done" });
      expect(visibilityPuts(s)).toBe(puts + 2);
      s.board.dismissAction();
      expect(s.board.store.get().action).toBeNull();
      // Nothing is edited locally while the answer is outstanding.
      expect(recentIds(s).sort()).toEqual(hidden ? [...ids].sort() : []);
      s.gate.hold = null;
      release();
      await writing;
      await settled(s);
      expect(recentIds(s).sort()).toEqual(expected);
      expect(s.board.store.get().action).toBeNull(); // not re-opened, not attributed to a later write
      expect(visibilityPuts(s)).toBe(puts + 2);
    }
    expect(s.lines.filter((line) => line.startsWith("DELETE "))).toEqual([]);
  });

  it("one settled write notifies once; a disposed board notifies nothing", async () => {
    const s = setup();
    const { root } = await finishedRetryChain(s);
    await s.job.loadRecent();
    const reads = listReads(s);
    await s.board.setHidden(root, true);
    await settled(s);
    await Promise.resolve();
    // One re-read of the board and one of the auxiliary list (each a single page here).
    expect(listReads(s)).toBe(reads + 2);

    const d = setup();
    const chain = await finishedRetryChain(d);
    await d.job.loadRecent();
    const shown = d.job.store.get().recent;
    let release!: () => void;
    d.gate.hold = new Promise<void>((resolve) => (release = resolve));
    const writing = d.board.setHidden(chain.root, true);
    await vi.waitFor(() => expect(d.gate.waiting).toBe(1));
    d.board.dispose();
    d.gate.hold = null;
    release();
    await writing;
    await Promise.resolve();
    expect(d.job.store.get().recent).toBe(shown);
  });
});
