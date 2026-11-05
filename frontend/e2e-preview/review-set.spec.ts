import type { Page } from "@playwright/test";
import { choose, expect, horizontalOverflow, setup, shot, test } from "./fixtures";

/**
 * The owner review set (docs/architecture/ui3/review-and-acceptance.md §2). Each case asserts the
 * layout (no horizontal overflow, expected column count) and, with UI3_SCREENSHOTS=1, writes
 * test-results/ui3-screenshots/<name>.png. Synthetic data only.
 */
async function columns(page: Page): Promise<number> {
  return page.evaluate(() => {
    const grid = document.querySelector(".u-panes");
    return grid ? getComputedStyle(grid).gridTemplateColumns.split(" ").length : 0;
  });
}

async function runToMixedResult(page: Page): Promise<void> {
  await page.getByTestId("normal-start").click();
  await page.getByTestId("queue-step-all").click();
  await page.getByTestId("queue-step-all").click();
  await page.getByTestId("normal-fail-demo-job-003").click();
  for (let i = 0; i < 3; i += 1) await page.getByTestId("queue-step-all").click();
  // A late addition: one more input started after the others finished.
  await page.getByTestId("normal-add-file").click();
  await page.getByTestId("normal-start").click();
  await page.getByTestId("queue-step-all").click();
}

test("1 normal · ja · light · 1280×820: the whole input → result flow", async ({ preview }) => {
  await runToMixedResult(preview);
  await expect(preview.getByTestId("normal-retry-demo-job-003")).toBeVisible();
  await expect(preview.getByTestId("normal-result-list").locator(".v-list-item")).toHaveCount(3);
  await expect(preview.getByTestId("normal-job-demo-job-005")).toContainText("準備中");
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "1-normal-ja-light-1280");
});

test("2 normal · en · dark · 1280×820: long texts, theme and translation", async ({ preview }) => {
  await setup(preview, { lang: "en", theme: "dark", scenario: /^処理中/ });
  await preview.getByTestId("config-details").click();
  await expect(preview.getByTestId("config-route")).toBeVisible();
  await expect(preview.locator(".v-application")).toHaveClass(/v-theme--dark/);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "2-normal-en-dark-1280");
});

test("3 expert · ja · light · 1280×820: inputs / Inspector / queue in three panes", async ({ preview }) => {
  await setup(preview, { scenario: /^処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-02").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("queue-row-demo-job-003").click();
  expect(await columns(preview)).toBe(3);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "3-expert-ja-light-1280");
});

test("4 expert · ja · dark · 1100×760: two columns with the queue below", async ({ preview }) => {
  await preview.setViewportSize({ width: 1100, height: 760 });
  await setup(preview, { theme: "dark", scenario: /^処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("queue-row-demo-job-001").click();
  expect(await columns(preview)).toBe(2);
  await expect(preview.getByTestId("pane-queue")).toBeVisible();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "4-expert-ja-dark-1100");
});

test("5 expert · en · light · 900×660: one pane with a switcher, keyboard focus", async ({ preview }) => {
  await preview.setViewportSize({ width: 900, height: 660 });
  await setup(preview, { lang: "en" });
  await preview.getByTestId("mode-expert").click();
  expect(await columns(preview)).toBe(1);
  await preview.getByTestId("inspector-tab-common").focus();
  await preview.keyboard.press("F6");
  await expect(preview.getByTestId("pane-queue")).toBeFocused();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "5-expert-en-light-900x660");
});

test("6 states: empty / loading / error / success with retry and disabled reasons", async ({ preview }) => {
  await setup(preview, { scenario: /^空/ });
  await expect(preview.getByTestId("normal-start-blocked")).toBeVisible();
  await shot(preview, "6a-normal-empty");
  await setup(preview, { scenario: /^読み込み中/ });
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("expert-inputs-loading")).toBeVisible();
  await shot(preview, "6b-expert-loading");
  await setup(preview, { scenario: /^エラー/ });
  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("normal-inputs-error")).toBeVisible();
  await shot(preview, "6c-normal-error");
  await setup(preview, { scenario: /^すべて完了/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("queue-row-demo-job-001").click();
  await expect(preview.getByTestId("submit-selected")).toBeDisabled(); // every input already has a job
  await shot(preview, "6d-expert-success");
  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("normal-result-list").locator(".v-list-item")).toHaveCount(4);
  await shot(preview, "6e-normal-success");
});

// A 1920×1200 screen with a maximised window (≈1920×1160 DIP of client area) at OS scale 100–200 %.
// Viewport/DPR emulation in Chromium is NOT a Windows DPI acceptance (UI-4 #22).
const DPI = [
  { scale: 1, width: 1920, height: 1160, cols: 3 },
  { scale: 1.25, width: 1536, height: 928, cols: 3 },
  { scale: 1.5, width: 1280, height: 773, cols: 3 },
  { scale: 2, width: 960, height: 580, cols: 1 },
];
for (const d of DPI) {
  test(`DPI ${d.scale * 100}% (1920×1200 screen, ${d.width}×${d.height} CSS px): expert layout`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: d.width, height: d.height }, deviceScaleFactor: d.scale });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/preview.html");
    await expect(page.getByTestId("preview-ready")).toBeVisible();
    await choose(page, "toolbar-scenario", /^処理中/);
    await page.getByTestId("mode-expert").click();
    expect(await columns(page)).toBe(d.cols);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    await shot(page, `dpi-${d.scale * 100}-expert`);
    expect(errors).toEqual([]);
    await context.close();
  });
}
