import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION, shot, test } from "./fixtures";

/**
 * Bundle D1 in the `--next-ui-v1` shape (real B5/B7 on the real loopback authority, synthetic B4
 * evidence and runner — tools/ui2/synthetic_v1.py). The shell (pages, modes, sidebar rail) must
 * not send, resend, cancel or delete anything, must keep the checked / accepted / unknown states
 * of the one set of v1 controllers, and must not multiply the board's polling.
 * Runs after the other v1 specs on the same data root (workers: 1, file order); every assertion is
 * scoped to the requests of this page. Headless Chromium — NOT a WebView2 acceptance.
 */

const ROOT = process.env.UI2_V1_ROOT!;
const input = (name: string) => `${ROOT}/in/${name}`;
const OUT = `${ROOT}/out`;
const AUTH = { Cookie: `utteran_gui_session=${SESSION}` };
const PRESET = "S5-B 合成";
const POLICY = "S5-B CPU";
const EXPERT = ["transcribe", "queue", "results", "models", "setup", "settings"];

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const writes = (seen: Request[]) => seen.filter((r) => r.method() !== "GET").map(line);
const legacyCalls = (seen: Request[]) => seen.map(line).filter((l) => !l.includes(" /api/v1/") && !l.endsWith(" /api/settings") && !l.includes(" /api/environment") && !l.includes(" /api/token") && !l.includes(" /api/setup") && !l.includes(" /api/logs"));

async function api<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path, { headers: AUTH });
  expect(response.status(), path).toBe(200);
  return (await response.json()) as T;
}

/** Use the S5-B Preset/Policy, creating them once (explicitly) when they do not exist yet. */
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

async function addInput(page: Page, path: string): Promise<void> {
  await page.getByTestId("tx-input-path").locator("input").fill(path);
  await page.getByTestId("tx-input-add-typed").click();
}

/** Every page of both modes, the sidebar rail both ways, and back to Normal transcription. */
async function walkTheShell(page: Page): Promise<void> {
  await page.getByTestId("nav-settings").click();
  await page.getByTestId("nav-setup").click();
  await page.getByTestId("mode-expert").click();
  for (const id of EXPERT) await page.getByTestId(`nav-${id}`).click();
  await page.getByTestId("nav-toggle").click();
  for (const id of EXPERT) await page.getByTestId(`nav-${id}`).click();
  await page.getByTestId("nav-toggle").click();
  await page.getByTestId("mode-normal").click();
  await page.getByTestId("nav-transcribe").click();
  await expect(page.getByTestId("content")).toHaveAttribute("data-section", "transcribe");
}

test("D1-04: a checked job and an accepted job keep their state across every page and mode; no extra POST, no cancel, no DELETE", async ({ app, request }) => {
  const seen = recordApi(app);
  await expect(app.getByTestId("preview-notice")).toHaveAttribute("data-notice", "app.previewNoticeV1");
  await library(app, request);
  await addInput(app, input("meeting.wav"));
  await app.getByTestId("v1-output-path").locator("input").fill(OUT);
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-validated")).toBeVisible();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  const afterCheck = writes(seen);

  await walkTheShell(app);
  // The check result is the same one (no new validation/resolution was requested).
  await expect(app.getByTestId("v1-validated")).toBeVisible();
  await expect(app.getByTestId("v1-submit")).toBeEnabled();
  expect(writes(seen)).toEqual(afterCheck);

  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  const afterSubmit = writes(seen);
  expect(afterSubmit.filter((l) => l === "POST /api/v1/jobs")).toHaveLength(1);

  await walkTheShell(app);
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  await app.waitForTimeout(1500);
  // No resend, no implicit cancel, no deletion, and nothing on the legacy start/cancel routes.
  expect(writes(seen)).toEqual(afterSubmit);
  expect(writes(seen).some((l) => l.startsWith("DELETE ") || l.includes("/cancel") || l.includes("/deletion"))).toBe(false);
  expect(legacyCalls(seen)).toEqual([]);
  await shot(app, "d1-v1-normal-accepted");
});

test("D1-04: an unknown submit outcome survives every switch; nothing is resent until the explicit re-check (same key and body)", async ({ app, request }) => {
  const seen = recordApi(app);
  await library(app, request);
  await addInput(app, input("other.wav"));
  await app.getByTestId("v1-output-path").locator("input").fill(OUT);
  let first = true;
  await app.route("**/api/v1/jobs", async (route) => {
    if (route.request().method() !== "POST" || !first) return route.continue();
    first = false;
    await route.fetch(); // the real server applies it…
    await route.fulfill({ status: 500, body: "" }); // …but the answer is lost
  });
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-unknown")).toBeVisible();
  const posts = () => seen.filter((r) => line(r) === "POST /api/v1/jobs");
  expect(posts()).toHaveLength(1);

  await walkTheShell(app);
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-transcribe").click();
  await app.getByTestId("mode-normal").click();
  await expect(app.getByTestId("v1-unknown")).toBeVisible();
  await expect(app.getByTestId("v1-check")).toBeDisabled();
  await app.waitForTimeout(2000);
  expect(posts()).toHaveLength(1);

  await app.getByTestId("v1-recheck").click();
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  expect(posts()).toHaveLength(2);
  const [a, b] = await Promise.all(posts().map((r) => r.allHeaders()));
  expect(b!["idempotency-key"]).toBe(a!["idempotency-key"]);
  expect(posts()[1]!.postData()).toBe(posts()[0]!.postData());
});

test("D1-04: leaving and re-entering the Expert page does not multiply the board's reads", async ({ app }) => {
  const seen = recordApi(app);
  const boardReads = () => seen.filter((r) => line(r) === "GET /api/v1/queue").length;
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("v1-board")).toBeVisible();
  await app.waitForTimeout(1500);
  let mark = boardReads();
  await app.waitForTimeout(6000);
  const baseline = boardReads() - mark;
  // Ten unmount/mount cycles of the board (page switch and mode switch).
  for (let i = 0; i < 5; i += 1) {
    await app.getByTestId("nav-queue").click();
    await app.getByTestId("nav-transcribe").click();
    await app.getByTestId("mode-normal").click();
    await app.getByTestId("mode-expert").click();
  }
  await expect(app.getByTestId("v1-board")).toBeVisible();
  await app.waitForTimeout(1500);
  mark = boardReads();
  await app.waitForTimeout(6000);
  const after = boardReads() - mark;
  // One watcher polls at most every 2 s (BOARD_POLL_MS): leaked watchers would multiply this.
  expect(after).toBeLessThanOrEqual(Math.max(baseline, 3) + 1);
  // Leaving the page stops it: nothing is read while Normal settings is shown.
  await app.getByTestId("mode-normal").click();
  await app.getByTestId("nav-settings").click();
  await app.waitForTimeout(1000);
  mark = boardReads();
  await app.waitForTimeout(4500);
  expect(boardReads() - mark).toBe(0);
});
