import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, openSingleJobTools, SESSION, shot } from "./fixtures";
import { test } from "./isolatedV1";

/**
 * Bundle C2b against the real FastAPI app in the `--next-ui-v1` shape (tools/ui2/serve_next_ui.py
 * --v1): real B5/B7, control store layout v7 (history visibility), queue and dispatcher, the real
 * session/Host/Origin middleware, the browser's own fetch. Synthetic only: B4 evidence and a worker
 * runner that loads no model and writes one synthetic .srt into the job's own directory
 * (tools/ui2/synthetic_v1.py). Headless Chromium — NOT a WebView2 acceptance.
 *
 * Each test owns its synthetic store, queue, server and dispatcher.
 */

const input = (root: string, name: string) => `${root}/in/${name}`;
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
const legacyWrites = (seen: Request[]) => writes(seen).filter((l) => !l.includes(" /api/v1/") && !l.startsWith("PATCH /api/settings"));

async function api<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path, { headers: AUTH });
  expect(response.status(), path).toBe(200);
  return (await response.json()) as T;
}

/** Rows of the auxiliary list "jobs on the server (v1)" that name one internal job id. */
const recentItem = (page: Page, id: string) => page.getByTestId("v1-recent-item").filter({ hasText: id });

/** Read the auxiliary list on request (the user's own button), and wait for the server's answer. */
async function loadRecent(page: Page): Promise<void> {
  await openSingleJobTools(page);
  await page.getByTestId("v1-recent-reload").click();
  await expect(page.getByTestId("v1-recent")).toHaveAttribute("data-phase", "done");
}

async function listsLoaded(page: Page): Promise<void> {
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
}

/** Use this spec's own Preset/Policy, creating them once (explicitly) when they do not exist yet. */
async function library(page: Page, request: APIRequestContext): Promise<void> {
  await listsLoaded(page);
  const presets = await api<{ items: { name: string }[] }>(request, "/api/v1/presets");
  if (!presets.items.some((p) => p.name === PRESET)) {
    await page.getByTestId("v1-preset-create-open").click();
    await page.getByTestId("v1-preset-name").locator("input").fill(PRESET);
    await page.getByTestId("v1-preset-model").locator("input").fill("large-v3-turbo");
    await page.getByTestId("v1-preset-check").click();
    await page.getByTestId("v1-preset-save").click();
    await expect(page.getByTestId("v1-preset-frozen")).toContainText(PRESET);
  } else {
    await choose(page, "v1-preset-select", new RegExp(PRESET));
  }
  const policies = await api<{ items: { name: string }[] }>(request, "/api/v1/execution-policies");
  if (!policies.items.some((p) => p.name === POLICY)) {
    await page.getByTestId("v1-policy-create-open").click();
    await page.getByTestId("v1-policy-name").locator("input").fill(POLICY);
    await page.getByTestId("v1-policy-check").click();
    await page.getByTestId("v1-policy-save").click();
    await expect(page.getByTestId("v1-policy-frozen")).toContainText(POLICY);
  } else {
    await choose(page, "v1-policy-select", new RegExp(POLICY));
  }
  await expect(page.getByTestId("v1-preset-frozen")).toContainText(PRESET);
  await expect(page.getByTestId("v1-policy-frozen")).toContainText(POLICY);
}

async function addInput(page: Page, path: string): Promise<void> {
  await page.getByTestId("tx-input-path").locator("input").fill(path);
  await page.getByTestId("tx-input-add-typed").click();
}

async function expert(page: Page, request: APIRequestContext, files: string[], root: string): Promise<void> {
  await page.getByTestId("mode-expert").click();
  await library(page, request);
  await page.getByTestId("v1-output-path").locator("input").fill(`${root}/out`);
  for (const name of files) await addInput(page, input(root, name));
  await page.getByTestId("tx-list-select-all").click();
  await expect(page.getByTestId("v1-batch-count")).toContainText(String(files.length));
}

async function ensureQueueRunning(page: Page): Promise<void> {
  await openSingleJobTools(page);
  const dispatch = page.getByTestId("v1-queue-dispatch");
  await expect(dispatch).toBeVisible();
  if (/停止中/.test((await dispatch.textContent()) ?? "")) {
    await page.getByTestId("v1-queue-resume").click();
    await expect(dispatch).toContainText(/実行中/);
  }
}

/** Submit the selected rows as one batch and return the ids of the created jobs (file order). */
async function submitBatch(page: Page, seen: Request[]): Promise<string[]> {
  await page.getByTestId("v1-batch-check").click();
  await expect(page.getByTestId("v1-batch-resolved")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("v1-batch-submit").click();
  await expect(page.getByTestId("v1-batch-accepted")).toBeVisible({ timeout: 20_000 });
  const post = seen.filter((r) => line(r) === "POST /api/v1/jobs").at(-1)!;
  const body = (await (await post.response())!.json()) as { jobs: { id: string }[] };
  return body.jobs.map((j) => j.id);
}

async function openHistory(page: Page): Promise<void> {
  await page.getByTestId("nav-results").click();
  await page.getByTestId("results-tab-history").click();
}

const row = (page: Page, section: "queue" | "history" | "hidden", id: string) => page.getByTestId(`v1-board-${section}`).locator(`[data-testid="v1-board-row"][data-root="${id}"]`);

test.beforeEach(async ({ request }) => {
  expect(await api(request, "/api/v1/queue")).toMatchObject({
    active_item_id: null, dispatch: "paused", items: [],
  });
});

test.describe("launch confirmation CAS", () => {
  test.use({ holdSlowLaunch: true });

  test("rejects a stale cancel and only cancels after a new explicit action", async ({ app, request, backend }) => {
    type Job = { status: string; revision: number; reason_code: string; updated_at: string };
    type Queue = { dispatch: string; active_item_id: string | null; items: { id: string; resource_id: string; position: number | null }[] };
    const seen = recordApi(app);
    await expert(app, request, ["slow-meeting.wav", "meeting.wav"], backend.root);
    const [id, follower] = await submitBatch(app, seen);
    await ensureQueueRunning(app);
    const path = `/api/v1/jobs/${id}`;
    const cancelPath = `${path}/cancel`;
    const read = () => api<Job>(request, path);
    const queue = () => api<Queue>(request, "/api/v1/queue");
    await app.getByTestId("nav-queue").click();
    await app.locator(`[data-testid="queue-row"][data-root="${id}"]`).click();
    const detail = app.getByTestId("queue-detail");
    await expect(detail).toHaveAttribute("data-status", "running");
    const before = await read();
    expect(before).toMatchObject({ revision: 2, reason_code: "dispatch_started" });
    const active = await queue();
    expect(active.active_item_id).toBe(active.items.find((item) => item.resource_id === id)?.id);
    expect(active.items.find((item) => item.resource_id === follower)?.position).toBe(1);

    // Release the real dispatcher only after the browser has frozen If-Match for its first click.
    await app.route(`**${cancelPath}`, async (route) => {
      await backend.releaseSlowLaunch();
      await expect.poll(read).toMatchObject({ status: "running", revision: 3, reason_code: "launch_confirmed" });
      await route.continue();
    }, { times: 1 });
    const first = app.waitForResponse((response) => line(response.request()) === `POST ${cancelPath}`);
    const cancel = app.getByTestId("queue-cancel");
    await cancel.click();
    const conflict = await first;
    expect(conflict.status()).toBe(412);
    expect(await conflict.json()).toMatchObject({ error: { code: "revision_conflict" } });
    expect((await conflict.request().allHeaders())["if-match"]).toBe('"2"');
    await expect(app.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "conflict");
    await expect(detail).toContainText("起動を確認");
    await expect(detail).toHaveAttribute("data-status", "running");
    await expect(cancel).toBeEnabled();
    const confirmed = await read();
    expect(confirmed).toMatchObject({ status: "running", revision: 3 });
    expect((await queue()).active_item_id).toBe(active.active_item_id);
    expect(await api(request, `/api/v1/jobs/${follower}`)).toMatchObject({ status: "queued" });
    const cancels = () => writes(seen).filter((entry) => entry === `POST ${cancelPath}`);
    expect(cancels()).toHaveLength(1);
    const event = ({ status, revision, reason_code, updated_at }: Job) => ({ status, revision, reason_code, updated_at });
    console.info(JSON.stringify({
      job: id, before: event(before), confirmed: event(confirmed),
      firstIfMatch: 2, firstStatus: 412, code: "revision_conflict",
      dispatch: active.dispatch, active: active.active_item_id, follower, followerPosition: 1,
    }));

    const second = app.waitForResponse((response) => line(response.request()) === `POST ${cancelPath}`);
    await cancel.click();
    const accepted = await second;
    expect(accepted.status()).toBe(202);
    expect((await accepted.request().allHeaders())["if-match"]).toBe('"3"');
    await expect.poll(read).toMatchObject({ status: "cancelled" });
    await expect.poll(() => api(request, `/api/v1/jobs/${follower}`)).toMatchObject({ status: "succeeded" });
    await expect.poll(queue).toMatchObject({ active_item_id: null, items: [] });
    expect(cancels()).toHaveLength(2);
  });
});

test("isolated teardown reaps a server even while a slow job occupies its slot", async ({ app, request, backend }) => {
  const seen = recordApi(app);
  await expert(app, request, ["slow-meeting.wav"], backend.root);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await expect.poll(() => api(request, `/api/v1/jobs/${id}`)).toMatchObject({
    status: "running", reason_code: "launch_confirmed",
  });
  expect(await api(request, "/api/v1/queue")).toMatchObject({ active_item_id: expect.any(String) });
  // Leave the slot occupied, as it would be after an assertion failure. Fixture teardown
  // waits for process-tree exit, and beforeEach verifies the next test sees an empty queue.
});

test("atomic batch: two files → one check, one submit, two jobs; the board shows them and their verified outputs", async ({ app, request, backend: { root } }) => {
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav", "other.wav"], root);
  // Nothing is sent before the explicit check; submit is impossible before it.
  await expect(app.getByTestId("v1-batch-submit")).toBeDisabled();
  const ids = await submitBatch(app, seen);
  expect(ids).toHaveLength(2);
  for (const id of ids) expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ id, retry_of: null });
  const v1Writes = writes(seen).filter((l) => l.includes("/api/v1/") && !l.includes("/presets") && !l.includes("/execution-policies"));
  expect(v1Writes).toEqual(["POST /api/v1/validations", "POST /api/v1/execution-resolutions", "POST /api/v1/jobs"]);
  const submitted = JSON.parse(seen.find((r) => line(r) === "POST /api/v1/jobs")!.postData()!) as { editing_session: { files: { input_path: string; output_dir: string }[] } };
  expect(submitted.editing_session.files.map((f) => f.input_path)).toEqual([input(root, "meeting.wav"), input(root, "other.wav")]);
  expect(submitted.editing_session.files.every((f) => f.output_dir === `${root}/out`)).toBe(true);
  expect(legacyWrites(seen)).toEqual([]);

  await ensureQueueRunning(app);
  await app.getByTestId("nav-results").click();
  const resultsList = app.getByTestId("results-table-results");
  for (const id of ids) await expect(resultsList.locator(`[data-root="${id}"]`)).toHaveAttribute("data-status", "succeeded");
  await openHistory(app);
  for (const id of ids) await expect(row(app, "history", id)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  // The row is labelled with the file of this page; the internal job id is shown for reference.
  await expect(row(app, "history", ids[0]!).getByTestId("v1-board-row-title")).toContainText("meeting.wav");
  await expect(row(app, "history", ids[1]!).getByTestId("v1-board-row-title")).toContainText("other.wav");

  // Read-only artifact view: format, size, integrity. No file name, no path.
  await row(app, "history", ids[0]!).getByTestId("results-select").click();
  const first = app.getByTestId("results-detail");
  await first.getByTestId("v1-artifacts-load").click();
  await expect(first.getByTestId("v1-artifacts")).toHaveAttribute("data-state", "available");
  await expect(first.locator('[data-format="srt"]')).toHaveAttribute("data-integrity", "verified");
  const text = (await first.textContent()) ?? "";
  expect(text).not.toContain(root);
  expect(text).not.toContain(".srt");
  const artifacts = await api<{ state: string; items: { format: string; integrity: string }[]; unexpected_count: number }>(request, `/api/v1/jobs/${ids[0]}/artifacts`);
  expect(artifacts).toMatchObject({ state: "available", unexpected_count: 0, items: [{ format: "srt", integrity: "verified" }] });
  expect(JSON.stringify(artifacts)).not.toContain(root);
  await shot(app, "s5b-batch-board");
});

test("hide and show: the row leaves and returns; the job, its state and its outputs are untouched; nothing is deleted", async ({ app, request, backend: { root } }) => {
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav"], root);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await openHistory(app);
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const job = await api<{ revision: number; status: string }>(request, `/api/v1/jobs/${id}`);
  // The auxiliary list was read before the hide (Windows acceptance P3: it must not stay stale).
  await app.getByTestId("nav-transcribe").click();
  await loadRecent(app);
  await expect(recentItem(app, id!)).toHaveCount(1);

  await openHistory(app);
  await row(app, "history", id!).getByTestId("results-select").click();
  await app.getByTestId("v1-board-hide").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "done");
  await expect(row(app, "history", id!)).toHaveCount(0);
  await app.getByTestId("nav-transcribe").click();
  await expect(recentItem(app, id!)).toHaveCount(0);
  await openHistory(app);
  const visible = await api<{ items: { id: string }[] }>(request, "/api/v1/jobs");
  expect(visible.items.map((j) => j.id)).not.toContain(id);
  // Still there by id, same state and revision; the output directory is still verified.
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ revision: job.revision, status: "succeeded" });
  expect(await api(request, `/api/v1/jobs/${id}/artifacts`)).toMatchObject({ state: "available", items: [{ format: "srt", integrity: "verified" }] });
  expect(await api(request, `/api/v1/jobs/${id}/visibility`)).toMatchObject({ hidden: true, revision: 1 });

  await app.getByTestId("v1-board-action-close").click();
  await app.getByTestId("v1-board-show-hidden").locator("input").check();
  await expect(row(app, "hidden", id!)).toBeVisible();
  await row(app, "hidden", id!).getByTestId("results-select").click();
  await app.getByTestId("v1-board-show").click();
  await expect(row(app, "hidden", id!)).toHaveCount(0);
  await expect(app.getByTestId("results-detail")).toHaveCount(0);
  await app.getByTestId("v1-board-show-hidden").locator("input").uncheck();
  await expect(row(app, "history", id!)).toBeVisible();
  await expect(row(app, "hidden", id!)).toHaveCount(0);
  await app.getByTestId("nav-transcribe").click();
  await expect(recentItem(app, id!)).toHaveCount(1);
  expect(await api(request, `/api/v1/jobs/${id}/visibility`)).toMatchObject({ hidden: false, revision: 2 });
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ revision: job.revision, status: "succeeded" });

  const puts = seen.filter((r) => r.method() === "PUT");
  expect(puts.map(line)).toEqual([`PUT /api/v1/jobs/${id}/visibility`, `PUT /api/v1/jobs/${id}/visibility`]);
  for (const r of puts) {
    const headers = await r.allHeaders();
    expect(headers["if-match"]).toMatch(/^"\d+"$/);
    expect(headers["idempotency-key"]).toBeUndefined();
    expect(headers.origin).toBe(new URL(r.url()).origin);
  }
  // No request of this screen can delete anything.
  expect(seen.filter((r) => r.method() === "DELETE")).toEqual([]);
  expect(legacyWrites(seen)).toEqual([]);
  await shot(app, "s5b-hidden-history");
});

async function cancelFromQueue(page: Page, root: string, running = false): Promise<void> {
  await page.getByTestId("nav-queue").click();
  await page.locator(`[data-testid="queue-row"][data-root="${root}"]`).click();
  if (running) {
    await expect(page.getByTestId("queue-detail")).toContainText("起動を確認");
  }
  await page.getByTestId("queue-cancel").click();
  await expect(page.getByTestId("queue-write-state")).toHaveAttribute("data-action-phase", "done");
  await page.getByTestId("nav-transcribe").click();
}

test("retry is the same row: cancel a running job, retry it, the row returns to the queue with two attempts", async ({ app, request, backend: { root } }) => {
  const seen = recordApi(app);
  await expert(app, request, ["slow-meeting.wav"], root);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await expect(row(app, "queue", id!)).toHaveAttribute("data-status", "running", { timeout: 30_000 });
  await cancelFromQueue(app, id!, true);
  await openHistory(app);
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await app.getByTestId("v1-board-action-close").click();
  const cancelled = await api<{ revision: number }>(request, `/api/v1/jobs/${id}`);

  await row(app, "history", id!).getByTestId("results-select").click();
  await app.getByTestId("v1-board-retry").click();
  await expect(row(app, "history", id!)).toHaveCount(0);
  await app.getByTestId("nav-transcribe").click();
  // Same row key (the first internal job), now with two attempts, back in the queue.
  await expect(row(app, "queue", id!)).toHaveAttribute("data-attempts", "2", { timeout: 20_000 });
  // The newest internal job of the row is the retry, linked to the job it retries.
  const internal = ((await row(app, "queue", id!).textContent()) ?? "").match(/job_[0-9a-f]{32}/)![0];
  expect(internal).not.toBe(id);
  expect(await api(request, `/api/v1/jobs/${internal}`)).toMatchObject({ retry_of: id });
  // B7 contract kept: the retried job itself is unchanged, the retry is a new internal job.
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ revision: cancelled.revision, status: "cancelled" });
  const retryPost = seen.find((r) => line(r) === `POST /api/v1/jobs/${id}/retry`)!;
  expect((await retryPost.allHeaders())["if-match"]).toBe(`"${cancelled.revision}"`);

  // Finish the retry so the entire chain can be hidden and shown.
  await app.getByTestId("v1-board-action-close").click();
  await expect(row(app, "queue", id!)).toHaveAttribute("data-status", "running", { timeout: 30_000 });
  await cancelFromQueue(app, id!, true);
  await openHistory(app);
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await expect(row(app, "history", id!)).toHaveAttribute("data-attempts", "2");

  // Windows acceptance P3: hiding a finished retry chain removes *both* internal jobs from the
  // auxiliary list that was read before, and showing it brings both back. Nothing is deleted.
  await app.getByTestId("v1-board-action-close").click();
  const chain = [id!, internal];
  const before = await Promise.all(chain.map((job) => api<{ revision: number; status: string }>(request, `/api/v1/jobs/${job}`)));
  await app.getByTestId("nav-transcribe").click();
  await loadRecent(app);
  for (const job of chain) await expect(recentItem(app, job)).toHaveCount(1);
  await openHistory(app);
  await row(app, "history", id!).getByTestId("results-select").click();
  await app.getByTestId("v1-board-hide").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "done");
  await expect(row(app, "history", id!)).toHaveCount(0);
  await app.getByTestId("nav-transcribe").click();
  for (const job of chain) await expect(recentItem(app, job)).toHaveCount(0);
  await openHistory(app);
  const visible = await api<{ items: { id: string }[] }>(request, "/api/v1/jobs");
  for (const job of chain) expect(visible.items.map((j) => j.id)).not.toContain(job);

  await app.getByTestId("v1-board-action-close").click();
  await app.getByTestId("v1-board-show-hidden").locator("input").check();
  await row(app, "hidden", id!).getByTestId("results-select").click();
  await app.getByTestId("v1-board-show").click();
  await expect(row(app, "hidden", id!)).toHaveCount(0);
  await expect(app.getByTestId("results-detail")).toHaveCount(0);
  await app.getByTestId("v1-board-show-hidden").locator("input").uncheck();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "done");
  await expect(row(app, "history", id!)).toHaveAttribute("data-attempts", "2");
  await app.getByTestId("nav-transcribe").click();
  for (const job of chain) await expect(recentItem(app, job)).toHaveCount(1);
  // Same job records as before (state and revision), four visibility PUTs, no DELETE.
  for (const [index, job] of chain.entries()) expect(await api(request, `/api/v1/jobs/${job}`)).toMatchObject(before[index]!);
  expect(seen.filter((r) => r.method() === "PUT" && line(r).endsWith("/visibility")).map(line).sort()).toEqual(chain.flatMap((job) => [`PUT /api/v1/jobs/${job}/visibility`, `PUT /api/v1/jobs/${job}/visibility`]).sort());
  expect(seen.filter((r) => r.method() === "DELETE")).toEqual([]);
  expect(legacyWrites(seen)).toEqual([]);
});

test("PR #53 P2-1: a retried row is placed where B7 queued its new job, after the job that was already waiting", async ({ app, request, backend: { root } }) => {
  const seen = recordApi(app);
  // Cancel slow-meeting (A), then pause dispatch so B is queued before A is retried.
  await expert(app, request, ["slow-meeting.wav"], root);
  const [a] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await expect(row(app, "queue", a!)).toHaveAttribute("data-status", "running", { timeout: 30_000 });
  await cancelFromQueue(app, a!, true);
  await openHistory(app);
  await expect(row(app, "history", a!)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await app.getByTestId("v1-board-action-close").click();

  // Pause dispatch through the real API so the order below is stable, then queue B.
  const queue = await api<{ revision: number }>(request, "/api/v1/queue");
  const paused = await request.patch("/api/v1/queue/control", {
    headers: { "x-utteran-session": SESSION, "If-Match": `"${queue.revision}"`, "Idempotency-Key": `s5b-pause-${a!.slice(4, 24)}` },
    data: { schema_version: 1, dispatch: "paused" },
  });
  expect(paused.status()).toBe(200);
  await app.getByTestId("nav-transcribe").click();
  await app.getByTestId("tx-list-select-none").click();
  await addInput(app, input(root, "meeting.wav"));
  await app.getByTestId("tx-list-check").last().locator("input").check();
  await expect(app.getByTestId("v1-batch-count")).toContainText("1");
  const [b] = await submitBatch(app, seen);
  await expect(row(app, "queue", b!)).toHaveAttribute("data-status", "queued");

  await openHistory(app);
  // Retry A: B7 queues the new internal job *behind* B. The row of A must not be shown before B.
  await row(app, "history", a!).getByTestId("results-select").click();
  await app.getByTestId("v1-board-retry").click();
  await expect(row(app, "history", a!)).toHaveCount(0);
  await app.getByTestId("nav-transcribe").click();
  await expect(row(app, "queue", a!)).toHaveAttribute("data-attempts", "2", { timeout: 20_000 });
  const order = await app.getByTestId("v1-board-queue").locator('[data-testid="v1-board-row"]').evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-root")));
  expect(order.indexOf(b!)).toBeGreaterThanOrEqual(0);
  expect(order.indexOf(b!)).toBeLessThan(order.indexOf(a!));
  // The positions on screen are B7's own.
  const view = await api<{ items: { resource_id: string; position: number | null }[] }>(request, "/api/v1/queue");
  const retried = await row(app, "queue", a!).getAttribute("data-job");
  const position = (id: string) => view.items.find((i) => i.resource_id === id)!.position!;
  expect(position(b!)).toBeLessThan(position(retried!));
  await expect(row(app, "queue", b!).getByTestId("v1-board-row-place")).toContainText(String(position(b!)));
  await expect(row(app, "queue", a!).getByTestId("v1-board-row-place")).toContainText(String(position(retried!)));

  // Clean up: cancel both waiting jobs and resume dispatch for later specs.
  await app.getByTestId("v1-board-action-close").click();
  await cancelFromQueue(app, b!);
  await openHistory(app);
  await expect(row(app, "history", b!)).toHaveAttribute("data-status", "cancelled", { timeout: 20_000 });
  await app.getByTestId("v1-board-action-close").click();
  await cancelFromQueue(app, a!);
  await openHistory(app);
  await expect(row(app, "history", a!)).toHaveAttribute("data-status", "cancelled", { timeout: 20_000 });
  const again = await api<{ revision: number }>(request, "/api/v1/queue");
  const resumed = await request.patch("/api/v1/queue/control", {
    headers: { "x-utteran-session": SESSION, "If-Match": `"${again.revision}"`, "Idempotency-Key": `s5b-resume-${a!.slice(4, 24)}` },
    data: { schema_version: 1, dispatch: "running" },
  });
  expect(resumed.status()).toBe(200);
  expect(legacyWrites(seen)).toEqual([]);
});

async function enableRerunPicker(app: Page): Promise<void> {
  await app.addInitScript(() => {
    const native = window as Window & { pywebview?: unknown; rerunPick?: string };
    native.pywebview = { api: { choose_path: async () => native.rerunPick ?? null } };
  });
  await app.goto(`/launch?session=${SESSION}`);
  await expect(app.getByTestId("app-ready")).toBeVisible();
}

test("D3-B Expert results and history share explicit rerun and verified artifact status", async ({ app, request, backend: { root } }) => {
  const relocated = input(root, "rerun-relocated.wav");
  const customOutput = `${root}/out/rerun-source`;
  copyFileSync(input(root, "meeting.wav"), relocated);
  mkdirSync(customOutput, { recursive: true });
  await enableRerunPicker(app);
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav"], root);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await app.getByTestId("tx-ov-output-custom").click();
  await app.getByTestId("tx-ov-output-path").locator("input").fill(customOutput);
  await app.getByTestId("tx-ov-apply").click();
  await app.getByTestId("nav-results").click();
  const result = app.getByTestId("results-table-results").locator(`[data-root="${id}"]`);
  await expect(result).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  await result.getByTestId("results-select").click();
  const source = await api<{ revision: number }>(request, `/api/v1/jobs/${id}`);
  const before = writes(seen);
  await app.getByTestId("v1-artifacts-load").click();
  await expect(app.getByTestId("v1-artifacts-external-blocked")).toBeVisible();
  await app.getByTestId("result-rerun").focus();
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("rerun-dialog")).toContainText("現在の設定で再実行");
  await expect(app.getByTestId("rerun-dialog")).toContainText("meeting.wav");
  await app.getByTestId("rerun-pick").click();
  await expect(app.getByTestId("rerun-close")).toBeEnabled();
  await expect(app.getByTestId("rerun-dialog")).toContainText("meeting.wav");
  await app.getByTestId("rerun-close").click();
  expect(writes(seen)).toEqual(before);

  await openHistory(app);
  await row(app, "history", id!).getByTestId("results-select").click();
  await app.setViewportSize({ width: 900, height: 660 });
  await app.getByTestId("result-rerun").click();
  const dialog = app.getByTestId("rerun-dialog");
  await expect(dialog).toBeVisible();
  await app.evaluate(path => {
    (window as Window & { rerunPick?: string }).rerunPick = path;
  }, relocated);
  await app.getByTestId("rerun-pick").click();
  await expect(dialog).toContainText("rerun-relocated.wav");
  await expect(app.getByTestId("rerun-settings-source")).toBeVisible();
  await expect(dialog).toContainText(customOutput);
  await expect(app.getByTestId("rerun-submit")).toBeDisabled();
  await app.getByTestId("rerun-check").click();
  await expect(dialog).toHaveAttribute("data-step", "resolved");
  await app.getByTestId("rerun-submit").click();
  await expect(dialog).toHaveAttribute("data-step", "accepted");
  const path = `POST /api/v1/jobs/${id}/rerun`;
  const posts = seen.filter(r => line(r) === path);
  expect(posts).toHaveLength(1);
  expect(posts[0]!.postDataJSON().submission.editing_session.files).toEqual([
    expect.objectContaining({ input_path: relocated, output_dir: customOutput, overrides: [] }),
  ]);
  expect(await posts[0]!.headerValue("If-Match")).toBe(`"${source.revision}"`);
  const { jobs: [created] } = (await (await posts[0]!.response())!.json()) as { jobs: { id: string; rerun_of: string; retry_of: null }[] };
  expect(created).toMatchObject({ rerun_of: id, retry_of: null });
  expect(created!.id).not.toBe(id);
  await app.getByTestId("rerun-close").click();
  await expect(row(app, "history", created!.id)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  await row(app, "history", created!.id).getByTestId("results-select").click();
  await expect(app.getByTestId("results-detail")).toContainText(id!);
  expect(await api(request, `/api/v1/jobs/${id}`)).toEqual(source);
  expect(seen.filter(r => line(r) === path)).toHaveLength(1);
  expect(legacyWrites(seen)).toEqual([]);
});

test("D3-B rejects a different file and uses common settings when the source row is absent", async ({ app, request, backend: { root } }) => {
  const other = input(root, "rerun-different.wav");
  writeFileSync(other, "RIFF a different synthetic input");
  await enableRerunPicker(app);
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav"], root);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await app.getByTestId("tx-list-remove").click();
  await app.getByTestId("tx-list-remove-yes").click();
  await openHistory(app);
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "succeeded");
  await row(app, "history", id!).getByTestId("results-select").click();
  await app.getByTestId("result-rerun").click();
  const path = `POST /api/v1/jobs/${id}/rerun`;
  const dialog = app.getByTestId("rerun-dialog");
  await app.evaluate(path => {
    (window as Window & { rerunPick?: string }).rerunPick = path;
  }, other);
  await app.getByTestId("rerun-pick").click();
  await expect(dialog).toContainText("rerun-different.wav");
  await expect(app.getByTestId("rerun-settings-default")).toBeVisible();
  await app.getByTestId("rerun-check").click();
  await expect(dialog).toHaveAttribute("data-step", "resolved");
  await app.getByTestId("rerun-submit").click();
  await expect(dialog).toHaveAttribute("data-step", "rejected");
  expect((await seen.filter(r => line(r) === path).at(-1)!.response())!.status()).toBe(409);
  await app.getByTestId("rerun-close").click();
  await app.getByTestId("mode-normal").click();
  await expect(app.getByTestId("result-rerun")).toHaveCount(0);
  expect(seen.filter(r => line(r) === path)).toHaveLength(1);
  expect(legacyWrites(seen)).toEqual([]);
});

test("D3-B English rerun confirmation returns keyboard focus without submitting", async ({ app, request, backend: { root } }) => {
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav"], root);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  const settings = await request.patch("/api/settings", {
    headers: { "x-utteran-session": SESSION }, data: { language: "en" },
  });
  expect(settings.status()).toBe(200);
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await app.getByTestId("mode-expert").click();
  await openHistory(app);
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "succeeded");
  await row(app, "history", id!).getByTestId("results-select").press("Enter");
  const rerun = app.getByTestId("result-rerun");
  await expect(rerun).toHaveText("Run again with current settings");
  await rerun.press("Enter");
  const dialog = app.getByTestId("rerun-dialog");
  await expect(dialog).toContainText("This creates a separate job using the current settings.");
  const close = app.getByTestId("rerun-close");
  await expect(close).toBeFocused();
  await close.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(rerun).toBeFocused();
  expect(seen.filter(r => line(r) === `POST /api/v1/jobs/${id}/rerun`)).toHaveLength(0);
  expect(legacyWrites(seen)).toEqual([]);
});
