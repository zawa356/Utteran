import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION, shot, test } from "./fixtures";

/**
 * Bundle C2c against the real FastAPI app in the `--next-ui-v1` shape (layout v8): the history-only
 * deletion of one internal job, and the outputs deletion, which is refused on every platform. Synthetic only (tools/ui2/synthetic_v1.py writes one .srt into the
 * job's own directory under the run's temporary data root). Headless Chromium on Linux — NOT a
 * WebView2 acceptance, and NOT evidence that deleting files is safe on Windows (refused on every platform).
 *
 * Runs after the S5-A/S5-B specs on the same data root; every assertion is scoped to the jobs this
 * spec creates. The test reads the synthetic output folder directly to prove what was (not) deleted.
 */

const ROOT = process.env.UI2_V1_ROOT!;
const input = (name: string) => `${ROOT}/in/${name}`;
const OUT = `${ROOT}/out`;
const AUTH = { Cookie: `utteran_gui_session=${SESSION}` };
const HEADERS = { "x-utteran-session": SESSION };
const PRESET = "S5-C 合成";
const POLICY = "S5-C CPU";

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const deletions = (seen: Request[]) => seen.filter((r) => line(r).endsWith("/deletions")).map(line);

async function api<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path, { headers: AUTH });
  expect(response.status(), path).toBe(200);
  return (await response.json()) as T;
}

/** The job's own output directory (its marker names the job), or null. Read-only. */
function ownDirectory(jobId: string): string | null {
  for (const name of readdirSync(OUT)) {
    const marker = join(OUT, name, ".utteran-job");
    if (existsSync(marker) && readFileSync(marker, "utf8").trim() === jobId) return join(OUT, name);
  }
  return null;
}

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

async function ensureQueueRunning(page: Page): Promise<void> {
  const dispatch = page.getByTestId("v1-queue-dispatch");
  await expect(dispatch).toBeVisible();
  if (/停止中/.test((await dispatch.textContent()) ?? "")) {
    await page.getByTestId("v1-queue-resume").click();
    await expect(dispatch).toContainText(/実行中/);
  }
}

/** One finished job for `name`, submitted through the Expert batch of this page. */
async function finishedJob(page: Page, request: APIRequestContext, seen: Request[], name: string): Promise<string> {
  await page.getByTestId("mode-expert").click();
  await library(page, request);
  await page.getByTestId("v1-output-path").locator("input").fill(OUT);
  // Only the file added now is selected (earlier rows of this page stay unselected).
  const none = page.getByTestId("tx-list-select-none");
  if ((await none.count()) > 0 && (await none.isEnabled())) await none.click();
  await page.getByTestId("tx-input-path").locator("input").fill(input(name));
  await page.getByTestId("tx-input-add-typed").click();
  await page.getByTestId("tx-list-check").last().locator("input").check();
  await expect(page.getByTestId("v1-batch-count")).toContainText("1");
  await page.getByTestId("v1-batch-check").click();
  await expect(page.getByTestId("v1-batch-resolved")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("v1-batch-submit").click();
  await expect(page.getByTestId("v1-batch-accepted")).toBeVisible({ timeout: 20_000 });
  const post = seen.filter((r) => line(r) === "POST /api/v1/jobs").at(-1)!;
  const [id] = ((await (await post.response())!.json()) as { jobs: { id: string }[] }).jobs.map((j) => j.id);
  await ensureQueueRunning(page);
  await openHistory(page);
  await expect(row(page, id!)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  return id!;
}

async function openHistory(page: Page): Promise<void> {
  await page.getByTestId("nav-results").click();
  await page.getByTestId("results-tab-history").click();
}

const row = (page: Page, id: string) => page.getByTestId("v1-board-history").locator(`[data-testid="v1-board-row"][data-root="${id}"]`);
const dialog = (page: Page) => page.getByTestId("v1-delete-card");

test("history only: two-step confirmation, cancel is the default, the files stay and the job is 410", async ({ app, request }) => {
  const seen = recordApi(app);
  const id = await finishedJob(app, request, seen, "meeting.wav");
  const directory = ownDirectory(id);
  expect(directory).not.toBeNull();
  const before = readdirSync(directory!).sort();

  await row(app, id).getByTestId("results-select").click();
  // Open the history-only dialog: a read-only plan, cancel focused, no deletion sent.
  await app.getByTestId("v1-board-delete-history").click();
  await expect(dialog(app)).toHaveAttribute("data-mode", "history");
  await expect(dialog(app)).toHaveAttribute("data-phase", "planned");
  await expect(app.getByTestId("v1-delete-cancel")).toBeFocused();
  await expect(app.getByTestId("v1-delete-target")).toContainText(id);
  await expect(app.getByTestId("v1-delete-retained").locator('[data-code="backups"]')).toBeVisible();
  await expect(app.getByTestId("v1-delete-retained").locator('[data-code="output_files"]')).toBeVisible();
  const text = (await dialog(app).textContent()) ?? "";
  expect(text).not.toContain(ROOT);
  expect(text).not.toMatch(/完全(に)?消去しました|完全削除しました/);
  await app.getByTestId("v1-delete-cancel").click();
  await expect(app.getByTestId("v1-delete-dialog")).toHaveCount(0);
  expect(deletions(seen)).toEqual([]);
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ id, status: "succeeded" });

  // Explicit approval: exactly one deletion request with If-Match and a key; then the job is gone.
  await app.getByTestId("v1-board-delete-history").click();
  await expect(dialog(app)).toHaveAttribute("data-phase", "planned");
  await app.getByTestId("v1-delete-confirm").click();
  await expect(app.getByTestId("v1-delete-outcome")).toHaveAttribute("data-outcome", "done");
  expect(deletions(seen)).toEqual([`POST /api/v1/jobs/${id}/deletions`]);
  const sent = seen.find((r) => line(r).endsWith("/deletions"))!;
  const headers = await sent.allHeaders();
  expect(headers["if-match"]).toMatch(/^"\d+"$/);
  expect(headers["idempotency-key"]).toBeTruthy();
  await shot(app, "s5c-history-deleted");
  await app.getByTestId("v1-delete-cancel").click();
  await expect(row(app, id)).toHaveCount(0);
  const gone = await request.get(`/api/v1/jobs/${id}`, { headers: AUTH });
  expect(gone.status()).toBe(410);
  expect(((await gone.json()) as { error: { code: string } }).error.code).toBe("job_deleted");
  // ③ touches no file: the output directory and the input are still there, unchanged.
  expect(readdirSync(directory!).sort()).toEqual(before);
  expect(existsSync(input("meeting.wav"))).toBe(true);
});

test("history and outputs is disabled on every platform: the plan is refused, approval impossible, nothing written", async ({ app, request }) => {
  // PR #58 owner review: a removal of exactly the verified files cannot be proven, so ④ is refused.
  const seen = recordApi(app);
  const id = await finishedJob(app, request, seen, "other.wav");
  const directory = ownDirectory(id)!;
  writeFileSync(join(directory, "notes-by-user.txt"), "mine"); // any file state: the same refusal
  const before = readdirSync(directory).sort().map((name) => [name, readFileSync(join(directory, name), "utf8")]);

  await row(app, id).getByTestId("results-select").click();
  await expect(app.getByTestId("v1-delete-refusal")).toHaveAttribute("data-refusal", "artifact_deletion_disabled");
  await expect(app.getByTestId("v1-board-delete-artifacts")).toHaveCount(0);
  await expect(app.getByTestId("v1-delete-confirm")).toHaveCount(0);
  const deniedPlan = await request.post(`/api/v1/jobs/${id}/deletion-plans`, {
    headers: HEADERS,
    data: { schema_version: 1, mode: "history_and_artifacts" },
  });
  expect(deniedPlan.status()).toBe(200);
  expect(await deniedPlan.json()).toMatchObject({ allowed: false, refusal: "artifact_deletion_disabled" });
  await shot(app, "s5c-artifacts-disabled");
  expect(deletions(seen)).toEqual([]);
  // The server refuses an execution too (409, fixed code), whatever token is sent.
  const job = await api<{ revision: number }>(request, `/api/v1/jobs/${id}`);
  const forced = await request.post(`/api/v1/jobs/${id}/deletions`, {
    headers: { ...HEADERS, "If-Match": `"${job.revision}"`, "Idempotency-Key": `s5c-art-${id.slice(4, 24)}` },
    data: { schema_version: 1, mode: "history_and_artifacts", plan_token: `dp1.${"9".repeat(13)}.${"a".repeat(64)}` },
  });
  expect(forced.status()).toBe(409);
  expect(((await forced.json()) as { error: { code: string } }).error.code).toBe("artifact_deletion_disabled");
  expect(readdirSync(directory).sort().map((name) => [name, readFileSync(join(directory, name), "utf8")])).toEqual(before);
  expect(existsSync(input("other.wav"))).toBe(true);
  expect(await api(request, `/api/v1/jobs/${id}`)).toMatchObject({ status: "succeeded", revision: job.revision });

  // History-only stays possible for the same job.
  await app.getByTestId("v1-board-delete-history").click();
  await expect(dialog(app)).toHaveAttribute("data-phase", "planned");
  await expect(app.getByTestId("v1-delete-confirm")).toBeEnabled();
  await app.getByTestId("v1-delete-cancel").click();
});

test("a retry chain: the person picks one internal job; the other attempt is not deleted", async ({ app, request }) => {
  const seen = recordApi(app);
  // Build a finished chain of two cancelled attempts without running anything: pause, submit, cancel, retry, cancel.
  const queue = await api<{ revision: number }>(request, "/api/v1/queue");
  const pause = await request.patch("/api/v1/queue/control", {
    headers: { ...HEADERS, "If-Match": `"${queue.revision}"`, "Idempotency-Key": `s5c-pause-${Date.now()}-key` },
    data: { schema_version: 1, dispatch: "paused" },
  });
  expect(pause.status()).toBe(200);
  await app.getByTestId("mode-expert").click();
  await library(app, request);
  await app.getByTestId("v1-output-path").locator("input").fill(OUT);
  await app.getByTestId("tx-input-path").locator("input").fill(input("meeting.wav"));
  await app.getByTestId("tx-input-add-typed").click();
  await app.getByTestId("tx-list-select-none").click();
  await app.getByTestId("tx-list-check").last().locator("input").check();
  await app.getByTestId("v1-batch-check").click();
  await expect(app.getByTestId("v1-batch-resolved")).toBeVisible({ timeout: 20_000 });
  await app.getByTestId("v1-batch-submit").click();
  await expect(app.getByTestId("v1-batch-accepted")).toBeVisible({ timeout: 20_000 });
  const post = seen.filter((r) => line(r) === "POST /api/v1/jobs").at(-1)!;
  const [root] = ((await (await post.response())!.json()) as { jobs: { id: string }[] }).jobs.map((j) => j.id);
  const cancel = async (job: string, key: string) => {
    const current = await api<{ revision: number }>(request, `/api/v1/jobs/${job}`);
    const response = await request.post(`/api/v1/jobs/${job}/cancel`, { headers: { ...HEADERS, "If-Match": `"${current.revision}"`, "Idempotency-Key": key }, data: { schema_version: 1, mode: "cooperative" } });
    expect(response.status()).toBe(202);
  };
  await cancel(root!, `s5c-cancel-${root!.slice(4, 24)}`);
  const cancelled = await api<{ revision: number }>(request, `/api/v1/jobs/${root}`);
  const retried = await request.post(`/api/v1/jobs/${root}/retry`, { headers: { ...HEADERS, "If-Match": `"${cancelled.revision}"`, "Idempotency-Key": `s5c-retry-${root!.slice(4, 24)}` }, data: { schema_version: 1 } });
  expect(retried.status()).toBe(202);
  const child = ((await retried.json()) as { id: string }).id;
  await cancel(child, `s5c-cancel-${child.slice(4, 24)}`);
  const again = await api<{ revision: number }>(request, "/api/v1/queue");
  await request.patch("/api/v1/queue/control", { headers: { ...HEADERS, "If-Match": `"${again.revision}"`, "Idempotency-Key": `s5c-resume-${Date.now()}-key` }, data: { schema_version: 1, dispatch: "running" } });

  const hidden = await request.put(`/api/v1/jobs/${root}/visibility`, {
    headers: { ...HEADERS, "If-Match": '"0"' },
    data: { schema_version: 1, hidden: true },
  });
  expect(hidden.status()).toBe(200);
  await app.getByTestId("nav-results").click();
  await expect(app.getByTestId("results-table-results").locator(`[data-root="${root}"]`)).toHaveCount(0);
  await openHistory(app);
  await expect(row(app, root!)).toHaveAttribute("data-attempts", "1");
  await app.getByTestId("v1-board-show-hidden").locator("input").check();
  const hiddenRow = app.getByTestId("v1-board-hidden").locator(`[data-root="${root}"]`);
  await expect(hiddenRow).toHaveAttribute("data-job", root!);
  await hiddenRow.getByTestId("results-select").click();
  await app.getByTestId("v1-board-show").click();
  await expect(hiddenRow).toHaveCount(0);
  await app.getByTestId("v1-board-show-hidden").locator("input").uncheck();
  await expect(row(app, root!)).toHaveAttribute("data-attempts", "2", { timeout: 20_000 });
  await row(app, root!).getByTestId("results-select").click();
  await app.locator(`[data-testid="results-attempt"][data-job="${root}"]`).getByTestId("v1-board-delete-history").click();
  await expect(dialog(app)).toHaveAttribute("data-job", root!);
  await expect(dialog(app)).toHaveAttribute("data-phase", "planned");
  await expect(app.getByTestId("v1-delete-related")).toBeVisible();
  await app.getByTestId("v1-delete-confirm").click();
  await expect(app.getByTestId("v1-delete-outcome")).toHaveAttribute("data-outcome", "done");
  await app.getByTestId("v1-delete-cancel").click();
  expect(deletions(seen)).toEqual([`POST /api/v1/jobs/${root}/deletions`]);
  expect((await request.get(`/api/v1/jobs/${root}`, { headers: AUTH })).status()).toBe(410);
  // The other attempt is untouched and still points at the deleted one.
  expect(await api(request, `/api/v1/jobs/${child}`)).toMatchObject({ id: child, status: "cancelled", retry_of: root });
  await expect(app.getByTestId("v1-board-history").locator(`[data-testid="v1-board-row"][data-job="${child}"]`)).toHaveCount(1);
});

test("D3-A: results and history use one board, and filtering and mode changes only read", async ({ app, request }) => {
  const seen = recordApi(app);
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-results").click();
  const results = app.getByTestId("section-results");
  await expect(results.getByTestId("results-table")).toBeVisible();
  const before = await api<{ items: unknown[] }>(request, "/api/v1/queue");
  await results.getByTestId("results-tab-history").click();
  await results.getByTestId("history-search").locator("input").fill("absent-synthetic-job");
  await expect(results.getByTestId("results-empty")).toBeVisible();
  await results.getByTestId("results-tab-results").click();
  await expect(results.getByTestId("history-search")).toHaveCount(0);
  await results.getByTestId("results-tab-history").click();
  await expect(results.getByTestId("history-search").locator("input")).toHaveValue("absent-synthetic-job");
  await app.getByTestId("mode-normal").click();
  await expect(app.getByTestId("nav-results")).toHaveCount(0);
  await app.getByTestId("mode-expert").click();
  await expect(results).toBeVisible();
  expect((await api<{ items: unknown[] }>(request, "/api/v1/queue")).items).toEqual(before.items);
  expect(seen.filter(r => !["GET", "HEAD"].includes(r.method())).map(line)).toEqual([]);
});

test("D3-A: a failing later page clears selection; reload recovers; search applies after IME composition", async ({ app, request }) => {
  const seen = recordApi(app);
  const id = await finishedJob(app, request, seen, "meeting.wav");
  const search = app.getByTestId("history-search").locator("input");
  const pageRow = row(app, id);
  await search.fill("meeting.wav");
  await pageRow.getByTestId("results-select").press("Enter");
  await expect(app.getByTestId("results-detail")).toHaveAttribute("data-root", id);
  await search.evaluate((el: HTMLInputElement) => {
    el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    el.value = "未確定";
    el.dispatchEvent(new InputEvent("input", { bubbles: true, isComposing: true }));
    const enter = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Enter", isComposing: true });
    if (!el.dispatchEvent(enter)) throw new Error("composition confirmation was cancelled");
  });
  await expect(pageRow).toBeVisible();
  await search.dispatchEvent("compositionend");
  await expect(app.getByTestId("results-empty")).toBeVisible();
  await expect(app.getByTestId("results-detail")).toHaveCount(0);

  await search.fill(id);
  await pageRow.getByTestId("results-select").click();
  const before = seen.length;
  const jobs = /\/api\/v1\/jobs\?/;
  await app.route(jobs, async route => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/v1/jobs") return route.continue();
    if (url.searchParams.has("cursor")) return route.abort("internetdisconnected");
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      json: { ...body, next_cursor: `j2.${"a".repeat(32)}.100.100.${"f".repeat(64)}` },
    });
  });
  await app.getByTestId("v1-board-refresh").click();
  await expect(app.getByTestId("v1-board-error")).toHaveAttribute("data-error-kind", "network");
  await expect(app.getByTestId("results-table")).toHaveCount(0);
  await expect(app.getByTestId("results-detail")).toHaveCount(0);
  await app.unroute(jobs);
  await app.getByTestId("v1-board-refresh").click();
  await expect(pageRow).toBeVisible();
  await expect(app.getByTestId("results-detail")).toHaveCount(0);
  expect(seen.slice(before).filter(r => r.method() !== "GET").map(line)).toEqual([]);
});

for (const language of ["ja", "en"]) {
  for (const theme of ["light", "dark"]) {
    test(`D3A-13: 900/1100/1280px ${language} ${theme}: results and confirmation fit`, async ({ app, request }) => {
      const seen = recordApi(app);
      const id = await finishedJob(app, request, seen, "meeting.wav");
      const settings = await request.patch("/api/settings", { headers: HEADERS, data: { language, theme } });
      expect(settings.status()).toBe(200);
      await app.reload();
      await expect(app.getByTestId("app-ready")).toBeVisible();
      await app.getByTestId("mode-expert").click();
      await openHistory(app);
      const select = row(app, id).getByTestId("results-select");
      await expect(select).toContainText(language === "ja" ? "ファイル名不明" : "Unknown file name");
      await select.press("Enter");
      await expect(app.getByTestId("results-detail")).toBeVisible();
      const overflow = () => app.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      for (const width of [900, 1100, 1280]) {
        await app.setViewportSize({ width, height: 820 });
        expect(await overflow()).toBeLessThanOrEqual(0);
        const table = (await app.getByTestId("results-table").boundingBox())!;
        const detail = (await app.getByTestId("results-detail").boundingBox())!;
        expect(detail.x).toBeGreaterThanOrEqual(table.x + table.width - 1);
        await app.getByTestId("v1-board-delete-history").click();
        await expect(dialog(app)).toHaveAttribute("data-phase", "planned");
        await expect(app.getByTestId("v1-delete-cancel")).toBeFocused();
        await expect(dialog(app)).toHaveCSS("background-color", theme === "dark" ? "rgb(30, 33, 38)" : "rgb(255, 255, 255)");
        expect(await overflow()).toBeLessThanOrEqual(0);
        await app.getByTestId("v1-delete-cancel").press("Enter");
        await expect(app.getByTestId("v1-board-delete-history")).toBeFocused();
      }
      expect(deletions(seen)).toEqual([]);
    });
  }
}
