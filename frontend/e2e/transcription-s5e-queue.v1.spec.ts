import type { APIRequestContext, Page, Request, Route } from "@playwright/test";
import { choose, expect, SESSION, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * Bundle D2-A: the Expert queue page in the `--next-ui-v1` shape. It reads the one board
 * (`core.v1.board`: every page of `GET /api/v1/jobs` + `GET /api/v1/queue`) and shows it as a table
 * plus the selected logical job's detail. Read-only: no cancel, retry, reorder, hold or pause here,
 * and no search/filter/sort ever sends a write.
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
  // Read-only page: no operation buttons at all.
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

test("D2A-08: one board poller for both views; leaving stops it; an unknown board write is never replayed by the queue page", async ({ app, request }) => {
  const seen = recordApi(app);
  const queueReads = () => seen.filter((r) => line(r) === "GET /api/v1/queue").length;
  await expert(app, request, ["slow-meeting.wav"]);
  const [slow] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await openQueue(app);
  await expect(rowOf(app, slow!)).toBeVisible();
  await app.waitForTimeout(1500);
  let mark = queueReads();
  await app.waitForTimeout(6000);
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
  await app.waitForTimeout(1500);
  mark = queueReads();
  await app.waitForTimeout(6000);
  expect(queueReads() - mark).toBeLessThanOrEqual(Math.max(baseline, 3) + 1);
  // Leaving both views stops the polling.
  await app.getByTestId("mode-normal").click();
  await app.getByTestId("nav-settings").click();
  await app.waitForTimeout(1000);
  mark = queueReads();
  await app.waitForTimeout(4500);
  expect(queueReads() - mark).toBe(0);

  // A cancel from Expert ③ whose answer is lost (the server applied it) stays unknown.
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-transcribe").click();
  let first = true;
  await app.route("**/api/v1/jobs/*/cancel", async (route) => {
    if (!first) return route.continue();
    first = false;
    await route.fetch();
    await route.fulfill({ status: 500, body: "" });
  });
  const cancels = () => seen.filter((r) => r.method() === "POST" && new URL(r.url()).pathname.endsWith("/cancel"));
  await app.locator(`[data-testid="v1-board-row"][data-root="${slow}"]`).getByTestId("v1-board-cancel").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "unknown");
  expect(cancels()).toHaveLength(1);
  // The queue page says so and points back; it neither resends nor hides the open state.
  await app.getByTestId("nav-queue").click();
  await expect(app.getByTestId("queue-action-open")).toBeVisible();
  await expect(page$(app).getByTestId("v1-board-recheck")).toHaveCount(0);
  await app.waitForTimeout(3000);
  expect(cancels()).toHaveLength(1);
  await app.getByTestId("queue-action-open").getByTestId("queue-open-transcribe").click();
  await expect(app.getByTestId("content")).toHaveAttribute("data-section", "transcribe");
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "unknown");
  await app.getByTestId("v1-board-recheck").click();
  await expect(app.getByTestId("v1-board-action")).not.toHaveAttribute("data-action-phase", "unknown");
  expect(cancels()).toHaveLength(2);
  const [a, b] = await Promise.all(cancels().map((r) => r.allHeaders()));
  expect(b!["idempotency-key"]).toBe(a!["idempotency-key"]);
  await drain(request, "poll");
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
  await expect(page$(app).locator(".v-progress-linear, [role='progressbar']")).toHaveCount(0);
  await expect(page$(app)).not.toContainText("%");
  expect(writes(seen)).toEqual([]);
  await shot(app, "d2a-03-crafted-branches");
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
