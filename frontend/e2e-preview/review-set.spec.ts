import { choose, columns, drag, expect, horizontalOverflow, paneWidth, setup, shot, test } from "./fixtures";

/**
 * Owner review set for iteration 2 (docs/architecture/ui3/iteration-2.md §5). Each case asserts
 * the layout (no horizontal overflow, expected columns) and, with UI3_SCREENSHOTS=1, writes
 * test-results/ui3-screenshots/<name>.png. Synthetic and fictional data only.
 */

test("① Normal home · light · green · 1280×820", async ({ preview }) => {
  await expect(preview.getByTestId("sidebar")).toHaveCount(0);
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "01-normal-light-green-1280");
  await setup(preview, { scenario: /^1ファイル・完了/ });
  await expect(preview.getByTestId("normal-result")).toBeVisible();
  await shot(preview, "01b-normal-light-done-1280");
});

test("② Normal · dark · running", async ({ preview }) => {
  await setup(preview, { theme: "dark", scenario: /^1ファイル・実行中/ });
  await preview.getByTestId("config-details").click();
  await expect(preview.locator(".v-application")).toHaveClass(/v-theme--greenDark/);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "02-normal-dark-running-1280");
});

test("③ Expert · 1280×820 · three panes", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-02").click({ modifiers: ["Control"] });
  await preview.getByTestId("inspector-tab-selection").click();
  expect(await columns(preview)).toBe(3);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "03-expert-1280");
});

test("④ Expert · 1920×1080 · narrow inputs, wide Inspector", async ({ preview }) => {
  await preview.setViewportSize({ width: 1920, height: 1080 });
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("preset-wide-inspector").click();
  await drag(preview, "sidebar-resize", 40);
  expect(await columns(preview)).toBe(3);
  expect(await paneWidth(preview, "inspector")).toBeGreaterThan((await paneWidth(preview, "inputs")) * 2);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "04-expert-1920-wide-inspector");
});

test("⑤ Expert · rail collapsed, re-expand button at the top", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-toggle").click();
  await expect(preview.getByTestId("sidebar")).toHaveAttribute("data-rail", "true");
  await expect(preview.getByTestId("nav-toggle")).toBeVisible();
  expect(await columns(preview)).toBe(3);
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "05-expert-rail-1280");
});

test("⑥ 900×660 · Normal and Expert (two columns, queue below)", async ({ preview }) => {
  await preview.setViewportSize({ width: 900, height: 660 });
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("toolbar-toggle").click();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "06a-normal-900x660");
  await preview.getByTestId("mode-expert").click();
  expect(await columns(preview)).toBe(2);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "06b-expert-900x660");
});

test("⑦ environment mock: GPU / CPU / unconfigured / load failure", async ({ preview }) => {
  await preview.getByTestId("topnav-setup").click();
  await preview.getByTestId("env-toggle-gpu").click();
  await shot(preview, "07a-env-normal-gpu");
  await preview.getByTestId("mode-expert").click();
  await choose(preview, "toolbar-env", /^CPUのみの例/);
  await preview.getByTestId("env-expand-all").click();
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "07b-env-expert-cpu");
  await preview.getByTestId("toolbar-toggle").click();
  await choose(preview, "toolbar-env", /^未構成の例/);
  await preview.getByTestId("mode-normal").click();
  await preview.getByTestId("topnav-transcribe").click();
  await expect(preview.getByTestId("normal-start-blocked")).toBeVisible();
  await shot(preview, "07c-env-unconfigured-normal-home");
  await choose(preview, "toolbar-env", /^検出の読み込み失敗の例/);
  await preview.getByTestId("topnav-setup").click();
  await expect(preview.getByTestId("env-load-failed")).toBeVisible();
  await shot(preview, "07d-env-load-failed");
});

test("⑧ Queue and Results pages (separate responsibilities)", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-queue").click();
  await preview.getByTestId("queue-row-demo-job-003").click();
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "08a-queue-page");
  const stepAll = preview.getByTestId("queue-page-step-all");
  for (let i = 0; i < 12 && (await stepAll.isEnabled()); i += 1) await stepAll.click();
  await preview.getByTestId("nav-results").click();
  await preview.getByTestId("result-row-demo-job-001").click();
  await shot(preview, "08b-results-page");
});

// A 1920×1200 screen with a maximised window (≈1920×1160 DIP) at OS scale 100–200 %.
// Viewport/DPR emulation in Chromium is NOT a Windows DPI acceptance (UI-4 #22).
const DPI = [
  { scale: 1, width: 1920, height: 1160, cols: 3 },
  { scale: 1.25, width: 1536, height: 928, cols: 3 },
  { scale: 1.5, width: 1280, height: 773, cols: 3 },
  { scale: 2, width: 960, height: 580, cols: 2 },
];
for (const d of DPI) {
  test(`DPI ${d.scale * 100}% (1920×1200 screen, ${d.width}×${d.height} CSS px): expert layout`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: d.width, height: d.height }, deviceScaleFactor: d.scale });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/preview.html");
    await expect(page.getByTestId("preview-ready")).toBeVisible();
    await choose(page, "toolbar-scenario", /^複数ファイル・処理中/);
    await page.getByTestId("mode-expert").click();
    expect(await columns(page)).toBe(d.cols);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    await shot(page, `dpi-${d.scale * 100}-expert`);
    expect(errors).toEqual([]);
    await context.close();
  });
}
