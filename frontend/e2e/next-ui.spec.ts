import { choose, expect, SESSION, SESSION_HEADER, shot, test } from "./fixtures";

test("launch cookie opens the next UI from local assets with a per-response nonce", async ({ app, externalRequests, request }) => {
  const nonce = await app.locator('meta[name="csp-nonce"]').getAttribute("content");
  expect(nonce).toMatch(/^[A-Za-z0-9_-]{22}$/);
  // Vuetify's theme <style> carries the same nonce the server put in the CSP header.
  await expect(app.locator(`style[nonce]`).first()).toBeAttached();
  const styleNonce = await app.locator("style[nonce]").first().evaluate((el) => (el as HTMLStyleElement).nonce);
  expect(styleNonce).toBe(nonce);

  const page = await request.get("/next/");
  const policy = page.headers()["content-security-policy"] ?? "";
  expect(policy).toMatch(/style-src 'self' 'nonce-[A-Za-z0-9_-]{22}';/);
  expect(policy).toContain("script-src 'self';");
  expect(policy).not.toMatch(/unsafe-inline|unsafe-eval/);
  expect(page.headers()["cache-control"]).toBe("no-store");
  const again = await request.get("/next/");
  expect(again.headers()["content-security-policy"]).not.toBe(policy);

  // The legacy UI is untouched and keeps its policy.
  const legacy = await request.get("/");
  expect(legacy.headers()["content-security-policy"]).toBe(
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
  );
  expect(externalRequests).toEqual([]);
  await shot(app, "01-normal-transcribe");
});

test("the API rejects requests without the session", async ({ playwright, baseURL }) => {
  const fresh = await playwright.request.newContext({ baseURL });
  expect((await fresh.get("/api/settings")).status()).toBe(401);
  expect((await fresh.get(`/launch?session=wrong-${SESSION}`, { maxRedirects: 0 })).status()).toBe(401);
  await fresh.dispose();
});

test("sidebar: un-migrated areas explain themselves and offer no fake actions", async ({ app }) => {
  for (const id of ["results", "models", "queue", "setup"]) {
    await app.getByTestId(`nav-${id}`).click();
    const section = app.getByTestId(`section-${id}`);
    await expect(section.getByTestId("not-migrated")).toBeVisible();
    await expect(section.getByRole("button")).toHaveCount(0);
  }
  await expect(app.getByTestId("nav-setup")).toHaveClass(/v-list-item--active/);
  await expect(app.getByTestId("nav-queue")).not.toHaveClass(/v-list-item--active/);
  await shot(app, "02-not-migrated");
});

test("environment: a late answer for an earlier profile does not roll the selection back (D-01)", async ({ app }) => {
  await expect(app.getByTestId("env-active")).toContainText("cpu");
  await choose(app, "env-profile", /^cuda/); // synthetic cuda answers after 1.5 s
  await expect(app.getByTestId("env-loading")).toBeVisible();
  await choose(app, "env-profile", /^cpu/);
  await expect(app.getByTestId("env-active")).toContainText("cpu");
  await app.waitForTimeout(2000); // let the stale cuda answer arrive
  await expect(app.getByTestId("env-active")).toContainText("cpu");
  await expect(app.getByTestId("env-profile")).toContainText("cpu");
  await expect(app.getByTestId("env-readiness")).toHaveText("未検証");
  await expect(app.getByTestId("env-loading")).toHaveCount(0);
});

test("environment: failure is shown in place and retry recovers", async ({ app }) => {
  let failures = 1;
  await app.route("**/api/environment*", async (route) => {
    if (failures-- > 0) return route.fulfill({ status: 500, body: "Internal Server Error" });
    return route.continue();
  });
  await choose(app, "env-profile", /^intel/);
  await expect(app.getByTestId("env-error")).toBeVisible();
  await expect(app.getByTestId("env-error")).toContainText("HTTP 500");
  await app.getByTestId("env-error-retry").click();
  await expect(app.getByTestId("env-active")).toContainText("intel");
  await expect(app.getByTestId("env-error")).toHaveCount(0);
});

test("settings: theme/language are saved through the real API, once per click", async ({ app, request }) => {
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("settings-token")).toContainText("未設定");
  const patches: string[] = [];
  app.on("request", (r) => {
    if (r.method() === "PATCH" && r.url().endsWith("/api/settings")) patches.push(r.postData() ?? "");
  });
  await choose(app, "settings-language", "English");
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
  await app.getByTestId("settings-save").dblclick();
  await expect(app.getByTestId("settings-status")).toHaveText("Saved.");
  expect(patches).toEqual(['{"language":"en"}']);
  await expect(app.getByTestId("nav-settings")).toContainText("Settings");
  await expect(app.locator("html")).toHaveAttribute("lang", "en");
  const stored = await request.get("/api/settings", { headers: SESSION_HEADER });
  expect((await stored.json()).language).toBe("en");
  await choose(app, "settings-theme", "Dark");
  await app.getByTestId("settings-save").click();
  await expect(app.locator(".v-theme--dark").first()).toBeAttached();
  await shot(app, "03-settings-en-dark");
});

test("settings: a failed save keeps the draft and the applied theme; retry succeeds", async ({ app }) => {
  await app.getByTestId("nav-settings").click();
  let failures = 1;
  await app.route("**/api/settings", async (route) => {
    if (route.request().method() === "PATCH" && failures-- > 0) return route.fulfill({ status: 503, body: "" });
    return route.continue();
  });
  await choose(app, "settings-theme", "ダーク");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-save-error")).toBeVisible();
  await expect(app.getByTestId("settings-theme")).toContainText("ダーク");
  await expect(app.locator(".v-theme--dark")).toHaveCount(0);
  await shot(app, "04-settings-save-error");
  await app.getByTestId("settings-save-error-retry").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await expect(app.locator(".v-theme--dark").first()).toBeAttached();
});

test("mode switch keeps navigation, environment and an unsaved settings draft", async ({ app }) => {
  await choose(app, "env-profile", /^intel/);
  await expect(app.getByTestId("env-active")).toContainText("intel");
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-theme", "ライト");
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("expert-workspace")).toBeVisible();
  await expect(app.getByTestId("pane-inputs")).toBeVisible();
  await expect(app.getByTestId("pane-queue")).toBeVisible();
  await expect(app.getByTestId("pane-inspector").getByTestId("env-active")).toContainText("intel");
  await expect(app.getByTestId("pane-queue").getByRole("button")).toHaveCount(0);
  await shot(app, "05-expert-3-pane");
  await app.getByTestId("mode-normal").click();
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("settings-theme")).toContainText("ライト");
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
});

test("narrow window: no horizontal scroll, one Expert pane at a time, rail sidebar", async ({ app }) => {
  await app.setViewportSize({ width: 900, height: 660 }); // pywebview min_size in app.py
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("expert-pane-switch")).toBeVisible();
  await expect(app.getByTestId("pane-inspector")).toBeVisible();
  await expect(app.getByTestId("pane-inputs")).toBeHidden();
  await app.getByTestId("expert-pane-switch").getByRole("button", { name: "出力・キュー" }).click();
  await expect(app.getByTestId("pane-queue")).toBeVisible();
  await expect(app.getByTestId("pane-inspector")).toBeHidden();
  const overflow = await app.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(app, "06-narrow-expert");
  await app.setViewportSize({ width: 1100, height: 760 });
  await expect(app.getByTestId("pane-inputs")).toBeVisible();
  await expect(app.getByTestId("pane-queue")).toBeVisible();
});

test("keyboard: Tab reaches the sidebar, arrow keys move, Enter activates (Vuetify list model)", async ({ app }) => {
  await app.locator("body").click({ position: { x: 600, y: 790 } });
  let inSidebar = false;
  for (let i = 0; i < 40 && !inSidebar; i += 1) {
    await app.keyboard.press("Tab");
    inSidebar = await app.evaluate(() => Boolean(document.activeElement?.closest('[data-testid="sidebar"]')));
  }
  expect(inSidebar).toBe(true);
  let onSettings = false;
  for (let i = 0; i < 8 && !onSettings; i += 1) {
    await app.keyboard.press("ArrowDown");
    onSettings = await app.evaluate(() => document.activeElement?.getAttribute("data-testid") === "nav-settings");
  }
  expect(onSettings).toBe(true);
  const outline = await app.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("settings-page")).toBeVisible();
});
