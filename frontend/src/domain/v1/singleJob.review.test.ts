/**
 * PR #48 owner review (2026-11-13): P1 cancel target frozen, P2 late cancel answers never touch
 * another job, P2 the recent list reads every cursor page. Synthetic fixtures only.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FetchLike } from "../../api/http";
import { createFakeHost } from "../../host/fake";
import { deferred, json } from "../../test/fakeFetch";
import { fakeV1Server, V1_LIST_PAGE, V1_POLICY, V1_PRESET } from "../../test/v1Server";
import { createCore } from "../core";

const OUT = ["", "synthetic", "out"].join("/");
const IN = ["", "synthetic", "media", "talk.wav"].join("/");

function setup() {
  const v1 = fakeV1Server();
  const lines: string[] = [];
  const fetch: FetchLike = async (url, init) => {
    const path = new URL(url, "http://x").pathname;
    lines.push(`${init.method} ${url}`);
    if (path.startsWith("/api/v1/")) return v1.fetch(url, init);
    if (path === "/api/settings") return json(200, { theme: "system", language: "ja", default_profile: null, default_output_dir: OUT });
    if (path === "/api/environment") return json(503, { detail: "synthetic" });
    throw new Error(`unexpected ${init.method} ${url}`);
  };
  const core = createCore({ fetch, host: createFakeHost(), apiMode: "v1-dev" });
  return { v1, lines, core, job: core.v1!.job };
}

/** Two durable jobs A (followed) and B, both at revision 1 — the dangerous same-revision case. */
async function followingA() {
  const s = setup();
  await s.core.start();
  const [a, b] = s.v1.addJobs(2) as [string, string];
  await s.job.loadRecent();
  s.job.follow(a);
  await vi.advanceTimersByTimeAsync(0);
  expect(s.job.store.get().job?.id).toBe(a);
  return { ...s, a, b };
}

const cancelPosts = (v1: ReturnType<typeof fakeV1Server>) => v1.writes().filter((w) => w.line.endsWith("/cancel"));

describe("P1: the cancel target, key and If-Match are frozen per cancel intent", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("P1-A: A's cancel pending → switching to B is refused; A's unknown re-check goes to A only (0 POSTs to B)", async () => {
    const { v1, job, a, b } = await followingA();
    const lost = deferred<Response>();
    v1.inject.set(`POST /api/v1/jobs/${a}/cancel`, { reply: () => lost.promise });
    const pending = job.cancel();
    await vi.advanceTimersByTimeAsync(0);
    expect(job.store.get().cancel.phase).toBe("pending");
    job.follow(b); // refused while A's cancel is in flight
    expect(job.store.get().job?.id).toBe(a);
    expect(job.switchBlocked()).toBe("cancel_pending");
    lost.reject(new TypeError("network down"));
    await pending;
    expect(job.store.get().cancel.phase).toBe("unknown");
    job.follow(b); // still refused: the unknown A intent must be re-checked or discarded first
    expect(job.store.get().job?.id).toBe(a);
    expect(job.switchBlocked()).toBe("cancel_unknown");
    await Promise.all([job.recheckCancel(), job.recheckCancel()]); // a double re-check sends once
    const posts = cancelPosts(v1);
    expect(posts.filter((p) => p.line.includes(b))).toEqual([]);
    expect(posts).toHaveLength(2);
    expect(posts.map((p) => p.line)).toEqual([`POST /api/v1/jobs/${a}/cancel`, `POST /api/v1/jobs/${a}/cancel`]);
    expect(posts[1]!.key).toBe(posts[0]!.key);
    expect(posts[1]!.ifMatch).toBe(posts[0]!.ifMatch);
    expect(posts[1]!.body).toBe(posts[0]!.body);
    // The queued A is cancelled at once by B7 (202 → final); B is untouched.
    expect(job.store.get().cancel).toMatchObject({ phase: "done", jobId: a });
    expect(v1.jobs.find((j) => j.id === a)!.status).toBe("cancelled");
    expect(v1.jobs.find((j) => j.id === b)!.status).toBe("queued");
  });

  it("P1-B: same revision on A and B — after discarding A's unknown intent, B gets its own new key; A can no longer be re-checked", async () => {
    const { v1, job, a, b } = await followingA();
    v1.inject.set(`POST /api/v1/jobs/${a}/cancel`, { reply: json(503, {}) });
    await job.cancel();
    expect(job.store.get().cancel.phase).toBe("unknown");
    job.discardCancel();
    expect(job.switchBlocked()).toBeNull();
    await job.recheckCancel(); // nothing left to re-check
    expect(cancelPosts(v1)).toHaveLength(1);
    job.follow(b);
    await vi.advanceTimersByTimeAsync(0);
    expect(job.store.get().job?.id).toBe(b);
    await job.recheckCancel();
    expect(cancelPosts(v1)).toHaveLength(1);
    await job.cancel(); // an explicit new intent for B
    const posts = cancelPosts(v1);
    expect(posts).toHaveLength(2);
    expect(posts[1]!.line).toBe(`POST /api/v1/jobs/${b}/cancel`);
    expect(posts[1]!.key).not.toBe(posts[0]!.key);
    expect(posts[0]!.ifMatch).toBe(posts[1]!.ifMatch); // same revision "1": only the key/URL keep them apart
  });

  it("a new cancel is refused while one is pending or unknown (no second intent next to an open one)", async () => {
    const { v1, job, a } = await followingA();
    v1.inject.set(`POST /api/v1/jobs/${a}/cancel`, { reply: new TypeError("lost") });
    await job.cancel();
    await job.cancel();
    expect(cancelPosts(v1)).toHaveLength(1);
  });
});

describe("P2: a cancel answer only applies to the job and target it was sent for", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("P2-A: the answer guard refuses another job id, another target epoch or a replaced intent", async () => {
    const { job, a, b } = await followingA();
    const ctx = job.cancelContextForTest();
    expect(ctx).toBeNull();
    const pending = job.cancel();
    const frozen = job.cancelContextForTest()!;
    expect(frozen).toMatchObject({ jobId: a, revision: 1 });
    await pending;
    expect(job.answerApplies(frozen, { ...frozen.target, jobId: b })).toBe(false);
    expect(job.answerApplies(frozen, { ...frozen.target, epoch: frozen.target.epoch + 1 })).toBe(false);
    expect(job.answerApplies({ ...frozen }, frozen.target)).toBe(false); // not the live intent object
  });

  it("P2-A: applyJob never takes another job's answer over the shown one", async () => {
    const { v1, job, a, b } = await followingA();
    const other = { ...(v1.jobs.find((j) => j.id === b) as object), status: "cancelled", revision: 9 };
    v1.inject.set(`POST /api/v1/jobs/${a}/cancel`, { reply: json(202, other) });
    await job.cancel();
    expect(job.store.get().job).toMatchObject({ id: a, status: "queued", revision: 1 });
  });

  it("P2-B: a 4xx or timeout on A's cancel leaves A's display intact; dispose during a pending cancel writes nothing", async () => {
    const { v1, job, a } = await followingA();
    v1.inject.set(`POST /api/v1/jobs/${a}/cancel`, { reply: json(409, { schema_version: 1, error: { code: "cancel_mode_unsupported", message_key: "x", request_id: "r", retryable: false, issues: [] } }) });
    await job.cancel();
    expect(job.store.get()).toMatchObject({ cancel: { phase: "rejected" }, job: { id: a, revision: 1 } });
    const late = deferred<Response>();
    v1.inject.set(`POST /api/v1/jobs/${a}/cancel`, { reply: () => late.promise });
    const pending = job.cancel();
    await vi.advanceTimersByTimeAsync(0);
    job.dispose();
    const before = job.store.get();
    late.resolve(json(202, { ...(v1.jobs[0] as object), status: "cancelled", revision: 5 }));
    await pending;
    expect(job.store.get()).toBe(before);
  });
});

describe("P2: the recent list reads every cursor page and keeps the true newest 20", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  for (const total of [0, 20, 100, 101, 201, 450]) {
    it(`P2-C: ${total} jobs → the newest ${Math.min(total, 20)}, newest first, every page read`, async () => {
      const s = setup();
      const ids = s.v1.addJobs(total);
      await s.job.loadRecent();
      const shown = s.job.store.get().recent;
      expect(shown.phase).toBe("done");
      expect(shown.items.map((j) => j.id)).toEqual(ids.slice(-20).reverse());
      const pages = s.lines.filter((l) => l.startsWith("GET /api/v1/jobs") && !/\/jobs\/job_/.test(l));
      expect(pages).toHaveLength(Math.max(1, Math.ceil(total / V1_LIST_PAGE)));
      expect(pages.slice(1).every((l) => l.includes("cursor=j2."))).toBe(true);
    });
  }

  it("P2-D: a failing later page (409 expired, 503, network) is an error, never a finished partial list; reload recovers", async () => {
    for (const reply of [
      json(409, { schema_version: 1, error: { code: "cursor_expired", message_key: "x", request_id: "r", retryable: false, issues: [] } }),
      json(503, {}),
      new TypeError("network down"),
    ]) {
      const s = setup();
      const ids = s.v1.addJobs(150);
      s.v1.listPage.set(100, reply);
      await s.job.loadRecent();
      expect(s.job.store.get().recent).toMatchObject({ phase: "error", items: [] });
      await s.job.loadRecent();
      expect(s.job.store.get().recent.phase).toBe("done");
      expect(s.job.store.get().recent.items[0]!.id).toBe(ids.at(-1));
    }
  });

  it("P2-D: a repeating cursor, a changed snapshot revision, duplicate ids or too many pages are errors (bounded, no retry)", async () => {
    const page = (items: unknown[], next: string | null, revision = 1) => json(200, { schema_version: 1, revision, items, next_cursor: next });
    const s = setup();
    const [a, b] = s.v1.addJobs(2);
    const ja = s.v1.jobs[0]!;
    const jb = s.v1.jobs[1]!;
    const loop = `j2.${"a".repeat(32)}.100.100.${"f".repeat(64)}`;
    // repeating cursor
    s.v1.listPage.set(0, page([ja], loop));
    s.v1.listPage.set(100, page([jb], loop));
    await s.job.loadRecent();
    expect(s.job.store.get().recent).toMatchObject({ phase: "error", items: [] });
    // revision changes between pages
    s.v1.listPage.set(0, page([ja], `j2.${"b".repeat(32)}.100.100.${"f".repeat(64)}`, 1));
    s.v1.listPage.set(100, page([jb], null, 2));
    await s.job.loadRecent();
    expect(s.job.store.get().recent.phase).toBe("error");
    // duplicate id across pages
    s.v1.listPage.set(0, page([ja], `j2.${"c".repeat(32)}.100.100.${"f".repeat(64)}`));
    s.v1.listPage.set(100, page([ja], null));
    await s.job.loadRecent();
    expect(s.job.store.get().recent.phase).toBe("error");
    expect([a, b]).toHaveLength(2);
    // more pages than any B7 snapshot can have (10,000 items / 100): stop and say so
    let n = 0;
    const endless = setup();
    endless.v1.addJobs(1);
    const one = endless.v1.jobs[0]!;
    for (let offset = 0; offset <= 100 * 101; offset += 100) {
      endless.v1.listPage.set(offset, page([{ ...one, id: `job_${(n++).toString(16).padStart(32, "0")}` }], `j2.${"d".repeat(32)}.${offset + 100}.100.${"f".repeat(64)}`));
    }
    await endless.job.loadRecent();
    expect(endless.job.store.get().recent).toMatchObject({ phase: "error", items: [] });
    const gets = endless.lines.filter((l) => l.startsWith("GET /api/v1/jobs")).length;
    expect(gets).toBe(100);
  });

  it("P2-D: a newer reload wins over an older one still in flight; dispose leaves it untouched", async () => {
    const s = setup();
    const ids = s.v1.addJobs(3);
    const slow = deferred<Response>();
    s.v1.listPage.set(0, () => slow.promise);
    const first = s.job.loadRecent();
    await vi.advanceTimersByTimeAsync(0);
    const ids2 = s.v1.addJobs(1);
    await s.job.loadRecent();
    expect(s.job.store.get().recent.items[0]!.id).toBe(ids2[0]);
    slow.resolve(json(200, { schema_version: 1, revision: 1, items: [s.v1.jobs[0]], next_cursor: null }));
    await first;
    expect(s.job.store.get().recent.items.map((j) => j.id)).toEqual([ids2[0], ...ids.slice().reverse()]);
    const slow2 = deferred<Response>();
    s.v1.listPage.set(0, () => slow2.promise);
    const third = s.job.loadRecent();
    await vi.advanceTimersByTimeAsync(0);
    s.job.dispose();
    const before = s.job.store.get();
    slow2.resolve(json(200, { schema_version: 1, revision: 1, items: [], next_cursor: null }));
    await third;
    expect(s.job.store.get()).toBe(before);
  });

  it("following a listed job tracks exactly that id", async () => {
    const s = setup();
    const ids = s.v1.addJobs(130);
    await s.job.loadRecent();
    s.job.follow(ids[125]!);
    await vi.advanceTimersByTimeAsync(0);
    expect(s.job.store.get().job?.id).toBe(ids[125]);
    expect(s.lines).toContain(`GET /api/v1/jobs/${ids[125]}`);
  });
});

// Keep the shared fixture ids referenced (the server answers for these refs).
void [V1_PRESET, V1_POLICY, IN];
