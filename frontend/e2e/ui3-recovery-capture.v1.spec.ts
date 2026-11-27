import { mkdirSync, readFileSync } from "node:fs";
import type { APIRequestContext, Page } from "@playwright/test";
import { choose, expect, fillOutput, revealLibrary, SESSION } from "./fixtures";
import { test } from "./isolatedV1";

/**
 * UI-3 #96 design recovery: the product half of the before/after comparison with the Design Freeze
 * prototype (e2e-preview/recovery-capture.spec.ts takes the same states there). Only runs with
 * `UI3_RECOVERY_SHOTS=<folder>` (e.g. `before`, `after`); otherwise every test is skipped.
 * Synthetic store, inputs and runner only (tools/ui2/serve_next_ui.py --v1). Headless Chromium,
 * NOT a WebView2 acceptance. Images go to frontend/ui3-recovery/<folder>/ (git-ignored, not committed).
 */
const folder = process.env.UI3_RECOVERY_SHOTS ?? "";
test.skip(folder === "", "UI3_RECOVERY_SHOTS is not set");
const dir = `ui3-recovery/${folder}`;

async function capture(page: Page, name: string): Promise<void> {
  mkdirSync(dir, { recursive: true });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${dir}/product-${name}.png`, animations: "disabled", fullPage: true });
}

async function seed(request: APIRequestContext, url: string): Promise<void> {
  const auth = { Cookie: `utteran_gui_session=${SESSION}`, Origin: url };
  const settings = JSON.parse(readFileSync(new URL("../src/domain/v1/presetTemplate.json", import.meta.url), "utf8"));
  const post = async (endpoint: string, data: object) => {
    const response = await request.post(`/api/v1/${endpoint}`, { headers: { ...auth, "Idempotency-Key": `ui3-recovery-${endpoint}` }, data });
    expect(response.status()).toBe(201);
  };
  await post("presets", { schema_version: 1, name: "Synthetic review", settings });
  await post("execution-policies", {
    schema_version: 1, name: "Synthetic CPU", mode: "auto", fixed_candidate_id: null,
    allowed_profile_ids: ["cpu"], objective: "balanced", max_memory_bytes: null,
    fallback: "none", execution_deadline_seconds: null,
  });
}

async function accent(page: Page): Promise<void> {
  await page.getByTestId("nav-settings").click();
  await choose(page, "settings-accent", /^(Green|緑)$/);
  await page.getByTestId("nav-transcribe").click();
}

async function prepare(page: Page, root: string, names: string[]): Promise<void> {
  // Before #96 the library was always shown; the helpers only reveal it when it is folded.
  if (await page.getByTestId("normal-details-toggle").count()) await revealLibrary(page);
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
  await choose(page, "v1-preset-select", /Synthetic review/);
  await choose(page, "v1-policy-select", /Synthetic CPU/);
  await fillOutput(page, `${root}/out`);
  for (const name of names) {
    await page.getByTestId("tx-input-path").locator("input").fill(`${root}/in/${name}`);
    await page.getByTestId("tx-input-add-typed").click();
  }
}

test("Normal: empty, ready, checked, accepted and finished", async ({ app, backend, request }) => {
  await capture(app, "normal-empty");
  await seed(request, backend.url);
  await app.reload();
  await accent(app);
  await prepare(app, backend.root, ["meeting.wav"]);
  await capture(app, "normal-ready");
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await capture(app, "normal-checked");
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-status-card")).toBeVisible();
  await capture(app, "normal-accepted");
  const resume = app.getByTestId("v1-queue-resume");
  await expect(app.getByTestId("v1-queue-dispatch")).toBeVisible();
  if (await resume.isVisible()) await resume.click();
  await expect(app.getByTestId("v1-job-artifacts")).toBeVisible({ timeout: 20_000 });
  await capture(app, "normal-finished");
  await app.setViewportSize({ width: 640, height: 900 });
  await capture(app, "normal-narrow-640");
});

test("Normal: English dark", async ({ app, backend, request }) => {
  await seed(request, backend.url);
  const patch = await request.patch("/api/settings", { headers: { "x-utteran-session": SESSION }, data: { language: "en", theme: "dark" } });
  expect(patch.status()).toBe(200);
  await app.reload();
  await accent(app);
  await prepare(app, backend.root, ["meeting.wav"]);
  await capture(app, "normal-ready-en-dark");
});

test("Expert: common, selection and narrow", async ({ app, backend, request }) => {
  await seed(request, backend.url);
  await app.reload();
  await accent(app);
  await prepare(app, backend.root, []);
  await app.getByTestId("mode-expert").click();
  for (const name of ["meeting.wav", "other.wav", "third.wav"]) {
    await app.getByTestId("tx-input-path").locator("input").fill(`${backend.root}/in/${name}`);
    await app.getByTestId("tx-input-add-typed").click();
  }
  const common = app.getByTestId("inspector-tab-common");
  if (await common.count()) await common.click();
  await capture(app, "expert-common");
  for (const category of ["output", "advanced"]) {
    const button = app.getByTestId(`category-${category}`);
    if (!(await button.count())) continue;
    await button.click();
    await capture(app, `expert-common-${category}`);
  }
  if (await app.getByTestId("category-asr").count()) await app.getByTestId("category-asr").click();
  await app.getByTestId("tx-list-select-all").click();
  const selection = app.getByTestId("inspector-tab-selection");
  if (await selection.count()) await selection.click();
  await capture(app, "expert-selection");
  for (const category of ["prompt", "diarization", "output", "advanced"]) {
    const button = app.getByTestId(`category-${category}`);
    if (!(await button.count())) continue;
    await button.click();
    await capture(app, `expert-selection-${category}`);
  }
  await app.setViewportSize({ width: 1100, height: 760 });
  await capture(app, "expert-1100");
  await app.setViewportSize({ width: 640, height: 760 });
  await capture(app, "expert-640");
});

test("Setup page", async ({ app }) => {
  await app.getByTestId("nav-setup").click();
  await capture(app, "setup");
});
