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
  await expect(row(page, id!)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  return id!;
}

const row = (page: Page, id: string) => page.getByTestId("v1-board-history").locator(`[data-testid="v1-board-row"][data-root="${id}"]`);
const dialog = (page: Page) => page.getByTestId("v1-delete-card");

test("history only: two-step confirmation, cancel is the default, the files stay and the job is 410", async ({ app, request }) => {
  const seen = recordApi(app);
  const id = await finishedJob(app, request, seen, "meeting.wav");
  const directory = ownDirectory(id);
  expect(directory).not.toBeNull();
  const before = readdirSync(directory!).sort();

  // Open the history-only dialog: a read-only plan, cancel focused, no deletion sent.
  await row(app, id).getByTestId("v1-board-delete-history").click();
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
  await row(app, id).getByTestId("v1-board-delete-history").click();
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

  await row(app, id).getByTestId("v1-board-delete-artifacts").click();
  await expect(dialog(app)).toHaveAttribute("data-mode", "history_and_artifacts");
  await expect(dialog(app)).toHaveAttribute("data-phase", "planned");
  await expect(app.getByTestId("v1-delete-cancel")).toBeFocused();
  await expect(app.getByTestId("v1-delete-refusal")).toHaveAttribute("data-refusal", "artifact_deletion_disabled");
  await expect(app.getByTestId("v1-delete-confirm")).toBeDisabled();
  await expect(app.getByTestId("v1-delete-files")).toHaveCount(0);
  await expect(app.getByTestId("v1-delete-retained")).toHaveCount(0); // a refused plan deletes nothing
  await shot(app, "s5c-artifacts-disabled");
  await app.getByTestId("v1-delete-cancel").click();
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
  await row(app, id).getByTestId("v1-board-delete-history").click();
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

  await app.getByTestId("v1-board-refresh").click();
  await expect(row(app, root!)).toHaveAttribute("data-attempts", "2", { timeout: 20_000 });
  await row(app, root!).getByTestId("v1-board-delete-history").click();
  // Default target = the job the row shows (the newest); the person explicitly picks the first one.
  await expect(dialog(app)).toHaveAttribute("data-job", child);
  await app.locator(`[data-testid="v1-delete-option"][data-job="${root}"] input`).check();
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
