import { type APIRequestContext, expect, type Page, test as base } from "@playwright/test";

export const SESSION = "ui2-e2e-synthetic-session-key";
export const SESSION_HEADER = { "x-utteran-session": SESSION };

declare global {
  interface Window {
    __violations: { directive: string; blocked: string }[];
  }
}

/**
 * E-B autosaves the workset through its own `/api/v1/worksets` routes. Specs that assert the exact
 * B5/B7 write sequence leave those out (they are verified in worksets-eb.v1.spec.ts).
 */
export const isWorksetPath = (pathname: string) => pathname === "/api/v1/worksets" || pathname.startsWith("/api/v1/worksets/");

const EMPTY_WORKSET = {
  schema_version: 1, preset_ref: null, policy_ref: null,
  common: { language: null, formats: null, output_dir: "", output_source: null },
  inputs: [], primary_index: null, glossaries: [],
};

/**
 * A server shared by several tests keeps its worksets (that is the feature): start every test from
 * one empty "作業セット1" in Normal mode, through the real API, like the settings reset above.
 */
async function resetWorksets(request: APIRequestContext): Promise<void> {
  const listing = await request.get("/api/v1/worksets", { headers: SESSION_HEADER });
  if (listing.status() !== 200) return; // legacy servers have no v1 routes
  const body = (await listing.json()) as { exists: boolean; items: { id: string; revision: number }[] };
  if (!body.exists || body.items.length === 0) return;
  const [keep, ...rest] = body.items;
  for (const item of rest) {
    const removed = await request.delete(`/api/v1/worksets/${item.id}`, { headers: { ...SESSION_HEADER, "If-Match": `"${item.revision}"` } });
    expect(removed.status()).toBe(200);
  }
  const put = await request.put(`/api/v1/worksets/${keep!.id}`, {
    headers: { ...SESSION_HEADER, "If-Match": `"${keep!.revision}"` },
    data: { schema_version: 1, name: "作業セット1", state: EMPTY_WORKSET },
  });
  expect(put.status()).toBe(200);
  const current = await request.get("/api/v1/worksets/active", { headers: SESSION_HEADER });
  expect(current.status()).toBe(200);
  const revision = ((await current.json()) as { revision: number }).revision;
  const active = await request.put("/api/v1/worksets/active", {
    headers: { ...SESSION_HEADER, "If-Match": `"${revision}"` },
    data: { schema_version: 1, workset_id: keep!.id, view: { mode: "normal" } },
  });
  expect(active.status()).toBe(200);
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
    // Reset the shared synthetic settings file and the in-memory synthetic keyring through the
    // real API (the harness never touches the real user settings or OS keyring).
    const reset = await request.patch("/api/settings", {
      headers: SESSION_HEADER,
      data: { theme: "system", language: "ja", default_profile: null, default_input_dir: "", default_output_dir: "" },
    });
    expect(reset.status()).toBe(200);
    expect((await request.delete("/api/token", { headers: SESSION_HEADER })).status()).toBe(200);
    await resetWorksets(request);
    await page.addInitScript(() => {
      window.__violations = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__violations.push({ directive: event.effectiveDirective, blocked: event.blockedURI });
      });
    });
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.name));
    await page.goto(`/launch?session=${SESSION}`);
    await expect(page.getByTestId("app-ready")).toBeVisible();
    await use(page);
    expect(await page.evaluate(() => window.__violations)).toEqual([]);
    expect(pageErrors).toEqual([]);
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

/**
 * Expert keeps single-job recovery behind its disclosure; Normal shows it directly. Since #96 the
 * queue and the server's job list inside it are folded until needed: open that fold too.
 */
export async function openSingleJobTools(page: Page): Promise<void> {
  const tools = page.getByTestId("v1-single-tools");
  if (await tools.isVisible() && await tools.getAttribute("open") === null) {
    await page.getByTestId("v1-single-tools-toggle").click();
  }
  await openRunRecords(page);
}

/**
 * UI-3 #96: Expert ② shows one Design Freeze category at a time for one target (the common
 * defaults or the files selected in ①). Opens the given target and category; no value changes.
 */
export async function inspectorCategory(page: Page, target: "common" | "selection", category: "asr" | "prompt" | "diarization" | "output" | "advanced"): Promise<void> {
  await page.getByTestId(`inspector-tab-${target}`).click();
  await page.getByTestId(`category-${category}`).click();
  await expect(page.getByTestId(`category-panel-${category}`)).toBeVisible();
}

/** UI-3 #96: Normal keeps the queue and the server's job list folded until they are needed. */
export async function openRunRecords(page: Page): Promise<void> {
  const toggle = page.locator("[data-testid='v1-records-toggle']:visible").first();
  await expect(toggle).toBeVisible();
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
}

/** UI-3 #96: Normal keeps the model, Preset/Policy and the environment folded under "details". */
export async function openNormalDetails(page: Page): Promise<void> {
  const toggle = page.getByTestId("normal-details-toggle");
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
}

/**
 * UI-3 #96: where the Preset/Policy library lives — Normal "details", Expert ② common › advanced.
 * Only reveals it (no value changes).
 */
export async function revealLibrary(page: Page): Promise<void> {
  const mode = await page.locator("[data-mode]").first().getAttribute("data-mode");
  if (mode === "expert") await inspectorCategory(page, "common", "advanced");
  else await openNormalDetails(page);
}

/** UI-3 #96: the common output folder — Normal "ファイル" card, Expert ② common › output. */
export async function fillOutput(page: Page, folder: string): Promise<void> {
  const mode = await page.locator("[data-mode]").first().getAttribute("data-mode");
  if (mode === "expert") await inspectorCategory(page, "common", "output");
  await page.getByTestId("v1-output-path").locator("input").fill(folder);
}
