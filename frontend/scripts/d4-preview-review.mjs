import { chromium } from "playwright";

const url = process.env.D4_PREVIEW_URL ?? "http://127.0.0.1:5180/preview.html";
const browser = await chromium.launch();
try {
  let page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
  const button = (id) => page.getByTestId(id);
  async function capture(name) {
    await page.waitForFunction(() => document.getAnimations().every(
      (animation) => animation.playState !== "running" || animation.effect?.getTiming().iterations === Infinity,
    ));
    await page.screenshot({ path: `test-results/d4-review/preview-${name}.png`, animations: "disabled", style: ".v-snackbar { visibility: hidden !important; }" });
  }
  async function choose(id, name) {
    await button(id).click();
    await page.getByRole("option", { name }).click();
  }
  async function begin() {
    await page.goto(url);
    await button("preview-ready").waitFor();
    await button("palette-blue").click();
    await button("theme-light").click();
  }
  await begin();
  await choose("toolbar-scenario", /^未選択/);
  await button("toolbar-toggle").click();
  await capture("normal-empty-ja-light-blue-1280");

  await begin();
  await choose("toolbar-scenario", /^複数ファイル・未投入/);
  await button("mode-expert").click();
  await button("toolbar-toggle").click();
  for (const [width, height, columns] of [[1920, 1080, 3], [1280, 820, 3], [1100, 760, 2], [640, 660, 1]]) {
    await page.setViewportSize({ width, height });
    await page.locator(`[data-testid=expert-layout][data-columns="${columns}"]`).waitFor();
    await capture(`expert-${width}-ja-light-blue`);
  }
  await page.setViewportSize({ width: 1280, height: 820 });
  await begin();
  await choose("toolbar-scenario", /^複数ファイル・処理中/);
  await button("mode-expert").click();
  await button("nav-queue").click();
  await button("queue-row-demo-job-001").click();
  await button("toolbar-toggle").click();
  await capture("queue-ja-light-blue-1280");
  await button("nav-results").click();
  await button("results-tab-history").click();
  await button("result-row-demo-job-003").click();
  await capture("history-ja-light-blue-1280");
  await button("result-delete").click();
  await button("history-delete-card").waitFor();
  await capture("history-dialog-ja-light-blue-1280");
  await button("history-delete-cancel").click();
  await button("nav-settings").click();
  await capture("settings-ja-light-blue-1280");

  await begin();
  await choose("toolbar-scenario", /^読み込みエラー$/);
  await button("mode-expert").click();
  await button("nav-queue").click();
  await button("toolbar-toggle").click();
  await button("queue-section-error").waitFor();
  await capture("queue-error-ja-light-blue-1280");

  await page.close();
  page = await browser.newPage({ viewport: { width: 640, height: 660 }, deviceScaleFactor: 2 });
  await begin();
  await button("theme-dark").click();
  await button("lang-en").click();
  await button("toolbar-toggle").click();
  await capture("normal-640-en-dark-blue");
  await page.close();
  page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await begin();
  await choose("toolbar-scenario", /^複数ファイル・未投入/);
  await button("palette-green").click();
  await button("theme-dark").click();
  await button("lang-en").click();
  await button("mode-expert").click();
  await button("toolbar-toggle").click();
  await capture("expert-1920-en-dark-green");
} finally {
  await browser.close();
}
