import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION, shot, test } from "./fixtures";

/**
 * Bundle C2b against the real FastAPI app in the `--next-ui-v1` shape (tools/ui2/serve_next_ui.py
 * --v1): real B5/B7, control store layout v7 (history visibility), queue and dispatcher, the real
 * session/Host/Origin middleware, the browser's own fetch. Synthetic only: B4 evidence and a worker
 * runner that loads no model and writes one synthetic .srt into the job's own directory
 * (tools/ui2/synthetic_v1.py). Headless Chromium — NOT a WebView2 acceptance.
 *
 * Runs after transcription-s5a.v1.spec.ts on the same data root (workers: 1, file order), so the
 * store already holds jobs; every assertion here is scoped to the jobs this spec creates.
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

async function expert(page: Page, request: APIRequestContext, files: string[]): Promise<void> {
  await page.getByTestId("mode-expert").click();
  await library(page, request);
  await page.getByTestId("v1-output-path").locator("input").fill(OUT);
  for (const name of files) await addInput(page, input(name));
  await page.getByTestId("tx-list-select-all").click();
  await expect(page.getByTestId("v1-batch-count")).toContainText(String(files.length));
}

async function ensureQueueRunning(page: Page): Promise<void> {
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

const row = (page: Page, section: "queue" | "history" | "hidden", id: string) => page.getByTestId(`v1-board-${section}`).locator(`[data-testid="v1-board-row"][data-root="${id}"]`);

test("setup: cancel the jobs earlier specs left waiting, so this spec's jobs are next in the single slot", async ({ request }) => {
  // The S5-A paging test leaves about a hundred queued jobs; each would take the slot for a second.
  const headers = { "x-utteran-session": SESSION };
  for (let round = 0; round < 5; round += 1) {
    const queue = await api<{ items: { resource_id: string; resource_kind: string }[] }>(request, "/api/v1/queue");
    const waiting = queue.items.filter((item) => item.resource_kind === "job");
    if (waiting.length === 0) break;
    for (const item of waiting) {
      const job = await api<{ revision: number }>(request, `/api/v1/jobs/${item.resource_id}`);
      const response = await request.post(`/api/v1/jobs/${item.resource_id}/cancel`, {
        headers: { ...headers, "If-Match": `"${job.revision}"`, "Idempotency-Key": `s5b-drain-${item.resource_id.slice(4, 20)}-${round}` },
        data: { schema_version: 1, mode: "cooperative" },
      });
      expect([200, 202, 412]).toContain(response.status());
    }
  }
  await expect.poll(async () => (await api<{ items: unknown[] }>(request, "/api/v1/queue")).items.length, { timeout: 30_000 }).toBe(0);
});

test("atomic batch: two files → one check, one submit, two jobs; the board shows them and their verified outputs", async ({ app, request }) => {
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav", "other.wav"]);
  // Nothing is sent before the explicit check; submit is impossible before it.
  await expect(app.getByTestId("v1-batch-submit")).toBeDisabled();
  const ids = await submitBatch(app, seen);
  expect(ids).toHaveLength(2);
  for (const id of ids) expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ id, retry_of: null });
  const v1Writes = writes(seen).filter((l) => l.includes("/api/v1/") && !l.includes("/presets") && !l.includes("/execution-policies"));
  expect(v1Writes).toEqual(["POST /api/v1/validations", "POST /api/v1/execution-resolutions", "POST /api/v1/jobs"]);
  const submitted = JSON.parse(seen.find((r) => line(r) === "POST /api/v1/jobs")!.postData()!) as { editing_session: { files: { input_path: string; output_dir: string }[] } };
  expect(submitted.editing_session.files.map((f) => f.input_path)).toEqual([input("meeting.wav"), input("other.wav")]);
  expect(submitted.editing_session.files.every((f) => f.output_dir === OUT)).toBe(true);
  expect(legacyWrites(seen)).toEqual([]);

  await ensureQueueRunning(app);
  for (const id of ids) await expect(row(app, "history", id)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  // The row is labelled with the file of this page; the internal job id is shown for reference.
  await expect(row(app, "history", ids[0]!).getByTestId("v1-board-row-title")).toContainText("meeting.wav");
  await expect(row(app, "history", ids[1]!).getByTestId("v1-board-row-title")).toContainText("other.wav");

  // Read-only artifact view: format, size, integrity. No file name, no path.
  const first = row(app, "history", ids[0]!);
  await first.getByTestId("v1-artifacts-load").click();
  await expect(first.getByTestId("v1-artifacts")).toHaveAttribute("data-state", "available");
  await expect(first.locator('[data-format="srt"]')).toHaveAttribute("data-integrity", "verified");
  const text = (await first.textContent()) ?? "";
  expect(text).not.toContain(ROOT);
  expect(text).not.toContain(".srt");
  const artifacts = await api<{ state: string; items: { format: string; integrity: string }[]; unexpected_count: number }>(request, `/api/v1/jobs/${ids[0]}/artifacts`);
  expect(artifacts).toMatchObject({ state: "available", unexpected_count: 0, items: [{ format: "srt", integrity: "verified" }] });
  expect(JSON.stringify(artifacts)).not.toContain(ROOT);
  await shot(app, "s5b-batch-board");
});

test("hide and show: the row leaves and returns; the job, its state and its outputs are untouched; nothing is deleted", async ({ app, request }) => {
  const seen = recordApi(app);
  await expert(app, request, ["meeting.wav"]);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const job = await api<{ revision: number; status: string }>(request, `/api/v1/jobs/${id}`);
  // The auxiliary list was read before the hide (Windows acceptance P3: it must not stay stale).
  await loadRecent(app);
  await expect(recentItem(app, id!)).toHaveCount(1);

  await row(app, "history", id!).getByTestId("v1-board-hide").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "done");
  await expect(row(app, "history", id!)).toHaveCount(0);
  await expect(recentItem(app, id!)).toHaveCount(0);
  const visible = await api<{ items: { id: string }[] }>(request, "/api/v1/jobs");
  expect(visible.items.map((j) => j.id)).not.toContain(id);
  // Still there by id, same state and revision; the output directory is still verified.
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ revision: job.revision, status: "succeeded" });
  expect(await api(request, `/api/v1/jobs/${id}/artifacts`)).toMatchObject({ state: "available", items: [{ format: "srt", integrity: "verified" }] });
  expect(await api(request, `/api/v1/jobs/${id}/visibility`)).toMatchObject({ hidden: true, revision: 1 });

  await app.getByTestId("v1-board-action-close").click();
  await app.getByTestId("v1-board-show-hidden").locator("input").check();
  await expect(row(app, "hidden", id!)).toBeVisible();
  await row(app, "hidden", id!).getByTestId("v1-board-show").click();
  await expect(row(app, "history", id!)).toBeVisible();
  await expect(row(app, "hidden", id!)).toHaveCount(0);
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

test("retry is the same row: cancel a running job, retry it, the row returns to the queue with two attempts", async ({ app, request }) => {
  const seen = recordApi(app);
  await expert(app, request, ["slow-meeting.wav"]);
  const [id] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await expect(row(app, "queue", id!)).toHaveAttribute("data-status", "running", { timeout: 30_000 });
  await row(app, "queue", id!).getByTestId("v1-board-cancel").click();
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await app.getByTestId("v1-board-action-close").click();
  const cancelled = await api<{ revision: number }>(request, `/api/v1/jobs/${id}`);

  await row(app, "history", id!).getByTestId("v1-board-retry").click();
  // Same row key (the first internal job), now with two attempts, back in the queue.
  await expect(row(app, "queue", id!)).toHaveAttribute("data-attempts", "2", { timeout: 20_000 });
  await expect(row(app, "history", id!)).toHaveCount(0);
  // The newest internal job of the row is the retry, linked to the job it retries.
  const internal = ((await row(app, "queue", id!).textContent()) ?? "").match(/job_[0-9a-f]{32}/)![0];
  expect(internal).not.toBe(id);
  expect(await api(request, `/api/v1/jobs/${internal}`)).toMatchObject({ retry_of: id });
  // B7 contract kept: the retried job itself is unchanged, the retry is a new internal job.
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ revision: cancelled.revision, status: "cancelled" });
  const retryPost = seen.find((r) => line(r) === `POST /api/v1/jobs/${id}/retry`)!;
  expect((await retryPost.allHeaders())["if-match"]).toBe(`"${cancelled.revision}"`);

  // Clean up the slow job so the single slot is free for later specs.
  await app.getByTestId("v1-board-action-close").click();
  await expect(row(app, "queue", id!)).toHaveAttribute("data-status", "running", { timeout: 30_000 });
  await row(app, "queue", id!).getByTestId("v1-board-cancel").click();
  await expect(row(app, "history", id!)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await expect(row(app, "history", id!)).toHaveAttribute("data-attempts", "2");

  // Windows acceptance P3: hiding a finished retry chain removes *both* internal jobs from the
  // auxiliary list that was read before, and showing it brings both back. Nothing is deleted.
  await app.getByTestId("v1-board-action-close").click();
  const chain = [id!, internal];
  const before = await Promise.all(chain.map((job) => api<{ revision: number; status: string }>(request, `/api/v1/jobs/${job}`)));
  await loadRecent(app);
  for (const job of chain) await expect(recentItem(app, job)).toHaveCount(1);
  await row(app, "history", id!).getByTestId("v1-board-hide").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "done");
  await expect(row(app, "history", id!)).toHaveCount(0);
  for (const job of chain) await expect(recentItem(app, job)).toHaveCount(0);
  const visible = await api<{ items: { id: string }[] }>(request, "/api/v1/jobs");
  for (const job of chain) expect(visible.items.map((j) => j.id)).not.toContain(job);

  await app.getByTestId("v1-board-action-close").click();
  await app.getByTestId("v1-board-show-hidden").locator("input").check();
  await row(app, "hidden", id!).getByTestId("v1-board-show").click();
  await expect(app.getByTestId("v1-board-action")).toHaveAttribute("data-action-phase", "done");
  await expect(row(app, "history", id!)).toHaveAttribute("data-attempts", "2");
  for (const job of chain) await expect(recentItem(app, job)).toHaveCount(1);
  // Same job records as before (state and revision), four visibility PUTs, no DELETE.
  for (const [index, job] of chain.entries()) expect(await api(request, `/api/v1/jobs/${job}`)).toMatchObject(before[index]!);
  expect(seen.filter((r) => r.method() === "PUT" && line(r).endsWith("/visibility")).map(line).sort()).toEqual(chain.flatMap((job) => [`PUT /api/v1/jobs/${job}/visibility`, `PUT /api/v1/jobs/${job}/visibility`]).sort());
  expect(seen.filter((r) => r.method() === "DELETE")).toEqual([]);
  expect(legacyWrites(seen)).toEqual([]);
});

test("PR #53 P2-1: a retried row is placed where B7 queued its new job, after the job that was already waiting", async ({ app, request }) => {
  const seen = recordApi(app);
  // slow-meeting occupies the slot; cancel it (A = cancelled), keep meeting.wav (B) waiting behind a second slow job.
  await expert(app, request, ["slow-meeting.wav"]);
  const [a] = await submitBatch(app, seen);
  await ensureQueueRunning(app);
  await expect(row(app, "queue", a!)).toHaveAttribute("data-status", "running", { timeout: 30_000 });
  await row(app, "queue", a!).getByTestId("v1-board-cancel").click();
  await expect(row(app, "history", a!)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await app.getByTestId("v1-board-action-close").click();

  // Pause dispatch through the real API so the order below is stable, then queue B.
  const queue = await api<{ revision: number }>(request, "/api/v1/queue");
  const paused = await request.patch("/api/v1/queue/control", {
    headers: { "x-utteran-session": SESSION, "If-Match": `"${queue.revision}"`, "Idempotency-Key": `s5b-pause-${a!.slice(4, 24)}` },
    data: { schema_version: 1, dispatch: "paused" },
  });
  expect(paused.status()).toBe(200);
  await app.getByTestId("tx-list-select-none").click();
  await addInput(app, input("meeting.wav"));
  await app.getByTestId("tx-list-check").last().locator("input").check();
  await expect(app.getByTestId("v1-batch-count")).toContainText("1");
  const [b] = await submitBatch(app, seen);
  await expect(row(app, "queue", b!)).toHaveAttribute("data-status", "queued");

  // Retry A: B7 queues the new internal job *behind* B. The row of A must not be shown before B.
  await row(app, "history", a!).getByTestId("v1-board-retry").click();
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
  await row(app, "queue", b!).getByTestId("v1-board-cancel").click();
  await expect(row(app, "history", b!)).toHaveAttribute("data-status", "cancelled", { timeout: 20_000 });
  await app.getByTestId("v1-board-action-close").click();
  await row(app, "queue", a!).getByTestId("v1-board-cancel").click();
  await expect(row(app, "history", a!)).toHaveAttribute("data-status", "cancelled", { timeout: 20_000 });
  const again = await api<{ revision: number }>(request, "/api/v1/queue");
  const resumed = await request.patch("/api/v1/queue/control", {
    headers: { "x-utteran-session": SESSION, "If-Match": `"${again.revision}"`, "Idempotency-Key": `s5b-resume-${a!.slice(4, 24)}` },
    data: { schema_version: 1, dispatch: "running" },
  });
  expect(resumed.status()).toBe(200);
  expect(legacyWrites(seen)).toEqual([]);
});
