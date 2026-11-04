import { expect, type Page, test as base } from "@playwright/test";

export const SESSION = "ui2-e2e-synthetic-session-key";
export const SESSION_HEADER = { "x-utteran-session": SESSION };

declare global {
  interface Window {
    __violations: { directive: string; blocked: string }[];
  }
}

/** Every page records CSP violations and same-origin-only network use. */
export const test = base.extend<{ app: Page; externalRequests: string[] }>({
  externalRequests: async ({ page }, use) => {
    const external: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.hostname !== "127.0.0.1") external.push(url.origin);
    });
    await use(external);
  },
  app: async ({ page, request }, use) => {
    // Reset the shared synthetic settings file through the real API.
    const reset = await request.patch("/api/settings", { headers: SESSION_HEADER, data: { theme: "system", language: "ja" } });
    expect(reset.status()).toBe(200);
    await page.addInitScript(() => {
      window.__violations = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__violations.push({ directive: event.effectiveDirective, blocked: event.blockedURI });
      });
    });
    await page.goto(`/launch?session=${SESSION}`);
    await expect(page.getByTestId("app-ready")).toBeVisible();
    await use(page);
    expect(await page.evaluate(() => window.__violations)).toEqual([]);
  },
});

export { expect };

export async function shot(page: Page, name: string): Promise<void> {
  if (!process.env.UI2_SCREENSHOTS) return;
  await page.waitForTimeout(500); // let theme transitions settle
  await page.screenshot({ path: `test-results/screenshots/${name}.png`, fullPage: false });
}

/** Pick an option of a Vuetify v-select identified by data-testid. */
export async function choose(page: Page, testid: string, optionText: RegExp | string): Promise<void> {
  await page.getByTestId(testid).click();
  await page.getByRole("option", { name: optionText }).click();
}
