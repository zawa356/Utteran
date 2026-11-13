import { expect, SESSION, test } from "./fixtures";

/**
 * UI-3 S5-A, `--next-ui` (legacy server): the page says `legacy` and nothing in the browser can
 * turn on the v1 path — no query string, no storage, no request to `/api/v1`.
 */
test("--next-ui stays on the legacy API: meta says legacy, no URL/storage upgrade, zero /api/v1 requests", async ({ app }) => {
  const v1Requests: string[] = [];
  app.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/v1")) v1Requests.push(r.url());
  });
  expect(await app.locator('meta[name="utteran-api-mode"]').getAttribute("content")).toBe("legacy");
  await expect(app.getByTestId("transcribe-not-migrated")).toBeVisible();
  await expect(app.getByTestId("v1-mode-notice")).toHaveCount(0);
  await expect(app.getByTestId("tx-start")).toBeVisible();

  await app.evaluate(() => {
    localStorage.setItem("utteran-api-mode", "v1-dev");
    sessionStorage.setItem("utteran-api-mode", "v1-dev");
  });
  await app.goto("/next/?api=v1-dev&utteran-api-mode=v1-dev#v1-dev");
  await expect(app.getByTestId("app-ready")).toBeVisible();
  expect(await app.locator('meta[name="utteran-api-mode"]').getAttribute("content")).toBe("legacy");
  await expect(app.getByTestId("v1-settings")).toHaveCount(0);
  await expect(app.getByTestId("tx-start")).toBeVisible();
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("tx-batch-start")).toBeVisible();
  await expect(app.getByTestId("v1-batch-start")).toHaveCount(0);
  expect(v1Requests).toEqual([]);
  // The legacy server has no v1 surface at all.
  const status = await app.evaluate(async () => (await fetch("/api/v1/presets")).status);
  expect(status).toBe(404);
  expect(SESSION).not.toBe("");
});
