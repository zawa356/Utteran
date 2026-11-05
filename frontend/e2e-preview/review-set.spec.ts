import { appBarOverlaps, choose, columns, expect, horizontalOverflow, readability, settled, setup, shot, test } from "./fixtures";

/**
 * Owner review set for iteration 3 (docs/architecture/ui3/iteration-3.md §6). Each case asserts
 * the layout (no horizontal overflow, expected columns, no small text) and, with UI3_SCREENSHOTS=1,
 * writes test-results/ui3-screenshots/<name>.png. Synthetic and fictional data only.
 * Viewport/zoom emulation in Chromium is NOT a Windows WebView2 / OS DPI acceptance (UI-4 #22).
 */

test("① Normal home: default, after 'choose another file', running, done (light green) and dark", async ({ preview }) => {
  await expect(preview.getByTestId("sidebar")).toHaveCount(0);
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "1a-normal-default-light");
  await preview.getByTestId("normal-choose-other").click();
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-call-06.mp3");
  await shot(preview, "1b-normal-replaced-light");
  await setup(preview, { scenario: /^1ファイル・実行中/ });
  await expect(preview.getByTestId("progress-percent")).toBeVisible();
  await shot(preview, "1c-normal-running-light");
  await setup(preview, { scenario: /^1ファイル・完了/ });
  await expect(preview.getByTestId("normal-result")).toBeVisible();
  await shot(preview, "1d-normal-done-light");
  await setup(preview, { theme: "dark", scenario: /^1ファイル・実行中/ });
  await preview.getByTestId("config-details").click();
  await expect(preview.locator(".v-application")).toHaveClass(/v-theme--greenDark/);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "1e-normal-running-dark");
});

test("② Expert: three unsubmitted selected, right after submit, busy with a failure, narrow vs wide files pane", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-01").click();
  await preview.getByTestId("input-row-in-02").click({ modifiers: ["Control"] });
  await preview.getByTestId("input-row-in-04").click({ modifiers: ["Control"] });
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("toolbar-toggle").click();
  expect(await columns(preview)).toBe(3);
  await shot(preview, "2a-expert-three-selected");
  await preview.getByTestId("submit-selected").click();
  await expect(preview.getByTestId("submit-result")).toBeVisible();
  await shot(preview, "2b-expert-just-submitted");
  await preview.getByTestId("toolbar-toggle").click();
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("toolbar-toggle").click();
  await preview.getByTestId("input-row-in-03").click();
  await shot(preview, "2c-expert-busy-failed");
  await preview.getByTestId("toolbar-toggle").click();
  await preview.getByTestId("preset-wideInputs").click();
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "2d-expert-wide-files-30-42-28");
  await preview.setViewportSize({ width: 1920, height: 1080 });
  await preview.getByTestId("toolbar-toggle").click();
  await preview.getByTestId("preset-compactInputs").click();
  await preview.getByTestId("toolbar-toggle").click();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  await shot(preview, "2e-expert-1920-narrow-files-22-53-25");
});

test("②' Expert dark with the job status unavailable (not a stopped job)", async ({ preview }) => {
  await setup(preview, { theme: "dark", scenario: /^複数ファイル・処理中/ });
  await choose(preview, "toolbar-sync", /^取得できない例/);
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("toolbar-toggle").click();
  await expect(preview.getByTestId("status-unavailable")).toBeVisible();
  await shot(preview, "2f-expert-dark-status-unavailable");
});

// 1280×820 at browser zoom 125/150/200 % ≈ 1024×656 / 853×547 / 640×410 CSS px.
const ZOOM = [
  { name: "900x660", width: 900, height: 660, expertCols: 2 },
  { name: "zoom125", width: 1024, height: 656, expertCols: 2 },
  { name: "zoom150", width: 853, height: 547, expertCols: 2 },
  { name: "zoom200", width: 640, height: 410, expertCols: 1 },
];
for (const z of ZOOM) {
  test(`③ ${z.name} (${z.width}×${z.height} CSS px): top bar, panes, readable text`, async ({ preview }) => {
    await preview.setViewportSize({ width: z.width, height: z.height });
    await setup(preview, { scenario: /^複数ファイル・処理中/ });
    await preview.getByTestId("toolbar-toggle").click();
    await settled(preview);
    expect(await appBarOverlaps(preview)).toEqual([]);
    expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
    expect((await readability(preview)).smallText).toEqual([]);
    await shot(preview, `3-${z.name}-normal`);
    await preview.getByTestId("mode-expert").click();
    expect(await columns(preview)).toBe(z.expertCols);
    expect(await appBarOverlaps(preview)).toEqual([]);
    expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
    expect((await readability(preview)).smallText).toEqual([]);
    await shot(preview, `3-${z.name}-expert`);
  });
}

test("④ Settings (Normal / Expert), log mock, About, simplified Setup", async ({ preview }) => {
  await preview.getByTestId("toolbar-toggle").click();
  await preview.getByTestId("topnav-settings").click();
  await shot(preview, "4a-settings-normal");
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("settings-cat-advanced").click();
  await shot(preview, "4b-settings-expert-advanced");
  await preview.getByTestId("settings-cat-presets").click();
  await shot(preview, "4c-settings-expert-presets");
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-systemLog").click();
  await expect(preview.getByTestId("log-lines")).toBeVisible();
  await shot(preview, "4d-system-log-mock");
  await preview.getByTestId("dialog-close").click();
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-diagnostics").click();
  await shot(preview, "4e-diagnostics-mock");
  await preview.getByTestId("dialog-ok").click();
  await preview.getByTestId("menu-help").click();
  await preview.getByTestId("menu-item-about").click();
  await expect(preview.getByTestId("about-version")).toBeVisible();
  await shot(preview, "4f-about");
  await preview.getByTestId("dialog-ok").click();
  await preview.getByTestId("nav-setup").click();
  await preview.getByTestId("env-toggle-gpu").click();
  await shot(preview, "4g-setup-expert-gpu");
  await preview.getByTestId("mode-normal").click();
  await shot(preview, "4h-setup-normal-gpu");
  await preview.getByTestId("toolbar-toggle").click();
  await choose(preview, "toolbar-env", /^CPUのみの例/);
  await preview.getByTestId("menustyle-button").click();
  await preview.getByTestId("lang-en").click();
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "4i-setup-normal-cpu-en");
  await preview.getByTestId("menu-button").click();
  await shot(preview, "4j-menu-button-en");
  await preview.keyboard.press("Escape");
});

test("⑤ Queue and Results pages", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-queue").click();
  await preview.getByTestId("queue-row-demo-job-001").click();
  await preview.getByTestId("toolbar-toggle").click();
  await shot(preview, "5a-queue-page");
  const stepAll = preview.getByTestId("queue-page-step-all");
  for (let i = 0; i < 12 && (await stepAll.isEnabled()); i += 1) await stepAll.click();
  await preview.getByTestId("nav-results").click();
  await preview.getByTestId("result-row-demo-job-001").click();
  await shot(preview, "5b-results-page");
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
    expect(await appBarOverlaps(page)).toEqual([]);
    await shot(page, `dpi-${d.scale * 100}-expert`);
    expect(errors).toEqual([]);
    await context.close();
  });
}
