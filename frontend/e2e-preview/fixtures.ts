import { expect, type Page, test as base } from "@playwright/test";

/**
 * Every prototype page records requests and console errors. The prototype may only load its own
 * dev-server modules: any /api request, any other origin or any console error fails the test.
 */
export const test = base.extend<{ preview: Page }>({
  preview: async ({ page }, use) => {
    const bad: string[] = [];
    const origin = new URL(test.info().project.use.baseURL ?? "http://127.0.0.1").origin;
    page.on("request", (r) => {
      const url = new URL(r.url());
      if (url.origin !== origin || url.pathname.startsWith("/api") || url.pathname.startsWith("/next")) bad.push(`${r.method()} ${r.url()}`);
    });
    page.on("console", (m) => {
      if (m.type() === "error") bad.push(`console: ${m.text()}`);
    });
    page.on("pageerror", (e) => bad.push(`pageerror: ${e.message}`));
    await page.goto("/preview.html");
    await expect(page.getByTestId("preview-ready")).toBeVisible();
    await use(page);
    expect(bad).toEqual([]);
  },
});
export { expect };

export async function shot(page: Page, name: string): Promise<void> {
  if (!process.env.UI3_SCREENSHOTS) return;
  await page.waitForTimeout(400); // let theme/snackbar transitions settle
  await page.screenshot({ path: `test-results/ui3-screenshots/${name}.png`, fullPage: true });
}

export async function choose(page: Page, testid: string, optionText: RegExp | string): Promise<void> {
  await page.getByTestId(testid).click();
  await page.getByRole("option", { name: optionText }).click();
}

export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/** Scenario/theme/language through the reviewer toolbar (in-memory only). */
export async function setup(page: Page, opts: { lang?: "ja" | "en"; theme?: "light" | "dark"; scenario?: RegExp }): Promise<void> {
  if (opts.scenario) await choose(page, "toolbar-scenario", opts.scenario);
  if (opts.lang) await page.getByTestId(`lang-${opts.lang}`).click();
  if (opts.theme) await page.getByTestId(`theme-${opts.theme}`).click();
}
