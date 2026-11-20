import type { Locator, Page } from "@playwright/test";
import { expect } from "./fixtures";

async function gripAppearance(handle: Locator) {
  return handle.locator(".u-resize__grip").evaluate((grip) => {
    const style = getComputedStyle(grip);
    const channels = (color: string) => (color.match(/[\d.]+/g) ?? []).map(Number);
    let background = [255, 255, 255];
    for (let element = grip.parentElement; element; element = element.parentElement) {
      const color = channels(getComputedStyle(element).backgroundColor);
      if (color.length === 3 || color[3] === 1) {
        background = color.slice(0, 3);
        break;
      }
    }
    const opacity = Number(style.opacity);
    const color = channels(style.backgroundColor).slice(0, 3);
    const painted = color.map((channel, i) => channel * opacity + background[i]! * (1 - opacity));
    const luminance = (rgb: number[]) => rgb.map((value) => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, channel, i) => sum + channel * [0.2126, 0.7152, 0.0722][i]!, 0);
    const [darker, lighter] = [luminance(background), luminance(painted)].sort((a, b) => a - b);
    return {
      opacity,
      contrast: (lighter! + 0.05) / (darker! + 0.05),
      primary: color.join() === channels(style.getPropertyValue("--v-theme-primary")).join(),
    };
  });
}

export async function expectSplitterFeedback(page: Page, id: string): Promise<void> {
  const handle = page.getByRole("separator").and(page.getByTestId(id));
  const activeGrip = () => expect.poll(() => gripAppearance(handle), id).toMatchObject({ opacity: 1, primary: true });
  const rest = await gripAppearance(handle);
  expect(rest.primary, id).toBe(false);
  await handle.hover();
  await activeGrip();
  expect(rest.contrast, id).toBeGreaterThanOrEqual(3);
  expect((await gripAppearance(handle)).contrast, id).toBeGreaterThanOrEqual(3);

  await page.mouse.move(8, 8);
  await handle.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(handle).toBeFocused();
  expect(await handle.evaluate((element) => element.matches(":focus-visible") && !element.matches(":hover")), id).toBe(true);
  await activeGrip();

  await handle.hover();
  await page.mouse.down();
  try {
    const bounds = (await handle.boundingBox())!;
    await page.mouse.move(bounds.x + bounds.width / 2 + 24, bounds.y + bounds.height / 2);
    await expect(handle).toHaveClass(/\bu-resize--active\b/);
    await activeGrip();
  } finally {
    await page.mouse.up();
  }
  await expect(handle).not.toHaveClass(/\bu-resize--active\b/);
  await page.mouse.move(8, 8);
  await handle.evaluate((element) => element.blur());
  await expect.poll(() => gripAppearance(handle), id).toEqual(rest);
}
