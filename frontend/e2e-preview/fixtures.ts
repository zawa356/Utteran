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
  // The transient snackbar is hidden so it does not cover the review images.
  await page.screenshot({ path: `test-results/ui3-screenshots/${name}.png`, fullPage: true, style: ".v-snackbar { visibility: hidden !important; }" });
}

export async function choose(page: Page, testid: string, optionText: RegExp | string): Promise<void> {
  await page.getByTestId(testid).click();
  await page.getByRole("option", { name: optionText }).click();
}

export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/**
 * Waits until the layout stopped moving: Vuetify animates v-main's padding when the sidebar
 * appears, collapses or resizes, so widths are only read once two frames agree.
 */
export async function settled(page: Page): Promise<void> {
  // No running finite CSS transition/animation (v-main padding, drawer width), then two frames.
  // Infinite ones (an indeterminate progress bar of a preparing job) never "settle" and are ignored.
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running" || a.effect?.getTiming().iterations === Infinity));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

export async function columns(page: Page): Promise<number> {
  await settled(page);
  return Number(await page.getByTestId("expert-layout").getAttribute("data-columns"));
}

export async function paneWidth(page: Page, pane: "inputs" | "inspector" | "queue"): Promise<number> {
  await settled(page);
  const box = await page.getByTestId(`pane-${pane}`).boundingBox();
  return box ? Math.round(box.width) : 0;
}

/** Pointer drag of a splitter by dx CSS px. */
export async function drag(page: Page, testid: string, dx: number): Promise<void> {
  const box = await page.getByTestId(testid).boundingBox();
  if (!box) throw new Error(`${testid} not visible`);
  const x = box.x + box.width / 2;
  const y = box.y + Math.min(box.height / 2, 200);
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y, { steps: 4 });
  await page.mouse.move(x + dx, y, { steps: 4 });
  await page.mouse.up();
  await settled(page);
}

/** Scenario/theme/language through the reviewer toolbar (in-memory only). */
export async function setup(page: Page, opts: { lang?: "ja" | "en"; theme?: "light" | "dark"; scenario?: RegExp }): Promise<void> {
  if (opts.scenario) await choose(page, "toolbar-scenario", opts.scenario);
  if (opts.lang) await page.getByTestId(`lang-${opts.lang}`).click();
  if (opts.theme) await page.getByTestId(`theme-${opts.theme}`).click();
}

/**
 * Readability floor (iteration 3): visible product text below 13 CSS px and buttons lower than
 * 36 CSS px. The review strip, snackbars and screen-reader-only text are excluded. Returns
 * human-readable findings so a failure names the element.
 */
export async function readability(page: Page): Promise<{ smallText: string[]; smallButtons: string[] }> {
  return page.evaluate(() => {
    const smallText: string[] = [];
    const smallButtons: string[] = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      if (el.closest(".p-sr-only, [data-testid=prototype-toolbar], .v-snackbar")) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const ownText = Array.from(el.childNodes).some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim() !== "");
      const label = `${el.tagName.toLowerCase()}.${String(el.className).split(" ")[0]} "${(el.textContent ?? "").trim().slice(0, 24)}"`;
      if (ownText && parseFloat(cs.fontSize) < 13) smallText.push(`${cs.fontSize} ${label}`);
      if (el.classList.contains("v-btn") && rect.height < 36) smallButtons.push(`${Math.round(rect.height)}px ${label}`);
    }
    return { smallText, smallButtons };
  });
}

/** Contrast of the resting splitter grip against the colour behind it (WCAG 1.4.11 needs 3:1). */
export async function gripContrast(page: Page, testid: string): Promise<number> {
  return page.evaluate((id) => {
    const handle = document.querySelector(`[data-testid="${id}"]`);
    const grip = handle?.querySelector(".p-resize__grip");
    if (!handle || !grip) return 0;
    const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).map(Number);
    const solid = (el: Element | null): number[] => {
      for (let n = el; n; n = n.parentElement) {
        const [r = 0, g = 0, b = 0, a = 1] = parse(getComputedStyle(n).backgroundColor);
        if (a > 0 && getComputedStyle(n).backgroundColor !== "rgba(0, 0, 0, 0)") return [r, g, b];
      }
      return [255, 255, 255];
    };
    const bg = solid(handle.parentElement);
    const [r = 0, g = 0, b = 0, a = 1] = parse(getComputedStyle(grip).backgroundColor);
    const fg = [r, g, b].map((c, i) => c * a + bg[i]! * (1 - a));
    const lum = (rgb: number[]) => {
      const [R, G, B] = rgb.map((v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * R! + 0.7152 * G! + 0.0722 * B!;
    };
    const [l1, l2] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    return (l1! + 0.05) / (l2! + 0.05);
  }, testid);
}

/** Bounding boxes of the app bar's direct items must not overlap (narrow windows, zoom). */
export async function appBarOverlaps(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bar = document.querySelector("[data-testid=app-bar] .v-toolbar__content");
    if (!bar) return ["no app bar"];
    const items = Array.from(bar.children).filter((c) => c.getBoundingClientRect().width > 1 && !c.classList.contains("v-spacer"));
    const out: string[] = [];
    for (let i = 0; i < items.length; i += 1) {
      const a = items[i]!.getBoundingClientRect();
      if (a.right > window.innerWidth + 0.5) out.push(`${items[i]!.className} beyond the window`);
      for (let j = i + 1; j < items.length; j += 1) {
        const b = items[j]!.getBoundingClientRect();
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5) out.push(`${items[i]!.className} × ${items[j]!.className}`);
      }
    }
    return out;
  });
}
