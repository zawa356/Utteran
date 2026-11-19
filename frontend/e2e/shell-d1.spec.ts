import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * Bundle D1 (UI-3, Design Freeze iteration 5): the product shell against the real FastAPI app in
 * the `--next-ui` (legacy) shape. Normal = three destinations in the app bar and no sidebar;
 * Expert = the six-destination management sidebar. Both modes show the same AppCore state.
 * Headless Chromium — NOT a WebView2 / OS-DPI acceptance (bundle D4, Issue #22).
 */

const NORMAL = ["transcribe", "setup", "settings"];
const EXPERT = ["transcribe", "queue", "results", "models", "setup", "settings"];
const EXPERT_ONLY = ["queue", "results", "models"];
/** Must match TOPNAV_FOLD_WIDTH / NARROW_WINDOW in src/domain/shell.ts. */
const TOPNAV_FOLD_WIDTH = 1000;
const NARROW_WINDOW = 1024;
const MEDIA = ["", "synthetic", "media"].join("/");

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const writes = (seen: Request[]) => seen.filter((r) => r.method() !== "GET").map(line);
const v1Calls = (seen: Request[]) => seen.map(line).filter((l) => l.includes(" /api/v1"));

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

/** Choose a Normal destination: the app-bar button, or the folded menu in a narrow window. */
async function normalNav(page: Page, id: string): Promise<void> {
  if ((await page.getByTestId("top-nav").getAttribute("data-folded")) === "true") await page.getByTestId("top-nav-menu").click();
  await page.getByTestId(`nav-${id}`).click();
}

/** The page shown and the navigation's current item agree. */
async function expectPage(page: Page, id: string): Promise<void> {
  await expect(page.getByTestId("content")).toHaveAttribute("data-section", id);
  // (A folded Normal menu names the current page on its button; the D1-07 cases check it.)
  if ((await page.getByTestId("top-nav-menu").count()) > 0) return;
  await expect(page.locator('[aria-current="page"]')).toHaveCount(1);
  await expect(page.getByTestId(`nav-${id}`)).toHaveAttribute("aria-current", "page");
}

async function drop(app: Page, names: string[]): Promise<void> {
  await app.evaluate((d) => window.dispatchEvent(new CustomEvent("utteran-input-dropped", { detail: d })), names.map((n) => ({ path: `${MEDIA}/${n}`, kind: "file" })));
}
const row = (app: Page, name: string) => app.locator(`[data-testid="tx-list-row"][data-name="${name}"]`);

async function useSettings(request: APIRequestContext, data: Record<string, unknown>): Promise<void> {
  expect((await request.patch("/api/settings", { headers: SESSION_HEADER, data })).status()).toBe(200);
}

test("D1-01: Normal = three app-bar destinations and no sidebar; Expert = the six-item sidebar and no app-bar destinations", async ({ app }) => {
  // Normal
  await expect(app.getByTestId("top-nav")).toBeVisible();
  await expect(app.getByTestId("sidebar")).toHaveCount(0);
  const normalIds = await app.getByTestId("top-nav").locator("[data-testid^='nav-']").evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  expect(normalIds).toEqual(NORMAL.map((id) => `nav-${id}`));
  for (const id of EXPERT_ONLY) await expect(app.getByTestId(`nav-${id}`)).toHaveCount(0);
  await expect(app.getByTestId("top-nav")).toHaveAttribute("aria-label", "メインナビゲーション");
  await expectPage(app, "transcribe");
  await expect(app.getByTestId("tx-start")).toBeVisible(); // the existing Normal transcription page
  await normalNav(app, "settings");
  await expectPage(app, "settings");
  await expect(app.getByTestId("settings-page")).toBeVisible();
  await expect(app.getByTestId("token-status")).toHaveText("未設定"); // the existing settings page works
  await shot(app, "d1-01-normal-settings-1280");

  // Expert
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("top-nav")).toHaveCount(0);
  await expect(app.getByTestId("sidebar")).toBeVisible();
  await expect(app.getByTestId("sidebar")).toHaveAttribute("data-rail", "false");
  const expertIds = await app.getByTestId("sidebar-list").locator("[data-testid^='nav-']").evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  expect(expertIds).toEqual(EXPERT.map((id) => `nav-${id}`));
  await expect(app.getByTestId("sidebar").locator("nav")).toHaveAttribute("aria-label", "メインナビゲーション");
  // First Expert visit: its own default page (transcription), with the existing three panes.
  await expectPage(app, "transcribe");
  await expect(app.getByTestId("expert-workspace")).toBeVisible();
  for (const pane of ["pane-inputs", "pane-inspector", "pane-queue"]) await expect(app.getByTestId(pane)).toBeVisible();
  await expect(app.getByTestId("pane-queue").getByTestId("tx-batch-start")).toBeDisabled();
  await shot(app, "d1-01-expert-transcribe-1280");
  for (const id of EXPERT) {
    await app.getByTestId(`nav-${id}`).click();
    await expectPage(app, id);
  }
  await expect(app.getByTestId("settings-page")).toBeVisible();
});

test("D1-02: each mode returns to its own last page — Normal settings ⇄ Expert queue", async ({ app }) => {
  await normalNav(app, "settings");
  await app.getByTestId("mode-expert").click();
  await expectPage(app, "transcribe");
  await app.getByTestId("nav-queue").click();
  await expectPage(app, "queue");
  await app.getByTestId("mode-normal").click();
  await expectPage(app, "settings"); // not the Expert-only queue, and not reset to transcription
  await expect(app.getByTestId("settings-page")).toBeVisible();
  await app.getByTestId("mode-expert").click();
  await expectPage(app, "queue");
  await expect(app.getByTestId("section-queue").getByTestId("not-migrated")).toBeVisible();
  // An Expert-only page with no Normal page chosen yet → Normal shows transcription.
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-results").click();
  await app.getByTestId("mode-normal").click();
  await expectPage(app, "transcribe");
  // "Several files (Expert)" from Normal opens the Expert transcription page, not its last page.
  await drop(app, ["d1-a.wav", "d1-b.wav"]);
  await app.getByTestId("tx-input-to-expert").click();
  await expectPage(app, "transcribe");
  await expect(app.getByTestId("expert-workspace")).toBeVisible();
});

test("D1-03/D1-05: inputs, selection, an override, the settings draft and the Expert pane survive every switch; no write, no v1 request", async ({ app }) => {
  const seen = recordApi(app);
  await app.getByTestId("mode-expert").click();
  await choose(app, "env-profile", /^cuda/);
  await expect(app.getByTestId("tx-profile")).toContainText("cuda");
  await drop(app, ["d1-a.wav", "d1-b.wav", "d1-c.wav"]);
  await expect(app.getByTestId("tx-list-count")).toHaveText("3件（選択 3件）");
  await app.getByTestId("tx-list-select-none").click();
  await row(app, "d1-b.wav").getByTestId("tx-list-check").click();
  await choose(app, "tx-ov-model", /large-v3-turbo/);
  await app.getByTestId("tx-ov-apply").click();
  await expect(row(app, "d1-b.wav").getByTestId("tx-list-overridden")).toBeVisible();
  await row(app, "d1-c.wav").getByTestId("tx-list-check").click();
  await expect(app.getByTestId("tx-list-count")).toHaveText("3件（選択 2件）");
  // A one-pane window: pick the queue pane.
  await app.setViewportSize({ width: 900, height: 660 });
  await app.getByTestId("expert-pane-switch").getByRole("button", { name: "出力・キュー" }).click();
  await expect(app.getByTestId("pane-queue")).toBeVisible();
  await app.setViewportSize({ width: 1280, height: 820 });
  // An unsaved settings draft.
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-theme", "ライト");
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
  const before = writes(seen).length;

  // Every page, both sidebar states, both modes, the Normal pages, and back.
  for (const id of EXPERT) await app.getByTestId(`nav-${id}`).click();
  await app.getByTestId("nav-toggle").click();
  await expect(app.getByTestId("sidebar")).toHaveAttribute("data-rail", "true");
  for (const id of EXPERT) await app.getByTestId(`nav-${id}`).click();
  await app.getByTestId("nav-toggle").click();
  await expect(app.getByTestId("sidebar")).toHaveAttribute("data-rail", "false");
  await app.getByTestId("mode-normal").click();
  for (const id of NORMAL) await normalNav(app, id);
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("mode-normal").click();
  await app.getByTestId("mode-expert").click();

  // Expert's last page was settings: the draft is still there, unsaved.
  await expectPage(app, "settings");
  await expect(app.getByTestId("settings-theme")).toContainText("ライト");
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("tx-list-count")).toHaveText("3件（選択 2件）");
  await expect(row(app, "d1-b.wav").getByTestId("tx-list-overridden")).toBeVisible();
  await expect(row(app, "d1-a.wav").getByTestId("tx-list-overridden")).toHaveCount(0);
  await expect(app.getByTestId("tx-profile")).toContainText("cuda");
  await app.setViewportSize({ width: 900, height: 660 });
  await expect(app.getByTestId("pane-queue")).toBeVisible(); // the pane choice is kept
  await expect(app.getByTestId("pane-inspector")).toBeHidden();
  // Normal shows the same list (its current file is the primary row).
  await app.getByTestId("mode-normal").click();
  await expectPage(app, "settings");
  await normalNav(app, "transcribe");
  await expect(app.getByTestId("tx-input-summary")).toContainText("3");

  expect(writes(seen).length).toBe(before);
  expect(writes(seen)).toEqual([]); // nothing was PATCHed, POSTed or DELETEd at any point
  expect(v1Calls(seen)).toEqual([]); // `--next-ui` never talks to /api/v1
});

test("D1-05: the hash, query and storage never change the API mode or the page", async ({ app }) => {
  const seen = recordApi(app);
  await app.evaluate(() => {
    localStorage.setItem("utteran-api-mode", "v1-dev");
    sessionStorage.setItem("utteran-shell", JSON.stringify({ mode: "expert", section: "queue" }));
    location.hash = "#/expert/queue";
  });
  await expect(app.getByTestId("content")).toHaveAttribute("data-section", "transcribe");
  await expect(app.getByTestId("top-nav")).toBeVisible();
  await app.goto("/next/?api=v1-dev&mode=expert&section=queue#v1-dev");
  await expect(app.getByTestId("app-ready")).toBeVisible();
  expect(await app.locator('meta[name="utteran-api-mode"]').getAttribute("content")).toBe("legacy");
  await expect(app.getByTestId("preview-notice")).toHaveAttribute("data-notice", "app.previewNotice");
  // A reload starts at the defaults: nothing about the shell is persisted (G-05/G-06).
  await expect(app.getByTestId("content")).toHaveAttribute("data-section", "transcribe");
  await expect(app.getByTestId("sidebar")).toHaveCount(0);
  await app.getByTestId("mode-expert").click();
  for (const id of EXPERT) await app.getByTestId(`nav-${id}`).click();
  await expect(app.getByTestId("preview-notice")).toHaveAttribute("data-notice", "app.previewNotice");
  expect(v1Calls(seen)).toEqual([]);
  expect(writes(seen)).toEqual([]);
});

test("D1-06: un-migrated pages say so in place in both modes; choosing them writes nothing", async ({ app }) => {
  const seen = recordApi(app);
  await normalNav(app, "setup");
  const normalSetup = app.getByTestId("section-setup");
  await expect(normalSetup.getByTestId("not-migrated")).toContainText("セットアップ");
  await expect(normalSetup.getByRole("button")).toHaveCount(0);
  await expect(normalSetup).not.toContainText(/成功|完了/);
  await app.getByTestId("mode-expert").click();
  for (const id of ["queue", "results", "models", "setup"]) {
    await app.getByTestId(`nav-${id}`).click();
    const section = app.getByTestId(`section-${id}`);
    await expect(section.getByTestId("not-migrated")).toBeVisible();
    await expect(section.getByRole("button")).toHaveCount(0);
    // The sidebar states the migration status next to the name.
    await expect(app.getByTestId(`nav-${id}`)).toContainText("未移行");
  }
  await expect(app.getByTestId("nav-transcribe")).toContainText("一部移行");
  await expect(app.getByTestId("nav-settings")).toContainText("一部移行");
  expect(writes(seen)).toEqual([]);
});

for (const width of [900, 1100, 1280]) {
  for (const language of ["ja", "en"] as const) {
    for (const theme of ["light", "dark"] as const) {
      test(`D1-07: ${width}px ${language} ${theme}: Normal folding, Expert rail and its toggle, no horizontal overflow`, async ({ app, request }) => {
        await useSettings(request, { language, theme });
        await app.setViewportSize({ width, height: width === 900 ? 660 : 820 });
        await app.reload();
        await expect(app.getByTestId("app-ready")).toBeVisible();
        await expect(app.locator(`.v-theme--${theme}`).first()).toBeAttached();
        const folded = width < TOPNAV_FOLD_WIDTH;
        const narrow = width < NARROW_WINDOW;

        // Normal
        await expect(app.getByTestId("top-nav")).toHaveAttribute("data-folded", String(folded));
        if (folded) {
          await expect(app.getByTestId("top-nav-menu")).toContainText(language === "ja" ? "文字起こし" : "Transcribe");
          await expect(app.getByTestId("nav-settings")).toHaveCount(0);
        } else {
          for (const id of NORMAL) await expect(app.getByTestId(`nav-${id}`)).toBeVisible();
        }
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await normalNav(app, "settings");
        await expect(app.getByTestId("settings-page")).toBeVisible();
        if (folded) {
          await expect(app.getByTestId("top-nav-menu")).toContainText(language === "ja" ? "設定" : "Settings");
          await app.getByTestId("top-nav-menu").click();
          await expect(app.getByTestId("nav-settings")).toHaveAttribute("aria-current", "page");
          await app.keyboard.press("Escape");
        } else {
          await expect(app.getByTestId("nav-settings")).toHaveAttribute("aria-current", "page");
        }
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await shot(app, `d1-07-normal-${width}-${language}-${theme}`);

        // Expert: narrow windows start as a rail; the toggle works both ways at every width.
        await app.getByTestId("mode-expert").click();
        await expect(app.getByTestId("expert-workspace")).toBeVisible();
        const sidebar = app.getByTestId("sidebar");
        await expect(sidebar).toHaveAttribute("data-rail", String(narrow));
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await shot(app, `d1-07-expert-${width}-${language}-${theme}`);
        await expect(app.getByTestId("nav-toggle")).toBeEnabled();
        await app.getByTestId("nav-toggle").click();
        await expect(sidebar).toHaveAttribute("data-rail", String(!narrow));
        await expect(app.getByTestId("nav-toggle")).toBeVisible();
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        // In the rail every item keeps its name for assistive technology.
        const railNow = !narrow;
        if (railNow) {
          await expect(app.getByTestId("nav-queue")).toHaveAttribute("aria-label", language === "ja" ? "キュー（未移行）" : "Queue (Not migrated)");
        }
        // The panes still switch (one pane at a time below 1024 px).
        if (narrow) {
          await app.getByTestId("expert-pane-switch").getByRole("button").first().click();
          await expect(app.getByTestId("pane-inputs")).toBeVisible();
          await expect(app.getByTestId("pane-inspector")).toBeHidden();
        } else {
          await expect(app.getByTestId("pane-inputs")).toBeVisible();
          await expect(app.getByTestId("pane-inspector")).toBeVisible();
        }
        expect(await overflow(app)).toBeLessThanOrEqual(0);
        await app.getByTestId("nav-toggle").click();
        await expect(sidebar).toHaveAttribute("data-rail", String(narrow));
      });
    }
  }
}

test("D1-07 keyboard: Normal app-bar buttons and the folded menu, then the Expert rail toggle; focus never edits a form", async ({ app }) => {
  // Wide Normal: Tab reaches the app-bar destinations, Enter opens the page, focus stays on the button.
  await app.locator("body").click({ position: { x: 600, y: 790 } });
  let onNav = false;
  for (let i = 0; i < 30 && !onNav; i += 1) {
    await app.keyboard.press("Tab");
    onNav = await app.evaluate(() => document.activeElement?.getAttribute("data-testid") === "nav-transcribe");
  }
  expect(onNav).toBe(true);
  await app.keyboard.press("Tab");
  await app.keyboard.press("Tab");
  expect(await app.evaluate(() => document.activeElement?.getAttribute("data-testid"))).toBe("nav-settings");
  const outline = await app.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("settings-page")).toBeVisible();
  await expect(app.getByTestId("nav-settings")).toHaveAttribute("aria-current", "page");
  expect(await app.evaluate(() => document.activeElement?.getAttribute("data-testid"))).toBe("nav-settings");
  await expect(app.getByTestId("settings-status")).not.toHaveText("未保存の変更があります。");

  // Narrow Normal: the folded menu opens with Enter, arrows move, Enter chooses.
  await app.setViewportSize({ width: 900, height: 660 });
  await app.getByTestId("top-nav-menu").focus();
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("top-nav-list")).toBeVisible();
  let onTranscribe = false;
  for (let i = 0; i < 6 && !onTranscribe; i += 1) {
    await app.keyboard.press("ArrowDown");
    onTranscribe = await app.evaluate(() => document.activeElement?.getAttribute("data-testid") === "nav-transcribe");
  }
  expect(onTranscribe).toBe(true);
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("content")).toHaveAttribute("data-section", "transcribe");
  await expect(app.getByTestId("top-nav-list")).toBeHidden();
  await expect(app.getByTestId("top-nav-menu")).toContainText("文字起こし");

  // Narrow Expert: the rail toggle is reachable and works with Enter; the list keeps its arrow keys.
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("sidebar")).toHaveAttribute("data-rail", "true");
  await app.getByTestId("nav-toggle").focus();
  await expect(app.getByTestId("nav-toggle")).toHaveAttribute("aria-expanded", "false");
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("sidebar")).toHaveAttribute("data-rail", "false");
  await expect(app.getByTestId("nav-toggle")).toHaveAttribute("aria-expanded", "true");
  expect(await app.evaluate(() => document.activeElement?.getAttribute("data-testid"))).toBe("nav-toggle");
  await app.keyboard.press("Tab");
  let onModels = false;
  for (let i = 0; i < 8 && !onModels; i += 1) {
    onModels = await app.evaluate(() => document.activeElement?.getAttribute("data-testid") === "nav-models");
    if (!onModels) await app.keyboard.press("ArrowDown");
  }
  expect(onModels).toBe(true);
  await app.keyboard.press("Enter");
  await expect(app.getByTestId("section-models")).toBeVisible();
  await expect(app.getByTestId("nav-models")).toHaveAttribute("aria-current", "page");
});
