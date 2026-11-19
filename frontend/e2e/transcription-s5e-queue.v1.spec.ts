import type { APIRequestContext, Page, Request, Route } from "@playwright/test";
import { choose, expect, SESSION, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * Bundle D2-A: the Expert queue page in the `--next-ui-v1` shape. It reads the one board
 * (`core.v1.board`: every page of `GET /api/v1/jobs` + `GET /api/v1/queue`) and shows it as a table
 * plus the selected logical job's detail. D2-B adds explicit queue controls;
 * no search/filter/sort ever sends a write.
 *
 * Two kinds of tests: real B7 data (real FastAPI, synthetic B4 evidence and runner,
 * tools/ui2/synthetic_v1.py) and the same page against crafted answers at the HTTP boundary
 * (`page.route`) for shapes the synthetic runner cannot produce on demand (branches, held items,
 * unknown places, broken pagination). Runs after the other v1 specs (workers: 1, file order).
 * Headless Chromium — NOT a WebView2 acceptance (bundle D4, Issue #22).
 */

const ROOT = process.env.UI2_V1_ROOT!;
const input = (name: string) => `${ROOT}/in/${name}`;
const OUT = `${ROOT}/out`;
const AUTH = { Cookie: `utteran_gui_session=${SESSION}` };
const PRESET = "S5-B 合成";
const POLICY = "S5-B CPU";

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const writes = (seen: Request[]) => seen.filter((r) => r.method() !== "GET").map(line);
const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

async function api<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path, { headers: AUTH });
  expect(response.status(), path).toBe(200);
  return (await response.json()) as T;
}

interface QueueWire {
  revision: number;
  active_item_id: string | null;
  items: { id: string; resource_id: string; resource_kind: string; position: number | null; held: boolean }[];
}

// ------------------------------------------------------------------ real-data helpers

async function library(page: Page, request: APIRequestContext): Promise<void> {
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
  const presets = await api<{ items: { name: string }[] }>(request, "/api/v1/presets");
  if (!presets.items.some((p) => p.name === PRESET)) {
    await page.getByTestId("v1-preset-create-open").click();
    await page.getByTestId("v1-preset-name").locator("input").fill(PRESET);
    await page.getByTestId("v1-preset-model").locator("input").fill("large-v3-turbo");
    await page.getByTestId("v1-preset-check").click();
    await page.getByTestId("v1-preset-save").click();
  } else {
    await choose(page, "v1-preset-select", new RegExp(PRESET));
  }
  const policies = await api<{ items: { name: string }[] }>(request, "/api/v1/execution-policies");
  if (!policies.items.some((p) => p.name === POLICY)) {
    await page.getByTestId("v1-policy-create-open").click();
    await page.getByTestId("v1-policy-name").locator("input").fill(POLICY);
    await page.getByTestId("v1-policy-check").click();
    await page.getByTestId("v1-policy-save").click();
  } else {
    await choose(page, "v1-policy-select", new RegExp(POLICY));
  }
  await expect(page.getByTestId("v1-preset-frozen")).toContainText(PRESET);
  await expect(page.getByTestId("v1-policy-frozen")).toContainText(POLICY);
}

async function expert(page: Page, request: APIRequestContext, files: string[]): Promise<void> {
  await page.getByTestId("mode-expert").click();
  await library(page, request);
  await page.getByTestId("v1-output-path").locator("input").fill(OUT);
  for (const name of files) {
    await page.getByTestId("tx-input-path").locator("input").fill(input(name));
    await page.getByTestId("tx-input-add-typed").click();
  }
  await page.getByTestId("tx-list-select-all").click();
  await expect(page.getByTestId("v1-batch-count")).toContainText(String(files.length));
}

async function submitBatch(page: Page, seen: Request[]): Promise<string[]> {
  await page.getByTestId("v1-batch-check").click();
  await expect(page.getByTestId("v1-batch-resolved")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("v1-batch-submit").click();
  await expect(page.getByTestId("v1-batch-accepted")).toBeVisible({ timeout: 20_000 });
  const post = seen.filter((r) => line(r) === "POST /api/v1/jobs").at(-1)!;
  const body = (await (await post.response())!.json()) as { jobs: { id: string }[] };
  return body.jobs.map((j) => j.id);
}

async function ensureQueueRunning(page: Page): Promise<void> {
  const dispatch = page.getByTestId("v1-queue-dispatch");
  await expect(dispatch).toBeVisible();
  if (/停止中/.test((await dispatch.textContent()) ?? "")) {
    await page.getByTestId("v1-queue-resume").click();
    await expect(dispatch).toContainText(/実行中/);
  }
}

/** Test harness only (the `request` context, not the page): cancel whatever is still queued. */
async function drain(request: APIRequestContext, tag: string): Promise<void> {
  for (let round = 0; round < 5; round += 1) {
    const queue = await api<QueueWire>(request, "/api/v1/queue");
    const waiting = queue.items.filter((item) => item.resource_kind === "job");
    if (waiting.length === 0) break;
    for (const item of waiting) {
      const job = await api<{ revision: number }>(request, `/api/v1/jobs/${item.resource_id}`);
      const response = await request.post(`/api/v1/jobs/${item.resource_id}/cancel`, {
        headers: { ...SESSION_HEADER, "If-Match": `"${job.revision}"`, "Idempotency-Key": `d2a-${tag}-${item.resource_id.slice(4, 20)}-${round}` },
        data: { schema_version: 1, mode: "cooperative" },
      });
      expect([200, 202, 412]).toContain(response.status());
    }
  }
  await expect.poll(async () => (await api<QueueWire>(request, "/api/v1/queue")).items.length, { timeout: 30_000 }).toBe(0);
}

const page$ = (page: Page) => page.getByTestId("section-queue");
const rows = (page: Page) => page.getByTestId("queue-row");
const rowOf = (page: Page, root: string) => page.locator(`[data-testid="queue-row"][data-root="${root}"]`);
const rowRoots = (page: Page) => rows(page).evaluateAll((els) => els.map((e) => e.getAttribute("data-root")));

async function openQueue(page: Page): Promise<void> {
  if ((await page.getByTestId("sidebar").count()) === 0) await page.getByTestId("mode-expert").click();
  await page.getByTestId("nav-queue").click();
  await expect(page.getByTestId("content")).toHaveAttribute("data-section", "queue");
}

// ------------------------------------------------------------------ crafted answers (HTTP boundary)

const hex = (n: number) => n.toString(16).padStart(32, "0");
const jid = (n: number) => `job_${hex(n)}`;
const qid = (n: number) => `qi_${hex(n)}`;
const SPEC = {
  candidate_id: "fw-cpu.cpu.generation-1",
  recipe_id: "fw-cpu",
  profile_id: "cpu",
  profile_generation: "generation-1",
  model_id: "large-v3-turbo",
  model_revision: "revision-1",
  backend: "faster-whisper",
  requested_route: { encoder: "selected-device", decoder: "selected-device" },
  device: "cpu",
  compute_type: "int8",
  inventory_revision: 3,
  policy_ref: { id: "pol_" + "2".repeat(32), revision: 1 },
  reason_codes: ["supported"],
  confidence: "medium",
  evidence_kinds: ["smoke"],
  warnings: [],
  fallback_candidate_ids: [],
  worker_support: "supported",
};
type Wire = Record<string, unknown>;
function jobWire(n: number, status: string, extra: Wire = {}, model = "large-v3-turbo"): Wire {
  return {
    schema_version: 1,
    id: jid(n),
    revision: 1,
    status,
    held: false,
    blockers: [],
    block_reason: null,
    recovery_required: null,
    reason_code: "submitted",
    snapshot: {
      created_at: "2026-11-19T00:00:00Z",
      client_file_id: `f-crafted-${n}`,
      preset_ref: { id: "pre_" + "1".repeat(32), revision: 1 },
      policy_ref: { id: "pol_" + "2".repeat(32), revision: 1 },
      resume_mode: "resume",
      input_identity: `in_${"a".repeat(32)}`,
      spec: { ...SPEC, model_id: model },
    },
    attempts: [],
    retry_of: null,
    rerun_of: null,
    result: null,
    error: null,
    created_at: `2026-11-19T00:00:0${n % 10}Z`,
    updated_at: `2026-11-19T00:01:0${n % 10}Z`,
    ...extra,
  };
}
interface Crafted {
  /** Pages of `GET /api/v1/jobs` keyed by cursor ("" = first page). */
  pages: Map<string, { status?: number; body?: Wire }>;
  queue: { status?: number; body?: Wire };
}
function onePage(jobs: Wire[], revision = 5): Crafted["pages"] {
  return new Map([["", { body: { schema_version: 1, revision, items: jobs, next_cursor: null } }]]);
}
function queueOf(revision: number, active: number | null, waiting: { n: number; position: number | null; held?: boolean }[]): Crafted["queue"] {
  const items = [
    ...(active === null ? [] : [{ id: qid(active), resource_id: jid(active), resource_kind: "job", position: null, held: false, blockers: [] }]),
    ...waiting.map((w) => ({ id: qid(w.n), resource_id: jid(w.n), resource_kind: "job", position: w.position, held: w.held ?? false, blockers: [] })),
  ];
  return { body: { schema_version: 1, revision, dispatch: "running", active_item_id: active === null ? null : qid(active), items } };
}

/** Serve crafted answers for the two board reads; every other request reaches the real server. */
async function craft(page: Page, state: Crafted): Promise<void> {
  await page.route(/\/api\/v1\/(jobs|queue)(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET") return route.continue();
    if (url.pathname === "/api/v1/queue") {
      const q = state.queue;
      const revision = (q.body?.revision as number | undefined) ?? 1;
      return route.fulfill({ status: q.status ?? 200, contentType: "application/json", headers: { ETag: `"${revision}"` }, body: JSON.stringify(q.body ?? {}) });
    }
    const page = state.pages.get(url.searchParams.get("cursor") ?? "");
    if (!page) return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    return route.fulfill({ status: page.status ?? 200, contentType: "application/json", body: JSON.stringify(page.body ?? {}) });
  });
}

/** Branched chain (1 → 2, 1 → 3), a held item, an item B7 gives no place, an ordinary waiting job. */
function branchedScenario(): Crafted {
  const jobs = [
    jobWire(1, "failed", { error: { code: "worker_failed" } }),
    jobWire(2, "queued", { retry_of: jid(1) }),
    jobWire(3, "running", { retry_of: jid(1), attempts: [{ state: "active" }] }),
    jobWire(4, "held", { held: true }),
    jobWire(5, "waiting_user", { block_reason: "some_future_reason" }),
    jobWire(6, "queued"),
  ];
  return { pages: onePage(jobs), queue: queueOf(9, 3, [{ n: 4, position: 1, held: true }, { n: 6, position: 2 }, { n: 2, position: 3 }]) };
}

// ------------------------------------------------------------------ tests

test("setup: cancel the jobs earlier specs left waiting", async ({ request }) => {
  await drain(request, "setup");
});

test("D2A-01/03/05/06: real B7 data as a table + detail; B7 order, places and names; search/filter/sort only read", async ({ app, request }) => {
  const seen = recordApi(app);
  await expert(app, request, ["slow-meeting.wav", "meeting.wav", "other.wav"]);
  const ids = await submitBatch(app, seen);
  expect(ids).toHaveLength(3);
  await ensureQueueRunning(app);
  await expect.poll(async () => (await api<QueueWire>(request, "/api/v1/queue")).active_item_id, { timeout: 20_000 }).not.toBeNull();
  const before = await api<QueueWire>(request, "/api/v1/queue");
  const placeOf = (id: string) => before.items.find((item) => item.resource_id === id)!;
  expect(placeOf(ids[0]!).id).toBe(before.active_item_id);

  await openQueue(app);
  const mark = writes(seen).length;
  await expect(page$(app)).toHaveAttribute("data-phase", "done");
  await expect(app.getByTestId("not-migrated")).toHaveCount(0);
  await expect(app.getByTestId("nav-queue")).toContainText("一部移行");
  // B7 order: the active job, then waiting by B7's position.
  await expect.poll(() => rowRoots(app)).toEqual(ids);
  await expect(rowOf(app, ids[0]!)).toHaveAttribute("data-place", "active");
  for (const id of ids.slice(1)) {
    await expect(rowOf(app, id)).toHaveAttribute("data-place", "waiting");
    await expect(rowOf(app, id)).toHaveAttribute("data-position", String(placeOf(id).position));
    await expect(rowOf(app, id).getByTestId("queue-row-place")).toContainText(`${placeOf(id).position}`);
  }
  await expect(rowOf(app, ids[0]!).getByTestId("queue-row-place")).toContainText("実行中");
  // Names are this page's basenames; the stage is not provided by the API.
  await expect(rowOf(app, ids[0]!).getByTestId("queue-row-name")).toHaveText("slow-meeting.wav");
  await expect(rowOf(app, ids[1]!).getByTestId("queue-row-name")).toHaveText("meeting.wav");
  for (const id of ids) await expect(rowOf(app, id).getByTestId("queue-row-stage")).toHaveText("未提供");
  await expect(app.getByTestId("queue-stage-note")).toBeVisible();
  await expect(app.getByTestId("queue-ops-note")).toBeVisible();
  await expect(app.getByTestId("queue-detail-none")).toBeVisible();
  // History operations stay in ③; passive navigation sends no write.
  for (const testid of ["v1-board-cancel", "v1-board-retry", "v1-board-hide", "v1-queue-pause", "v1-queue-resume"]) await expect(page$(app).getByTestId(testid)).toHaveCount(0);
  await shot(app, "d2a-01-real-queue");

  // Select by click → detail of the same logical job.
  await rowOf(app, ids[1]!).click();
  await expect(rowOf(app, ids[1]!)).toHaveAttribute("aria-selected", "true");
  await expect(app.locator('[data-testid="queue-row"][aria-selected="true"]')).toHaveCount(1);
  const detail = app.getByTestId("queue-detail");
  await expect(detail).toHaveAttribute("data-root", ids[1]!);
  await expect(detail.getByTestId("queue-detail-current")).toContainText(ids[1]!);
  await expect(detail).toContainText("large-v3-turbo");
  await expect(detail).toContainText("cpu");
  await expect(detail.getByTestId("queue-detail-place")).toContainText(`${placeOf(ids[1]!).position}`);
  // Keyboard: arrow to the next row, Enter selects it.
  await rowOf(app, ids[1]!).focus();
  await app.keyboard.press("ArrowDown");
  await expect(rowOf(app, ids[2]!)).toBeFocused();
  await app.keyboard.press("Enter");
  await expect(detail).toHaveAttribute("data-root", ids[2]!);
  await expect(rowOf(app, ids[2]!)).toHaveAttribute("aria-selected", "true");
  await expect(rowOf(app, ids[1]!)).toHaveAttribute("aria-selected", "false");
  await shot(app, "d2a-02-real-selected");

  // Search: name, internal job id; a path fragment finds nothing (no path is known to the page).
  const search = app.getByTestId("queue-search").locator("input");
  await search.fill("other");
  await expect.poll(() => rowRoots(app)).toEqual([ids[2]]);
  await search.fill(ids[0]!.slice(-10));
  await expect.poll(() => rowRoots(app)).toEqual([ids[0]]);
  await search.fill("/in/");
  await expect(rows(app)).toHaveCount(0);
  await expect(app.getByTestId("queue-no-match")).toBeVisible();
  await expect(app.getByTestId("queue-empty")).toHaveCount(0);
  await expect(app.getByTestId("queue-no-match")).toContainText("3");
  await search.press("Enter"); // Enter in the search box does nothing but search
  await search.fill("");
  await expect.poll(() => rowRoots(app)).toEqual(ids);

  // Filter by status.
  await choose(app, "queue-filter", "実行中");
  await expect.poll(() => rowRoots(app)).toEqual([ids[0]]);
  await choose(app, "queue-filter", "待機中");
  await expect.poll(() => rowRoots(app)).toEqual(ids.slice(1));
  await choose(app, "queue-filter", "すべて");

  // Display sort: names; B7's order and the queue revision are untouched.
  await expect(app.getByTestId("queue-order-note")).toBeVisible();
  await choose(app, "queue-sort", "名前");
  await expect.poll(() => rowRoots(app)).toEqual([ids[1], ids[2], ids[0]]);
  await expect(app.getByTestId("queue-sort-note")).toBeVisible();
  // The selection follows the row, not the screen position.
  await expect(detail).toHaveAttribute("data-root", ids[2]!);
  await expect(rowOf(app, ids[2]!)).toHaveAttribute("aria-selected", "true");
  const after = await api<QueueWire>(request, "/api/v1/queue");
  expect(after.revision).toBe(before.revision);
  expect(after.items.map((i) => i.resource_id)).toEqual(before.items.map((i) => i.resource_id));
  await choose(app, "queue-sort", "キューの順");
  await expect.poll(() => rowRoots(app)).toEqual(ids);

  // Privacy: no path anywhere on the page.
  const text = (await page$(app).textContent()) ?? "";
  expect(text).not.toContain(ROOT);
  expect(text).not.toContain("/in/");
  expect(await page$(app).innerHTML()).not.toContain(ROOT);
  // No write and nothing on the legacy job routes while the page was used.
  expect(writes(seen).slice(mark)).toEqual([]);
  expect(seen.map(line).filter((l) => / \/api\/(jobs|queue)(\/|$)/.test(l))).toEqual([]);

  // The selected row finishes and leaves the queue: it is deselected explicitly.
  await drain(request, "real");
  await expect(app.getByTestId("queue-detail-lost")).toBeVisible({ timeout: 30_000 });
  await expect(app.getByTestId("queue-detail")).toHaveCount(0);
  await expect(app.locator('[data-testid="queue-row"][aria-selected="true"]')).toHaveCount(0);
  await expect(app.getByTestId("queue-empty")).toBeVisible({ timeout: 30_000 });
  await expect(app.getByTestId("queue-no-match")).toHaveCount(0);
  expect(writes(seen).slice(mark)).toEqual([]);
});

test("D2A-08: one board poller for both views (Expert ③ and the queue page); leaving both stops it", async ({ app, request }) => {
  const seen = recordApi(app);
  const queueReads = () => seen.filter((r) => line(r) === "GET /api/v1/queue").length;
  await expert(app, request, ["slow-meeting.wav"]);
  const [slow] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  const writeMark = writes(seen).length; // explicit setup resume is not a navigation write
  await openQueue(app);
  await expect(rowOf(app, slow!)).toBeVisible();
  await app.waitForTimeout(1000);
  let mark = queueReads();
  await app.waitForTimeout(4000);
  const baseline = queueReads() - mark;
  expect(baseline).toBeGreaterThan(0); // something is unfinished: the board polls
  // Ten unmount/mount cycles between Expert ③ (V1JobBoard), the queue page and Normal.
  for (let i = 0; i < 5; i += 1) {
    await app.getByTestId("nav-transcribe").click();
    await expect(app.getByTestId("v1-board")).toBeVisible();
    await app.getByTestId("nav-queue").click();
    await expect(page$(app)).toBeVisible();
    await app.getByTestId("mode-normal").click();
    await app.getByTestId("mode-expert").click();
    await expect(page$(app)).toBeVisible();
  }
  await app.waitForTimeout(1000);
  mark = queueReads();
  await app.waitForTimeout(4000);
  // One watcher polls at most every 2 s (BOARD_POLL_MS): leaked watchers would multiply this.
  expect(queueReads() - mark).toBeLessThanOrEqual(Math.max(baseline, 2) + 1);
  // Leaving both views stops the polling.
  await app.getByTestId("mode-normal").click();
  await app.getByTestId("nav-settings").click();
  await app.waitForTimeout(1000);
  mark = queueReads();
  await app.waitForTimeout(4000);
  expect(queueReads() - mark).toBe(0);
  expect(writes(seen).slice(writeMark).filter((l) => l.includes("/cancel") || l.startsWith("DELETE ") || l.startsWith("PUT ") || l.startsWith("PATCH /api/v1/queue"))).toEqual([]);
  await drain(request, "poll");
});

test("D2A-08: an unknown board write stays unknown on the queue page and is never replayed by it", async ({ app, request }) => {
  const seen = recordApi(app);
  await expert(app, request, ["slow-meeting.wav"]);
  const [slow] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  // A cancel from the queue whose answer is lost (the server applied it) stays unknown.
  let first = true;
  await app.route("**/api/v1/jobs/*/cancel", async (route) => {
    if (!first) return route.continue();
    first = false;
    await route.fetch();
    await route.fulfill({ status: 500, body: "" });
  });
  const cancels = () => seen.filter((r) => r.method() === "POST" && new URL(r.url()).pathname.endsWith("/cancel"));
  await openQueue(app);
  await rowOf(app, slow!).click();
  await app.getByTestId("queue-cancel").click();
  await expect(app.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "unknown");
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "unknown");
  expect(cancels()).toHaveLength(1);
  // The queue page says so and points back; it neither resends nor hides the open state.
  await app.getByTestId("nav-queue").click();
  await expect(app.getByTestId("queue-action-open")).toHaveAttribute("data-action-phase", "unknown");
  await expect(page$(app).getByTestId("v1-board-recheck")).toHaveCount(0);
  await app.waitForTimeout(3000);
  expect(cancels()).toHaveLength(1);
  await app.getByTestId("queue-action-open").getByTestId("queue-open-transcribe").click();
  await expect(app.getByTestId("content")).toHaveAttribute("data-section", "transcribe");
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "unknown");
  // Only the explicit re-check resends, with the same key.
  await app.getByTestId("v1-board-recheck").click();
  await expect(app.getByTestId("v1-board-action")).not.toHaveAttribute("data-action-phase", "unknown");
  expect(cancels()).toHaveLength(2);
  const [a, b] = await Promise.all(cancels().map((r) => r.allHeaders()));
  expect(b!["idempotency-key"]).toBe(a!["idempotency-key"]);
  await drain(request, "unknown");
});

test("D2A-02/03/06: crafted B7 answers: one row per retry chain with every open branch, held, unknown place last, no invented name", async ({ app }) => {
  const seen = recordApi(app);
  await craft(app, branchedScenario());
  await openQueue(app);
  await expect(page$(app)).toHaveAttribute("data-phase", "done");
  // 1 → (2, 3) is one logical row; its current job is the active branch 3, not the newest 3/2 by list.
  await expect.poll(() => rowRoots(app)).toEqual([jid(1), jid(4), jid(6), jid(5)]);
  const chain = rowOf(app, jid(1));
  await expect(chain).toHaveAttribute("data-job", jid(3));
  await expect(chain).toHaveAttribute("data-place", "active");
  await expect(chain).toHaveAttribute("data-attempts", "3");
  await expect(rowOf(app, jid(4))).toHaveAttribute("data-held", "true");
  await expect(rowOf(app, jid(4)).getByTestId("queue-row-place")).toContainText("1");
  await expect(rowOf(app, jid(4)).getByTestId("queue-row-place")).toContainText("保留");
  await expect(rowOf(app, jid(6))).toHaveAttribute("data-position", "2");
  // No place from B7: last, no number, never 0.
  const unknown = rowOf(app, jid(5));
  await expect(unknown).toHaveAttribute("data-place", "unknown");
  await expect(unknown).not.toHaveAttribute("data-position", /.+/);
  await expect(unknown.getByTestId("queue-row-place")).toHaveText("順位不明");
  // No local name for these jobs: "unknown", identified by the internal id instead.
  for (const n of [1, 4, 5, 6]) await expect(rowOf(app, jid(n)).getByTestId("queue-row-name")).toHaveText("ファイル名不明");
  await expect(unknown).toContainText(jid(5).slice(0, 12));
  // Detail of the branched row: every unfinished branch with its id, status and B7 place.
  await chain.click();
  const detail = app.getByTestId("queue-detail");
  await expect(detail).toHaveAttribute("data-root", jid(1));
  await expect(detail.getByTestId("queue-detail-branches")).toBeVisible();
  const branches = detail.getByTestId("queue-detail-branch");
  await expect(branches).toHaveCount(2);
  await expect(detail.locator(`[data-testid="queue-detail-branch"][data-job="${jid(2)}"]`)).toContainText("3");
  await expect(detail.locator(`[data-testid="queue-detail-branch"][data-job="${jid(3)}"]`)).toContainText("実行中");
  await expect(detail.getByTestId("queue-detail-attempt")).toHaveCount(3);
  await expect(detail.locator(`[data-testid="queue-detail-attempt"][data-job="${jid(1)}"]`)).toHaveAttribute("data-status", "failed");
  // An unknown reason code is shown safely (the code stays visible).
  await unknown.click();
  await expect(detail).toContainText("some_future_reason");
  // No progress, no stage chips: the API does not publish them.
  // (Vuetify cards and fields always render an *inactive* loader; only an active one would be a progress display.)
  await expect(page$(app).locator(".v-progress-linear--active, .v-progress-circular")).toHaveCount(0);
  await expect(page$(app)).not.toContainText("%");
  expect(writes(seen)).toEqual([]);
  await shot(app, "d2a-03-crafted-branches");
});

test("D2A-05 (IME): composing text is not applied; the confirming Enter is not prevented; the confirmed text is applied once", async ({ app }) => {
  const seen = recordApi(app);
  await craft(app, branchedScenario());
  await openQueue(app);
  await expect(rows(app)).toHaveCount(4);
  const search = app.getByTestId("queue-search").locator("input");
  // Count how often the shown rows change (each application of a query re-renders the count).
  await page$(app).evaluate((el) => {
    const w = window as unknown as { __shownChanges: string[] };
    w.__shownChanges = [];
    new MutationObserver(() => w.__shownChanges.push(el.getAttribute("data-shown") ?? "")).observe(el, { attributes: true, attributeFilter: ["data-shown"] });
  });
  // compositionstart → composing input (full-width, not final) → Enter that confirms the candidate → compositionend.
  const result = await search.evaluate((input: HTMLInputElement) => {
    const setValue = (value: string) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new InputEvent("input", { bubbles: true, isComposing: true, data: value }));
    };
    input.focus();
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true, data: "" }));
    setValue("０");
    setValue("０００４");
    const enter = new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true, isComposing: true });
    input.dispatchEvent(enter);
    // keyCode 229 (Process) is what Windows IMEs report for keys the IME consumes.
    const process = new KeyboardEvent("keydown", { key: "Process", keyCode: 229, bubbles: true, cancelable: true, isComposing: true } as KeyboardEventInit);
    input.dispatchEvent(process);
    return { enterPrevented: enter.defaultPrevented, processPrevented: process.defaultPrevented };
  });
  expect(result).toEqual({ enterPrevented: false, processPrevented: false });
  // While composing, nothing was applied.
  await app.waitForTimeout(300);
  await expect(rows(app)).toHaveCount(4);
  expect(await app.evaluate(() => (window as unknown as { __shownChanges: string[] }).__shownChanges)).toEqual([]);
  // compositionend: the confirmed text is applied once (NFKC: "０００４" → "0004" = job 4 only).
  await search.evaluate((input: HTMLInputElement) => input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "０００４" })));
  await expect.poll(() => rowRoots(app)).toEqual([jid(4)]);
  await app.waitForTimeout(300);
  expect(await app.evaluate(() => (window as unknown as { __shownChanges: string[] }).__shownChanges)).toEqual(["1"]);
  // An ordinary Enter after that is not prevented either, and changes nothing.
  const plainPrevented = await search.evaluate((input: HTMLInputElement) => {
    const enter = new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true });
    input.dispatchEvent(enter);
    return enter.defaultPrevented;
  });
  expect(plainPrevented).toBe(false);
  await search.press("Enter");
  await expect.poll(() => rowRoots(app)).toEqual([jid(4)]);
  expect(writes(seen)).toEqual([]);
});

test("D2A-04: a selected row keeps its root across polls and shows the newest values; the stale object is never kept", async ({ app }) => {
  const state = branchedScenario();
  await craft(app, state);
  await openQueue(app);
  await rowOf(app, jid(6)).click();
  const detail = app.getByTestId("queue-detail");
  await expect(detail).toHaveAttribute("data-status", "queued");
  // The server moves job 6 to the front and starts it (new revision); the next poll shows it.
  const jobs = [jobWire(1, "failed"), jobWire(2, "queued", { retry_of: jid(1) }), jobWire(3, "running", { retry_of: jid(1) }), jobWire(4, "held", { held: true }), jobWire(5, "waiting_user"), jobWire(6, "running", { revision: 2 })];
  state.pages = onePage(jobs, 6);
  state.queue = queueOf(10, 6, [{ n: 4, position: 1, held: true }, { n: 3, position: 2 }, { n: 2, position: 3 }]);
  await expect(detail).toHaveAttribute("data-status", "running", { timeout: 10_000 });
  await expect(detail).toHaveAttribute("data-root", jid(6));
  await expect(rowOf(app, jid(6))).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => rowRoots(app)).toEqual([jid(6), jid(4), jid(1), jid(5)]);
  // Job 6 finishes: the row leaves the queue, the selection is cleared and says why.
  state.pages = onePage([...jobs.slice(0, 5), jobWire(6, "succeeded", { revision: 3 })], 7);
  state.queue = queueOf(11, null, [{ n: 4, position: 1, held: true }, { n: 3, position: 2 }, { n: 2, position: 3 }]);
  await expect(app.getByTestId("queue-detail-lost")).toBeVisible({ timeout: 10_000 });
  await expect(detail).toHaveCount(0);
  await expect(rowOf(app, jid(6))).toHaveCount(0);
});

test("D2A-07: multi-page lists are read in full; broken pagination, failed pages and a failed queue are errors, never a partial list", async ({ app }) => {
  const all = [1, 2, 3, 4, 5].map((n) => jobWire(n, "queued"));
  const good = (): Crafted => ({
    pages: new Map([
      ["", { body: { schema_version: 1, revision: 5, items: all.slice(0, 3), next_cursor: "c1" } }],
      ["c1", { body: { schema_version: 1, revision: 5, items: all.slice(3), next_cursor: null } }],
    ]),
    queue: queueOf(3, null, [1, 2, 3, 4, 5].map((n) => ({ n, position: n }))),
  });
  const state = good();
  await craft(app, state);
  const seen = recordApi(app);
  await openQueue(app);
  await expect.poll(() => rowRoots(app)).toEqual([1, 2, 3, 4, 5].map(jid));
  expect(seen.filter((r) => line(r) === "GET /api/v1/jobs").length).toBeGreaterThanOrEqual(2);
  await rowOf(app, jid(4)).click();

  const broken: [string, (c: Crafted) => void][] = [
    ["cursor cycle", (c) => (c.pages.get("c1")!.body!.next_cursor = "c1")],
    ["revision changed between pages", (c) => (c.pages.get("c1")!.body!.revision = 6)],
    ["duplicate id across pages", (c) => (c.pages.get("c1")!.body!.items = [all[0], all[4]])],
    ["second page fails", (c) => (c.pages.get("c1")!.status = 500)],
    ["queue fails", (c) => (c.queue = { status: 503, body: {} })],
    ["malformed job", (c) => (c.pages.get("c1")!.body!.items = [{ ...all[3], status: "teleported" }, all[4]])],
  ];
  for (const [name, breakIt] of broken) {
    const next = good();
    breakIt(next);
    state.pages = next.pages;
    state.queue = next.queue;
    await app.getByTestId("queue-reload").click();
    await expect(page$(app), name).toHaveAttribute("data-phase", "error");
    await expect(app.getByTestId("queue-error"), name).toBeVisible();
    // Nothing of the old or the partial answer is shown as "the queue".
    await expect(rows(app), name).toHaveCount(0);
    await expect(app.getByTestId("queue-detail"), name).toHaveCount(0);
    await expect(app.getByTestId("queue-empty"), name).toHaveCount(0);
    await expect(app.getByTestId("queue-no-match"), name).toHaveCount(0);
    // Polling stopped; the explicit reload brings it back.
    const reads = seen.length;
    await app.waitForTimeout(2600);
    expect(seen.length, name).toBe(reads);
    const fixed = good();
    state.pages = fixed.pages;
    state.queue = fixed.queue;
    await app.getByTestId("queue-reload").click();
    await expect(page$(app), name).toHaveAttribute("data-phase", "done");
    await expect.poll(() => rowRoots(app)).toEqual([1, 2, 3, 4, 5].map(jid));
    // The selection survives a failed read of a row that is still there.
    await expect(app.getByTestId("queue-detail"), name).toHaveAttribute("data-root", jid(4));
  }
  expect(writes(seen)).toEqual([]);
  await shot(app, "d2a-07-recovered");
});

test("D2A-07: network loss and the empty queue are told apart", async ({ app }) => {
  const state: Crafted = { pages: onePage([]), queue: queueOf(1, null, []) };
  await craft(app, state);
  await openQueue(app);
  await expect(app.getByTestId("queue-empty")).toBeVisible();
  await expect(app.getByTestId("queue-table")).toHaveCount(0);
  await app.unroute(/\/api\/v1\/(jobs|queue)(\?.*)?$/);
  await app.route(/\/api\/v1\/(jobs|queue)(\?.*)?$/, (route) => route.abort("internetdisconnected"));
  await app.getByTestId("queue-reload").click();
  await expect(page$(app)).toHaveAttribute("data-phase", "error");
  await expect(app.getByTestId("queue-error")).toHaveAttribute("data-error-kind", "network");
  await expect(app.getByTestId("queue-empty")).toHaveCount(0);
  await shot(app, "d2a-07-network");
});

for (const width of [900, 1100, 1280]) {
  for (const language of ["ja", "en"] as const) {
    for (const theme of ["light", "dark"] as const) {
      test(`D2A-09: ${width}px ${language} ${theme}: table, detail, selection and search fit without horizontal overflow`, async ({ app, request }) => {
        expect((await request.patch("/api/settings", { headers: SESSION_HEADER, data: { language, theme } })).status()).toBe(200);
        await app.setViewportSize({ width, height: width === 900 ? 660 : 820 });
        await app.reload();
        await expect(app.getByTestId("app-ready")).toBeVisible();
        const longModel = "synthetic-very-long-model-identifier-" + "x".repeat(90);
        const scenario = branchedScenario();
        const first = scenario.pages.get("")!.body!;
        first.items = [...(first.items as Wire[]), jobWire(7, "blocked", { block_reason: "a_very_long_future_block_reason_code_for_layout" }, longModel)];
        scenario.queue = queueOf(9, 3, [{ n: 4, position: 1, held: true }, { n: 6, position: 2 }, { n: 2, position: 3 }, { n: 7, position: 4 }]);
        await craft(app, scenario);
        await openQueue(app);
        await expect(rows(app)).toHaveCount(5);
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await rowOf(app, jid(7)).click();
        await expect(app.getByTestId("queue-detail")).toHaveAttribute("data-root", jid(7));
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        // Table and detail never overlap (side by side when wide, stacked when narrow).
        const table = (await app.getByTestId("queue-table").boundingBox())!;
        const box = (await app.getByTestId("queue-detail").boundingBox())!;
        const apart = box.x >= table.x + table.width - 1 || box.y >= table.y + table.height - 1;
        expect(apart).toBe(true);
        // Every cell stays inside the table (nothing pushed out by long ids or model names).
        const cellsInside = await app.getByTestId("queue-table").evaluate((el) => {
          const outer = el.getBoundingClientRect();
          return [...el.querySelectorAll("td, th")].every((c) => {
            const r = c.getBoundingClientRect();
            return r.left >= outer.left - 1 && r.right <= outer.right + 1;
          });
        });
        expect(cellsInside).toBe(true);
        await shot(app, `d2a-09-${width}-${language}-${theme}-selected`);
        await app.getByTestId("queue-search").locator("input").fill("no-such-job");
        await expect(app.getByTestId("queue-no-match")).toBeVisible();
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await shot(app, `d2a-09-${width}-${language}-${theme}-nomatch`);
        const queueState: Crafted["queue"] = { status: 503, body: {} };
        scenario.queue = queueState;
        await app.getByTestId("queue-reload").click();
        await expect(app.getByTestId("queue-error")).toBeVisible();
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await shot(app, `d2a-09-${width}-${language}-${theme}-error`);
      });
    }
  }
}

// D2-B acceptance uses real FastAPI/B7 and crafted failures at the same UI boundary.
async function waitingBatch(page: Page, request: APIRequestContext, seen: Request[]): Promise<string[]> {
  await drain(request, "d2b-arrange");
  const q = await api<QueueWire>(request, "/api/v1/queue");
  expect((await request.patch("/api/v1/queue/control", { headers: { ...SESSION_HEADER, "If-Match": `"${q.revision}"`, "Idempotency-Key": `d2b-arrange-${q.revision}-paused` }, data: { schema_version: 1, dispatch: "paused" } })).status()).toBe(200);
  await expert(page, request, ["slow-meeting.wav", "meeting.wav", "other.wav"]);
  const ids = await submitBatch(page, seen); await openQueue(page); await expect(rows(page)).toHaveCount(3); return ids;
}
async function settled(page: Page): Promise<void> {
  await expect(page.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "done");
  await expect(page.getByTestId("queue-dispatch")).toBeEnabled();
}
for (const operation of ["dispatch", "hold", "order", "cancel"] as const) test(`D2B real B7: ${operation} with revision and payload verification`, async ({ app, request }) => {
  const seen = recordApi(app); const ids = await waitingBatch(app, request, seen);
  const before = await api<QueueWire>(request, "/api/v1/queue");
  if (operation === "dispatch") {
    await app.getByTestId("queue-dispatch").click(); await settled(app);
    await expect.poll(async () => (await api<QueueWire>(request, "/api/v1/queue")).active_item_id).not.toBeNull();
    const running = await api<QueueWire>(request, "/api/v1/queue");
    await app.getByTestId("queue-reload").click();
    await expect(app.getByTestId("queue-dispatch")).toContainText("一時停止");
    await app.getByTestId("queue-dispatch").click(); await settled(app);
    const paused = await api<QueueWire & { dispatch: string }>(request, "/api/v1/queue");
    expect(paused.dispatch).toBe("paused"); expect(paused.active_item_id).toBe(running.active_item_id);
    expect(paused.revision).toBeGreaterThan(running.revision);
    expect(await api(request, `/api/v1/jobs/${ids[0]}`)).toMatchObject({ status: "running" });
    await expect(app.getByTestId("queue-pause-semantics")).toContainText("止まりません");
  } else {
    await rowOf(app, ids[1]!).click();
    if (operation === "hold" || operation === "order") {
      await app.getByTestId("queue-hold").click(); await settled(app);
      const held = await api<QueueWire>(request, "/api/v1/queue");
      const item = held.items.find(i => i.resource_id === ids[1])!; expect(item.held).toBe(true);
      const patch = seen.find(r => r.method() === "PATCH" && r.url().endsWith(`/queue/items/${item.id}`))!;
      expect(patch.postDataJSON()).toEqual({ schema_version: 1, held: true });
      expect((await patch.allHeaders())["if-match"]).toBe(`"${before.revision}"`);
      if (operation === "hold") {
        await expect(app.getByTestId("queue-hold")).toContainText("解除");
        await app.getByTestId("queue-hold").click(); await settled(app);
        expect((await api<QueueWire>(request, "/api/v1/queue")).items.find(i => i.id === item.id)!.held).toBe(false);
      } else {
        const waiting = held.items.filter(i => i.id !== held.active_item_id).sort((a,b) => a.position! - b.position!);
        const expected = [waiting[1]!.id, waiting[0]!.id, ...waiting.slice(2).map(i => i.id)];
        await app.getByTestId("queue-up").focus(); await app.keyboard.press("Space"); await settled(app);
        const put = seen.filter(r => r.method() === "PUT" && r.url().endsWith("/queue/order")).at(-1)!;
        expect(put.postDataJSON()).toEqual({ schema_version: 1, waiting_item_ids: expected });
        const after = await api<QueueWire>(request, "/api/v1/queue");
        expect(after.items.filter(i => i.id !== after.active_item_id).sort((a,b) => a.position! - b.position!).map(i => i.id)).toEqual(expected);
        const mark = writes(seen).length;
        await app.getByTestId("queue-search").locator("input").fill(ids[1]!); await expect(app.getByTestId("queue-down")).toBeDisabled();
        expect(writes(seen).length).toBe(mark); expect((await api<QueueWire>(request, "/api/v1/queue")).revision).toBe(after.revision);
      }
    } else {
      await app.getByTestId("queue-cancel").focus(); await app.keyboard.press("Enter"); await settled(app);
      const post = seen.find(r => r.method() === "POST" && r.url().endsWith(`/jobs/${ids[1]}/cancel`))!;
      expect(post.postDataJSON()).toEqual({ schema_version: 1, mode: "cooperative" });
      expect(await api(request, `/api/v1/jobs/${ids[1]}`)).toMatchObject({ status: "cancelled" });
      expect((await api<QueueWire>(request, "/api/v1/queue")).items.some(i => i.resource_id === ids[1])).toBe(false);
    }
  }
  expect((await api<QueueWire>(request, "/api/v1/queue")).revision).toBeGreaterThan(before.revision);
  await drain(request, `d2b-${operation}`);
});

for (const failure of ["500", "network", "malformed", "etag"] as const) test(`D2B-08/09 ${failure}: no auto resend, frozen replay across modes and pages`, async ({ app }) => {
  const state: Crafted = { pages: onePage([jobWire(1, "queued")]), queue: queueOf(8, null, [{ n: 1, position: 1 }]) };
  await craft(app, state); await openQueue(app); const requests: Request[] = [];
  await app.route("**/api/v1/queue/control", async route => {
    requests.push(route.request()); state.queue.body!.revision = 9;
    if (requests.length === 1) {
      if (failure === "network") return route.abort("internetdisconnected");
      if (failure === "malformed") return route.fulfill({ status: 200, body: "not-json" });
      if (failure === "500") return route.fulfill({ status: 500, body: "" });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...state.queue.body, dispatch: "paused" }), headers: { ETag: requests.length === 1 ? '"100"' : '"9"' } });
  });
  await app.getByTestId("queue-dispatch").evaluate(el => { (el as HTMLButtonElement).click(); (el as HTMLButtonElement).click(); });
  await expect(app.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "unknown");
  await expect(app.getByTestId("queue-dispatch")).toBeDisabled(); await rowOf(app, jid(1)).click(); await expect(app.getByTestId("queue-cancel")).toBeDisabled();
  await app.getByTestId("mode-normal").click(); await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-transcribe").click(); await openQueue(app); await app.waitForTimeout(2400); expect(requests).toHaveLength(1);
  await app.getByTestId("queue-recheck").click(); await settled(app); expect(requests).toHaveLength(2);
  const [a,b] = await Promise.all(requests.map(r => r.allHeaders()));
  expect(b!["idempotency-key"]).toBe(a!["idempotency-key"]); expect(b!["if-match"]).toBe('"8"'); expect(b!["if-match"]).toBe(a!["if-match"]);
  expect(requests[1]!.postData()).toBe(requests[0]!.postData());
});

test("D2B-10/15: 412 refreshes without retry; failed GET disables stale writes", async ({ app }) => {
  const state: Crafted = { pages: onePage([jobWire(1, "queued")]), queue: queueOf(8, null, [{ n: 1, position: 1 }]) };
  await craft(app, state); await openQueue(app); let writes = 0;
  await app.route("**/api/v1/queue/control", async route => { writes++; state.queue.body!.revision = 9; await route.fulfill({ status: 412, contentType: "application/json", body: JSON.stringify({ error: { code: "revision_conflict" } }) }); });
  await app.getByTestId("queue-dispatch").click(); await expect(app.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "conflict");
  await expect(app.getByTestId("queue-recheck")).toHaveCount(0); await app.waitForTimeout(2200); expect(writes).toBe(1);
  state.queue.status = 503; await app.getByTestId("queue-reload").click();
  await expect(page$(app)).toHaveAttribute("data-phase", "error"); await expect(app.getByTestId("queue-dispatch")).toBeDisabled(); await expect(rows(app)).toHaveCount(0); expect(writes).toBe(1);
});

test("D2B-03/04/06: branch selection, held blockers, complete permutation protects setup", async ({ app }) => {
  const state = branchedScenario();
  const list = state.pages.get("")!.body!;
  list.items = (list.items as Wire[]).map(job => job.id === jid(2) ? jobWire(2, "blocked", { retry_of: jid(1), blockers: ["capability_unavailable"] }) : job);
  const queue = state.queue.body!; const items = queue.items as Wire[];
  items.splice(2, 0, { id: qid(10), resource_id: "setup_1", resource_kind: "setup_operation", position: 2, held: false, blockers: [] });
  items[3]!.position = 3; items[4]!.position = 4; items[4]!.blockers = ["capability_unavailable"];
  await craft(app, state); await openQueue(app); await rowOf(app, jid(1)).click();
  await expect(app.getByTestId("queue-hold")).toBeDisabled(); await expect(app.getByTestId("queue-cancel")).toBeDisabled();
  await choose(app, "queue-target", new RegExp(jid(2))); let hold: Request | null = null;
  await app.route(`**/api/v1/queue/items/${qid(2)}`, async route => {
    hold = route.request(); items[4]!.held = true; queue.revision = 10;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(queue), headers: { ETag: '"10"' } });
  });
  await app.getByTestId("queue-hold").click(); await settled(app); expect(hold!.postDataJSON()).toEqual({ schema_version: 1, held: true }); expect(items[4]!.blockers).toEqual(["capability_unavailable"]);
  await expect(app.getByTestId("queue-hold")).toContainText("解除"); await rowOf(app, jid(6)).click();
  await expect(app.getByTestId("queue-up")).toBeDisabled(); await expect(app.getByTestId("queue-up-reason")).toContainText("跨ぐ");
  let put: Request | null = null;
  await app.route("**/api/v1/queue/order", async route => { put = route.request(); await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(queue), headers: { ETag: '"10"' } }); });
  await app.getByTestId("queue-down").click(); await settled(app);
  expect(put!.postDataJSON()).toEqual({ schema_version: 1, waiting_item_ids: [qid(4), qid(10), qid(2), qid(6)] });
  await rowOf(app, jid(1)).click(); await choose(app, "queue-target", new RegExp(jid(3))); await expect(app.getByTestId("queue-hold")).toBeDisabled();
  let cancel: Request | null = null;
  await app.route(`**/api/v1/jobs/${jid(3)}/cancel`, async route => {
    cancel = route.request();
    const cancelling = jobWire(3, "cancelling", { revision: 2, retry_of: jid(1), attempts: [{ state: "active" }] });
    list.items = (list.items as Wire[]).map(job => job.id === jid(3) ? cancelling : job);
    await route.fulfill({ status: 202, contentType: "application/json", headers: { ETag: '\"2\"' }, body: JSON.stringify(cancelling) });
  });
  await app.getByTestId("queue-cancel").click(); await settled(app);
  expect(cancel!.postDataJSON()).toEqual({ schema_version: 1, mode: "cooperative" });
  expect((await cancel!.allHeaders())["if-match"]).toBe('\"1\"');
  await expect(app.getByTestId("queue-detail")).toHaveAttribute("data-status", "cancelling");
  await expect(app.getByTestId("queue-cancel")).toBeDisabled();
  expect((list.items as Wire[]).find(job => job.id === jid(2))!.status).toBe("blocked");
  expect(items[4]!.blockers).toEqual(["capability_unavailable"]);
});

test("D2B-08: old resume unknown owns the same gate as the queue page", async ({ app, request }) => {
  // App startup already read the real queue. Crafted revisions must not go backwards in that epoch.
  const revision = (await api<QueueWire>(request, "/api/v1/queue")).revision + 1;
  const state: Crafted = { pages: onePage([jobWire(1, "queued")]), queue: queueOf(revision, null, [{ n: 1, position: 1 }]) };
  state.queue.body!.dispatch = "paused";
  await craft(app, state); await app.getByTestId("mode-expert").click();
  await app.getByTestId("v1-queue-reload").click(); await expect(app.getByTestId("v1-queue-resume")).toBeEnabled();
  const requests: Request[] = [];
  await app.route("**/api/v1/queue/control", async route => {
    requests.push(route.request());
    if (requests.length === 1) return route.fulfill({ status: 500, body: "" });
    state.queue.body!.dispatch = "running"; state.queue.body!.revision = revision + 1;
    return route.fulfill({ status: 200, contentType: "application/json", headers: { ETag: `"${revision + 1}"` }, body: JSON.stringify(state.queue.body) });
  });
  await app.getByTestId("v1-queue-resume").click(); await expect(app.getByTestId("v1-queue-unknown")).toBeVisible();
  await openQueue(app); await expect(app.getByTestId("queue-dispatch")).toBeDisabled();
  await rowOf(app, jid(1)).click(); await expect(app.getByTestId("queue-cancel")).toBeDisabled();
  await expect(app.getByTestId("queue-foreign-write")).toBeVisible(); await app.waitForTimeout(2200); expect(requests).toHaveLength(1);
  await app.getByTestId("nav-transcribe").click(); await app.getByTestId("v1-queue-recheck").click();
  await expect(app.getByTestId("v1-queue-dispatch")).toContainText("実行中");
  expect(requests).toHaveLength(2); const [a,b] = await Promise.all(requests.map(r => r.allHeaders()));
  expect(b!["idempotency-key"]).toBe(a!["idempotency-key"]); expect(b!["if-match"]).toBe(a!["if-match"]); expect(requests[1]!.postData()).toBe(requests[0]!.postData());
  await openQueue(app); await expect(app.getByTestId("queue-dispatch")).toBeEnabled();
});

test("D2B-15: a GET older than the mutation acknowledgement stays fail closed until a fresh read", async ({ app }) => {
  const state: Crafted = { pages: onePage([jobWire(1, "queued")]), queue: queueOf(8, null, [{ n: 1, position: 1 }]) };
  await craft(app, state); await openQueue(app); let writes = 0;
  await app.route("**/api/v1/queue/control", async route => {
    writes++; state.queue.body!.revision = 9;
    await route.fulfill({ status: 200, contentType: "application/json", headers: { ETag: '\"10\"' }, body: JSON.stringify({ ...state.queue.body, revision: 10, dispatch: "paused" }) });
  });
  await app.getByTestId("queue-dispatch").click();
  await expect(app.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "done");
  await expect(page$(app)).toHaveAttribute("data-phase", "error");
  await expect(app.getByTestId("queue-dispatch")).toBeDisabled(); await expect(rows(app)).toHaveCount(0);
  expect(writes).toBe(1);
  state.queue.body!.revision = 10; state.queue.body!.dispatch = "paused";
  await app.getByTestId("queue-reload").click(); await expect(page$(app)).toHaveAttribute("data-phase", "done");
  await expect(app.getByTestId("queue-dispatch")).toBeEnabled(); expect(writes).toBe(1);
});

test("D3A-03–07: finished branches, retry roots, rerun roots and artifact metadata in Results", async ({ app }) => {
  const seen = recordApi(app);
  const records = [
    jobWire(1, "failed"),
    jobWire(2, "succeeded", { retry_of: jid(1) }),
    jobWire(3, "interrupted", { rerun_of: jid(1) }),
    jobWire(4, "cancelled"),
    jobWire(5, "failed"),
    jobWire(6, "queued", { retry_of: jid(5) }),
    jobWire(7, "succeeded", { retry_of: jid(5) }),
  ];
  const state: Crafted = { pages: onePage(records), queue: queueOf(9, null, [{ n: 6, position: 1 }]) };
  await craft(app, state);
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-results").click();
  const rows = app.getByTestId("v1-board-row");
  const roots = () => rows.evaluateAll(elements => elements.map(el => el.getAttribute("data-root")));
  await expect.poll(roots).toEqual([jid(1)]);
  const first = rows.first();
  await expect(first).toHaveAttribute("data-job", jid(2));
  await first.getByTestId("results-select").click();
  const detail = app.getByTestId("results-detail");
  const artifacts = app.getByTestId("v1-artifacts");
  const endpoint = `**/api/v1/jobs/${jid(2)}/artifacts`;
  let artifactState = "available";
  await app.route(endpoint, route => route.fulfill({ json: {
    schema_version: 1, job_id: jid(2), state: artifactState, unexpected_count: artifactState === "available" ? 1 : 0,
    items: artifactState === "available" ? [
      { format: "srt", size: 50, integrity: "verified" },
      { format: "vtt", size: 40, integrity: "changed" },
      { format: "json", size: 30, integrity: "unverified" },
    ] : [],
  } }));
  await app.getByTestId("v1-artifacts-load").click();
  await expect(artifacts).toHaveAttribute("data-state", "available");
  for (const integrity of ["verified", "changed", "unverified"]) await expect(artifacts.locator(`[data-integrity="${integrity}"]`)).toBeVisible();
  for (const next of ["missing", "legacy", "none"]) {
    artifactState = next;
    await app.getByTestId("v1-artifacts-load").click();
    await expect(artifacts).toHaveAttribute("data-state", next);
  }
  await app.getByTestId("results-tab-history").click();
  await expect(detail).toHaveCount(0);
  await expect.poll(roots).toEqual([jid(4), jid(3), jid(1)]);
  await choose(app, "history-sort", "古い順");
  await expect.poll(roots).toEqual([jid(1), jid(3), jid(4)]);
  await choose(app, "history-filter", "中断");
  await expect.poll(roots).toEqual([jid(3)]);
  await rows.first().getByTestId("results-select").click();
  await expect(detail).toContainText(jid(1));
  expect(writes(seen)).toEqual([]);
});
